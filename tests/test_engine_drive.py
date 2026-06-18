import os
from savemanager.api import Engine
from tests.fixtures import make_steam_tree
from tests.drive_fakes import FakeDriveClient


def _engine(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path)
    eng = Engine(os.path.join(str(tmp_path), "data"), steam_root)
    eng.set_account_id(acct)
    return eng, app


def test_drive_credentials_roundtrip(tmp_path):
    eng, _ = _engine(tmp_path)
    assert eng.get_drive_status()["linked"] is False
    eng.set_drive_client("CID", "SECRET")
    eng.set_drive_refresh_token("RT")
    st = eng.get_drive_status()
    assert st["linked"] is True and st["hasClient"] is True


def test_sync_drive_with_client_mirrors_kept_versions(tmp_path):
    eng, app = _engine(tmp_path)
    steam_root = eng.steam_root
    acct = eng.account_ids[0]
    from savemanager.versioning import do_backup
    do_backup(eng.data_root, steam_root, acct, {"appId": app, "name": "XCOM 2"},
              now_ms=1, rand_hex="a")
    client = FakeDriveClient()
    idx = eng.sync_drive_with_client(app, "XCOM 2", client, "ROOT")    # inject fake client
    assert len(idx["versions"]) == 1
    assert any(name != "index.json" for (name, _p, _c) in client.files.values())


def test_list_remote_versions_empty_when_no_game_folder(tmp_path):
    eng, _ = _engine(tmp_path)
    assert eng.list_remote_versions_with_client("Nope", FakeDriveClient(), "ROOT") == []


def test_drive_backup_then_restore_round_trip(tmp_path):
    from savemanager.versioning import do_backup, list_versions, load_version_files
    steam_root, acct, app = make_steam_tree(tmp_path)
    engA = Engine(os.path.join(str(tmp_path), "A"), steam_root); engA.set_account_id(acct)
    do_backup(engA.data_root, steam_root, acct, {"appId": app, "name": "XCOM 2"},
              now_ms=1000, rand_hex="aaa")
    client = FakeDriveClient()
    engA.sync_drive_with_client(app, "XCOM 2", client, "ROOT")              # mirror to (fake) Drive

    engB = Engine(os.path.join(str(tmp_path), "B"), steam_root); engB.set_account_id(acct)
    remote = engB.list_remote_versions_with_client("XCOM 2", client, "ROOT")
    assert [v["versionId"] for v in remote] == ["v_1000_aaa"]
    engB.restore_from_drive_with_client(app, "XCOM 2", "v_1000_aaa", client, "ROOT")
    assert [v["versionId"] for v in list_versions(engB.data_root, app)["versions"]] == ["v_1000_aaa"]
    assert load_version_files(engB.data_root, app, "v_1000_aaa") == \
        {"root/save1.sav": b"AAAAA", "root/profile.bin": b"BBBB"}            # bytes restored exactly
