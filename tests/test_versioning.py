import os
from savemanager.versioning import do_backup, list_versions, is_supported
from savemanager.discovery import parse_installdir
from tests.fixtures import make_steam_tree


def _args(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path)
    data_root = os.path.join(str(tmp_path), "data")
    return data_root, steam_root, acct, app


def test_is_supported_true_for_cloud_game(tmp_path):
    _, steam_root, acct, app = _args(tmp_path)
    assert is_supported(steam_root, acct, app, parse_installdir(steam_root, app)) is True


def test_is_supported_false_when_no_remotecache(tmp_path):
    _, steam_root, acct, _ = _args(tmp_path)
    assert is_supported(steam_root, acct, 999999, None) is False


def test_do_backup_creates_version_and_sets_head(tmp_path):
    data_root, steam_root, acct, app = _args(tmp_path)
    entry = do_backup(data_root, steam_root, acct, {"appId": app, "name": "XCOM 2"},
                      now_ms=1000, rand_hex="aaa")
    assert entry["versionId"] == "v_1000_aaa"
    listing = list_versions(data_root, app)
    assert listing["head"]["versionId"] == "v_1000_aaa"
    assert len(listing["versions"]) == 1


def test_do_backup_skips_when_unchanged(tmp_path):
    data_root, steam_root, acct, app = _args(tmp_path)
    do_backup(data_root, steam_root, acct, {"appId": app, "name": "X"},
              now_ms=1000, rand_hex="aaa")
    second = do_backup(data_root, steam_root, acct, {"appId": app, "name": "X"},
                       now_ms=2000, rand_hex="bbb")
    assert second is None
    assert len(list_versions(data_root, app)["versions"]) == 1


def test_do_backup_survives_corrupt_head_meta(tmp_path):
    data_root, steam_root, acct, app = _args(tmp_path)
    do_backup(data_root, steam_root, acct, {"appId": app, "name": "X"},
              now_ms=1000, rand_hex="aaa")
    # corrupt the HEAD version's meta.json
    from savemanager.store import version_dir
    with open(os.path.join(version_dir(data_root, app, "v_1000_aaa"), "meta.json"), "w") as f:
        f.write("not json")
    # must not raise; can't compare -> takes a fresh backup
    entry = do_backup(data_root, steam_root, acct, {"appId": app, "name": "X"},
                      now_ms=2000, rand_hex="bbb")
    assert entry is not None and entry["versionId"] == "v_2000_bbb"


def test_do_backup_after_file_deletion_creates_version(tmp_path):
    data_root, steam_root, acct, app = _args(tmp_path)
    do_backup(data_root, steam_root, acct, {"appId": app, "name": "X"},
              now_ms=1000, rand_hex="aaa")
    remote = os.path.join(steam_root, "userdata", str(acct), str(app), "remote")
    os.remove(os.path.join(remote, "profile.bin"))     # delete one save file
    entry = do_backup(data_root, steam_root, acct, {"appId": app, "name": "X"},
                      now_ms=2000, rand_hex="bbb")
    assert entry is not None     # fingerprint differs -> new version


def test_do_backup_new_version_after_change_links_parent(tmp_path):
    data_root, steam_root, acct, app = _args(tmp_path)
    do_backup(data_root, steam_root, acct, {"appId": app, "name": "X"},
              now_ms=1000, rand_hex="aaa")
    # change a save file
    remote = os.path.join(steam_root, "userdata", str(acct), str(app), "remote")
    with open(os.path.join(remote, "save1.sav"), "w") as f:
        f.write("CHANGED")
    entry = do_backup(data_root, steam_root, acct, {"appId": app, "name": "X"},
                      now_ms=3000, rand_hex="ccc")
    assert entry["versionId"] == "v_3000_ccc"
    assert entry["parent"] == "v_1000_aaa"
    versions = list_versions(data_root, app)["versions"]
    assert [v["versionId"] for v in versions] == ["v_3000_ccc", "v_1000_aaa"]  # newest first
