---
name: forward-links
description: List outgoing wikilinks from the active note and flag broken ones
---
Read the note currently open. Extract every `[[wikilink]]` (including
aliased forms like `[[target|alias]]` and header links like
`[[target#heading]]`).

For each link target, use Glob to check whether a file named
`<target>.md` (or `<target>/index.md`) exists anywhere in the vault.

Return a Markdown table:

| Link | Target exists? | Path (if found) |
|---|---|---|

Flag broken links (no target found) prominently above the table — those
are the ones the user most likely wants to fix.

{{args}}
