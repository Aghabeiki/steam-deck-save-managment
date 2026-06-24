# Save Manager — M5 Restore-from-Drive — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user pull a Drive-mirrored save version back down into the local version store (e.g. on a fresh/wiped Deck), where it appears in the normal version list and can be reverted into the live save.

**Architecture:** Non-destructive: restore = download a version's files from Drive and reconstruct it as a LOCAL version (store dir + `meta.json` + a `refs.json` entry), WITHOUT changing HEAD — the existing M2 revert then materializes it into the live save. Builds on the M4 Drive client + remote `index.json`. New: `mirror.list_remote_versions`/`download_version`, `versioning.import_version`, Engine `list_remote_versions_with_client`/`restore_from_drive_with_client`, Decky wiring, and a "Restore from Drive" QAM affordance. Engine pieces are fake-transport tested (incl. a full backup→Drive→restore round-trip); the Decky/UI glue is `py_compile`/`tsc`-verified.

**Tech Stack:** Python 3.11, pytest. Frontend: TypeScript/React, `@decky/api`/`@decky/ui`.

**Reference:** Spec §6 + the M4a/M4b "After" roadmap ("Restore-from-Drive UI — download_file + index already in place"). The remote `index.json` shape is `{appId, gameFolderId, versions: {vid: {label, folderId, fileIds: {relpath: fileId}, pinned}}, schemaVersion}`; `fileIds` keys are suffix-qualified relpaths (`root<suffix>/<path>`).

---

## Existing code this builds on

- `drive.py` `DriveClient`: `download_file(file_id) -> bytes`, `list_children(parent_id)`, `find_or_create_folder`, `refresh_access_token`. `DriveAuthError`.
- `mirror.py`: `read_index(client, game_folder_id) -> (index|None, file_id|None)`, `write_index`, `sync_game`, `sync_versions`, `empty_index`, `plan_sync`.
- `versioning.py`: `do_backup`, `list_versions`, `load_version_files(data_root, app_id, vid) -> {root<suffix>/<path>: bytes}`, `kept_versions_for`; imports `os`, and `from .store import …, _hash_file, version_dir`, `from .refs import read_refs, write_refs, make_version_entry`. (Does NOT import `json` yet.)
- `api.Engine`: `sync_drive_with_client(app_id, game_name, client, root_folder_id)`, `_read_secrets`, `get_drive_status`, `set_drive_refresh_token`. Imports `os`, `json`, `from .mirror import sync_game`, `from .versioning import kept_versions_for, load_version_files`.
- `main.py`: `_drive_http()`, `_DRIVE_ROOT_FOLDER`, `DriveClient`, `drive_mod`, `self._drive_lock` (asyncio.Lock from `_main`), `_do_sync_drive`, `_maybe_mirror`, `get_engine()`.
- `tests/drive_fakes.py` `FakeDriveClient`: `folders`, `files`, `create_folder`, `find_or_create_folder`, `upload_file`, `list_children`, `download_file`, `update_file`, `trash_file`.
- `src/index.tsx`: `Content` (game-detail view + `refresh(selected)`), `DriveSection`, the `GameInfo`/`VersionEntry`/`Listing` interfaces, callables, and the `addEventListener`/`toaster` imports.

---

## File Structure (M5)

| File | Change | Responsibility |
|---|---|---|
| `defaults/py_modules/savemanager/mirror.py` | Modify | `list_remote_versions(index)` + `download_version(client, index, version_id)`. |
| `defaults/py_modules/savemanager/versioning.py` | Modify | `import_version(...)` — reconstruct a local version from downloaded bytes (no HEAD change). |
| `defaults/py_modules/savemanager/api.py` | Modify | `Engine.list_remote_versions_with_client` + `restore_from_drive_with_client`. |
| `main.py` | Modify | `_drive_client_and_root` helper; Plugin `list_remote_versions` + `restore_from_drive`. |
| `src/index.tsx` | Modify | "Restore from Drive" affordance (list remote-only versions + download). |
| `tests/test_mirror.py`, `tests/test_restore.py`, `tests/test_engine_drive.py` | Create/modify | Unit tests incl. the round-trip. |

---

## Task 1: `list_remote_versions` + `download_version`

**Files:** Modify `defaults/py_modules/savemanager/mirror.py`; Test `tests/test_mirror.py` (append).

- [ ] **Step 1: Append the failing tests to `tests/test_mirror.py`:**
```python
from savemanager.mirror import list_remote_versions, download_version


def test_list_remote_versions_from_index():
    idx = {"versions": {"v1": {"label": "L1", "folderId": "f1", "fileIds": {}, "pinned": True},
                        "v2": {"label": "L2", "folderId": "f2", "fileIds": {}, "pinned": False}}}
    out = list_remote_versions(idx)
    assert {v["versionId"] for v in out} == {"v1", "v2"}
    assert next(v for v in out if v["versionId"] == "v1")["pinned"] is True
    assert next(v for v in out if v["versionId"] == "v2")["label"] == "L2"


def test_download_version_fetches_each_file():
    client = FakeDriveClient()
    fid = client.upload_file("x", "folder", b"BYTES")
    idx = {"versions": {"v1": {"label": "L", "folderId": "folder",
                               "fileIds": {"root/s.sav": fid}, "pinned": False}}}
    assert download_version(client, idx, "v1") == {"root/s.sav": b"BYTES"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mirror.py::test_list_remote_versions_from_index -v`
Expected: FAIL (`ImportError: cannot import name 'list_remote_versions'`).

- [ ] **Step 3: Append to `mirror.py`:**
```python
def list_remote_versions(index) -> list:
    """Summaries of the versions present in a remote index.json."""
    return [{"versionId": vid, "label": v.get("label", vid), "pinned": v.get("pinned", False)}
            for vid, v in index.get("versions", {}).items()]


def download_version(client, index, version_id) -> dict:
    """Download a remote version's files as {suffix-qualified relpath: bytes}."""
    entry = index["versions"][version_id]
    return {relpath: client.download_file(file_id) for relpath, file_id in entry["fileIds"].items()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mirror.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add defaults/py_modules/savemanager/mirror.py tests/test_mirror.py
git commit -m "feat: list_remote_versions + download_version (Drive restore reads)"
```

---

## Task 2: `import_version` — reconstruct a local version from bytes

**Files:** Modify `defaults/py_modules/savemanager/versioning.py`; Test `tests/test_restore.py`.

- [ ] **Step 1: Write the failing test `tests/test_restore.py`:**
```python
import os
from savemanager.versioning import import_version, list_versions
from savemanager.store import read_meta, version_dir


def test_import_version_reconstructs_local_version(tmp_path):
    data_root = os.path.join(str(tmp_path), "data")
    files = {"root/save1.sav": b"AAAAA", "root_1/x/y.sav": b"BB"}
    entry = import_version(data_root, 281990, "v_1000_aaa", "Before boss", True, files)
    assert entry["versionId"] == "v_1000_aaa" and entry["pinned"] is True
    assert entry["name"] == "Before boss" and entry["createdAt"] == 1000
    vdir = version_dir(data_root, 281990, "v_1000_aaa")
    with open(os.path.join(vdir, "root", "save1.sav"), "rb") as f:
        assert f.read() == b"AAAAA"
    with open(os.path.join(vdir, "root_1", "x", "y.sav"), "rb") as f:
        assert f.read() == b"BB"
    # appears in the list with a real meta (sha256 + suffix/path), HEAD unchanged
    listing = list_versions(data_root, 281990)
    assert [v["versionId"] for v in listing["versions"]] == ["v_1000_aaa"]
    assert listing["head"]["versionId"] is None
    meta = read_meta(data_root, 281990, "v_1000_aaa")
    assert all(len(f["sha256"]) == 64 for f in meta["files"])
    assert {(f["suffix"], f["path"]) for f in meta["files"]} == {("", "save1.sav"), ("_1", "x/y.sav")}


def test_import_version_is_idempotent(tmp_path):
    data_root = os.path.join(str(tmp_path), "data")
    import_version(data_root, 1, "v_1_a", "L", False, {"root/s.sav": b"X"})
    import_version(data_root, 1, "v_1_a", "L", False, {"root/s.sav": b"X"})
    assert len(list_versions(data_root, 1)["versions"]) == 1     # no duplicate entry


def test_import_version_label_equal_to_id_means_no_name(tmp_path):
    data_root = os.path.join(str(tmp_path), "data")
    entry = import_version(data_root, 1, "v_2_b", "v_2_b", False, {"root/s.sav": b"X"})
    assert entry["name"] is None                                  # label == versionId -> no user name
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_restore.py -v`
Expected: FAIL (`ImportError: cannot import name 'import_version'`).

- [ ] **Step 3: Modify `versioning.py`.** Add `import json` at the top (with the existing `import os`). Then append:
```python
def import_version(data_root, app_id, version_id, label, pinned, files) -> dict:
    """Reconstruct a LOCAL version from Drive-downloaded {suffix-qualified relpath: bytes}.
    Writes the files + meta.json and adds a refs entry. Does NOT change HEAD (the user reverts
    to it to materialize it into the live save). Idempotent on the version id. Returns the entry."""
    vdir = version_dir(data_root, app_id, version_id)
    os.makedirs(vdir, exist_ok=True)
    meta_files = []
    total = 0
    for relpath, content in files.items():
        parts = relpath.split("/", 1)
        if len(parts) < 2:
            continue                                  # not 'root<suffix>/<path>' -> skip
        seg0, path = parts
        suffix = seg0[len("root"):] if seg0.startswith("root") else ""
        dst = os.path.join(vdir, seg0, path)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as fh:
            fh.write(content)
        st = os.stat(dst)
        meta_files.append({"suffix": suffix, "path": path, "size": st.st_size,
                           "mtime": int(st.st_mtime * 1000), "sha256": _hash_file(dst)})
        total += st.st_size

    bits = version_id.split("_")
    created_at = int(bits[1]) if len(bits) >= 3 and bits[1].isdigit() else 0
    meta = {"versionId": version_id, "appId": app_id, "createdAt": created_at,
            "kind": "import", "reason": "drive-restore", "parent": None,
            "saveRoots": {}, "files": meta_files, "fileCount": len(meta_files),
            "totalBytes": total, "schemaVersion": 1}
    tmp = os.path.join(vdir, "meta.json.tmp")
    with open(tmp, "w") as f:
        json.dump(meta, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, os.path.join(vdir, "meta.json"))

    entry = {"versionId": version_id, "createdAt": created_at, "kind": "import",
             "reason": "drive-restore", "parent": None, "pinned": bool(pinned),
             "name": (label if label != version_id else None),
             "fileCount": len(meta_files), "totalBytes": total}
    refs = read_refs(data_root, app_id)
    if not any(v["versionId"] == version_id for v in refs["versions"]):
        refs["versions"].append(entry)
        refs["versions"].sort(key=lambda v: v["createdAt"], reverse=True)    # keep newest-first
        write_refs(data_root, app_id, refs)
    return entry
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_restore.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add defaults/py_modules/savemanager/versioning.py tests/test_restore.py
git commit -m "feat: import_version — reconstruct a local version from Drive bytes (no HEAD change)"
```

---

## Task 3: Engine restore composition + backup→Drive→restore round-trip

**Files:** Modify `defaults/py_modules/savemanager/api.py`; Test `tests/test_engine_drive.py` (append).

- [ ] **Step 1: Append the failing tests to `tests/test_engine_drive.py`:**
```python
def test_list_remote_versions_empty_when_no_game_folder(tmp_path):
    eng, _ = _engine(tmp_path)
    assert eng.list_remote_versions_with_client("Nope", FakeDriveClient(), "ROOT") == []


def test_drive_backup_then_restore_round_trip(tmp_path):
    from savemanager.versioning import do_backup, list_versions, load_version_files
    steam_root, acct, app = make_steam_tree(tmp_path)
    engA = Engine(os.path.join(str(tmp_path), "A"), steam_root); engA.set_account_id(acct)
    do_backup(engA.data_root, steam_root, acct, {"appId": app, "name": "XCOM 2"},
              now_ms=1000, rand_hex="aaa")
    client = FakeDriveClient()
    engA.sync_drive_with_client(app, "XCOM 2", client, "ROOT")              # mirror to (fake) Drive

    engB = Engine(os.path.join(str(tmp_path), "B"), steam_root); engB.set_account_id(acct)
    remote = engB.list_remote_versions_with_client("XCOM 2", client, "ROOT")
    assert [v["versionId"] for v in remote] == ["v_1000_aaa"]
    engB.restore_from_drive_with_client(app, "XCOM 2", "v_1000_aaa", client, "ROOT")
    assert [v["versionId"] for v in list_versions(engB.data_root, app)["versions"]] == ["v_1000_aaa"]
    assert load_version_files(engB.data_root, app, "v_1000_aaa") == \
        {"root/save1.sav": b"AAAAA", "root/profile.bin": b"BBBB"}            # bytes restored exactly
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_engine_drive.py::test_drive_backup_then_restore_round_trip -v`
Expected: FAIL (`AttributeError: 'Engine' object has no attribute 'list_remote_versions_with_client'`).

- [ ] **Step 3: Extend `Engine` in `api.py`.** Add to the imports — the existing line `from .mirror import sync_game` becomes:
```python
from .mirror import download_version, list_remote_versions, read_index, sync_game
```
and the existing `from .versioning import kept_versions_for, load_version_files` becomes:
```python
from .versioning import import_version, kept_versions_for, load_version_files
```
Add these methods to the `Engine` class (after `sync_drive_with_client`):
```python
    def _find_game_folder(self, client, root_folder_id, game_name):
        return next((c["id"] for c in client.list_children(root_folder_id)
                     if c["name"] == game_name), None)

    def list_remote_versions_with_client(self, game_name, client, root_folder_id) -> list:
        folder = self._find_game_folder(client, root_folder_id, game_name)
        if folder is None:
            return []
        index, _ = read_index(client, folder)
        return list_remote_versions(index) if index else []

    def restore_from_drive_with_client(self, app_id, game_name, version_id, client, root_folder_id):
        folder = self._find_game_folder(client, root_folder_id, game_name)
        if folder is None:
            return None
        index, _ = read_index(client, folder)
        if not index or version_id not in index.get("versions", {}):
            return None
        meta = index["versions"][version_id]
        files = download_version(client, index, version_id)
        return import_version(self.data_root, app_id, version_id,
                              meta.get("label", version_id), meta.get("pinned", False), files)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_engine_drive.py -v`
Expected: PASS (the round-trip + the empty case + the earlier engine-drive tests).

- [ ] **Step 5: Commit**

```bash
git add defaults/py_modules/savemanager/api.py tests/test_engine_drive.py
git commit -m "feat: Engine list/restore remote versions; backup->Drive->restore round-trip"
```

---

## Task 4: Decky wiring (list remote + restore)

**Files:** Modify `main.py`. Verified by `python -m py_compile`.

- [ ] **Step 1: Add a `_drive_client_and_root` helper** to the `Plugin` class (after the M4b `_maybe_mirror` method):
```python
    def _drive_client_and_root(self):
        secrets = get_engine()._read_secrets()
        http = _drive_http()
        access = drive_mod.refresh_access_token(http, secrets["client_id"],
                                                secrets["client_secret"], secrets["refresh_token"])
        client = DriveClient(http, access)
        return client, client.find_or_create_folder(_DRIVE_ROOT_FOLDER, "root")
```

- [ ] **Step 2: Add the `Plugin` methods** (after `_do_sync_drive`):
```python
    async def list_remote_versions(self, game_info: dict) -> list:
        try:
            client, root = self._drive_client_and_root()
            return get_engine().list_remote_versions_with_client(game_info["name"], client, root)
        except drive_mod.DriveAuthError:
            get_engine().set_drive_refresh_token(None)
            await decky.emit("drive_needs_relink", game_info.get("appId"))
            return []
        except Exception as e:
            decky.logger.error(f"SaveManager list remote versions failed: {e}")
            return []

    async def restore_from_drive(self, game_info: dict, version_id: str):
        self.loop.create_task(self._do_restore_from_drive(game_info, version_id))
        return None

    async def _do_restore_from_drive(self, game_info: dict, version_id: str):
        async with self._drive_lock:
            try:
                client, root = self._drive_client_and_root()
                get_engine().restore_from_drive_with_client(game_info["appId"], game_info["name"],
                                                            version_id, client, root)
                await decky.emit("drive_restore_done", game_info["appId"], version_id)
            except drive_mod.DriveAuthError:
                get_engine().set_drive_refresh_token(None)
                await decky.emit("drive_needs_relink", game_info.get("appId"))
            except Exception as e:
                decky.logger.error(f"SaveManager drive restore failed: {e}")
                await decky.emit("drive_restore_error", game_info.get("appId"), str(e))
```

- [ ] **Step 3: Validate syntax**

Run: `python -m py_compile main.py && echo OK`
Expected: "OK".

- [ ] **Step 4: Run full suite + compile**

Run: `python -m pytest -q && python -m py_compile main.py && echo OK`
Expected: all pass; "OK".

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "feat: Decky list_remote_versions + non-blocking restore_from_drive"
```

---

## Task 5: QAM "Restore from Drive" UI

**Files:** Modify `src/index.tsx`. Verified by `tsc` + build.

- [ ] **Step 1: Add the callables + a `RemoteVersion` type.** After the existing Drive callables in `src/index.tsx`, add:
```tsx
interface RemoteVersion { versionId: string; label: string; pinned: boolean; }
const listRemoteVersions = callable<[GameInfo], RemoteVersion[]>("list_remote_versions");
const restoreFromDrive = callable<[GameInfo, string], null>("restore_from_drive");
```

- [ ] **Step 2: In `Content`, add remote-restore state + a fetch + event handling.** Add states next to the others:
```tsx
  const [remote, setRemote] = useState<RemoteVersion[] | null>(null);
```
Add an effect (after the existing effects) that toasts restore results and refreshes the local list + the remote list when a restore finishes:
```tsx
  useEffect(() => {
    const done = addEventListener("drive_restore_done", () => {
      toaster.toast({ title: "Restored from Drive", body: "Version downloaded into your list" });
      if (selected) { refresh(selected); listRemoteVersions(selected).then(setRemote).catch(console.error); }
    });
    const err = addEventListener("drive_restore_error", (_a: number, msg: string) =>
      toaster.toast({ title: "Restore failed", body: String(msg) }));
    return () => { removeEventListener("drive_restore_done", done); removeEventListener("drive_restore_error", err); };
  }, [selected]);
```

- [ ] **Step 3: Render the "Restore from Drive" affordance** in the game-detail view (after the "Sync to Drive now" `ButtonItem` row). It fetches remote versions on demand and lists the ones NOT already present locally:
```tsx
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => listRemoteVersions(selected).then(setRemote).catch(console.error)}>
          Restore from Drive…
        </ButtonItem>
      </PanelSectionRow>
      {remote && remote
        .filter((rv) => !(listing?.versions ?? []).some((v) => v.versionId === rv.versionId))
        .map((rv) => (
          <PanelSectionRow key={rv.versionId}>
            <ButtonItem layout="below"
              onClick={() => restoreFromDrive(selected, rv.versionId).catch(console.error)}>
              ⬇ {rv.pinned ? "★ " : ""}{rv.label}
            </ButtonItem>
          </PanelSectionRow>
        ))}
      {remote && remote.filter((rv) => !(listing?.versions ?? []).some((v) => v.versionId === rv.versionId)).length === 0 && (
        <PanelSectionRow>No Drive-only versions to restore.</PanelSectionRow>
      )}
```

- [ ] **Step 4: Type-check + build**

Run: `pnpm exec tsc --noEmit && pnpm build`
Expected: no errors; `dist/index.js` produced. (Reconcile any `@decky/ui` prop names against the installed types if needed.)

- [ ] **Step 5: Validate backend + full suite**

Run: `python -m pytest -q && python -m py_compile main.py && echo OK`
Expected: all pass; "OK".

- [ ] **Step 6: On-device verification (manual — SKIP).** On a Deck with Drive linked: pick a game, "Restore from Drive…", tap a Drive-only version → it downloads and appears in the local version list; then "Restore" (revert) materializes it into the live save.

- [ ] **Step 7: Commit**

```bash
git add src/index.tsx
git commit -m "feat: QAM Restore-from-Drive — list remote-only versions + download"
```

---

## Self-review (done while writing)

- **Coverage:** download a remote version's files ✓ (Task 1); reconstruct it locally without disturbing HEAD ✓ (Task 2); Engine composition + an end-to-end backup→Drive→restore round-trip into a FRESH store ✓ (Task 3); Decky list/restore wiring with re-link recovery + non-blocking restore ✓ (Task 4); QAM affordance listing Drive-ONLY versions ✓ (Task 5). Materializing into the live save reuses the existing M2 revert (no new code) — the restored version shows up and the user reverts to it.
- **Type consistency:** `index["versions"][vid]` shape (`label`/`folderId`/`fileIds`/`pinned`) is consumed identically by `list_remote_versions`/`download_version`/`restore_from_drive_with_client`; `import_version` consumes `{root<suffix>/<path>: bytes}` (exactly what `download_version` returns and what `load_version_files` produces — verified by the round-trip asserting `load_version_files` equality). `RemoteVersion` (`versionId`/`label`/`pinned`) matches `list_remote_versions`' output and the `list_remote_versions`/`restore_from_drive` callable signatures match the new `Plugin` methods.
- **No placeholders:** every step has complete code.
- **Notes:** `import_version` parses `createdAt` from the `v_<ms>_<hex>` id and keeps `refs.versions` newest-first; it sets `kind="import"`/`reason="drive-restore"` (new provenance values, fine — `make_version_entry` isn't used here). `list_remote_versions` (Task 4 Plugin method) does a brief synchronous network read on the RPC thread — acceptable for a user-initiated metadata fetch; could move to an executor later. The restore itself is a non-blocking task.

---

## After M5 — remaining roadmap

- Backend revert guard / `locking.py`; discovery hardening; "pinned exceeds keep cap" warning; Drive minors (respect device-flow `interval`, appId in Drive folder naming, resumable uploads); pre-emptive token refresh.
```
