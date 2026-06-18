import os
from savemanager.api import Engine
from tests.fixtures import make_steam_tree


def _engine(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path)
    data_root = os.path.join(str(tmp_path), "data")
    eng = Engine(data_root, steam_root)
    eng.set_account_id(acct)
    return eng, app


def test_find_supported_filters_to_cloud_games(tmp_path):
    eng, app = _engine(tmp_path)
    result = eng.find_supported([{"appId": app, "name": "XCOM 2"},
                                 {"appId": 999999, "name": "Nope"}])
    assert result == [{"appId": app, "name": "XCOM 2"}]


def test_do_backup_then_get_versions(tmp_path):
    eng, app = _engine(tmp_path)
    entry = eng.do_backup({"appId": app, "name": "XCOM 2"}, now_ms=1000, rand_hex="aaa")
    assert entry["versionId"] == "v_1000_aaa"
    assert eng.get_versions(app)["head"]["versionId"] == "v_1000_aaa"


def test_set_account_id_is_idempotent(tmp_path):
    eng, _ = _engine(tmp_path)
    eng.set_account_id(123)
    assert eng.account_ids == [123]


def test_find_supported_isolates_bad_game_info(tmp_path):
    eng, app = _engine(tmp_path)
    # second entry is malformed (missing "appId") -> must be skipped, not abort the scan
    result = eng.find_supported([{"appId": app, "name": "XCOM 2"}, {"name": "broken"}])
    assert result == [{"appId": app, "name": "XCOM 2"}]


def test_falls_back_to_discovered_account(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path)
    eng = Engine(os.path.join(str(tmp_path), "data"), steam_root)  # no set_account_id
    assert eng.find_supported([{"appId": app, "name": "X"}]) == [{"appId": app, "name": "X"}]
