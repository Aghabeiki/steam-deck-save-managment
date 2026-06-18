from savemanager.config import get_game_settings, set_game_setting, DEFAULTS


def test_defaults_when_no_file(tmp_path):
    s = get_game_settings(str(tmp_path), 281990)
    assert s["keepCount"] == DEFAULTS["keepCount"] == 20


def test_set_then_get_roundtrip(tmp_path):
    merged = set_game_setting(str(tmp_path), 281990, "keepCount", 7)
    assert merged["keepCount"] == 7
    assert get_game_settings(str(tmp_path), 281990)["keepCount"] == 7
    # unspecified keys still fall back to defaults
    assert get_game_settings(str(tmp_path), 281990)["ignoreUnchanged"] is True


def test_corrupt_game_json_falls_back_to_defaults(tmp_path):
    import os
    from savemanager.store import game_dir
    os.makedirs(game_dir(str(tmp_path), 281990), exist_ok=True)
    with open(os.path.join(game_dir(str(tmp_path), 281990), "game.json"), "w") as f:
        f.write("not json")
    assert get_game_settings(str(tmp_path), 281990)["keepCount"] == 20
