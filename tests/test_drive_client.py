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


def test_find_folder_escapes_apostrophe_in_name():
    client, http = _client([resp(200, {"files": []})])
    client.find_folder("Baldur's Gate", "ROOT")
    assert "name='Baldur\\'s Gate'" in http.calls[0]["params"]["q"]   # apostrophe escaped


def test_upload_file_multipart_is_well_formed():
    client, http = _client([resp(200, {"id": "u"})])
    client.upload_file("s.sav", "F", b"PAYLOAD")
    body = http.calls[0]["data"]
    boundary = http.calls[0]["headers"]["Content-Type"].split("boundary=")[1].encode("ascii")
    assert body.count(b"--" + boundary) == 3                      # 2 part delimiters + 1 closing
    assert body.endswith(b"--" + boundary + b"--")                # proper closing delimiter
    assert b"application/json" in body and b"application/octet-stream" in body
    assert b"PAYLOAD" in body


def test_delete_file_tolerates_404():
    client, _ = _client([resp(404, {"error": "notFound"})])
    client.delete_file("gone")                                    # must NOT raise


def test_trash_file_patches_trashed_true():
    client, http = _client([resp(200, {"id": "f1"})])
    client.trash_file("f1")
    call = http.calls[0]
    assert call["method"] == "PATCH" and call["url"].endswith("/drive/v3/files/f1")
    assert call["json_body"] == {"trashed": True}


def test_trash_file_tolerates_404():
    client, _ = _client([resp(404, {"error": "notFound"})])
    client.trash_file("gone")        # must not raise


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
