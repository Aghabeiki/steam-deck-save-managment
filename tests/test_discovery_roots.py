import os
from savemanager.discovery import (
    parse_installdir, rcf_is_valid, resolve_save_roots, read_entries,
)
from tests.fixtures import make_steam_tree


def test_parse_installdir(tmp_path):
    steam_root, _, app = make_steam_tree(tmp_path)
    assert parse_installdir(steam_root, app) == "XCOM 2"


def test_parse_installdir_missing(tmp_path):
    steam_root, _, _ = make_steam_tree(tmp_path)
    assert parse_installdir(steam_root, 999999) is None


def test_rcf_is_valid_true_when_a_file_exists(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path)
    entries = read_entries(steam_root, acct, app)
    remote = os.path.join(steam_root, "userdata", str(acct), str(app), "remote")
    assert rcf_is_valid(remote, entries) is True


def test_rcf_is_valid_false_for_empty_dir(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path)
    entries = read_entries(steam_root, acct, app)
    empty = os.path.join(str(tmp_path), "empty")
    os.makedirs(empty)
    assert rcf_is_valid(empty, entries) is False


def test_resolve_save_roots_finds_remote_with_suffix(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path)
    entries = read_entries(steam_root, acct, app)
    roots = resolve_save_roots(steam_root, acct, app, entries, "XCOM 2")
    remote = os.path.join(steam_root, "userdata", str(acct), str(app), "remote")
    assert roots == {remote: ""}


def test_resolve_save_roots_empty_when_no_files(tmp_path):
    steam_root, acct, app = make_steam_tree(tmp_path, with_saves=False)
    entries = read_entries(steam_root, acct, app)
    assert resolve_save_roots(steam_root, acct, app, entries, "XCOM 2") == {}
