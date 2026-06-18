import os
from savemanager.api import Engine
from tests.fixtures import make_steam_tree


def _engine(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path)
    eng = Engine(os.path.join(str(tmp_path), "data"), steam_root)
    eng.set_account_id(acct)
    return eng, steam_root, acct, app


def test_set_auto_backup_persists(tmp_path):
    eng, _, _, app = _engine(tmp_path)
    assert eng.get_settings(app)["autoBackupOnExit"] is False
    assert eng.set_auto_backup(app, True)["autoBackupOnExit"] is True
    assert eng.get_settings(app)["autoBackupOnExit"] is True


def test_do_backup_on_exit_noop_when_disabled(tmp_path):
    eng, _, _, app = _engine(tmp_path)
    assert eng.do_backup_on_exit({"appId": app, "name": "X"}, now_ms=1, rand_hex="a") is None
    assert eng.get_versions(app)["versions"] == []


def test_do_backup_on_exit_creates_auto_version_when_enabled(tmp_path):
    eng, _, _, app = _engine(tmp_path)
    eng.set_auto_backup(app, True)
    entry = eng.do_backup_on_exit({"appId": app, "name": "X"}, now_ms=1000, rand_hex="aaa")
    assert entry is not None
    assert entry["kind"] == "auto" and entry["reason"] == "game-exit"
    assert eng.get_versions(app)["head"]["versionId"] == "v_1000_aaa"


def test_remotecache_mtime_returns_file_mtime(tmp_path):
    eng, steam_root, acct, app = _engine(tmp_path)
    rc = os.path.join(steam_root, "userdata", str(acct), str(app), "remotecache.vdf")
    os.utime(rc, (1234.0, 1234.0))
    assert eng.remotecache_mtime(app) == 1234.0
    assert eng.remotecache_mtime(999999) == 0.0       # no file -> 0.0
