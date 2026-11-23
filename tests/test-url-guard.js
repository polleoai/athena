/**
 * test-url-guard.js — node --test
 *
 * capture-deep's browser guard (#243): every request the capture page makes
 * is refused when it targets localhost / a private network, by literal, by
 * name, or by what the name resolves to. Also pins that the guard module
 * ships wherever capture-deep.js ships (npm-pack FILES), since capture-deep
 * require()s it and would crash without it.
 */
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const guard = require(path.join(ROOT, "scripts", "lib", "url-guard.js"));

const INTERNAL = [
  "http://localhost/", "http://foo.localhost:3000/", "http://printer.local/",
  "http://metadata.google.internal/", "http://127.0.0.1/", "http://127.1/",
  "http://0x7f000001/", "http://2130706433/", "http://0177.0.0.1/", "http://0.0.0.0/",
  "http://10.0.0.5/", "http://172.16.3.4/", "http://192.168.1.1/", "http://100.64.0.1/",
  "http://169.254.169.254/latest/meta-data/", "http://[::1]/", "http://[::ffff:127.0.0.1]/",
  "http://[::ffff:a9fe:a9fe]/", "http://[64:ff9b::a9fe:a9fe]/", "http://[2002:a00:1::]/",
  "http://[fe80::1]/", "http://[fd00::1]/", "http://user:pw@example.com/",
  "file:///etc/passwd", "ftp://example.com/", "http://１２７.０.０.１/",
];
const PUBLIC = [
  "https://x.com/i/article/123", "https://www.linkedin.com/feed/", "http://8.8.8.8/",
  "https://[2606:4700:4700::1111]/", "http://[64:ff9b::808:808]/", "https://localhost-tools.example.com/",
];

test("internal URLs refused by name/literal", () => {
  for (const u of INTERNAL) assert.ok(guard.internalUrlReason(u), u);
});

test("IPv6 forms that embed an IPv4 address are internal (security review, 11-23)", () => {
  for (const ip of ["::7f00:1", "::127.0.0.1", "::a9fe:a9fe", "::ffff:0:7f00:1", "::ffff:0:8.8.8.8",
                    "64:ff9b:1::1", "64:ff9b:1::808:808"]) {
    assert.equal(guard.ipIsInternal(ip), true, ip);
  }
  for (const ip of ["2606:4700:4700::1111", "64:ff9b::808:808", "::ffff:8.8.8.8"]) {
    assert.equal(guard.ipIsInternal(ip), false, ip);
  }
});

test("public URLs allowed", () => {
  for (const u of PUBLIC) assert.equal(guard.internalUrlReason(u), "", u);
});

const fakeLookup = (table) => async (host) => {
  if (!(host in table)) throw Object.assign(new Error("ENOTFOUND"), { code: "ENOTFOUND" });
  return table[host].map((address) => ({ address, family: address.includes(":") ? 6 : 4 }));
};

test("a public-looking name that resolves to a private address is refused", async () => {
  const lookup = fakeLookup({ "rebind.attacker.example": ["93.184.215.14", "10.0.0.1"], "ok.example": ["93.184.215.14"] });
  assert.match(await guard.resolvedUrlReason("https://rebind.attacker.example/", lookup), /non-public/);
  assert.equal(await guard.resolvedUrlReason("https://ok.example/", lookup), "");
  // Fail closed (security review, 11-23): Chromium resolves names itself (a
  // proxy, DoH), so "our lookup failed" says nothing about where it connects.
  assert.match(await guard.resolvedUrlReason("https://nxdomain.example/", lookup), /could not resolve/);
});

function fakePage() {
  const seen = { handler: null, ws: null, popup: null };
  return {
    seen,
    async route(pattern, handler) { seen.pattern = pattern; seen.handler = handler; },
    async routeWebSocket(pattern, handler) { seen.ws = handler; },
    on(ev, fn) { if (ev === "popup") seen.popup = fn; },
    async fire(url, nav = true) {
      let outcome = null;
      await seen.handler({
        request: () => ({ url: () => url, isNavigationRequest: () => nav }),
        abort: async (why) => { outcome = `abort:${why}`; },
        continue: async () => { outcome = "continue"; },
      });
      return outcome;
    },
  };
}

test("installUrlGuard aborts internal requests (redirect targets, frames, rebinding) and continues public ones", async () => {
  const page = fakePage();
  const logs = [];
  await guard.installUrlGuard(page, {
    lookup: fakeLookup({ "rebind.attacker.example": ["10.0.0.1"], "www.linkedin.com": ["13.107.42.14"] }),
    log: (m) => logs.push(m),
  });
  assert.equal(page.seen.pattern, "**/*");
  assert.equal(await page.fire("https://www.linkedin.com/feed/"), "continue");
  assert.equal(await page.fire("http://169.254.169.254/latest/meta-data/"), "abort:blockedbyclient");
  assert.equal(await page.fire("http://localhost:8080/admin", false), "abort:blockedbyclient");
  assert.equal(await page.fire("https://rebind.attacker.example/x"), "abort:blockedbyclient");
  assert.equal(logs.length, 3);
});

test("websockets, popups and a flipped DNS answer are all checked", async () => {
  const table = { "flip.example": ["93.184.215.14"] };
  let t = 0;
  const page = fakePage();
  await guard.installUrlGuard(page, { lookup: fakeLookup(table), log: () => {}, now: () => t });
  assert.equal(await page.fire("https://flip.example/a"), "continue");
  table["flip.example"] = ["10.0.0.1"];
  t += 5000; // past the TTL: the new answer is looked up again
  assert.equal(await page.fire("https://flip.example/b"), "abort:blockedbyclient");

  let closed = false, connected = false;
  await page.seen.ws({ url: () => "ws://127.0.0.1:9222/devtools", close: () => { closed = true; }, connectToServer: () => { connected = true; } });
  assert.ok(closed && !connected, "internal websocket closed");

});

test("a popup of the guarded page is guarded, then closed", async () => {
  const page = fakePage();
  page.opener = () => null;
  await guard.installUrlGuard(page, { lookup: fakeLookup({}), log: () => {} });
  const popup = fakePage();
  popup.opener = () => page;
  let closed = false;
  popup.close = async () => { closed = true; };
  await page.seen.popup(popup);
  assert.ok(popup.seen.handler, "guard installed on the popup");
  assert.ok(closed, "popup closed");
});

test("no automated caller runs capture-deep with --cdp-attach (its guard is page-level only)", () => {
  const offenders = [];
  const walk = (dir) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) { if (e.name !== "__pycache__" && e.name !== "node_modules") walk(p); continue; }
      if (!/\.(py|js|ts|sh)$/.test(e.name) && !["kb", "kb-capture", "kb-legacy"].includes(e.name)) continue;
      if (fs.readFileSync(p, "utf8").includes("--cdp-attach")) offenders.push(path.relative(ROOT, p));
    }
  };
  for (const d of ["bin", "server", "src"]) walk(path.join(ROOT, d));
  assert.deepEqual(offenders, []);
});

test("hardening script disables window.open, target=_blank and SW registration", async () => {
  const listeners = {};
  const a = { attrs: { target: "_blank" }, removeAttribute(n) { delete this.attrs[n]; } };
  const sw = {};
  const env = {
    window: { open: () => "popup" },
    document: { addEventListener: (ev, fn) => { listeners[ev] = fn; } },
    navigator: { serviceWorker: sw },
  };
  new Function("window", "document", "navigator", `(${guard.pageHardeningScript.toString()})()`)(env.window, env.document, env.navigator);
  assert.equal(env.window.open(), null);
  listeners.click({ target: { closest: () => a } });
  assert.equal(a.attrs.target, undefined);
  await assert.rejects(sw.register("/sw.js"), /disabled/);
});

test("owned browser context blocks service workers", () => {
  const src = fs.readFileSync(path.join(ROOT, "scripts", "capture-deep.js"), "utf8");
  assert.match(src, /launchPersistentContext\(AUTH_DIR, \{\s*serviceWorkers: "block"/);
});

test("capture-deep installs the guard on its page and checks the start URL before launching", () => {
  const src = fs.readFileSync(path.join(ROOT, "scripts", "capture-deep.js"), "utf8");
  assert.match(src, /require\("\.\/lib\/url-guard"\)/);
  assert.match(src, /hardenPage\(page\)[\s\S]*installUrlGuard\(page\)[\s\S]*page\.goto\(url/);
  assert.match(src, /launchPersistentContext\(AUTH_DIR, \{[\s\S]*?\}\);\s*await urlGuard\.installUrlGuard\(ctx\)/);
  const startCheck = src.indexOf("resolvedUrlReason(url)");
  const launch = src.indexOf("launchPersistentContext(AUTH_DIR");
  assert.ok(startCheck > 0 && startCheck < launch, "start URL is checked before any browser launch");
});

test("every relative module capture-deep.js requires ships in the npm package", () => {
  const src = fs.readFileSync(path.join(ROOT, "scripts", "capture-deep.js"), "utf8");
  const pack = fs.readFileSync(path.join(ROOT, "scripts", "npm-pack.mjs"), "utf8");
  const files = pack.match(/const FILES = \[([\s\S]*?)\];/)[1];
  for (const [, rel] of src.matchAll(/require\("(\.[^"]+)"\)/g)) {
    const shipped = path.posix.join("scripts", rel.replace(/^\.\//, "")) + (rel.endsWith(".js") ? "" : ".js");
    assert.ok(files.includes(`"${shipped}"`), `${shipped} is required by capture-deep.js but not in npm-pack FILES`);
  }
});
