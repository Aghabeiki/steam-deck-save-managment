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
