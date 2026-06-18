import os
from savemanager.refs import read_refs, write_refs
from savemanager.store import version_dir
from savemanager.versioning import cull_versions


def _mk_entry(ms, pinned=False):
    return {"versionId": f"v_{ms}_x", "createdAt": ms, "kind": "manual",
            "reason": "manual", "parent": None, "pinned": pinned, "name": None,
            "fileCount": 0, "totalBytes": 0}


def _seed(tmp_path, app_id, entries, head_id):
    data_root = str(tmp_path)
    for e in entries:
        os.makedirs(version_dir(data_root, app_id, e["versionId"]), exist_ok=True)
    refs = read_refs(data_root, app_id)
    refs["versions"] = list(entries)              # newest-first
    refs["head"] = {"versionId": head_id, "detached": False}
    write_refs(data_root, app_id, refs)
    return data_root


def test_cull_deletes_oldest_unpinned_until_cap(tmp_path):
    # newest-first: v5, v4, v3, v2, v1 ; head=v5 ; cap=3
    entries = [_mk_entry(ms) for ms in (5, 4, 3, 2, 1)]
    data_root = _seed(tmp_path, 1, entries, "v_5_x")
    deleted = cull_versions(data_root, 1, 3)
    assert set(deleted) == {"v_1_x", "v_2_x"}                 # 2 oldest unpinned
    remaining = [v["versionId"] for v in read_refs(data_root, 1)["versions"]]
    assert remaining == ["v_5_x", "v_4_x", "v_3_x"]
    assert not os.path.exists(version_dir(data_root, 1, "v_1_x"))   # dir removed


def test_cull_never_deletes_pinned_or_head(tmp_path):
    # cap=2 but v1 pinned and v5 is head -> can only delete v2,v3,v4 down to cap,
    # protecting pinned v1 and head v5 even though that leaves 3 > cap.
    entries = [_mk_entry(5), _mk_entry(4), _mk_entry(3), _mk_entry(2), _mk_entry(1, pinned=True)]
    data_root = _seed(tmp_path, 1, entries, "v_5_x")
    cull_versions(data_root, 1, 2)
    remaining = [v["versionId"] for v in read_refs(data_root, 1)["versions"]]
    assert "v_1_x" in remaining and "v_5_x" in remaining       # protected survive
    assert "v_2_x" not in remaining and "v_3_x" not in remaining and "v_4_x" not in remaining


def test_cull_noop_under_cap(tmp_path):
    entries = [_mk_entry(2), _mk_entry(1)]
    data_root = _seed(tmp_path, 1, entries, "v_2_x")
    assert cull_versions(data_root, 1, 5) == []


def test_do_backup_enforces_keep_count_end_to_end(tmp_path):
    from savemanager.versioning import do_backup, list_versions
    from tests.fixtures import make_steam_tree
    steam_root, acct, app = make_steam_tree(tmp_path)
    data_root = os.path.join(str(tmp_path), "data")
    gi = {"appId": app, "name": "X"}
    save1 = os.path.join(steam_root, "userdata", str(acct), str(app), "remote", "save1.sav")
    for i in range(6):
        with open(save1, "w") as f:
            f.write("X" * (i + 1))     # different LENGTH each time -> always a fresh version
        do_backup(data_root, steam_root, acct, gi, now_ms=1000 + i, rand_hex=f"r{i}", keep_count=3)
    assert len(list_versions(data_root, app)["versions"]) == 3      # capped through do_backup
