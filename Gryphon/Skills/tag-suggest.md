---
name: tag-suggest
description: Propose Obsidian tags for the active note
argument-hint: "[style: casual|academic|...]"
---
Read the note currently open in Obsidian — the auto-injected
`[gryphon-context]` block at the start of the conversation gives you
the path.

Propose 3 to 5 tags appropriate for this note following Obsidian
conventions: lowercase, hyphen-separated, no spaces, no leading `#`
(the user adds that themselves). Prefer general-enough tags that
they'll apply to other notes too — a tag that fits only this one note
is not useful.

For each tag, give a one-line rationale explaining why it fits.

Return a bulleted list. Do NOT edit the note — just propose.

Style preference (if provided): {{args}}
