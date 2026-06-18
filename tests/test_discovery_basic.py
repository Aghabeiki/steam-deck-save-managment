from savemanager.discovery import get_account_ids, remotecache_path, read_entries
from tests.fixtures import make_steam_tree


def test_get_account_ids_skips_zero(tmp_path):
    steam_root, acct, _ = make_steam_tree(tmp_path)
    import os
    os.makedirs(os.path.join(steam_root, "userdata", "0"), exist_ok=True)
    assert get_account_ids(steam_root) == [acct]


def test_read_entries_returns_parsed_rows(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path)
    entries = read_entries(steam_root, acct, app)
    assert {e.path for e in entries} == {"save1.sav", "profile.bin"}


def test_read_entries_missing_file_is_empty(tmp_path):
    steam_root, acct, _ = make_steam_tree(tmp_path)
    assert read_entries(steam_root, acct, 999999) == []
