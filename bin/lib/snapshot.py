"""Soft delete, full-state snapshot, and restore for kb operations.

Every destructive command snapshots ALL affected files (deleted + modified)
before making changes. kb undo restores the full prior state.

Deleted files go to .kb-trash/{timestamp}_{operation}/ and stay for 30 days.
kb purge removes batches older than 30 days.

Usage:
    from snapshot import trash_files, snapshot_files, restore_last, purge_old, list_trash
"""

import calendar, os, re, shutil, json, time, glob

import audit  # #180: every destructive path below is logged
import url_status  # a restored page's URL must not stay `removed`


TRASH_DIR = '.kb-trash'

# Two batch shapes live in .kb-trash/:
#   this module's:  YYYYMMDD_HHMMSS_mmm_<op>/ (LOCAL time) with manifest.json,
#                   deleted/<rel> and modified/<rel>;
#   the plugin's (before #180 moved it onto bin/kb):
#                   YYYYMMDDTHHMMSS_kb-<verb>/ (UTC, from toISOString) holding
#                   the vault-relative files directly, no manifest.
# "The latest" is decided by real time across both, never by name order: on
# the same day '_' sorts after 'T', so name order always picked a Python
# batch over a newer plugin bundle, and the CLI skipped plugin bundles.
_PY_BATCH_RE = re.compile(r'^(\d{8}_\d{6})_(\d{3})_')
_JS_BUNDLE_RE = re.compile(r'^(\d{8}T\d{6})_kb-([a-z][a-z-]*)$')


def _batch_epoch(name):
    """Seconds since the epoch the batch was made, or None if the name is
    neither shape."""
    m = _PY_BATCH_RE.match(name)
    if m:
        try:
            return time.mktime(time.strptime(m.group(1), '%Y%m%d_%H%M%S')) + int(m.group(2)) / 1000
        except (ValueError, OverflowError):
            return None
    m = _JS_BUNDLE_RE.match(name)
    if m:
        try:
            return calendar.timegm(time.strptime(m.group(1), '%Y%m%dT%H%M%S'))
        except (ValueError, OverflowError):
            return None
    return None


def _plugin_manifest(name, path):
    """A manifest-shaped view of a plugin bundle (it has none on disk)."""
    m = _JS_BUNDLE_RE.match(name)
    files = []
    for dirpath, _dirs, fnames in os.walk(path):
        for fn in fnames:
            files.append(os.path.relpath(os.path.join(dirpath, fn), path))
    return {
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(_batch_epoch(name))),
        'operation': m.group(2),
        'description': f"plugin kb-{m.group(2)} (no manifest)",
        'deleted': sorted(files), 'modified': [], 'created': [],
        'status': 'complete', 'plugin_bundle': True,
    }


def _ordered_batches(trash_root):
    """[(name, path)] of both shapes, oldest first by real time."""
    out = []
    for path in glob.glob(os.path.join(trash_root, '*')):
        name = os.path.basename(path)
        epoch = _batch_epoch(name)
        if os.path.isdir(path) and epoch is not None:
            out.append((epoch, name, path))
    return [(n, p) for _e, n, p in sorted(out)]


def _validate_path(kb_root, rel_path):
    """Ensure a relative path resolves within kb_root. Returns True if safe."""
    full = os.path.realpath(os.path.join(kb_root, rel_path))
    root = os.path.realpath(kb_root)
    return full.startswith(root + os.sep) or full == root


def _batch_path(kb_root, operation):
    """Create a unique batch directory in trash."""
    trash_root = os.path.join(kb_root, TRASH_DIR)
    os.makedirs(trash_root, exist_ok=True)
    # Millisecond timestamp + counter to avoid collisions
    ts = time.strftime('%Y%m%d_%H%M%S')
    ms = str(int(time.time() * 1000) % 1000).zfill(3)
    batch_name = f"{ts}_{ms}_{operation}"
    batch = os.path.join(trash_root, batch_name)
    # Handle rare collision
    counter = 0
    while os.path.exists(batch):
        counter += 1
        batch = os.path.join(trash_root, f"{batch_name}_{counter}")
    os.makedirs(batch)
    return batch


def snapshot_files(kb_root, files_to_delete, files_to_modify, operation="remove", description="",
                   reason=None):
    """Full-state snapshot: move deleted files to trash, copy modified files for undo.

    This is the primary function for destructive commands. It:
    1. Writes the manifest FIRST (so crash recovery knows what was planned)
    2. Copies modified files to the batch (for undo of reference cleanup)
    3. Moves deleted files to the batch

    Args:
        kb_root: KB root directory
        files_to_delete: list of absolute paths to move to trash
        files_to_modify: list of absolute paths that will be edited (copied for undo)
        operation: command name
        description: what happened
        reason: why it was allowed, for the audit log ("user-confirmed",
            "--yes", ...); None = the caller has no prompt to report
    Returns:
        batch_path
    """
    batch = _batch_path(kb_root, operation)

    # Validate all paths stay within KB root
    all_files = list(files_to_delete) + list(files_to_modify)
    for filepath in all_files:
        rel = os.path.relpath(filepath, kb_root)
        if not _validate_path(kb_root, rel):
            raise ValueError(f"Path escapes KB root: {rel}")

    # Step 1: Write manifest BEFORE any file operations
    manifest = {
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        'operation': operation,
        'description': description,
        'deleted': [],
        'modified': [],
        'created': [],  # files created by the operation (deleted on undo)
        'status': 'started',
    }
    manifest_path = os.path.join(batch, 'manifest.json')
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)

    # Step 2: Copy modified files (so undo can restore their pre-edit state)
    for filepath in files_to_modify:
        if not os.path.exists(filepath):
            continue
        rel = os.path.relpath(filepath, kb_root)
        dest = os.path.join(batch, 'modified', rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(filepath, dest)
        manifest['modified'].append(rel)

    # Step 3: Move deleted files to trash
    for filepath in files_to_delete:
        if not os.path.exists(filepath):
            continue
        rel = os.path.relpath(filepath, kb_root)
        dest = os.path.join(batch, 'deleted', rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(filepath, dest)
        manifest['deleted'].append(rel)

    # Step 4: Mark manifest as complete
    manifest['status'] = 'complete'
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)

    audit.record(kb_root, f"kb {operation}", description or operation, "allow", reason,
                 default_reason="no prompt (command ran as invoked)",
                 files=len(manifest['deleted']) + len(manifest['modified']),
                 batch=os.path.relpath(batch, kb_root))
    return batch


def mark_created(kb_root, batch_path, filepath):
    """Mark a file as newly created so undo can delete it."""
    manifest_path = os.path.join(batch_path, 'manifest.json')
    if not os.path.exists(manifest_path):
        return
    with open(manifest_path, 'r') as f:
        manifest = json.load(f)
    rel = os.path.relpath(filepath, kb_root)
    if rel not in manifest.get('created', []):
        manifest.setdefault('created', []).append(rel)
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)


def trash_files(kb_root, files, operation="remove", description="", reason=None):
    """Simple trash: move files without tracking modified files. For backward compat."""
    return snapshot_files(kb_root, files_to_delete=files, files_to_modify=[],
                         operation=operation, description=description, reason=reason)


def restore_last(kb_root, reason=None):
    """Restore the most recent trash batch. Returns (success, message)."""
    trash_root = os.path.join(kb_root, TRASH_DIR)
    if not os.path.exists(trash_root):
        return False, "No trash found. Nothing to undo."

    # Issue #143: filter to directories whose name starts with the
    # timestamp prefix used by snapshot_files (`YYYYMMDD_HHMMSS_NNN_<op>`).
    # A stray `.kb-trash/wiki/` (or any non-batch directory created by an
    # ingest mishap) would otherwise sort lexicographically last and
    # poison every undo with "No manifest in wiki".
    # Both shapes, ordered by real time (see _ordered_batches).
    batches = [p for _n, p in _ordered_batches(trash_root)]
    if not batches:
        return False, "Trash is empty. Nothing to undo."

    # Issue #143: skip-and-continue rather than fatal-fail when a batch
    # has no manifest. A manifest-less batch is unrecoverable; the user
    # should still be able to undo through earlier real batches.
    batch = None
    manifest = None
    skipped = []
    for candidate in reversed(batches):
        if _JS_BUNDLE_RE.match(os.path.basename(candidate)):
            return _restore_plugin_bundle(kb_root, candidate, reason)
        manifest_path = os.path.join(candidate, 'manifest.json')
        if not os.path.exists(manifest_path):
            skipped.append(os.path.basename(candidate))
            continue
        with open(manifest_path, 'r') as f:
            manifest = json.load(f)
        batch = candidate
        break

    if batch is None:
        msg = "No restorable batches found in trash."
        if skipped:
            msg += f" Skipped {len(skipped)} manifest-less directories: {', '.join(skipped[:3])}"
        return False, msg

    if manifest.get('status') != 'complete':
        return False, f"Batch {os.path.basename(batch)} has status '{manifest.get('status')}' (incomplete operation). Manual recovery needed."

    restored = 0
    conflicts = []

    # Restore deleted files
    for rel in manifest.get('deleted', manifest.get('files', [])):
        if not _validate_path(kb_root, rel):
            conflicts.append(f"{rel} (path escapes KB root — skipped)")
            continue
        # Check old format (files in batch root) vs new format (files in deleted/ subdir)
        src = os.path.join(batch, 'deleted', rel)
        if not os.path.exists(src):
            src = os.path.join(batch, rel)  # old format fallback
        dest = os.path.join(kb_root, rel)
        if os.path.exists(src):
            if os.path.exists(dest):
                conflicts.append(f"{rel} (exists — overwritten)")
                # A directory (a raw's asset dir) recreated since, e.g. by a
                # re-capture: replace it as a file is replaced. shutil.move
                # into an existing dir would nest the restored one inside.
                if os.path.isdir(src) and os.path.isdir(dest) and not os.path.islink(dest):
                    shutil.rmtree(dest)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(src, dest)
            restored += 1

    # Delete files that were created by the operation
    created_deleted = 0
    for rel in manifest.get('created', []):
        if not _validate_path(kb_root, rel):
            continue
        dest = os.path.join(kb_root, rel)
        if os.path.isdir(dest) and not os.path.islink(dest):
            shutil.rmtree(dest)  # a created asset dir (lint #39 rename)
            created_deleted += 1
        elif os.path.exists(dest):
            os.remove(dest)
            created_deleted += 1

    # Restore modified files (overwrite current versions with pre-edit copies)
    modified_restored = 0
    for rel in manifest.get('modified', []):
        if not _validate_path(kb_root, rel):
            continue
        src = os.path.join(batch, 'modified', rel)
        dest = os.path.join(kb_root, rel)
        if os.path.exists(src):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(src, dest)
            modified_restored += 1

    # Clean up batch
    shutil.rmtree(batch)

    desc = manifest.get('description', manifest.get('operation', '?'))
    ts = manifest.get('timestamp', '?')
    msg = f"Restored {restored} deleted + {modified_restored} modified files from: {desc} ({ts})"
    if conflicts:
        msg += f"\nNotes: {'; '.join(conflicts)}"
    url_status.revive_removed(kb_root, only=list(manifest.get('deleted', manifest.get('files', []))))
    audit.record(kb_root, "kb undo", f"restored {desc} ({ts})", "allow", reason,
                 files=restored + modified_restored, batch=os.path.relpath(batch, kb_root))
    return True, msg


def _restore_plugin_bundle(kb_root, bundle, reason):
    """Undo a plugin-shape bundle: every file goes back to the same
    vault-relative path, as the plugin's own undo did."""
    name = os.path.basename(bundle)
    restored, conflicts, rels = 0, [], []
    for dirpath, _dirs, fnames in os.walk(bundle):
        for fn in fnames:
            src = os.path.join(dirpath, fn)
            rel = os.path.relpath(src, bundle)
            if not _validate_path(kb_root, rel):
                conflicts.append(f"{rel} (path escapes KB root — skipped)")
                continue
            dest = os.path.join(kb_root, rel)
            if os.path.exists(dest):
                conflicts.append(f"{rel} (exists — overwritten)")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(src, dest)
            restored += 1
            rels.append(rel)
    url_status.revive_removed(kb_root, only=rels)
    if any(c.endswith("skipped") for c in conflicts):
        msg = f"Restored {restored} files from {name}; left the rest in trash"
    else:
        shutil.rmtree(bundle)
        msg = f"Restored {restored} deleted + 0 modified files from: plugin {name}"
    if conflicts:
        msg += f"\nNotes: {'; '.join(conflicts)}"
    audit.record(kb_root, "kb undo", f"restored plugin bundle {name}", "allow", reason,
                 files=restored, batch=os.path.relpath(bundle, kb_root))
    return True, msg


def purge_old(kb_root, days=30):
    """Delete trash batches older than N days. Returns count purged."""
    purged = _purge_old(kb_root, days)
    audit.record(kb_root, "kb purge", f"{purged} batch(es) older than {days} days", "allow", None,
                 files=purged)
    return purged


def _purge_old(kb_root, days):
    trash_root = os.path.join(kb_root, TRASH_DIR)
    if not os.path.exists(trash_root):
        return 0

    cutoff = time.time() - (days * 86400)
    purged = 0

    for batch in sorted(glob.glob(os.path.join(trash_root, '*'))):
        if not os.path.isdir(batch):
            continue
        if _JS_BUNDLE_RE.match(os.path.basename(batch)):  # no manifest: aged by its name
            if _batch_epoch(os.path.basename(batch)) < cutoff:
                shutil.rmtree(batch)
                purged += 1
            continue
        manifest_path = os.path.join(batch, 'manifest.json')
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, 'r') as f:
                    manifest = json.load(f)
                ts_str = manifest.get('timestamp', '')
                batch_time = time.mktime(time.strptime(ts_str, '%Y-%m-%d %H:%M:%S'))
                if batch_time < cutoff:
                    shutil.rmtree(batch)
                    purged += 1
            except (ValueError, OverflowError, json.JSONDecodeError, KeyError) as e:
                # Corrupt manifest — purge it too (can't determine age)
                shutil.rmtree(batch)
                purged += 1
    return purged


def list_trash(kb_root):
    """List all trash batches. Returns list of (batch_name, manifest)."""
    trash_root = os.path.join(kb_root, TRASH_DIR)
    if not os.path.exists(trash_root):
        return []

    results = []
    for name, batch in _ordered_batches(trash_root):
        if _JS_BUNDLE_RE.match(name):
            results.append((name, _plugin_manifest(name, batch)))
            continue
        manifest_path = os.path.join(batch, 'manifest.json')
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, 'r') as f:
                    manifest = json.load(f)
                results.append((os.path.basename(batch), manifest))
            except (json.JSONDecodeError, IOError):
                results.append((os.path.basename(batch), {'error': 'corrupt manifest'}))
    return results
