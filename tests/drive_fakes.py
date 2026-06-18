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


class FakeDriveClient:
    """In-memory stand-in for DriveClient: records folders/files and deletions."""
    def __init__(self):
        self.folders = {}     # id -> (name, parent)
        self.files = {}       # id -> (name, parent, content)
        self.deleted = []
        self.trashed = []
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

    def trash_file(self, file_id):
        self.trashed.append(file_id)
        self.folders.pop(file_id, None)
        self.files.pop(file_id, None)

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
