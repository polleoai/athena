#!/usr/bin/env node
/**
 * capture-deep.js — Playwright-driven LinkedIn carousel capturer.
 *
 * Why this exists: Web Clipper extracts only what's currently in the
 * rendered DOM. LinkedIn's image carousels lazy-load pages 2-N; they
 * never enter the DOM unless the user manually paginates first. The
 * result is the documented "8 pages text but no images" failure mode.
 *
 * This script opens the URL in a persistent-context Chromium browser,
 * auto-paginates the carousel via clicking the next-arrow until disabled,
 * accumulates image URLs after each click, then writes a clip-shaped
 * markdown file to clippings/. The existing autoingest pipeline
 * (process_clip → chrome strip → backfill assets → wiki page) does
 * everything else — zero duplication of downstream logic.
 *
 * Auth model: launchPersistentContext stores cookies + localStorage in
 * ~/.athena/playwright-userdata/. First run is HEADED (you log into
 * LinkedIn manually); subsequent runs are HEADLESS and reuse the saved
 * session.
 *
 * Usage:
 *   node scripts/capture-deep.js <url>
 *
 * Output: writes clipfile path to stdout (for shell-script consumption);
 * progress messages go to stderr.
 *
 * Scope discipline: v1 supports LinkedIn carousels only. Other lazy-load
 * patterns (X infinite scroll, Substack subscriber-only) are explicit
 * non-goals until v1 validates the architecture.
 */

// Use playwright-extra + stealth plugin to defeat LinkedIn's automation
// detection. Falls back to plain playwright if either dep is missing
// (so the script still works in environments without the stealth deps).
let chromium;
try {
  const playwrightExtra = require("playwright-extra");
  const stealth = require("puppeteer-extra-plugin-stealth")();
  playwrightExtra.chromium.use(stealth);
  chromium = playwrightExtra.chromium;
} catch (e) {
  console.error(
    `[warn] playwright-extra/stealth not available; falling back to plain playwright. ` +
    `LinkedIn may show CAPTCHA. Install: npm install --save-dev playwright-extra puppeteer-extra-plugin-stealth`
  );
  chromium = require("playwright").chromium;
}

const path = require("path");
const fs = require("fs");
const os = require("os");
const crypto = require("crypto");

const VAULT_ROOT = process.env.KB_ROOT || process.cwd();
const AUTH_DIR = path.join(os.homedir(), ".athena", "playwright-userdata");

// LinkedIn DOM heuristics — try multiple selectors to absorb minor
// markup changes. If none match, fall back to single-image capture.
const NEXT_BUTTON_SELECTORS = [
  'button[aria-label*="Next" i][aria-label*="image" i]',
  'button[aria-label*="Next" i][aria-label*="page" i]',
  'button[aria-label="Next" i]',
  '[data-test-id*="carousel-next"]',
  '[data-test-id*="document-viewer-next"]',
];

// LinkedIn DOM hierarchy for a feed post (most → least specific):
//
//   article[data-urn*="ugcPost"|"activity"]   ← stable URN identifier
//     └─ .feed-shared-update-v2__description-wrapper
//          └─ .feed-shared-update-v2__description     ← post text container
//               └─ .feed-shared-inline-show-more-text  ← inside "see more"
//                    └─ span[dir="ltr"]                ← actual paragraphs
//
// data-urn values are the most durable selectors: they're keyed off
// LinkedIn's underlying URN model (urn:li:ugcPost:ID, urn:li:activity:ID,
// urn:li:share:ID) which doesn't drift with their UI redesigns.
// Class-name selectors below are kept as fallbacks but rot more often.
//
// Order matters: try the most specific first. Each candidate must match
// exactly the post body, NOT the wrapper that includes the author header
// + Connect button + reactions row (those leak into innerText if we pick
// the outer wrapper). When all DOM selectors fail, try JSON-LD structured
// data extraction (LinkedIn embeds schema.org SocialMediaPosting on most
// post pages) — slowest path but most semantically correct.
const POST_BODY_SELECTORS = [
  // Most specific: span with dir="ltr" inside the show-more text wrapper
  '.feed-shared-inline-show-more-text span[dir="ltr"]',
  '.feed-shared-update-v2__description span[dir="ltr"]',
  // Description text container (may include "...see more" toggle)
  ".feed-shared-update-v2__description",
  ".feed-shared-update-v2__description-wrapper",
  // URN-anchored containers (more stable than class names)
  '[data-urn*="ugcPost"] .update-components-text',
  '[data-urn*="activity"] .update-components-text',
  // Generic update-components-text (works on some non-Feed surfaces)
  ".update-components-text",
  // Last DOM resort: role="article" — may include some chrome but
  // chrome-strip handles that downstream.
  '[role="article"]',
];

// X Article rich-text body. Verified against the logged-in DOM (2026-06-16):
// the article renders inside `[data-testid="longformRichTextComponent"]` — and
// X's article page has NO <article> element, so selectors must NOT be scoped
// under `article`. Lead with the clean rich-text node (excludes the feed-post
// engagement chrome the <main> fallback sweeps in), then widen to the read-view
// wrappers, then keep stale-testid guesses for forward-resilience; <main> is the
// ultimate fallback downstream.
const X_ARTICLE_SELECTORS = [
  '[data-testid="longformRichTextComponent"]',
  '[data-testid="twitterArticleRichTextView"]',
  '[data-testid="twitterArticleReadView"]',
  '[data-testid="twitterArticleRichText"]',
  'div[role="article"]',
];
const X_ARTICLE_TITLE_SELECTORS = [
  '[data-testid="twitter-article-title"]',
  '[data-testid="twitterArticleTitle"]',
  'h1',
];

// Serialize an X Article's Draft.js rich text to Markdown IN-PAGE. `.innerText`
// (what the generic body cascade uses) flattens headings/lists/links/code to
// plain paragraphs and drops every <img> — so the local copy loses structure
// AND images never reach asset_download. This walks the article's top-level
// blocks (paragraph = div.longform-unstyled, heading = section>h2, code =
// section>pre, list = ul/ol, image = wrapper>img) and emits real Markdown,
// upgrading twimg `name=small` thumbnails to `name=large` for full-res. The
// returned `![](url)` refs are localized by the backfill-assets ingest step.
async function extractXArticleBody(page) {
  return await page.evaluate(() => {
    const root = document.querySelector('[data-testid="longformRichTextComponent"]');
    if (!root) return "";
    const upgrade = (src) => {
      try {
        const u = new URL(src);
        if (/(^|\.)twimg\.com$/.test(u.hostname) && u.searchParams.get("name")) {
          u.searchParams.set("name", "large");
          return u.toString();
        }
      } catch { /* not an absolute URL — leave as-is */ }
      return src;
    };
    const inlineMd = (el) => {
      let s = "";
      el.childNodes.forEach((c) => {
        if (c.nodeType === 3) { s += c.textContent; return; }
        if (c.nodeType !== 1) return;
        const t = c.tagName.toLowerCase();
        if (t === "a") {
          const txt = inlineMd(c).trim();
          const href = c.getAttribute("href") || "";
          s += txt ? (href ? `[${txt}](${href})` : txt) : "";
        } else if (t === "strong" || t === "b") {
          s += `**${inlineMd(c)}**`;
        } else if (t === "em" || t === "i") {
          s += `*${inlineMd(c)}*`;
        } else if (t === "code") {
          s += "`" + (c.textContent || "") + "`";
        } else if (t === "br") {
          s += "\n";
        } else if (t === "img") {
          /* block-level; handled by the walker below */
        } else {
          s += inlineMd(c);
        }
      });
      return s;
    };
    // Draft.js nests the actual blocks under a <div data-contents="true">
    // wrapper; iterate THAT container's children, not root's single wrapper.
    const container = root.querySelector('[data-contents="true"]') || root;
    const out = [];
    for (const block of container.children) {
      const h = block.querySelector("h1, h2, h3");
      const pre = block.querySelector("pre");
      const isList = block.tagName === "UL" || block.tagName === "OL";
      const imgs = block.querySelectorAll("img");
      if (h) {
        const lvl = h.tagName === "H1" ? "#" : h.tagName === "H2" ? "##" : "###";
        const txt = inlineMd(h).trim();
        if (txt) out.push(`${lvl} ${txt}`, "");
      } else if (pre) {
        out.push("```", (pre.textContent || "").replace(/\n+$/, ""), "```", "");
      } else if (isList) {
        const ordered = block.tagName === "OL";
        let i = 1;
        block.querySelectorAll("li").forEach((li) => {
          const txt = inlineMd(li).trim();
          if (txt) out.push(ordered ? `${i++}. ${txt}` : `- ${txt}`);
        });
        out.push("");
      } else if (imgs.length) {
        imgs.forEach((im) => {
          const src = im.getAttribute("src");
          if (src) out.push(`![](${upgrade(src)})`, "");
        });
      } else {
        const txt = inlineMd(block).trim();
        if (txt) out.push(txt, "");
      }
    }
    return out.join("\n").replace(/\n{3,}/g, "\n\n").trim();
  });
}

const MAX_CAROUSEL_PAGES = 30; // safety cap; LinkedIn caps carousels at ~20

async function main() {
  const args = process.argv.slice(2);
  const headedFlag = args.includes("--headed");
  const reauthFlag = args.includes("--reauth");
  const cdpAttachFlag = args.includes("--cdp-attach");
  // --cdp-port=9222 (with =) OR --cdp-port 9222 (separate arg)
  let cdpPort = 9222;
  for (let i = 0; i < args.length; i++) {
    if (args[i].startsWith("--cdp-port=")) {
      cdpPort = parseInt(args[i].split("=")[1], 10) || 9222;
    } else if (args[i] === "--cdp-port" && args[i + 1]) {
      cdpPort = parseInt(args[i + 1], 10) || 9222;
    }
  }
  const setupFlag = args.includes("--setup");
  let url = args.find((a) => !a.startsWith("--") && !/^\d+$/.test(a));
  if (!url && !setupFlag) {
    console.error("Usage: node capture-deep.js <url> [--headed] [--reauth] [--cdp-attach [--cdp-port N]]");
    console.error("       node capture-deep.js --setup     # one-time: log into LinkedIn");
    console.error("");
    console.error("  --setup        one-time setup. Opens a browser, prompts you to log");
    console.error("                 into LinkedIn, saves the session for autoingest to");
    console.error("                 reuse on every future LinkedIn capture.");
    console.error("  --headed       force visible browser (debug or re-login)");
    console.error("  --reauth       delete saved auth + open browser for fresh login");
    console.error("  --cdp-attach   attach to a user-launched Chrome over CDP (defeats");
    console.error("                 automation detection that beats stealth). Requires");
    console.error("                 Chrome running with --remote-debugging-port=N.");
    console.error("  --cdp-port N   port for --cdp-attach (default 9222)");
    process.exit(2);
  }
  // X-Article detection. When the URL is an x.com/i/article/<id> (or the
  // legacy twitter.com host), we drive a pre-seeded X session instead of
  // LinkedIn: the X cookie was imported into the shared persistent profile
  // by scripts/x-cookie-import.js (marker .athena-x-auth-confirmed). X blocks
  // automated login, so there is no interactive login flow for this path —
  // we require the marker to already exist and bail otherwise.
  const isXArticle = /(?:twitter|x)\.com\/i\/article\//i.test(url || "");
  const X_MARKER = path.join(AUTH_DIR, ".athena-x-auth-confirmed");
  if (isXArticle && !fs.existsSync(X_MARKER)) {
    console.error("No X session — run scripts/x-cookie-import.js first. Skipping.");
    process.exit(1);   // kb-capture falls back to the syndication preview
  }

  // --setup is sugar for "open the login flow without trying to capture
  // anything". Reuses the existing first-run login path by navigating to
  // the LinkedIn login page; user logs in, presses Enter, marker file
  // gets written. Same auth state powers all future auto-promote runs.
  if (setupFlag) {
    url = "https://www.linkedin.com/login";
  }

  // Sanitize URL: strip ALL whitespace including newlines + leading
  // whitespace from terminal-line-wrapped paste. Without this, the URL
  // would contain literal `\n  ` from the user's quoted-string paste,
  // which the browser URL-encodes to %20%20 — LinkedIn returns 404.
  // Bug surfaced 0.10.4: user's terminal visually wrapped a long URL
  // and the in-quote line break was preserved verbatim.
  const originalUrl = url;
  url = url.replace(/\s+/g, "");
  if (url !== originalUrl) {
    console.error(`Note: stripped whitespace from URL (paste was line-wrapped)`);
  }

  // ── CDP-ATTACH MODE ──────────────────────────────────
  // Connect to a user-launched Chrome (with --remote-debugging-port=N)
  // instead of launching Playwright's bundled Chromium. The user's real
  // Chrome has no automation fingerprint that LinkedIn can detect, so
  // this is the recommended fallback when stealth isn't enough.
  //
  // The user must launch Chrome themselves; capture-deep does NOT spawn
  // it (a user-launched browser is the entire point — spawning it from
  // here would defeat the fingerprint advantage). When connection fails,
  // we print the exact launch command and exit non-zero so the shell
  // wrapper can prompt them.
  let ctx;
  if (cdpAttachFlag) {
    const endpointURL = `http://127.0.0.1:${cdpPort}`;
    console.error(`Attempting CDP attach: ${endpointURL}`);
    let browser;
    try {
      browser = await chromium.connectOverCDP(endpointURL);
    } catch (e) {
      console.error("");
      console.error("──────────────────────────────────────────────────");
      console.error(`  CDP connection FAILED at ${endpointURL}`);
      console.error("──────────────────────────────────────────────────");
      console.error(`Error: ${e.message}`);
      console.error("");
      console.error("Launch Chrome with the debugging port enabled, log into");
      console.error("LinkedIn manually, then re-run this command:");
      console.error("");
      console.error("  # macOS — uses a dedicated profile so it doesn't conflict");
      console.error("  # with your normal Chrome session:");
      console.error(`  open -na 'Google Chrome' --args \\`);
      console.error(`    --remote-debugging-port=${cdpPort} \\`);
      console.error(`    --user-data-dir=$HOME/.athena/chrome-cdp-profile`);
      console.error("");
      console.error("After Chrome opens: log in at https://www.linkedin.com/login,");
      console.error("then re-run: bin/kb capture-deep --cdp-attach <url>");
      process.exit(4);
    }
    // CDP gives us all existing browser contexts; pick the first one
    // (Chrome usually exposes one default context per browser instance).
    const contexts = browser.contexts();
    if (contexts.length === 0) {
      console.error("");
      console.error("CDP attached but Chrome has no open contexts — open at least one tab and retry.");
      process.exit(5);
    }
    ctx = contexts[0];
    console.error(`✓ Attached to user Chrome (${contexts.length} context(s), ${(await Promise.all(contexts.map(c => c.pages()))).reduce((a, p) => a + p.length, 0)} page(s))`);
    // CDP-attach mode skips the saved-auth flow entirely — auth lives in
    // the user's real Chrome profile, which we don't manage. Skip
    // straight to navigation.
  } else {
    if (reauthFlag && fs.existsSync(AUTH_DIR)) {
      console.error(`Removing saved auth state at ${AUTH_DIR}`);
      fs.rmSync(AUTH_DIR, { recursive: true, force: true });
    }

    // Clean up stale Chromium SingletonLock symlinks. If a previous
    // capture-deep was force-killed (parent script SIGKILL'd, terminal
    // closed, etc.), the child Chromium can survive briefly while
    // holding the user-data-dir lock — and even after THAT Chromium
    // exits, the SingletonLock symlink can persist and block new
    // launches with NO error message (Chromium silently refuses to
    // start a second instance for the same user-data-dir). The window
    // never opens, and the user has no idea why. Detect a stale lock
    // (PID it points to is dead) and remove it so the launch succeeds.
    // 0.10.15 fix: surfaced when the user reported "the playwright
    // window is already closed. i am unable to open it" — root cause
    // was an orphan Chromium from an earlier force-killed agent run.
    for (const lockName of ["SingletonLock", "SingletonCookie", "SingletonSocket"]) {
      const lockPath = path.join(AUTH_DIR, lockName);
      if (fs.existsSync(lockPath) || fs.lstatSync(lockPath, { throwIfNoEntry: false })) {
        try {
          // SingletonLock format: "<hostname>-<pid>" — extract pid and
          // check if alive. If dead OR if the symlink is broken, drop it.
          const target = fs.readlinkSync(lockPath);
          const pidMatch = target.match(/-(\d+)$/);
          let stale = !pidMatch;
          if (pidMatch) {
            const pid = parseInt(pidMatch[1], 10);
            try {
              process.kill(pid, 0);  // signal 0 = check existence
              stale = false;
            } catch {
              stale = true;  // ESRCH — process is gone
            }
          }
          if (stale) {
            console.error(`Removing stale ${lockName} (was: ${target})`);
            fs.unlinkSync(lockPath);
          }
        } catch (e) {
          // readlink failed (not a symlink, or doesn't exist) — best
          // effort: try to unlink, ignore if it doesn't work.
          try { fs.unlinkSync(lockPath); } catch {}
        }
      }
    }

    // CRITICAL: capture needsLogin state BEFORE chromium.launchPersistentContext()
    // Chromium auto-creates `Default/` the moment it launches, which would
    // make a post-launch existsSync(Default) check return true even on a
    // fresh first-run. We use our own marker file (.athena-auth-confirmed)
    // that's written only after the user explicitly confirms login + we
    // verify the li_at cookie. Bug surfaced 0.10.3.
    const AUTH_CONFIRMED_MARKER = path.join(AUTH_DIR, ".athena-auth-confirmed");
    // --setup explicitly requests the login flow (re-auth or first-time);
    // --headed forces a visible browser for debugging; missing marker means
    // we've never logged in here. Any of these triggers the login UI.
    //
    // X-Article path NEVER triggers the LinkedIn login flow: the X cookie is
    // pre-seeded by scripts/x-cookie-import.js (X blocks automated login), and
    // the .athena-x-auth-confirmed marker was already verified above. Force
    // needsLogin false so we launch headless and skip straight to navigation.
    const needsLogin =
      !isXArticle &&
      (!fs.existsSync(AUTH_CONFIRMED_MARKER) || headedFlag || setupFlag);

    const firstRun = needsLogin;
    if (firstRun && !fs.existsSync(path.join(AUTH_DIR, "Default"))) {
      fs.mkdirSync(AUTH_DIR, { recursive: true });
      console.error("");
      console.error("──────────────────────────────────────────────────");
      console.error("  FIRST-TIME SETUP");
      console.error("──────────────────────────────────────────────────");
      console.error("Opening Chromium. Log into LinkedIn, then close the");
      console.error("browser window. Your session will persist for future runs.");
      console.error("");
      console.error("Tip: if LinkedIn keeps showing CAPTCHA loops, retry with");
      console.error("--cdp-attach to drive your real Chrome instead of Chromium:");
      console.error(`  bin/kb capture-deep --cdp-attach <url>`);
      console.error("");
    } else {
      console.error(`Capturing: ${url}`);
    }

    ctx = await chromium.launchPersistentContext(AUTH_DIR, {
      headless: !needsLogin,
      viewport: { width: 1280, height: 1024 },
      userAgent:
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    });

    if (needsLogin) {
    const loginPage = await ctx.newPage();
    await loginPage.goto("https://www.linkedin.com/login");
    console.error("");
    console.error("──────────────────────────────────────────────────");
    console.error("  Browser opened. Complete login in the Chromium window:");
    console.error("    1. Enter your email + password");
    console.error("    2. Solve any CAPTCHA / 2FA challenge");
    console.error("    3. Wait until you see your LinkedIn home feed");
    console.error("");
    console.error("  THEN come back here and press ENTER to save the session.");
    console.error("──────────────────────────────────────────────────");
    console.error("");

    // Wait for explicit user signal (press Enter in terminal). This replaces
    // the fragile "wait for browser close" polling — LinkedIn auth flow opens
    // and closes multiple sub-pages (CAPTCHA frames, 2FA popups) during login,
    // which made the page-count check trigger prematurely.
    await new Promise((resolve) => {
      process.stdin.resume();
      process.stdin.setEncoding("utf8");
      process.stdin.once("data", () => {
        process.stdin.pause();
        resolve();
      });
    });

    // Verify the LinkedIn auth cookie (li_at) is actually present before
    // declaring success. Without it, a re-run will hit the auth wall again.
    const cookies = await ctx.cookies("https://www.linkedin.com");
    const liAt = cookies.find((c) => c.name === "li_at");
    if (!liAt) {
      console.error("");
      console.error("⚠️  WARNING: `li_at` cookie not found — login may NOT have succeeded.");
      console.error("    The next capture run will likely hit the auth wall again.");
      console.error("    To retry: bin/kb capture-deep <url> --reauth");
    } else {
      console.error("");
      console.error("✓ li_at cookie confirmed (expires "
        + new Date(liAt.expires * 1000).toISOString().split("T")[0]
        + ") — session saved.");
      // Write the marker file so future runs know auth is good and
      // skip the login prompt. Without this, every run thinks it's
      // first-run because Chromium creates Default/ at launch.
      fs.writeFileSync(AUTH_CONFIRMED_MARKER, new Date().toISOString());
    }
    await ctx.close();
    console.error("");
    console.error("Re-run the same command (without --reauth) to capture in headless mode.");
    process.exit(0);
    }
  }  // ← closes else-branch of cdpAttachFlag (the launchPersistentContext path)

  const page = await ctx.newPage();
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 });
  // Extra settle time for LinkedIn's progressive hydration
  await page.waitForTimeout(3000);

  let pageTitle = await page.title();
  // X Articles: the <title> is the generic "X" / handle chrome, not the
  // article headline. Prefer the in-DOM article title selectors; fall
  // through to the page.title() value above if none match.
  if (isXArticle) {
    for (const sel of X_ARTICLE_TITLE_SELECTORS) {
      try {
        const t = await page.locator(sel).first().innerText({ timeout: 2000 });
        if (t && t.trim().length > 0) {
          pageTitle = t.trim();
          break;
        }
      } catch {
        /* try next title selector */
      }
    }
  }
  console.error(`  Page title: ${pageTitle}`);

  // Initial scrape (handles non-carousel posts naturally)
  const images = new Set();
  await scrapeImages(page, images);
  console.error(`  Initial images: ${images.size}`);

  // Try to find a carousel "next" button
  let nextLocator = null;
  for (const sel of NEXT_BUTTON_SELECTORS) {
    const found = page.locator(sel).first();
    if ((await found.count()) > 0) {
      nextLocator = found;
      console.error(`  Carousel detected via selector: ${sel}`);
      break;
    }
  }

  if (nextLocator) {
    for (let i = 0; i < MAX_CAROUSEL_PAGES; i++) {
      const isDisabled = await nextLocator
        .evaluate(
          (el) =>
            el.disabled === true ||
            el.getAttribute("aria-disabled") === "true" ||
            el.classList.contains("disabled")
        )
        .catch(() => true);
      if (isDisabled) {
        console.error(`  Carousel end reached (next button disabled) after page ${i + 1}`);
        break;
      }
      try {
        await nextLocator.click({ timeout: 5000 });
      } catch (e) {
        console.error(`  Click failed at page ${i + 1}: ${e.message}`);
        break;
      }
      await page.waitForTimeout(800); // let new slide render + image load
      const before = images.size;
      await scrapeImages(page, images);
      const delta = images.size - before;
      console.error(`  After page ${i + 2}: +${delta} new images (total ${images.size})`);
      if (delta === 0) {
        console.error(`  No new images after click — assuming carousel end`);
        break;
      }
    }
  } else {
    console.error("  No carousel detected; single-image (or text-only) capture");
  }

  // Extract post body text. Strategy (most → least semantic):
  //   1. DOM selectors targeting the post text container directly
  //   2. JSON-LD structured data (schema.org SocialMediaPosting.articleBody)
  //   3. <main>.innerText (kitchen-sink fallback, relies on chrome strip)
  let body = "";
  let extractedVia = "";

  // X Articles: prefer the structured Markdown serializer (preserves
  // headings/lists/links/code + inline images). Falls through to the generic
  // innerText cascade below if the longform container isn't found.
  if (isXArticle) {
    try {
      const md = await extractXArticleBody(page);
      if (md && md.length > 50) {
        body = md;
        extractedVia = 'dom: longform Markdown (structured)';
      }
    } catch { /* fall through to the innerText cascade */ }
  }

  const bodySelectors = isXArticle ? X_ARTICLE_SELECTORS : POST_BODY_SELECTORS;
  for (const sel of (body ? [] : bodySelectors)) {
    try {
      const text = await page.locator(sel).first().innerText({ timeout: 2000 });
      if (text && text.length > 50) {
        body = text;
        extractedVia = `dom: ${sel}`;
        break;
      }
    } catch {
      /* try next selector */
    }
  }

  // JSON-LD fallback — LinkedIn embeds a <script type="application/ld+json">
  // block on most post pages with the post text in `articleBody`. This
  // bypasses DOM class-name churn entirely; only LinkedIn changing the
  // schema breaks it. Slower (parses every JSON-LD on the page) but a
  // reasonable safety net before the kitchen-sink <main> fallback.
  if (!body) {
    try {
      const fromJsonLd = await page.evaluate(() => {
        const scripts = document.querySelectorAll('script[type="application/ld+json"]');
        for (const s of scripts) {
          try {
            const data = JSON.parse(s.textContent || "{}");
            const items = Array.isArray(data) ? data : [data];
            for (const item of items) {
              if (!item || typeof item !== "object") continue;
              if (item.articleBody && typeof item.articleBody === "string"
                  && item.articleBody.length > 50) {
                return item.articleBody;
              }
              // Sometimes nested under @graph
              if (Array.isArray(item["@graph"])) {
                for (const node of item["@graph"]) {
                  if (node && node.articleBody && node.articleBody.length > 50) {
                    return node.articleBody;
                  }
                }
              }
            }
          } catch { /* malformed JSON-LD — skip this script */ }
        }
        return "";
      });
      if (fromJsonLd) {
        body = fromJsonLd;
        extractedVia = "json-ld: articleBody";
      }
    } catch { /* page.evaluate failed — fall through */ }
  }

  if (!body) {
    console.error("  WARNING: no body via DOM selectors OR JSON-LD. Falling back to <main>.");
    try {
      body = await page.locator("main").first().innerText({ timeout: 2000 });
      extractedVia = "main: innerText (chrome strip downstream)";
    } catch {
      body = "";
    }
  }
  if (extractedVia) {
    console.error(`  Body extracted via ${extractedVia} (${body.length} chars)`);
  }

  // Comment scrape. LinkedIn posts often end with "Link in comments" where
  // the author drops github / arxiv URLs in a follow-up comment that the
  // body extraction above misses entirely. Scroll to the comments section,
  // expand truncated comments, then collect comment text + any URLs the
  // comments resolve via t.co-style redirectors. The captured text is
  // appended to body under a `## Comments` heading so process_clip's
  // _queue_referenced_urls picks the URLs up automatically.
  // Witnessed 2026-05-19: Niels Provos's IronCurtain v0.11.0 post ("Link
  // in comments") whose github URL only appeared in the author's reply.
  let commentText = "";
  try {
    // Scroll the comments section into view. LinkedIn renders the
    // comments below the post; scroll until they're in the viewport.
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await page.waitForTimeout(1500);
    // Second pass triggers lazy-load of additional comment chunks.
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await page.waitForTimeout(1500);

    // Click "Show more comments" / "Load more replies" buttons if present.
    // Multiple LinkedIn locales + shapes — try a small selector set.
    const expandSelectors = [
      'button:has-text("Show more comments")',
      'button:has-text("Load more")',
      'button:has-text("more replies")',
      'button.comments-comments-list__load-more-comments-button',
    ];
    for (const sel of expandSelectors) {
      try {
        const btns = page.locator(sel);
        const count = await btns.count();
        // Cap at 3 clicks per shape to avoid infinite loops on
        // pathological pages.
        for (let i = 0; i < Math.min(count, 3); i++) {
          try {
            await btns.nth(i).click({ timeout: 2000 });
            await page.waitForTimeout(800);
          } catch { /* button vanished mid-click */ }
        }
      } catch { /* selector didn't match */ }
    }

    // Extract comment text. LinkedIn class names churn; try multiple
    // shapes and concatenate everything that matches. Fall back to
    // section-level extraction if specific selectors return nothing.
    const commentSelectors = [
      '.comments-comment-item',
      '.comments-comment-entity',
      'article.comments-comment-item',
      '[data-test-id^="comment-"]',
    ];
    let chunks = [];
    for (const sel of commentSelectors) {
      try {
        const locs = page.locator(sel);
        const count = await locs.count();
        for (let i = 0; i < Math.min(count, 50); i++) {
          try {
            const text = await locs.nth(i).innerText({ timeout: 1000 });
            if (text && text.trim().length > 10) {
              chunks.push(text.trim());
            }
          } catch { /* skip */ }
        }
        if (chunks.length > 0) break;  // first selector that yielded comments wins
      } catch { /* try next selector */ }
    }
    if (chunks.length === 0) {
      // Section-level kitchen-sink fallback — grab the whole comments
      // container if it has any text.
      try {
        const sec = await page.locator('section.comments-comments-list, section[aria-label*="omment"]').first().innerText({ timeout: 2000 });
        if (sec && sec.length > 30) {
          chunks.push(sec);
        }
      } catch { /* no comments section located */ }
    }
    if (chunks.length > 0) {
      // De-dupe and cap. Comments can be repetitive (multiple replies
      // quoting the same parent); a hard cap prevents the body from
      // ballooning if a post has hundreds of comments.
      const seen = new Set();
      const deduped = chunks.filter((c) => {
        const key = c.slice(0, 80);
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
      commentText = deduped.slice(0, 30).join("\n\n---\n\n");
      console.error(`  Captured ${chunks.length} comment(s) → ${commentText.length} chars`);
    } else {
      console.error("  No comments found / extracted");
    }
  } catch (e) {
    console.error(`  Comment scrape failed: ${e.message}`);
  }

  // CDP-attach mode owns the browser via the user's launched Chrome —
  // closing the context would shut THEIR Chrome down. Close only the
  // page we opened. Owned-Playwright path can close the whole context.
  if (cdpAttachFlag) {
    await page.close();
  } else {
    await ctx.close();
  }

  // Auth-wall detection. LinkedIn serves a "Join LinkedIn now / Sign in"
  // page when the persistent context's session has expired or wasn't
  // saved properly. The captured body is a few hundred chars of marketing
  // boilerplate that's worse than useless — it would clobber any existing
  // good raw at the same URL slug if we let process_clip ingest it.
  // Defensive: detect the auth-wall pattern and refuse to write the clip.
  const authWallSignals = [
    /Join LinkedIn now/i,
    /Sign Up \| LinkedIn/i,
    /Email\s*\n\s*Password/i,
    /By clicking Agree & Join/i,
  ];
  const looksLikeAuthWall =
    authWallSignals.some((re) => re.test(body) || re.test(pageTitle)) &&
    images.size === 0;
  if (looksLikeAuthWall) {
    console.error("");
    console.error("──────────────────────────────────────────────────");
    console.error("  AUTH-WALL DETECTED — clip NOT written");
    console.error("──────────────────────────────────────────────────");
    console.error("LinkedIn served the sign-up wall instead of the post.");
    console.error("Your saved session has expired or was never created.");
    console.error("");
    console.error("To fix:");
    console.error("  1. rm -rf ~/.athena/playwright-userdata/");
    console.error("  2. bin/kb capture-deep <url>   # opens browser; LOG IN PROPERLY");
    console.error("  3. After login, navigate to the post URL in the browser");
    console.error("     to confirm you can see the post content.");
    console.error("  4. Close the browser window completely.");
    console.error("  5. bin/kb capture-deep <url>   # should now work in headless mode");
    process.exit(3);
  }

  // Compose clip
  const clipDir = path.join(VAULT_ROOT, "clippings");
  fs.mkdirSync(clipDir, { recursive: true });
  const slug = `deep-${crypto.createHash("sha1").update(url).digest("hex").slice(0, 16)}`;
  const clipPath = path.join(clipDir, `${slug}.md`);

  const imageMd = Array.from(images)
    .map((u) => `![View image](${u})`)
    .join("\n\n");

  const titleEsc = pageTitle.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  const urlEsc = url.replace(/\\/g, "\\\\").replace(/"/g, '\\"');

  // Comment section, if scrape yielded anything. process_clip's
  // _queue_referenced_urls scans the whole body so github/arxiv URLs
  // in comments get queued automatically — no separate integration
  // point needed here.
  const commentsMd = commentText
    ? `\n\n## Comments\n\n${commentText}\n`
    : "";

  const content =
    `---\n` +
    `title: "${titleEsc}"\n` +
    `source: "${urlEsc}"\n` +
    `captured_at: "${new Date().toISOString()}"\n` +
    `clipped_via: "deep-capture"\n` +
    `tags:\n` +
    `  - "clippings"\n` +
    `---\n\n` +
    `${isXArticle ? "" : "## Feed post\n\n"}` +
    `${body}\n\n` +
    `${imageMd}\n` +
    `${commentsMd}`;

  fs.writeFileSync(clipPath, content);
  console.error("");
  console.error(`Captured ${images.size} images + ${body.length} chars body${commentText ? ` + ${commentText.length} chars comments` : ""}`);
  console.error(`Clip written: ${path.relative(VAULT_ROOT, clipPath)}`);
  console.error("Autoingest will pick it up within ~30s, OR run: bin/kb add");

  // stdout = path for downstream consumers
  console.log(clipPath);
}

async function scrapeImages(page, imageSet) {
  // Pull <img> srcs that look like LinkedIn post content (feedshare, document,
  // article, videoshare URLs). Profile photos and avatars are explicitly filtered.
  const srcs = await page.evaluate(() => {
    const all = Array.from(document.querySelectorAll("img"));
    return all
      .map((img) => img.currentSrc || img.src)
      .filter((src) => {
        if (!src || !src.includes("media.licdn.com")) return false;
        // Filter out profile photos / avatars / company logos (chrome)
        if (src.includes("profile-displayphoto")) return false;
        if (src.includes("profile-framedphoto")) return false;
        if (src.includes("company-logo")) return false;
        return true;
      });
  });
  for (const src of srcs) imageSet.add(src);
}

main().catch((err) => {
  console.error("ERROR:", err.message);
  console.error(err.stack);
  process.exit(1);
});
