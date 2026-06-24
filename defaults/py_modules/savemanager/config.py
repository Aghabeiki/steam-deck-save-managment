# defaults/py_modules/savemanager/config.py
import json
import os

from .store import game_dir

DEFAULTS = {
    "keepCount": 20,
    "autoBackupOnExit": False,   # honored in M3
    "driveMirror": False,        # honored in M4b: auto-mirror after a backup
    "ignoreUnchanged": True,
}


def _game_json_path(data_root, app_id) -> str:
    return os.path.join(game_dir(data_root, app_id), "game.json")


def get_game_settings(data_root, app_id) -> dict:
    """Return per-game settings merged over DEFAULTS (corruption-tolerant)."""
    try:
        with open(_game_json_path(data_root, app_id)) as f:
            data = json.load(f)
        stored = data.get("settings", {}) if isinstance(data, dict) else {}
        if not isinstance(stored, dict):
            stored = {}
    except (OSError, ValueError):
        stored = {}
    return {**DEFAULTS, **stored}


def set_game_setting(data_root, app_id, key, value) -> dict:
    """Persist one setting; return the merged settings dict."""
    path = _game_json_path(data_root, app_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data.setdefault("appId", app_id)
    data.setdefault("schemaVersion", 1)
    settings = data.get("settings", {})
    settings[key] = value
    data["settings"] = settings
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return {**DEFAULTS, **settings}
