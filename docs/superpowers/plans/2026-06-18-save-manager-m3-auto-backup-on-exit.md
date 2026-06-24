# Save Manager — M3 Auto-Backup-on-Exit — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically snapshot a game's save when it exits, controlled by a per-game toggle — safely, by first hardening change-detection with a content hash so an exit-adjacent backup can't be falsely skipped.

**Architecture:** Extends the merged M1+M2 engine. Task 1 (prerequisite, flagged by the M2 review): store a `sha256` per file in each version's `meta.json` and make `_live_matches_head` confirm "unchanged" with the hash when size+mtime match — closing the same-size/same-mtime-tick false-skip. Tasks 2–4 add the per-game `autoBackupOnExit` setting, an engine auto-backup path (`kind=auto`, `reason=game-exit`) with a debounce that waits for Steam's post-exit `remotecache.vdf` to settle, and the QAM toggle + exit-hook wiring.

**Tech Stack:** Python 3.11 (stdlib, incl. `hashlib`, `asyncio`), pytest. Frontend: TypeScript/React, `@decky/api`, `@decky/ui`.

**Reference:** Spec `docs/superpowers/specs/2026-06-17-steam-deck-save-manager-design.md` (§5.1 auto-backup + debounce, §3.1 settings, §8 toggle). M2 review flagged the hash prerequisite. Plans: M1/M2 under `docs/superpowers/plans/`.

**Out of scope (deferred to their own plans):** backend revert guard / `locking.py`; discovery hardening (autocloud, SD-card, Cloud-conflict mtime); M4 Google Drive; the "pinned exceeds keep cap" UI warning.

---

## Existing engine (M1+M2 — call these, don't re-implement)

- `store.py`: `_safe_rel`, `game_dir`, `version_dir`, `new_version_id`, `atomic_copy`, `create_snapshot(data_root, app_id, save_roots, entries, version_id, created_at, kind, reason, parent) -> meta`, `read_meta`, `delete_version`, `restore_version`. Imports `json, os, shutil` at top.
- `versioning.py`: `is_supported`, `_live_fingerprint` (REMOVED in Task 1), `_live_matches_head(data_root, app_id, refs, save_roots, entries)`, `do_backup(data_root, steam_root, account_id, game_info, now_ms, rand_hex, ignore_unchanged=True, kind="manual", reason="manual", keep_count=20)`, `list_versions`, `cull_versions`, `_materialize`, `_apply_pending`, `revert_to`, `resume_pending_revert`. Imports `os`; `from .discovery import parse_installdir, read_entries, resolve_save_roots`; `from .refs import make_version_entry, read_refs, write_refs`; `from .store import create_snapshot, new_version_id, read_meta, _safe_rel, delete_version, restore_version`.
- `discovery.py`: `get_account_ids`, `remotecache_path(steam_root, account_id, app_id)`, `read_entries`, `parse_installdir`, `resolve_save_roots`.
- `config.py`: `DEFAULTS = {keepCount:20, autoBackupOnExit:False, driveMirror:False, ignoreUnchanged:True}`, `get_game_settings(data_root, app_id)`, `set_game_setting(data_root, app_id, key, value) -> merged settings`.
- `api.py`: `Engine(data_root, steam_root)` with `set_account_id`, `_primary`, `find_supported`, `do_backup`, `get_versions`, `revert`, `set_pinned`, `set_name`, `remove_version`, `get_settings`, `set_keep_count`. Imports: `from .config import get_game_settings, set_game_setting`; `from .curation import remove_version, set_name, set_pinned`; `from .discovery import get_account_ids, parse_installdir`; `from .versioning import cull_versions, do_backup, is_supported, list_versions, resume_pending_revert, revert_to`.
- `main.py`: `class Plugin` with the matching async methods; helpers `get_engine()`, `_now_ms()`, `_rand_hex()`; imports `os, time, decky`; `_main`/`_unload` log.

**Data:** `meta.json` files = `[{suffix, path, size, mtime}]` (Task 1 adds `sha256`). `refs.json` version entry has `kind`/`reason` provenance.

---

## File Structure (M3)

| File | Change | Responsibility |
|---|---|---|
| `defaults/py_modules/savemanager/store.py` | Modify | Add `_hash_file`; `create_snapshot` records `sha256` per file. |
| `defaults/py_modules/savemanager/versioning.py` | Modify | `_live_matches_head` confirms unchanged via hash on size+mtime tie; remove now-unused `_live_fingerprint`. |
| `defaults/py_modules/savemanager/api.py` | Modify | Engine: `set_auto_backup`, `do_backup_on_exit` (kind=auto/reason=game-exit, honors toggle), `remotecache_mtime` (for debounce). |
| `main.py` | Modify | `_main` sets `self.loop`; `Plugin.set_auto_backup`; `Plugin.backup_on_exit` schedules a debounced background task. |
| `src/index.tsx` | Modify | Per-game "Auto-backup on exit" `ToggleField`; lifetime hook calls `backup_on_exit` on game exit. |
| `tests/test_change_detection.py`, `tests/test_auto_backup.py` | Create | Unit tests. |

---

## Task 1: sha256 change-detection hardening (M2-review prerequisite)

**Files:** Modify `defaults/py_modules/savemanager/store.py`, `defaults/py_modules/savemanager/versioning.py`; Test `tests/test_change_detection.py`, and append one assertion to `tests/test_store_snapshot.py`.

- [ ] **Step 1: Write the failing test `tests/test_change_detection.py`**

```python
import os
from savemanager.versioning import do_backup, list_versions
from savemanager.store import read_meta, version_dir
from tests.fixtures import make_steam_tree


def _ctx(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path)
    data_root = os.path.join(str(tmp_path), "data")
    save1 = os.path.join(steam_root, "userdata", str(acct), str(app), "remote", "save1.sav")
    return data_root, steam_root, acct, app, save1


def test_meta_records_sha256(tmp_path):
    data_root, steam_root, acct, app, _ = _ctx(tmp_path)
    do_backup(data_root, steam_root, acct, {"appId": app, "name": "X"}, now_ms=1, rand_hex="a")
    head = list_versions(data_root, app)["head"]["versionId"]
    meta = read_meta(data_root, app, head)
    assert all(len(f["sha256"]) == 64 for f in meta["files"])     # sha256 hex digest


def test_same_size_same_mtime_change_is_detected_by_hash(tmp_path):
    """The M2 false-skip: identical size AND mtime but different content -> only the
    hash tiebreaker catches it, so do_backup must still create a new version."""
    data_root, steam_root, acct, app, save1 = _ctx(tmp_path)
    gi = {"appId": app, "name": "X"}
    do_backup(data_root, steam_root, acct, gi, now_ms=1000, rand_hex="aaa")     # v1 = "AAAAA"
    head = list_versions(data_root, app)["head"]["versionId"]
    stored = os.path.join(version_dir(data_root, app, head), "root", "save1.sav")
    st_ns = os.stat(stored).st_mtime_ns                     # exact mtime of the stored copy
    with open(save1, "w") as f:
        f.write("BBBBB")                                    # SAME length as "AAAAA" (5 bytes)
    os.utime(save1, ns=(st_ns, st_ns))                      # force IDENTICAL mtime -> size+mtime match
    entry = do_backup(data_root, steam_root, acct, gi, now_ms=2000, rand_hex="bbb")
    assert entry is not None and entry["versionId"] == "v_2000_bbb"   # hash detected the change


def test_truly_unchanged_is_still_skipped(tmp_path):
    data_root, steam_root, acct, app, _ = _ctx(tmp_path)
    gi = {"appId": app, "name": "X"}
    do_backup(data_root, steam_root, acct, gi, now_ms=1000, rand_hex="aaa")
    assert do_backup(data_root, steam_root, acct, gi, now_ms=2000, rand_hex="bbb") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_change_detection.py -v`
Expected: FAIL — `test_meta_records_sha256` KeyErrors on `"sha256"`, and `test_same_size_same_mtime_change_is_detected_by_hash` fails because the current size+mtime check returns None (the exact false-skip we're fixing).

- [ ] **Step 3: Add `_hash_file` + record `sha256` in `store.py`.**

At the top of `store.py`, the imports are `import json` / `import os` / `import shutil`. Add `import hashlib` with them.

Add this helper near the top (after the imports, before `_safe_rel`):
```python
def _hash_file(path: str) -> str:
    """Streaming SHA-256 hex digest (bounded memory for large saves)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
```

In `create_snapshot`, the per-file loop currently appends:
```python
            st = os.stat(dst)
            files.append({
                "suffix": suffix, "path": e.path,
                "size": st.st_size, "mtime": int(st.st_mtime * 1000),
            })
```
Change the append to also record the hash:
```python
            st = os.stat(dst)
            files.append({
                "suffix": suffix, "path": e.path,
                "size": st.st_size, "mtime": int(st.st_mtime * 1000),
                "sha256": _hash_file(dst),
            })
```

- [ ] **Step 4: Rewrite `_live_matches_head` in `versioning.py` to confirm via hash; remove `_live_fingerprint`.**

Add `_hash_file` to the store import. The line:
```python
from .store import create_snapshot, new_version_id, read_meta, _safe_rel, delete_version, restore_version
```
becomes:
```python
from .store import create_snapshot, new_version_id, read_meta, _safe_rel, delete_version, restore_version, _hash_file
```

DELETE the entire `_live_fingerprint` function (it is only used by `_live_matches_head`, which no longer needs it):
```python
def _live_fingerprint(save_roots, entries) -> dict:
    cur = {}
    for absdir, suffix in save_roots.items():
        for e in entries:
            src = os.path.join(absdir, e.path)
            if os.path.isfile(src):
                st = os.stat(src)
                cur[(suffix, e.path)] = (st.st_size, int(st.st_mtime * 1000))
    return cur
```

REPLACE the entire `_live_matches_head` function with:
```python
def _live_matches_head(data_root, app_id, refs, save_roots, entries) -> bool:
    """True iff the live save dirs exactly equal the current HEAD version.
    A file is unchanged iff size AND mtime AND (when recorded) sha256 all match —
    the hash closes the same-size/same-mtime-tick false-skip flagged by the M2 review."""
    head_id = refs["head"]["versionId"]
    if not head_id:
        return False
    try:
        meta = read_meta(data_root, app_id, head_id)
    except (OSError, ValueError):
        return False
    meta_by_key = {(f["suffix"], f["path"]): f for f in meta["files"]}
    suffix_to_root = {suffix: absdir for absdir, suffix in save_roots.items()}

    live_keys = set()
    for absdir, suffix in save_roots.items():
        for e in entries:
            if _safe_rel(e.path) and os.path.isfile(os.path.join(absdir, e.path)):
                live_keys.add((suffix, e.path))
    if live_keys != set(meta_by_key.keys()):
        return False                                  # a managed file was added or removed

    for (suffix, rel), mf in meta_by_key.items():
        root = suffix_to_root.get(suffix)
        if root is None:
            return False
        p = os.path.join(root, rel)
        st = os.stat(p)
        if st.st_size != mf["size"] or int(st.st_mtime * 1000) != mf["mtime"]:
            return False
        if mf.get("sha256") is not None and _hash_file(p) != mf["sha256"]:
            return False                              # same size+mtime but different content
    return True
```

- [ ] **Step 5: Run tests to verify they pass (and the whole suite is green)**

Run: `python -m pytest tests/test_change_detection.py tests/test_versioning.py tests/test_revert.py -v`
Expected: PASS — the 3 new change-detection tests, plus the existing skip-unchanged and revert tests (unchanged content still hashes equal → still skipped/handled correctly).

- [ ] **Step 6: Append a `sha256` assertion to the existing meta test in `tests/test_store_snapshot.py`.** In `test_create_snapshot_copies_files_and_writes_meta`, after the existing final assertion:
```python
    assert {f["path"] for f in meta["files"]} == {"save1.sav", "profile.bin"}
```
add:
```python
    assert all(len(f["sha256"]) == 64 for f in meta["files"])
```

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add defaults/py_modules/savemanager/store.py defaults/py_modules/savemanager/versioning.py tests/test_change_detection.py tests/test_store_snapshot.py
git commit -m "feat: sha256 change-detection (close same-size/same-tick false-skip)"
```

---

## Task 2: Engine auto-backup path (toggle + do_backup_on_exit + remotecache_mtime)

**Files:** Modify `defaults/py_modules/savemanager/api.py`; Test `tests/test_auto_backup.py`.

- [ ] **Step 1: Write the failing test `tests/test_auto_backup.py`**

```python
import os
from savemanager.api import Engine
from tests.fixtures import make_steam_tree


def _engine(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path)
    eng = Engine(os.path.join(str(tmp_path), "data"), steam_root)
    eng.set_account_id(acct)
    return eng, steam_root, acct, app


def test_set_auto_backup_persists(tmp_path):
    eng, _, _, app = _engine(tmp_path)
    assert eng.get_settings(app)["autoBackupOnExit"] is False
    assert eng.set_auto_backup(app, True)["autoBackupOnExit"] is True
    assert eng.get_settings(app)["autoBackupOnExit"] is True


def test_do_backup_on_exit_noop_when_disabled(tmp_path):
    eng, _, _, app = _engine(tmp_path)
    assert eng.do_backup_on_exit({"appId": app, "name": "X"}, now_ms=1, rand_hex="a") is None
    assert eng.get_versions(app)["versions"] == []


def test_do_backup_on_exit_creates_auto_version_when_enabled(tmp_path):
    eng, _, _, app = _engine(tmp_path)
    eng.set_auto_backup(app, True)
    entry = eng.do_backup_on_exit({"appId": app, "name": "X"}, now_ms=1000, rand_hex="aaa")
    assert entry is not None
    assert entry["kind"] == "auto" and entry["reason"] == "game-exit"
    assert eng.get_versions(app)["head"]["versionId"] == "v_1000_aaa"


def test_remotecache_mtime_returns_file_mtime(tmp_path):
    eng, steam_root, acct, app = _engine(tmp_path)
    rc = os.path.join(steam_root, "userdata", str(acct), str(app), "remotecache.vdf")
    os.utime(rc, (1234.0, 1234.0))
    assert eng.remotecache_mtime(app) == 1234.0
    assert eng.remotecache_mtime(999999) == 0.0       # no file -> 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_auto_backup.py -v`
Expected: FAIL (`AttributeError: 'Engine' object has no attribute 'set_auto_backup'`).

- [ ] **Step 3: Extend `Engine` in `api.py`.**

Add `import os` as the FIRST line of `api.py` (it currently starts with `from .config ...` and has no `os` import). Then update the discovery import — the line:
```python
from .discovery import get_account_ids, parse_installdir
```
becomes:
```python
from .discovery import get_account_ids, parse_installdir, remotecache_path
```

Add these three methods to the `Engine` class (after `set_keep_count`):
```python
    def set_auto_backup(self, app_id: int, enabled: bool) -> dict:
        return set_game_setting(self.data_root, app_id, "autoBackupOnExit", bool(enabled))

    def do_backup_on_exit(self, game_info: dict, now_ms: int, rand_hex: str):
        acct = self._primary()
        if acct is None:
            return None
        app_id = game_info["appId"]
        settings = get_game_settings(self.data_root, app_id)
        if not settings.get("autoBackupOnExit"):
            return None
        return do_backup(self.data_root, self.steam_root, acct, game_info, now_ms, rand_hex,
                         kind="auto", reason="game-exit", keep_count=settings["keepCount"])

    def remotecache_mtime(self, app_id: int) -> float:
        accounts = self.account_ids or get_account_ids(self.steam_root)
        mtimes = []
        for acct in accounts:
            try:
                mtimes.append(os.path.getmtime(remotecache_path(self.steam_root, acct, app_id)))
            except OSError:
                pass
        return max(mtimes) if mtimes else 0.0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_auto_backup.py tests/test_engine_m2.py -v`
Expected: PASS (new auto-backup tests + unchanged M2 Engine tests).

- [ ] **Step 5: Commit**

```bash
git add defaults/py_modules/savemanager/api.py tests/test_auto_backup.py
git commit -m "feat: Engine auto-backup-on-exit path + remotecache mtime helper"
```

---

## Task 3: main.py — debounced exit handler + Plugin methods

**Files:** Modify `main.py`. (Verified by `python -m py_compile`; the async/Decky glue is verified on-device.)

- [ ] **Step 1: Add `asyncio` import and a debounce constant.** `main.py` currently begins:
```python
import os
import time

import decky  # provided by Decky at runtime

from savemanager.api import Engine
```
Change it to:
```python
import asyncio
import os
import time

import decky  # provided by Decky at runtime

from savemanager.api import Engine

# Max seconds to wait for Steam's post-exit remotecache.vdf to settle before snapshotting.
_EXIT_SETTLE_MAX_SECONDS = 8
```

- [ ] **Step 2: Set the event loop in `_main`.** Replace:
```python
    async def _main(self):
        decky.logger.info("SaveManager loaded")
```
with:
```python
    async def _main(self):
        self.loop = asyncio.get_event_loop()
        decky.logger.info("SaveManager loaded")
```

- [ ] **Step 3: Add the `Plugin` methods** (after the existing `set_keep_count` method):
```python
    async def set_auto_backup(self, app_id: int, enabled: bool) -> dict:
        return get_engine().set_auto_backup(app_id, enabled)

    async def backup_on_exit(self, game_info: dict):
        # Fast-return; do the debounce + backup off the RPC path so the socket never blocks.
        self.loop.create_task(self._debounced_backup(game_info))
        return None

    async def _debounced_backup(self, game_info: dict):
        try:
            engine = get_engine()
            app_id = game_info["appId"]
            if not engine.get_settings(app_id).get("autoBackupOnExit"):
                return                                  # toggle off -> nothing to do (skip polling)
            # Wait until Steam's post-exit remotecache.vdf mtime stops advancing (bounded).
            prev = engine.remotecache_mtime(app_id)
            for _ in range(_EXIT_SETTLE_MAX_SECONDS):
                await asyncio.sleep(1.0)
                cur = engine.remotecache_mtime(app_id)
                if cur == prev:
                    break
                prev = cur
            result = engine.do_backup_on_exit(game_info, _now_ms(), _rand_hex())
            decky.logger.info(f"SaveManager auto-backup on exit: {game_info.get('appId')} -> {result}")
        except Exception as e:
            decky.logger.error(f"SaveManager auto-backup failed: {e}")
```

- [ ] **Step 4: Validate syntax**

Run: `python -m py_compile main.py && echo OK`
Expected: prints "OK" (compiles without importing `decky`).

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "feat: debounced auto-backup-on-exit handler in Decky Plugin"
```

---

## Task 4: QAM frontend — auto-backup toggle + exit-hook wiring

**Files:** Modify `src/index.tsx`. Verified by `tsc` + build (on-device verification is manual).

- [ ] **Step 1: Add the two callables + settings field.** In `src/index.tsx`, the `Settings` interface is:
```tsx
interface Settings { keepCount: number; }
```
Change it to:
```tsx
interface Settings { keepCount: number; autoBackupOnExit: boolean; }
```
After the existing `const setKeepCount = callable<...>("set_keep_count");` line, add:
```tsx
const setAutoBackup = callable<[number, boolean], Settings>("set_auto_backup");
const backupOnExit = callable<[GameInfo], null>("backup_on_exit");
```

- [ ] **Step 2: Track the toggle state + load it.** In `Content()`, the state block is:
```tsx
  const [keepCount, setKeep] = useState<number>(20);
```
Add below it:
```tsx
  const [autoBackup, setAuto] = useState<boolean>(false);
```
The `refresh` function currently is:
```tsx
  const refresh = (g: GameInfo) => {
    getVersions(g.appId).then(setListing).catch(console.error);
    getSettings(g.appId).then((s) => setKeep(s.keepCount)).catch(console.error);
  };
```
Change the settings line to also load the toggle:
```tsx
  const refresh = (g: GameInfo) => {
    getVersions(g.appId).then(setListing).catch(console.error);
    getSettings(g.appId).then((s) => { setKeep(s.keepCount); setAuto(s.autoBackupOnExit); }).catch(console.error);
  };
```

- [ ] **Step 3: Render the toggle.** `ToggleField` is not yet imported. In the `@decky/ui` import block, add `ToggleField` to the named imports (keep alphabetical-ish; it must be in the list). Then, in the game-detail `return`, immediately BEFORE the `<SliderField ... "Keep last N" ... />` `PanelSectionRow`, add:
```tsx
      <PanelSectionRow>
        <ToggleField label="Auto-backup on exit" checked={autoBackup}
          onChange={(v: boolean) => { setAuto(v); setAutoBackup(selected.appId, v).catch(console.error); }} />
      </PanelSectionRow>
```

- [ ] **Step 4: Wire the lifetime hook to trigger auto-backup on exit.** In `definePlugin`, the hook currently is:
```tsx
  // @ts-ignore - Steam internal
  const hook = SteamClient.GameSessions.RegisterForAppLifetimeNotifications((n: any) => {
    console.log("SaveManager lifetime", n.unAppID, n.bRunning);
  });
```
Replace it with:
```tsx
  // @ts-ignore - Steam internal
  const hook = SteamClient.GameSessions.RegisterForAppLifetimeNotifications((n: any) => {
    if (n.bRunning) return;                         // only act on EXIT
    let name = String(n.unAppID);
    try {
      // @ts-ignore - Steam internal
      name = appStore.GetAppOverviewByGameID(n.unAppID)?.display_name ?? name;
    } catch (e) { /* keep the appId as the name */ }
    // Backend no-ops if this game's auto-backup toggle is off.
    backupOnExit({ appId: n.unAppID, name }).catch(console.error);
  });
```

- [ ] **Step 5: Type-check**

Run: `pnpm exec tsc --noEmit`
Expected: No errors. (If `ToggleField`'s prop names differ in the installed `@decky/ui`, open `node_modules/@decky/ui/dist/` to match them; do not `@ts-ignore` a real prop error.)

- [ ] **Step 6: Build**

Run: `pnpm build`
Expected: `dist/index.js` produced, no errors.

- [ ] **Step 7: On-device verification (manual — SKIP in CI).** Toggle "Auto-backup on exit" on for a game, play and quit it, reopen the panel — a new `auto` / `game-exit` version should appear (after the debounce). With the toggle off, quitting creates nothing.

- [ ] **Step 8: Commit**

```bash
git add src/index.tsx
git commit -m "feat: QAM auto-backup-on-exit toggle + exit-hook wiring"
```

---

## Self-review (done while writing)

- **Spec coverage:** §5.1 auto-backup-on-exit ✓ (Tasks 2–4); the §5.1 debounce ("poll until remotecache.vdf mtime settles") ✓ (Task 3); §3.1 `autoBackupOnExit` setting ✓ (Task 2); §8 per-game toggle ✓ (Task 4). The M2-review hash prerequisite ✓ (Task 1). Deferred (documented): backend revert guard, discovery hardening, Cloud-conflict mtime, M4 — out of M3 scope.
- **Type consistency:** Task 1 adds `sha256` to the `meta.files` dict and `_hash_file` to `store.py`, consumed by the rewritten `_live_matches_head`. `Engine.do_backup_on_exit` passes `kind="auto", reason="game-exit"` into the existing `do_backup(..., kind, reason, keep_count)` signature; the version entry's `kind`/`reason` come through `make_version_entry`. Callable names (`set_auto_backup`, `backup_on_exit`) match the new `Plugin` methods.
- **Backward-compat:** `meta.get("sha256")` is guarded (`mf.get("sha256") is not None`), so the hash check no-ops on any legacy meta; all existing M1/M2 tests stay green (unchanged content hashes equal → still skipped; content changes already differ in size/mtime in those tests).
- **No placeholders:** every step has complete code.

---

## After M3 — remaining roadmap

- **Backend revert guard / `locking.py`** (per-game flock): M2's running-guard is frontend-only (TOCTOU). 
- **Discovery hardening** (own plan): root-enum resolution, `steam_autocloud.vdf` autocloud + common-prefix fallback, SD-card system-dir search, Steam-Cloud-conflict mtime mitigation (bump live mtimes after a revert so Steam treats local as newest).
- **"Pinned exceeds keep cap" UI warning** (spec §5.4/§8).
- **M4** Google Drive: vendor `requests`+`certifi`, device-code OAuth (user's own client), real-file mirror + remote `index.json`, non-blocking upload with progress.
```
