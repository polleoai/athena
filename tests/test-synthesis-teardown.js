/**
 * test-synthesis-teardown.js  — node --test
 *
 * Verifies that callProvider() tears down the provider (calls abort()) on
 * both the success path and the error path (issue #196 regression guard).
 *
 * synthesis.js requires the vendored Gryphon provider-runtime from a TypeScript
 * source path that esbuild resolves at bundle time but vanilla `require` cannot.
 * We intercept Module._resolveFilename to redirect that import to a fake module
 * registered in the module cache BEFORE synthesis.js is loaded.
 */

"use strict";

const { test } = require("node:test");
const assert  = require("node:assert/strict");
const path    = require("path");
const Module  = require("module");

// ── Paths ────────────────────────────────────────────────────────────────────
const ATHENA_SRC    = path.resolve(__dirname, "../src/athena");
const SYNTHESIS_JS  = path.join(ATHENA_SRC, "synthesis.js");

// The vendor module string as synthesis.js passes it to require().
const VENDOR_REQUIRE_STR = "../../vendor/gryphon/packages/provider-runtime/src/index";

// A fake (non-existent) key we'll register in the cache so _resolveFilename
// doesn't fail when it can't find the real .ts file.
const FAKE_VENDOR_KEY = "__athena_test_fake_vendor__";

// ── Intercept module resolution ───────────────────────────────────────────────
// Hook _resolveFilename once, globally: whenever synthesis.js requests the
// vendor string, redirect to our fake key.  This must happen before any
// require(SYNTHESIS_JS) runs.

const _origResolve = Module._resolveFilename.bind(Module);
Module._resolveFilename = function (request, parent, isMain, options) {
  // Only intercept the specific vendor import from within synthesis.js.
  if (request === VENDOR_REQUIRE_STR &&
      parent && parent.filename === SYNTHESIS_JS) {
    return FAKE_VENDOR_KEY;
  }
  return _origResolve(request, parent, isMain, options);
};

// ── Helpers ───────────────────────────────────────────────────────────────────
function injectFakeVendor(createProviderFn) {
  const fakeModule = new Module(FAKE_VENDOR_KEY);
  fakeModule.loaded = true;
  fakeModule.exports = {
    createProvider:    createProviderFn,
    explainUnavailable: () => "provider unavailable",
  };
  Module._cache[FAKE_VENDOR_KEY] = fakeModule;
}

function clearCaches() {
  delete Module._cache[SYNTHESIS_JS];
  delete Module._cache[FAKE_VENDOR_KEY];
}

function makePlugin() {
  return {
    app: { vault: { adapter: { basePath: "/fake/vault" } } },
    settings: { model: "claude-opus-4" },
  };
}

// ── Tests ────────────────────────────────────────────────────────────────────

test("callProvider calls provider.abort() after a successful send", async () => {
  let abortCalled = false;

  const fakeProvider = {
    onMessage: null,
    onError:   null,
    send: async () => ({
      text: '{"summary":"Good summary text here that is long enough","body":"## Key Findings\\n- concrete finding\\n\\n## Relevance\\n- relevant context"}',
      cost: 0.001,
    }),
    abort: () => { abortCalled = true; },
  };

  // Clear first, then inject — order matters: clearCaches() deletes the fake
  // vendor key, so inject must come after.
  clearCaches();
  injectFakeVendor(() => fakeProvider);
  const { callProvider } = require(SYNTHESIS_JS);

  const result = await callProvider(makePlugin(), "test prompt");

  assert.equal(result.ok, true, "callProvider should succeed");
  assert.equal(abortCalled, true,
    "provider.abort() MUST be called after success — fixes issue #196 process leak");
});

test("callProvider calls provider.abort() when send() throws", async () => {
  let abortCalled = false;

  const fakeProvider = {
    onMessage: null,
    onError:   null,
    send: async () => { throw new Error("simulated network failure"); },
    abort: () => { abortCalled = true; },
  };

  clearCaches();
  injectFakeVendor(() => fakeProvider);
  const { callProvider } = require(SYNTHESIS_JS);

  const result = await callProvider(makePlugin(), "test prompt");

  assert.equal(result.ok,     false,          "callProvider should fail gracefully");
  assert.equal(result.reason, "provider-error", "reason should be provider-error");
  assert.equal(abortCalled, true,
    "provider.abort() MUST be called after error — fixes issue #196 process leak");
});

test("callProvider returns ok:false without crashing when no provider is available", async () => {
  // createProvider returns null — no process spawned, abort is irrelevant.
  clearCaches();
  injectFakeVendor(() => null);
  const { callProvider } = require(SYNTHESIS_JS);

  const result = await callProvider(makePlugin(), "test prompt");

  assert.equal(result.ok,     false,         "should fail cleanly");
  assert.equal(result.reason, "no-provider", "reason should be no-provider");
});

test("callProvider tolerates a provider with no abort() method (API providers)", async () => {
  // Safety: the guard `if (typeof provider.abort === "function")` means we
  // never throw on API-based providers that might lack abort().
  const fakeProvider = {
    onMessage: null,
    onError:   null,
    send: async () => ({ text: "some text", cost: 0 }),
    // deliberately no abort property
  };

  clearCaches();
  injectFakeVendor(() => fakeProvider);
  const { callProvider } = require(SYNTHESIS_JS);

  // Should not throw even though abort is missing.
  const result = await callProvider(makePlugin(), "test prompt");
  // ok may be false because the text isn't valid JSON — that's fine;
  // we're only checking no crash occurs.
  assert.ok(typeof result === "object", "should return a result object");
});
