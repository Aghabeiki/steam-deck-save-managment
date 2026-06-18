import os
from savemanager.versioning import do_backup, load_version_files, kept_versions_for, list_versions
from savemanager.curation import set_pinned, set_name
from tests.fixtures import make_steam_tree


def _ctx(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path)
    return os.path.join(str(tmp_path), "data"), steam_root, acct, app


def test_load_version_files_returns_suffix_qualified_bytes(tmp_path):
    data_root, steam_root, acct, app = _ctx(tmp_path)
    do_backup(data_root, steam_root, acct, {"appId": app, "name": "X"}, now_ms=1, rand_hex="a")
    head = list_versions(data_root, app)["head"]["versionId"]
    files = load_version_files(data_root, app, head)
    assert files == {"root/save1.sav": b"AAAAA", "root/profile.bin": b"BBBB"}


def test_kept_versions_for_uses_name_then_versionid_and_carries_pinned(tmp_path):
    data_root, steam_root, acct, app = _ctx(tmp_path)
    do_backup(data_root, steam_root, acct, {"appId": app, "name": "X"}, now_ms=1, rand_hex="a")
    head = list_versions(data_root, app)["head"]["versionId"]
    set_pinned(data_root, app, head, True)
    set_name(data_root, app, head, "Before boss")
    kept = kept_versions_for(data_root, app)
    assert kept == [{"versionId": head, "label": "Before boss", "pinned": True}]
