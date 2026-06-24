# Save Manager — M4b Drive Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the M4a Drive core into a working one-way Google Drive backup — the deferred hardening (trash-by-default, pagination, robust parsing), `index.json` read/write on Drive, a suffix-qualified version-file loader, a `sync_game` orchestrator, the real `requests` transport, the Engine/Decky wiring (link account via device flow, sync), and the QAM Drive UI.

**Architecture:** Tasks 1–5 finish the offline-testable engine (extend `drive.py`/`mirror.py`/`versioning.py`, all fake-transport unit-tested). Task 6 adds the real `requests`+`certifi` HTTP adapter (the adapter is tested against a fake session; `requests` itself is on-device). Tasks 7–8 are the on-device glue — `main.py` device-flow loop + non-blocking sync, and the QAM Drive panel — verified by `py_compile`/`tsc`/build, with true verification on a Steam Deck + the user's own Google OAuth client.

**Tech Stack:** Python 3.11; `requests`+`certifi` (pure-python, vendored via `requirements.txt`); pytest. Frontend: TypeScript/React, `@decky/api`/`@decky/ui`.

**Reference:** Spec §6 + §11 (the user's one-time Google client setup). M4a delivered `drive.py` (OAuth + `DriveClient`) and `mirror.py` (`empty_index`, `plan_sync`, `sync_versions` with index-before-prune). The M4a review deferred I4 (trash default), M2 (malformed-2xx), M3 (pagination), M4 (refresh shape) to here.

**Out of scope:** resumable/chunked uploads (multipart is fine for saves); restore-from-Drive UI; multi-account Drive; `locking.py`; discovery hardening.

---

## Existing code this builds on (call verbatim)

- `drive.py`: `DRIVE_FILE_SCOPE`, `_DEVICE_CODE_URL`, `_TOKEN_URL`, `_DRIVE_V3`, `_UPLOAD_V3`, `_FOLDER_MIME`, `_q_value`, `DriveError`, `HttpResponse(status, body, headers)` (`.json()`, `.text`), `request_device_code(http, client_id, scope=…)`, `poll_token(http, cid, secret, device_code) -> {"status", "tokens"?}`, `refresh_access_token(http, cid, secret, refresh_token) -> str`, `DriveClient(http, access_token)` (`list_children`, `find_folder`, `create_folder`, `find_or_create_folder`, `upload_file`, `update_file`, `delete_file`, `download_file`). Imports `json`, `os`.
- `mirror.py`: `empty_index(app_id)`, `plan_sync(local_kept_vids, remote_index)`, `sync_versions(client, root_folder_id, game_name, kept_versions, remote_index, load_version_files, persist_index)`.
- `tests/drive_fakes.py`: `FakeHttp`, `resp`, `FakeDriveClient` (fields: `folders`, `files`, `deleted`; methods `find_or_create_folder`, `create_folder`, `upload_file`, `delete_file`).
- Engine: `store.version_dir`/`read_meta`, `refs.read_refs`, `config.get_game_settings`/`set_game_setting` (DEFAULTS has `driveMirror`), `api.Engine`.

**`http` transport contract:** `http(method, url, *, headers=None, params=None, data=None, json_body=None) -> HttpResponse`. `data` dict = form-encoded; `data` bytes = raw; `json_body` = JSON.

---

## File Structure (M4b)

| File | Change | Responsibility |
|---|---|---|
| `defaults/py_modules/savemanager/drive.py` | Modify | Add `DriveClient.trash_file`; paginate `list_children`/`find_folder`; robust success-parse (`_require_id`). |
| `defaults/py_modules/savemanager/mirror.py` | Modify | `sync_versions` prunes via `trash_file`; add `read_index`/`write_index`; add `sync_game` orchestrator. |
| `defaults/py_modules/savemanager/versioning.py` | Modify | `load_version_files` (suffix-qualified bytes) + `kept_versions_for` (label/pinned from refs). |
| `defaults/py_modules/savemanager/drive_transport.py` | Create | `make_requests_http(session)` — the real `requests` adapter (tested via a fake session). |
| `defaults/py_modules/savemanager/api.py` | Modify | Drive credential storage + `Engine.sync_drive(app_id, http)` composition. |
| `main.py` | Modify | Device-flow link loop + non-blocking sync task + Plugin methods. |
| `requirements.txt` | Modify | Add `requests`, `certifi`. |
| `src/index.tsx` | Modify | QAM Drive section (link account, code/QR, Sync now, per-game Mirror toggle). |
| `tests/drive_fakes.py` | Modify | Extend `FakeDriveClient` (`trashed`, `list_children`, `download_file`, `update_file`); add `FakeSession`. |
| `tests/test_*.py` | Create/modify | Unit tests. |

---

## Task 1: `trash_file` + prune-via-trash (review I4)

**Files:** Modify `defaults/py_modules/savemanager/drive.py`, `defaults/py_modules/savemanager/mirror.py`, `tests/drive_fakes.py`, `tests/test_mirror.py`; Test `tests/test_drive_client.py` (append).

- [ ] **Step 1: Extend `FakeDriveClient` in `tests/drive_fakes.py`.** Add `self.trashed = []` to `__init__` (next to `self.deleted = []`), and add this method:
```python
    def trash_file(self, file_id):
        self.trashed.append(file_id)
        self.folders.pop(file_id, None)
        self.files.pop(file_id, None)
```

- [ ] **Step 2: Append the failing test to `tests/test_drive_client.py`:**
```python
def test_trash_file_patches_trashed_true():
    client, http = _client([resp(200, {"id": "f1"})])
    client.trash_file("f1")
    call = http.calls[0]
    assert call["method"] == "PATCH" and call["url"].endswith("/drive/v3/files/f1")
    assert call["json_body"] == {"trashed": True}


def test_trash_file_tolerates_404():
    client, _ = _client([resp(404, {"error": "notFound"})])
    client.trash_file("gone")        # must not raise
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_drive_client.py::test_trash_file_patches_trashed_true -v`
Expected: FAIL (`AttributeError: 'DriveClient' object has no attribute 'trash_file'`).

- [ ] **Step 4: Add `trash_file` to the `DriveClient` class in `drive.py`** (after `delete_file`):
```python
    def trash_file(self, file_id) -> None:
        r = self.http("PATCH", f"{_DRIVE_V3}/files/{file_id}",
                      headers=self._auth({"Content-Type": "application/json"}),
                      json_body={"trashed": True})
        if r.status not in (200, 204, 404):     # 404 == already gone -> idempotent
            raise DriveError(f"trash_file: {r.status} {r.text}")
```

- [ ] **Step 5: Make `sync_versions` prune via trash (spec default).** In `mirror.py`, in `sync_versions`, change the prune line:
```python
    for folder_id in prune_folder_ids:         # prune AFTER the index is durable
        client.delete_file(folder_id)
```
to:
```python
    for folder_id in prune_folder_ids:         # prune AFTER the index is durable (trash, not permanent)
        client.trash_file(folder_id)
```

- [ ] **Step 6: Update the two existing `sync_versions` prune assertions in `tests/test_mirror.py`** to check `trashed` instead of `deleted`:
  - In `test_sync_versions_uploads_new_prunes_old_and_updates_index`, change `assert "OLDF" in client.deleted` to `assert "OLDF" in client.trashed`.
  - In `test_sync_versions_persists_index_before_pruning`, change `seen["deletes_at_persist"] = list(client.deleted)` to `seen["deletes_at_persist"] = list(client.trashed)`, and change the final `assert "OLDF" in client.deleted` to `assert "OLDF" in client.trashed`.

- [ ] **Step 7: Run tests to verify they pass**

Run: `python -m pytest tests/test_drive_client.py tests/test_mirror.py -v`
Expected: PASS (the new trash tests + the updated mirror tests).

- [ ] **Step 8: Commit**

```bash
git add defaults/py_modules/savemanager/drive.py defaults/py_modules/savemanager/mirror.py tests/drive_fakes.py tests/test_drive_client.py tests/test_mirror.py
git commit -m "feat: DriveClient.trash_file; prune via trash, not permanent delete (M4a review I4)"
```

---

## Task 2: pagination + robust success-parse (review M3 + M2)

**Files:** Modify `defaults/py_modules/savemanager/drive.py`; Test `tests/test_drive_client.py` (append).

- [ ] **Step 1: Append the failing tests to `tests/test_drive_client.py`:**
```python
def test_list_children_follows_pagination():
    client, http = _client([
        resp(200, {"files": [{"id": "a", "name": "1"}], "nextPageToken": "PAGE2"}),
        resp(200, {"files": [{"id": "b", "name": "2"}]}),
    ])
    out = client.list_children("P")
    assert [f["id"] for f in out] == ["a", "b"]          # both pages accumulated
    assert http.calls[1]["params"]["pageToken"] == "PAGE2"


def test_create_folder_malformed_success_raises():
    client, _ = _client([resp(200, body=b"")])            # 200 but empty body
    with pytest.raises(DriveError):
        client.create_folder("G", "ROOT")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_drive_client.py::test_list_children_follows_pagination -v`
Expected: FAIL (`out` has only one file — pagination not followed).

- [ ] **Step 3: Add a `_require_id` helper and a paginating `_list_all`; rewrite `list_children`/`find_folder` and harden `create_folder`/`upload_file`.**

Add this module-level helper to `drive.py` (right after `_q_value`):
```python
def _require_id(r, what) -> str:
    try:
        return r.json()["id"]
    except (ValueError, KeyError, TypeError):
        raise DriveError(f"{what}: malformed success response: {r.status} {r.text}")
```

In the `DriveClient` class, ADD a private paginator and REPLACE `list_children` + `find_folder` with versions that use it:
```python
    def _list_all(self, q) -> list:
        files, page_token = [], None
        while True:
            params = {"q": q, "fields": "nextPageToken,files(id,name)", "pageSize": 1000}
            if page_token:
                params["pageToken"] = page_token
            r = self.http("GET", f"{_DRIVE_V3}/files", headers=self._auth(), params=params)
            if r.status != 200:
                raise DriveError(f"list: {r.status} {r.text}")
            data = r.json()
            files.extend(data.get("files", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                return files

    def list_children(self, parent_id) -> list:
        return self._list_all(f"'{parent_id}' in parents and trashed=false")

    def find_folder(self, name, parent_id):
        files = self._list_all(f"name='{_q_value(name)}' and '{parent_id}' in parents and "
                               f"mimeType='{_FOLDER_MIME}' and trashed=false")
        return files[0]["id"] if files else None
```

Then change `create_folder`'s return from `return r.json()["id"]` to `return _require_id(r, "create_folder")`, and `upload_file`'s return from `return r.json()["id"]` to `return _require_id(r, "upload_file")`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_drive_client.py -v`
Expected: PASS (pagination + malformed + all earlier client tests; the earlier `q`/Bearer assertions still hold).

- [ ] **Step 5: Commit**

```bash
git add defaults/py_modules/savemanager/drive.py tests/test_drive_client.py
git commit -m "feat: paginate list/find; raise DriveError on malformed success (M4a review M3+M2)"
```

---

## Task 3: `read_index` + `write_index` on Drive

**Files:** Modify `defaults/py_modules/savemanager/mirror.py`, `tests/drive_fakes.py`; Test `tests/test_mirror.py` (append).

- [ ] **Step 1: Extend `FakeDriveClient`** in `tests/drive_fakes.py` so it can serve index reads/writes. Add these methods:
```python
    def list_children(self, parent_id):
        out = []
        for fid, (name, parent) in self.folders.items():
            if parent == parent_id:
                out.append({"id": fid, "name": name})
        for fid, (name, parent, _content) in self.files.items():
            if parent == parent_id:
                out.append({"id": fid, "name": name})
        return out

    def download_file(self, file_id):
        return self.files[file_id][2]

    def update_file(self, file_id, content):
        name, parent, _old = self.files[file_id]
        self.files[file_id] = (name, parent, content)
```

- [ ] **Step 2: Append the failing test to `tests/test_mirror.py`:**
```python
from savemanager.mirror import read_index, write_index


def test_write_then_read_index_roundtrips(tmp_path):
    client = FakeDriveClient()
    game_folder = client.create_folder("XCOM 2", "ROOT")
    idx = empty_index(281990)
    idx["versions"]["v1"] = {"label": "L", "folderId": "f", "fileIds": {}, "pinned": False}
    file_id = write_index(client, game_folder, idx, existing_id=None)
    got, got_id = read_index(client, game_folder)
    assert got_id == file_id
    assert got["versions"]["v1"]["label"] == "L"


def test_read_index_missing_returns_none(tmp_path):
    client = FakeDriveClient()
    game_folder = client.create_folder("G", "ROOT")
    assert read_index(client, game_folder) == (None, None)


def test_write_index_updates_existing(tmp_path):
    client = FakeDriveClient()
    game_folder = client.create_folder("G", "ROOT")
    fid = write_index(client, game_folder, empty_index(1), existing_id=None)
    idx2 = empty_index(1); idx2["gameFolderId"] = game_folder
    same = write_index(client, game_folder, idx2, existing_id=fid)
    assert same == fid                                   # updated in place, no new file
    got, _ = read_index(client, game_folder)
    assert got["gameFolderId"] == game_folder
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_mirror.py::test_read_index_missing_returns_none -v`
Expected: FAIL (`ImportError: cannot import name 'read_index'`).

- [ ] **Step 4: Append `read_index`/`write_index` to `mirror.py`** (add `import json` at the top of the file first — `mirror.py` currently has no imports):
```python
import json

_INDEX_NAME = "index.json"
```
(Place the `import json` and `_INDEX_NAME` at the very top, above `empty_index`.) Then append:
```python
def read_index(client, game_folder_id):
    """Return (index_dict, index_file_id) for this game's Drive index.json, or (None, None)."""
    for child in client.list_children(game_folder_id):
        if child["name"] == _INDEX_NAME:
            raw = client.download_file(child["id"])
            return json.loads(raw.decode("utf-8")), child["id"]
    return None, None


def write_index(client, game_folder_id, index, existing_id=None) -> str:
    """Persist index.json under the game folder. Updates in place if existing_id is given,
    else creates it. Returns the Drive file id."""
    content = json.dumps(index, indent=1).encode("utf-8")
    if existing_id:
        client.update_file(existing_id, content)
        return existing_id
    return client.upload_file(_INDEX_NAME, game_folder_id, content)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_mirror.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add defaults/py_modules/savemanager/mirror.py tests/drive_fakes.py tests/test_mirror.py
git commit -m "feat: read_index/write_index — index.json on Drive (commit point)"
```

---

## Task 4: version-file loader + kept-versions builder

**Files:** Modify `defaults/py_modules/savemanager/versioning.py`; Test `tests/test_drive_loader.py`.

- [ ] **Step 1: Write the failing test `tests/test_drive_loader.py`:**
```python
import os
from savemanager.versioning import do_backup, load_version_files, kept_versions_for, list_versions
from savemanager.curation import set_pinned, set_name
from tests.fixtures import make_steam_tree


def _ctx(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path)
    return os.path.join(str(tmp_path), "data"), steam_root, acct, app


def test_load_version_files_returns_suffix_qualified_bytes(tmp_path):
    data_root, steam_root, acct, app = _ctx(tmp_path)
    do_backup(data_root, steam_root, acct, {"appId": app, "name": "X"}, now_ms=1, rand_hex="a")
    head = list_versions(data_root, app)["head"]["versionId"]
    files = load_version_files(data_root, app, head)
    assert files == {"root/save1.sav": b"AAAAA", "root/profile.bin": b"BBBB"}


def test_kept_versions_for_uses_name_then_versionid_and_carries_pinned(tmp_path):
    data_root, steam_root, acct, app = _ctx(tmp_path)
    do_backup(data_root, steam_root, acct, {"appId": app, "name": "X"}, now_ms=1, rand_hex="a")
    head = list_versions(data_root, app)["head"]["versionId"]
    set_pinned(data_root, app, head, True)
    set_name(data_root, app, head, "Before boss")
    kept = kept_versions_for(data_root, app)
    assert kept == [{"versionId": head, "label": "Before boss", "pinned": True}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_drive_loader.py -v`
Expected: FAIL (`ImportError: cannot import name 'load_version_files'`).

- [ ] **Step 3: Append to `versioning.py`** (it already imports `os`, `read_meta`, `version_dir`? — it imports `read_meta` from store but NOT `version_dir`; add `version_dir` to the store import line, which is `from .store import create_snapshot, new_version_id, read_meta, _safe_rel, delete_version, restore_version, _hash_file` → add `version_dir`). Then append:
```python
def load_version_files(data_root, app_id, version_id) -> dict:
    """Read a stored version's files as {suffix-qualified relpath: bytes}. The key is
    'root<suffix>/<path>' so files from different save roots never collide."""
    meta = read_meta(data_root, app_id, version_id)
    vdir = version_dir(data_root, app_id, version_id)
    out = {}
    for f in meta["files"]:
        key = f"root{f['suffix']}/{f['path']}"
        with open(os.path.join(vdir, f"root{f['suffix']}", f["path"]), "rb") as fh:
            out[key] = fh.read()
    return out


def kept_versions_for(data_root, app_id) -> list:
    """Build the ordered kept-version list for Drive mirroring from refs.json.
    label = the user name if set, else the versionId (Drive folder name)."""
    refs = read_refs(data_root, app_id)
    return [{"versionId": v["versionId"], "label": v["name"] or v["versionId"],
             "pinned": v["pinned"]} for v in refs["versions"]]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_drive_loader.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add defaults/py_modules/savemanager/versioning.py tests/test_drive_loader.py
git commit -m "feat: load_version_files (suffix-qualified) + kept_versions_for from refs"
```

---

## Task 5: `sync_game` orchestration

**Files:** Modify `defaults/py_modules/savemanager/mirror.py`; Test `tests/test_mirror.py` (append).

- [ ] **Step 1: Append the failing test to `tests/test_mirror.py`:**
```python
from savemanager.mirror import sync_game


def test_sync_game_uploads_then_is_idempotent():
    client = FakeDriveClient()
    files = {"v1": {"root/s.sav": b"DATA"}}
    kept = [{"versionId": "v1", "label": "v1", "pinned": False}]

    idx1 = sync_game(client, "ROOT", "XCOM 2", kept, lambda vid: files[vid])
    assert "v1" in idx1["versions"]
    uploads_after_first = len([f for f in client.files.values() if f[0] != "index.json"])
    assert uploads_after_first == 1                       # the one save file

    idx2 = sync_game(client, "ROOT", "XCOM 2", kept, lambda vid: files[vid])
    assert "v1" in idx2["versions"]
    uploads_after_second = len([f for f in client.files.values() if f[0] != "index.json"])
    assert uploads_after_second == 1                       # nothing re-uploaded (read index → no-op)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mirror.py::test_sync_game_uploads_then_is_idempotent -v`
Expected: FAIL (`ImportError: cannot import name 'sync_game'`).

- [ ] **Step 3: Append `sync_game` to `mirror.py`:**
```python
def sync_game(client, root_folder_id, game_name, kept_versions, load_version_files) -> dict:
    """End-to-end one-game mirror: ensure the game folder, read its index.json, sync the
    kept versions (upload new, trash removed), and persist index.json LAST. Returns the index."""
    game_folder = client.find_or_create_folder(game_name, root_folder_id)
    index, index_id = read_index(client, game_folder)
    if index is None:
        index = empty_index(0)
    index["gameFolderId"] = game_folder
    holder = {"id": index_id}

    def persist(idx):
        holder["id"] = write_index(client, game_folder, idx, existing_id=holder["id"])

    return sync_versions(client, root_folder_id, game_name, kept_versions, index,
                         load_version_files, persist_index=persist)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mirror.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add defaults/py_modules/savemanager/mirror.py tests/test_mirror.py
git commit -m "feat: sync_game — ensure folder, read index, sync versions, persist index"
```

---

## Task 6: real `requests` HTTP transport

**Files:** Create `defaults/py_modules/savemanager/drive_transport.py`; Modify `requirements.txt`, `tests/drive_fakes.py`; Test `tests/test_drive_transport.py`.

- [ ] **Step 1: Append a `FakeSession` to `tests/drive_fakes.py`:**
```python
class _FakeResp:
    def __init__(self, status_code=200, content=b"{}", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}


class FakeSession:
    """Stand-in for requests.Session: records the last request kwargs."""
    def __init__(self, status_code=200, content=b"{}"):
        self.last = None
        self._status = status_code
        self._content = content

    def request(self, method, url, **kwargs):
        self.last = {"method": method, "url": url, **kwargs}
        return _FakeResp(self._status, self._content)
```

- [ ] **Step 2: Write the failing test `tests/test_drive_transport.py`:**
```python
from savemanager.drive_transport import make_requests_http
from savemanager.drive import HttpResponse
from tests.drive_fakes import FakeSession


def test_dict_data_is_form_encoded_field():
    s = FakeSession(200, b'{"ok": true}')
    http = make_requests_http(s)
    r = http("POST", "https://x/token", data={"client_id": "c", "grant_type": "g"})
    assert isinstance(r, HttpResponse) and r.json() == {"ok": True}
    assert s.last["method"] == "POST" and s.last["url"] == "https://x/token"
    assert s.last["data"] == {"client_id": "c", "grant_type": "g"}    # requests form-encodes a dict
    assert "json" not in s.last or s.last["json"] is None


def test_bytes_data_passed_through_and_headers_preserved():
    s = FakeSession()
    http = make_requests_http(s)
    http("POST", "https://x/upload", headers={"Content-Type": "multipart/related; boundary=b"},
         params={"uploadType": "multipart"}, data=b"RAW")
    assert s.last["data"] == b"RAW"
    assert s.last["headers"]["Content-Type"].startswith("multipart/related")   # not overridden
    assert s.last["params"] == {"uploadType": "multipart"}


def test_json_body_sent_as_json():
    s = FakeSession()
    http = make_requests_http(s)
    http("POST", "https://x/files", json_body={"name": "f"})
    assert s.last["json"] == {"name": "f"}


def test_maps_response_to_httpresponse():
    s = FakeSession(404, b'{"error":"x"}')
    r = make_requests_http(s)("GET", "https://x")
    assert r.status == 404 and r.json() == {"error": "x"}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_drive_transport.py -v`
Expected: FAIL (`No module named 'savemanager.drive_transport'`).

- [ ] **Step 4: Implement `drive_transport.py`:**
```python
# defaults/py_modules/savemanager/drive_transport.py
from .drive import HttpResponse


def make_requests_http(session):
    """Adapt a requests.Session (or compatible) into the `http(method, url, *, headers,
    params, data, json_body)` callable the Drive client expects.

    - dict `data`  -> requests form-encodes (application/x-www-form-urlencoded)
    - bytes `data` -> sent verbatim; the caller's explicit Content-Type is preserved
    - json_body    -> sent as a JSON body
    """
    def http(method, url, *, headers=None, params=None, data=None, json_body=None):
        resp = session.request(method, url, headers=headers, params=params,
                               data=data, json=json_body)
        return HttpResponse(resp.status_code, resp.content or b"", dict(resp.headers))
    return http
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_drive_transport.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Add the runtime deps to `requirements.txt`.** Replace its contents with:
```text
# Pure-Python HTTPS for the Drive backend (in-process; never shell out — decky #729).
requests
certifi
```

- [ ] **Step 7: Commit**

```bash
git add defaults/py_modules/savemanager/drive_transport.py requirements.txt tests/drive_fakes.py tests/test_drive_transport.py
git commit -m "feat: real requests HTTP transport adapter + vendored deps"
```

---

## Task 7: Engine + Decky wiring (link account + sync)

**Files:** Modify `defaults/py_modules/savemanager/api.py`, `main.py`; Test `tests/test_engine_drive.py`.

> The credential storage + `sync_drive` composition are unit-tested with fakes; the `main.py` device-flow loop is `py_compile`-verified (it needs network + Decky on-device).

- [ ] **Step 1: Write the failing test `tests/test_engine_drive.py`:**
```python
import os
from savemanager.api import Engine
from tests.fixtures import make_steam_tree
from tests.drive_fakes import FakeDriveClient


def _engine(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path)
    eng = Engine(os.path.join(str(tmp_path), "data"), steam_root)
    eng.set_account_id(acct)
    return eng, app


def test_drive_credentials_roundtrip(tmp_path):
    eng, _ = _engine(tmp_path)
    assert eng.get_drive_status()["linked"] is False
    eng.set_drive_client("CID", "SECRET")
    eng.set_drive_refresh_token("RT")
    st = eng.get_drive_status()
    assert st["linked"] is True and st["hasClient"] is True


def test_sync_drive_with_client_mirrors_kept_versions(tmp_path):
    eng, app = _engine(tmp_path)
    steam_root = eng.steam_root
    acct = eng.account_ids[0]
    from savemanager.versioning import do_backup
    do_backup(eng.data_root, steam_root, acct, {"appId": app, "name": "XCOM 2"},
              now_ms=1, rand_hex="a")
    client = FakeDriveClient()
    idx = eng.sync_drive_with_client(app, "XCOM 2", client, "ROOT")    # inject fake client
    assert len(idx["versions"]) == 1
    assert any(name != "index.json" for (name, _p, _c) in client.files.values())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_engine_drive.py -v`
Expected: FAIL (`AttributeError: 'Engine' object has no attribute 'get_drive_status'`).

- [ ] **Step 3: Extend `Engine` in `api.py`.** Add imports at the top (after the existing `from .` imports):
```python
import json
from .mirror import sync_game
from .versioning import kept_versions_for, load_version_files
```
(`import os` is already present from M3.) Add a secrets-path helper and the methods to the `Engine` class (after `remotecache_mtime`):
```python
    def _secrets_path(self):
        return os.path.join(self.data_root, "drive_secrets.json")

    def _read_secrets(self) -> dict:
        try:
            with open(self._secrets_path()) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _write_secrets(self, secrets) -> None:
        path = self._secrets_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(secrets, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)

    def set_drive_client(self, client_id, client_secret) -> None:
        s = self._read_secrets()
        s["client_id"] = client_id
        s["client_secret"] = client_secret
        self._write_secrets(s)

    def set_drive_refresh_token(self, refresh_token) -> None:
        s = self._read_secrets()
        s["refresh_token"] = refresh_token
        self._write_secrets(s)

    def get_drive_status(self) -> dict:
        s = self._read_secrets()
        return {"hasClient": bool(s.get("client_id") and s.get("client_secret")),
                "linked": bool(s.get("refresh_token"))}

    def sync_drive_with_client(self, app_id, game_name, client, root_folder_id) -> dict:
        """Mirror one game's kept versions using an already-built DriveClient (or fake).
        The Decky layer builds the real client from the stored refresh token."""
        kept = kept_versions_for(self.data_root, app_id)
        return sync_game(client, root_folder_id, game_name, kept,
                         lambda vid: load_version_files(self.data_root, app_id, vid))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_engine_drive.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Wire `main.py`** — the device-flow loop + non-blocking sync (uses the real transport; `py_compile` only). Add near the top imports:
```python
import certifi
import requests

from savemanager import drive as drive_mod
from savemanager.drive import DriveClient
from savemanager.drive_transport import make_requests_http

_DRIVE_ROOT_FOLDER = "SteamDeckSaveManager"
```
Add a helper after `_rand_hex()`:
```python
def _drive_http():
    session = requests.Session()
    session.verify = certifi.where()
    return make_requests_http(session)
```
Add these `Plugin` methods (after the M3 ones):
```python
    async def set_drive_client(self, client_id: str, client_secret: str):
        get_engine().set_drive_client(client_id, client_secret)
        return None

    async def get_drive_status(self) -> dict:
        return get_engine().get_drive_status()

    async def link_drive_start(self) -> dict:
        # Begin the device flow; returns the user_code + URL to display. Polling is link_drive_poll.
        secrets = get_engine()._read_secrets()
        http = _drive_http()
        dc = drive_mod.request_device_code(http, secrets["client_id"])
        self._drive_device = {"device_code": dc["device_code"], "interval": dc.get("interval", 5)}
        return {"user_code": dc["user_code"], "verification_url": dc.get("verification_url"),
                "expires_in": dc.get("expires_in")}

    async def link_drive_poll(self) -> dict:
        # Poll once; the frontend calls this on the device-flow interval. Returns a status string.
        eng = get_engine()
        secrets = eng._read_secrets()
        http = _drive_http()
        out = drive_mod.poll_token(http, secrets["client_id"], secrets["client_secret"],
                                   self._drive_device["device_code"])
        if out["status"] == "ok":
            eng.set_drive_refresh_token(out["tokens"]["refresh_token"])
        return {"status": out["status"]}

    async def sync_drive(self, game_info: dict):
        self.loop.create_task(self._do_sync_drive(game_info))
        return None

    async def _do_sync_drive(self, game_info: dict):
        try:
            eng = get_engine()
            secrets = eng._read_secrets()
            http = _drive_http()
            access = drive_mod.refresh_access_token(http, secrets["client_id"],
                                                    secrets["client_secret"], secrets["refresh_token"])
            client = DriveClient(http, access)
            root = client.find_or_create_folder(_DRIVE_ROOT_FOLDER, "root")
            idx = eng.sync_drive_with_client(game_info["appId"], game_info["name"], client, root)
            await decky.emit("drive_sync_done", game_info["appId"], len(idx["versions"]))
        except Exception as e:
            decky.logger.error(f"SaveManager drive sync failed: {e}")
            await decky.emit("drive_sync_error", game_info.get("appId"), str(e))
```

- [ ] **Step 6: Validate syntax**

Run: `python -m py_compile main.py && echo OK`
Expected: "OK".

- [ ] **Step 7: Run the full suite + compile**

Run: `python -m pytest -q && python -m py_compile main.py && echo OK`
Expected: all pass; "OK".

- [ ] **Step 8: Commit**

```bash
git add defaults/py_modules/savemanager/api.py main.py tests/test_engine_drive.py
git commit -m "feat: Engine drive credentials + sync composition; Decky link/sync wiring"
```

---

## Task 8: QAM Drive UI

**Files:** Modify `src/index.tsx`. Verified by `tsc` + build (on-device manual).

- [ ] **Step 1: Add the callables + a `DriveStatus` type.** After the existing callables in `src/index.tsx`, add:
```tsx
interface DriveStatus { hasClient: boolean; linked: boolean; }
const setDriveClient = callable<[string, string], null>("set_drive_client");
const getDriveStatus = callable<[], DriveStatus>("get_drive_status");
const linkDriveStart = callable<[], { user_code: string; verification_url: string }>("link_drive_start");
const linkDrivePoll = callable<[], { status: string }>("link_drive_poll");
const syncDrive = callable<[GameInfo], null>("sync_drive");
```

- [ ] **Step 2: Add a Drive section component.** Add this component above `Content`:
```tsx
function DriveSection() {
  const [status, setStatus] = useState<DriveStatus | null>(null);
  const [cid, setCid] = useState("");
  const [secret, setSecret] = useState("");
  const [code, setCode] = useState<{ user_code: string; verification_url: string } | null>(null);

  useEffect(() => { getDriveStatus().then(setStatus).catch(console.error); }, []);

  const save = async () => { await setDriveClient(cid, secret); getDriveStatus().then(setStatus); };
  const link = async () => {
    const c = await linkDriveStart(); setCode(c);
    const timer = setInterval(async () => {
      const r = await linkDrivePoll().catch(() => ({ status: "error" }));
      if (r.status === "ok") { clearInterval(timer); setCode(null); getDriveStatus().then(setStatus); }
      else if (r.status === "denied" || r.status === "expired" || r.status === "error") { clearInterval(timer); setCode(null); }
    }, 5000);
  };

  return (
    <PanelSection title="Google Drive">
      {!status?.hasClient && (
        <>
          <PanelSectionRow><TextField label="Client ID" value={cid} onChange={(e) => setCid(e.target.value)} /></PanelSectionRow>
          <PanelSectionRow><TextField label="Client secret" value={secret} onChange={(e) => setSecret(e.target.value)} /></PanelSectionRow>
          <PanelSectionRow><ButtonItem layout="below" onClick={save}>Save Google client</ButtonItem></PanelSectionRow>
        </>
      )}
      {status?.hasClient && !status.linked && !code && (
        <PanelSectionRow><ButtonItem layout="below" onClick={link}>Link Google account</ButtonItem></PanelSectionRow>
      )}
      {code && (
        <PanelSectionRow>
          Go to {code.verification_url} and enter code: <b>{code.user_code}</b>
        </PanelSectionRow>
      )}
      {status?.linked && <PanelSectionRow>✓ Google Drive linked</PanelSectionRow>}
    </PanelSection>
  );
}
```

- [ ] **Step 3: Show the Drive section + a per-game "Mirror to Drive" toggle + Sync button.** In `Content`, when no game is selected, render `<DriveSection />` after the games `PanelSection` (wrap the existing return in a fragment). In the game-detail view, after the "Auto-backup on exit" `ToggleField` row, add a mirror toggle + sync button:
```tsx
      <PanelSectionRow>
        <ToggleField label="Mirror to Drive" checked={driveMirror}
          onChange={(v: boolean) => { setDriveMirror(v); setDriveMirrorSetting(selected.appId, v).catch(console.error); }} />
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => syncDrive(selected).catch(console.error)}>Sync to Drive now</ButtonItem>
      </PanelSectionRow>
```
This needs: a `driveMirror` state (`const [driveMirror, setDriveMirror] = useState<boolean>(false);`), loading it in `refresh` (`setDriveMirror(s.driveMirror)`), adding `driveMirror: boolean` to the `Settings` interface, and a `setDriveMirrorSetting` callable: `const setDriveMirrorSetting = callable<[number, boolean], Settings>("set_drive_mirror");`. Add the backend `Engine.set_drive_mirror`/`Plugin.set_drive_mirror` if not present — they are NOT, so add to `api.py`: `def set_drive_mirror(self, app_id, enabled): return set_game_setting(self.data_root, app_id, "driveMirror", bool(enabled))` and to `main.py`: `async def set_drive_mirror(self, app_id: int, enabled: bool): return get_engine().set_drive_mirror(app_id, enabled)`. (Do these backend additions in this task, then wire the frontend.)

- [ ] **Step 4: Type-check + build**

Run: `pnpm exec tsc --noEmit && pnpm build`
Expected: no errors; `dist/index.js` produced. (Reconcile any `@decky/ui` prop names against the installed types if needed.)

- [ ] **Step 5: Validate backend compiles + full suite**

Run: `python -m pytest -q && python -m py_compile main.py && echo OK`
Expected: all pass; "OK".

- [ ] **Step 6: On-device verification (manual — SKIP).** With a Google OAuth client created (spec §11), enter the client id/secret, link via the shown code, toggle Mirror on, Sync now → version folders + `index.json` appear in `SteamDeckSaveManager/<game>/` in My Drive.

- [ ] **Step 7: Commit**

```bash
git add src/index.tsx defaults/py_modules/savemanager/api.py main.py
git commit -m "feat: QAM Drive UI — link account, per-game mirror toggle, sync now"
```

---

## Self-review (done while writing)

- **Spec §6 coverage:** native Drive REST + device-code OAuth (M4a) wired up here; one-way real-file mirror with per-version folders + `index.json` commit point ✓ (Tasks 3, 5); `drive.file` scope (M4a); upload→index-last→trash-after ✓ (Task 1 trash + M4a ordering); device-flow link + token persistence ✓ (Task 7); non-blocking sync + progress events ✓ (Task 7); per-game `driveMirror` toggle + Drive UI ✓ (Task 8). The M4a-review deferrals I4/M2/M3 are resolved (Tasks 1–2). M4 (refresh returning `expires_in`) is intentionally NOT done — sync refreshes reactively on each run, so pre-emptive scheduling isn't needed; noted as a future nicety.
- **Type consistency:** `http(...)`/`HttpResponse` contract honored by `make_requests_http`; `sync_game(client, root, game_name, kept_versions, load_version_files)` matches `sync_versions`' params and the `kept_versions` `{versionId,label,pinned}` shape from `kept_versions_for`; `load_version_files` returns `root<suffix>/<path>` keys (the suffix-qualified contract the M4a review required). `FakeDriveClient` gains exactly the methods `read_index`/`write_index`/`sync_game` call (`list_children`, `download_file`, `update_file`, `trash_file`).
- **No placeholders:** every step has complete code.
- **On-device caveats:** Task 7's `main.py` device-flow loop and Task 8's UI are `py_compile`/`tsc`-verified only; real verification needs the user's Google client + a Deck (spec §11). The `_read_secrets` access from `main.py` uses `get_engine()._read_secrets()` (a deliberate internal reuse to keep secret-handling in one place).

---

## After M4b — remaining roadmap

- **Restore-from-Drive UI** (download a version's folder back into the local store / live save) — the `download_file` + index are already in place.
- **Backend revert guard / `locking.py`**; **discovery hardening**; **"pinned exceeds keep cap" warning**; resumable uploads for very large saves; pre-emptive token refresh (`expires_in`).
```
