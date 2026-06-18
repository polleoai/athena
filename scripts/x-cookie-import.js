#!/usr/bin/env node
/**
 * Import an existing X (Twitter) session into Athena's Playwright profile —
 * WITHOUT automating a login (which X actively blocks: "Preparing
 * verification" loops, "not allowed to log in at this time").
 *
 * You are already logged into X in your normal browser. This lifts that
 * session's cookie into the shared persistent profile
 * (~/.athena/playwright-userdata/) so future X-Article captures run headless.
 *
 * Get the cookie from your logged-in browser:
 *   1. Open https://x.com (logged in) → DevTools (Cmd+Opt+I)
 *   2. Application ▸ Storage ▸ Cookies ▸ https://x.com
 *   3. Copy the VALUE of `auth_token` (and optionally `ct0`).
 *
 * Run it in your terminal, passing the cookie via ENV (never as an argument —
 * argv is visible in `ps`; the token is a session secret, treated in-memory
 * only and stored solely in the Chromium profile, never printed or committed):
 *
 *   X_AUTH_TOKEN=<auth_token value> X_CT0=<ct0 value> node scripts/x-cookie-import.js
 *
 * (X_CT0 is optional but recommended.) The script validates the session by
 * loading x.com and checking you're recognized, then writes the auth marker.
 */
const { chromium } = require("playwright");
const path = require("path");
const fs = require("fs");
const os = require("os");

const AUTH_DIR = path.join(os.homedir(), ".athena", "playwright-userdata");
const X_MARKER = path.join(AUTH_DIR, ".athena-x-auth-confirmed");
const UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

async function main() {
  const authToken = process.env.X_AUTH_TOKEN;
  const ct0 = process.env.X_CT0;
  if (!authToken) {
    console.error("ERROR: set X_AUTH_TOKEN (the auth_token cookie value) in the");
    console.error("environment. See the header of this file for how to find it.");
    process.exit(2);
  }
  fs.mkdirSync(AUTH_DIR, { recursive: true });

  // CRITICAL: set an explicit `expires`. A persistent context only flushes
  // cookies WITH an expiry to its on-disk SQLite store; an expiry-less cookie
  // is a *session* cookie that lives in memory and is lost on ctx.close() — so
  // the import would "validate" (cookies live this run) yet vanish before the
  // next headless capture launch. ~400 days mirrors X's own auth_token TTL.
  const expires = Math.floor(Date.now() / 1000) + 400 * 24 * 60 * 60;
  const cookies = [
    { name: "auth_token", value: authToken, domain: ".x.com", path: "/",
      httpOnly: true, secure: true, sameSite: "None", expires },
  ];
  if (ct0) {
    cookies.push({ name: "ct0", value: ct0, domain: ".x.com", path: "/",
      httpOnly: false, secure: true, sameSite: "Lax", expires });
  }

  // Headless is fine — no login flow runs, so there's no automation challenge
  // to solve. We only carry an already-valid cookie.
  const ctx = await chromium.launchPersistentContext(AUTH_DIR, {
    headless: true,
    userAgent: UA,
    args: ["--disable-blink-features=AutomationControlled"],
    ignoreDefaultArgs: ["--enable-automation"],
  });
  await ctx.addCookies(cookies);

  // Validate: load the home timeline and check we're NOT bounced to /login.
  const page = ctx.pages()[0] || (await ctx.newPage());
  await page.goto("https://x.com/home", { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.waitForTimeout(3000);
  const url = page.url();
  const loggedOut = /\/login|\/i\/flow\/login|\/account\/access/.test(url);
  if (loggedOut) {
    console.error("");
    console.error("⚠️  Session NOT valid — x.com bounced to: " + url);
    console.error("    The auth_token may be stale or mistyped. Recopy it from a");
    console.error("    freshly-loaded, logged-in x.com tab and retry.");
    await ctx.close();
    process.exit(1);
  }
  await ctx.close();

  // Prove PERSISTENCE, not just liveness: relaunch a fresh persistent context
  // and read the on-disk cookie store. Reading ctx.cookies() on the still-open
  // import context would pass even for in-memory session cookies — the exact
  // false-positive that hid this bug. Only a relaunch confirms the flush.
  const verifyCtx = await chromium.launchPersistentContext(AUTH_DIR, { headless: true });
  const saved = await verifyCtx.cookies("https://x.com");
  const ok = saved.find((c) => c.name === "auth_token");
  await verifyCtx.close();
  if (!ok) {
    console.error("⚠️  Cookie did not persist to the profile store — retry.");
    process.exit(1);
  }
  fs.writeFileSync(X_MARKER, new Date().toISOString());
  console.error("✓ X session imported and validated. Athena can now deep-capture");
  console.error("  X Articles headlessly (until the cookie expires).");
  process.exit(0);
}

main().catch((e) => {
  console.error("x-cookie-import failed:", e && e.message);
  process.exit(1);
});
