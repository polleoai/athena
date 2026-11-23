# tree-lock.sh: one lock on the Max's athena working tree, sourced by the
# two things that must not overlap on it (11-21):
#   scripts/sync.sh           hourly `git pull` + `npm run build:all`
#                             (rewrites vendor/gryphon and both plugins)
#   scripts/pro-release-tests.sh
#                             pin check, then `git bundle` of what it checked
# The 11-21 gate refused at 18:05:34Z because the 18:05:11Z sync build had just
# rewritten vendor/gryphon; a build starting just AFTER the check could have
# let it bundle a tree it never checked.
#
#   tree_lock_acquire <holder> [wait_s]   0 = ours; 1 = held by a live owner
#   tree_lock_release                     drops it only if this shell took it
#   tree_lock_holder                      the owner line, for messages
#
# The lock is a directory (mkdir is atomic) holding an `owner` line:
#   pid=<pid> holder=<who> since=<UTC>
# Reclaimed, with a message on stderr, when it is stale:
#   - its owner's pid is gone,
#   - it has no owner line for over 60 s (a crash between mkdir and write),
#   - or it is over TREE_LOCK_MAX_AGE_S old (default 30 min) whatever the
#     owner line says: neither holder needs more than minutes, and a reused
#     pid or an unreadable line must not make sync skip pull + build forever
#     while reporting green.
# Reclaiming happens under a second mkdir mutex, re-checked inside it, so two
# reclaimers can't both end up holding the lock. ATHENA_TREE_LOCK overrides the path
# (tests); the default is <repo>/.athena/tree.lock, git-ignored.

TREE_LOCK="${ATHENA_TREE_LOCK:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/.athena/tree.lock}"
TREE_LOCK_MINE=0
TREE_LOCK_MAX_AGE_S="${TREE_LOCK_MAX_AGE_S:-1800}"

tree_lock_holder() { cat "$TREE_LOCK/owner" 2>/dev/null || echo "no owner"; }

# Prints why the lock is stale and returns 0, or returns 1 if it isn't.
_tree_lock_stale() {
  local pid mtime age
  mtime="$(date -r "$TREE_LOCK" +%s 2>/dev/null)" || return 1   # gone meanwhile: not ours to judge
  age=$(( $(date +%s) - mtime ))
  [ "$age" -gt "$TREE_LOCK_MAX_AGE_S" ] && { echo "older than ${TREE_LOCK_MAX_AGE_S}s"; return 0; }
  pid="$(sed -n 's/^pid=\([0-9][0-9]*\) .*/\1/p' "$TREE_LOCK/owner" 2>/dev/null)"
  if [ -n "$pid" ]; then
    kill -0 "$pid" 2>/dev/null && return 1
    echo "owner pid $pid is gone"; return 0
  fi
  [ -e "$TREE_LOCK/owner" ] && return 1        # an owner line not yet readable: wait (the age cap covers a dead one)
  [ "$age" -gt 60 ] && { echo "no owner for ${age}s"; return 0; }
  return 1
}

# Remove a stale lock under the reclaim mutex, re-checking inside it.
_tree_lock_reclaim() {
  local why m
  # A reclaim mutex left by a crash (older than 60 s) must not wedge reclaims.
  m="$(date -r "$TREE_LOCK.reclaim" +%s 2>/dev/null)" && [ $(( $(date +%s) - m )) -gt 60 ] && rmdir "$TREE_LOCK.reclaim" 2>/dev/null
  mkdir "$TREE_LOCK.reclaim" 2>/dev/null || return 0           # someone else is reclaiming
  if why="$(_tree_lock_stale)"; then
    echo "tree-lock: reclaiming a stale lock, $why ($(tree_lock_holder))" >&2
    rm -rf "$TREE_LOCK"
  fi
  rmdir "$TREE_LOCK.reclaim" 2>/dev/null
  return 0
}

tree_lock_acquire() {
  local holder="$1" wait_s="${2:-0}" end
  end=$(( $(date +%s) + wait_s ))
  mkdir -p "$(dirname "$TREE_LOCK")" || return 1
  while :; do
    if mkdir "$TREE_LOCK" 2>/dev/null; then
      # $$ is the calling script's pid. Acquire in its MAIN shell, as both
      # callers do: inside a ( ) subshell $$ would still name the parent.
      if printf 'pid=%s holder=%s since=%s\n' "$$" "$holder" "$(date -u +%FT%TZ)" > "$TREE_LOCK/owner" 2>/dev/null; then
        TREE_LOCK_MINE=1
        return 0
      fi
      echo "tree-lock: could not write the owner line in $TREE_LOCK" >&2
      return 1
    fi
    if [ -d "$TREE_LOCK" ] && _tree_lock_stale >/dev/null; then
      _tree_lock_reclaim
      [ -e "$TREE_LOCK" ] || continue                             # gone: try again now
    fi                                                            # else wait, bounded, like any holder
    [ "$(date +%s)" -ge "$end" ] && return 1
    sleep 1
  done
}

tree_lock_release() {
  [ "$TREE_LOCK_MINE" = 1 ] || return 0
  rm -rf "$TREE_LOCK"
  TREE_LOCK_MINE=0
}
