# Save Manager — M4a Drive Core (offline-testable) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the network-free core of Google Drive backup — a transport-injected Drive client (OAuth 2.0 device-code flow + Drive v3 REST ops) and the one-way mirror reconciliation — fully unit-tested with a fake HTTP transport.

**Architecture:** Two new pure-Python modules under `defaults/py_modules/savemanager/`: `drive.py` (an `HttpResponse` value type, OAuth device-flow functions, and a `DriveClient` whose every method calls an injected `http(method, url, ...)` callable) and `mirror.py` (an index schema + `plan_sync` reconciliation + a `sync_versions` orchestrator that drives an injected client). Because the HTTP transport and the Drive client are injected, all logic is testable off-device with fakes. M4b supplies the real `requests`/`certifi` transport and wires this into the Engine/Plugin/UI.

**Tech Stack:** Python 3.11 (stdlib only — `json`, `urllib`-free here; the real transport is M4b), pytest. No network in this plan.

**Reference:** Spec `docs/superpowers/specs/2026-06-17-steam-deck-save-manager-design.md` §6 (Drive: native REST, `drive.file` scope, device-code OAuth, real-file mirror, remote `index.json`, upload→index-last→prune-after). Endpoints confirmed by prior research: device code `POST https://oauth2.googleapis.com/device/code`; token `POST https://oauth2.googleapis.com/token`; Drive v3 base `https://www.googleapis.com/drive/v3`, upload base `https://www.googleapis.com/upload/drive/v3`.

**Out of scope (M4b and beyond):** the real `requests`/`certifi` transport + vendoring; Engine/`main.py` link-account + sync wiring; the QAM Drive UI; resumable (chunked) uploads (M4a uses multipart, fine for small saves); restore-from-Drive UI.

---

## Design contract (locked — use verbatim)

- **`http` transport callable** (injected): `http(method: str, url: str, *, headers: dict | None = None, params: dict | None = None, data: bytes | dict | None = None, json_body: dict | None = None) -> HttpResponse`. (`data` as a dict = form-encoded body for OAuth; `data` as bytes = raw upload body; `json_body` = JSON body. The real transport in M4b encodes these; the fake records them.)
- **`HttpResponse`**: `.status: int`, `.body: bytes`, `.headers: dict`, `.json() -> dict`, `.text -> str`.
- **OAuth functions** (module-level in `drive.py`): `request_device_code(http, client_id, scope=DRIVE_FILE_SCOPE) -> dict`; `poll_token(http, client_id, client_secret, device_code) -> {"status": "ok"|"pending"|"slow_down"|"denied"|"expired", "tokens"?: dict}`; `refresh_access_token(http, client_id, client_secret, refresh_token) -> str`.
- **`DriveClient(http, access_token)`** methods: `list_children(parent_id) -> list[dict]`; `find_folder(name, parent_id) -> str | None`; `create_folder(name, parent_id) -> str`; `find_or_create_folder(name, parent_id) -> str`; `upload_file(name, parent_id, content: bytes) -> str`; `update_file(file_id, content: bytes) -> None`; `delete_file(file_id) -> None`; `download_file(file_id) -> bytes`.
- **`mirror.py`**: `empty_index(app_id) -> dict`; `plan_sync(local_kept_vids: list[str], remote_index: dict) -> {"to_upload": list, "to_prune": list}`; `sync_versions(client, root_folder_id, game_name, kept_versions, remote_index, load_version_files) -> dict` (returns the updated index). `kept_versions` = ordered `list[{"versionId", "label"}]`; `load_version_files(version_id) -> dict[str, bytes]` maps a relative path to its bytes.
- **Remote `index.json` shape**: `{"appId": int, "gameFolderId": str | None, "versions": {vid: {"label": str, "folderId": str, "fileIds": {relpath: fileId}}}, "schemaVersion": 1}`.
- `DriveError(Exception)` for non-success HTTP.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `defaults/py_modules/savemanager/drive.py` | Create | `HttpResponse`, `DriveError`, OAuth device-flow fns, `DriveClient` (Drive v3 REST, transport-injected). |
| `defaults/py_modules/savemanager/mirror.py` | Create | Remote-index schema + `plan_sync` + `sync_versions` orchestration over an injected client. |
| `tests/drive_fakes.py` | Create | `FakeHttp` (records calls, returns queued responses) + `resp()` helper; `FakeDriveClient`. |
| `tests/test_drive_oauth.py`, `tests/test_drive_client.py`, `tests/test_mirror.py` | Create | Unit tests. |

---

## Task 1: `HttpResponse` + OAuth `request_device_code` + fakes

**Files:** Create `defaults/py_modules/savemanager/drive.py`, `tests/drive_fakes.py`; Test `tests/test_drive_oauth.py`.

- [ ] **Step 1: Create the test fakes `tests/drive_fakes.py`**

```python
# tests/drive_fakes.py
import json
from savemanager.drive import HttpResponse


def resp(status, obj=None, body=None):
    if obj is not None:
        body = json.dumps(obj).encode("utf-8")
    if isinstance(body, str):
        body = body.encode("utf-8")
    return HttpResponse(status, body or b"", {})


class FakeHttp:
    """Records each request and returns queued HttpResponse objects in FIFO order."""
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, method, url, *, headers=None, params=None, data=None, json_body=None):
        self.calls.append({"method": method, "url": url, "headers": headers or {},
                           "params": params or {}, "data": data, "json_body": json_body})
        return self._responses.pop(0)
```

- [ ] **Step 2: Write the failing test `tests/test_drive_oauth.py`**

```python
import pytest
from savemanager.drive import request_device_code, DRIVE_FILE_SCOPE, DriveError
from tests.drive_fakes import FakeHttp, resp


def test_request_device_code_posts_and_parses():
    http = FakeHttp([resp(200, {"device_code": "DC", "user_code": "ABCD-1234",
                                 "verification_url": "https://www.google.com/device",
                                 "interval": 5, "expires_in": 1800})])
    out = request_device_code(http, "client-123")
    assert out["user_code"] == "ABCD-1234"
    call = http.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://oauth2.googleapis.com/device/code"
    assert call["data"] == {"client_id": "client-123", "scope": DRIVE_FILE_SCOPE}


def test_request_device_code_raises_on_error():
    http = FakeHttp([resp(400, {"error": "invalid_client"})])
    with pytest.raises(DriveError):
        request_device_code(http, "bad")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_drive_oauth.py -v`
Expected: FAIL (`No module named 'savemanager.drive'`).

- [ ] **Step 4: Implement the start of `drive.py`**

```python
# defaults/py_modules/savemanager/drive.py
import json

DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
_DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
_TOKEN_URL = "https://oauth2.googleapis.com/token"


class DriveError(Exception):
    pass


class HttpResponse:
    def __init__(self, status, body=b"", headers=None):
        self.status = status
        self.body = body if isinstance(body, bytes) else (body or "").encode("utf-8")
        self.headers = headers or {}

    def json(self) -> dict:
        return json.loads(self.body or b"{}")

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


def request_device_code(http, client_id, scope=DRIVE_FILE_SCOPE) -> dict:
    """Start the OAuth 2.0 device flow. Returns Google's device/code response dict
    (device_code, user_code, verification_url, interval, expires_in)."""
    r = http("POST", _DEVICE_CODE_URL, data={"client_id": client_id, "scope": scope})
    if r.status != 200:
        raise DriveError(f"device/code failed: {r.status} {r.text}")
    return r.json()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_drive_oauth.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add defaults/py_modules/savemanager/drive.py tests/drive_fakes.py tests/test_drive_oauth.py
git commit -m "feat: Drive HttpResponse + OAuth device-code start (transport-injected)"
```

---

## Task 2: OAuth `poll_token` + `refresh_access_token`

**Files:** Modify `defaults/py_modules/savemanager/drive.py`; Test `tests/test_drive_oauth.py` (append).

- [ ] **Step 1: Append the failing tests to `tests/test_drive_oauth.py`**

```python
from savemanager.drive import poll_token, refresh_access_token


def _poll(http_resp):
    return poll_token(FakeHttp([http_resp]), "cid", "secret", "DC")


def test_poll_token_success_returns_tokens():
    out = _poll(resp(200, {"access_token": "AT", "refresh_token": "RT", "expires_in": 3599}))
    assert out["status"] == "ok"
    assert out["tokens"]["refresh_token"] == "RT"


def test_poll_token_pending():
    assert _poll(resp(428, {"error": "authorization_pending"}))["status"] == "pending"


def test_poll_token_slow_down():
    assert _poll(resp(403, {"error": "slow_down"}))["status"] == "slow_down"


def test_poll_token_denied():
    assert _poll(resp(403, {"error": "access_denied"}))["status"] == "denied"


def test_poll_token_expired():
    assert _poll(resp(400, {"error": "expired_token"}))["status"] == "expired"


def test_poll_token_unexpected_raises():
    with pytest.raises(DriveError):
        _poll(resp(500, {"error": "boom"}))


def test_refresh_access_token_returns_new_token():
    http = FakeHttp([resp(200, {"access_token": "NEW", "expires_in": 3599})])
    assert refresh_access_token(http, "cid", "secret", "RT") == "NEW"
    assert http.calls[0]["data"]["grant_type"] == "refresh_token"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_drive_oauth.py -v`
Expected: FAIL (`ImportError: cannot import name 'poll_token'`).

- [ ] **Step 3: Append to `drive.py`**

```python
def poll_token(http, client_id, client_secret, device_code) -> dict:
    """Poll the token endpoint once. Returns {"status": ...} with the device-flow state;
    on success also includes "tokens" (access_token, refresh_token, expires_in)."""
    r = http("POST", _TOKEN_URL, data={
        "client_id": client_id,
        "client_secret": client_secret,
        "device_code": device_code,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
    })
    if r.status == 200:
        return {"status": "ok", "tokens": r.json()}
    try:
        err = r.json().get("error")
    except Exception:
        err = None
    if err == "authorization_pending":
        return {"status": "pending"}
    if err == "slow_down":
        return {"status": "slow_down"}
    if err == "access_denied":
        return {"status": "denied"}
    if err in ("expired_token", "invalid_grant"):
        return {"status": "expired"}
    raise DriveError(f"token poll failed: {r.status} {r.text}")


def refresh_access_token(http, client_id, client_secret, refresh_token) -> str:
    r = http("POST", _TOKEN_URL, data={
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    })
    if r.status != 200:
        raise DriveError(f"token refresh failed: {r.status} {r.text}")
    return r.json()["access_token"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_drive_oauth.py -v`
Expected: PASS (9 tests total).

- [ ] **Step 5: Commit**

```bash
git add defaults/py_modules/savemanager/drive.py tests/test_drive_oauth.py
git commit -m "feat: Drive OAuth poll_token state machine + refresh_access_token"
```

---

## Task 3: `DriveClient` folder operations

**Files:** Modify `defaults/py_modules/savemanager/drive.py`; Test `tests/test_drive_client.py`.

- [ ] **Step 1: Write the failing test `tests/test_drive_client.py`**

```python
import pytest
from savemanager.drive import DriveClient, DriveError
from tests.drive_fakes import FakeHttp, resp


def _client(responses):
    http = FakeHttp(responses)
    return DriveClient(http, "ACCESS"), http


def test_list_children_queries_parent_and_returns_files():
    client, http = _client([resp(200, {"files": [{"id": "a", "name": "X"}]})])
    assert client.list_children("PARENT") == [{"id": "a", "name": "X"}]
    call = http.calls[0]
    assert call["method"] == "GET"
    assert "'PARENT' in parents and trashed=false" in call["params"]["q"]
    assert call["headers"]["Authorization"] == "Bearer ACCESS"


def test_find_folder_returns_id_or_none():
    client, _ = _client([resp(200, {"files": [{"id": "fid", "name": "Game"}]})])
    assert client.find_folder("Game", "ROOT") == "fid"
    client2, _ = _client([resp(200, {"files": []})])
    assert client2.find_folder("Game", "ROOT") is None


def test_create_folder_posts_metadata_and_returns_id():
    client, http = _client([resp(200, {"id": "newfid"})])
    assert client.create_folder("Game", "ROOT") == "newfid"
    body = http.calls[0]["json_body"]
    assert body["name"] == "Game"
    assert body["mimeType"] == "application/vnd.google-apps.folder"
    assert body["parents"] == ["ROOT"]


def test_find_or_create_uses_existing_then_creates():
    client, _ = _client([resp(200, {"files": [{"id": "exists"}]})])
    assert client.find_or_create_folder("G", "ROOT") == "exists"
    client2, _ = _client([resp(200, {"files": []}), resp(200, {"id": "made"})])
    assert client2.find_or_create_folder("G", "ROOT") == "made"


def test_non_success_raises():
    client, _ = _client([resp(500, {"error": "x"})])
    with pytest.raises(DriveError):
        client.list_children("P")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_drive_client.py -v`
Expected: FAIL (`ImportError: cannot import name 'DriveClient'`).

- [ ] **Step 3: Append the `DriveClient` class (folder ops) to `drive.py`**

```python
_DRIVE_V3 = "https://www.googleapis.com/drive/v3"
_UPLOAD_V3 = "https://www.googleapis.com/upload/drive/v3"
_FOLDER_MIME = "application/vnd.google-apps.folder"


class DriveClient:
    """Thin Drive v3 client over an injected http transport. drive.file scope: it only
    ever sees files it created."""

    def __init__(self, http, access_token):
        self.http = http
        self.token = access_token

    def _auth(self, extra=None) -> dict:
        h = {"Authorization": f"Bearer {self.token}"}
        if extra:
            h.update(extra)
        return h

    def list_children(self, parent_id) -> list:
        r = self.http("GET", f"{_DRIVE_V3}/files", headers=self._auth(),
                      params={"q": f"'{parent_id}' in parents and trashed=false",
                              "fields": "files(id,name)"})
        if r.status != 200:
            raise DriveError(f"list_children: {r.status} {r.text}")
        return r.json().get("files", [])

    def find_folder(self, name, parent_id):
        r = self.http("GET", f"{_DRIVE_V3}/files", headers=self._auth(),
                      params={"q": (f"name='{name}' and '{parent_id}' in parents and "
                                    f"mimeType='{_FOLDER_MIME}' and trashed=false"),
                              "fields": "files(id,name)"})
        if r.status != 200:
            raise DriveError(f"find_folder: {r.status} {r.text}")
        files = r.json().get("files", [])
        return files[0]["id"] if files else None

    def create_folder(self, name, parent_id) -> str:
        r = self.http("POST", f"{_DRIVE_V3}/files",
                      headers=self._auth({"Content-Type": "application/json"}),
                      json_body={"name": name, "mimeType": _FOLDER_MIME, "parents": [parent_id]})
        if r.status not in (200, 201):
            raise DriveError(f"create_folder: {r.status} {r.text}")
        return r.json()["id"]

    def find_or_create_folder(self, name, parent_id) -> str:
        return self.find_folder(name, parent_id) or self.create_folder(name, parent_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_drive_client.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add defaults/py_modules/savemanager/drive.py tests/test_drive_client.py
git commit -m "feat: DriveClient folder operations (list/find/create)"
```

---

## Task 4: `DriveClient` file operations

**Files:** Modify `defaults/py_modules/savemanager/drive.py`; Test `tests/test_drive_client.py` (append).

- [ ] **Step 1: Append the failing tests to `tests/test_drive_client.py`**

```python
def test_upload_file_multipart_includes_name_and_content():
    client, http = _client([resp(200, {"id": "up1"})])
    assert client.upload_file("save1.sav", "FOLDER", b"\x00\x01DATA") == "up1"
    call = http.calls[0]
    assert call["method"] == "POST" and call["url"].endswith("/upload/drive/v3/files")
    assert call["params"]["uploadType"] == "multipart"
    body = call["data"]
    assert isinstance(body, (bytes, bytearray))
    assert b"save1.sav" in body and b"\x00\x01DATA" in body          # metadata + media present
    assert call["headers"]["Content-Type"].startswith("multipart/related; boundary=")


def test_update_file_patches_media():
    client, http = _client([resp(200, {"id": "f1"})])
    client.update_file("f1", b"NEW")
    call = http.calls[0]
    assert call["method"] == "PATCH" and call["url"].endswith("/upload/drive/v3/files/f1")
    assert call["params"]["uploadType"] == "media" and call["data"] == b"NEW"


def test_delete_file_issues_delete():
    client, http = _client([resp(204, body=b"")])
    client.delete_file("f9")
    assert http.calls[0]["method"] == "DELETE" and http.calls[0]["url"].endswith("/files/f9")


def test_download_file_returns_body_bytes():
    client, http = _client([resp(200, body=b"RAWBYTES")])
    assert client.download_file("f1") == b"RAWBYTES"
    assert http.calls[0]["params"]["alt"] == "media"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_drive_client.py -v`
Expected: FAIL (`AttributeError: 'DriveClient' object has no attribute 'upload_file'`).

- [ ] **Step 3: Append the file-op methods to the `DriveClient` class in `drive.py`**

```python
    def upload_file(self, name, parent_id, content: bytes) -> str:
        boundary = "smdrive7e1bd0b1boundary"
        meta = json.dumps({"name": name, "parents": [parent_id]}).encode("utf-8")
        b = boundary.encode("utf-8")
        body = (b"--" + b + b"\r\n"
                b"Content-Type: application/json; charset=UTF-8\r\n\r\n" + meta + b"\r\n"
                b"--" + b + b"\r\n"
                b"Content-Type: application/octet-stream\r\n\r\n" + content + b"\r\n"
                b"--" + b + b"--")
        r = self.http("POST", f"{_UPLOAD_V3}/files",
                      headers=self._auth({"Content-Type": f"multipart/related; boundary={boundary}"}),
                      params={"uploadType": "multipart", "fields": "id"}, data=body)
        if r.status not in (200, 201):
            raise DriveError(f"upload_file: {r.status} {r.text}")
        return r.json()["id"]

    def update_file(self, file_id, content: bytes) -> None:
        r = self.http("PATCH", f"{_UPLOAD_V3}/files/{file_id}",
                      headers=self._auth({"Content-Type": "application/octet-stream"}),
                      params={"uploadType": "media"}, data=content)
        if r.status not in (200, 201):
            raise DriveError(f"update_file: {r.status} {r.text}")

    def delete_file(self, file_id) -> None:
        r = self.http("DELETE", f"{_DRIVE_V3}/files/{file_id}", headers=self._auth())
        if r.status not in (200, 204):
            raise DriveError(f"delete_file: {r.status} {r.text}")

    def download_file(self, file_id) -> bytes:
        r = self.http("GET", f"{_DRIVE_V3}/files/{file_id}", headers=self._auth(),
                      params={"alt": "media"})
        if r.status != 200:
            raise DriveError(f"download_file: {r.status} {r.text}")
        return r.body
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_drive_client.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add defaults/py_modules/savemanager/drive.py tests/test_drive_client.py
git commit -m "feat: DriveClient file operations (upload/update/delete/download)"
```

---

## Task 5: `mirror.py` — index + `plan_sync` reconciliation

**Files:** Create `defaults/py_modules/savemanager/mirror.py`; Test `tests/test_mirror.py`.

- [ ] **Step 1: Write the failing test `tests/test_mirror.py`**

```python
from savemanager.mirror import empty_index, plan_sync


def test_empty_index_shape():
    idx = empty_index(281990)
    assert idx == {"appId": 281990, "gameFolderId": None, "versions": {}, "schemaVersion": 1}


def test_plan_sync_uploads_missing_and_prunes_removed():
    remote = {"appId": 1, "gameFolderId": "g", "schemaVersion": 1,
              "versions": {"v_old": {"label": "old", "folderId": "f1", "fileIds": {}},
                           "v_keep": {"label": "keep", "folderId": "f2", "fileIds": {}}}}
    plan = plan_sync(["v_keep", "v_new"], remote)        # local kept set
    assert plan["to_upload"] == ["v_new"]                # not yet on Drive
    assert plan["to_prune"] == ["v_old"]                 # on Drive but no longer kept


def test_plan_sync_empty_remote_uploads_all():
    plan = plan_sync(["a", "b"], empty_index(1))
    assert plan["to_upload"] == ["a", "b"] and plan["to_prune"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mirror.py -v`
Expected: FAIL (`No module named 'savemanager.mirror'`).

- [ ] **Step 3: Implement the start of `mirror.py`**

```python
# defaults/py_modules/savemanager/mirror.py


def empty_index(app_id) -> dict:
    return {"appId": app_id, "gameFolderId": None, "versions": {}, "schemaVersion": 1}


def plan_sync(local_kept_vids, remote_index) -> dict:
    """Compare the local kept version-id list to what the remote index already has.
    Returns the version ids to upload (kept but not remote) and to prune (remote but
    no longer kept). Order of to_upload follows local_kept_vids."""
    remote_vids = set(remote_index.get("versions", {}).keys())
    kept = list(local_kept_vids)
    kept_set = set(kept)
    to_upload = [v for v in kept if v not in remote_vids]
    to_prune = [v for v in remote_vids if v not in kept_set]
    return {"to_upload": to_upload, "to_prune": to_prune}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mirror.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add defaults/py_modules/savemanager/mirror.py tests/test_mirror.py
git commit -m "feat: mirror index schema + plan_sync reconciliation"
```

---

## Task 6: `mirror.sync_versions` — orchestrate uploads + prunes

**Files:** Modify `defaults/py_modules/savemanager/mirror.py`, `tests/drive_fakes.py` (append `FakeDriveClient`); Test `tests/test_mirror.py` (append).

- [ ] **Step 1: Append `FakeDriveClient` to `tests/drive_fakes.py`**

```python
class FakeDriveClient:
    """In-memory stand-in for DriveClient: records folders/files and deletions."""
    def __init__(self):
        self.folders = {}     # id -> (name, parent)
        self.files = {}       # id -> (name, parent, content)
        self.deleted = []
        self._n = 0

    def _new_id(self, prefix):
        self._n += 1
        return f"{prefix}{self._n}"

    def find_or_create_folder(self, name, parent_id):
        for fid, (n, p) in self.folders.items():
            if n == name and p == parent_id:
                return fid
        return self.create_folder(name, parent_id)

    def create_folder(self, name, parent_id):
        fid = self._new_id("fld")
        self.folders[fid] = (name, parent_id)
        return fid

    def upload_file(self, name, parent_id, content):
        fid = self._new_id("file")
        self.files[fid] = (name, parent_id, content)
        return fid

    def delete_file(self, file_id):
        self.deleted.append(file_id)
        self.folders.pop(file_id, None)
        self.files.pop(file_id, None)
```

- [ ] **Step 2: Append the failing test to `tests/test_mirror.py`**

```python
from savemanager.mirror import sync_versions
from tests.drive_fakes import FakeDriveClient


def test_sync_versions_uploads_new_prunes_old_and_updates_index():
    client = FakeDriveClient()
    remote = {"appId": 1, "gameFolderId": "GAME", "schemaVersion": 1,
              "versions": {"v_old": {"label": "old", "folderId": "OLDF", "fileIds": {"a.sav": "x"}}}}
    kept = [{"versionId": "v_new", "label": "2026-06-18 (auto)"}]   # v_old no longer kept
    files = {"v_new": {"XComGame/SaveData/save1.sav": b"AAAAA"}}

    idx = sync_versions(client, "ROOT", "XCOM 2", kept, remote,
                        load_version_files=lambda vid: files[vid])

    # uploaded v_new's file into a freshly created version folder
    assert "v_new" in idx["versions"]
    vfolder = idx["versions"]["v_new"]["folderId"]
    assert any(p == vfolder for (_n, p, _c) in client.files.values())
    assert list(idx["versions"]["v_new"]["fileIds"].keys()) == ["XComGame/SaveData/save1.sav"]
    # pruned v_old's Drive folder and dropped it from the index
    assert "OLDF" in client.deleted
    assert "v_old" not in idx["versions"]
    # game folder reused from the index
    assert idx["gameFolderId"] == "GAME"


def test_sync_versions_creates_game_folder_when_absent():
    client = FakeDriveClient()
    kept = [{"versionId": "v1", "label": "L"}]
    idx = sync_versions(client, "ROOT", "Game", kept, empty_index(1),
                        load_version_files=lambda vid: {"s.sav": b"X"})
    assert idx["gameFolderId"] is not None
    assert idx["gameFolderId"] in client.folders
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_mirror.py -v`
Expected: FAIL (`ImportError: cannot import name 'sync_versions'`).

- [ ] **Step 4: Append `sync_versions` to `mirror.py`**

```python
def sync_versions(client, root_folder_id, game_name, kept_versions, remote_index,
                  load_version_files) -> dict:
    """Make Drive mirror the local kept set. Uploads each not-yet-remote version's files
    into a per-version Drive folder, prunes versions no longer kept, and returns the
    updated index (the caller persists index.json to Drive last, as the commit point).

    kept_versions: ordered list of {"versionId", "label"}.
    load_version_files(version_id) -> {relpath: bytes}.
    """
    index = dict(remote_index)
    index.setdefault("versions", {})
    index["versions"] = dict(index["versions"])
    plan = plan_sync([v["versionId"] for v in kept_versions], index)

    game_folder = index.get("gameFolderId") or client.find_or_create_folder(game_name, root_folder_id)
    index["gameFolderId"] = game_folder

    label_by_vid = {v["versionId"]: v["label"] for v in kept_versions}
    for vid in plan["to_upload"]:
        vfolder = client.create_folder(label_by_vid[vid], game_folder)
        file_ids = {}
        for relpath, content in load_version_files(vid).items():
            drive_name = relpath.replace("/", "_")          # flatten into the version folder
            file_ids[relpath] = client.upload_file(drive_name, vfolder, content)
        index["versions"][vid] = {"label": label_by_vid[vid], "folderId": vfolder, "fileIds": file_ids}

    for vid in plan["to_prune"]:
        meta = index["versions"].pop(vid, None)
        if meta and meta.get("folderId"):
            client.delete_file(meta["folderId"])

    return index
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_mirror.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Run the full suite (no regressions)**

Run: `python -m pytest -q`
Expected: all pass (the M1–M3 suite + the new Drive-core tests).

- [ ] **Step 7: Commit**

```bash
git add defaults/py_modules/savemanager/mirror.py tests/drive_fakes.py tests/test_mirror.py
git commit -m "feat: mirror.sync_versions — upload new + prune old + updated index"
```

---

## Self-review (done while writing)

- **Spec coverage (§6, the offline-testable slice):** native Drive REST client ✓ (Tasks 3–4); `drive.file` scope constant ✓ (Task 1); device-code OAuth flow — start/poll/refresh ✓ (Tasks 1–2); one-way mirror reconciliation (upload missing, prune removed) ✓ (Tasks 5–6); remote `index.json` schema ✓ (Tasks 5–6, returned as the commit point for the caller to persist last). Deferred to M4b (documented): real `requests`/`certifi` transport, the device-flow polling LOOP (with `asyncio.sleep`), token persistence, Engine/`main.py` link+sync wiring, the QAM UI, resumable uploads, restore-from-Drive.
- **Type consistency:** the injected `http(method, url, *, headers, params, data, json_body)` signature, `HttpResponse` (`.status`/`.body`/`.json()`/`.text`), the `DriveClient` method names, and the `index.json` shape (`gameFolderId`, `versions[vid].{label,folderId,fileIds}`) are used identically across tasks and by `sync_versions`. `sync_versions` calls only `find_or_create_folder`/`create_folder`/`upload_file`/`delete_file` — exactly what both the real `DriveClient` and `FakeDriveClient` implement.
- **No placeholders:** every step has complete, runnable code.
- **Note for M4b:** `sync_versions` flattens `a/b.sav` → `a_b.sav` per version folder (browsable, simple); nested Drive folders are a possible later refinement. The label format and `load_version_files` (reading `version_dir/root<suffix>/<path>`) are supplied by M4b's wiring.

---

## After M4a — M4b (next plan) and beyond

- **M4b — Drive integration:** real `requests`+`certifi` transport (vendored, in-process HTTPS — never shell out, per decky #729); persist `client_id`/`client_secret`/`refresh_token` in `DECKY_PLUGIN_SETTINGS_DIR` (git-ignored); Engine/`main.py`: `link_drive` (device-flow poll loop with `asyncio.sleep` + `decky.emit` of the user code), `sync_drive` (non-blocking task, refresh-on-401, progress events); per-game `driveMirror` toggle already in config; QAM Drive section (enter client id/secret, show user code + QR, Sync now, progress). On-device verified. Appendix §11 already documents the user's one-time Google client setup.
- **Backend revert guard / `locking.py`**; **discovery hardening**; "pinned exceeds keep cap" warning.
```
