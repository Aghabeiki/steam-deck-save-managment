# Force Backup / Restore While a Game Is Running — Design

**Goal:** Let the user manually back up *and* restore a save while the game is
running, guarding against torn (mid-write) snapshots with a content-quiescence
check, and being honest in the UI about the risks of restoring under a live game.

**Architecture:** A small async quiescence gate in `main.py` wraps the two
existing, already-tested engine operations (`do_backup`, `revert`). The gate
hashes the live save, waits 0.8 s, and re-hashes; it only proceeds when the
content is stable. The frontend enables the previously-disabled controls while
running and routes them through the new force RPCs.

**Tech stack:** Python 3.11 engine (`defaults/py_modules/savemanager/`),
`main.py` Decky plugin host (async RPC), React/TS frontend (`src/index.tsx`,
`@decky/api` + `@decky/ui`).

---

## Background — current behavior

- The backup button is `disabled={running || busy}` (`src/index.tsx:182`) with the
  label "Stop the game to back up". The block is **purely frontend**: the engine's
  `do_backup` has no running guard — it simply snapshots whatever is on disk.
- `doRestore` blocks with a toast when running (`src/index.tsx:126`). Again the
  engine's `revert_to` has no running guard.
- `Engine.current_state` (`api.py`) already hashes every live cloud file
  (content-only, mtime-independent) to answer "which save am I on?". That exact
  loop is what the quiescence gate needs.

The risk that justified the guards: snapshotting or overwriting a save the game
is actively writing can capture/produce a torn file. We address that risk rather
than just removing the guards.

---

## Design

### Shared piece — `Engine.hash_live_save`

Extract the live-file hashing loop currently inline in `current_state` into a
reusable method, and have `current_state` call it (DRY, no behavior change):

```python
def hash_live_save(self, app_id: int) -> dict | None:
    """{(suffix, path): sha256} for every live cloud file, or None if the
    save roots can't be resolved. Content-only (survives mtime/Steam-Cloud bumps)."""
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

`current_state` is refactored to call `self.hash_live_save(app_id)` for its `live`
map; all existing `current_state` behavior and tests stay green.

### The quiescence gate — `main.py`

```python
_QUIESCE_SECONDS = 0.8   # one hash -> wait -> hash window

async def _quiescent(self, app_id: int) -> str:
    """'stable' | 'writing' | 'unresolvable'. Off the engine's synchronous
    mutation path (same pattern as the debounced auto-backup)."""
    eng = get_engine()
    h1 = eng.hash_live_save(app_id)
    if h1 is None:
        return "unresolvable"
    await asyncio.sleep(_QUIESCE_SECONDS)
    h2 = eng.hash_live_save(app_id)
    if h2 is None:
        return "unresolvable"
    return "stable" if h1 == h2 else "writing"
```

The sleep lives in the async RPC handler, never inside an engine mutation, so the
single-threaded "every engine mutation is synchronous + atomic" invariant holds.

### Force backup — `main.py`

```python
async def force_backup(self, game_info: dict) -> dict:
    app_id = game_info.get("appId")
    q = await self._quiescent(app_id)
    if q != "stable":
        return {"status": q}                      # 'writing' | 'unresolvable'
    entry = get_engine().do_backup(game_info, _now_ms(), _rand_hex())
    if entry:
        self._maybe_mirror(game_info)
    return {"status": "ok" if entry else "nochange", "entry": entry}
```

- `do_backup` keeps ignore-unchanged + cull + atomic refs commit; a forced backup
  is an ordinary manual snapshot, no special kind/reason.

### Force restore — `main.py`

```python
async def force_restore(self, game_info: dict, target_id: str) -> dict:
    app_id = game_info.get("appId")
    q = await self._quiescent(app_id)
    if q != "stable":
        return {"status": q}                      # restoring mid-write is the worst moment
    head = get_engine().revert(game_info, target_id, _now_ms(), _rand_hex())
    return {"status": "ok", "head": head} if head is not None else {"status": "notfound"}
```

`revert` already auto-snapshots the current live save before materializing the
target (crash-safe via `pendingRevertTo`), so the user's pre-restore state is
preserved as a real version even when restoring under a running game.

### Frontend — `src/index.tsx`

New callables:

```tsx
const forceBackup  = callable<[GameInfo], { status: string; entry?: VersionEntry }>("force_backup");
const forceRestore = callable<[GameInfo, string], { status: string }>("force_restore");
```

**Backup button** — enable while running, route by `running`:

```tsx
// button: disabled={busy}; label = busy ? "Backing up…"
//   : running ? "⬇  Back up now (while playing)" : "⬇  Back up now"
const backup = async () => {
  setBusy(true);
  try {
    if (running) {
      const r = await forceBackup(game);
      if (r.status === "ok")        toast("Backed up", "Snapshot taken while playing.");
      else if (r.status === "nochange") toast("No change since last backup");
      else if (r.status === "writing")  toast("Save is being written", "Try again in a moment.");
      else                          toast("Couldn’t read the save");
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

**Restore** — while running, show an inline confirm (new `confirmRestoreId`
state, mirroring `confirmDeleteId`) instead of the "Stop the game first" toast:

```tsx
const doRestore = async (v: VersionEntry) => {
  if (isRunning(game.appId)) { setConfirmRestoreId(v.versionId); return; }   // -> inline confirm
  try { await revert(game, v.versionId);
        toast(`Restored “${labelOf(v)}”`, "Your previous save was snapshotted — undo anytime."); }
  catch (err) { console.error("SaveManager: restore failed", err); toast("Restore failed", "Your save was not changed."); }
  finally { refresh(); }
};

const doForceRestore = async (v: VersionEntry) => {
  setConfirmRestoreId(null);
  try {
    const r = await forceRestore(game, v.versionId);
    if (r.status === "ok")
      toast(`Restored “${labelOf(v)}” to disk`,
            `Load your save in-game or restart ${game.name}; don’t let it autosave first.`);
    else if (r.status === "writing")     toast("Save is being written", "Try again in a moment.");
    else if (r.status === "unresolvable") toast("Couldn’t read the save");
    else                                 toast("Restore failed", "That version was not found.");
  } catch (err) { console.error("SaveManager: force restore failed", err); toast("Restore failed", "Your save was not changed."); }
  finally { refresh(); }
};
```

Inline confirm block in the `versions.map` (rendered when
`confirmRestoreId === v.versionId`, mirroring the delete-confirm block):

```tsx
<Field label={`Restore “${labelOf(v)}” while playing?`}
       description={`⚠ ${game.name} is running. This overwrites the save on disk, but the game still has the old save in memory and may overwrite this on its next autosave or when you quit.`}
       bottomSeparator="none" />
<PanelSectionRow><ButtonItem layout="below" onClick={() => doForceRestore(v)}>Restore anyway</ButtonItem></PanelSectionRow>
<PanelSectionRow><ButtonItem layout="below" onClick={() => setConfirmRestoreId(null)}>Cancel</ButtonItem></PanelSectionRow>
```

The Restore button still shows only on non-current rows (the `!isCurrent` guard
from the current-state fix is unchanged).

---

## RPC contracts

| RPC | Args | Returns |
|---|---|---|
| `force_backup` | `game_info` | `{status: "ok", entry}` · `{status: "nochange"}` · `{status: "writing"}` · `{status: "unresolvable"}` |
| `force_restore` | `game_info, target_id` | `{status: "ok", head}` · `{status: "writing"}` · `{status: "unresolvable"}` · `{status: "notfound"}` |
| `hash_live_save` (engine only, not RPC) | `app_id` | `{(suffix,path): sha256}` · `None` |

## Error handling

- **`writing`** → toast, nothing changed, user retries. Same for backup and restore.
- **`unresolvable`** (save roots gone) → toast "Couldn’t read the save".
- **`notfound`** (restore target missing) → toast "Restore failed — version not found".
- **Exceptions** → existing per-handler try/catch toasts; engine never half-commits
  (refs.json is the atomic commit point).
- Restore stays a deliberate two-step (confirm) only while running; the
  not-running path is unchanged.

## Testing

- `hash_live_save`: returns a stable `{(suffix,path): sha256}` map for a known
  save; the map changes when a managed file’s content changes; returns `None`
  when no save roots resolve. (~3 engine tests.)
- `current_state` regression: still returns the same result after being
  refactored onto `hash_live_save` (existing tests cover this).
- `do_backup` / `revert` already have engine tests; the force RPCs are thin async
  glue (`_quiescent` → existing op) and are not separately unit-tested, consistent
  with the rest of `main.py`.

## Out of scope (YAGNI)

- **Force restore is inherently best-effort.** On games that read the save only at
  load and write it back on exit, the on-disk restore may not take until the game
  reloads, and the game can overwrite it. The confirm + post-restore toast are the
  honest mitigation; we do not try to suspend the game or intercept its file I/O.
- No "live backup" badge or special tagging in the version list — a forced backup
  is an ordinary manual snapshot.
- No change to auto-backup-on-exit, Drive (v2), or the not-running paths.
- Quiescence is a single hash→0.8 s→hash window, not a repeated poll.

## Risks

- A write that lands *after* the quiescence window still slips through — the gate
  is best-effort, not a lock. Acceptable: it catches the common "mid-save right
  now" case cheaply, and a real cross-process lock is the separate `locking.py`
  follow-up already noted in `main.py`.
- Steam Cloud activity (not the game) could trip `writing` and make the user
  retry. Rare mid-play; safe failure mode.
