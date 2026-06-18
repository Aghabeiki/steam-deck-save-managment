import os
from savemanager.refs import read_refs, write_refs, make_version_entry


def test_read_refs_returns_fresh_when_missing(tmp_path):
    refs = read_refs(str(tmp_path), 281990)
    assert refs["appId"] == 281990
    assert refs["head"] == {"versionId": None, "detached": False}
    assert refs["versions"] == []


def test_write_then_read_roundtrip(tmp_path):
    refs = read_refs(str(tmp_path), 281990)
    refs["head"] = {"versionId": "v_1_a", "detached": False}
    write_refs(str(tmp_path), 281990, refs)
    again = read_refs(str(tmp_path), 281990)
    assert again["head"]["versionId"] == "v_1_a"


def test_second_write_creates_bak(tmp_path):
    write_refs(str(tmp_path), 281990, read_refs(str(tmp_path), 281990))
    write_refs(str(tmp_path), 281990, read_refs(str(tmp_path), 281990))
    from savemanager.store import game_dir
    assert os.path.isfile(os.path.join(game_dir(str(tmp_path), 281990), "refs.json.bak"))


def test_read_refs_falls_back_to_bak_when_main_corrupt(tmp_path):
    from savemanager.refs import refs_path
    r = read_refs(str(tmp_path), 281990)
    r["head"] = {"versionId": "v_good", "detached": False}
    write_refs(str(tmp_path), 281990, r)            # refs.json = v_good
    r["head"] = {"versionId": "v_newer", "detached": False}
    write_refs(str(tmp_path), 281990, r)            # refs.json = v_newer, .bak = v_good
    with open(refs_path(str(tmp_path), 281990), "w") as f:
        f.write("{ this is : not json")             # corrupt main
    got = read_refs(str(tmp_path), 281990)
    assert got["head"]["versionId"] == "v_good"     # recovered from .bak


def test_read_refs_returns_fresh_when_both_corrupt(tmp_path):
    from savemanager.refs import refs_path
    write_refs(str(tmp_path), 281990, read_refs(str(tmp_path), 281990))
    write_refs(str(tmp_path), 281990, read_refs(str(tmp_path), 281990))
    for p in (refs_path(str(tmp_path), 281990), refs_path(str(tmp_path), 281990) + ".bak"):
        with open(p, "w") as f:
            f.write("garbage")
    got = read_refs(str(tmp_path), 281990)
    assert got["versions"] == [] and got["appId"] == 281990


def test_write_refs_keeps_main_present_and_valid(tmp_path):
    import json as _json
    from savemanager.refs import refs_path
    write_refs(str(tmp_path), 281990, read_refs(str(tmp_path), 281990))
    write_refs(str(tmp_path), 281990, read_refs(str(tmp_path), 281990))
    # main is always present and valid JSON after a write (never the absent window)
    with open(refs_path(str(tmp_path), 281990)) as f:
        assert _json.load(f)["appId"] == 281990


def test_make_version_entry_from_meta():
    meta = {
        "versionId": "v_1_a", "createdAt": 5, "kind": "manual",
        "reason": "manual", "parent": None, "fileCount": 2, "totalBytes": 9,
    }
    entry = make_version_entry(meta)
    assert entry == {
        "versionId": "v_1_a", "createdAt": 5, "kind": "manual", "reason": "manual",
        "parent": None, "pinned": False, "name": None, "fileCount": 2, "totalBytes": 9,
    }
