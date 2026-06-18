"use strict";

/**
 * 06-kb-commands-full — broaden AT-3 beyond stats/list/search/lint to the
 * rest of the non-LLM, non-interactive `kb` command surface, so a single
 * cross-platform run proves the whole Python dispatcher boots and exits
 * cleanly on each OS (the "test all the kb commands" ask).
 *
 * Pure-shell: runs `bin/kb <cmd>` directly via the Argus AF4 shell helper
 * against a throwaway prepared vault — NO Obsidian boot. Faster and
 * headless-VM-friendly (no Electron/display dependency). The plugin's
 * runMechanical path is already covered by 02-slash-commands; this spec
 * targets the command implementations themselves.
 *
 * Commands deliberately excluded: those that make LLM calls (reflect,
 * insight body synthesis, query) or prompt interactively without a
 * --yes escape — they belong to mocked-network specs (argus AF5).
 *
 * P2 milestone (issue #184).
 */

const { test, expect } = require("@playwright/test");
const argus = require("@shared/argus");
const { createPreparedVault } = require("./_helpers");

// One prepared vault shared across the read-only/idempotent commands in
// this file — each command is independent and none mutate shared state in
// a way that leaks across cases (journal/create write distinct files).
let vault;
let sh;

test.beforeAll(async () => {
  vault = await createPreparedVault();
  sh = argus.helpers.shell.createShell({
    cwd: vault.vaultPath,
    env: { ATHENA_KB_ROOT: vault.vaultPath },
    timeoutMs: 60_000,
  });
});

test.afterAll(async () => {
  if (vault) await vault.cleanup();
});

// kb is a Python program; invoke it through the interpreter so the same command
// runs on Windows (python) and POSIX (python3) guests alike. Spawning `bin/kb`
// directly ENOENTs on Windows (no shebang mechanism).
const PY = process.env.ATHENA_PYTHON || (process.platform === "win32" ? "python" : "python3");

// Helper: run a kb command, fail loudly with full output on non-zero exit,
// and guard against Python tracebacks leaking on either stream.
async function kb(args) {
  const r = await sh.run(`${PY} bin/kb ${args}`);
  expect(
    r.exitCode,
    `bin/kb ${args} exited ${r.exitCode}\nstdout:\n${r.stdout}\nstderr:\n${r.stderr}`,
  ).toBe(0);
  expect(r.stdout + r.stderr).not.toMatch(/Traceback \(most recent call last\)/);
  return r;
}

test("kb stats reports KB categories", async () => {
  const r = await kb("stats");
  expect(r.stdout.toLowerCase()).toMatch(/raw|wiki|sources|pages/);
});

test("kb rules shows the processing rules (read-only view)", async () => {
  await kb("rules");
});

test("kb index builds the search index without error", async () => {
  const r = await kb("index");
  expect(r.stdout.toLowerCase()).toMatch(/index|indexed|search/);
});

test("kb journal writes a learning entry", async () => {
  const r = await kb('journal "argus cross-platform smoke note"');
  // Journal command prints the created file path on success.
  expect(r.stdout.toLowerCase()).toMatch(/journal|created|saved|\.md/);
});

test("kb list --topics handles the topics filter", async () => {
  await kb("list --topics");
});

test("kb list --entities handles the entities filter", async () => {
  await kb("list --entities");
});

test("kb list --recent handles the recent filter", async () => {
  await kb("list --recent");
});

test("kb trash lists the (empty) trash without error", async () => {
  await kb("trash");
});
