# Force Backup / Restore While Running — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **⚠ Project rule — NO auto-commits.** This repo has a hard "never commit without explicit per-action permission" rule. The `Commit` steps below mark logical checkpoints, but the executor MUST ask the user before running any `git commit`. Do not push/tag/release.

**Goal:** Let the user manually back up and restore a save while the game is running, gated by a content-quiescence check that refuses to act on a save being actively written.

**Architecture:** Extract the live-save hashing loop already inside `Engine.current_state` into a reusable `Engine.hash_live_save`, add a pure `quiescence_verdict` helper, then add an async `_quiescent` gate in `main.py` that wraps the existing, already-tested `do_backup` / `revert` engine ops behind two new RPCs (`force_backup`, `force_restore`). The frontend enables the previously-disabled controls while running and routes them through the new RPCs; restore additionally requires an inline confirm.

**Tech Stack:** Python 3.11 engine (`defaults/py_modules/savemanager/`), `main.py` Decky async RPC host, React/TS frontend (`src/index.tsx`, `@decky/api` + `@decky/ui`), pytest.

**Spec:** `docs/superpowers/specs/2026-06-22-force-save-ops-while-running-design.md`

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `defaults/py_modules/savemanager/api.py` | engine facade | add `hash_live_save`, module-level `quiescence_verdict`; refactor `current_state` onto `hash_live_save` |
| `main.py` | Decky async RPC host | add `_QUIESCE_SECONDS`, `_quiescent`, `force_backup`, `force_restore` |
| `src/index.tsx` | QAM UI | `forceBackup`/`forceRestore` callables; enable backup while running; restore inline-confirm + force path |
| `tests/test_force_ops.py` | **new** | tests for `hash_live_save`, `quiescence_verdict`, `current_state` regression |

**Note on testability:** `main.py` imports `decky` (only present in the Decky runtime) so it is **not importable under pytest** — there are no `main.py` unit tests in this repo. Task 2's logic is kept testable by living in `api.py` (`hash_live_save`, `quiescence_verdict`); the thin async glue in `main.py` is verified by syntax-check + on-device run in Task 5.

---

## Task 1: Engine — `hash_live_save` + `quiescence_verdict` + `current_state` refactor

**Files:**
- Modify: `defaults/py_modules/savemanager/api.py` (`current_state` is at lines 94–144)
- Test: `tests/test_force_ops.py` (create)

Run tests from the repo root. The suite is configured so `from savemanager.api import …` and `from tests.fixtures import …` resolve (see existing `tests/test_engine_m2.py`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_force_ops.py`:

```python
import os
from savemanager.api import Engine, quiescence_verdict
from tests.fixtures import make_steam_tree


def _engine(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path)
    eng = Engine(os.path.join(str(tmp_path), "data"), steam_root)
    eng.set_account_id(acct)
    return eng, steam_root, acct, app


def _save1(steam_root, acct, app):
    return os.path.join(steam_root, "userdata", str(acct), str(app), "remote", "save1.sav")


def _key(h, name):
    return next(k for k in h if k[1] == name)


def test_hash_live_save_returns_content_map(tmp_path):
    eng, steam_root, acct, app = _engine(tmp_path)
    h = eng.hash_live_save(app)
    assert {path for (_suffix, path) in h} == {"save1.sav", "profile.bin"}
    assert all(isinstance(v, str) and len(v) == 64 for v in h.values())


def test_hash_live_save_reflects_content_change(tmp_path):
    eng, steam_root, acct, app = _engine(tmp_path)
    before = eng.hash_live_save(app)
    with open(_save1(steam_root, acct, app), "w") as f:
        f.write("DIFFERENT-CONTENT")
    after = eng.hash_live_save(app)
    assert after[_key(after, "save1.sav")] != before[_key(before, "save1.sav")]
    assert after[_key(after, "profile.bin")] == before[_key(before, "profile.bin")]


def test_hash_live_save_none_when_unresolvable(tmp_path):
    # empty steam tree -> no account discovered -> no resolvable save
    eng = Engine(os.path.join(str(tmp_path), "data"), os.path.join(str(tmp_path), "EmptySteam"))
    assert eng.hash_live_save(281990) is None


def test_quiescence_verdict():
    a = {("", "save1.sav"): "x"}
    assert quiescence_verdict(a, dict(a)) == "stable"
    assert quiescence_verdict(a, {("", "save1.sav"): "y"}) == "writing"
    assert quiescence_verdict(None, a) == "unresolvable"
    assert quiescence_verdict(a, None) == "unresolvable"


def test_current_state_after_backup_is_head(tmp_path):
    eng, steam_root, acct, app = _engine(tmp_path)
    entry = eng.do_backup({"appId": app, "name": "X"}, now_ms=1000, rand_hex="aaa")
    st = eng.current_state(app)
    assert st["matchedVersionId"] == entry["versionId"]
    assert st["isHead"] is True and st["modified"] is False and st["resolvable"] is True


def test_current_state_modified_after_play(tmp_path):
    eng, steam_root, acct, app = _engine(tmp_path)
    eng.do_backup({"appId": app, "name": "X"}, now_ms=1000, rand_hex="aaa")
    with open(_save1(steam_root, acct, app), "w") as f:
        f.write("PLAYED-MORE")
    st = eng.current_state(app)
    assert st["modified"] is True and st["matchedVersionId"] is None and st["resolvable"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_force_ops.py -q`
Expected: FAIL — `ImportError: cannot import name 'quiescence_verdict'` / `AttributeError: 'Engine' object has no attribute 'hash_live_save'`.

- [ ] **Step 3: Add `quiescence_verdict` (module-level) and `hash_live_save` (method)**

In `defaults/py_modules/savemanager/api.py`, add this module-level function just above `class Engine:` (after the imports):

```python
def quiescence_verdict(h1, h2) -> str:
    """'unresolvable' if either live-hash map is None, else 'stable' if the two
    maps are equal (save not being written), else 'writing'."""
    if h1 is None or h2 is None:
        return "unresolvable"
    return "stable" if h1 == h2 else "writing"
```

Add this method to `Engine` (place it directly above `current_state`):

```python
    def hash_live_save(self, app_id: int) -> dict | None:
        """{(suffix, path): sha256} for every live cloud file, or None if the save
        roots can't be resolved. Content-only — survives mtime/Steam-Cloud bumps."""
        from .discovery import read_entries, resolve_save_roots
        from .versioning import _hash_file, _safe_rel
        acct = self._primary()
        if acct is None:
            return None
        installdir = parse_installdir(self.steam_root, app_id)
        entries = read_entries(self.steam_root, acct, app_id)
        save_roots = resolve_save_roots(self.steam_root, acct, app_id, entries, installdir)
        if not save_roots:
            return None
        live = {}
        for absdir, suffix in save_roots.items():
            for e in entries:
                if not _safe_rel(e.path):
                    continue
                p = os.path.join(absdir, e.path)
                try:
                    if os.path.isfile(p):
                        live[(suffix, e.path)] = _hash_file(p)
                except OSError:
                    continue
        return live
```

- [ ] **Step 4: Refactor `current_state` to reuse `hash_live_save`**

In `current_state`, replace the inline live-hashing block. The current body (api.py ~104–124) is:

```python
        refs = read_refs(self.data_root, app_id)
        head_id = refs["head"]["versionId"]
        installdir = parse_installdir(self.steam_root, app_id)
        entries = read_entries(self.steam_root, acct, app_id)
        save_roots = resolve_save_roots(self.steam_root, acct, app_id, entries, installdir)
        if not save_roots:
            return none
        # Hash the live cloud files once (content only — survives mtime/Steam-Cloud bumps).
        live = {}
        for absdir, suffix in save_roots.items():
            for e in entries:
                if not _safe_rel(e.path):
                    continue
                p = os.path.join(absdir, e.path)
                try:
                    if os.path.isfile(p):
                        live[(suffix, e.path)] = _hash_file(p)
                except OSError:
                    continue
```

Replace it with:

```python
        refs = read_refs(self.data_root, app_id)
        head_id = refs["head"]["versionId"]
        live = self.hash_live_save(app_id)
        if live is None:
            return none
```

Leave the rest of `current_state` (the `version_matches` closure, the HEAD-first ordering, the return values) unchanged. The now-unused local imports inside `current_state` (`read_entries`, `resolve_save_roots`, `_hash_file`, `_safe_rel`) can be removed from its top `from … import …` lines; keep `read_meta` and `read_refs` which it still uses.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_force_ops.py -q`
Expected: PASS (6 passed).

- [ ] **Step 6: Run the full suite (no regressions)**

Run: `python -m pytest -q`
Expected: `143 passed` (137 existing + 6 new).

- [ ] **Step 7: Commit** *(ask the user first — see header rule)*

```bash
git add defaults/py_modules/savemanager/api.py tests/test_force_ops.py
git commit -m "feat(engine): hash_live_save + quiescence_verdict; reuse in current_state"
```

---

## Task 2: `main.py` — quiescence gate + force RPCs

**Files:**
- Modify: `main.py` (constants near line 20; `from savemanager.api import Engine` at line 12; add methods in `class Plugin`)

No pytest coverage (main.py imports `decky`, not importable here). Verified by syntax-check + the engine-symbol import + on-device run (Task 5).

- [ ] **Step 1: Import the helper and add the constant**

In `main.py`, change the engine import (line 12) from:

```python
from savemanager.api import Engine
```
to:
```python
from savemanager.api import Engine, quiescence_verdict
```

Add the constant next to `_EXIT_SETTLE_MAX_SECONDS` (near line 20):

```python
# Quiescence window for force backup/restore while a game runs: hash -> wait -> hash.
_QUIESCE_SECONDS = 0.8
```

- [ ] **Step 2: Add the gate + two RPC methods**

In `class Plugin`, add these methods immediately after `get_current_state` (line ~75):

```python
    async def _quiescent(self, app_id) -> str:
        """'stable' | 'writing' | 'unresolvable'. The sleep is off the engine's
        synchronous mutation path (same pattern as the debounced auto-backup)."""
        eng = get_engine()
        h1 = eng.hash_live_save(app_id)
        if h1 is None:
            return "unresolvable"
        await asyncio.sleep(_QUIESCE_SECONDS)
        return quiescence_verdict(h1, eng.hash_live_save(app_id))

    async def force_backup(self, game_info: dict) -> dict:
        """Manual backup while the game runs: snapshot only if the save is quiescent."""
        q = await self._quiescent(game_info.get("appId"))
        if q != "stable":
            return {"status": q}                       # 'writing' | 'unresolvable'
        entry = get_engine().do_backup(game_info, _now_ms(), _rand_hex())
        if entry:
            self._maybe_mirror(game_info)
        return {"status": "ok" if entry else "nochange", "entry": entry}

    async def force_restore(self, game_info: dict, target_id: str) -> dict:
        """Restore while the game runs: only if quiescent (restoring mid-write is the
        worst moment). revert() auto-snapshots the current live save first."""
        q = await self._quiescent(game_info.get("appId"))
        if q != "stable":
            return {"status": q}
        head = get_engine().revert(game_info, target_id, _now_ms(), _rand_hex())
        return {"status": "ok", "head": head} if head is not None else {"status": "notfound"}
```

- [ ] **Step 3: Verify it parses and the imported symbol exists**

Run:
```bash
python -c "import ast; ast.parse(open('main.py').read()); print('main.py parses')"
PYTHONPATH=defaults/py_modules python -c "from savemanager.api import Engine, quiescence_verdict; print('symbols OK')"
```
Expected:
```
main.py parses
symbols OK
```

- [ ] **Step 4: Commit** *(ask the user first)*

```bash
git add main.py
git commit -m "feat(host): force_backup/force_restore RPCs gated by quiescence check"
```

---

## Task 3: Frontend — force backup (enable while running)

**Files:**
- Modify: `src/index.tsx` (callables ~25–38; `backup` handler 115–124; backup button 181–185)

- [ ] **Step 1: Add the `forceBackup` callable**

In `src/index.tsx`, add directly after the `revert` callable (line 30):

```tsx
const forceBackup = callable<[GameInfo], { status: string; entry?: VersionEntry | null }>("force_backup");
```

- [ ] **Step 2: Route the backup handler by running-state**

Replace the `backup` handler (lines 115–124):

```tsx
  const backup = async () => {
    setBusy(true);
    try {
      if (running) {
        const r = await forceBackup(game);
        if (r.status === "ok") toast("Backed up", "Snapshot taken while playing.");
        else if (r.status === "nochange") toast("No change since last backup");
        else if (r.status === "writing") toast("Save is being written", "Try again in a moment.");
        else toast("Couldn’t read the save");
      } else {
        const e = await doBackup(game);
        toast(e ? "Backed up" : "No change since last backup");
      }
    } catch (err) {
      console.error("SaveManager: backup failed", err);
      toast("Backup failed", "See the plugin log for details.");
    } finally { setBusy(false); refresh(); }
  };
```

- [ ] **Step 3: Enable the button while running + relabel**

Replace the backup button block (lines 181–185):

```tsx
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={busy} onClick={backup}>
          {busy ? "Backing up…" : running ? "⬇  Back up now (while playing)" : "⬇  Back up now"}
        </ButtonItem>
      </PanelSectionRow>
```

- [ ] **Step 4: Build to verify it compiles**

Run: `pnpm run build 2>&1 | tail -3`
Expected: `created dist in …` with no TypeScript errors.

- [ ] **Step 5: Commit** *(ask the user first)*

```bash
git add src/index.tsx
git commit -m "feat(ui): force backup while a game is running"
```

---

## Task 4: Frontend — force restore (inline confirm)

**Files:**
- Modify: `src/index.tsx` (add `forceRestore` callable; `confirmRestoreId` state near line 99; `doRestore` 125–134; `startRename` line 142; add confirm block in `versions.map` after the delete-confirm block at lines 202–209)

- [ ] **Step 1: Add the `forceRestore` callable**

Add directly after the `forceBackup` callable from Task 3:

```tsx
const forceRestore = callable<[GameInfo, string], { status: string }>("force_restore");
```

- [ ] **Step 2: Add the confirm state**

Add after the `confirmDeleteId` state declaration (line 99):

```tsx
  const [confirmRestoreId, setConfirmRestoreId] = useState<string | null>(null);
```

- [ ] **Step 3: Make `doRestore` open a confirm when running, and add `doForceRestore`**

Replace the `doRestore` handler (lines 125–134) with:

```tsx
  const doRestore = async (v: VersionEntry) => {
    if (isRunning(game.appId)) { setConfirmRestoreId(v.versionId); return; }   // -> inline confirm
    try {
      await revert(game, v.versionId);
      toast(`Restored “${labelOf(v)}”`, "Your previous save was snapshotted — undo anytime.");
    } catch (err) {
      console.error("SaveManager: restore failed", err);
      toast("Restore failed", "Your save was not changed.");
    } finally { refresh(); }
  };
  const doForceRestore = async (v: VersionEntry) => {
    setConfirmRestoreId(null);
    try {
      const r = await forceRestore(game, v.versionId);
      if (r.status === "ok")
        toast(`Restored “${labelOf(v)}” to disk`,
          `Load your save in-game or restart ${game.name}; don’t let it autosave first.`);
      else if (r.status === "writing") toast("Save is being written", "Try again in a moment.");
      else if (r.status === "unresolvable") toast("Couldn’t read the save");
      else toast("Restore failed", "That version was not found.");
    } catch (err) {
      console.error("SaveManager: force restore failed", err);
      toast("Restore failed", "Your save was not changed.");
    } finally { refresh(); }
  };
```

- [ ] **Step 4: Clear the restore confirm when starting a rename**

Replace `startRename` (line 142):

```tsx
  const startRename = (v: VersionEntry) => { setConfirmDeleteId(null); setConfirmRestoreId(null); setRenamingId(v.versionId); setRenameText(v.name ?? ""); };
```

- [ ] **Step 5: Render the inline restore-confirm block**

In the `versions.map` callback, directly after the existing `if (confirmDeleteId === v.versionId) { … }` block (ends line 210), add:

```tsx
        if (confirmRestoreId === v.versionId) {
          return (
            <Fragment key={v.versionId}>
              <Field label={`Restore “${labelOf(v)}” while playing?`}
                description={`⚠ ${game.name} is running. This overwrites the save on disk, but the game still has the old save in memory and may overwrite this on its next autosave or when you quit.`}
                bottomSeparator="none" />
              <PanelSectionRow><ButtonItem layout="below" onClick={() => doForceRestore(v)}>Restore anyway</ButtonItem></PanelSectionRow>
              <PanelSectionRow><ButtonItem layout="below" onClick={() => setConfirmRestoreId(null)}>Cancel</ButtonItem></PanelSectionRow>
            </Fragment>
          );
        }
```

- [ ] **Step 6: Build to verify it compiles**

Run: `pnpm run build 2>&1 | tail -3`
Expected: `created dist in …` with no TypeScript errors.

- [ ] **Step 7: Commit** *(ask the user first)*

```bash
git add src/index.tsx
git commit -m "feat(ui): inline-confirmed force restore while a game is running"
```

---

## Task 5: Integration verification + on-device deploy

**Files:** none (verification only)

- [ ] **Step 1: Full backend suite green**

Run: `python -m pytest -q`
Expected: `143 passed`.

- [ ] **Step 2: Frontend builds clean**

Run: `pnpm run build 2>&1 | tail -3`
Expected: `created dist in …`, no errors.

- [ ] **Step 3: Deploy + reload (no Steam shutdown needed; backend changed → restart plugin_loader)**

Copy files (no sudo — plugin files are user-owned), then reload the backend:

```bash
DST=~/homebrew/plugins/SaveManager
cp main.py "$DST/main.py"
cp defaults/py_modules/savemanager/api.py "$DST/py_modules/savemanager/api.py"
cp dist/index.js "$DST/dist/index.js"
cp dist/index.js.map "$DST/dist/index.js.map" 2>/dev/null || true
```
Then the user runs (sudo needs a password):
```
! sudo systemctl restart plugin_loader
```
Expected (in the CEF monitor): `Loaded SaveManager in …ms`, no exceptions.

- [ ] **Step 4: Manual on-device checks** (game running — e.g. Hollow Knight)

  - **Force backup:** With the game running, the button reads `⬇ Back up now (while playing)` and is enabled. Tap it → a new `●`-current version appears (or "Save is being written — try again" if tapped mid-save; retry succeeds). Top field shows `… up to date ✓`.
  - **Force restore:** Expand an older version → `↩ Restore this save` → because the game is running, an inline confirm appears with the ⚠ warning. Tap **Restore anyway** → toast "Restored … to disk — load your save in-game …". Verify on disk that the live save file now matches the chosen version. **Cancel** dismisses with no change.
  - **Not-running paths unchanged:** With the game closed, backup says `⬇ Back up now` and restore happens immediately with no confirm.

- [ ] **Step 5: Final commit (if anything uncommitted)** *(ask the user first)*

```bash
git status --short
```

---

## Self-Review (filled in by the planner)

**Spec coverage:**
- Shared `hash_live_save` + `current_state` reuse → Task 1. ✓
- `quiescence_verdict` / `_quiescent` gate (hash → 0.8 s → hash) → Task 1 (pure) + Task 2 (async). ✓
- `force_backup` RPC + status contract (`ok`/`nochange`/`writing`/`unresolvable`) → Task 2 + Task 3. ✓
- `force_restore` RPC + status contract (`ok`/`writing`/`unresolvable`/`notfound`) → Task 2 + Task 4. ✓
- Backup button enabled while running + label → Task 3. ✓
- Restore inline confirm + honest warning + post-restore guidance toast → Task 4. ✓
- Restore stays a confirm only while running; not-running paths unchanged → Tasks 3–4 + Task 5 Step 4. ✓
- Tests: `hash_live_save` (map / change / None) + `current_state` regression → Task 1. ✓

**Type consistency:** RPC names (`force_backup`, `force_restore`) match between `main.py` (Task 2) and the TS callables (Tasks 3–4). Status strings (`ok`, `nochange`, `writing`, `unresolvable`, `notfound`) match between the Python returns and the TS `r.status` checks. `hash_live_save` returns `dict | None` and `quiescence_verdict(h1, h2)` consumes exactly that.

**No placeholders:** every code step shows complete code; every run step shows the exact command + expected output.
