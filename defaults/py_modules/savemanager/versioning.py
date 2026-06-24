# defaults/py_modules/savemanager/versioning.py
import json
import os

from .discovery import parse_installdir, read_entries, resolve_save_roots
from .refs import make_version_entry, read_refs, write_refs
from .store import create_snapshot, new_version_id, read_meta, _safe_rel, delete_version, restore_version, _hash_file, version_dir


def is_supported(steam_root, account_id, app_id, installdir) -> bool:
    entries = read_entries(steam_root, account_id, app_id)
    if not entries:
        return False
    return bool(resolve_save_roots(steam_root, account_id, app_id, entries, installdir))


def _live_matches_head(data_root, app_id, refs, save_roots, entries) -> bool:
    """True iff the live save dirs exactly equal the current HEAD version.
    A file is unchanged iff size AND mtime AND (when recorded) sha256 all match —
    the hash closes the same-size/same-mtime-tick false-skip flagged by the M2 review."""
    head_id = refs["head"]["versionId"]
    if not head_id:
        return False
    try:
        meta = read_meta(data_root, app_id, head_id)
    except (OSError, ValueError):
        return False
    meta_by_key = {(f["suffix"], f["path"]): f for f in meta["files"]}
    suffix_to_root = {suffix: absdir for absdir, suffix in save_roots.items()}

    live_keys = set()
    for absdir, suffix in save_roots.items():
        for e in entries:
            if _safe_rel(e.path) and os.path.isfile(os.path.join(absdir, e.path)):
                live_keys.add((suffix, e.path))
    if live_keys != set(meta_by_key.keys()):
        return False                                  # a managed file was added or removed

    for (suffix, rel), mf in meta_by_key.items():
        root = suffix_to_root.get(suffix)
        if root is None:
            return False
        p = os.path.join(root, rel)
        try:
            st = os.stat(p)
            if st.st_size != mf["size"] or int(st.st_mtime * 1000) != mf["mtime"]:
                return False
            if mf.get("sha256") is not None and _hash_file(p) != mf["sha256"]:
                return False                          # same size+mtime but different content
        except OSError:
            return False                              # file vanished mid-check -> treat as changed
    return True


def do_backup(data_root, steam_root, account_id, game_info, now_ms, rand_hex,
              ignore_unchanged=True, kind="manual", reason="manual", keep_count=20):
    """Snapshot the current save. Returns the new refs version entry, or None."""
    app_id = game_info["appId"]
    installdir = parse_installdir(steam_root, app_id)
    entries = read_entries(steam_root, account_id, app_id)
    if not entries:
        return None
    save_roots = resolve_save_roots(steam_root, account_id, app_id, entries, installdir)
    if not save_roots:
        return None

    refs = read_refs(data_root, app_id)
    if ignore_unchanged and _live_matches_head(data_root, app_id, refs, save_roots, entries):
        return None

    version_id = new_version_id(now_ms, rand_hex)
    parent = refs["head"]["versionId"]
    meta = create_snapshot(data_root, app_id, save_roots, entries, version_id,
                           now_ms, kind=kind, reason=reason, parent=parent)
    entry = make_version_entry(meta)
    refs["versions"].insert(0, entry)
    refs["head"] = {"versionId": version_id, "detached": False}
    refs["updatedAt"] = now_ms
    write_refs(data_root, app_id, refs)
    cull_versions(data_root, app_id, keep_count)
    return entry


def list_versions(data_root, app_id) -> dict:
    refs = read_refs(data_root, app_id)
    return {"head": refs["head"], "versions": refs["versions"]}


def cull_versions(data_root, app_id, keep_count) -> list:
    """Delete oldest non-pinned, non-HEAD versions until total <= keep_count.
    Pinned and HEAD versions are protected (but still count toward the cap).
    Returns the list of deleted versionIds. refs.json is written before dirs are
    removed (refs is the commit point; orphan dirs are harmless)."""
    keep_count = max(1, int(keep_count))
    refs = read_refs(data_root, app_id)
    versions = refs["versions"]
    head_id = refs["head"]["versionId"]
    surplus = len(versions) - keep_count
    if surplus <= 0:
        return []
    # newest-first list -> oldest are at the end; reversed() gives oldest-first.
    deletable = [v for v in reversed(versions)
                 if not v["pinned"] and v["versionId"] != head_id]
    to_delete = {v["versionId"] for v in deletable[:surplus]}
    if not to_delete:
        return []
    refs["versions"] = [v for v in versions if v["versionId"] not in to_delete]
    write_refs(data_root, app_id, refs)
    for vid in to_delete:
        delete_version(data_root, app_id, vid)
    return list(to_delete)


def _materialize(data_root, app_id, target_id, save_roots, entries, prev_head_id) -> None:
    """Make the live save dirs exactly equal target_id: restore target's files, then
    delete managed live files that target does not contain."""
    suffix_to_root = {suffix: absdir for absdir, suffix in save_roots.items()}
    restore_version(data_root, app_id, target_id, suffix_to_root)

    target_meta = read_meta(data_root, app_id, target_id)
    target_set = {(f["suffix"], f["path"]) for f in target_meta["files"]}

    # "managed" live files = every rcf-listed file (per suffix) + the previous HEAD's files.
    managed = set()
    for suffix in save_roots.values():
        for e in entries:
            if _safe_rel(e.path):
                managed.add((suffix, e.path))
    if prev_head_id:
        try:
            for f in read_meta(data_root, app_id, prev_head_id)["files"]:
                managed.add((f["suffix"], f["path"]))
        except (OSError, ValueError):
            pass

    for suffix, rel in managed - target_set:
        root = suffix_to_root.get(suffix)
        if root and _safe_rel(rel):
            p = os.path.join(root, rel)
            if os.path.isfile(p):
                os.remove(p)


def _apply_pending(data_root, steam_root, account_id, app_id):
    """Finish (or resume) a revert: materialize refs.pendingRevertTo, move HEAD, clear it.
    Idempotent — safe to re-run after a crash. Returns the new head dict or None."""
    refs = read_refs(data_root, app_id)
    target_id = refs.get("pendingRevertTo")
    if not target_id:
        return None
    installdir = parse_installdir(steam_root, app_id)
    entries = read_entries(steam_root, account_id, app_id)
    save_roots = resolve_save_roots(steam_root, account_id, app_id, entries, installdir)
    if not save_roots:
        return None
    _materialize(data_root, app_id, target_id, save_roots, entries, refs["head"]["versionId"])
    refs = read_refs(data_root, app_id)
    newest_id = refs["versions"][0]["versionId"] if refs["versions"] else None
    refs["head"] = {"versionId": target_id, "detached": target_id != newest_id}
    refs["pendingRevertTo"] = None
    write_refs(data_root, app_id, refs)
    return refs["head"]


def revert_to(data_root, steam_root, account_id, app_id, target_id, now_ms, rand_hex):
    """Git-like revert to target_id. Auto-snapshots unsaved live changes first, then
    moves HEAD and materializes the target into the live save dirs. Crash-safe via
    refs.pendingRevertTo. Returns the new head dict, or None if target/roots not found.
    NOTE: the caller MUST ensure the game is not running (frontend guards on Router.RunningApps)."""
    installdir = parse_installdir(steam_root, app_id)
    entries = read_entries(steam_root, account_id, app_id)
    save_roots = resolve_save_roots(steam_root, account_id, app_id, entries, installdir)
    if not save_roots:
        return None
    refs = read_refs(data_root, app_id)
    if not any(v["versionId"] == target_id for v in refs["versions"]):
        return None

    # Preserve unsaved live progress as a real, listed version before we overwrite it.
    if not _live_matches_head(data_root, app_id, refs, save_roots, entries):
        snap_id = new_version_id(now_ms, rand_hex)
        meta = create_snapshot(data_root, app_id, save_roots, entries, snap_id, now_ms,
                               kind="auto", reason="pre-revert-autosnapshot",
                               parent=refs["head"]["versionId"])
        refs["versions"].insert(0, make_version_entry(meta))
        refs["head"] = {"versionId": snap_id, "detached": False}

    refs["pendingRevertTo"] = target_id
    refs["updatedAt"] = now_ms
    write_refs(data_root, app_id, refs)
    return _apply_pending(data_root, steam_root, account_id, app_id)


def resume_pending_revert(data_root, steam_root, account_id, app_id):
    """If a revert was interrupted (refs.pendingRevertTo set), finish it. No-op otherwise."""
    return _apply_pending(data_root, steam_root, account_id, app_id)


def load_version_files(data_root, app_id, version_id) -> dict:
    """Read a stored version's files as {suffix-qualified relpath: bytes}. The key is
    'root<suffix>/<path>' so files from different save roots never collide."""
    meta = read_meta(data_root, app_id, version_id)
    vdir = version_dir(data_root, app_id, version_id)
    out = {}
    for f in meta["files"]:
        key = f"root{f['suffix']}/{f['path']}"
        with open(os.path.join(vdir, f"root{f['suffix']}", f["path"]), "rb") as fh:
            out[key] = fh.read()
    return out


def kept_versions_for(data_root, app_id) -> list:
    """Build the ordered kept-version list for Drive mirroring from refs.json.
    label = the user name if set, else the versionId (Drive folder name)."""
    refs = read_refs(data_root, app_id)
    return [{"versionId": v["versionId"], "label": v["name"] or v["versionId"],
             "pinned": v["pinned"]} for v in refs["versions"]]


def import_version(data_root, app_id, version_id, label, pinned, files) -> dict:
    """Reconstruct a LOCAL version from Drive-downloaded {suffix-qualified relpath: bytes}.
    Writes the files + meta.json and adds a refs entry. Does NOT change HEAD (the user reverts
    to it to materialize it into the live save). Idempotent: if the version already exists it is
    returned unchanged. Returns the entry."""
    refs = read_refs(data_root, app_id)
    existing = next((v for v in refs["versions"] if v["versionId"] == version_id), None)
    if existing is not None:
        return existing                          # already present -> no-op (don't rewrite a native version)

    vdir = version_dir(data_root, app_id, version_id)
    os.makedirs(vdir, exist_ok=True)
    meta_files = []
    total = 0
    for relpath, content in files.items():
        if not _safe_rel(relpath):
            continue                              # reject path traversal from a corrupt/hostile index
        parts = relpath.split("/", 1)
        if len(parts) < 2:
            continue                              # not 'root<suffix>/<path>'
        seg0, path = parts
        if not (seg0 == "root" or (seg0.startswith("root_") and seg0[5:].isdigit())):
            continue                              # only well-formed root<suffix> segments
        suffix = seg0[len("root"):]
        dst = os.path.join(vdir, seg0, path)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as fh:
            fh.write(content)
        st = os.stat(dst)
        meta_files.append({"suffix": suffix, "path": path, "size": st.st_size,
                           "mtime": int(st.st_mtime * 1000), "sha256": _hash_file(dst)})
        total += st.st_size

    bits = version_id.split("_")
    created_at = int(bits[1]) if len(bits) >= 3 and bits[1].isdigit() else 0
    meta = {"versionId": version_id, "appId": app_id, "createdAt": created_at,
            "kind": "import", "reason": "drive-restore", "parent": None,
            "saveRoots": {}, "files": meta_files, "fileCount": len(meta_files),
            "totalBytes": total, "schemaVersion": 1}
    tmp = os.path.join(vdir, "meta.json.tmp")
    with open(tmp, "w") as f:
        json.dump(meta, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, os.path.join(vdir, "meta.json"))

    entry = {"versionId": version_id, "createdAt": created_at, "kind": "import",
             "reason": "drive-restore", "parent": None, "pinned": bool(pinned),
             "name": (label if label != version_id else None),
             "fileCount": len(meta_files), "totalBytes": total}
    refs["versions"].append(entry)
    refs["versions"].sort(key=lambda v: v["createdAt"], reverse=True)    # keep newest-first
    write_refs(data_root, app_id, refs)
    return entry
