# defaults/py_modules/savemanager/store.py
import hashlib
import json
import os
import shutil


def _hash_file(path: str) -> str:
    """Streaming SHA-256 hex digest (bounded memory for large saves)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_rel(rel_path: str) -> bool:
    """Reject absolute paths or ones that escape the destination root (path traversal)."""
    if os.path.isabs(rel_path):
        return False
    norm = os.path.normpath(rel_path)
    return norm != ".." and not norm.startswith(".." + os.sep)


def game_dir(data_root: str, app_id: int) -> str:
    return os.path.join(data_root, "games", str(app_id))


def version_dir(data_root: str, app_id: int, version_id: str) -> str:
    return os.path.join(game_dir(data_root, app_id), "versions", version_id)


def new_version_id(now_ms: int, rand_hex: str) -> str:
    return f"v_{now_ms}_{rand_hex}"


def atomic_copy(src: str, dst: str) -> None:
    """Copy src -> dst (preserving mtime) atomically + durably, creating parent dirs."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".tmp"
    shutil.copy2(src, tmp)
    with open(tmp, "rb") as f:
        os.fsync(f.fileno())
    os.replace(tmp, dst)


def create_snapshot(data_root, app_id, save_roots, entries, version_id,
                    created_at, kind, reason, parent) -> dict:
    """Full-copy every listed file from each save root into the version dir.

    save_roots: {absDir: suffix}. Files land under version_dir/root<suffix>/<path>.
    Returns the meta dict (also written as meta.json).
    """
    vdir = version_dir(data_root, app_id, version_id)
    os.makedirs(vdir, exist_ok=True)
    files = []
    total = 0
    for absdir, suffix in save_roots.items():
        for e in entries:
            if not _safe_rel(e.path):
                continue
            src = os.path.join(absdir, e.path)
            if not os.path.isfile(src):
                continue
            dst = os.path.join(vdir, f"root{suffix}", e.path)
            atomic_copy(src, dst)
            st = os.stat(dst)
            files.append({
                "suffix": suffix, "path": e.path,
                "size": st.st_size, "mtime": int(st.st_mtime * 1000),
                "sha256": _hash_file(dst),
            })
            total += st.st_size
    meta = {
        "versionId": version_id, "appId": app_id, "createdAt": created_at,
        "kind": kind, "reason": reason, "parent": parent,
        "saveRoots": {suffix: absdir for absdir, suffix in save_roots.items()},
        "files": files, "fileCount": len(files), "totalBytes": total,
        "schemaVersion": 1,
    }
    tmp = os.path.join(vdir, "meta.json.tmp")
    with open(tmp, "w") as f:
        json.dump(meta, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, os.path.join(vdir, "meta.json"))
    return meta


def read_meta(data_root, app_id, version_id) -> dict:
    with open(os.path.join(version_dir(data_root, app_id, version_id), "meta.json")) as f:
        return json.load(f)


def delete_version(data_root, app_id, version_id) -> None:
    shutil.rmtree(version_dir(data_root, app_id, version_id), ignore_errors=True)


def restore_version(data_root, app_id, version_id, suffix_to_root) -> set:
    """Copy a version's stored files back into the live save roots.

    suffix_to_root: {suffix: absDir} of the CURRENT live roots (invert save_roots).
    Returns the set of (suffix, path) actually restored.
    """
    meta = read_meta(data_root, app_id, version_id)
    vdir = version_dir(data_root, app_id, version_id)
    restored = set()
    for f in meta["files"]:
        suffix, rel = f["suffix"], f["path"]
        root = suffix_to_root.get(suffix)
        if root is None or not _safe_rel(rel):
            continue
        src = os.path.join(vdir, f"root{suffix}", rel)
        if not os.path.isfile(src):
            continue
        atomic_copy(src, os.path.join(root, rel))
        restored.add((suffix, rel))
    return restored
