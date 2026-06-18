#!/usr/bin/env node
/**
 * One-time X (Twitter) login for Athena's shared Playwright profile.
 *
 * X Articles (long-form posts) are login-gated: the public syndication CDN
 * exposes only the title/preview/cover, and the article page bounces to the
 * login flow without an authenticated session. This helper opens a HEADED
 * Chromium against the same persistent profile the LinkedIn deep-capture uses
 * (~/.athena/playwright-userdata/), lets you log into X once, verifies the
 * `auth_token` cookie, and drops a marker so future X-Article captures can run
 * headlessly — mirroring scripts/capture-deep.js's LinkedIn first-run flow.
 *
 * Run it in your OWN terminal (it needs interactive stdin):
 *     node scripts/x-login.js
 *
 * X's login (Arkose) throws an unsolvable "Preparing verification" loop when
 * it detects automation. We defeat that by (1) using your REAL installed
 * Chrome (channel: "chrome") instead of Playwright's more-detectable bundled
 * Chromium, (2) dropping the --enable-automation / AutomationControlled flags,
 * and (3) masking navigator.webdriver. If it STILL loops, fall back to the
 * CDP-attach route printed at the end (drive your own already-running Chrome).
 *
 * Re-run with --reauth to force a fresh login (clears just the marker).
 */
const { chromium } = require("playwright");
const path = require("path");
const fs = require("fs");
const os = require("os");

const AUTH_DIR = path.join(os.homedir(), ".athena", "playwright-userdata");
const X_MARKER = path.join(AUTH_DIR, ".athena-x-auth-confirmed");
const LOGIN_URL = "https://x.com/i/flow/login";
const UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

async function main() {
  const reauth = process.argv.includes("--reauth");
  if (reauth) {
    try { fs.unlinkSync(X_MARKER); } catch {}
  }
  fs.mkdirSync(AUTH_DIR, { recursive: true });

  console.error("──────────────────────────────────────────────────");
  console.error("  X (Twitter) LOGIN — one-time setup");
  console.error("──────────────────────────────────────────────────");
  console.error("Opening Chrome. Log into X in the window, wait until you");
  console.error("see your home timeline, THEN return here and press ENTER.");
  console.error("");

  // Anti-detection: real Chrome + no automation flags. X's Arkose challenge
  // loops forever ("Preparing verification") if it sees navigator.webdriver
  // or the --enable-automation switch.
  const launchOpts = {
    headless: false,
    viewport: { width: 1280, height: 1024 },
    userAgent: UA,
    args: ["--disable-blink-features=AutomationControlled"],
    ignoreDefaultArgs: ["--enable-automation"],
  };
  let ctx;
  try {
    // Prefer the user's installed Google Chrome (proper fingerprint).
    ctx = await chromium.launchPersistentContext(AUTH_DIR, {
      ...launchOpts, channel: "chrome",
    });
  } catch (e) {
    console.error("(Google Chrome not found via channel — using bundled Chromium)");
    ctx = await chromium.launchPersistentContext(AUTH_DIR, launchOpts);
  }
  // Mask the residual automation tell before any page script runs.
  await ctx.addInitScript(() => {
    Object.defineProperty(navigator, "webdriver", { get: () => undefined });
  });
  const page = ctx.pages()[0] || (await ctx.newPage());
  await page.goto(LOGIN_URL, { waitUntil: "domcontentloaded", timeout: 30000 });

  await new Promise((resolve) => {
    process.stdin.resume();
    process.stdin.setEncoding("utf8");
    process.stdin.once("data", () => { process.stdin.pause(); resolve(); });
  });

  // Verify the X auth cookie is present before declaring success — without it
  // the next headless capture hits the login wall again.
  const cookies = await ctx.cookies("https://x.com");
  const authToken = cookies.find((c) => c.name === "auth_token");
  if (!authToken) {
    console.error("");
    console.error("⚠️  `auth_token` cookie not found — login didn't complete.");
    console.error("    If X kept looping on 'Preparing verification', use the");
    console.error("    CDP-attach fallback (drive your OWN already-running Chrome,");
    console.error("    which X cannot fingerprint as automated):");
    console.error("");
    console.error("    1. Quit Chrome, then relaunch with a debugging port:");
    console.error("       open -na 'Google Chrome' --args \\");
    console.error("         --remote-debugging-port=9222 \\");
    console.error("         --user-data-dir=$HOME/.athena/chrome-cdp-profile");
    console.error("    2. Log into X in that window.");
    console.error("    3. Tell me — I'll capture the article via CDP-attach.");
    await ctx.close();
    process.exit(1);
  }
  console.error("");
  console.error(
    "✓ auth_token confirmed (expires " +
      (authToken.expires > 0
        ? new Date(authToken.expires * 1000).toISOString().split("T")[0]
        : "session") +
      ") — X session saved.",
  );
  fs.writeFileSync(X_MARKER, new Date().toISOString());
  await ctx.close();
  console.error("Done. Athena can now deep-capture X Articles headlessly.");
  process.exit(0);
}

main().catch((e) => {
  console.error("x-login failed:", e && e.message);
  process.exit(1);
});
