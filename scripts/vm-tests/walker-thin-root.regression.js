"use strict";

/**
 * Regression: browser-capture DOM walker must not root on a thin teaser
 * <article> and drop the real body.
 *
 * Bug (2026-07-08): component-CMS pages (Adobe AEM "insights" templates,
 * e.g. jpmorgan.com) wrap ONLY a hero/side-panel teaser in
 * <article class="cmp-article-side-panel-container">, while the real body
 * lives in sibling grid columns AFTER </article>. The walker rooted on
 * `document.querySelector('article, main, [role="main"]')` and captured a
 * ~244-char shell, discarding the ~2,200-word article. Fix: a thin-root
 * guard re-roots on <body> when the chosen generic root carries < 30% of
 * the document's text.
 *
 * This test extracts the live _BROWSER_EXTRACT_JS string from
 * src/athena/plugin.js (so it never drifts from the shipped walker), runs
 * it in a real Chromium page against a synthetic AEM-shaped fixture, and
 * asserts the body is captured. It also runs the PRE-FIX selection inline
 * to prove the test discriminates (old logic returns the shell only).
 *
 * Run: node scripts/vm-tests/walker-thin-root.regression.js
 * (from the athena vault root). Exit 0 = pass, 1 = fail.
 */

const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const VAULT = path.resolve(__dirname, "..", "..");
const PLUGIN_SRC = path.join(VAULT, "src", "athena", "plugin.js");

// ── Extract the walker IIFE string from the BUILT BUNDLE ──
// CRITICAL: test the bundle (.obsidian/plugins/athena/main.js), not the source.
// The walker lives inside a template literal, and a single-backslash escape in
// the source (e.g. `\s`) is silently collapsed by esbuild's template evaluation
// (`\s` -> `s`), which broke the chrome-strip regex in the shipped plugin while
// the source read fine. Extracting the template from the bundle and resolving it
// exactly as the JS engine does (via Function) reproduces the shipped walker
// byte-for-byte, so escaping corruption is caught. Requires `npm run build` first.
const BUNDLE = path.join(VAULT, ".obsidian", "plugins", "athena", "main.js");
function extractWalkerJs() {
  const main = fs.readFileSync(BUNDLE, "utf8");
  const mi = main.indexOf("var titleEl");
  if (mi < 0) throw new Error("could not locate walker in bundle (run `npm run build` first)");
  const sb = main.lastIndexOf("`", mi);
  let i = mi, eb;
  while (true) { eb = main.indexOf("`", i); if (eb < 0) break; if (main[eb - 1] !== "\\") break; i = eb + 1; }
  const rawTemplate = main.slice(sb + 1, eb);
  // Resolve the template literal the way the JS engine evaluates it in Electron.
  // eslint-disable-next-line no-new-func
  return Function("return `" + rawTemplate + "`;")();
}

// ── Synthetic AEM-shaped fixture ──
// A tiny <article> teaser (hero + Overview TOC) followed by the real body in
// sibling cmp-text grid columns. Mirrors the jpmorgan.com structure.
// Paragraphs are padded to realistic article length (real jpmorgan.com body
// was ~23,000 chars) so document.body.textContent clears the guard's floor,
// exactly as it does on a real long-form page.
const _FILLER =
  " Security teams and builders alike are re-drawing their assumptions about where trust begins and ends, " +
  "and the practical work of the next few years will be turning these observations into controls, budgets, and product roadmaps that hold up under real adversarial pressure.";
const BODY_SECTIONS = [
  ["The identity perimeter has moved",
   "Enterprise AI adoption is pushing identity to the center of the threat landscape. " +
   "Non-human identities now outnumber human ones in most enterprises, and each is a credential an attacker can steal or forge. " +
   "The perimeter is no longer the network edge; it is every token an agent carries." + _FILLER + _FILLER],
  ["How AI is expanding the attack surface",
   "Identity is one dimension of the expanding threat surface; speed is another. " +
   "Agents act in milliseconds and chain API calls faster than any human reviewer can follow. " +
   "Impersonation, prompt injection, and tool abuse become first-class attack classes." + _FILLER + _FILLER],
  ["Where venture capital is concentrating",
   "Venture funding patterns reflect this shift. Capital is flowing to identity-native security, " +
   "runtime agent monitoring, and data-provenance tooling rather than legacy perimeter products." + _FILLER + _FILLER],
  ["How to build for an agent-driven environment",
   "Leading teams build products that benefit as models improve over time. " +
   "They instrument every agent action, keep humans in the loop for irreversible steps, and design for auditability from day one." + _FILLER + _FILLER],
  ["What this means for builders and investors",
   "The strongest candidates for success are teams building AI-native solutions to problems that AI itself is generating. " +
   "That reflexive loop, AI creating the risk AI must then defend, is the defining shape of the next security cycle." + _FILLER + _FILLER],
];

function buildFixture() {
  const bodyDivs = BODY_SECTIONS.map(([h, p]) =>
    `<div class="text cmp-text--pt aem-GridColumn"><div class="cmp-text"><h2><span class="section-header">${h}</span></h2><p>${p}</p></div></div>`
  ).join("\n");
  const toc = BODY_SECTIONS.map(([h]) => `<li>${h}</li>`).join("");
  // Chrome that must be stripped: a skip-link, a class-only mega-menu (no <nav>
  // tag, like the jpmorgan.com rendered DOM), a cookie/OneTrust banner, and a
  // "related insights" recirculation carousel after the body.
  return `<!doctype html><html><head><title>AI Cybersecurity: Threats, Funding &amp; Builder Priorities</title></head>
<body>
  <a class="skip-link" href="#main">Skip to main content</a>
  <div id="onetrust-banner-sdk">We and our partners use cookies. Reject All Confirm My Choices</div>
  <ul class="breadcrumbs section"><li>Insights</li><li>Commercial Banking</li></ul>
  <div class="infographic-footer"><button class="infographic-text-version">View text Version</button><button class="infographic-in-modal">View Infographic</button></div>
  <header><nav>Home Insights Banking Search</nav></header>
  <div class="primary-navigation-sections">
    <div class="expanded-nav-container"><button>Back to menu</button>
      <ul><li class="tab-nav-item">For Companies and Institutions</li>
          <li class="tab-nav-item">For Institutional Investors</li></ul>
    </div>
  </div>
  <article class="cmp-article-side-panel-container">
    <section class="cmp-article-side-panel-content">
      <p class="eyebrow"><a href="/insights/banking">Banking</a></p>
      <h1 class="title">Cybersecurity in the age of artificial intelligence</h1>
      <p>June 25, 2026</p>
      <p>AI is both the threat and the defense.</p>
      <p>Overview</p>
      <ul>${toc}</ul>
    </section>
  </article>
  ${bodyDivs}
  <p>By Jane Author</p>
  <div class="author-container--mobile" style="display:none"><p>By Jane Author</p></div>
  <div class="modal infographicModal" style="display:none"><p>Hidden modal chart text that must be kept.</p></div>
  <div class="pull-quote cmp-pullquote__marble">
    <div class="cmp-pullquote-main"><div class="cmp-pullquote-wrapper"><div class="cmp-pullquote">
      <div class="cmp-pullquote__quote"><p>The modern SaaS delivery model is quietly enabling cyber attackers.</p></div>
      <div class="cmp-pullquote__info-and-link"><div class="cmp-pullquote__person--details">
        <p class="cmp-pullquote__person--name">Pat Opet</p>
        <p class="cmp-pullquote__person--title">Chief information security officer, JPMorganChase</p>
      </div></div>
    </div></div></div>
  </div>
  <div class="related-insights__cards"><div class="card-article-with-image">
    <p class="eyebrow">Banking</p><h3>Healthcare markets outlook</h3><a class="card-link">Read more</a>
  </div></div>
  <footer><nav>Legal Privacy Cookies</nav></footer>
</body></html>`;
}

// The exact PRE-FIX root selection (for the discrimination check).
const OLD_SELECT = `(() => {
  var a = document.querySelector('article[data-testid="tweet"][role="article"]')
       || document.querySelector('article[role="article"]')
       || document.querySelector('main article')
       || document.querySelector('article, main, [role="main"]')
       || document.body;
  return (a.textContent || '').trim();
})()`;

async function main() {
  const walkerJs = extractWalkerJs();
  const fixture = buildFixture();
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.setContent(fixture, { waitUntil: "load" });

  const result = await page.evaluate(walkerJs);
  const oldShell = await page.evaluate(OLD_SELECT);
  await browser.close();

  const text = (result && result.text) || "";
  const words = text.split(/\s+/).filter(Boolean).length;

  const checks = [];
  const must = [
    "Enterprise AI adoption is pushing identity",
    "expanding threat surface",
    "Venture funding patterns reflect",
    "AI-native solutions to problems that AI itself",
  ];
  for (const m of must) checks.push([`body contains: "${m.slice(0, 40)}..."`, text.includes(m)]);
  checks.push(["body word count > 120", words > 120]);
  // Chrome must be stripped from the widened <body> walk.
  const chrome = ["Skip to main content", "Back to menu", "For Companies and Institutions",
                  "Reject All Confirm", "Healthcare markets outlook", "Read more",
                  "View text Version", "View Infographic", "Insights", "Commercial Banking"];
  for (const c of chrome) checks.push([`chrome stripped: "${c.slice(0, 30)}"`, !text.includes(c)]);
  // Pull-quote component must render as a blockquote (grouped, prefixed with >),
  // not scattered paragraphs, and must not double-wrap ("> >").
  checks.push(["pull-quote rendered as blockquote", /> The modern SaaS delivery model/.test(text)]);
  checks.push(["quote attribution grouped in blockquote", /> Pat Opet/.test(text) && /> Chief information security officer/.test(text)]);
  checks.push(["no double-wrapped quotes", !text.includes("> >")]);
  // Whitespace hygiene: no whitespace-only lines (render as a gray gap in
  // Obsidian) and no 4+-space-indented content lines (render as a code block).
  const outLines = text.split("\n");
  checks.push(["no whitespace-only lines", !outLines.some((l) => /^[ \t]+$/.test(l))]);
  checks.push(["no accidental code-block indentation", !outLines.some((l) => /^ {4,}\S/.test(l))]);
  // CSS-hidden responsive duplicates are skipped, but hidden modals are kept.
  checks.push(["hidden responsive duplicate skipped (byline once)", (text.match(/By Jane Author/g) || []).length === 1]);
  checks.push(["hidden modal content kept", text.includes("Hidden modal chart text that must be kept")]);
  // Discrimination: the old logic must have captured only the shell (no body).
  checks.push([
    "PRE-FIX logic captured shell only (proves test discriminates)",
    !oldShell.includes("Enterprise AI adoption is pushing identity"),
  ]);

  let ok = true;
  for (const [label, pass] of checks) {
    console.log(`  ${pass ? "PASS" : "FAIL"}  ${label}`);
    if (!pass) ok = false;
  }
  console.log(`\n  captured words: ${words}   (pre-fix shell chars: ${oldShell.length})`);

  if (!ok) {
    console.error("\nREGRESSION FAILED: walker did not capture the fragmented body.");
    process.exit(1);
  }
  console.log("\nOK: walker thin-root guard captures the full body.");
}

main().catch((e) => { console.error(e); process.exit(1); });
