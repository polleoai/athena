# VM testing — Windows and Linux

Athena is desktop-only (per `manifest.json`'s `isDesktopOnly: true`), so cross-platform smoke tests target three OSes:

- macOS — dev box, primary test environment
- Linux — typically Ubuntu Desktop or Debian under UTM / Parallels / VirtualBox
- Windows 11 — typically under UTM or Parallels

The Obsidian plugin runtime is what we test on each platform. The terminal `kb` command set (Python KB engine) is also cross-platform but is *not* required for the plugin smoke test — the chat surface, settings panel, and slash commands all work without it.

## One-time VM setup

Inside each VM:

1. Install Obsidian — [obsidian.md/download](https://obsidian.md/download).
2. Create or open a small test vault. The deploy script defaults to `~/athena-test-vault/` on Linux and `%USERPROFILE%\athena-test-vault\` on Windows.
3. Once Obsidian has initialized the vault, close Obsidian. The deploy script writes into `.obsidian/plugins/` and Obsidian sometimes caches the plugin list while running.
4. Verify the VM can reach the host machine — `ping <host-ip>` from inside the VM. Under most UTM / Parallels NAT modes the host is the default gateway from the VM's view.

You don't need API keys for the plugin-runtime smoke test. The Settings → Athena and Settings → Gryphon panels render without any provider configured; you'll see the unconfigured-state UI which is part of what we want to verify.

## Cycle 1 — local install (macOS dev)

When iterating on macOS, install into a sibling test vault via:

```bash
./scripts/install-to-vault.sh ~/Documents/athena-test-vault
```

This copies the built bundle into the vault's `.obsidian/plugins/athena/` and `.obsidian/plugins/gryphon/`. Reload Obsidian (`Cmd+R`) and toggle the plugins off + on in Settings → Community plugins to pick up the new bundle.

Pass `--athena-only` if the target vault already has Gryphon installed via the Community Plugins directory or BRAT, and you only want to refresh the Athena bundle.

## Cycle 2 — push to a VM (Linux / Windows)

On the host (macOS):

```bash
./scripts/deploy-to-vm.sh
```

The script rebuilds the bundle, packages both plugin dirs into `/tmp/athena-plugin.tar.gz`, prints platform-specific one-liner commands for the VM, then starts a Python HTTP server on port 8000. Ctrl+C to stop.

Inside the **Linux VM**, paste the printed Linux one-liner (it self-discovers the host IP via `ip route`).

Inside the **Windows VM**, paste the printed PowerShell one-liner. The script substitutes a best-guess host IP into the snippet; if that IP isn't reachable from the VM (e.g. the VM is on a different network adapter than the host's `en0`), run `ipconfig | findstr Gateway` inside the VM and replace `$HOST_IP = '...'` with the printed Default Gateway address.

After the tarball extracts, reload Obsidian inside the VM and toggle the plugins off+on. You should see:

- **Athena** loaded in Settings → Community plugins
- **Gryphon** loaded in Settings → Community plugins
- The Athena setup wizard or the Gryphon chat panel appears in the right sidebar
- No JS console errors in `Ctrl+Shift+I` → Console

## Smoke test checklist (each VM)

These don't require a configured LLM provider — they verify the plugin loaded and the UI renders:

- [ ] Athena's setup wizard renders (or the in-vault `Athena/` folder is created on first launch)
- [ ] Settings → Athena tab opens without errors
- [ ] Settings → Gryphon tab opens without errors
- [ ] Gryphon chat panel opens via the ribbon icon or `Cmd/Ctrl+P` → "Open Gryphon chat"
- [ ] `/help` typed in the chat panel shows the slash-command modal
- [ ] No console errors during plugin enable/reload (`Ctrl+Shift+I`)
- [ ] Path handling: Athena's `vault.adapter.basePath` should print a valid native path (e.g. `C:\Users\...` on Windows, `/home/...` on Linux). Verify via the Athena settings tab or the chat panel debug output if any.

If you have an API key handy, add the optional checks:

- [ ] Configure Anthropic API key in Settings → Gryphon → Anthropic API key
- [ ] Send a one-message chat request, confirm streaming response renders
- [ ] If the test vault has any markdown files, ask Gryphon to read one and confirm the tool-use approval modal appears (per Gryphon's built-in security layer)

## Fast iteration loop

For a tight fix → test cycle:

1. Edit code on macOS
2. `./scripts/deploy-to-vm.sh --no-build` if you've already built, or just `./scripts/deploy-to-vm.sh` to rebuild
3. Paste the Linux/Windows one-liner in each VM
4. In each VM's Obsidian, toggle Athena off+on
5. Re-test

The HTTP server stays running, so you can re-paste the same one-liner after a rebuild — the VM pulls a fresh tarball each time.

## When something fails

- **Athena loads but Gryphon doesn't**: check that the Gryphon plugin dir got copied. Re-run `deploy-to-vm.sh` without `--athena-only`.
- **Plugin loads but chat doesn't connect**: missing or invalid API key. Check Settings → Gryphon. The plugin-runtime smoke test doesn't require this — only end-to-end chat does.
- **`tar` not found on Windows**: Windows 10 1803+ ships `tar` in `System32`. If it's missing on an older build, install Git for Windows (which provides a portable `tar.exe`) or use 7-Zip's CLI.
- **Curl 404 from the VM**: the host firewall is blocking port 8000. Allow incoming on the macOS firewall or pick a different port with `--port 9000`.
- **PowerShell parser error on the host-IP line**: the `${HOST_IP}` braces were lost in paste. Retype them around the variable name and re-run.
