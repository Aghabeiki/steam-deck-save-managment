import os
from savemanager.versioning import do_backup, list_versions
from savemanager.store import read_meta, version_dir
from tests.fixtures import make_steam_tree


def _ctx(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path)
    data_root = os.path.join(str(tmp_path), "data")
    save1 = os.path.join(steam_root, "userdata", str(acct), str(app), "remote", "save1.sav")
    return data_root, steam_root, acct, app, save1


def test_meta_records_sha256(tmp_path):
    data_root, steam_root, acct, app, _ = _ctx(tmp_path)
    do_backup(data_root, steam_root, acct, {"appId": app, "name": "X"}, now_ms=1, rand_hex="a")
    head = list_versions(data_root, app)["head"]["versionId"]
    meta = read_meta(data_root, app, head)
    assert all(len(f["sha256"]) == 64 for f in meta["files"])     # sha256 hex digest


def test_same_size_same_mtime_change_is_detected_by_hash(tmp_path):
    """The M2 false-skip: identical size AND mtime but different content -> only the
    hash tiebreaker catches it, so do_backup must still create a new version."""
    data_root, steam_root, acct, app, save1 = _ctx(tmp_path)
    gi = {"appId": app, "name": "X"}
    do_backup(data_root, steam_root, acct, gi, now_ms=1000, rand_hex="aaa")     # v1 = "AAAAA"
    head = list_versions(data_root, app)["head"]["versionId"]
    stored = os.path.join(version_dir(data_root, app, head), "root", "save1.sav")
    st_ns = os.stat(stored).st_mtime_ns                     # exact mtime of the stored copy
    with open(save1, "w") as f:
        f.write("BBBBB")                                    # SAME length as "AAAAA" (5 bytes)
    os.utime(save1, ns=(st_ns, st_ns))                      # force IDENTICAL mtime -> size+mtime match
    entry = do_backup(data_root, steam_root, acct, gi, now_ms=2000, rand_hex="bbb")
    assert entry is not None and entry["versionId"] == "v_2000_bbb"   # hash detected the change


def test_truly_unchanged_is_still_skipped(tmp_path):
    data_root, steam_root, acct, app, _ = _ctx(tmp_path)
    gi = {"appId": app, "name": "X"}
    do_backup(data_root, steam_root, acct, gi, now_ms=1000, rand_hex="aaa")
    assert do_backup(data_root, steam_root, acct, gi, now_ms=2000, rand_hex="bbb") is None


def test_legacy_meta_without_sha256_falls_back_to_size_mtime(tmp_path):
    # A pre-M3 version has no per-file sha256; change-detection must still work via size+mtime.
    import json
    data_root, steam_root, acct, app, _ = _ctx(tmp_path)
    gi = {"appId": app, "name": "X"}
    do_backup(data_root, steam_root, acct, gi, now_ms=1000, rand_hex="aaa")
    head = list_versions(data_root, app)["head"]["versionId"]
    mpath = os.path.join(version_dir(data_root, app, head), "meta.json")
    with open(mpath) as f:
        meta = json.load(f)
    for fobj in meta["files"]:
        fobj.pop("sha256", None)            # simulate a legacy (pre-M3) meta
    with open(mpath, "w") as f:
        json.dump(meta, f)
    # nothing changed on disk -> still skipped via the size+mtime fallback (no sha256 to compare)
    assert do_backup(data_root, steam_root, acct, gi, now_ms=2000, rand_hex="bbb") is None
