import os
from savemanager.api import Engine, quiescence_verdict
from tests.fixtures import make_steam_tree


def _engine(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path)
    eng = Engine(os.path.join(str(tmp_path), "data"), steam_root)
    eng.set_account_id(acct)
    return eng, steam_root, acct, app


def _save1(steam_root, acct, app):
    return os.path.join(steam_root, "userdata", str(acct), str(app), "remote", "save1.sav")


def _key(h, name):
    return next(k for k in h if k[1] == name)


def test_hash_live_save_returns_content_map(tmp_path):
    eng, steam_root, acct, app = _engine(tmp_path)
    h = eng.hash_live_save(app)
    assert {path for (_suffix, path) in h} == {"save1.sav", "profile.bin"}
    assert all(isinstance(v, str) and len(v) == 64 for v in h.values())


def test_hash_live_save_reflects_content_change(tmp_path):
    eng, steam_root, acct, app = _engine(tmp_path)
    before = eng.hash_live_save(app)
    with open(_save1(steam_root, acct, app), "w") as f:
        f.write("DIFFERENT-CONTENT")
    after = eng.hash_live_save(app)
    assert after[_key(after, "save1.sav")] != before[_key(before, "save1.sav")]
    assert after[_key(after, "profile.bin")] == before[_key(before, "profile.bin")]


def test_hash_live_save_none_when_unresolvable(tmp_path):
    eng = Engine(os.path.join(str(tmp_path), "data"), os.path.join(str(tmp_path), "EmptySteam"))
    assert eng.hash_live_save(281990) is None


def test_quiescence_verdict():
    a = {("", "save1.sav"): "x"}
    assert quiescence_verdict(a, dict(a)) == "stable"
    assert quiescence_verdict(a, {("", "save1.sav"): "y"}) == "writing"
    assert quiescence_verdict(None, a) == "unresolvable"
    assert quiescence_verdict(a, None) == "unresolvable"


def test_current_state_after_backup_is_head(tmp_path):
    eng, steam_root, acct, app = _engine(tmp_path)
    entry = eng.do_backup({"appId": app, "name": "X"}, now_ms=1000, rand_hex="aaa")
    st = eng.current_state(app)
    assert st["matchedVersionId"] == entry["versionId"]
    assert st["isHead"] is True and st["modified"] is False and st["resolvable"] is True


def test_current_state_modified_after_play(tmp_path):
    eng, steam_root, acct, app = _engine(tmp_path)
    eng.do_backup({"appId": app, "name": "X"}, now_ms=1000, rand_hex="aaa")
    with open(_save1(steam_root, acct, app), "w") as f:
        f.write("PLAYED-MORE")
    st = eng.current_state(app)
    assert st["modified"] is True and st["matchedVersionId"] is None and st["resolvable"] is True


def test_hash_live_save_deterministic(tmp_path):
    eng, _, _, app = _engine(tmp_path)
    assert eng.hash_live_save(app) == eng.hash_live_save(app)
