from savemanager.mirror import empty_index, plan_sync


def test_empty_index_shape():
    idx = empty_index(281990)
    assert idx == {"appId": 281990, "gameFolderId": None, "versions": {}, "schemaVersion": 1}


def test_plan_sync_uploads_missing_and_prunes_removed():
    remote = {"appId": 1, "gameFolderId": "g", "schemaVersion": 1,
              "versions": {"v_old": {"label": "old", "folderId": "f1", "fileIds": {}},
                           "v_keep": {"label": "keep", "folderId": "f2", "fileIds": {}}}}
    plan = plan_sync(["v_keep", "v_new"], remote)        # local kept set
    assert plan["to_upload"] == ["v_new"]                # not yet on Drive
    assert plan["to_prune"] == ["v_old"]                 # on Drive but no longer kept


def test_plan_sync_empty_remote_uploads_all():
    plan = plan_sync(["a", "b"], empty_index(1))
    assert plan["to_upload"] == ["a", "b"] and plan["to_prune"] == []


from savemanager.mirror import sync_versions
from tests.drive_fakes import FakeDriveClient


def test_sync_versions_uploads_new_prunes_old_and_updates_index():
    client = FakeDriveClient()
    remote = {"appId": 1, "gameFolderId": "GAME", "schemaVersion": 1,
              "versions": {"v_old": {"label": "old", "folderId": "OLDF", "fileIds": {"a.sav": "x"}}}}
    kept = [{"versionId": "v_new", "label": "2026-06-18 (auto)"}]   # v_old no longer kept
    files = {"v_new": {"XComGame/SaveData/save1.sav": b"AAAAA"}}

    idx = sync_versions(client, "ROOT", "XCOM 2", kept, remote,
                        load_version_files=lambda vid: files[vid],
                        persist_index=lambda idx: None)

    # uploaded v_new's file into a freshly created version folder
    assert "v_new" in idx["versions"]
    vfolder = idx["versions"]["v_new"]["folderId"]
    assert any(p == vfolder for (_n, p, _c) in client.files.values())
    assert list(idx["versions"]["v_new"]["fileIds"].keys()) == ["XComGame/SaveData/save1.sav"]
    # pruned v_old's Drive folder and dropped it from the index
    assert "OLDF" in client.trashed
    assert "v_old" not in idx["versions"]
    # game folder reused from the index
    assert idx["gameFolderId"] == "GAME"


def test_sync_versions_creates_game_folder_when_absent():
    client = FakeDriveClient()
    kept = [{"versionId": "v1", "label": "L"}]
    idx = sync_versions(client, "ROOT", "Game", kept, empty_index(1),
                        load_version_files=lambda vid: {"s.sav": b"X"},
                        persist_index=lambda idx: None)
    assert idx["gameFolderId"] is not None
    assert idx["gameFolderId"] in client.folders


def test_sync_versions_persists_index_before_pruning():
    # C1: the index must be persisted (commit point) BEFORE any prune delete, and the
    # persisted index must already exclude the pruned version (no dangling reference).
    client = FakeDriveClient()
    remote = {"appId": 1, "gameFolderId": "GAME", "schemaVersion": 1,
              "versions": {"v_old": {"label": "old", "folderId": "OLDF", "fileIds": {}, "pinned": False}}}
    kept = [{"versionId": "v_new", "label": "L", "pinned": True}]
    seen = {}

    def persist(idx):
        seen["deletes_at_persist"] = list(client.trashed)        # deletes so far
        seen["versions_at_persist"] = set(idx["versions"].keys())

    sync_versions(client, "ROOT", "G", kept, remote,
                  load_version_files=lambda v: {"s.sav": b"X"}, persist_index=persist)

    assert seen["deletes_at_persist"] == []                      # nothing pruned before persist
    assert "v_old" not in seen["versions_at_persist"]            # index already excludes pruned
    assert "OLDF" in client.trashed                              # prune happened AFTER persist


def test_sync_versions_carries_pinned_and_multiple_files():
    client = FakeDriveClient()
    kept = [{"versionId": "v1", "label": "L", "pinned": True}]
    files = {"v1": {"a.sav": b"AA", "sub/b.sav": b"BBB"}}        # incl. a nested relpath
    idx = sync_versions(client, "ROOT", "G", kept, empty_index(1),
                        load_version_files=lambda v: files[v], persist_index=lambda i: None)
    entry = idx["versions"]["v1"]
    assert entry["pinned"] is True
    assert set(entry["fileIds"].keys()) == {"a.sav", "sub/b.sav"}   # original relpaths kept as keys
    assert len(client.files) == 2                                   # both files uploaded


from savemanager.mirror import read_index, write_index


def test_write_then_read_index_roundtrips(tmp_path):
    client = FakeDriveClient()
    game_folder = client.create_folder("XCOM 2", "ROOT")
    idx = empty_index(281990)
    idx["versions"]["v1"] = {"label": "L", "folderId": "f", "fileIds": {}, "pinned": False}
    file_id = write_index(client, game_folder, idx, existing_id=None)
    got, got_id = read_index(client, game_folder)
    assert got_id == file_id
    assert got["versions"]["v1"]["label"] == "L"


def test_read_index_missing_returns_none(tmp_path):
    client = FakeDriveClient()
    game_folder = client.create_folder("G", "ROOT")
    assert read_index(client, game_folder) == (None, None)


def test_write_index_updates_existing(tmp_path):
    client = FakeDriveClient()
    game_folder = client.create_folder("G", "ROOT")
    fid = write_index(client, game_folder, empty_index(1), existing_id=None)
    idx2 = empty_index(1); idx2["gameFolderId"] = game_folder
    same = write_index(client, game_folder, idx2, existing_id=fid)
    assert same == fid                                   # updated in place, no new file
    got, _ = read_index(client, game_folder)
    assert got["gameFolderId"] == game_folder


from savemanager.mirror import sync_game


def test_sync_game_uploads_then_is_idempotent():
    client = FakeDriveClient()
    files = {"v1": {"root/s.sav": b"DATA"}}
    kept = [{"versionId": "v1", "label": "v1", "pinned": False}]

    idx1 = sync_game(client, "ROOT", "XCOM 2", kept, lambda vid: files[vid])
    assert "v1" in idx1["versions"]
    uploads_after_first = len([f for f in client.files.values() if f[0] != "index.json"])
    assert uploads_after_first == 1                       # the one save file

    idx2 = sync_game(client, "ROOT", "XCOM 2", kept, lambda vid: files[vid])
    assert "v1" in idx2["versions"]
    uploads_after_second = len([f for f in client.files.values() if f[0] != "index.json"])
    assert uploads_after_second == 1                       # nothing re-uploaded (read index → no-op)


def test_sync_game_sets_app_id():
    client = FakeDriveClient()
    idx = sync_game(client, "ROOT", "G", [], lambda v: {}, app_id=42)
    assert idx["appId"] == 42


def test_read_index_corrupt_returns_none():
    client = FakeDriveClient()
    gf = client.create_folder("G", "ROOT")
    client.upload_file("index.json", gf, b"not json")
    assert read_index(client, gf) == (None, None)


from savemanager.mirror import list_remote_versions, download_version


def test_list_remote_versions_from_index():
    idx = {"versions": {"v1": {"label": "L1", "folderId": "f1", "fileIds": {}, "pinned": True},
                        "v2": {"label": "L2", "folderId": "f2", "fileIds": {}, "pinned": False}}}
    out = list_remote_versions(idx)
    assert {v["versionId"] for v in out} == {"v1", "v2"}
    assert next(v for v in out if v["versionId"] == "v1")["pinned"] is True
    assert next(v for v in out if v["versionId"] == "v2")["label"] == "L2"


def test_download_version_fetches_each_file():
    client = FakeDriveClient()
    fid = client.upload_file("x", "folder", b"BYTES")
    idx = {"versions": {"v1": {"label": "L", "folderId": "folder",
                               "fileIds": {"root/s.sav": fid}, "pinned": False}}}
    assert download_version(client, idx, "v1") == {"root/s.sav": b"BYTES"}
