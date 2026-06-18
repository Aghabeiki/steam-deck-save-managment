import os
from savemanager.api import Engine
from tests.fixtures import make_steam_tree


def _engine(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path)
    eng = Engine(os.path.join(str(tmp_path), "data"), steam_root)
    eng.set_account_id(acct)
    return eng, steam_root, acct, app


def _save1(steam_root, acct, app):
    return os.path.join(steam_root, "userdata", str(acct), str(app), "remote", "save1.sav")


def test_engine_revert_round_trip(tmp_path):
    eng, steam_root, acct, app = _engine(tmp_path)
    gi = {"appId": app, "name": "X"}
    eng.do_backup(gi, now_ms=1000, rand_hex="aaa")
    with open(_save1(steam_root, acct, app), "w") as f:
        f.write("CHANGED-SAVE-DATA")
    eng.do_backup(gi, now_ms=2000, rand_hex="bbb")
    head = eng.revert(gi, "v_1000_aaa", now_ms=3000, rand_hex="ccc")
    assert head["versionId"] == "v_1000_aaa"
    with open(_save1(steam_root, acct, app)) as f:
        assert f.read() == "AAAAA"


def test_engine_pin_rename_delete_and_settings(tmp_path):
    eng, steam_root, acct, app = _engine(tmp_path)
    gi = {"appId": app, "name": "X"}
    eng.do_backup(gi, now_ms=1000, rand_hex="aaa")
    assert eng.set_pinned(app, "v_1000_aaa", True) is True
    assert eng.set_name(app, "v_1000_aaa", "boss") is True
    v = eng.get_versions(app)["versions"][0]
    assert v["pinned"] is True and v["name"] == "boss"
    # settings
    assert eng.get_settings(app)["keepCount"] == 20
    assert eng.set_keep_count(app, 9)["keepCount"] == 9
    assert eng.get_settings(app)["keepCount"] == 9
    # cannot delete head
    assert eng.remove_version(app, "v_1000_aaa") is False


def test_engine_get_versions_resumes_pending_revert(tmp_path):
    from savemanager.refs import read_refs, write_refs
    eng, steam_root, acct, app = _engine(tmp_path)
    gi = {"appId": app, "name": "X"}
    eng.do_backup(gi, now_ms=1000, rand_hex="aaa")
    with open(_save1(steam_root, acct, app), "w") as f:
        f.write("CHANGED-SAVE-DATA")
    eng.do_backup(gi, now_ms=2000, rand_hex="bbb")
    refs = read_refs(eng.data_root, app)
    refs["pendingRevertTo"] = "v_1000_aaa"
    write_refs(eng.data_root, app, refs)
    listing = eng.get_versions(app)          # should self-heal the interrupted revert
    assert listing["head"]["versionId"] == "v_1000_aaa"
    assert read_refs(eng.data_root, app)["pendingRevertTo"] is None
