"""Soft delete, full-state snapshot, and restore for kb operations.

Every destructive command snapshots ALL affected files (deleted + modified)
before making changes. kb undo restores the full prior state.

Deleted files go to .kb-trash/{timestamp}_{operation}/ and stay for 30 days.
kb purge removes batches older than 30 days.

Usage:
    from snapshot import trash_files, snapshot_files, restore_last, purge_old, list_trash
"""

import os, re, shutil, json, time, glob


TRASH_DIR = '.kb-trash'


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


def snapshot_files(kb_root, files_to_delete, files_to_modify, operation="remove", description=""):
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


def trash_files(kb_root, files, operation="remove", description=""):
    """Simple trash: move files without tracking modified files. For backward compat."""
    return snapshot_files(kb_root, files_to_delete=files, files_to_modify=[],
                         operation=operation, description=description)


def restore_last(kb_root):
    """Restore the most recent trash batch. Returns (success, message)."""
    trash_root = os.path.join(kb_root, TRASH_DIR)
    if not os.path.exists(trash_root):
        return False, "No trash found. Nothing to undo."

    # Issue #143: filter to directories whose name starts with the
    # timestamp prefix used by snapshot_files (`YYYYMMDD_HHMMSS_NNN_<op>`).
    # A stray `.kb-trash/wiki/` (or any non-batch directory created by an
    # ingest mishap) would otherwise sort lexicographically last and
    # poison every undo with "No manifest in wiki".
    _BATCH_DIR_RE = re.compile(r'^\d{8}_\d{6}_\d{3}_')
    batches = sorted(glob.glob(os.path.join(trash_root, '*')))
    batches = [b for b in batches if os.path.isdir(b)
               and _BATCH_DIR_RE.match(os.path.basename(b))]
    if not batches:
        return False, "Trash is empty. Nothing to undo."

    # Issue #143: skip-and-continue rather than fatal-fail when a batch
    # has no manifest. A manifest-less batch is unrecoverable; the user
    # should still be able to undo through earlier real batches.
    batch = None
    manifest = None
    skipped = []
    for candidate in reversed(batches):
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
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(src, dest)
            restored += 1

    # Delete files that were created by the operation
    created_deleted = 0
    for rel in manifest.get('created', []):
        if not _validate_path(kb_root, rel):
            continue
        dest = os.path.join(kb_root, rel)
        if os.path.exists(dest):
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
    return True, msg


def purge_old(kb_root, days=30):
    """Delete trash batches older than N days. Returns count purged."""
    trash_root = os.path.join(kb_root, TRASH_DIR)
    if not os.path.exists(trash_root):
        return 0

    cutoff = time.time() - (days * 86400)
    purged = 0

    for batch in sorted(glob.glob(os.path.join(trash_root, '*'))):
        if not os.path.isdir(batch):
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
    for batch in sorted(glob.glob(os.path.join(trash_root, '*'))):
        if not os.path.isdir(batch):
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
