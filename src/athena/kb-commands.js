/**
 * KB command detection and output metadata for Athena.
 *
 * Used by AthenaPlugin via the GryphonChatView onBeforeSend hook.
 *
 * Exports:
 *   KB_COMMANDS           — autocomplete list (cmd + description)
 *   DONE_STATUS_MAP       — status bar text to show after a mechanical run
 *   STATUS_MAP            — status bar text to show while running a command
 *   detectMechanicalCommand(text) — returns { command, args } or null
 */

const KB_COMMANDS = [
  { cmd: "kb add",                     desc: "Process all pending (inbox + clippings)" },
  { cmd: "kb add <url>",               desc: "Capture a single URL" },
  { cmd: "kb add <url1> <url2> ...",   desc: "Capture multiple URLs" },
  { cmd: "kb search <query>",          desc: "Search the knowledge base" },
  { cmd: "kb query <question>",        desc: "Answer a question with sources" },
  { cmd: "kb stats",                   desc: "Show KB counts" },
  { cmd: "kb lint",                    desc: "Health check + auto-fix" },
  { cmd: "kb index",                   desc: "Rebuild search index" },
  { cmd: "kb list",                    desc: "List all wiki pages" },
  { cmd: "kb list --insights",         desc: "List insight pages" },
  { cmd: "kb list --topics",           desc: "List topic pages" },
  { cmd: "kb list --entities",         desc: "List entity pages" },
  { cmd: "kb list --repos",            desc: "List repo pages" },
  { cmd: "kb list --papers",           desc: "List paper pages" },
  { cmd: "kb list --videos",           desc: "List video pages" },
  { cmd: "kb list --webpages",         desc: "List webpage pages" },
  { cmd: "kb list --images",           desc: "List image pages" },
  { cmd: "kb list --comparisons",      desc: "List comparison pages" },
  { cmd: "kb list --projects",         desc: "List project pages" },
  { cmd: "kb list --urls",              desc: "All tracked URLs and their status" },
  { cmd: "kb list --recent",           desc: "Last 20 additions" },
  { cmd: "kb list --journal",          desc: "List journal entries" },
  { cmd: "kb journal \"text\"",        desc: "Write a journal entry" },
  { cmd: "kb insight \"Title\"",       desc: "Save a polished finding" },
  { cmd: "kb reflect",                 desc: "AI proposes insights from journal" },
  { cmd: "kb rules",                   desc: "Show current processing rules" },
  { cmd: "kb rules add \"rule\"",      desc: "Add a new processing rule" },
  { cmd: "kb rename <page> --to \"N\"",desc: "Rename page + update links" },
  { cmd: "kb remove <page>",           desc: "Soft-delete to trash" },
  { cmd: "kb create <name>",           desc: "Create a hub/group page" },
  { cmd: "kb move <p> --into \"Hub\"", desc: "Move pages into a hub" },
  { cmd: "kb merge <p1> <p2>",         desc: "Merge pages into one" },
  { cmd: "kb ungroup <hub>",           desc: "Dissolve a hub" },
  { cmd: "kb undo",                    desc: "Restore last trashed files" },
  { cmd: "kb trash",                   desc: "List items in trash" },
  { cmd: "kb purge",                   desc: "Permanently delete old trash" },
];

// Shown while a mechanical command is executing.
const STATUS_MAP = {
  stats: "Gathering stats...",
  lint: "Running health check...",
  index: "Building search index...",
  trash: "Checking trash...",
  undo: "Restoring from trash...",
  purge: "Cleaning up trash...",
  list: "Listing pages...",
  search: "Searching...",
  journal: "Writing journal entry...",
  insight: "Listing insights...",
  rules: "Loading rules...",
};

// Shown after the command completes successfully. Each entry names what
// just finished — "Ready" was vague and duplicated the idle status.
const DONE_STATUS_MAP = {
  stats: "Stats loaded",
  lint: "Health check complete",
  index: "Index rebuilt",
  trash: "Trash listed",
  undo: "Restore complete",
  purge: "Purge complete",
  list: "Listing complete",
  search: "Search complete",
  journal: "Journal entry saved",
  insight: "Insights listed",
  rules: "Rules loaded",
};

function detectMechanicalCommand(text) {
  const t = (text || "").trim();
  const patterns = [
    { pattern: /^kb\s+stats$/i, command: "stats", args: [] },
    { pattern: /^kb\s+lint$/i, command: "lint", args: [] },
    { pattern: /^kb\s+index$/i, command: "index", args: [] },
    { pattern: /^kb\s+trash$/i, command: "trash", args: [] },
    { pattern: /^kb\s+undo$/i, command: "undo", args: [] },
    { pattern: /^kb\s+purge$/i, command: "purge", args: [] },
    { pattern: /^kb\s+list$/i, command: "list", args: [] },
    { pattern: /^kb\s+list\s+(--\w+)$/i, command: "list", argsMatch: 1 },
    { pattern: /^kb\s+search\s+(.+)$/i, command: "search", argsMatch: 1 },
    { pattern: /^kb\s+journal\s+"(.+)"$/i, command: "journal", argsMatch: 1 },
    { pattern: /^kb\s+journal\s+--recent$/i, command: "journal", args: ["--recent"] },
    { pattern: /^kb\s+insight\s+--list$/i, command: "insight", args: ["--list"] },
    { pattern: /^kb\s+rules$/i, command: "rules", args: [] },
    { pattern: /^kb\s+add$/i, command: "add", args: [] },
    { pattern: /^kb\s+add\s+(https?:\/\/\S+)$/i, command: "add", argsMatch: 1 },
    // `kb refresh <url>` — regenerate the wiki page for an existing
    // captured URL. Bypasses dedup so a stale wiki page (e.g. one
    // created before a YAML/wikilink fix landed) can be rewritten with
    // the current code's output. Doesn't re-capture the raw — only
    // regenerates the wiki layer from the existing raw on disk.
    { pattern: /^kb\s+refresh\s+(https?:\/\/\S+)$/i, command: "refresh", argsMatch: 1 },
    // 1.0.17 consolidation: every kb verb from CLAUDE.md gets a
    // pattern so the LLM never sees the raw text (was the source of
    // the multi-turn exploration the user observed for `kb remove`).
    // Verbs with full JS handlers (remove, undo, rename) run inline;
    // verbs without yet route to runMechanical / bin/kb which works
    // on macOS/Linux and surfaces a clear "bash backend not on
    // Windows" message there.
    { pattern: /^kb\s+remove\s+(.+?)\s+--with-raw$/i, command: "remove", argsMatchAll: [1, "--with-raw"] },
    { pattern: /^kb\s+remove\s+(.+)$/i, command: "remove", argsMatch: 1 },
    { pattern: /^kb\s+rename\s+(.+?)\s+--to\s+"(.+)"$/i, command: "rename", argsMatchAll: [1, "--to", 2] },
    { pattern: /^kb\s+rename\s+(.+?)\s+--to\s+(.+)$/i, command: "rename", argsMatchAll: [1, "--to", 2] },
    { pattern: /^kb\s+merge\s+(.+?)\s+(.+?)\s+--into\s+"(.+)"$/i, command: "merge", argsMatchAll: [1, 2, "--into", 3] },
    { pattern: /^kb\s+merge\s+(.+?)\s+(.+)$/i, command: "merge", argsMatchAll: [1, 2] },
    { pattern: /^kb\s+move\s+(.+?)\s+--into\s+"(.+)"$/i, command: "move", argsMatchAll: [1, "--into", 2] },
    { pattern: /^kb\s+ungroup\s+(.+)$/i, command: "ungroup", argsMatch: 1 },
    { pattern: /^kb\s+create\s+(.+?)\s+(--\w+)$/i, command: "create", argsMatchAll: [1, 2] },
    { pattern: /^kb\s+create\s+(.+)$/i, command: "create", argsMatch: 1 },
    { pattern: /^kb\s+insight\s+"(.+)"$/i, command: "insight", argsMatch: 1 },
    { pattern: /^kb\s+reflect$/i, command: "reflect", args: [] },
    { pattern: /^kb\s+query\s+(.+)$/i, command: "query", argsMatch: 1 },
    { pattern: /^kb\s+refresh-wiki\s+(.+)$/i, command: "refresh-wiki", argsMatch: 1 },
    { pattern: /^kb\s+config$/i, command: "config", args: [] },
    { pattern: /^kb\s+rules\s+add\s+"(.+)"$/i, command: "rules", argsMatchAll: ["add", 1] },
    { pattern: /^kb\s+report-bug\s+"(.+)"$/i, command: "report-bug", argsMatch: 1 },
    { pattern: /^kb\s+request-feature\s+"(.+)"$/i, command: "request-feature", argsMatch: 1 },
  ];
  for (const p of patterns) {
    const match = t.match(p.pattern);
    if (!match) continue;
    if (p.args) return { command: p.command, args: p.args };
    if (p.argsMatch !== undefined) return { command: p.command, args: [match[p.argsMatch]] };
    if (p.argsMatchAll) {
      // argsMatchAll: array of either capture-group indices (number) or
      // literal strings. Resolved in order — e.g. [1, "--to", 2] →
      // [match[1], "--to", match[2]]. Used for multi-arg verbs that
      // mix captured page names with literal flag markers.
      return {
        command: p.command,
        args: p.argsMatchAll.map(x => typeof x === "number" ? match[x] : x),
      };
    }
    return { command: p.command, args: [] };
  }
  return null;
}

module.exports = {
  KB_COMMANDS,
  STATUS_MAP,
  DONE_STATUS_MAP,
  detectMechanicalCommand,
};
