import os
from savemanager.discovery import read_entries, resolve_save_roots
from savemanager.store import create_snapshot, read_meta, delete_version, version_dir
from savemanager.vdf import RcfEntry
from tests.fixtures import make_steam_tree


def _setup(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path)
    entries = read_entries(steam_root, acct, app)
    roots = resolve_save_roots(steam_root, acct, app, entries, "XCOM 2")
    data_root = os.path.join(str(tmp_path), "data")
    return data_root, app, roots, entries


def test_create_snapshot_copies_files_and_writes_meta(tmp_path):
    data_root, app, roots, entries = _setup(tmp_path)
    meta = create_snapshot(
        data_root, app, roots, entries, "v_1_aa", 1718600000000,
        kind="manual", reason="manual", parent=None,
    )
    vdir = version_dir(data_root, app, "v_1_aa")
    assert os.path.isfile(os.path.join(vdir, "root", "save1.sav"))
    assert os.path.isfile(os.path.join(vdir, "root", "profile.bin"))
    assert meta["fileCount"] == 2
    assert meta["totalBytes"] == 9  # "AAAAA"(5) + "BBBB"(4)
    assert meta["parent"] is None
    assert {f["path"] for f in meta["files"]} == {"save1.sav", "profile.bin"}
    assert all(len(f["sha256"]) == 64 for f in meta["files"])


def test_read_meta_roundtrips(tmp_path):
    data_root, app, roots, entries = _setup(tmp_path)
    create_snapshot(data_root, app, roots, entries, "v_1_aa", 1718600000000,
                    kind="manual", reason="manual", parent=None)
    meta = read_meta(data_root, app, "v_1_aa")
    assert meta["versionId"] == "v_1_aa"


def test_delete_version_removes_dir(tmp_path):
    data_root, app, roots, entries = _setup(tmp_path)
    create_snapshot(data_root, app, roots, entries, "v_1_aa", 1718600000000,
                    kind="manual", reason="manual", parent=None)
    delete_version(data_root, app, "v_1_aa")
    assert not os.path.exists(version_dir(data_root, app, "v_1_aa"))


def test_create_snapshot_handles_subdirectory_paths(tmp_path):
    root = os.path.join(str(tmp_path), "saves")
    os.makedirs(os.path.join(root, "slots"))
    with open(os.path.join(root, "slots", "a.sav"), "w") as f:
        f.write("HI")
    entries = [RcfEntry(path="slots/a.sav", root=0, size=2, mtime=0)]
    data_root = os.path.join(str(tmp_path), "data")
    create_snapshot(data_root, 1, {root: ""}, entries, "v_1_a", 1,
                    kind="manual", reason="manual", parent=None)
    assert os.path.isfile(os.path.join(version_dir(data_root, 1, "v_1_a"),
                                       "root", "slots", "a.sav"))


def test_create_snapshot_skips_unsafe_paths(tmp_path):
    root = os.path.join(str(tmp_path), "saves")
    os.makedirs(root)
    with open(os.path.join(root, "ok.sav"), "w") as f:
        f.write("OK")
    entries = [
        RcfEntry(path="ok.sav", root=0, size=2, mtime=0),
        RcfEntry(path="../escape.sav", root=0, size=2, mtime=0),
        RcfEntry(path=os.path.join(str(tmp_path), "abs.sav"), root=0, size=2, mtime=0),
    ]
    data_root = os.path.join(str(tmp_path), "data")
    meta = create_snapshot(data_root, 1, {root: ""}, entries, "v_1_a", 1,
                           kind="manual", reason="manual", parent=None)
    assert {f["path"] for f in meta["files"]} == {"ok.sav"}
    assert not os.path.exists(os.path.join(str(tmp_path), "escape.sav"))


def test_create_snapshot_multi_root_suffixes(tmp_path):
    r0 = os.path.join(str(tmp_path), "r0"); os.makedirs(r0)
    r1 = os.path.join(str(tmp_path), "r1"); os.makedirs(r1)
    with open(os.path.join(r0, "s.sav"), "w") as f: f.write("A")
    with open(os.path.join(r1, "s.sav"), "w") as f: f.write("BB")
    entries = [RcfEntry(path="s.sav", root=0, size=1, mtime=0)]
    data_root = os.path.join(str(tmp_path), "data")
    create_snapshot(data_root, 1, {r0: "", r1: "_1"}, entries, "v_1_a", 1,
                    kind="manual", reason="manual", parent=None)
    vdir = version_dir(data_root, 1, "v_1_a")
    assert os.path.isfile(os.path.join(vdir, "root", "s.sav"))
    assert os.path.isfile(os.path.join(vdir, "root_1", "s.sav"))


def test_create_snapshot_blocks_traversal_when_source_exists(tmp_path):
    # The traversal target REALLY exists, so the isfile() check would pass.
    # Only _safe_rel prevents the escape -> this proves the guard, not isfile, blocks it.
    base = os.path.join(str(tmp_path), "base")
    root = os.path.join(base, "saves")
    os.makedirs(root)
    with open(os.path.join(base, "escape.sav"), "w") as f:  # one level above the root
        f.write("SECRET")
    with open(os.path.join(root, "ok.sav"), "w") as f:
        f.write("OK")
    entries = [
        RcfEntry(path="ok.sav", root=0, size=2, mtime=0),
        RcfEntry(path="../escape.sav", root=0, size=6, mtime=0),  # src resolves to base/escape.sav (exists)
    ]
    data_root = os.path.join(str(tmp_path), "data")
    meta = create_snapshot(data_root, 1, {root: ""}, entries, "v_1_a", 1,
                           kind="manual", reason="manual", parent=None)
    vdir = version_dir(data_root, 1, "v_1_a")
    assert {f["path"] for f in meta["files"]} == {"ok.sav"}        # traversal entry skipped
    assert not os.path.exists(os.path.join(vdir, "escape.sav"))    # nothing escaped the version dir
