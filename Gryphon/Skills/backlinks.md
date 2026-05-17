---
name: backlinks
description: List notes that link to the active note with context
---
Read the note currently open — its path is in the auto-context. The
note's "name" for wikilink purposes is the filename without `.md`.

Use Glob on `**/*.md` to find candidate files, then Grep for
`[[<note-name>]]` (and also `[[<note-name>|` for aliased links) across
the vault. Skip the note itself.

For each match, report:
- the source note path (as a wikilink)
- a context snippet of about 50 words around the link

If no backlinks exist, say so clearly — that often means the note is an
orphan and might benefit from being linked somewhere.

{{args}}
