# Athena — Community Review Disclosures

This document explains the elevated capabilities an automated plugin review flags
in Athena, why each is present, and how each is bounded. Athena is a personal
knowledge base for Obsidian: it captures sources, runs a Python synthesis engine
over your vault, and embeds the **Gryphon** AI assistant (with its runtime
guardrail). Some flagged capabilities come from Athena's own code; others come
from the bundled Gryphon assistant.

**Bundled Gryphon:** Athena bundles the Gryphon assistant, so Gryphon's own
capabilities (Electron IPC for the guardrail, CLI shell execution, its config
`fs` access, OS detection) are present in Athena's bundle too. Those are
documented in Gryphon's own disclosures
(`polleoai/gryphon → docs/community-review-disclosures.md`) and are, in short,
the mechanism that lets the guardrail gate what a coding CLI may do. This
document does not repeat them in full; the entries below add Athena's own
capabilities and note where Athena extends a Gryphon one.

All behaviour is local. Athena's network access is limited to source capture
(the URLs you ask it to ingest), local embedding calls, and the model provider
Gryphon talks to with your own API key. It has no telemetry and sends no usage,
identity, or vault data anywhere.

## Shell execution (`child_process`) — Athena's Python engine + CLI detection

**Why:** Athena's knowledge-base engine is Python. The plugin spawns your Python
interpreter to run Athena's own bundled scripts (`bin/lib/…`) for ingest,
synthesis, lint, search, and reflection, and it runs a short Python-validity
probe. (It also inherits Gryphon's CLI-provider spawning — see Gryphon's
disclosures.)

**Bounds:** It executes only (a) Athena's own bundled Python modules shipped
inside the plugin, invoked as `python <bundled-script> …`, and (b) the version
probe of the Python interpreter and of the AI CLI you select. It never fetches or
runs remote or arbitrary code; the scripts it runs are the ones shipped in the
plugin and visible in the source.

## Direct filesystem access (`fs`) — the knowledge base

**Why:** Athena *is* a file-backed knowledge base. It reads and writes the vault
directories it manages (`raw/`, `wiki/`, `inbox/`), stages its bundled Python
sources into the plugin directory at load, and uses the OS temp dir for transient
capture artifacts. It uses `fs` directly (rather than only the vault API) because
the Python engine, the plugin-asset staging, and temp-file capture operate on
paths the vault API does not cover.

**Bounds:** Operations are scoped to the user's vault and the OS temp directory.
Athena does not read unrelated system files. (Vault reads/writes that *can* go
through the Obsidian API do, and are reported as **Pass**.)

## Clipboard (write-only)

**Why:** During setup, Athena copies a couple of setup strings to the clipboard
for convenience — the vault name and the `inbox/Clippings` capture path — so you
can paste them into the Web Clipper configuration.

**Bounds:** Athena only **writes** the clipboard (`clipboard.writeText`); it never
reads it. The values written are non-sensitive setup strings, not vault content.

## System information (`os.platform()` / `os.release()` / `os.homedir()`)

**Why:** Platform detection selects OS-correct behaviour (Python/CLI spawn paths,
Windows vs macOS/Linux differences); `os.homedir()`/`os.tmpdir()` locate config
and temp paths.

**Bounds:** Athena does **not** read `os.hostname()`, `os.userInfo()`, or
`os.networkInterfaces()` — none of the machine-fingerprinting calls the warning
names appear in Athena's or Gryphon's source. Nothing derived from OS detection
is transmitted.

## Electron IPC — via the bundled Gryphon guardrail

**Why:** The IPC the review flags belongs to the bundled Gryphon guardrail, which
uses it to enforce the per-command approval layer for CLI providers. Athena's own
code only checks whether `ipcRenderer` is available. See Gryphon's disclosures for
the full rationale — in short, it is the enforcement channel that *adds* safety.

**Bounds:** Carries only Gryphon's approval traffic; no other privileged surface.

---

*The above are the intended, bounded capabilities of a Python-backed knowledge
base that embeds a guardrailed AI assistant. None collect or transmit identity or
usage data.*
