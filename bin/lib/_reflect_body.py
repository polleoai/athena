import sys, os

KB = sys.argv[1]
args = sys.argv[2:]

# ── Parse arguments ──────────────────────────────────
days = 7
deep = False
focus = None
i = 0
while i < len(args):
    if args[i] in ('--help', '-h'):
        print("""Usage: kb reflect [--focus "topic"] [--days N] [--deep]

Gather journal entries, session logs, and related pages for pattern analysis.

Options:
  --focus "text"  Guide analysis toward a specific topic or question
  --days N        Look back N days (default: 7)
  --deep          Also search the index for related pages (slower)
  -h, --help      Show this help

The --focus flag directs the AI to analyze through a specific lens.
It also auto-searches the KB for pages related to the focus topic.

In an AI session, the AI analyzes the gathered data and proposes insights.
From the terminal, this outputs the raw report for review.

Examples:
  kb reflect                                                # Undirected, last 7 days
  kb reflect --focus "how do ML courses explain gradient descent differently"
  kb reflect --focus "security gaps" --days 14              # Focused + 14 days
  kb reflect --deep                                         # Include search discovery
""")
        sys.exit(0)
    elif args[i] == '--days' and i + 1 < len(args):
        days = int(args[i + 1]); i += 2
    elif args[i] == '--focus' and i + 1 < len(args):
        focus = args[i + 1]; i += 2
    elif args[i] == '--deep':
        deep = True; i += 1
    elif not args[i].startswith('--') and focus is None:
        # Bare argument treated as focus
        focus = args[i]; i += 1
    else:
        i += 1

# Add lib dir to path
lib_dir = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), 'lib') if '__file__' not in dir() else ''
if not lib_dir:
    lib_dir = os.path.join(KB, 'bin', 'lib')
if lib_dir not in sys.path:
    sys.path.insert(0, lib_dir)

from reflect import build_reflect_report, format_report_text

report = build_reflect_report(KB, days=days, deep=deep, focus=focus)
print(format_report_text(report))