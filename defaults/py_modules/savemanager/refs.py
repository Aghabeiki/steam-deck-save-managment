# defaults/py_modules/savemanager/refs.py
import json
import os
import shutil

from .store import game_dir


def refs_path(data_root, app_id) -> str:
    return os.path.join(game_dir(data_root, app_id), "refs.json")


def read_refs(data_root, app_id) -> dict:
    path = refs_path(data_root, app_id)
    for candidate in (path, path + ".bak"):
        try:
            with open(candidate) as f:
                return json.load(f)
        except (OSError, ValueError):
            continue
    return {
        "appId": app_id,
        "head": {"versionId": None, "detached": False},
        "pendingRevertTo": None,
        "versions": [],
        "updatedAt": 0,
        "schemaVersion": 1,
    }


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
