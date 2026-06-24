# Save Manager — Discovery Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Widen which games' saves the plugin can locate — support games installed on a secondary Steam library (SD card) via `libraryfolders.vdf`, and "lazy" Auto-Cloud games whose saves sit in a non-standard subfolder marked by `steam_autocloud.vdf`.

**Architecture:** Pure additions to `discovery.py`'s `resolve_save_roots` pipeline. Today it tries a fixed set of candidate dirs under the MAIN Steam dir and keeps those that contain ≥1 `remotecache.vdf`-listed file. Task 1 makes the candidate dirs span EVERY Steam library (from `libraryfolders.vdf`), so SD-card installs resolve. Task 2 adds a last-resort fallback: scan the game's install/Proton trees for `steam_autocloud.vdf` and validate its directory (and a few parents) against the rcf list, catching saves in non-standard locations. All fixture-testable off-device.

**Tech Stack:** Python 3.11 (stdlib), pytest.

**Reference:** Spec §4.1 (discovery edge cases — SD-card/`/run` storage, autocloud heuristic). Inspiration: steamback's multi-library + `steam_autocloud.vdf` handling.

**Out of scope (smaller follow-ups):** resolving via the `root` enum first (the validate-all approach already covers the same dirs); caching resolved roots in `game.json` (perf — only the Task-2 `rglob` is costly, and only for the minority of autocloud games); the post-revert Steam-Cloud-conflict mtime bump.

---

## Existing code (in `defaults/py_modules/savemanager/discovery.py`)

```python
_PROTON_SUBDIRS = ["Documents", os.path.join("AppData", "Local"), "Saved Games",
                   os.path.join("Documents", "Steam Cloud"), os.path.join("AppData", "LocalLow")]

def rcf_is_valid(root_dir, entries) -> bool:
    return any(os.path.isfile(os.path.join(root_dir, e.path)) for e in entries)

def _candidate_roots(steam_root, account_id, app_id, installdir):
    ud = os.path.join(steam_root, "userdata", str(account_id), str(app_id))
    roots = [os.path.join(ud, "remote")]
    if installdir:
        roots.append(os.path.join(steam_root, "steamapps", "common", installdir))
    pfx = os.path.join(steam_root, "steamapps", "compatdata", str(app_id),
                       "pfx", "drive_c", "users", "steamuser")
    roots.extend(os.path.join(pfx, sub) for sub in _PROTON_SUBDIRS)
    return roots

def resolve_save_roots(steam_root, account_id, app_id, entries, installdir) -> dict:
    found = []
    for r in _candidate_roots(steam_root, account_id, app_id, installdir):
        if rcf_is_valid(r, entries) and r not in found:
            found.append(r)
    return {d: ("" if i == 0 else f"_{i}") for i, d in enumerate(found)}
```
`os` and `re` are imported at the top. `resolve_save_roots`'s output (`{absDir: suffix}`, suffixes `""`,`_1`,…) is consumed by the store/versioning layer — keep that shape.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `defaults/py_modules/savemanager/discovery.py` | Modify | `_get_library_paths`; `_candidate_roots` spans libraries; `_find_autocloud_roots` fallback; `resolve_save_roots` pipeline. |
| `tests/test_discovery_hardening.py` | Create | Multi-library + autocloud fixtures and assertions. |

---

## Task 1: Multi-library candidate roots (SD card)

**Files:** Modify `defaults/py_modules/savemanager/discovery.py`; Test `tests/test_discovery_hardening.py`.

- [ ] **Step 1: Write the failing test `tests/test_discovery_hardening.py`**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_discovery_hardening.py -v`
Expected: FAIL (`ImportError: cannot import name '_get_library_paths'`).

- [ ] **Step 3: Add `_get_library_paths` and make `_candidate_roots` span libraries.**

Add this function ABOVE `_candidate_roots`:
```python
def _get_library_paths(steam_root) -> list:
    """All Steam library roots: the main steam dir plus any in libraryfolders.vdf.
    Each returned path has a steamapps/ subdir (compatdata/common live under it)."""
    libs = [steam_root]
    try:
        with open(os.path.join(steam_root, "steamapps", "libraryfolders.vdf")) as f:
            text = f.read()
    except OSError:
        return libs
    for m in re.finditer(r'"path"\s+"(.+?)"', text):
        path = m.group(1).replace("\\\\", "\\")             # vdf escapes backslashes
        if path not in libs:
            libs.append(path)
    return libs
```

Replace `_candidate_roots` with a version that takes the library list and generates the install/Proton candidates under EACH library (the cloud `remote` dir stays under the main userdata):
```python
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
```

Replace `resolve_save_roots` (drop the stale M1 NOTE comment above it too) with one that resolves the libraries and passes them through:
```python
def resolve_save_roots(steam_root, account_id, app_id, entries, installdir) -> dict:
    """Return {absDir: suffix} for every candidate root that holds >=1 listed file,
    searching every Steam library (so SD-card installs resolve)."""
    libs = _get_library_paths(steam_root)
    found = []
    for r in _candidate_roots(steam_root, account_id, app_id, installdir, libs):
        if rcf_is_valid(r, entries) and r not in found:
            found.append(r)
    return {d: ("" if i == 0 else f"_{i}") for i, d in enumerate(found)}
```

- [ ] **Step 4: Run tests to verify they pass (and existing discovery tests still pass)**

Run: `python -m pytest tests/test_discovery_hardening.py tests/test_discovery_roots.py tests/test_discovery_basic.py -v`
Expected: PASS — the 3 new multi-library tests + the existing discovery tests (with no `libraryfolders.vdf`, `_get_library_paths` returns just `[steam_root]`, so behavior is unchanged).

- [ ] **Step 5: Commit**

```bash
git add defaults/py_modules/savemanager/discovery.py tests/test_discovery_hardening.py
git commit -m "feat: discovery spans all Steam libraries (SD-card installs) via libraryfolders.vdf"
```

---

## Task 2: `steam_autocloud.vdf` fallback (non-standard save locations)

**Files:** Modify `defaults/py_modules/savemanager/discovery.py`; Test `tests/test_discovery_hardening.py` (append).

- [ ] **Step 1: Append the failing test to `tests/test_discovery_hardening.py`**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_discovery_hardening.py::test_resolve_autocloud_fallback_when_standard_roots_miss -v`
Expected: FAIL (`resolve_save_roots` returns `{}` — the autocloud fallback doesn't exist yet).

- [ ] **Step 3: Add `_find_autocloud_roots` and wire it as the fallback.**

Add this function ABOVE `resolve_save_roots`:
```python
def _autocloud_search_dirs(steam_root, app_id, installdir, libs):
    dirs = []
    for lib in libs:
        if installdir:
            dirs.append(os.path.join(lib, "steamapps", "common", installdir))
        dirs.append(os.path.join(lib, "steamapps", "compatdata", str(app_id),
                                 "pfx", "drive_c", "users", "steamuser"))
    return dirs


def _find_autocloud_roots(steam_root, app_id, installdir, libs, entries) -> list:
    """Last resort: find steam_autocloud.vdf markers under the game's install/Proton trees;
    the save root is the marker's directory or whichever ancestor (up to 3 levels) makes the
    rcf paths resolve. Covers 'lazy' Auto-Cloud games with non-standard save folders."""
    from pathlib import Path
    found = []
    for base in _autocloud_search_dirs(steam_root, app_id, installdir, libs):
        p = Path(base)
        if not p.is_dir():
            continue
        for marker in p.rglob("steam_autocloud.vdf"):
            d = marker.parent
            for _ in range(4):                  # the marker dir, then up to 3 parents
                ds = str(d)
                if rcf_is_valid(ds, entries) and ds not in found:
                    found.append(ds)
                    break
                if d.parent == d:
                    break
                d = d.parent
    return found
```

Add the fallback to `resolve_save_roots` — insert the two lines right BEFORE its final `return`:
```python
    if not found:
        found = _find_autocloud_roots(steam_root, app_id, installdir, libs, entries)
    return {d: ("" if i == 0 else f"_{i}") for i, d in enumerate(found)}
```
(So `resolve_save_roots` becomes: resolve libs → validate candidate roots → if none, autocloud fallback → suffix map.)

- [ ] **Step 4: Run tests to verify they pass (full discovery suite)**

Run: `python -m pytest tests/test_discovery_hardening.py tests/test_discovery_roots.py tests/test_discovery_basic.py tests/test_versioning.py -v`
Expected: PASS — the new autocloud tests + the empty case + all existing discovery/versioning tests (the fallback only fires when the standard roots find nothing).

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add defaults/py_modules/savemanager/discovery.py tests/test_discovery_hardening.py
git commit -m "feat: steam_autocloud.vdf fallback for non-standard save locations"
```

---

## Self-review (done while writing)

- **Coverage:** SD-card / secondary-library installs ✓ (Task 1, via `libraryfolders.vdf`); non-standard Auto-Cloud save folders ✓ (Task 2, `steam_autocloud.vdf` marker + ancestor validation). Both validate against the actual `remotecache.vdf` file list (`rcf_is_valid`), so a wrong guess is never returned. Deferred (noted): `root`-enum-first resolution (validate-all already covers the same dirs), resolved-root caching (perf), Cloud-conflict mtime bump.
- **Backward-compat:** `_get_library_paths` returns `[steam_root]` when there's no `libraryfolders.vdf`, so single-library games (all existing tests + the M1 fixture) resolve exactly as before; the autocloud fallback only runs when the standard candidates find nothing. The `{absDir: suffix}` output shape is unchanged.
- **Type consistency:** `_candidate_roots` gains a `libs` param (all call sites — only `resolve_save_roots` — pass it); `_find_autocloud_roots`/`_autocloud_search_dirs` use the same `libs`, `entries`, and `rcf_is_valid` already in the module. `re`/`os` are imported.
- **No placeholders:** every step has complete code.

---

## After this — remaining roadmap

- `root`-enum-first resolution + resolved-root caching in `game.json`; the post-revert Steam-Cloud-conflict mtime bump; backend revert guard / `locking.py`; "pinned exceeds keep cap" warning; Drive minors (poll interval, appId in folder naming, resumable uploads).
```
