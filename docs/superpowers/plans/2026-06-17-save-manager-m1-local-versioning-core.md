# Save Manager — M1 Local Versioning Core — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the off-device-testable Python engine + a minimal Decky QAM panel that discovers Steam Cloud games, takes full-copy save snapshots on demand, and lists versions per game.

**Architecture:** Pure-Python engine under `defaults/py_modules/savemanager/` (vendored into the plugin, importable off-device for pytest), exposing discovery (parse `remotecache.vdf`, resolve save roots), a full-copy snapshot store, a `refs.json` version index, and an `Engine` facade. A thin `main.py` `Plugin` class wires the facade to Decky. A minimal React QAM panel lists supported games and versions and triggers a manual backup.

**Tech Stack:** Python 3.11 (stdlib only for the engine), pytest (off-device tests). Frontend: TypeScript, React, `@decky/api`, `@decky/ui`, Rollup, pnpm 9. Decky Loader plugin packaging.

**Reference:** Design spec `docs/superpowers/specs/2026-06-17-steam-deck-save-manager-design.md`. Inspiration: steamback's discovery engine (do NOT copy its removed `decky-frontend-lib` frontend).

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | pytest config (`pythonpath` so tests import the vendored package). |
| `plugin.json` | Decky manifest (`flags: []`, no root). |
| `requirements.txt` | Pure-Python runtime deps for the Docker builder (empty for M1; engine is stdlib-only). |
| `main.py` | Decky `Plugin` class; instantiates `Engine`, delegates async methods, supplies `now_ms`/`rand_hex`. |
| `defaults/py_modules/savemanager/__init__.py` | Package marker + version. |
| `defaults/py_modules/savemanager/vdf.py` | Valve KeyValues tokenizer + `parse_remotecache` → `list[RcfEntry]`. |
| `defaults/py_modules/savemanager/discovery.py` | Account ids, `remotecache.vdf` read, install dir, save-root resolution, validation. |
| `defaults/py_modules/savemanager/store.py` | Version ids, version dirs, atomic copy, `create_snapshot`/`read_meta`/`delete_version`. |
| `defaults/py_modules/savemanager/refs.py` | `refs.json` read/write (atomic + `.bak`), version-entry helper. |
| `defaults/py_modules/savemanager/versioning.py` | `do_backup`, `list_versions`, `is_supported`. |
| `defaults/py_modules/savemanager/api.py` | `Engine` facade (holds config + account ids; pure & testable). |
| `tests/test_*.py` | pytest unit tests for every engine module. |
| `tests/fixtures.py` | Helper to build a fake Steam tree + sample `remotecache.vdf`. |
| `src/index.tsx` | QAM panel: supported-games list, version list, "Back up now", lifetime hook (log only in M1). |
| `package.json`, `tsconfig.json`, `rollup.config.js` | Frontend build. |

**Data types (locked — use verbatim across tasks):**

- `RcfEntry` (dataclass): `path: str`, `root: int`, `size: int`, `mtime: int` (seconds from the `time` field).
- **`meta.json`** (per version, immutable): `{versionId, appId, createdAt, kind, reason, parent, saveRoots: {suffix: absDir}, files: [{suffix, path, size, mtime}], fileCount, totalBytes, schemaVersion}` (`mtime` here is **ms**).
- **`refs.json`** (mutable): `{appId, head: {versionId, detached}, pendingRevertTo, versions: [entry...], updatedAt, schemaVersion}`.
- **refs version entry:** `{versionId, createdAt, kind, reason, parent, pinned, name, fileCount, totalBytes}`.
- **`game_info`** (from frontend): `{appId: int, name: str}`.

---

## Task 1: Project scaffold + pytest

**Files:**
- Create: `pyproject.toml`, `plugin.json`, `requirements.txt`, `main.py`, `defaults/py_modules/savemanager/__init__.py`, `tests/__init__.py`, `tests/test_smoke.py`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[tool.pytest.ini_options]
pythonpath = [".", "defaults/py_modules"]
testpaths = ["tests"]
```

(`.` is on the path so `from tests.fixtures import ...` resolves; `defaults/py_modules` so `import savemanager` resolves.)

- [ ] **Step 2: Create `defaults/py_modules/savemanager/__init__.py`**

```python
"""Steam Deck Save Manager engine (pure-Python, off-device testable)."""

__version__ = "0.1.0"
```

- [ ] **Step 3: Create `plugin.json`**

```json
{
  "name": "SaveManager",
  "author": "amin",
  "flags": [],
  "publish": {
    "tags": ["save", "backup", "cloud"],
    "description": "Per-game save version manager with Google Drive backup.",
    "image": ""
  }
}
```

- [ ] **Step 4: Create `requirements.txt` (empty for M1)**

```text
# M1 engine is stdlib-only. Drive deps (requests, certifi) added in M4.
```

- [ ] **Step 5: Create `main.py` placeholder (not imported by tests)**

```python
"""Decky entry point. Wired in Task 9; kept minimal so tests never import decky."""
```

- [ ] **Step 6: Create `tests/__init__.py` (empty — makes `tests` an importable package)**

```python
```

- [ ] **Step 7: Write the smoke test `tests/test_smoke.py`**

```python
def test_package_imports():
    import savemanager
    assert savemanager.__version__ == "0.1.0"
```

- [ ] **Step 8: Run the smoke test to verify it passes**

Run: `pytest tests/test_smoke.py -v`
Expected: PASS (`test_package_imports`).

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml plugin.json requirements.txt main.py defaults/py_modules/savemanager/__init__.py tests/__init__.py tests/test_smoke.py
git commit -m "chore: scaffold save manager plugin + pytest"
```

---

## Task 2: remotecache.vdf parser

**Files:**
- Create: `defaults/py_modules/savemanager/vdf.py`
- Test: `tests/test_vdf.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vdf.py
from savemanager.vdf import parse_remotecache, RcfEntry

SAMPLE = '''"281990"
{
\t"ChangeNumber"\t\t"-6703994677807818784"
\t"ostype"\t\t"-184"
\t"my games/XCOM2/XComGame/SaveData/profile.bin"
\t{
\t\t"root"\t\t"2"
\t\t"size"\t\t"15741"
\t\t"localtime"\t\t"1671427173"
\t\t"time"\t\t"1671427172"
\t\t"sha"\t\t"df59d8d7b2f0c7ddd25e966493d61c1b107f9b7a"
\t}
\t"my games/XCOM2/XComGame/SaveData/save1.sav"
\t{
\t\t"root"\t\t"2"
\t\t"size"\t\t"1048576"
\t\t"time"\t\t"1671427180"
\t}
}
'''

def test_parses_two_file_entries_and_ignores_scalars():
    entries = parse_remotecache(SAMPLE)
    assert len(entries) == 2
    by_path = {e.path: e for e in entries}
    p = by_path["my games/XCOM2/XComGame/SaveData/profile.bin"]
    assert isinstance(p, RcfEntry)
    assert p.root == 2 and p.size == 15741 and p.mtime == 1671427172
    s = by_path["my games/XCOM2/XComGame/SaveData/save1.sav"]
    assert s.size == 1048576 and s.mtime == 1671427180

def test_empty_text_returns_empty_list():
    assert parse_remotecache("") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_vdf.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'savemanager.vdf'`).

- [ ] **Step 3: Implement `vdf.py`**

```python
# defaults/py_modules/savemanager/vdf.py
import re
from dataclasses import dataclass


@dataclass
class RcfEntry:
    path: str
    root: int
    size: int
    mtime: int  # seconds, from the "time" field


_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|\{|\}')


def _tokenize(text):
    for m in _TOKEN.finditer(text):
        tok = m.group(0)
        if tok == "{":
            yield ("open", None)
        elif tok == "}":
            yield ("close", None)
        else:
            yield ("str", m.group(1))


def _parse_block(tokens):
    """Parse key/value pairs until a 'close' (or EOF). Values are str or dict."""
    out = {}
    for kind, val in tokens:
        if kind == "close":
            return out
        key = val
        try:
            kind2, val2 = next(tokens)
        except StopIteration:
            break
        out[key] = _parse_block(tokens) if kind2 == "open" else val2
    return out


def parse_remotecache(text: str) -> list[RcfEntry]:
    """Parse a Valve remotecache.vdf into RcfEntry rows (one per synced file)."""
    root = _parse_block(_tokenize(text))
    entries: list[RcfEntry] = []
    for _appid, appblock in root.items():
        if not isinstance(appblock, dict):
            continue
        for key, val in appblock.items():
            if isinstance(val, dict) and ("size" in val or "root" in val):
                entries.append(
                    RcfEntry(
                        path=key,
                        root=int(val.get("root", 0)),
                        size=int(val.get("size", 0)),
                        mtime=int(val.get("time", 0)),
                    )
                )
    return entries
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_vdf.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add defaults/py_modules/savemanager/vdf.py tests/test_vdf.py
git commit -m "feat: parse remotecache.vdf into RcfEntry rows"
```

---

## Task 3: Fake-tree fixture + account ids + entry reading

**Files:**
- Create: `tests/fixtures.py`
- Create: `defaults/py_modules/savemanager/discovery.py`
- Test: `tests/test_discovery_basic.py`

- [ ] **Step 1: Create the fake-tree fixture helper `tests/fixtures.py`**

```python
# tests/fixtures.py
import os

REMOTECACHE = '''"281990"
{
\t"ChangeNumber"\t\t"1"
\t"save1.sav"
\t{
\t\t"root"\t\t"0"
\t\t"size"\t\t"5"
\t\t"time"\t\t"1671427180"
\t}
\t"profile.bin"
\t{
\t\t"root"\t\t"0"
\t\t"size"\t\t"4"
\t\t"time"\t\t"1671427181"
\t}
}
'''


def make_steam_tree(tmp_path, account_id=123, app_id=281990, with_saves=True):
    """Build a minimal fake Steam dir. Returns (steam_root, account_id, app_id)."""
    steam_root = os.path.join(str(tmp_path), "Steam")
    ud = os.path.join(steam_root, "userdata", str(account_id), str(app_id))
    os.makedirs(ud, exist_ok=True)
    with open(os.path.join(ud, "remotecache.vdf"), "w") as f:
        f.write(REMOTECACHE)
    if with_saves:
        remote = os.path.join(ud, "remote")
        os.makedirs(remote, exist_ok=True)
        with open(os.path.join(remote, "save1.sav"), "w") as f:
            f.write("AAAAA")
        with open(os.path.join(remote, "profile.bin"), "w") as f:
            f.write("BBBB")
    # appmanifest so parse_installdir works
    sa = os.path.join(steam_root, "steamapps")
    os.makedirs(sa, exist_ok=True)
    with open(os.path.join(sa, f"appmanifest_{app_id}.acf"), "w") as f:
        f.write('"AppState"\n{\n\t"installdir"\t\t"XCOM 2"\n}\n')
    return steam_root, account_id, app_id
```

- [ ] **Step 2: Write the failing test `tests/test_discovery_basic.py`**

```python
from savemanager.discovery import get_account_ids, remotecache_path, read_entries
from tests.fixtures import make_steam_tree


def test_get_account_ids_skips_zero(tmp_path):
    steam_root, acct, _ = make_steam_tree(tmp_path)
    import os
    os.makedirs(os.path.join(steam_root, "userdata", "0"), exist_ok=True)
    assert get_account_ids(steam_root) == [acct]


def test_read_entries_returns_parsed_rows(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path)
    entries = read_entries(steam_root, acct, app)
    assert {e.path for e in entries} == {"save1.sav", "profile.bin"}


def test_read_entries_missing_file_is_empty(tmp_path):
    steam_root, acct, _ = make_steam_tree(tmp_path)
    assert read_entries(steam_root, acct, 999999) == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_discovery_basic.py -v`
Expected: FAIL (`No module named 'savemanager.discovery'`).

- [ ] **Step 4: Implement the first slice of `discovery.py`**

```python
# defaults/py_modules/savemanager/discovery.py
import os

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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_discovery_basic.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures.py defaults/py_modules/savemanager/discovery.py tests/test_discovery_basic.py
git commit -m "feat: discovery account ids + remotecache reading"
```

---

## Task 4: Install dir + save-root resolution + validation

**Files:**
- Modify: `defaults/py_modules/savemanager/discovery.py`
- Test: `tests/test_discovery_roots.py`

- [ ] **Step 1: Write the failing test `tests/test_discovery_roots.py`**

```python
import os
from savemanager.discovery import (
    parse_installdir, rcf_is_valid, resolve_save_roots, read_entries,
)
from tests.fixtures import make_steam_tree


def test_parse_installdir(tmp_path):
    steam_root, _, app = make_steam_tree(tmp_path)
    assert parse_installdir(steam_root, app) == "XCOM 2"


def test_parse_installdir_missing(tmp_path):
    steam_root, _, _ = make_steam_tree(tmp_path)
    assert parse_installdir(steam_root, 999999) is None


def test_rcf_is_valid_true_when_a_file_exists(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path)
    entries = read_entries(steam_root, acct, app)
    remote = os.path.join(steam_root, "userdata", str(acct), str(app), "remote")
    assert rcf_is_valid(remote, entries) is True


def test_rcf_is_valid_false_for_empty_dir(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path)
    entries = read_entries(steam_root, acct, app)
    empty = os.path.join(str(tmp_path), "empty")
    os.makedirs(empty)
    assert rcf_is_valid(empty, entries) is False


def test_resolve_save_roots_finds_remote_with_suffix(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path)
    entries = read_entries(steam_root, acct, app)
    roots = resolve_save_roots(steam_root, acct, app, entries, "XCOM 2")
    remote = os.path.join(steam_root, "userdata", str(acct), str(app), "remote")
    assert roots == {remote: ""}


def test_resolve_save_roots_empty_when_no_files(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path, with_saves=False)
    entries = read_entries(steam_root, acct, app)
    assert resolve_save_roots(steam_root, acct, app, entries, "XCOM 2") == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_discovery_roots.py -v`
Expected: FAIL (`ImportError: cannot import name 'parse_installdir'`).

- [ ] **Step 3: Extend `discovery.py`**

```python
# append to defaults/py_modules/savemanager/discovery.py
import re

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


def rcf_is_valid(root_dir: str, entries: list) -> bool:
    return any(os.path.isfile(os.path.join(root_dir, e.path)) for e in entries)


def _candidate_roots(steam_root, account_id, app_id, installdir):
    ud = os.path.join(steam_root, "userdata", str(account_id), str(app_id))
    roots = [os.path.join(ud, "remote")]
    if installdir:
        roots.append(os.path.join(steam_root, "steamapps", "common", installdir))
    pfx = os.path.join(
        steam_root, "steamapps", "compatdata", str(app_id),
        "pfx", "drive_c", "users", "steamuser",
    )
    roots.extend(os.path.join(pfx, sub) for sub in _PROTON_SUBDIRS)
    return roots


def resolve_save_roots(steam_root, account_id, app_id, entries, installdir) -> dict:
    """Return {absDir: suffix} for every candidate root that holds >=1 listed file."""
    found = []
    for r in _candidate_roots(steam_root, account_id, app_id, installdir):
        if rcf_is_valid(r, entries) and r not in found:
            found.append(r)
    return {d: ("" if i == 0 else f"_{i}") for i, d in enumerate(found)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_discovery_roots.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add defaults/py_modules/savemanager/discovery.py tests/test_discovery_roots.py
git commit -m "feat: resolve + validate save roots via root candidates"
```

---

## Task 5: Store primitives — version ids, dirs, atomic copy

**Files:**
- Create: `defaults/py_modules/savemanager/store.py`
- Test: `tests/test_store_basics.py`

- [ ] **Step 1: Write the failing test `tests/test_store_basics.py`**

```python
import os
from savemanager.store import new_version_id, game_dir, version_dir, atomic_copy


def test_new_version_id_format():
    assert new_version_id(1718600000000, "a1b2c3") == "v_1718600000000_a1b2c3"


def test_dir_helpers():
    assert game_dir("/data", 281990).endswith(os.path.join("games", "281990"))
    assert version_dir("/data", 281990, "v_1_x").endswith(
        os.path.join("games", "281990", "versions", "v_1_x")
    )


def test_atomic_copy_copies_content_and_creates_dirs(tmp_path):
    src = os.path.join(str(tmp_path), "src.bin")
    with open(src, "wb") as f:
        f.write(b"\x00\x01\x02hello")
    dst = os.path.join(str(tmp_path), "nested", "deep", "out.bin")
    atomic_copy(src, dst)
    with open(dst, "rb") as f:
        assert f.read() == b"\x00\x01\x02hello"
    assert not os.path.exists(dst + ".tmp")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_store_basics.py -v`
Expected: FAIL (`No module named 'savemanager.store'`).

- [ ] **Step 3: Implement the store primitives in `store.py`**

```python
# defaults/py_modules/savemanager/store.py
import os
import shutil


def game_dir(data_root: str, app_id: int) -> str:
    return os.path.join(data_root, "games", str(app_id))


def version_dir(data_root: str, app_id: int, version_id: str) -> str:
    return os.path.join(game_dir(data_root, app_id), "versions", version_id)


def new_version_id(now_ms: int, rand_hex: str) -> str:
    return f"v_{now_ms}_{rand_hex}"


def atomic_copy(src: str, dst: str) -> None:
    """Copy src -> dst (preserving mtime) atomically, creating parent dirs."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".tmp"
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_store_basics.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add defaults/py_modules/savemanager/store.py tests/test_store_basics.py
git commit -m "feat: store primitives (version ids, dirs, atomic copy)"
```

---

## Task 6: Store — create_snapshot / read_meta / delete_version

**Files:**
- Modify: `defaults/py_modules/savemanager/store.py`
- Test: `tests/test_store_snapshot.py`

- [ ] **Step 1: Write the failing test `tests/test_store_snapshot.py`**

```python
import os
from savemanager.discovery import read_entries, resolve_save_roots
from savemanager.store import create_snapshot, read_meta, delete_version, version_dir
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_store_snapshot.py -v`
Expected: FAIL (`ImportError: cannot import name 'create_snapshot'`).

- [ ] **Step 3: Extend `store.py`**

```python
# append to defaults/py_modules/savemanager/store.py
import json


def create_snapshot(data_root, app_id, save_roots, entries, version_id,
                    created_at, kind, reason, parent) -> dict:
    """Full-copy every listed file from each save root into the version dir.

    save_roots: {absDir: suffix}. Files land under version_dir/root<suffix>/<path>.
    Returns the meta dict (also written as meta.json).
    """
    vdir = version_dir(data_root, app_id, version_id)
    os.makedirs(vdir, exist_ok=True)
    files = []
    total = 0
    for absdir, suffix in save_roots.items():
        for e in entries:
            src = os.path.join(absdir, e.path)
            if not os.path.isfile(src):
                continue
            dst = os.path.join(vdir, f"root{suffix}", e.path)
            atomic_copy(src, dst)
            st = os.stat(dst)
            files.append({
                "suffix": suffix, "path": e.path,
                "size": st.st_size, "mtime": int(st.st_mtime * 1000),
            })
            total += st.st_size
    meta = {
        "versionId": version_id, "appId": app_id, "createdAt": created_at,
        "kind": kind, "reason": reason, "parent": parent,
        "saveRoots": {suffix: absdir for absdir, suffix in save_roots.items()},
        "files": files, "fileCount": len(files), "totalBytes": total,
        "schemaVersion": 1,
    }
    tmp = os.path.join(vdir, "meta.json.tmp")
    with open(tmp, "w") as f:
        json.dump(meta, f, indent=1)
    os.replace(tmp, os.path.join(vdir, "meta.json"))
    return meta


def read_meta(data_root, app_id, version_id) -> dict:
    with open(os.path.join(version_dir(data_root, app_id, version_id), "meta.json")) as f:
        return json.load(f)


def delete_version(data_root, app_id, version_id) -> None:
    shutil.rmtree(version_dir(data_root, app_id, version_id), ignore_errors=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_store_snapshot.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add defaults/py_modules/savemanager/store.py tests/test_store_snapshot.py
git commit -m "feat: full-copy snapshot create/read/delete"
```

---

## Task 7: refs.json read/write + version-entry helper

**Files:**
- Create: `defaults/py_modules/savemanager/refs.py`
- Test: `tests/test_refs.py`

- [ ] **Step 1: Write the failing test `tests/test_refs.py`**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_refs.py -v`
Expected: FAIL (`No module named 'savemanager.refs'`).

- [ ] **Step 3: Implement `refs.py`**

```python
# defaults/py_modules/savemanager/refs.py
import json
import os

from .store import game_dir


def refs_path(data_root, app_id) -> str:
    return os.path.join(game_dir(data_root, app_id), "refs.json")


def read_refs(data_root, app_id) -> dict:
    try:
        with open(refs_path(data_root, app_id)) as f:
            return json.load(f)
    except OSError:
        return {
            "appId": app_id,
            "head": {"versionId": None, "detached": False},
            "pendingRevertTo": None,
            "versions": [],
            "updatedAt": 0,
            "schemaVersion": 1,
        }


def write_refs(data_root, app_id, refs: dict) -> None:
    path = refs_path(data_root, app_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.isfile(path):
        try:
            os.replace(path, path + ".bak")
        except OSError:
            pass
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(refs, f, indent=1)
    os.replace(tmp, path)


def make_version_entry(meta: dict) -> dict:
    return {
        "versionId": meta["versionId"],
        "createdAt": meta["createdAt"],
        "kind": meta["kind"],
        "reason": meta["reason"],
        "parent": meta["parent"],
        "pinned": False,
        "name": None,
        "fileCount": meta["fileCount"],
        "totalBytes": meta["totalBytes"],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_refs.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add defaults/py_modules/savemanager/refs.py tests/test_refs.py
git commit -m "feat: refs.json read/write + version entry helper"
```

---

## Task 8: versioning — do_backup / list_versions / is_supported

**Files:**
- Create: `defaults/py_modules/savemanager/versioning.py`
- Test: `tests/test_versioning.py`

- [ ] **Step 1: Write the failing test `tests/test_versioning.py`**

```python
import os
from savemanager.versioning import do_backup, list_versions, is_supported
from savemanager.discovery import parse_installdir
from tests.fixtures import make_steam_tree


def _args(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path)
    data_root = os.path.join(str(tmp_path), "data")
    return data_root, steam_root, acct, app


def test_is_supported_true_for_cloud_game(tmp_path):
    _, steam_root, acct, app = _args(tmp_path)
    assert is_supported(steam_root, acct, app, parse_installdir(steam_root, app)) is True


def test_is_supported_false_when_no_remotecache(tmp_path):
    _, steam_root, acct, _ = _args(tmp_path)
    assert is_supported(steam_root, acct, 999999, None) is False


def test_do_backup_creates_version_and_sets_head(tmp_path):
    data_root, steam_root, acct, app = _args(tmp_path)
    entry = do_backup(data_root, steam_root, acct, {"appId": app, "name": "XCOM 2"},
                      now_ms=1000, rand_hex="aaa")
    assert entry["versionId"] == "v_1000_aaa"
    listing = list_versions(data_root, app)
    assert listing["head"]["versionId"] == "v_1000_aaa"
    assert len(listing["versions"]) == 1


def test_do_backup_skips_when_unchanged(tmp_path):
    data_root, steam_root, acct, app = _args(tmp_path)
    do_backup(data_root, steam_root, acct, {"appId": app, "name": "X"},
              now_ms=1000, rand_hex="aaa")
    second = do_backup(data_root, steam_root, acct, {"appId": app, "name": "X"},
                       now_ms=2000, rand_hex="bbb")
    assert second is None
    assert len(list_versions(data_root, app)["versions"]) == 1


def test_do_backup_new_version_after_change_links_parent(tmp_path):
    data_root, steam_root, acct, app = _args(tmp_path)
    do_backup(data_root, steam_root, acct, {"appId": app, "name": "X"},
              now_ms=1000, rand_hex="aaa")
    # change a save file
    remote = os.path.join(steam_root, "userdata", str(acct), str(app), "remote")
    with open(os.path.join(remote, "save1.sav"), "w") as f:
        f.write("CHANGED")
    entry = do_backup(data_root, steam_root, acct, {"appId": app, "name": "X"},
                      now_ms=3000, rand_hex="ccc")
    assert entry["versionId"] == "v_3000_ccc"
    assert entry["parent"] == "v_1000_aaa"
    versions = list_versions(data_root, app)["versions"]
    assert [v["versionId"] for v in versions] == ["v_3000_ccc", "v_1000_aaa"]  # newest first
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_versioning.py -v`
Expected: FAIL (`No module named 'savemanager.versioning'`).

- [ ] **Step 3: Implement `versioning.py`**

```python
# defaults/py_modules/savemanager/versioning.py
import os

from .discovery import parse_installdir, read_entries, resolve_save_roots
from .refs import make_version_entry, read_refs, write_refs
from .store import create_snapshot, new_version_id, read_meta


def is_supported(steam_root, account_id, app_id, installdir) -> bool:
    entries = read_entries(steam_root, account_id, app_id)
    if not entries:
        return False
    return bool(resolve_save_roots(steam_root, account_id, app_id, entries, installdir))


def _live_fingerprint(save_roots, entries) -> dict:
    cur = {}
    for absdir, suffix in save_roots.items():
        for e in entries:
            src = os.path.join(absdir, e.path)
            if os.path.isfile(src):
                st = os.stat(src)
                cur[(suffix, e.path)] = (st.st_size, int(st.st_mtime * 1000))
    return cur


def _live_matches_head(data_root, app_id, refs, save_roots, entries) -> bool:
    head_id = refs["head"]["versionId"]
    if not head_id:
        return False
    try:
        meta = read_meta(data_root, app_id, head_id)
    except OSError:
        return False
    head = {(f["suffix"], f["path"]): (f["size"], f["mtime"]) for f in meta["files"]}
    return _live_fingerprint(save_roots, entries) == head


def do_backup(data_root, steam_root, account_id, game_info, now_ms, rand_hex,
              ignore_unchanged=True, kind="manual", reason="manual"):
    """Snapshot the current save. Returns the new refs version entry, or None."""
    app_id = game_info["appId"]
    installdir = parse_installdir(steam_root, app_id)
    entries = read_entries(steam_root, account_id, app_id)
    if not entries:
        return None
    save_roots = resolve_save_roots(steam_root, account_id, app_id, entries, installdir)
    if not save_roots:
        return None

    refs = read_refs(data_root, app_id)
    if ignore_unchanged and _live_matches_head(data_root, app_id, refs, save_roots, entries):
        return None

    version_id = new_version_id(now_ms, rand_hex)
    parent = refs["head"]["versionId"]
    meta = create_snapshot(data_root, app_id, save_roots, entries, version_id,
                           now_ms, kind=kind, reason=reason, parent=parent)
    entry = make_version_entry(meta)
    refs["versions"].insert(0, entry)
    refs["head"] = {"versionId": version_id, "detached": False}
    refs["updatedAt"] = now_ms
    write_refs(data_root, app_id, refs)
    return entry


def list_versions(data_root, app_id) -> dict:
    refs = read_refs(data_root, app_id)
    return {"head": refs["head"], "versions": refs["versions"]}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_versioning.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Run the full suite to confirm nothing regressed**

Run: `pytest -v`
Expected: PASS (all tests from Tasks 1–8).

- [ ] **Step 6: Commit**

```bash
git add defaults/py_modules/savemanager/versioning.py tests/test_versioning.py
git commit -m "feat: do_backup, list_versions, is_supported"
```

---

## Task 9: Engine facade + main.py wiring

**Files:**
- Create: `defaults/py_modules/savemanager/api.py`
- Modify: `main.py`
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write the failing test `tests/test_engine.py`**

```python
import os
from savemanager.api import Engine
from tests.fixtures import make_steam_tree


def _engine(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path)
    data_root = os.path.join(str(tmp_path), "data")
    eng = Engine(data_root, steam_root)
    eng.set_account_id(acct)
    return eng, app


def test_find_supported_filters_to_cloud_games(tmp_path):
    eng, app = _engine(tmp_path)
    result = eng.find_supported([{"appId": app, "name": "XCOM 2"},
                                 {"appId": 999999, "name": "Nope"}])
    assert result == [{"appId": app, "name": "XCOM 2"}]


def test_do_backup_then_get_versions(tmp_path):
    eng, app = _engine(tmp_path)
    entry = eng.do_backup({"appId": app, "name": "XCOM 2"}, now_ms=1000, rand_hex="aaa")
    assert entry["versionId"] == "v_1000_aaa"
    assert eng.get_versions(app)["head"]["versionId"] == "v_1000_aaa"


def test_set_account_id_is_idempotent(tmp_path):
    eng, _ = _engine(tmp_path)
    eng.set_account_id(123)
    assert eng.account_ids == [123]


def test_falls_back_to_discovered_account(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path)
    eng = Engine(os.path.join(str(tmp_path), "data"), steam_root)  # no set_account_id
    assert eng.find_supported([{"appId": app, "name": "X"}]) == [{"appId": app, "name": "X"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_engine.py -v`
Expected: FAIL (`No module named 'savemanager.api'`).

- [ ] **Step 3: Implement `api.py`**

```python
# defaults/py_modules/savemanager/api.py
from .discovery import get_account_ids, parse_installdir
from .versioning import do_backup, is_supported, list_versions


class Engine:
    """Pure, testable facade over the engine. main.py supplies time/randomness."""

    def __init__(self, data_root: str, steam_root: str):
        self.data_root = data_root
        self.steam_root = steam_root
        self.account_ids: list[int] = []

    def set_account_id(self, account_id: int) -> None:
        if account_id not in self.account_ids:
            self.account_ids.append(account_id)

    def _primary(self):
        if self.account_ids:
            return self.account_ids[0]
        discovered = get_account_ids(self.steam_root)
        return discovered[0] if discovered else None

    def find_supported(self, game_infos: list) -> list:
        acct = self._primary()
        if acct is None:
            return []
        out = []
        for g in game_infos:
            installdir = parse_installdir(self.steam_root, g["appId"])
            if is_supported(self.steam_root, acct, g["appId"], installdir):
                out.append(g)
        return out

    def do_backup(self, game_info: dict, now_ms: int, rand_hex: str):
        acct = self._primary()
        if acct is None:
            return None
        return do_backup(self.data_root, self.steam_root, acct, game_info, now_ms, rand_hex)

    def get_versions(self, app_id: int) -> dict:
        return list_versions(self.data_root, app_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_engine.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Wire `main.py` (manual verification — not imported by tests)**

```python
# main.py
import os
import time

import decky  # provided by Decky at runtime

from savemanager.api import Engine

_engine = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        data_root = os.environ["DECKY_PLUGIN_RUNTIME_DIR"]
        steam_root = os.path.join(os.path.expanduser("~"), ".local", "share", "Steam")
        _engine = Engine(data_root, steam_root)
    return _engine


def _now_ms() -> int:
    return int(round(time.time() * 1000))


def _rand_hex() -> str:
    return os.urandom(3).hex()


class Plugin:
    async def set_account_id(self, account_id: int):
        get_engine().set_account_id(account_id)
        return None

    async def find_supported(self, game_infos: list) -> list:
        return get_engine().find_supported(game_infos)

    async def do_backup(self, game_info: dict) -> dict:
        return get_engine().do_backup(game_info, _now_ms(), _rand_hex())

    async def get_versions(self, app_id: int) -> dict:
        return get_engine().get_versions(app_id)

    async def _main(self):
        decky.logger.info("SaveManager loaded")

    async def _unload(self):
        decky.logger.info("SaveManager unloaded")
```

- [ ] **Step 6: Commit**

```bash
git add defaults/py_modules/savemanager/api.py main.py tests/test_engine.py
git commit -m "feat: Engine facade + Decky main.py wiring"
```

---

## Task 10: Minimal QAM frontend (manual verification)

**Files:**
- Create: `package.json`, `tsconfig.json`, `rollup.config.js`, `src/index.tsx`

> No unit tests — Decky/React UI is verified on-device. Build must succeed and the panel must load.

- [ ] **Step 1: Create `package.json`**

```json
{
  "name": "save-manager",
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "build": "rollup -c",
    "watch": "rollup -c -w"
  },
  "devDependencies": {
    "@decky/rollup": "^1.0.2",
    "@decky/ui": "^4.11.0",
    "rollup": "^4.22.0",
    "typescript": "^5.6.0"
  },
  "dependencies": {
    "@decky/api": "^1.1.3",
    "react-icons": "^5.3.0"
  }
}
```

- [ ] **Step 2: Create `tsconfig.json`**

```json
{
  "compilerOptions": {
    "jsx": "react-jsx",
    "module": "ESNext",
    "target": "ES2020",
    "moduleResolution": "bundler",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "lib": ["DOM", "ES2020"]
  },
  "include": ["src"]
}
```

- [ ] **Step 3: Create `rollup.config.js`**

```javascript
import { defineConfig } from "@decky/rollup";

export default defineConfig({});
```

- [ ] **Step 4: Create `src/index.tsx`**

```tsx
import {
  callable,
  definePlugin,
} from "@decky/api";
import {
  ButtonItem,
  PanelSection,
  PanelSectionRow,
  staticClasses,
} from "@decky/ui";
import { useEffect, useState } from "react";
import { FaDownload } from "react-icons/fa";

interface GameInfo { appId: number; name: string; }
interface VersionEntry { versionId: string; createdAt: number; name: string | null; pinned: boolean; }
interface Listing { head: { versionId: string | null }; versions: VersionEntry[]; }

const setAccountId = callable<[number], null>("set_account_id");
const findSupported = callable<[GameInfo[]], GameInfo[]>("find_supported");
const doBackup = callable<[GameInfo], VersionEntry | null>("do_backup");
const getVersions = callable<[number], Listing>("get_versions");

// Minimal installed-games read from Steam UI internals (defensive).
function installedGames(): GameInfo[] {
  try {
    // @ts-ignore - Steam internal
    const folders = SteamClient.InstallFolder.GetInstallFolders();
    const out: GameInfo[] = [];
    // @ts-ignore
    for (const f of folders) for (const a of f.vecApps) {
      // @ts-ignore
      const ov = appStore.GetAppOverviewByGameID(a.nAppID);
      out.push({ appId: a.nAppID, name: ov?.display_name ?? String(a.nAppID) });
    }
    return out;
  } catch (e) {
    console.error("SaveManager: cannot list games", e);
    return [];
  }
}

function Content() {
  const [supported, setSupported] = useState<GameInfo[]>([]);
  const [selected, setSelected] = useState<GameInfo | null>(null);
  const [listing, setListing] = useState<Listing | null>(null);

  useEffect(() => { findSupported(installedGames()).then(setSupported).catch(console.error); }, []);

  const refresh = (g: GameInfo) => getVersions(g.appId).then(setListing).catch(console.error);

  return (
    <PanelSection title="Supported games">
      {supported.map((g) => (
        <PanelSectionRow key={g.appId}>
          <ButtonItem layout="below" onClick={() => { setSelected(g); refresh(g); }}>
            {g.name}
          </ButtonItem>
        </PanelSectionRow>
      ))}
      {selected && (
        <>
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={async () => { await doBackup(selected); refresh(selected); }}>
              Back up "{selected.name}" now
            </ButtonItem>
          </PanelSectionRow>
          {listing?.versions.map((v) => (
            <PanelSectionRow key={v.versionId}>
              {v.name ?? new Date(v.createdAt).toLocaleString()}
              {listing.head.versionId === v.versionId ? "  ●" : ""}
            </PanelSectionRow>
          ))}
        </>
      )}
    </PanelSection>
  );
}

export default definePlugin(() => {
  // Account id from the logged-in user (32-bit). Steam internal — defensive.
  try {
    // @ts-ignore
    const steam64 = BigInt(App.m_CurrentUser.strSteamID);
    const accountId = Number(steam64 & 0xffffffffn);
    setAccountId(accountId).catch(console.error);
  } catch (e) {
    console.error("SaveManager: cannot read account id", e);
  }

  // M1: register lifetime notifications but only LOG (auto-backup wired in M3).
  // @ts-ignore - Steam internal
  const hook = SteamClient.GameSessions.RegisterForAppLifetimeNotifications((n: any) => {
    console.log("SaveManager lifetime", n.unAppID, n.bRunning);
  });

  return {
    name: "Save Manager",
    title: <div className={staticClasses.Title}>Save Manager</div>,
    content: <Content />,
    icon: <FaDownload />,
    onDismount() { hook?.unregister?.(); },
  };
});
```

- [ ] **Step 5: Install deps and build**

Run: `pnpm install && pnpm build`
Expected: `dist/index.js` is produced with no TypeScript errors.

- [ ] **Step 6: On-device verification (manual)**

Deploy (rsync the plugin folder to `~/homebrew/plugins/SaveManager/` and `sudo systemctl restart plugin_loader.service`), open the Decky menu → Save Manager. Verify: supported games list appears; selecting one shows "Back up now"; tapping it creates a version that appears in the list with a `●` HEAD marker.

- [ ] **Step 7: Commit**

```bash
git add package.json tsconfig.json rollup.config.js src/index.tsx
git commit -m "feat: minimal QAM panel — list games, versions, manual backup"
```

---

## M1 self-review (done while writing — recorded for the executor)

- **Spec coverage (M1 slice):** discovery via `remotecache.vdf` ✓ (Tasks 2–4); full-copy snapshot store + `meta.json` ✓ (Tasks 5–6); `refs.json` + HEAD + version list ✓ (Tasks 7–8); manual backup ✓ (Task 8/9); supported-game detection ✓ (Task 8); QAM version list + manual backup button ✓ (Task 10). Deferred to later milestones by design: revert/pins/retention (M2), auto-on-exit + debounce (M3), Google Drive (M4).
- **Type consistency:** `RcfEntry` fields, `meta.json`/`refs.json` schemas, and the `do_backup` → `make_version_entry` → `list_versions` chain use identical names across Tasks 2–9.
- **No placeholders:** every code step is complete and runnable.

---

## Roadmap — M2–M4 (expand into full plans when reached)

These are intentionally **not** broken into bite-sized tasks yet (they build on M1 and may shift as M1 lands). Each becomes its own `docs/superpowers/plans/…` file via the writing-plans skill.

### M2 — Revert & curation
- `versioning.revert_to(data_root, steam_root, account_id, app_id, target_id, now_ms, rand_hex)`: pre-revert auto-snapshot if live ≠ HEAD (`reason="pre-revert-autosnapshot"`); set `pendingRevertTo`; materialize target into live roots (atomic copy + managed-file deletion); move HEAD (`detached = target != newest`); clear pending. Crash-resume on startup if `pendingRevertTo` set.
- Pin/unpin + rename (mutate `refs` entry only). Manual delete (not HEAD).
- Retention: count cap (per-game `keepCount`), pins counted-but-protected, HEAD protected, delete oldest non-pinned non-HEAD; "pins exceed cap" warning.
- Frontend: Restore/Pin/Rename/Delete row actions + `ConfirmModal`; `Router.RunningApps` guard; retention `SliderField`.
- Discovery hardening: `steam_autocloud.vdf` rglob + common-prefix fallback; SD-card system-dir search.

### M3 — Automation
- Per-game `autoBackupOnExit` setting via Decky `SettingsManager`.
- Frontend lifetime hook: on `bRunning==false` with toggle on, call `do_backup` (kind/reason = `auto`/`game-exit`) after an exit debounce (poll `remotecache.vdf` mtime settle ~2–5 s).
- Per-game toggle UI.

### M4 — Google Drive backup
- Vendor `requests` + `certifi`; `drive.py`: device-code OAuth (user's own client), token persistence/refresh, resumable upload, real-file mirror, remote `index.json`, prune-after-index, Drive-trash default.
- Non-blocking `loop.create_task` + `decky.emit` progress; QAM Drive section (link account / `user_code` + QR, sync now, progress); per-game `driveMirror` toggle.
- OAuth client setup documented in the spec appendix (§11).
```
