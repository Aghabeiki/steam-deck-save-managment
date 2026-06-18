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


def test_refresh_raises_driveauth_on_invalid_grant():
    from savemanager.drive import DriveAuthError
    http = FakeHttp([resp(400, {"error": "invalid_grant"})])
    with pytest.raises(DriveAuthError):
        refresh_access_token(http, "cid", "secret", "RT")
