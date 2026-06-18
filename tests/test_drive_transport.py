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
