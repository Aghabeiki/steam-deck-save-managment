# defaults/py_modules/savemanager/drive.py
import json
import os

DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
_DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
_TOKEN_URL = "https://oauth2.googleapis.com/token"


class DriveError(Exception):
    pass


class DriveAuthError(DriveError):
    """Raised when the refresh token is revoked/expired (needs re-link)."""
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
        try:
            err = r.json().get("error")
        except (ValueError, AttributeError):
            err = None
        if err == "invalid_grant":
            raise DriveAuthError("refresh token revoked/expired (invalid_grant)")
        raise DriveError(f"token refresh failed: {r.status} {r.text}")
    return r.json()["access_token"]


_DRIVE_V3 = "https://www.googleapis.com/drive/v3"
_UPLOAD_V3 = "https://www.googleapis.com/upload/drive/v3"
_FOLDER_MIME = "application/vnd.google-apps.folder"


def _q_value(s: str) -> str:
    """Escape a string for safe embedding inside a Drive query literal (q="name='...'")."""
    return s.replace("\\", "\\\\").replace("'", "\\'")


def _require_id(r, what) -> str:
    try:
        return r.json()["id"]
    except (ValueError, KeyError, TypeError):
        raise DriveError(f"{what}: malformed success response: {r.status} {r.text}")


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

    def create_folder(self, name, parent_id) -> str:
        r = self.http("POST", f"{_DRIVE_V3}/files",
                      headers=self._auth({"Content-Type": "application/json"}),
                      json_body={"name": name, "mimeType": _FOLDER_MIME, "parents": [parent_id]})
        if r.status not in (200, 201):
            raise DriveError(f"create_folder: {r.status} {r.text}")
        return _require_id(r, "create_folder")

    def find_or_create_folder(self, name, parent_id) -> str:
        return self.find_folder(name, parent_id) or self.create_folder(name, parent_id)

    def upload_file(self, name, parent_id, content: bytes) -> str:
        boundary = "smdrive" + os.urandom(12).hex()
        b = boundary.encode("ascii")
        while (b"--" + b) in content:
            boundary = "smdrive" + os.urandom(12).hex()
            b = boundary.encode("ascii")
        meta = json.dumps({"name": name, "parents": [parent_id]}).encode("utf-8")
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
        return _require_id(r, "upload_file")

    def update_file(self, file_id, content: bytes) -> None:
        r = self.http("PATCH", f"{_UPLOAD_V3}/files/{file_id}",
                      headers=self._auth({"Content-Type": "application/octet-stream"}),
                      params={"uploadType": "media"}, data=content)
        if r.status not in (200, 201):
            raise DriveError(f"update_file: {r.status} {r.text}")

    def delete_file(self, file_id) -> None:
        r = self.http("DELETE", f"{_DRIVE_V3}/files/{file_id}", headers=self._auth())
        if r.status not in (200, 204, 404):     # 404 == already gone -> idempotent
            raise DriveError(f"delete_file: {r.status} {r.text}")

    def trash_file(self, file_id) -> None:
        r = self.http("PATCH", f"{_DRIVE_V3}/files/{file_id}",
                      headers=self._auth({"Content-Type": "application/json"}),
                      json_body={"trashed": True})
        if r.status not in (200, 204, 404):     # 404 == already gone -> idempotent
            raise DriveError(f"trash_file: {r.status} {r.text}")

    def download_file(self, file_id) -> bytes:
        r = self.http("GET", f"{_DRIVE_V3}/files/{file_id}", headers=self._auth(),
                      params={"alt": "media"})
        if r.status != 200:
            raise DriveError(f"download_file: {r.status} {r.text}")
        return r.body
