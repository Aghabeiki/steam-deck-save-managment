# defaults/py_modules/savemanager/refs.py
import json
import os
import shutil

from .store import game_dir


def refs_path(data_root, app_id) -> str:
    return os.path.join(game_dir(data_root, app_id), "refs.json")


def _valid_refs(d) -> bool:
    """A refs dict callers can safely use: head has a versionId, versions is a list."""
    return (isinstance(d, dict)
            and isinstance(d.get("head"), dict) and "versionId" in d["head"]
            and isinstance(d.get("versions"), list))


def read_refs(data_root, app_id) -> dict:
    default = {
        "appId": app_id,
        "head": {"versionId": None, "detached": False},
        "pendingRevertTo": None,
        "versions": [],
        "updatedAt": 0,
        "schemaVersion": 1,
    }
    path = refs_path(data_root, app_id)
    for candidate in (path, path + ".bak"):
        try:
            with open(candidate) as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if _valid_refs(data):
            for k, v in default.items():
                data.setdefault(k, v)            # backfill any missing optional keys
            return data
        # parsed but malformed (partial write / manual edit): try .bak, then default
    return default


def write_refs(data_root, app_id, refs: dict) -> None:
    path = refs_path(data_root, app_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(refs, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    if os.path.isfile(path):
        shutil.copy2(path, path + ".bak")
    os.replace(tmp, path)


def make_version_entry(meta: dict) -> dict:
    return {
        "versionId": meta["versionId"],
        "createdAt": meta["createdAt"],
        "kind": meta["kind"],
        "reason": meta["reason"],
        "parent": meta["parent"],
        "pinned": False,
        "name": None,
        "fileCount": meta["fileCount"],
        "totalBytes": meta["totalBytes"],
    }
