# Steam Deck Save Manager — Design Spec

**Date:** 2026-06-17
**Status:** Approved (design direction); ready for implementation planning
**Author:** amin (+ Claude)

---

## 1. Overview

A [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) plugin for the Steam Deck that gives **per-game, versioned save management** with optional **Google Drive backup**. It targets **Steam Cloud games** and reuses the save-discovery approach proven by [steamback](https://github.com/geeksville/steamback) (parsing `remotecache.vdf`).

It extends steamback's "snapshot on exit" idea into a full version manager: a browsable version list per game, named/pinned versions, **git-like revert** (a movable HEAD you can move back and forward repeatedly), a manual backup button, a per-game auto-backup-on-exit toggle, count-based retention, and a one-way Google Drive mirror of the real save files.

**Runtime:** single Steam Deck, single user, Game Mode (Decky). Python 3.11 backend + TypeScript/React Quick-Access-Menu (QAM) frontend.

### 1.1 Goals (the six features)

1. **Version list per game** — browse every saved snapshot of a title, newest first.
2. **Pin / name a version** — give a version a label ("Before final boss"); pinned versions are protected from auto-deletion.
3. **Git-like revert** — a HEAD pointer tracked separately from the version history; revert to any version/pin, backward *and* forward, as many times as you like. The live save before a revert is never lost (auto-snapshot first).
4. **Manual backup button** — snapshot the current save on demand.
5. **Per-game auto-backup-on-exit toggle** — steamback-style automatic snapshot when a game closes, switchable per game.
6. **Google Drive backup** — one-way mirror of the kept versions as **real, browsable files**, with **device-code login** (no desktop browser needed). Uses the **user's own** Google OAuth client.

### 1.2 Non-goals (explicitly cut — YAGNI)

These were considered and **deliberately dropped** as over-engineering for a single-device personal plugin with normal-sized saves:

- **Content-addressed / dedup blob store** (files keyed by hash), garbage collection, delta uploads. Saves are small and change infrequently; dedup's payoff here is ~nil and the complexity is high. *(M3 does record a per-file `sha256` in each version's manifest, but only as a change-detection tiebreaker — not as a content-addressed store.)*
- **Multi-device two-way sync** / conflict resolution. Single device only; Drive is backup, not sync.
- **Non-Steam / emulator / arbitrary-folder games.** Steam Cloud games only (those with a `remotecache.vdf`).
- **Hybrid efficient/readable Drive export.** Drive stores plain full-copy real files.

> **Future option (documented, not built):** if a game ever has a *single large* save file that changes every session, full-copy storage costs one full copy per version. The escape hatch is content-defined chunking behind the same `store` interface — see §12. We do not build it now.

---

## 2. Architecture

Standard modern Decky plugin (scaffolded from the official `decky-plugin-template`, **not** steamback's removed `decky-frontend-lib`/`ServerAPI` code).

```
┌─────────────────────────────────────────────────────────┐
│  Frontend  (TypeScript / React, @decky/ui + @decky/api)  │
│  QAM panel: game list, version list, pin/rename/delete,  │
│  revert, manual backup, per-game toggles, retention      │
│  slider, Drive status + device-code display.             │
│  Registers RegisterForAppLifetimeNotifications.          │
└───────────────┬───────────────────────────▲─────────────┘
        callable() RPC (positional)   decky.emit() events
┌───────────────▼───────────────────────────┴─────────────┐
│  Backend  (Python 3.11, main.py → class Plugin)          │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐            │
│  │ discovery  │ │ versioning │ │   store    │            │
│  │ remotecache│ │ HEAD/pins/ │ │ full-copy  │            │
│  │ .vdf, roots│ │ revert/    │ │ snapshots, │            │
│  │            │ │ retention  │ │ atomic I/O │            │
│  └────────────┘ └────────────┘ └────────────┘            │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐            │
│  │   drive    │ │   config   │ │  locking   │            │
│  │ device-auth│ │ per-game + │ │ per-game   │            │
│  │ mirror,    │ │ global     │ │ flock      │            │
│  │ index.json │ │ settings   │ │            │            │
│  └────────────┘ └────────────┘ └────────────┘            │
└──────────────────────────────────────────────────────────┘
```

### 2.1 Backend modules (single clear responsibility each)

| Module | Responsibility |
|---|---|
| `discovery.py` | Parse `remotecache.vdf`; resolve save roots; list/validate a game's save files; detect "supported" games. (Ported & lightly improved from steamback's engine.) |
| `store.py` | Full-copy snapshot create/read/delete; atomic file copy (temp+rename); version directory layout; per-version `meta.json`. |
| `versioning.py` | Backup orchestration, git-like revert (movable HEAD + pre-revert auto-snapshot), pin/rename, retention (count cap, pins protected-but-counted). Owns `refs.json`. |
| `drive.py` | Device-code OAuth (user's own client), token refresh, resumable upload, one-way mirror reconciliation, remote `index.json`. All HTTPS in-process via `requests` + `certifi`. |
| `config.py` | Global + per-game settings via Decky's `SettingsManager` (`DECKY_PLUGIN_SETTINGS_DIR`). |
| `locking.py` | Per-game `flock` to serialize the game-exit hook vs. manual UI operations. |
| `main.py` | Thin `class Plugin` exposing async methods to the frontend; schedules long work on the event loop; emits progress events. |

### 2.2 Frontend (`src/index.tsx` + small components)

`@decky/ui` components: `PanelSection`, `ButtonItem`, `ToggleField` (auto-backup, per-game Drive mirror), `SliderField` (retention cap), `DropdownItem`/list rows for games & versions, `TextField` (rename/pin), `showModal`+`ConfirmModal` (destructive revert/delete), `ProgressBarWithInfo` (Drive upload), `Navigation`. Registers `SteamClient.GameSessions.RegisterForAppLifetimeNotifications`; disables revert while the app is in `Router.RunningApps`.

### 2.3 Tech stack & constraints

- `@decky/api ^1.1.x`, `@decky/ui ^4.11.x`, `@decky/rollup`, **pnpm v9**, Node 16.14+, TypeScript ^5.6.
- Python **3.11** (Decky's bundled interpreter). Pure-Python deps only: vendor `requests` + `certifi` (+ their pure-python transitive deps) under `defaults/py_modules/` (steamback's vendoring pattern) or via `requirements.txt` for the Docker builder.
- `plugin.json` `flags: []` — **no root** (userdata is owned by the `deck` user). Writing as root would create root-owned files and break Steam.
- **HTTPS only in-process.** Never shell out to `curl`/`rclone`/`git` over HTTPS — Decky's bundled OpenSSL breaks system binaries (decky-loader issue #729). Use `certifi.where()` for the CA bundle.
- **Never block an RPC call** on a long upload (decky-loader issue #158). Long work runs via `self.loop.create_task(...)`; progress streams back through `decky.emit(...)`.

---

## 3. Data model (on-disk, the Deck)

Root: `$DECKY_PLUGIN_RUNTIME_DIR` (≈ `~/homebrew/data/SaveManager/`). Per game, **full-copy** snapshots:

```
config.json                              # global defaults (keepCount, drive settings)
games/<appId>/
  game.json                              # metadata + per-game settings + cached saveRoots
  refs.json                              # MUTABLE: HEAD + pins + version list  (single commit point)
  refs.json.bak                          # previous refs.json (recovery)
  drive-state.json                       # local cache of Drive fileIds (rebuildable from remote index.json)
  versions/<versionId>/                   # a full copy of the save, one subtree per save-root suffix
    root/      …relative save files…     # suffix "" → "root", "_1" → "root_1", …
    root_1/    …                          #   (mirrors steamback's multi-root suffix scheme)
    meta.json                            # this version's saveRoots map + file list (size, mtime)
```

`versionId = v_<unixMillis>_<6hex>` (time-sortable, random suffix avoids same-ms collision).

### 3.1 `game.json`
```json
{
  "appId": 281990,
  "name": "XCOM 2",
  "saveRoots": { "/home/deck/.local/share/Steam/userdata/<acct>/281990/remote": "" },
  "settings": { "autoBackupOnExit": true, "keepCount": 20, "driveMirror": true, "ignoreUnchanged": true },
  "lastDiscoveredAt": 1718600000000,
  "schemaVersion": 1
}
```

### 3.2 `versions/<versionId>/meta.json` (written once, with the copy)
```json
{
  "versionId": "v_1718600000000_a1b2c3",
  "appId": 281990,
  "createdAt": 1718600000000,
  "kind": "auto",                       // auto | manual
  "reason": "game-exit",                // game-exit | manual | pre-revert-autosnapshot
  "parent": "v_1718500000000_998877",   // HEAD when captured (null for first)
  "saveRoots": { "": "/home/deck/.local/.../281990/remote" },
  "files": [
    { "suffix": "", "path": "XComGame/SaveData/save1.sav", "size": 1048576, "mtime": 1718599990000, "sha256": "ab34…ef" }
  ],
  "fileCount": 2, "totalBytes": 1064317,
  "schemaVersion": 1
}
```

### 3.3 `refs.json` (the ONLY transactionally-written file)
```json
{
  "appId": 281990,
  "head": { "versionId": "v_1718600000000_a1b2c3", "detached": false },
  "pendingRevertTo": null,
  "versions": [
    { "versionId": "v_1718600000000_a1b2c3", "createdAt": 1718600000000, "kind": "auto",
      "reason": "game-exit", "parent": "v_...998877", "pinned": false, "name": null,
      "fileCount": 2, "totalBytes": 1064317 },
    { "versionId": "v_...998877", "createdAt": 1718500000000, "kind": "manual",
      "reason": "manual", "parent": null, "pinned": true, "name": "Before final boss",
      "fileCount": 2, "totalBytes": 1050001 }
  ],
  "updatedAt": 1718600005000,
  "schemaVersion": 1
}
```

**Semantics**
- **Version** = an entry in `versions[]` + its `versions/<id>/` folder. Newest first by `createdAt`.
- **Pinned** ⇔ `pinned == true` (protected from deletion). A **pin** = `pinned:true` + a user `name`. `kind`/`reason` are provenance only.
- **HEAD** = `head.versionId` = the version whose files are currently materialized in the live save dir. `detached == false` when HEAD is the newest version (new snapshots follow the tip); `true` after reverting to an older version (new snapshots branch, linked via `meta.parent`).
- `pendingRevertTo` lets an interrupted revert finish idempotently on next launch.

> `pinned`/`name` live **only** in `refs.json`, so pinning/renaming never rewrites a version's `meta.json` or its files.

---

## 4. Save discovery

Port steamback's engine (`defaults/py_modules/steamback/__init__.py`), with two improvements. Steam root is `~/.local/share/Steam`. Account id (32-bit Steam3) comes from the frontend: `App.m_CurrentUser.strSteamID` → `SteamID().accountid`.

Per game, locate `userdata/<accountid>/<appId>/remotecache.vdf`, then:

1. **Parse with a real KeyValues parser** (improvement over steamback's filename-only line scanner) and **keep per-file metadata** (`root` enum, `size`, `time`/`remotetime`). Each entry's key is the cloud-relative path.
2. **Resolve paths via the `root` enum first** (improvement): `0`→`userdata/<id>/<appId>/remote/`, `1`→`steamapps/common/<installdir>/`, `2/3/9/11/12`→Proton prefix `compatdata/<appId>/pfx/drive_c/users/steamuser/{Documents|AppData/Local|Saved Games|Documents/Steam Cloud|AppData/LocalLow}/`.
3. **Validate** each candidate root against the actual file list — accept only if ≥1 listed file exists on disk (steamback's `_rcf_is_valid`).
4. **Fall back to steamback's heuristics** when the enum mapping doesn't validate (the mapping is reverse-engineered): `remote/` + `ac/LinuxXdgDataHome` fast paths; native+Proton "likely locations"; `rglob('steam_autocloud.vdf')` + longest-common-prefix trimming.
5. Store results as ordered `saveRoots {absDir: suffix}` (`""`, `_1`, …) and **cache them in `game.json`** to skip the expensive `rglob` on every backup.
6. **"Supported"** = `remotecache.vdf` exists AND ≥1 save file located. Isolate per-game exceptions so one bad game (e.g. unmounted SD → `FileNotFoundError`) never aborts the whole scan.

**Installed-games list & names** come from the frontend (`SteamClient.InstallFolder.GetInstallFolders()`, `appStore.GetAppOverviewByGameID(appId).display_name`), filtered through a backend `find_mounted` check to drop unmounted SD cards.

### 4.1 Discovery edge cases (handle explicitly)
- Reverse-engineered `root` enum may drift → always validate, keep heuristic fallback.
- SD-card games whose saves live on internal storage (`/run…` prefix → also search the system dir).
- Multiple Steam accounts (`userdata/*` numeric set).
- Games not yet cloud-synced (no `remotecache.vdf`) → simply not "supported" until first sync (accepted gap).
- Proton `steamuser` vs older `xuser`; filenames containing `/` (guard the prefix logic).

---

## 5. Core operations

### 5.1 Backup (manual button + auto-on-exit)
1. Acquire the per-game lock.
2. Discover/refresh save roots; list current save files.
3. **Skip-if-unchanged** (optional, configurable): compare each live file's `size`+`mtime` against the current HEAD version's `meta.json`. If identical, no-op (avoids duplicate snapshots).
4. Create `versions/<newId>/`, copy every save file (`shutil.copy2`, atomic temp+rename) into the suffix subtree, write `meta.json` (`parent = head`).
5. Update `refs.json`: prepend the new version, set `head = newId`, `detached = false`. **`refs.json` is written last** (single commit point), keeping a `.bak`.
6. Run retention (§5.4). Release the lock.
7. If `driveMirror` is on, schedule a Drive sync (non-blocking).

**Auto-on-exit:** the frontend's `RegisterForAppLifetimeNotifications` fires `do_backup(appId)` when `bRunning` becomes `false` *and* the game's `autoBackupOnExit` is on. A short **debounce** (poll until `remotecache.vdf` mtime settles, ~2–5 s) avoids racing Steam's post-exit cloud sync (§7).

### 5.2 Revert (git-like, movable HEAD)
`revertTo(versionId)`:
1. **Guard:** refuse if the game is in `Router.RunningApps` (checked frontend-side; backend double-checks). Confirm via `ConfirmModal` (destructive).
2. **Fingerprint** the live save dir (size+mtime of listed files) vs HEAD's `meta.json`. If the live state differs (you played since the last snapshot), **first take a normal snapshot** (`reason = pre-revert-autosnapshot`, `parent = head`) → live progress is never lost. (This is steamback's "undo" generalized into a real, listed, revertable version.)
3. Set `refs.pendingRevertTo = target`; persist `refs.json`.
4. **Materialize target:** for each file in target's `meta.json`, atomically copy from `versions/<target>/…` into the live save location (temp+rename). Then delete only **managed** live files (present in the old HEAD's meta or the rcf list, absent from target) so the live dir exactly equals the target. **Never touch unmanaged files.**
5. Set `head.versionId = target`, `head.detached = (target != newest)`, clear `pendingRevertTo`; persist `refs.json`.
6. `Navigation.Navigate('/library/app/<appId>')`.

Because history is never deleted by reverting and each version is a complete snapshot, you can move HEAD **backward and forward** to any version/pin repeatedly (git `checkout`, not `reset`). **Crash-safe:** on startup, if `pendingRevertTo != null`, re-run steps 4–5 (target already fully present on disk).

### 5.3 Pin / rename / delete
- **Pin/unpin:** flip `pinned` on the `refs` entry. **Rename:** set `name`. Both touch only `refs.json`.
- **Delete (manual):** allowed on any version except the current HEAD; drop from `refs.versions[]`, then `rmtree(versions/<id>/)`. Confirm if pinned.

### 5.4 Retention (count cap, pins counted-but-protected)
- `keepCount` (per game, configurable; global default in `config.json`).
- Pinned versions **count toward** `keepCount` but are **never** auto-deleted; HEAD is never auto-deleted.
- If `len(versions) > keepCount`: delete `len − keepCount` versions from `{ pinned == false AND != HEAD }`, **oldest first**.
- If protected count (pins + HEAD) ≥ `keepCount`: keep all protected, surface a UI warning *"pinned versions exceed keep cap"* (effective floor = `max(keepCount, protectedCount)`).

---

## 6. Google Drive backup (one-way mirror, user's own client)

**Decision:** native Drive REST API in the Python backend (not rclone — rclone can't do device-code phone auth). **Scope `https://www.googleapis.com/auth/drive.file`** (non-sensitive: no Google app-verification, files visible in the user's My Drive). **The user supplies their own OAuth client** (see §11).

### 6.1 Device-code OAuth
1. `POST https://oauth2.googleapis.com/device/code` (client_id + scope) → `{device_code, user_code, verification_url, interval, expires_in}`.
2. Backend `emit`s `user_code` + `https://www.google.com/device` to the QAM (rendered as text **and** a QR code).
3. Poll `POST https://oauth2.googleapis.com/token` (`grant_type=urn:ietf:params:oauth:grant-type:device_code`) at `interval`; handle `authorization_pending` (keep polling), `slow_down` (+5 s), `access_denied`, `expired_token` (restart).
4. On success, persist the **refresh_token** in `DECKY_PLUGIN_SETTINGS_DIR` with restrictive perms. Refresh the access token on 401; on `invalid_grant` (revoked / expired), surface a "re-link account" device-code prompt.

### 6.2 Remote layout (browsable real files)
```
SteamDeckSaveManager/
  <Game Name>/
    2026-06-17 14-30 (auto)/      …real save files, original relative paths…
    Before final boss (pinned)/   …real save files…
    index.json                    # versionId → { label, driveFolderId, fileIds, pinned }
```
`index.json` is the **remote commit point** (Drive has no path addressing or multi-file transaction): it records what's uploaded and the opaque Drive `fileId`s. A local `drive-state.json` caches the same map and is rebuildable from `index.json`.

### 6.3 Mirror algorithm (idempotent, index-committed-last)
1. Compute the local kept set from `refs.json`.
2. Read remote `index.json` (or rebuild by listing the game folder).
3. For each kept version **not** in the index: create its Drive folder, upload its files (`uploadType=resumable` for large files, `media` for small), record `fileId`s.
4. **Update `index.json` last** (`files.update`).
5. **Prune** remote versions no longer in the local kept set **after** the index update (so an interrupted prune never orphans a referenced file). Configurable: permanent delete vs. Drive-trash window (default: **trash**, safer for a backup tool).

Runs as a non-blocking `loop.create_task`; progress streams via `decky.emit('drive_progress', uploaded, total, currentFile)`.

---

## 7. Error handling & edge cases

| Concern | Handling |
|---|---|
| **Revert while game running** | Disable revert when app ∈ `Router.RunningApps` (frontend) + backend guard. |
| **Save-flush race on exit** | `bRunning=false` is process death, not cloud-sync completion. Debounce: poll until `remotecache.vdf` mtime settles (~2–5 s) before snapshotting. Forced kill/Alt-F4 may leave saves un-synced — accepted. |
| **Steam Cloud conflict on revert** | Restoring an old save while Cloud is enabled can trigger a re-download/conflict that undoes the revert. Mitigation: after writing files, bump their mtimes so the local copy is newest; surface a one-time tip suggesting the game's Cloud be paused during heavy revert use. Document the limitation (no Decky "sync finished" event exists). |
| **Partial write / crash** | Atomic temp+fsync+rename for every copied file and for `refs.json` (+ `refs.json.bak`). `refs.json` written **last** → it never references an incomplete version. `pendingRevertTo` makes an interrupted revert re-runnable. |
| **`refs.json` + `.bak` lost** | Rebuild `versions[]` by scanning `versions/*/meta.json`; default HEAD=newest; pins lost (also recoverable from Drive `index.json` if mirrored). Data-safe. |
| **Unmounted SD / FileNotFound** | Per-game try/except in discovery; `find_mounted` filter. |
| **Drive: token expiry / 401** | Refresh access token; on hard failure, re-link prompt. |
| **Drive: rate limit 429/403** | Exponential backoff + jitter. |
| **Drive: interrupted upload** | Resumable session (resume via `Content-Range`); 404 session → restart that file. Index updated only after files land. |
| **HTTPS from Decky** | In-process `requests` + `certifi`; never shell to curl/rclone (issue #729). |
| **Concurrent ops** | Per-game `flock` serializes exit-hook backup vs. UI actions. |
| **Steam internal APIs** | `RegisterForAppLifetimeNotifications`, `GetAppOverviewByGameID`, `GetInstallFolders`, `Router.RunningApps` are undocumented → wrap in try/except + feature-detection. |

---

## 8. Frontend UX (QAM panel)

- **Games section** — list of supported games (icon + name + version count + "auto" badge if exit-backup on). Tap → game detail.
- **Game detail**
  - **Back up now** button.
  - **Auto-backup on exit** toggle · **Mirror to Drive** toggle · **Keep last N** slider.
  - **Versions** list (newest first): each row shows time, label/pin (★), and HEAD marker (●). Row actions: **Restore** (revert, confirm), **Pin/Unpin**, **Rename**, **Delete** (confirm). "Pinned versions exceed keep cap" warning when applicable.
- **Drive section** — account status; **Link account** → shows `user_code` + QR + URL; **Sync now**; upload progress bar; last-sync time.
- Destructive actions use `ConfirmModal` with `bDestructiveWarning`.

---

## 9. Testing strategy

- **Off-device unit tests (pure-Python engine).** Like steamback's `tests/` + desktop mode, the backend engine must run off the Deck. Fixtures: a fake `userdata` tree + sample `remotecache.vdf` (native, Proton, multi-root, autocloud variants).
  - `discovery`: path resolution per `root` enum + each fallback; validation; multi-account; unmounted SD; no-rcf game.
  - `store`/`versioning`: backup creates correct copy + `meta.json`; skip-if-unchanged; retention math (cap, pins counted-but-protected, HEAD protected); pin/rename/delete.
  - `revert`: pre-revert auto-snapshot when live differs; movable HEAD back+forward; managed-file deletion never touches unmanaged files; crash-resume via `pendingRevertTo`.
  - `drive`: mirror reconciliation, index-last invariant, prune-after-index, against a mock HTTP server; device-code polling state machine (`authorization_pending`/`slow_down`).
- **Manual on-device test plan.** Install via rsync + `systemctl restart plugin_loader.service`; verify exit-backup on a real Cloud game, revert round-trip, Drive link + browse the real files in My Drive, restore after simulating a wipe.

---

## 10. Milestones (suggested phasing)

1. **M1 — Local versioning core:** scaffold plugin; discovery; full-copy backup; version list UI; manual backup button.
2. **M2 — Revert & curation:** git-like revert (HEAD + auto-snapshot); pin/rename/delete; count retention.
3. **M3 — Automation:** per-game auto-backup-on-exit toggle + exit debounce.
4. **M4 — Drive backup:** device-code OAuth (user's client); one-way mirror of real files + `index.json`; restore-from-Drive.

Each milestone is independently useful and testable.

---

## 11. Appendix — creating your own Google OAuth client (one-time)

The plugin uses **your** Google client so no shared app/verification is involved.

1. In [Google Cloud Console](https://console.cloud.google.com/): create (or pick) a project.
2. **APIs & Services → Library →** enable **Google Drive API**.
3. **OAuth consent screen:** User type **External**; fill the minimal required fields; add the scope `.../auth/drive.file`. **Set Publishing status to "In production"** — critical: in "Testing", refresh tokens **expire after 7 days**. (Because `drive.file` is non-sensitive, "In production" needs **no** Google verification.)
4. **Credentials → Create credentials → OAuth client ID → Application type: "TVs and Limited Input devices."** Copy the **client ID** and **client secret**.
5. Enter them in the plugin's Drive settings (stored under `DECKY_PLUGIN_SETTINGS_DIR`, git-ignored, never committed). The plugin then runs the device-code flow with your client.

---

## 12. Future options (not in scope)

- **Content-defined chunking** behind the `store` interface — only if a game shows a large save mutating every session (restores the dedup win without changing versioning/discovery/Drive contracts).
- **Restore-from-Drive UI** for a fresh Deck (browse remote `index.json`, pull a version).
- **Other cloud targets** (any rclone-style backend) — out of scope; Drive only.
```
