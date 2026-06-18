from savemanager.refs import read_refs, write_refs
from savemanager.curation import set_pinned, set_name


def _seed_one(tmp_path, vid="v_1_a"):
    data_root = str(tmp_path)
    refs = read_refs(data_root, 1)
    refs["versions"] = [{"versionId": vid, "createdAt": 1, "kind": "manual",
                         "reason": "manual", "parent": None, "pinned": False,
                         "name": None, "fileCount": 0, "totalBytes": 0}]
    refs["head"] = {"versionId": vid, "detached": False}
    write_refs(data_root, 1, refs)
    return data_root


def test_set_pinned_toggles_flag(tmp_path):
    data_root = _seed_one(tmp_path)
    assert set_pinned(data_root, 1, "v_1_a", True) is True
    assert read_refs(data_root, 1)["versions"][0]["pinned"] is True
    set_pinned(data_root, 1, "v_1_a", False)
    assert read_refs(data_root, 1)["versions"][0]["pinned"] is False


def test_set_name_sets_label(tmp_path):
    data_root = _seed_one(tmp_path)
    assert set_name(data_root, 1, "v_1_a", "Before boss") is True
    assert read_refs(data_root, 1)["versions"][0]["name"] == "Before boss"


def test_returns_false_for_unknown_version(tmp_path):
    data_root = _seed_one(tmp_path)
    assert set_pinned(data_root, 1, "nope", True) is False
    assert set_name(data_root, 1, "nope", "x") is False


def test_remove_version_deletes_entry_and_dir(tmp_path):
    import os
    from savemanager.store import version_dir
    from savemanager.curation import remove_version
    data_root = _seed_one(tmp_path, "v_1_a")
    # add a second, non-head version with a real dir
    refs = read_refs(data_root, 1)
    refs["versions"].insert(0, {"versionId": "v_2_b", "createdAt": 2, "kind": "manual",
                                "reason": "manual", "parent": "v_1_a", "pinned": False,
                                "name": None, "fileCount": 0, "totalBytes": 0})
    refs["head"] = {"versionId": "v_2_b", "detached": False}
    write_refs(data_root, 1, refs)
    os.makedirs(version_dir(data_root, 1, "v_1_a"), exist_ok=True)
    assert remove_version(data_root, 1, "v_1_a") is True
    assert [v["versionId"] for v in read_refs(data_root, 1)["versions"]] == ["v_2_b"]
    assert not os.path.exists(version_dir(data_root, 1, "v_1_a"))


def test_remove_version_refuses_head(tmp_path):
    from savemanager.curation import remove_version
    data_root = _seed_one(tmp_path, "v_1_a")   # head == v_1_a
    assert remove_version(data_root, 1, "v_1_a") is False
    assert len(read_refs(data_root, 1)["versions"]) == 1
