# defaults/py_modules/savemanager/mirror.py
import json

_INDEX_NAME = "index.json"


def empty_index(app_id) -> dict:
    return {"appId": app_id, "gameFolderId": None, "versions": {}, "schemaVersion": 1}


def plan_sync(local_kept_vids, remote_index) -> dict:
    """Compare the local kept version-id list to what the remote index already has.
    Returns the version ids to upload (kept but not remote) and to prune (remote but
    no longer kept). Order of to_upload follows local_kept_vids."""
    remote_versions = remote_index.get("versions", {})
    remote_vids = set(remote_versions.keys())
    kept = list(local_kept_vids)
    kept_set = set(kept)
    to_upload = [v for v in kept if v not in remote_vids]
    to_prune = [v for v in remote_versions if v not in kept_set]
    return {"to_upload": to_upload, "to_prune": to_prune}


def sync_versions(client, root_folder_id, game_name, kept_versions, remote_index,
                  load_version_files, persist_index) -> dict:
    """Make Drive mirror the local kept set, honoring the spec's commit ordering:
    upload new versions -> remove pruned versions from the index -> PERSIST the index
    (commit point) -> only then delete the pruned Drive folders. A crash after the
    persist leaves at most orphan (unreferenced) folders, never a dangling reference.

    kept_versions: ordered list of {"versionId", "label", "pinned"?}.
    load_version_files(version_id) -> {relpath: bytes}.  relpath MUST be suffix-qualified
        by the caller (e.g. "root_1/<path>") so multi-root files never collide.
    persist_index(index_dict) -> None: writes index.json to Drive (the commit point).
    """
    index = dict(remote_index)
    index.setdefault("versions", {})
    index["versions"] = dict(index["versions"])
    plan = plan_sync([v["versionId"] for v in kept_versions], index)

    game_folder = index.get("gameFolderId") or client.find_or_create_folder(game_name, root_folder_id)
    index["gameFolderId"] = game_folder

    meta_by_vid = {v["versionId"]: v for v in kept_versions}
    for vid in plan["to_upload"]:
        v = meta_by_vid[vid]
        vfolder = client.create_folder(v["label"], game_folder)
        file_ids = {}
        for relpath, content in load_version_files(vid).items():
            file_ids[relpath] = client.upload_file(relpath.replace("/", "_"), vfolder, content)
        index["versions"][vid] = {"label": v["label"], "folderId": vfolder,
                                  "fileIds": file_ids, "pinned": bool(v.get("pinned", False))}

    # Remove pruned versions from the index BEFORE persisting, remembering their folders.
    prune_folder_ids = []
    for vid in plan["to_prune"]:
        entry = index["versions"].pop(vid, None)
        if entry and entry.get("folderId"):
            prune_folder_ids.append(entry["folderId"])

    persist_index(index)                       # COMMIT POINT: index reflects the kept set

    for folder_id in prune_folder_ids:         # prune AFTER the index is durable (trash, not permanent)
        client.trash_file(folder_id)

    return index


def read_index(client, game_folder_id):
    """Return (index_dict, index_file_id) for this game's Drive index.json, or (None, None)."""
    for child in client.list_children(game_folder_id):
        if child["name"] == _INDEX_NAME:
            raw = client.download_file(child["id"])
            try:
                return json.loads(raw.decode("utf-8")), child["id"]
            except (ValueError, UnicodeDecodeError):
                return None, None        # corrupt remote index -> treat as absent, re-create
    return None, None


def write_index(client, game_folder_id, index, existing_id=None) -> str:
    """Persist index.json under the game folder. Updates in place if existing_id is given,
    else creates it. Returns the Drive file id."""
    content = json.dumps(index, indent=1).encode("utf-8")
    if existing_id:
        client.update_file(existing_id, content)
        return existing_id
    return client.upload_file(_INDEX_NAME, game_folder_id, content)


def sync_game(client, root_folder_id, game_name, kept_versions, load_version_files, app_id=0) -> dict:
    """End-to-end one-game mirror: ensure the game folder, read its index.json, sync the
    kept versions (upload new, trash removed), and persist index.json LAST. Returns the index."""
    game_folder = client.find_or_create_folder(game_name, root_folder_id)
    index, index_id = read_index(client, game_folder)
    if index is None:
        index = empty_index(app_id)
    index["appId"] = app_id
    index["gameFolderId"] = game_folder
    holder = {"id": index_id}

    def persist(idx):
        holder["id"] = write_index(client, game_folder, idx, existing_id=holder["id"])

    return sync_versions(client, root_folder_id, game_name, kept_versions, index,
                         load_version_files, persist_index=persist)


def list_remote_versions(index) -> list:
    """Summaries of the versions present in a remote index.json."""
    return [{"versionId": vid, "label": v.get("label", vid), "pinned": v.get("pinned", False)}
            for vid, v in index.get("versions", {}).items()]


def download_version(client, index, version_id) -> dict:
    """Download a remote version's files as {suffix-qualified relpath: bytes}."""
    entry = index["versions"][version_id]
    return {relpath: client.download_file(file_id) for relpath, file_id in entry["fileIds"].items()}
