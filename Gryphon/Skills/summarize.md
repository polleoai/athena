---
name: summarize
description: Summarize the active note (or an argument-provided target)
argument-hint: "[path/to/note.md or folder]"
---
Produce a summary of the target document.

Target resolution:
- If `{{args}}` is non-empty, treat it as a path — Read that file (or, if
  it's a folder, summarize across all `.md` files in it using Glob).
- Otherwise, summarize the note currently open (path is in the
  auto-context).

Return:
1. A 2 to 3 sentence abstract.
2. 3 to 5 key points as a bulleted list, each one sentence.
3. Any notable open questions or unresolved claims the note contains.

Keep it compact. Do not edit the source.
