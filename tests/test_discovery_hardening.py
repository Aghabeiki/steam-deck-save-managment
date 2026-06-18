import os
from savemanager.discovery import resolve_save_roots, _get_library_paths
from savemanager.vdf import RcfEntry


def _write(path, content="X"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def test_get_library_paths_includes_steam_root_and_extra_libraries(tmp_path):
    steam_root = os.path.join(str(tmp_path), "Steam")
    sd = os.path.join(str(tmp_path), "sdcard")
    _write(os.path.join(steam_root, "steamapps", "libraryfolders.vdf"),
           '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n' % sd)
    libs = _get_library_paths(steam_root)
    assert steam_root in libs and sd in libs


def test_get_library_paths_no_file_returns_just_steam_root(tmp_path):
    steam_root = os.path.join(str(tmp_path), "Steam")
    os.makedirs(steam_root)
    assert _get_library_paths(steam_root) == [steam_root]


def test_resolve_finds_proton_save_on_secondary_library(tmp_path):
    steam_root = os.path.join(str(tmp_path), "Steam")
    sd = os.path.join(str(tmp_path), "sdcard")
    app = 700
    _write(os.path.join(steam_root, "steamapps", "libraryfolders.vdf"),
           '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n' % sd)
    docs = os.path.join(sd, "steamapps", "compatdata", str(app), "pfx", "drive_c",
                        "users", "steamuser", "Documents")
    _write(os.path.join(docs, "save.dat"))                  # the save lives on the SD library
    entries = [RcfEntry(path="save.dat", root=2, size=1, mtime=0)]
    assert resolve_save_roots(steam_root, 123, app, entries, installdir=None) == {docs: ""}


def test_resolve_autocloud_fallback_when_standard_roots_miss(tmp_path):
    # A native game whose saves sit under common/<installdir>/Cloud/Slots/, marked by a
    # steam_autocloud.vdf one level up. The standard candidates (common/<installdir>, Proton
    # subdirs) do NOT contain the rcf file, so only the autocloud fallback finds it.
    steam_root = os.path.join(str(tmp_path), "Steam")
    app = 800
    install = os.path.join(steam_root, "steamapps", "common", "MyGame")
    cloud = os.path.join(install, "Cloud")
    _write(os.path.join(cloud, "steam_autocloud.vdf"), '"autocloud"\n{\n}\n')
    _write(os.path.join(cloud, "Slots", "slot1.sav"))
    entries = [RcfEntry(path="Slots/slot1.sav", root=1, size=1, mtime=0)]
    # standard root common/MyGame would need MyGame/Slots/slot1.sav -> absent, so it misses
    roots = resolve_save_roots(steam_root, 123, app, entries, installdir="MyGame")
    assert roots == {cloud: ""}


def test_no_autocloud_and_no_match_returns_empty(tmp_path):
    steam_root = os.path.join(str(tmp_path), "Steam")
    os.makedirs(os.path.join(steam_root, "steamapps"))
    entries = [RcfEntry(path="nope.sav", root=1, size=1, mtime=0)]
    assert resolve_save_roots(steam_root, 123, 999, entries, installdir="X") == {}


def test_autocloud_walk_stops_at_lowest_valid_dir_no_phantom_ancestor(tmp_path):
    # Two markers in one tree + a stray file that also makes the ANCESTOR validate must NOT
    # produce a phantom second root — the walk stops at the lowest valid dir.
    steam_root = os.path.join(str(tmp_path), "Steam")
    app = 810
    cloud = os.path.join(steam_root, "steamapps", "common", "G", "Cloud")
    slots = os.path.join(cloud, "Slots")
    _write(os.path.join(slots, "steam_autocloud.vdf"))
    _write(os.path.join(slots, "Deep", "steam_autocloud.vdf"))     # a 2nd, deeper marker
    _write(os.path.join(slots, "x.sav"))
    _write(os.path.join(cloud, "x.sav"))                            # makes Cloud ALSO validate
    entries = [RcfEntry(path="x.sav", root=1, size=1, mtime=0)]
    assert resolve_save_roots(steam_root, 123, app, entries, installdir="G") == {slots: ""}


def test_autocloud_climbs_to_ancestor_when_marker_dir_lacks_files(tmp_path):
    # Marker sits in a meta/ subdir with no save files; the parent holds them -> climb to parent.
    steam_root = os.path.join(str(tmp_path), "Steam")
    app = 820
    saves = os.path.join(steam_root, "steamapps", "common", "G2", "Saves")
    _write(os.path.join(saves, "meta", "steam_autocloud.vdf"))
    _write(os.path.join(saves, "game.sav"))
    entries = [RcfEntry(path="game.sav", root=1, size=1, mtime=0)]
    assert resolve_save_roots(steam_root, 123, app, entries, installdir="G2") == {saves: ""}
