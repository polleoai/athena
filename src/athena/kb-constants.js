/**
 * Athena-specific MCP tool status messages.
 *
 * Merged into the GryphonChatView's toolStatusMap via options.extraToolStatus
 * during view construction. Gryphon alone never sees these.
 */

const TOOL_STATUS_KB = {
  "mcp__athena__kb_add": "Capturing URL...",
  "mcp__athena__kb_add_content": "Creating page...",
  "mcp__athena__kb_search": "Searching knowledge base...",
  "mcp__athena__kb_query": "Answering question...",
  "mcp__athena__kb_lint": "Running health check...",
  "mcp__athena__kb_list": "Listing pages...",
  "mcp__athena__kb_stats": "Gathering stats...",
  "mcp__athena__kb_index": "Building search index...",
  "mcp__athena__kb_reflect": "Analyzing journal...",
  "mcp__athena__kb_journal": "Writing journal entry...",
  "mcp__athena__kb_insight": "Saving insight...",
  "mcp__athena__kb_rules": "Checking rules...",
  "mcp__athena__kb_create": "Creating page...",
  "mcp__athena__kb_rename": "Renaming page...",
  "mcp__athena__kb_remove": "Removing page...",
  "mcp__athena__kb_move": "Moving pages...",
  "mcp__athena__kb_merge": "Merging pages...",
  "mcp__athena__kb_export": "Generating export...",
  "mcp__athena__kb_ungroup": "Dissolving hub...",
  "mcp__athena__kb_undo": "Restoring from trash...",
  "mcp__athena__kb_purge": "Cleaning up trash...",
  "mcp__athena__kb_trash": "Checking trash...",
};

module.exports = { TOOL_STATUS_KB };
