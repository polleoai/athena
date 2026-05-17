# Gryphon Skills

Skills are user-authored slash commands that expand a prompt template and
send it as a chat message. Type `/<skill-name>` in the Gryphon chat and
Gryphon expands the skill's body (substituting any arguments you typed)
and sends the result to Claude.

## File format

Each skill is a single `.md` file in this folder:

```markdown
---
name: my-skill
description: One-line summary shown in the autocomplete dropdown
argument-hint: "[optional args]"
---
The body of your skill goes here. This is a prompt template — whatever
you write becomes a user message to Claude when the skill fires.

Use {{args}} anywhere to substitute whatever the user typed after the
command name. If no args were given, {{args}} becomes empty string.
```

**Fields:**

- `name` (required) — becomes the slash command (`name: tag-suggest` →
  `/tag-suggest`). Lowercase + hyphens. Must not collide with a Gryphon
  built-in (`clear`, `compact`, `context`, `cost`, `effort`, `export`,
  `memory`, `model`, `perm`, `quote`, `settings`, `stop`, `usage`).
- `description` (required) — shown in the autocomplete dropdown. Keep
  under 60 characters.
- `argument-hint` (optional) — displayed after the command name in the
  dropdown, e.g. `[optional focus]`. Purely informational.

**Body:** plain Markdown. `{{args}}` is the only template feature.

## Creating a new skill

Three ways:

1. **Ask Claude in Gryphon chat**: *"Read Gryphon/Skills/README.md, then
   create a skill at Gryphon/Skills/weekly-review.md that summarizes the
   last week of my journal entries."* Claude writes the file; Gryphon
   picks it up on next autocomplete.
2. **Copy + edit**: duplicate any bundled skill, rename, edit the
   frontmatter and body, save.
3. **From scratch**: create an `.md` file with the frontmatter block
   above and your template body.

The folder is live — Gryphon watches for changes and updates the command
list without reload.

## What skills CAN do

Anything a normal chat turn can do. The skill body is sent as a user
message, so Claude has the full toolbox (Read, Write, Edit, Glob, Grep,
WebFetch, WebSearch, Bash, plus any MCP tools) plus the auto-injected
active-file context.

## What skills CAN'T do (v1)

- Override the current model, effort, or permission mode
- Run without creating a visible chat turn
- Restrict Claude's tool access (skills inherit session-wide access)
- Use template features beyond `{{args}}`

These are in `docs/gryphon-skills-design.md` §13 if you want the rationale.

## Bundled skills

Gryphon ships five starter skills that live next to this README. You can
edit them freely — customizations persist across upgrades. If you delete
one, it'll be recreated on the next plugin reload. To permanently replace
a bundled skill, keep the file but change its contents.
