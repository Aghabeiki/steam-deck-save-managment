import os
from savemanager.versioning import import_version, list_versions
from savemanager.store import read_meta, version_dir


def test_import_version_reconstructs_local_version(tmp_path):
    data_root = os.path.join(str(tmp_path), "data")
    files = {"root/save1.sav": b"AAAAA", "root_1/x/y.sav": b"BB"}
    entry = import_version(data_root, 281990, "v_1000_aaa", "Before boss", True, files)
    assert entry["versionId"] == "v_1000_aaa" and entry["pinned"] is True
    assert entry["name"] == "Before boss" and entry["createdAt"] == 1000
    vdir = version_dir(data_root, 281990, "v_1000_aaa")
    with open(os.path.join(vdir, "root", "save1.sav"), "rb") as f:
        assert f.read() == b"AAAAA"
    with open(os.path.join(vdir, "root_1", "x", "y.sav"), "rb") as f:
        assert f.read() == b"BB"
    # appears in the list with a real meta (sha256 + suffix/path), HEAD unchanged
    listing = list_versions(data_root, 281990)
    assert [v["versionId"] for v in listing["versions"]] == ["v_1000_aaa"]
    assert listing["head"]["versionId"] is None
    meta = read_meta(data_root, 281990, "v_1000_aaa")
    assert all(len(f["sha256"]) == 64 for f in meta["files"])
    assert {(f["suffix"], f["path"]) for f in meta["files"]} == {("", "save1.sav"), ("_1", "x/y.sav")}


def test_import_version_is_idempotent(tmp_path):
    data_root = os.path.join(str(tmp_path), "data")
    import_version(data_root, 1, "v_1_a", "L", False, {"root/s.sav": b"X"})
    import_version(data_root, 1, "v_1_a", "L", False, {"root/s.sav": b"X"})
    assert len(list_versions(data_root, 1)["versions"]) == 1     # no duplicate entry


def test_import_version_label_equal_to_id_means_no_name(tmp_path):
    data_root = os.path.join(str(tmp_path), "data")
    entry = import_version(data_root, 1, "v_2_b", "v_2_b", False, {"root/s.sav": b"X"})
    assert entry["name"] is None                                  # label == versionId -> no user name


def test_import_version_rejects_path_traversal(tmp_path):
    data_root = os.path.join(str(tmp_path), "data")
    import_version(data_root, 1, "v_1_a", "L", False,
                   {"root/../../../../escape.txt": b"EVIL", "root/ok.sav": b"OK"})
    assert not os.path.exists(os.path.join(str(tmp_path), "escape.txt"))     # traversal skipped
    meta = read_meta(data_root, 1, "v_1_a")
    assert {f["path"] for f in meta["files"]} == {"ok.sav"}                   # only the safe file


def test_import_version_existing_is_not_overwritten(tmp_path):
    from savemanager.versioning import load_version_files
    data_root = os.path.join(str(tmp_path), "data")
    import_version(data_root, 1, "v_1_a", "L", False, {"root/s.sav": b"FIRST"})
    import_version(data_root, 1, "v_1_a", "L", False, {"root/s.sav": b"SECOND"})   # same id
    assert load_version_files(data_root, 1, "v_1_a") == {"root/s.sav": b"FIRST"}    # not rewritten
