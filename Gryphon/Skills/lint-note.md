---
name: lint-note
description: Check the active note for common usability issues
---
Read the note currently open. Check for these issues and report what you
find. Do NOT auto-fix — just list findings with file:line locations
where applicable.

1. **Broken wikilinks** — `[[target]]` where no matching file exists
   anywhere in the vault (use Glob to verify).
2. **Missing frontmatter** — if other notes in the same folder have
   YAML frontmatter and this one doesn't, flag it.
3. **Heading-level skips** — e.g. an H1 followed directly by an H3
   (skipping H2). Each skip is one finding.
4. **Unclosed code fences** — ``` that opens but is never closed.
5. **TODO / FIXME markers** — anything matching `TODO:`, `FIXME:`, or
   `XXX:`. List each with its line and surrounding context.

Format the output as a Markdown checklist grouped by category. If
everything looks clean, say so explicitly — don't invent issues.

{{args}}
