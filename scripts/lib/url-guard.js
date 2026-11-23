/**
 * url-guard.js — keep capture-deep's browser off the local machine and the
 * private network (#243; JS twin of bin/lib/url_safety.py).
 *
 * capture-deep navigates to URLs Athena did not choose (an X article or a
 * LinkedIn post found inside a captured page). #242 checks the URL before
 * capture-deep starts; this guard checks every request the browser then
 * makes, so a redirect, a meta-refresh, a frame or a script request to
 * 169.254.169.254 / localhost / 10.x is aborted, and a public name that
 * resolves to a private address (DNS rebinding) is aborted too.
 *
 * Residual (--cdp-attach only): the user's own Chrome can't be given a
 * context-level guard, so a popup's first request, or a navigation started
 * by a script path the init script doesn't cover, can race the page guard.
 * Acceptable because --cdp-attach is manual-only (the user types it, with
 * a URL they chose); tests/test-url-guard.js fails if an automated caller
 * ever passes it. Closing it fully needs CDP Target.setAutoAttach with
 * waitForDebuggerOnStart.
 *
 * Residual: Chromium resolves the name again itself after our lookup, so a
 * rebinding server with a sub-second TTL can still race the check. Closing
 * that needs per-request resolver pinning (--host-resolver-rules), not done.
 */
"use strict";

const dns = require("dns");
const net = require("net");

const INTERNAL_NAMES = new Set(["localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"]);
const INTERNAL_SUFFIXES = [".localhost", ".local", ".internal", ".home.arpa", ".lan", ".intranet"];

const V4_BLOCKED = new net.BlockList();
for (const [a, p] of [
  ["0.0.0.0", 8], ["10.0.0.0", 8], ["100.64.0.0", 10], ["127.0.0.0", 8],
  ["169.254.0.0", 16], ["172.16.0.0", 12], ["192.0.0.0", 24], ["192.0.2.0", 24],
  ["192.88.99.0", 24], ["192.168.0.0", 16], ["198.18.0.0", 15], ["198.51.100.0", 24],
  ["203.0.113.0", 24], ["224.0.0.0", 4], ["240.0.0.0", 4],
]) V4_BLOCKED.addSubnet(a, p, "ipv4");

const V6_BLOCKED = new net.BlockList();
for (const [a, p] of [
  ["::", 128], ["::1", 128], ["100::", 64], ["2001::", 23], ["2001:db8::", 32],
  ["fc00::", 7], ["fe80::", 10], ["fec0::", 10], ["ff00::", 8],
  // IPv4-translated ::ffff:0:a.b.c.d and local-use NAT64 64:ff9b:1::/48 also
  // carry an IPv4 address; refused outright (security review, 11-23).
  ["::ffff:0:0:0", 96], ["64:ff9b:1::", 48],
]) V6_BLOCKED.addSubnet(a, p, "ipv6");

/** Expand an IPv6 address to 8 numeric hextets (handles :: and a dotted tail). */
function hextets(addr) {
  let a = addr.split("%")[0].toLowerCase();
  const tail = a.match(/(\d+\.\d+\.\d+\.\d+)$/);
  if (tail) {
    const o = tail[1].split(".").map(Number);
    a = a.slice(0, -tail[1].length) + ((o[0] << 8) | o[1]).toString(16) + ":" + ((o[2] << 8) | o[3]).toString(16);
  }
  const [head, rest] = a.includes("::") ? a.split("::") : [a, null];
  const h = head ? head.split(":") : [];
  const r = rest ? rest.split(":") : [];
  const fill = rest === null ? [] : new Array(8 - h.length - r.length).fill("0");
  return [...h, ...fill, ...r].map((x) => parseInt(x || "0", 16));
}

function v4FromHextets(hi, lo) {
  return [hi >> 8, hi & 255, lo >> 8, lo & 255].join(".");
}

/** True if `ip` (v4 or v6 literal) is not a public unicast address. */
function ipIsInternal(ip) {
  const fam = net.isIP(ip.split("%")[0]);
  if (fam === 4) return V4_BLOCKED.check(ip, "ipv4");
  if (fam !== 6) return true; // not an address at all: refuse, never guess
  const h = hextets(ip);
  // IPv4-mapped ::ffff:a.b.c.d, NAT64 64:ff9b::/96, 6to4 2002::/16 embed an
  // IPv4 address; judge the embedded one (a NAT64 network maps every site).
  if (h.slice(0, 5).every((x) => x === 0) && h[5] === 0xffff) return ipIsInternal(v4FromHextets(h[6], h[7]));
  // IPv4-compatible ::a.b.c.d (::/96, deprecated): no legitimate use; refuse.
  if (h.slice(0, 6).every((x) => x === 0)) return true;
  if (h[0] === 0x64 && h[1] === 0xff9b && h.slice(2, 6).every((x) => x === 0)) return ipIsInternal(v4FromHextets(h[6], h[7]));
  if (h[0] === 0x2002) return ipIsInternal(v4FromHextets(h[1], h[2]));
  return V6_BLOCKED.check(ip.split("%")[0], "ipv6");
}

/** Why `url` must not be requested (name/literal only, no DNS), or "". */
function internalUrlReason(url) {
  let u;
  try { u = new URL(url); } catch { return "unparseable URL"; }
  if (!["http:", "https:", "ws:", "wss:"].includes(u.protocol)) return `scheme ${u.protocol} is not http(s)`;
  if (u.username || u.password) return "URL carries userinfo";
  // WHATWG URL already normalised legacy IPv4 (0x7f000001, 127.1), fullwidth
  // digits and IDNA, so hostname is what the browser will resolve.
  let host = u.hostname.toLowerCase().replace(/\.$/, "");
  if (host.startsWith("[")) host = host.slice(1, -1);
  if (!host) return "no host";
  if (INTERNAL_NAMES.has(host) || INTERNAL_SUFFIXES.some((s) => host.endsWith(s))) return `host ${host} is a local name`;
  if (net.isIP(host) && ipIsInternal(host)) return `host ${host} is a non-public address`;
  return "";
}

/**
 * internalUrlReason plus a DNS check: refuse if ANY address the name
 * resolves to is non-public. A lookup failure is not a refusal (the browser
 * will fail on its own). `lookup` is injectable for tests.
 */
async function resolvedUrlReason(url, lookup = dns.promises.lookup) {
  const r = internalUrlReason(url);
  if (r) return r;
  let host = new URL(url).hostname.replace(/\.$/, "");
  if (host.startsWith("[")) return ""; // literal, already checked
  if (net.isIP(host)) return "";
  let addrs;
  // Fail closed: Chromium resolves names itself (a proxy, DoH), so a lookup
  // that fails here says nothing about where it would connect (security
  // review, 11-23). The Python side can pass on a failed pre-check because
  // its fetches go through pinned connections; the browser path has none.
  try { addrs = await lookup(host, { all: true }); } catch (e) { return `could not resolve ${host} (${e && e.code || "lookup failed"}); refusing`; }
  if (!addrs || !addrs.length) return `could not resolve ${host} (no addresses); refusing`;
  for (const { address } of addrs) {
    if (ipIsInternal(address)) return `${host} resolves to non-public address ${address}`;
  }
  return "";
}

const DNS_TTL_MS = 2000;

/**
 * Route every request of a Playwright Page (all its frames) or of a whole
 * BrowserContext through the guard. capture-deep guards the context it
 * launched itself (that covers popups before their first request) and,
 * always, the capture page: in --cdp-attach mode the context is the user's
 * own Chrome, whose other tabs must not be touched, so only the page is. WebSockets are
 * checked too (page.routeWebSocket, Playwright >= 1.48). Service workers
 * bypass page.route: launch the owned context with serviceWorkers: "block".
 * The name/literal check runs on every request; a DNS answer is reused for
 * at most DNS_TTL_MS so a flipped answer is seen again soon.
 */
async function installUrlGuard(page, { lookup, log = console.error, now = Date.now } = {}) {
  const dnsCache = new Map();
  const reasonFor = async (url) => {
    const r = internalUrlReason(url);
    if (r) return r;
    const host = new URL(url).hostname;
    const hit = dnsCache.get(host);
    if (hit && now() - hit.at < DNS_TTL_MS) return hit.reason;
    const reason = await resolvedUrlReason(url, lookup);
    dnsCache.set(host, { at: now(), reason });
    return reason;
  };
  await page.route("**/*", async (route) => {
    const req = route.request();
    const reason = await reasonFor(req.url());
    if (reason) {
      log(`capture-deep: blocked ${req.isNavigationRequest() ? "navigation" : "request"}: ${reason}`);
      return route.abort("blockedbyclient");
    }
    return route.continue();
  });
  if (typeof page.routeWebSocket === "function") {
    await page.routeWebSocket(/.*/, async (ws) => {
      const reason = await reasonFor(ws.url());
      if (reason) {
        log(`capture-deep: blocked websocket: ${reason}`);
        return ws.close();
      }
      ws.connectToServer();
    });
  }
  if (typeof page.on === "function" && typeof page.opener === "function") {
    // A Page: captures never need popups. Its first request can leave
    // before a guard could be installed on it, so close it instead; the
    // init script below stops most popups from opening at all, and in owned
    // mode the context-level guard already covers them.
    page.on("popup", async (popup) => {
      // Guard first (catches whatever it can of the popup's requests), then
      // close: captures never need popups.
      log("capture-deep: closing a popup opened by the captured page");
      try { await installUrlGuard(popup, { lookup, log, now }); } catch { /* closing anyway */ }
      popup.close().catch(() => {});
    });
  }
}

/**
 * In-page hardening for the guarded page, installed before navigation:
 * no window.open, no target=_blank links or forms (both open popups whose
 * first request races any guard), no service-worker registration (SW
 * requests bypass page.route; --cdp-attach can't set serviceWorkers:block).
 */
function pageHardeningScript() {
  window.open = () => null;
  const strip = (e) => {
    const el = e.target && e.target.closest && e.target.closest("a[target], area[target], form[target]");
    if (el) el.removeAttribute("target");
  };
  document.addEventListener("click", strip, true);
  document.addEventListener("submit", strip, true);
  try {
    if (navigator.serviceWorker) {
      Object.defineProperty(navigator.serviceWorker, "register", {
        value: () => Promise.reject(new Error("service workers are disabled during capture")),
      });
    }
  } catch { /* best effort */ }
}

async function hardenPage(page) {
  await page.addInitScript(pageHardeningScript);
}

module.exports = { internalUrlReason, resolvedUrlReason, installUrlGuard, hardenPage, pageHardeningScript, ipIsInternal };
