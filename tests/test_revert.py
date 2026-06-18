import os
from savemanager.store import create_snapshot, restore_version, version_dir
from savemanager.vdf import RcfEntry


def test_restore_version_copies_files_into_live_roots(tmp_path):
    live = os.path.join(str(tmp_path), "live"); os.makedirs(live)
    with open(os.path.join(live, "s.sav"), "w") as f:
        f.write("ORIGINAL")
    entries = [RcfEntry(path="s.sav", root=0, size=8, mtime=0)]
    data_root = os.path.join(str(tmp_path), "data")
    create_snapshot(data_root, 1, {live: ""}, entries, "v_1_a", 1,
                    kind="manual", reason="manual", parent=None)
    # mutate the live file, then restore the snapshot over it
    with open(os.path.join(live, "s.sav"), "w") as f:
        f.write("CHANGED")
    restored = restore_version(data_root, 1, "v_1_a", {"": live})
    assert restored == {("", "s.sav")}
    with open(os.path.join(live, "s.sav")) as f:
        assert f.read() == "ORIGINAL"      # restored byte-for-byte


def test_restore_version_skips_suffix_with_no_current_root(tmp_path):
    live = os.path.join(str(tmp_path), "live"); os.makedirs(live)
    with open(os.path.join(live, "s.sav"), "w") as f:
        f.write("X")
    entries = [RcfEntry(path="s.sav", root=0, size=1, mtime=0)]
    data_root = os.path.join(str(tmp_path), "data")
    create_snapshot(data_root, 1, {live: "_1"}, entries, "v_1_a", 1,
                    kind="manual", reason="manual", parent=None)
    # current roots only know suffix "" -> the snapshot's "_1" files are skipped
    assert restore_version(data_root, 1, "v_1_a", {"": live}) == set()


from savemanager.versioning import do_backup, revert_to, list_versions
from tests.fixtures import make_steam_tree


def _save_path(steam_root, acct, app, name):
    return os.path.join(steam_root, "userdata", str(acct), str(app), "remote", name)


def _args(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path)
    return os.path.join(str(tmp_path), "data2"), steam_root, acct, app


def _bump_mtime(path, offset_s=1):
    """Advance a file's mtime by offset_s seconds so mtime-based change detection
    works reliably on systems with coarse mtime granularity (e.g. tmpfs at HZ=300)."""
    t = os.stat(path).st_mtime + offset_s
    os.utime(path, (t, t))


def test_revert_moves_head_and_restores_files(tmp_path):
    data_root, steam_root, acct, app = _args(tmp_path)
    gi = {"appId": app, "name": "X"}
    do_backup(data_root, steam_root, acct, gi, now_ms=1000, rand_hex="aaa")   # v1
    with open(_save_path(steam_root, acct, app, "save1.sav"), "w") as f:
        f.write("LATER")
    _bump_mtime(_save_path(steam_root, acct, app, "save1.sav"))
    do_backup(data_root, steam_root, acct, gi, now_ms=2000, rand_hex="bbb")   # v2 (head)
    head = revert_to(data_root, steam_root, acct, app, "v_1000_aaa",
                     now_ms=3000, rand_hex="ccc")
    assert head == {"versionId": "v_1000_aaa", "detached": True}              # older than newest
    with open(_save_path(steam_root, acct, app, "save1.sav")) as f:
        assert f.read() == "AAAAA"          # v1 content restored
    # no auto-snapshot needed (live matched v2 before revert)
    assert {v["versionId"] for v in list_versions(data_root, app)["versions"]} == {"v_1000_aaa", "v_2000_bbb"}


def test_revert_forward_again_clears_detached(tmp_path):
    data_root, steam_root, acct, app = _args(tmp_path)
    gi = {"appId": app, "name": "X"}
    do_backup(data_root, steam_root, acct, gi, now_ms=1000, rand_hex="aaa")
    with open(_save_path(steam_root, acct, app, "save1.sav"), "w") as f:
        f.write("LATER")
    _bump_mtime(_save_path(steam_root, acct, app, "save1.sav"))
    do_backup(data_root, steam_root, acct, gi, now_ms=2000, rand_hex="bbb")
    revert_to(data_root, steam_root, acct, app, "v_1000_aaa", now_ms=3000, rand_hex="ccc")
    head = revert_to(data_root, steam_root, acct, app, "v_2000_bbb", now_ms=4000, rand_hex="ddd")
    assert head == {"versionId": "v_2000_bbb", "detached": False}   # v2 is newest
    with open(_save_path(steam_root, acct, app, "save1.sav")) as f:
        assert f.read() == "LATER"


def test_revert_autosnapshots_unsaved_live_changes(tmp_path):
    data_root, steam_root, acct, app = _args(tmp_path)
    gi = {"appId": app, "name": "X"}
    do_backup(data_root, steam_root, acct, gi, now_ms=1000, rand_hex="aaa")   # v1 = "AAAAA"
    # play without backing up
    with open(_save_path(steam_root, acct, app, "save1.sav"), "w") as f:
        f.write("UNSAVED")
    revert_to(data_root, steam_root, acct, app, "v_1000_aaa", now_ms=2000, rand_hex="bbb")
    versions = list_versions(data_root, app)["versions"]
    autos = [v for v in versions if v["reason"] == "pre-revert-autosnapshot"]
    assert len(autos) == 1                                      # unsaved state preserved
    # and the live file is now v1
    with open(_save_path(steam_root, acct, app, "save1.sav")) as f:
        assert f.read() == "AAAAA"


def test_revert_deletes_managed_files_absent_from_target(tmp_path):
    data_root, steam_root, acct, app = _args(tmp_path)
    gi = {"appId": app, "name": "X"}
    do_backup(data_root, steam_root, acct, gi, now_ms=1000, rand_hex="aaa")   # v1 has save1+profile
    os.remove(_save_path(steam_root, acct, app, "profile.bin"))               # delete one save
    do_backup(data_root, steam_root, acct, gi, now_ms=2000, rand_hex="bbb")   # v2 has only save1
    revert_to(data_root, steam_root, acct, app, "v_1000_aaa", now_ms=3000, rand_hex="ccc")
    assert os.path.isfile(_save_path(steam_root, acct, app, "profile.bin"))   # restored by v1
    revert_to(data_root, steam_root, acct, app, "v_2000_bbb", now_ms=4000, rand_hex="ddd")
    assert not os.path.isfile(_save_path(steam_root, acct, app, "profile.bin"))  # managed + absent from v2 -> removed


def test_revert_returns_none_for_unknown_target(tmp_path):
    data_root, steam_root, acct, app = _args(tmp_path)
    do_backup(data_root, steam_root, acct, {"appId": app, "name": "X"}, now_ms=1000, rand_hex="aaa")
    assert revert_to(data_root, steam_root, acct, app, "v_nope", now_ms=2000, rand_hex="bbb") is None


from savemanager.versioning import resume_pending_revert
from savemanager.refs import read_refs, write_refs


def test_resume_pending_finishes_interrupted_revert(tmp_path):
    data_root, steam_root, acct, app = _args(tmp_path)
    gi = {"appId": app, "name": "X"}
    do_backup(data_root, steam_root, acct, gi, now_ms=1000, rand_hex="aaa")   # v1 = AAAAA
    with open(_save_path(steam_root, acct, app, "save1.sav"), "w") as f:
        f.write("CHANGED-SAVE-DATA")                                          # different LENGTH -> reliably detected
    do_backup(data_root, steam_root, acct, gi, now_ms=2000, rand_hex="bbb")   # v2 (head), live=CHANGED...
    # Simulate a crash mid-revert: pendingRevertTo set, HEAD still v2, live still CHANGED.
    refs = read_refs(data_root, app)
    refs["pendingRevertTo"] = "v_1000_aaa"
    write_refs(data_root, app, refs)
    head = resume_pending_revert(data_root, steam_root, acct, app)
    assert head == {"versionId": "v_1000_aaa", "detached": True}
    assert read_refs(data_root, app)["pendingRevertTo"] is None
    with open(_save_path(steam_root, acct, app, "save1.sav")) as f:
        assert f.read() == "AAAAA"                              # target materialized


def test_resume_pending_noop_when_nothing_pending(tmp_path):
    data_root, steam_root, acct, app = _args(tmp_path)
    do_backup(data_root, steam_root, acct, {"appId": app, "name": "X"}, now_ms=1000, rand_hex="aaa")
    assert resume_pending_revert(data_root, steam_root, acct, app) is None


def test_restore_version_multi_root(tmp_path):
    from savemanager.store import create_snapshot, restore_version
    r0 = os.path.join(str(tmp_path), "r0"); os.makedirs(r0)
    r1 = os.path.join(str(tmp_path), "r1"); os.makedirs(r1)
    with open(os.path.join(r0, "s.sav"), "w") as f: f.write("ZERO")
    with open(os.path.join(r1, "s.sav"), "w") as f: f.write("ONE")
    entries = [RcfEntry(path="s.sav", root=0, size=4, mtime=0)]
    data_root = os.path.join(str(tmp_path), "data")
    create_snapshot(data_root, 1, {r0: "", r1: "_1"}, entries, "v_1_a", 1,
                    kind="manual", reason="manual", parent=None)
    with open(os.path.join(r0, "s.sav"), "w") as f: f.write("XXXX")
    with open(os.path.join(r1, "s.sav"), "w") as f: f.write("YYYY")
    restored = restore_version(data_root, 1, "v_1_a", {"": r0, "_1": r1})
    assert restored == {("", "s.sav"), ("_1", "s.sav")}
    with open(os.path.join(r0, "s.sav")) as f: assert f.read() == "ZERO"
    with open(os.path.join(r1, "s.sav")) as f: assert f.read() == "ONE"
