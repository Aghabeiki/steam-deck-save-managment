# Save Manager — M2 Revert & Curation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add git-like revert (movable HEAD + pre-revert auto-snapshot, crash-safe), version curation (pin/name/delete), and count-based retention to the M1 engine, plus the QAM UI to drive them.

**Architecture:** Extends the M1 pure-Python engine under `defaults/py_modules/savemanager/`. New: `config.py` (per-game `game.json` settings), `curation.py` (pin/name/delete refs mutations). Extended: `store.py` gains `restore_version`; `versioning.py` gains `cull_versions`, `revert_to`, `_materialize`, `_apply_pending`, `resume_pending_revert`, and `do_backup` gains retention. `api.Engine` + `main.py` expose the new ops; `src/index.tsx` gains per-version Restore/Pin/Rename/Delete actions, a retention slider, and a "game running" guard.

**Tech Stack:** Python 3.11 (stdlib only), pytest. Frontend: TypeScript/React, `@decky/api`, `@decky/ui`.

**Reference:** Spec `docs/superpowers/specs/2026-06-17-steam-deck-save-manager-design.md` (§5.2 revert, §5.3 pin/rename/delete, §5.4 retention, §8 UX). M1 plan: `docs/superpowers/plans/2026-06-17-save-manager-m1-local-versioning-core.md`.

**Out of scope (deferred):** discovery hardening (autocloud/SD-card — separate plan), auto-backup-on-exit (M3), Google Drive (M4).

---

## Existing M1 engine (do not re-implement — call these)

- `vdf.py`: `RcfEntry(path, root, size, mtime)`, `parse_remotecache`.
- `discovery.py`: `get_account_ids`, `read_entries`, `parse_installdir`, `resolve_save_roots(steam_root, account_id, app_id, entries, installdir) -> {absDir: suffix}`.
- `store.py`: `_safe_rel(rel)->bool`, `game_dir`, `version_dir`, `new_version_id(now_ms, rand_hex)`, `atomic_copy(src,dst)`, `create_snapshot(...) -> meta`, `read_meta(data_root, app_id, version_id) -> dict`, `delete_version(data_root, app_id, version_id)` (rmtree).
- `refs.py`: `read_refs`, `write_refs`, `make_version_entry(meta) -> entry`.
- `versioning.py`: `is_supported`, `_live_fingerprint(save_roots, entries)`, `_live_matches_head(data_root, app_id, refs, save_roots, entries)`, `do_backup(data_root, steam_root, account_id, game_info, now_ms, rand_hex, ignore_unchanged=True, kind="manual", reason="manual")`, `list_versions(data_root, app_id) -> {head, versions}`.
- `api.py`: `Engine(data_root, steam_root)` with `set_account_id`, `_primary`, `find_supported`, `do_backup(game_info, now_ms, rand_hex)`, `get_versions(app_id)`.

**Data shapes:** `meta.json` = `{versionId, appId, createdAt, kind, reason, parent, saveRoots:{suffix:absDir}, files:[{suffix,path,size,mtime}], fileCount, totalBytes, schemaVersion}`. `refs.json` = `{appId, head:{versionId, detached}, pendingRevertTo, versions:[entry...], updatedAt, schemaVersion}`. Version entry = `{versionId, createdAt, kind, reason, parent, pinned, name, fileCount, totalBytes}`. `save_roots` map is `{absDir: suffix}`; per-version files live under `version_dir/root<suffix>/<path>`.

---

## File Structure (M2)

| File | Change | Responsibility |
|---|---|---|
| `defaults/py_modules/savemanager/config.py` | Create | Per-game `game.json` settings (keepCount, etc.) with defaults. |
| `defaults/py_modules/savemanager/curation.py` | Create | Pin/unpin, rename, manual delete (refs mutations). |
| `defaults/py_modules/savemanager/store.py` | Modify | Add `restore_version` (copy a version's files back into live roots). |
| `defaults/py_modules/savemanager/versioning.py` | Modify | Add `cull_versions`, `revert_to`, `_materialize`, `_apply_pending`, `resume_pending_revert`; add retention to `do_backup`. |
| `defaults/py_modules/savemanager/api.py` | Modify | Engine: `revert`, `set_pinned`, `set_name`, `remove_version`, `get_settings`, `set_keep_count`; retention-aware `do_backup`; self-heal pending revert in `get_versions`. |
| `main.py` | Modify | New `Plugin` methods for the above. |
| `src/index.tsx` | Modify | Per-version Restore/Pin/Rename/Delete, retention slider, "game running" guard. |
| `tests/test_config.py`, `tests/test_curation.py`, `tests/test_retention.py`, `tests/test_revert.py`, `tests/test_engine_m2.py` | Create | Unit tests. |

---

## Task 1: Per-game config (`game.json`)

**Files:** Create `defaults/py_modules/savemanager/config.py`; Test `tests/test_config.py`.

- [ ] **Step 1: Write the failing test `tests/test_config.py`**

```python
from savemanager.config import get_game_settings, set_game_setting, DEFAULTS


def test_defaults_when_no_file(tmp_path):
    s = get_game_settings(str(tmp_path), 281990)
    assert s["keepCount"] == DEFAULTS["keepCount"] == 20


def test_set_then_get_roundtrip(tmp_path):
    merged = set_game_setting(str(tmp_path), 281990, "keepCount", 7)
    assert merged["keepCount"] == 7
    assert get_game_settings(str(tmp_path), 281990)["keepCount"] == 7
    # unspecified keys still fall back to defaults
    assert get_game_settings(str(tmp_path), 281990)["ignoreUnchanged"] is True


def test_corrupt_game_json_falls_back_to_defaults(tmp_path):
    import os
    from savemanager.store import game_dir
    os.makedirs(game_dir(str(tmp_path), 281990), exist_ok=True)
    with open(os.path.join(game_dir(str(tmp_path), 281990), "game.json"), "w") as f:
        f.write("not json")
    assert get_game_settings(str(tmp_path), 281990)["keepCount"] == 20
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL (`No module named 'savemanager.config'`).

- [ ] **Step 3: Implement `config.py`**

```python
# defaults/py_modules/savemanager/config.py
import json
import os

from .store import game_dir

DEFAULTS = {
    "keepCount": 20,
    "autoBackupOnExit": False,   # honored in M3
    "driveMirror": False,        # honored in M4
    "ignoreUnchanged": True,
}


def _game_json_path(data_root, app_id) -> str:
    return os.path.join(game_dir(data_root, app_id), "game.json")


def get_game_settings(data_root, app_id) -> dict:
    """Return per-game settings merged over DEFAULTS (corruption-tolerant)."""
    try:
        with open(_game_json_path(data_root, app_id)) as f:
            stored = json.load(f).get("settings", {})
    except (OSError, ValueError):
        stored = {}
    return {**DEFAULTS, **stored}


def set_game_setting(data_root, app_id, key, value) -> dict:
    """Persist one setting; return the merged settings dict."""
    path = _game_json_path(data_root, app_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data.setdefault("appId", app_id)
    data.setdefault("schemaVersion", 1)
    settings = data.get("settings", {})
    settings[key] = value
    data["settings"] = settings
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return {**DEFAULTS, **settings}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add defaults/py_modules/savemanager/config.py tests/test_config.py
git commit -m "feat: per-game game.json settings (keepCount + defaults)"
```

---

## Task 2: Retention (`cull_versions`) + wire into `do_backup`

**Files:** Modify `defaults/py_modules/savemanager/versioning.py`, `defaults/py_modules/savemanager/api.py`; Test `tests/test_retention.py`.

- [ ] **Step 1: Write the failing test `tests/test_retention.py`**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_retention.py -v`
Expected: FAIL (`ImportError: cannot import name 'cull_versions'`).

- [ ] **Step 3: Add `cull_versions` to `versioning.py`** (append at end) and wire retention into `do_backup`.

Append:
```python
def cull_versions(data_root, app_id, keep_count) -> list:
    """Delete oldest non-pinned, non-HEAD versions until total <= keep_count.
    Pinned and HEAD versions are protected (but still count toward the cap).
    Returns the list of deleted versionIds. refs.json is written before dirs are
    removed (refs is the commit point; orphan dirs are harmless)."""
    refs = read_refs(data_root, app_id)
    versions = refs["versions"]
    head_id = refs["head"]["versionId"]
    surplus = len(versions) - keep_count
    if surplus <= 0:
        return []
    # newest-first list -> oldest are at the end; reversed() gives oldest-first.
    deletable = [v for v in reversed(versions)
                 if not v["pinned"] and v["versionId"] != head_id]
    to_delete = {v["versionId"] for v in deletable[:surplus]}
    if not to_delete:
        return []
    refs["versions"] = [v for v in versions if v["versionId"] not in to_delete]
    write_refs(data_root, app_id, refs)
    for vid in to_delete:
        delete_version(data_root, app_id, vid)
    return list(to_delete)
```

Then change the `do_backup` signature line from:
```python
def do_backup(data_root, steam_root, account_id, game_info, now_ms, rand_hex,
              ignore_unchanged=True, kind="manual", reason="manual"):
```
to:
```python
def do_backup(data_root, steam_root, account_id, game_info, now_ms, rand_hex,
              ignore_unchanged=True, kind="manual", reason="manual", keep_count=20):
```
and immediately BEFORE the final `return entry` line in `do_backup`, insert:
```python
    cull_versions(data_root, app_id, keep_count)
```
(So `do_backup` writes the new version, then culls.)

`store.delete_version` is already imported? No — `versioning.py` imports `from .store import create_snapshot, new_version_id, read_meta, _safe_rel`. Add `delete_version`:
```python
from .store import create_snapshot, new_version_id, read_meta, _safe_rel, delete_version
```

- [ ] **Step 4: Make `Engine.do_backup` retention-aware** in `api.py`.

Add `get_game_settings` to the imports:
```python
from .config import get_game_settings
```
Replace `Engine.do_backup` with:
```python
    def do_backup(self, game_info: dict, now_ms: int, rand_hex: str):
        acct = self._primary()
        if acct is None:
            return None
        keep = get_game_settings(self.data_root, game_info["appId"])["keepCount"]
        return do_backup(self.data_root, self.steam_root, acct, game_info,
                         now_ms, rand_hex, keep_count=keep)
```

- [ ] **Step 5: Run tests to verify they pass (and M1 suite still green)**

Run: `python -m pytest tests/test_retention.py tests/test_versioning.py tests/test_engine.py -v`
Expected: PASS (new retention tests + unchanged M1 tests — the default `keep_count=20` makes cull a no-op for the small M1 cases).

- [ ] **Step 6: Commit**

```bash
git add defaults/py_modules/savemanager/versioning.py defaults/py_modules/savemanager/api.py tests/test_retention.py
git commit -m "feat: count-based retention (pins counted-but-protected) wired into do_backup"
```

---

## Task 3: Pin / rename (`curation.py`)

**Files:** Create `defaults/py_modules/savemanager/curation.py`; Test `tests/test_curation.py`.

- [ ] **Step 1: Write the failing test `tests/test_curation.py`**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_curation.py -v`
Expected: FAIL (`No module named 'savemanager.curation'`).

- [ ] **Step 3: Implement `curation.py`**

```python
# defaults/py_modules/savemanager/curation.py
from .refs import read_refs, write_refs
from .store import delete_version


def _find(refs, version_id):
    for v in refs["versions"]:
        if v["versionId"] == version_id:
            return v
    return None


def set_pinned(data_root, app_id, version_id, pinned) -> bool:
    refs = read_refs(data_root, app_id)
    v = _find(refs, version_id)
    if v is None:
        return False
    v["pinned"] = bool(pinned)
    write_refs(data_root, app_id, refs)
    return True


def set_name(data_root, app_id, version_id, name) -> bool:
    refs = read_refs(data_root, app_id)
    v = _find(refs, version_id)
    if v is None:
        return False
    v["name"] = name
    write_refs(data_root, app_id, refs)
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_curation.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add defaults/py_modules/savemanager/curation.py tests/test_curation.py
git commit -m "feat: pin/unpin + rename version (curation)"
```

---

## Task 4: Manual delete version

**Files:** Modify `defaults/py_modules/savemanager/curation.py`; Test `tests/test_curation.py`.

- [ ] **Step 1: Add the failing test to `tests/test_curation.py`** (append):

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_curation.py::test_remove_version_refuses_head -v`
Expected: FAIL (`ImportError: cannot import name 'remove_version'`).

- [ ] **Step 3: Add `remove_version` to `curation.py`** (append):

```python
def remove_version(data_root, app_id, version_id) -> bool:
    """Delete a non-HEAD version (entry + on-disk dir). Refuses to delete HEAD."""
    refs = read_refs(data_root, app_id)
    if refs["head"]["versionId"] == version_id:
        return False
    if _find(refs, version_id) is None:
        return False
    refs["versions"] = [v for v in refs["versions"] if v["versionId"] != version_id]
    write_refs(data_root, app_id, refs)
    delete_version(data_root, app_id, version_id)
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_curation.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add defaults/py_modules/savemanager/curation.py tests/test_curation.py
git commit -m "feat: manual delete version (refuses HEAD)"
```

---

## Task 5: `restore_version` (copy a version's files back into live roots)

**Files:** Modify `defaults/py_modules/savemanager/store.py`; Test `tests/test_revert.py`.

- [ ] **Step 1: Write the failing test `tests/test_revert.py`**

```python
import os
from savemanager.store import create_snapshot, restore_version, version_dir
from savemanager.vdf import RcfEntry


def test_restore_version_copies_files_into_live_roots(tmp_path):
    live = os.path.join(str(tmp_path), "live"); os.makedirs(live)
    with open(os.path.join(live, "s.sav"), "w") as f:
        f.write("ORIGINAL")
    entries = [RcfEntry(path="s.sav", root=0, size=8, mtime=0)]
    data_root = os.path.join(str(tmp_path), "data")
    create_snapshot(data_root, 1, {live: ""}, entries, "v_1_a", 1,
                    kind="manual", reason="manual", parent=None)
    # mutate the live file, then restore the snapshot over it
    with open(os.path.join(live, "s.sav"), "w") as f:
        f.write("CHANGED")
    restored = restore_version(data_root, 1, "v_1_a", {"": live})
    assert restored == {("", "s.sav")}
    with open(os.path.join(live, "s.sav")) as f:
        assert f.read() == "ORIGINAL"      # restored byte-for-byte


def test_restore_version_skips_suffix_with_no_current_root(tmp_path):
    live = os.path.join(str(tmp_path), "live"); os.makedirs(live)
    with open(os.path.join(live, "s.sav"), "w") as f:
        f.write("X")
    entries = [RcfEntry(path="s.sav", root=0, size=1, mtime=0)]
    data_root = os.path.join(str(tmp_path), "data")
    create_snapshot(data_root, 1, {live: "_1"}, entries, "v_1_a", 1,
                    kind="manual", reason="manual", parent=None)
    # current roots only know suffix "" -> the snapshot's "_1" files are skipped
    assert restore_version(data_root, 1, "v_1_a", {"": live}) == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_revert.py -v`
Expected: FAIL (`ImportError: cannot import name 'restore_version'`).

- [ ] **Step 3: Add `restore_version` to `store.py`** (append at end):

```python
def restore_version(data_root, app_id, version_id, suffix_to_root) -> set:
    """Copy a version's stored files back into the live save roots.

    suffix_to_root: {suffix: absDir} of the CURRENT live roots (invert save_roots).
    Returns the set of (suffix, path) actually restored.
    """
    meta = read_meta(data_root, app_id, version_id)
    vdir = version_dir(data_root, app_id, version_id)
    restored = set()
    for f in meta["files"]:
        suffix, rel = f["suffix"], f["path"]
        root = suffix_to_root.get(suffix)
        if root is None or not _safe_rel(rel):
            continue
        src = os.path.join(vdir, f"root{suffix}", rel)
        if not os.path.isfile(src):
            continue
        atomic_copy(src, os.path.join(root, rel))
        restored.add((suffix, rel))
    return restored
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_revert.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add defaults/py_modules/savemanager/store.py tests/test_revert.py
git commit -m "feat: restore_version — copy a version's files back to live roots"
```

---

## Task 6: `revert_to` (git-like, movable HEAD + pre-revert auto-snapshot)

**Files:** Modify `defaults/py_modules/savemanager/versioning.py`; Test `tests/test_revert.py`.

- [ ] **Step 1: Add the failing tests to `tests/test_revert.py`** (append):

```python
from savemanager.versioning import do_backup, revert_to, list_versions
from tests.fixtures import make_steam_tree


def _save_path(steam_root, acct, app, name):
    return os.path.join(steam_root, "userdata", str(acct), str(app), "remote", name)


def _args(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path)
    return os.path.join(str(tmp_path), "data2"), steam_root, acct, app


def test_revert_moves_head_and_restores_files(tmp_path):
    data_root, steam_root, acct, app = _args(tmp_path)
    gi = {"appId": app, "name": "X"}
    do_backup(data_root, steam_root, acct, gi, now_ms=1000, rand_hex="aaa")   # v1
    with open(_save_path(steam_root, acct, app, "save1.sav"), "w") as f:
        f.write("LATER")
    do_backup(data_root, steam_root, acct, gi, now_ms=2000, rand_hex="bbb")   # v2 (head)
    head = revert_to(data_root, steam_root, acct, app, "v_1000_aaa",
                     now_ms=3000, rand_hex="ccc")
    assert head == {"versionId": "v_1000_aaa", "detached": True}              # older than newest
    with open(_save_path(steam_root, acct, app, "save1.sav")) as f:
        assert f.read() == "AAAAA"          # v1 content restored
    # no auto-snapshot needed (live matched v2 before revert)
    assert {v["versionId"] for v in list_versions(data_root, app)["versions"]} == {"v_1000_aaa", "v_2000_bbb"}


def test_revert_forward_again_clears_detached(tmp_path):
    data_root, steam_root, acct, app = _args(tmp_path)
    gi = {"appId": app, "name": "X"}
    do_backup(data_root, steam_root, acct, gi, now_ms=1000, rand_hex="aaa")
    with open(_save_path(steam_root, acct, app, "save1.sav"), "w") as f:
        f.write("LATER")
    do_backup(data_root, steam_root, acct, gi, now_ms=2000, rand_hex="bbb")
    revert_to(data_root, steam_root, acct, app, "v_1000_aaa", now_ms=3000, rand_hex="ccc")
    head = revert_to(data_root, steam_root, acct, app, "v_2000_bbb", now_ms=4000, rand_hex="ddd")
    assert head == {"versionId": "v_2000_bbb", "detached": False}   # v2 is newest
    with open(_save_path(steam_root, acct, app, "save1.sav")) as f:
        assert f.read() == "LATER"


def test_revert_autosnapshots_unsaved_live_changes(tmp_path):
    data_root, steam_root, acct, app = _args(tmp_path)
    gi = {"appId": app, "name": "X"}
    do_backup(data_root, steam_root, acct, gi, now_ms=1000, rand_hex="aaa")   # v1 = "AAAAA"
    # play without backing up
    with open(_save_path(steam_root, acct, app, "save1.sav"), "w") as f:
        f.write("UNSAVED")
    revert_to(data_root, steam_root, acct, app, "v_1000_aaa", now_ms=2000, rand_hex="bbb")
    versions = list_versions(data_root, app)["versions"]
    autos = [v for v in versions if v["reason"] == "pre-revert-autosnapshot"]
    assert len(autos) == 1                                      # unsaved state preserved
    # and the live file is now v1
    with open(_save_path(steam_root, acct, app, "save1.sav")) as f:
        assert f.read() == "AAAAA"


def test_revert_deletes_managed_files_absent_from_target(tmp_path):
    data_root, steam_root, acct, app = _args(tmp_path)
    gi = {"appId": app, "name": "X"}
    do_backup(data_root, steam_root, acct, gi, now_ms=1000, rand_hex="aaa")   # v1 has save1+profile
    os.remove(_save_path(steam_root, acct, app, "profile.bin"))               # delete one save
    do_backup(data_root, steam_root, acct, gi, now_ms=2000, rand_hex="bbb")   # v2 has only save1
    revert_to(data_root, steam_root, acct, app, "v_1000_aaa", now_ms=3000, rand_hex="ccc")
    assert os.path.isfile(_save_path(steam_root, acct, app, "profile.bin"))   # restored by v1
    revert_to(data_root, steam_root, acct, app, "v_2000_bbb", now_ms=4000, rand_hex="ddd")
    assert not os.path.isfile(_save_path(steam_root, acct, app, "profile.bin"))  # managed + absent from v2 -> removed


def test_revert_returns_none_for_unknown_target(tmp_path):
    data_root, steam_root, acct, app = _args(tmp_path)
    do_backup(data_root, steam_root, acct, {"appId": app, "name": "X"}, now_ms=1000, rand_hex="aaa")
    assert revert_to(data_root, steam_root, acct, app, "v_nope", now_ms=2000, rand_hex="bbb") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_revert.py -v`
Expected: FAIL (`ImportError: cannot import name 'revert_to'`).

- [ ] **Step 3: Add the revert machinery to `versioning.py`** (append at end). Also add `make_version_entry` and `write_refs`/`read_refs` are already imported (they are: `from .refs import make_version_entry, read_refs, write_refs`). Add `restore_version` to the store import line:

```python
from .store import create_snapshot, new_version_id, read_meta, _safe_rel, delete_version, restore_version
```

Append:
```python
def _materialize(data_root, app_id, target_id, save_roots, entries, prev_head_id) -> None:
    """Make the live save dirs exactly equal target_id: restore target's files, then
    delete managed live files that target does not contain."""
    suffix_to_root = {suffix: absdir for absdir, suffix in save_roots.items()}
    restore_version(data_root, app_id, target_id, suffix_to_root)

    target_meta = read_meta(data_root, app_id, target_id)
    target_set = {(f["suffix"], f["path"]) for f in target_meta["files"]}

    # "managed" live files = every rcf-listed file (per suffix) + the previous HEAD's files.
    managed = set()
    for suffix in save_roots.values():
        for e in entries:
            if _safe_rel(e.path):
                managed.add((suffix, e.path))
    if prev_head_id:
        try:
            for f in read_meta(data_root, app_id, prev_head_id)["files"]:
                managed.add((f["suffix"], f["path"]))
        except (OSError, ValueError):
            pass

    for suffix, rel in managed - target_set:
        root = suffix_to_root.get(suffix)
        if root and _safe_rel(rel):
            p = os.path.join(root, rel)
            if os.path.isfile(p):
                os.remove(p)


def _apply_pending(data_root, steam_root, account_id, app_id):
    """Finish (or resume) a revert: materialize refs.pendingRevertTo, move HEAD, clear it.
    Idempotent — safe to re-run after a crash. Returns the new head dict or None."""
    refs = read_refs(data_root, app_id)
    target_id = refs.get("pendingRevertTo")
    if not target_id:
        return None
    installdir = parse_installdir(steam_root, app_id)
    entries = read_entries(steam_root, account_id, app_id)
    save_roots = resolve_save_roots(steam_root, account_id, app_id, entries, installdir)
    if not save_roots:
        return None
    _materialize(data_root, app_id, target_id, save_roots, entries, refs["head"]["versionId"])
    refs = read_refs(data_root, app_id)
    newest_id = refs["versions"][0]["versionId"] if refs["versions"] else None
    refs["head"] = {"versionId": target_id, "detached": target_id != newest_id}
    refs["pendingRevertTo"] = None
    write_refs(data_root, app_id, refs)
    return refs["head"]


def revert_to(data_root, steam_root, account_id, app_id, target_id, now_ms, rand_hex):
    """Git-like revert to target_id. Auto-snapshots unsaved live changes first, then
    moves HEAD and materializes the target into the live save dirs. Crash-safe via
    refs.pendingRevertTo. Returns the new head dict, or None if target/roots not found.
    NOTE: the caller MUST ensure the game is not running (frontend guards on Router.RunningApps)."""
    installdir = parse_installdir(steam_root, app_id)
    entries = read_entries(steam_root, account_id, app_id)
    save_roots = resolve_save_roots(steam_root, account_id, app_id, entries, installdir)
    if not save_roots:
        return None
    refs = read_refs(data_root, app_id)
    if not any(v["versionId"] == target_id for v in refs["versions"]):
        return None

    # Preserve unsaved live progress as a real, listed version before we overwrite it.
    if not _live_matches_head(data_root, app_id, refs, save_roots, entries):
        snap_id = new_version_id(now_ms, rand_hex)
        meta = create_snapshot(data_root, app_id, save_roots, entries, snap_id, now_ms,
                               kind="auto", reason="pre-revert-autosnapshot",
                               parent=refs["head"]["versionId"])
        refs["versions"].insert(0, make_version_entry(meta))
        refs["head"] = {"versionId": snap_id, "detached": False}

    refs["pendingRevertTo"] = target_id
    refs["updatedAt"] = now_ms
    write_refs(data_root, app_id, refs)
    return _apply_pending(data_root, steam_root, account_id, app_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_revert.py -v`
Expected: PASS (all revert tests).

- [ ] **Step 5: Commit**

```bash
git add defaults/py_modules/savemanager/versioning.py tests/test_revert.py
git commit -m "feat: git-like revert_to with pre-revert auto-snapshot + managed-file cleanup"
```

---

## Task 7: Crash-resume of an interrupted revert

**Files:** Modify `defaults/py_modules/savemanager/versioning.py`; Test `tests/test_revert.py`.

- [ ] **Step 1: Add the failing test to `tests/test_revert.py`** (append):

```python
from savemanager.versioning import resume_pending_revert
from savemanager.refs import read_refs, write_refs


def test_resume_pending_finishes_interrupted_revert(tmp_path):
    data_root, steam_root, acct, app = _args(tmp_path)
    gi = {"appId": app, "name": "X"}
    do_backup(data_root, steam_root, acct, gi, now_ms=1000, rand_hex="aaa")   # v1 = AAAAA
    with open(_save_path(steam_root, acct, app, "save1.sav"), "w") as f:
        f.write("LATER")
    do_backup(data_root, steam_root, acct, gi, now_ms=2000, rand_hex="bbb")   # v2 (head), live=LATER
    # Simulate a crash mid-revert: pendingRevertTo set, HEAD still v2, live still LATER.
    refs = read_refs(data_root, app)
    refs["pendingRevertTo"] = "v_1000_aaa"
    write_refs(data_root, app, refs)
    head = resume_pending_revert(data_root, steam_root, acct, app)
    assert head == {"versionId": "v_1000_aaa", "detached": True}
    assert read_refs(data_root, app)["pendingRevertTo"] is None
    with open(_save_path(steam_root, acct, app, "save1.sav")) as f:
        assert f.read() == "AAAAA"                              # target materialized


def test_resume_pending_noop_when_nothing_pending(tmp_path):
    data_root, steam_root, acct, app = _args(tmp_path)
    do_backup(data_root, steam_root, acct, {"appId": app, "name": "X"}, now_ms=1000, rand_hex="aaa")
    assert resume_pending_revert(data_root, steam_root, acct, app) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_revert.py::test_resume_pending_noop_when_nothing_pending -v`
Expected: FAIL (`ImportError: cannot import name 'resume_pending_revert'`).

- [ ] **Step 3: Add `resume_pending_revert` to `versioning.py`** (append) — it's just the public alias of `_apply_pending`:

```python
def resume_pending_revert(data_root, steam_root, account_id, app_id):
    """If a revert was interrupted (refs.pendingRevertTo set), finish it. No-op otherwise."""
    return _apply_pending(data_root, steam_root, account_id, app_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_revert.py -v`
Expected: PASS (all revert + resume tests).

- [ ] **Step 5: Commit**

```bash
git add defaults/py_modules/savemanager/versioning.py tests/test_revert.py
git commit -m "feat: resume an interrupted revert on demand (crash-safety)"
```

---

## Task 8: Engine facade + `main.py` wiring for M2 ops

**Files:** Modify `defaults/py_modules/savemanager/api.py`, `main.py`; Test `tests/test_engine_m2.py`.

- [ ] **Step 1: Write the failing test `tests/test_engine_m2.py`**

```python
import os
from savemanager.api import Engine
from tests.fixtures import make_steam_tree


def _engine(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path)
    eng = Engine(os.path.join(str(tmp_path), "data"), steam_root)
    eng.set_account_id(acct)
    return eng, steam_root, acct, app


def _save1(steam_root, acct, app):
    return os.path.join(steam_root, "userdata", str(acct), str(app), "remote", "save1.sav")


def test_engine_revert_round_trip(tmp_path):
    eng, steam_root, acct, app = _engine(tmp_path)
    gi = {"appId": app, "name": "X"}
    eng.do_backup(gi, now_ms=1000, rand_hex="aaa")
    with open(_save1(steam_root, acct, app), "w") as f:
        f.write("LATER")
    eng.do_backup(gi, now_ms=2000, rand_hex="bbb")
    head = eng.revert(gi, "v_1000_aaa", now_ms=3000, rand_hex="ccc")
    assert head["versionId"] == "v_1000_aaa"
    with open(_save1(steam_root, acct, app)) as f:
        assert f.read() == "AAAAA"


def test_engine_pin_rename_delete_and_settings(tmp_path):
    eng, steam_root, acct, app = _engine(tmp_path)
    gi = {"appId": app, "name": "X"}
    eng.do_backup(gi, now_ms=1000, rand_hex="aaa")
    assert eng.set_pinned(app, "v_1000_aaa", True) is True
    assert eng.set_name(app, "v_1000_aaa", "boss") is True
    v = eng.get_versions(app)["versions"][0]
    assert v["pinned"] is True and v["name"] == "boss"
    # settings
    assert eng.get_settings(app)["keepCount"] == 20
    assert eng.set_keep_count(app, 9)["keepCount"] == 9
    assert eng.get_settings(app)["keepCount"] == 9
    # cannot delete head
    assert eng.remove_version(app, "v_1000_aaa") is False


def test_engine_get_versions_resumes_pending_revert(tmp_path):
    from savemanager.refs import read_refs, write_refs
    eng, steam_root, acct, app = _engine(tmp_path)
    gi = {"appId": app, "name": "X"}
    eng.do_backup(gi, now_ms=1000, rand_hex="aaa")
    with open(_save1(steam_root, acct, app), "w") as f:
        f.write("LATER")
    eng.do_backup(gi, now_ms=2000, rand_hex="bbb")
    refs = read_refs(eng.data_root, app)
    refs["pendingRevertTo"] = "v_1000_aaa"
    write_refs(eng.data_root, app, refs)
    listing = eng.get_versions(app)          # should self-heal the interrupted revert
    assert listing["head"]["versionId"] == "v_1000_aaa"
    assert read_refs(eng.data_root, app)["pendingRevertTo"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_engine_m2.py -v`
Expected: FAIL (`AttributeError: 'Engine' object has no attribute 'revert'`).

- [ ] **Step 3: Extend `Engine` in `api.py`.**

Update imports at the top of `api.py`:
```python
from .config import get_game_settings, set_game_setting
from .curation import remove_version, set_name, set_pinned
from .discovery import get_account_ids, parse_installdir
from .versioning import do_backup, is_supported, list_versions, resume_pending_revert, revert_to
```

Add these methods to the `Engine` class (after `get_versions`):
```python
    def revert(self, game_info: dict, target_id: str, now_ms: int, rand_hex: str):
        acct = self._primary()
        if acct is None:
            return None
        return revert_to(self.data_root, self.steam_root, acct,
                         game_info["appId"], target_id, now_ms, rand_hex)

    def set_pinned(self, app_id: int, version_id: str, pinned: bool) -> bool:
        return set_pinned(self.data_root, app_id, version_id, pinned)

    def set_name(self, app_id: int, version_id: str, name) -> bool:
        return set_name(self.data_root, app_id, version_id, name)

    def remove_version(self, app_id: int, version_id: str) -> bool:
        return remove_version(self.data_root, app_id, version_id)

    def get_settings(self, app_id: int) -> dict:
        return get_game_settings(self.data_root, app_id)

    def set_keep_count(self, app_id: int, keep_count: int) -> dict:
        return set_game_setting(self.data_root, app_id, "keepCount", int(keep_count))
```

Replace `Engine.get_versions` so it self-heals an interrupted revert first:
```python
    def get_versions(self, app_id: int) -> dict:
        acct = self._primary()
        if acct is not None:
            resume_pending_revert(self.data_root, self.steam_root, acct, app_id)
        return list_versions(self.data_root, app_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_engine_m2.py tests/test_engine.py -v`
Expected: PASS (new M2 Engine tests + unchanged M1 Engine tests).

- [ ] **Step 5: Wire `main.py`** — add the new `Plugin` methods (after `get_versions`):

```python
    async def revert(self, game_info: dict, target_id: str):
        return get_engine().revert(game_info, target_id, _now_ms(), _rand_hex())

    async def set_pinned(self, app_id: int, version_id: str, pinned: bool):
        return get_engine().set_pinned(app_id, version_id, pinned)

    async def set_name(self, app_id: int, version_id: str, name: str):
        return get_engine().set_name(app_id, version_id, name)

    async def remove_version(self, app_id: int, version_id: str):
        return get_engine().remove_version(app_id, version_id)

    async def get_settings(self, app_id: int) -> dict:
        return get_engine().get_settings(app_id)

    async def set_keep_count(self, app_id: int, keep_count: int) -> dict:
        return get_engine().set_keep_count(app_id, keep_count)
```

Validate syntax: `python -m py_compile main.py && echo OK` (do NOT import it).

- [ ] **Step 6: Run the full suite + main.py compile**

Run: `python -m pytest -q && python -m py_compile main.py && echo OK`
Expected: all tests pass; "OK".

- [ ] **Step 7: Commit**

```bash
git add defaults/py_modules/savemanager/api.py main.py tests/test_engine_m2.py
git commit -m "feat: Engine + Plugin wiring for revert/pin/rename/delete/settings"
```

---

## Task 9: QAM frontend — restore / pin / rename / delete / retention

**Files:** Modify `src/index.tsx`. Verified by `tsc` + build (on-device verification is manual).

- [ ] **Step 1: Replace `src/index.tsx` with the M2 UI**

Replace the ENTIRE file with:
```tsx
import { callable, definePlugin } from "@decky/api";
import {
  ButtonItem,
  ConfirmModal,
  PanelSection,
  PanelSectionRow,
  Router,
  showModal,
  SliderField,
  TextField,
  staticClasses,
} from "@decky/ui";
import { useEffect, useState } from "react";
import { FaDownload } from "react-icons/fa";

interface GameInfo { appId: number; name: string; }
interface VersionEntry {
  versionId: string; createdAt: number; name: string | null; pinned: boolean; reason: string;
}
interface Listing { head: { versionId: string | null }; versions: VersionEntry[]; }
interface Settings { keepCount: number; }

const setAccountId = callable<[number], null>("set_account_id");
const findSupported = callable<[GameInfo[]], GameInfo[]>("find_supported");
const doBackup = callable<[GameInfo], VersionEntry | null>("do_backup");
const getVersions = callable<[number], Listing>("get_versions");
const revert = callable<[GameInfo, string], { versionId: string } | null>("revert");
const setPinned = callable<[number, string, boolean], boolean>("set_pinned");
const setName = callable<[number, string, string], boolean>("set_name");
const removeVersion = callable<[number, string], boolean>("remove_version");
const getSettings = callable<[number], Settings>("get_settings");
const setKeepCount = callable<[number, number], Settings>("set_keep_count");

function isRunning(appId: number): boolean {
  try {
    // @ts-ignore - Steam internal
    return (Router.RunningApps ?? []).some((a: any) => Number(a.appid) === appId);
  } catch {
    return false;
  }
}

function installedGames(): GameInfo[] {
  try {
    // @ts-ignore - Steam internal
    const folders = SteamClient.InstallFolder.GetInstallFolders();
    const out: GameInfo[] = [];
    // @ts-ignore
    for (const f of folders) for (const a of f.vecApps) {
      try {
        // @ts-ignore
        const ov = appStore.GetAppOverviewByGameID(a.nAppID);
        out.push({ appId: a.nAppID, name: ov?.display_name ?? String(a.nAppID) });
      } catch (e) {
        console.error("SaveManager: skipping app", a?.nAppID, e);
      }
    }
    return out;
  } catch (e) {
    console.error("SaveManager: cannot list games", e);
    return [];
  }
}

function RenameModal({ initial, onSave, closeModal }:
  { initial: string; onSave: (v: string) => void; closeModal?: () => void }) {
  const [value, setValue] = useState(initial);
  return (
    <ConfirmModal
      strTitle="Name this version"
      onOK={() => { onSave(value); closeModal?.(); }}
      onCancel={() => closeModal?.()}
    >
      <TextField value={value} onChange={(e) => setValue(e.target.value)} />
    </ConfirmModal>
  );
}

function Content() {
  const [supported, setSupported] = useState<GameInfo[]>([]);
  const [selected, setSelected] = useState<GameInfo | null>(null);
  const [listing, setListing] = useState<Listing | null>(null);
  const [keepCount, setKeep] = useState<number>(20);

  useEffect(() => { findSupported(installedGames()).then(setSupported).catch(console.error); }, []);

  const refresh = (g: GameInfo) => {
    getVersions(g.appId).then(setListing).catch(console.error);
    getSettings(g.appId).then((s) => setKeep(s.keepCount)).catch(console.error);
  };
  const open = (g: GameInfo) => { setSelected(g); refresh(g); };

  if (!selected) {
    return (
      <PanelSection title="Supported games">
        {supported.map((g) => (
          <PanelSectionRow key={g.appId}>
            <ButtonItem layout="below" onClick={() => open(g)}>{g.name}</ButtonItem>
          </PanelSectionRow>
        ))}
      </PanelSection>
    );
  }

  const running = isRunning(selected.appId);
  const head = listing?.head.versionId ?? null;

  return (
    <PanelSection title={selected.name}>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => { setSelected(null); setListing(null); }}>
          ← Back
        </ButtonItem>
      </PanelSectionRow>

      <PanelSectionRow>
        <ButtonItem layout="below" disabled={running}
          onClick={async () => { await doBackup(selected); refresh(selected); }}>
          {running ? "Stop the game to back up" : "Back up now"}
        </ButtonItem>
      </PanelSectionRow>

      <PanelSectionRow>
        <SliderField label="Keep last N" value={keepCount} min={5} max={100} step={5}
          showValue notchTicksVisible
          onChange={(v: number) => { setKeep(v); setKeepCount(selected.appId, v).catch(console.error); }} />
      </PanelSectionRow>

      {listing?.versions.map((v) => {
        const label = v.name ?? new Date(v.createdAt).toLocaleString();
        const isHead = v.versionId === head;
        return (
          <PanelSectionRow key={v.versionId}>
            <ButtonItem layout="below"
              label={`${v.pinned ? "★ " : ""}${label}${isHead ? "  ●" : ""}`}
              onClick={() => showModal(
                <ConfirmModal strTitle={`Restore "${label}"?`} bDestructiveWarning
                  strDescription="Your current save is snapshotted first, so you can revert this."
                  strOKButtonText={running ? "Game is running" : "Restore"}
                  onOK={async () => {
                    if (running) return;
                    await revert(selected, v.versionId); refresh(selected);
                  }} />
              )}>
              Restore
            </ButtonItem>
            <ButtonItem layout="below"
              onClick={async () => { await setPinned(selected.appId, v.versionId, !v.pinned); refresh(selected); }}>
              {v.pinned ? "Unpin" : "Pin"}
            </ButtonItem>
            <ButtonItem layout="below"
              onClick={() => showModal(
                <RenameModal initial={v.name ?? ""}
                  onSave={async (name) => { await setName(selected.appId, v.versionId, name); refresh(selected); }} />
              )}>
              Rename
            </ButtonItem>
            {!isHead && (
              <ButtonItem layout="below"
                onClick={() => showModal(
                  <ConfirmModal strTitle={`Delete "${label}"?`} bDestructiveWarning
                    onOK={async () => { await removeVersion(selected.appId, v.versionId); refresh(selected); }} />
                )}>
                Delete
              </ButtonItem>
            )}
          </PanelSectionRow>
        );
      })}
    </PanelSection>
  );
}

export default definePlugin(() => {
  try {
    // @ts-ignore
    const steam64 = BigInt(App.m_CurrentUser.strSteamID);
    setAccountId(Number(steam64 & 0xffffffffn)).catch(console.error);
  } catch (e) {
    console.error("SaveManager: cannot read account id", e);
  }
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

- [ ] **Step 2: Type-check**

Run: `pnpm exec tsc --noEmit`
Expected: No errors. (If `SliderField`/`ConfirmModal` prop types differ in the installed `@decky/ui`, adjust prop names to match the package's `.d.ts` — do not add `@ts-ignore` to hide a real type error unless it's a known-untyped Steam global.)

- [ ] **Step 3: Build**

Run: `pnpm build`
Expected: `dist/index.js` produced, no errors.

- [ ] **Step 4: On-device verification (manual — SKIP in CI).** Deploy and confirm: selecting a game shows versions with ★/● markers; Restore snapshots-then-reverts (and is blocked while the game runs); Pin/Rename/Delete update the list; the Keep-last-N slider persists.

- [ ] **Step 5: Commit**

```bash
git add src/index.tsx
git commit -m "feat: QAM restore/pin/rename/delete + retention slider + running guard"
```

---

## Self-review (done while writing)

- **Spec coverage:** revert with movable HEAD + pre-revert auto-snapshot + crash-safety (Tasks 5–8, §5.2) ✓; pin/rename (Task 3, §5.3) ✓; manual delete refusing HEAD (Task 4, §5.3) ✓; count retention with pins counted-but-protected + HEAD protected (Task 2, §5.4) ✓; per-game keepCount config (Task 1, §3.1) ✓; frontend restore/pin/rename/delete + retention slider + `Router.RunningApps` guard (Task 9, §8) ✓. Deferred (noted): discovery hardening (§4.1) and the Steam-Cloud-conflict mtime mitigation (§7) — both belong to the discovery-hardening follow-up; M2's revert assumes the single-device, cloud-quiescent case.
- **Type consistency:** `do_backup(..., keep_count=20)`, `cull_versions(data_root, app_id, keep_count)`, `revert_to(data_root, steam_root, account_id, app_id, target_id, now_ms, rand_hex)`, `_apply_pending`/`resume_pending_revert` signatures, `restore_version(data_root, app_id, version_id, suffix_to_root)`, and the `Engine`/`Plugin` method names line up with the frontend `callable` names.
- **No placeholders:** every step has complete code.
- **Note for the implementer:** Task 2 changes `do_backup`'s signature — only adds a defaulted `keep_count`, so existing M1 callers/tests are unaffected. Task 8 changes `get_versions` to self-heal pending reverts; the M1 `test_engine.py::test_do_backup_then_get_versions` still holds (no pending revert → no-op).

---

## After M2 — remaining roadmap

- **Discovery hardening** (its own plan): `steam_autocloud.vdf` rglob + longest-common-prefix root recovery; SD-card (`/run…`) system-dir search; cache resolved roots in `game.json`; resolve via the `root` enum first. The `_safe_rel` guard is already load-bearing for revert restore.
- **Steam Cloud conflict mitigation** (§7): after a revert, bump live-file mtimes so Steam treats local as newest; surface a one-time tip. Fold into discovery-hardening or M3.
- **M3** auto-backup-on-exit toggle + exit debounce. **M4** Google Drive.
```
