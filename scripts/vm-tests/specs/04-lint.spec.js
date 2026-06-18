"use strict";

/**
 * 04-lint — AT-11 from docs/requirements-athena.md.
 *
 * Validates `bin/kb lint` runs to completion on a freshly-prepared
 * vault. `kb lint` is athena's structural-consistency guard (130+
 * checks); if any check crashes or the script aborts, the test fails
 * loudly. This is the broadest Python-pipeline regression catcher we
 * have outside the pytest suite — it touches every reader/writer in
 * bin/lib.
 *
 * Uses the Argus AF4 shell helper directly. Doesn't need the obsidian
 * fixture (lint is a pure Python invocation), but uses it for vault
 * setup convenience.
 *
 * P2 milestone (issue #184).
 */

const { test, expect, prepareKbVault } = require("./_helpers");
const argus = require("@shared/argus");

test.describe("AT-11: kb lint runs to completion", () => {
  test.beforeEach(async ({ obsidian }) => {
    await prepareKbVault(obsidian.vaultPath);
  });

  test("kb lint exits 0 + emits the expected report header", async ({ obsidian }) => {
    const sh = argus.helpers.shell.createShell({ cwd: obsidian.vaultPath });
    // kb is a Python program; invoke via the interpreter so the same command
    // runs on Windows (python) and POSIX (python3) guests (no shebang on Windows).
    const PY = process.env.ATHENA_PYTHON || (process.platform === "win32" ? "python" : "python3");
    const result = await sh.run(`${PY} bin/kb lint`, { timeoutMs: 90_000 });

    // kb lint may surface vault-level warnings (lots of "✗ ..." lines)
    // — that's expected on an empty vault and not a failure. What we
    // care about is that the script COMPLETES without a traceback or
    // crash. Exit code 0 means it walked all checks cleanly.
    expect(
      result.exitCode,
      `kb lint exited ${result.exitCode}:\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`,
    ).toBe(0);

    // Lint always prints a final summary banner.
    expect(result.stdout).toMatch(/checks?\s*·/i);

    // No Python tracebacks in either stream.
    expect(result.stdout).not.toMatch(/Traceback \(most recent call last\)/);
    expect(result.stderr).not.toMatch(/Traceback \(most recent call last\)/);
  });
});
