# defaults/py_modules/savemanager/discovery.py
import os
import re

from .vdf import RcfEntry, parse_remotecache


def get_account_ids(steam_root: str) -> list[int]:
    ud = os.path.join(steam_root, "userdata")
    try:
        names = os.listdir(ud)
    except OSError:
        return []
    return [int(n) for n in names if n.isdigit() and n != "0"]


def remotecache_path(steam_root: str, account_id: int, app_id: int) -> str:
    return os.path.join(
        steam_root, "userdata", str(account_id), str(app_id), "remotecache.vdf"
    )


def read_entries(steam_root: str, account_id: int, app_id: int) -> list[RcfEntry]:
    path = remotecache_path(steam_root, account_id, app_id)
    try:
        with open(path) as f:
            return parse_remotecache(f.read())
    except OSError:
        return []


# Proton prefix subdirs tried for Windows games, in priority order.
_PROTON_SUBDIRS = [
    "Documents",
    os.path.join("AppData", "Local"),
    "Saved Games",
    os.path.join("Documents", "Steam Cloud"),
    os.path.join("AppData", "LocalLow"),
]


def parse_installdir(steam_root: str, app_id: int):
    path = os.path.join(steam_root, "steamapps", f"appmanifest_{app_id}.acf")
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        return None
    m = re.search(r'"installdir"\s+"(.*?)"', text)
    return m.group(1) if m else None


def parse_appname(steam_root: str, app_id: int):
    """Display name from the game's appmanifest (main library), or None."""
    path = os.path.join(steam_root, "steamapps", f"appmanifest_{app_id}.acf")
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        return None
    m = re.search(r'"name"\s+"(.*?)"', text)
    return m.group(1) if m else None


def list_cloud_app_ids(steam_root: str, account_id: int) -> list:
    """Every app_id under userdata/<account>/ that has a remotecache.vdf (i.e. uses Steam Cloud)."""
    ud = os.path.join(steam_root, "userdata", str(account_id))
    try:
        names = os.listdir(ud)
    except OSError:
        return []
    out = []
    for n in names:
        if n.isdigit() and os.path.isfile(os.path.join(ud, n, "remotecache.vdf")):
            out.append(int(n))
    return sorted(out)


def rcf_is_valid(root_dir: str, entries: list) -> bool:
    return any(os.path.isfile(os.path.join(root_dir, e.path)) for e in entries)


def _get_library_paths(steam_root) -> list:
    """All Steam library roots: the main steam dir plus any in libraryfolders.vdf.
    Each returned path has a steamapps/ subdir (compatdata/common live under it).
    Deduped by realpath so a symlinked steam_root isn't counted twice."""
    libs = [steam_root]
    seen = {os.path.realpath(steam_root)}
    try:
        with open(os.path.join(steam_root, "steamapps", "libraryfolders.vdf")) as f:
            text = f.read()
    except OSError:
        return libs
    for m in re.finditer(r'"path"\s+"(.+?)"', text):
        path = m.group(1).replace("\\\\", "\\")             # vdf escapes backslashes
        real = os.path.realpath(path)
        if real not in seen:
            seen.add(real)
            libs.append(path)
    return libs


def _candidate_roots(steam_root, account_id, app_id, installdir, libs):
    ud = os.path.join(steam_root, "userdata", str(account_id), str(app_id))
    roots = [os.path.join(ud, "remote")]
    for lib in libs:
        if installdir:
            roots.append(os.path.join(lib, "steamapps", "common", installdir))
        pfx = os.path.join(lib, "steamapps", "compatdata", str(app_id),
                           "pfx", "drive_c", "users", "steamuser")
        roots.extend(os.path.join(pfx, sub) for sub in _PROTON_SUBDIRS)
    return roots


def _autocloud_search_dirs(app_id, installdir, libs):
    dirs = []
    for lib in libs:
        if installdir:
            dirs.append(os.path.join(lib, "steamapps", "common", installdir))
        dirs.append(os.path.join(lib, "steamapps", "compatdata", str(app_id),
                                 "pfx", "drive_c", "users", "steamuser"))
    return dirs


def _find_autocloud_roots(app_id, installdir, libs, entries) -> list:
    """Last resort: find steam_autocloud.vdf markers under the game's install/Proton trees;
    the save root is the marker's directory or whichever ancestor (up to 3 levels) makes the
    rcf paths resolve. Covers 'lazy' Auto-Cloud games with non-standard save folders.
    Saves in a SUBdirectory of the marker are not searched (Steam puts the marker at the cloud root)."""
    from pathlib import Path
    found = []
    for base in _autocloud_search_dirs(app_id, installdir, libs):
        p = Path(base)
        if not p.is_dir():
            continue
        for marker in p.rglob("steam_autocloud.vdf"):
            d = marker.parent
            for _ in range(4):                  # the marker dir, then up to 3 parents
                ds = str(d)
                if rcf_is_valid(ds, entries):
                    if ds not in found:
                        found.append(ds)
                    break                        # stop at the FIRST (lowest) valid dir, always
                if d.parent == d:
                    break
                d = d.parent
    return found


def resolve_save_roots(steam_root, account_id, app_id, entries, installdir) -> dict:
    """Return {absDir: suffix} for every candidate root that holds >=1 listed file,
    searching every Steam library (so SD-card installs resolve)."""
    libs = _get_library_paths(steam_root)
    found = []
    for r in _candidate_roots(steam_root, account_id, app_id, installdir, libs):
        if rcf_is_valid(r, entries) and r not in found:
            found.append(r)
    if not found:
        found = _find_autocloud_roots(app_id, installdir, libs, entries)
    return {d: ("" if i == 0 else f"_{i}") for i, d in enumerate(found)}
