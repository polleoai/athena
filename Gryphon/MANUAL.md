# Gryphon — User Manual

Gryphon is a chat plugin that connects Obsidian to Claude. You can read
and edit your vault files, run shell commands, and search the web — all
inside Obsidian.

This manual lives in your vault at `Gryphon/MANUAL.md`. Edit or delete
freely; it's seeded on first install but never overwritten.

---

## Getting started

Gryphon needs **one** of these:

- **Claude Code CLI** installed locally — uses your Anthropic Pro/Max
  subscription, no per-token cost
- **Anthropic API key** from [console.anthropic.com](https://console.anthropic.com)
  — pay-per-token, works without a subscription

If both are available, Gryphon uses the CLI by default (subscription =
zero marginal cost). You can force CLI or SDK in **Settings → Gryphon
→ Provider**.

If neither is set up when you open Gryphon for the first time, you'll
see a welcome panel with two cards — one for each path. Click the
appropriate "Use this" button (or follow the install link) to
configure.

---

## The chat panel

Open Gryphon via:

- The "send" icon in the left ribbon, OR
- Command palette → **"Gryphon: Open chat"**, OR
- Hotkey (Settings → Hotkeys → search "Gryphon")

The chat panel docks to the right sidebar by default. To open in the
main editor area instead, toggle **Settings → Gryphon → Open in main
tab**.

### Sending messages

- **Enter** sends the message
- **Shift+Enter** inserts a newline (multi-line input)
- The send button (paper-plane icon) also sends

While Claude is responding, the input is disabled and a "Stop" button
appears in the toolbar. Click it (or use `/stop`) to abort the turn.

### Auto-context

Every message you send is silently prefixed with a small
`[gryphon-context]` block telling Claude what file you currently have
open and which folder it's in. References like *"this note"* or *"this
folder"* in your prompt resolve correctly without you having to spell
them out.

This adds ~50 tokens per message; cost is negligible.

---

## Slash commands

Type `/` in the input to see all available commands in the
autocomplete dropdown. Press **Tab** to complete the highlighted entry,
**Enter** to send.

| Command | What it does |
|---|---|
| `/clear` | Start a new session (with confirmation if non-empty) |
| `/compact` | Summarize the conversation and continue with the summary as context |
| `/context` | Show context-window usage (% of model's max) |
| `/cost` | Show session cost (suffixed `(est.)` in SDK mode) |
| `/effort` | Switch effort level (low / medium / high) |
| `/export` | Save the conversation as a note in `Gryphon/Exports/` |
| `/help` | Open this help reference as a modal |
| `/model` | Switch model (haiku / sonnet / opus) |
| `/perm` | Switch permission mode (Prompt / Safe / YOLO / Plan) |
| `/quote` | Insert highlighted editor text as a quoted reference |
| `/settings` | Open Gryphon settings |
| `/stop` | Stop the current turn |
| `/usage` | Show messages, cost, tokens, duration |

Plus any custom skills you've created — see **Skills** below.

---

## Keyboard shortcuts

| Key | Action |
|---|---|
| **Enter** | Send message |
| **Shift+Enter** | Newline |
| **↑** (cursor not at start) | Jump cursor to start of prompt |
| **↑** (cursor at start, or empty) | Walk back through prompt history |
| **↓** (cursor not at end) | Jump cursor to end of prompt |
| **↓** (cursor at end, in history) | Walk forward through history |
| **Tab / Enter** (autocomplete open) | Complete selected command |
| **Esc** (autocomplete open) | Close dropdown |

Prompt history persists across plugin reloads. Up to 100 most recent
prompts are recallable.

---

## Permission modes

When Claude wants to write a file, edit a file, or run a shell command,
the permission mode decides what happens:

| Mode | File reads | File writes/edits | Shell commands |
|---|---|---|---|
| **Prompt** (default) | Always allowed | Modal per file | Modal per command |
| **Safe** | Always allowed | Auto-accept | Modal per command |
| **YOLO** | Always allowed | Auto-accept | Auto-accept |
| **Plan** | Always allowed | Refused (model proposes only) | Refused |

In **Prompt** mode, a confirmation modal appears for each file edit and
each shell command. The modal shows:

- The file path or full command
- A diff preview (Edit) or content preview (Write)
- "Remember for this session" checkbox (file edits only — shell
  commands always re-prompt for safety)

In **Plan** mode, Claude can read your vault but won't write or run
anything. Use this when you want to discuss approaches before doing.

**Vault-only access**: even in YOLO mode, file paths are validated
against your vault root. Claude can never read or write outside the
vault, regardless of any prompt-injection attempt.

---

## Skills (custom slash commands)

A **skill** is a markdown file in `Gryphon/Skills/` that becomes a
slash command. Type `/<skill-name>` to invoke it; the file's body is
expanded and sent as a chat message.

### Format

```markdown
---
name: weekly-review
description: Summarize what I worked on this week
argument-hint: "[optional: extra focus]"
---
Read the last 7 days of journal entries in journal/ and produce a
summary covering: key decisions, files created or substantially edited,
open questions, and what I should focus on next week.

{{args}}
```

`{{args}}` is replaced with whatever you typed after the command name
when invoking the skill. Empty if you didn't pass anything.

### Required fields

- `name` — slash command (lowercase letters/digits/hyphens, must
  start with a letter)
- `description` — one-line summary shown in the autocomplete dropdown

### Reserved names

These names collide with built-in Gryphon commands and will be rejected:
`clear`, `compact`, `context`, `cost`, `effort`, `export`,
`help`, `model`, `perm`, `quote`, `settings`, `stop`, `usage`.

### Three ways to create a skill

1. **Ask Claude in chat** (easiest):
   *"Create a Gryphon skill called weekly-review that summarizes my
   journal entries from the last 7 days."*
   Claude writes the file (Write permission required); the skill
   loader picks it up immediately.

2. **Copy a bundled example**: duplicate any file in `Gryphon/Skills/`,
   rename, edit the frontmatter, save.

3. **Write from scratch**: create an `.md` file in
   `Gryphon/Skills/` with the format above.

The folder is live — Gryphon watches for changes, no plugin reload
required.

### Bundled examples

Five skills ship pre-populated:

- `/tag-suggest` — propose tags for the active note
- `/backlinks` — list backlinks with context snippets
- `/forward-links` — list outgoing wikilinks, flag broken ones
- `/summarize` — summarize the active note
- `/lint-note` — check for common issues (broken links, missing
  frontmatter, inconsistent headings)

Delete them if you don't want them; they won't be re-created. See
`Gryphon/Skills/README.md` for the full skill format reference.

---

## Settings

**Settings → Gryphon** has these sections:

### Provider

- **Provider** — Auto / CLI / SDK
- **Claude CLI path** — leave blank for auto-detect
- **Anthropic API key** — for SDK mode (paste here; env var also works
  if Obsidian was launched from a shell that has the variable set)
- **Brave Search API key** — for SDK-mode WebSearch (free tier at
  [brave.com/search/api](https://brave.com/search/api/))

### Defaults

- **Default model** — Haiku / Sonnet / Opus / Opus 1M
- **Default effort** — Low / Medium / High
- **Default permissions** — Prompt / Safe / YOLO / Plan
- **Open in main tab** — chat opens in the main area instead of sidebar

---

## Troubleshooting

### Gryphon doesn't open

Check **Settings → Community plugins** that Gryphon is enabled. If you
have multiple plugins that compose Gryphon (e.g. Athena), they may have
disabled it on their own load — that's by design (mutual exclusivity).

### "No provider available" message

Either Claude Code isn't installed AND no API key is set, OR you've
selected a provider preference that doesn't have a backing
configuration. The welcome panel inside the chat will guide you through
setup.

### "Credit balance too low" when using SDK

You need to add credits at
[console.anthropic.com → Plans & Billing](https://console.anthropic.com/settings/billing).
Note: a Pro or Max subscription is **separate billing** from API
credits. Subscriptions cover Claude.ai and Claude Code; the API has
its own credit pool.

### Welcome panel keeps appearing after I configured a key

Click the "Use Anthropic API" button in the panel (or change Provider
to Auto/SDK in settings). The panel only auto-hides when a provider
can resolve based on your current preference + configuration.

### Cost in /cost doesn't match my Anthropic invoice

Two possibilities:
1. CLI mode: cost is server-attested. For Pro/Max users, it often
   reads $0 because the prompt was subscription-covered.
2. SDK mode: cost is computed locally from a price table that may
   drift from current Anthropic pricing — that's why `/cost` shows
   `(est.)` in SDK mode. For authoritative billing, see your
   Anthropic dashboard.

### A skill file isn't appearing in autocomplete

Gryphon shows skill load errors in chat as a system message. If you
don't see one, the file's probably outside `Gryphon/Skills/` or
doesn't have the `.md` extension. Open the dev console (Cmd+Opt+I)
and look for `[gryphon] Skill` log lines for details.

### Nothing happens when I type a /command and press Enter

Likely the autocomplete dropdown is open. Press **Esc** to close it,
then **Enter** to send. Or press **Tab** to complete the highlighted
entry first, then **Enter**.

### A WebFetch always fails on certain sites

Sites with strict anti-bot WAFs (Cloudflare managed challenge, X /
Twitter, LinkedIn, Reddit) block automated requests by design. Use
`WebSearch` instead — it surfaces the content via indexed third-party
sources. Claude often suggests this fallback on its own.

---

## Where to ask for help

- **This manual** — you're reading it
- **/help in chat** — opens a quick-reference modal of commands and shortcuts
- **GitHub issues** — bugs and feature requests at
  [github.com/jivebug/gryphon-obsidian/issues](https://github.com/jivebug/gryphon-obsidian/issues)
- **GitHub discussions** — open-ended questions at
  [github.com/jivebug/gryphon-obsidian/discussions](https://github.com/jivebug/gryphon-obsidian/discussions)

Privacy reminder: when filing bugs, never paste your API key or vault
content you'd prefer to keep private. A short reproducible example is
more useful than full logs anyway.
