# defaults/py_modules/savemanager/curation.py
from .refs import read_refs, write_refs
from .store import delete_version


def _find(refs, version_id):
    for v in refs["versions"]:
        if v["versionId"] == version_id:
            return v
    return None


def set_pinned(data_root, app_id, version_id, pinned) -> bool:
    refs = read_refs(data_root, app_id)
    v = _find(refs, version_id)
    if v is None:
        return False
    v["pinned"] = bool(pinned)
    write_refs(data_root, app_id, refs)
    return True


def set_name(data_root, app_id, version_id, name) -> bool:
    refs = read_refs(data_root, app_id)
    v = _find(refs, version_id)
    if v is None:
        return False
    v["name"] = name
    write_refs(data_root, app_id, refs)
    return True


def remove_version(data_root, app_id, version_id) -> bool:
    """Delete a non-HEAD, non-pinned version (entry + on-disk dir).
    Refuses to delete HEAD or a pinned version — the caller must unpin it first."""
    refs = read_refs(data_root, app_id)
    if refs["head"]["versionId"] == version_id:
        return False
    v = _find(refs, version_id)
    if v is None or v["pinned"]:
        return False
    refs["versions"] = [v for v in refs["versions"] if v["versionId"] != version_id]
    write_refs(data_root, app_id, refs)
    delete_version(data_root, app_id, version_id)
    return True
