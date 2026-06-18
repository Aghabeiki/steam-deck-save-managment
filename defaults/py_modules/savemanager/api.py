# defaults/py_modules/savemanager/api.py
import json
import os
from .config import get_game_settings, set_game_setting
from .curation import remove_version, set_name, set_pinned
from .discovery import get_account_ids, parse_installdir, remotecache_path
from .mirror import download_version, list_remote_versions, read_index, sync_game
from .versioning import cull_versions, do_backup, import_version, is_supported, kept_versions_for, list_versions, load_version_files, resume_pending_revert, revert_to


class Engine:
    """Pure, testable facade over the engine. main.py supplies time/randomness."""

    def __init__(self, data_root: str, steam_root: str):
        self.data_root = data_root
        self.steam_root = steam_root
        self.account_ids: list[int] = []

    def set_account_id(self, account_id: int) -> None:
        if account_id not in self.account_ids:
            self.account_ids.append(account_id)

    def _primary(self):
        if self.account_ids:
            return self.account_ids[0]
        discovered = sorted(get_account_ids(self.steam_root))
        return discovered[0] if discovered else None

    def find_supported(self, game_infos: list) -> list:
        acct = self._primary()
        if acct is None:
            return []
        out = []
        for g in game_infos:
            try:
                installdir = parse_installdir(self.steam_root, g["appId"])
                if is_supported(self.steam_root, acct, g["appId"], installdir):
                    out.append(g)
            except Exception:
                continue
        return out

    def do_backup(self, game_info: dict, now_ms: int, rand_hex: str):
        acct = self._primary()
        if acct is None:
            return None
        keep = get_game_settings(self.data_root, game_info["appId"])["keepCount"]
        return do_backup(self.data_root, self.steam_root, acct, game_info,
                         now_ms, rand_hex, keep_count=keep)

    def get_versions(self, app_id: int) -> dict:
        acct = self._primary()
        if acct is not None:
            resume_pending_revert(self.data_root, self.steam_root, acct, app_id)
        return list_versions(self.data_root, app_id)

    def revert(self, game_info: dict, target_id: str, now_ms: int, rand_hex: str):
        acct = self._primary()
        if acct is None:
            return None
        app_id = game_info["appId"]
        head = revert_to(self.data_root, self.steam_root, acct,
                         app_id, target_id, now_ms, rand_hex)
        if head is not None:
            keep = get_game_settings(self.data_root, app_id)["keepCount"]
            cull_versions(self.data_root, app_id, keep)
        return head

    def set_pinned(self, app_id: int, version_id: str, pinned: bool) -> bool:
        return set_pinned(self.data_root, app_id, version_id, pinned)

    def set_name(self, app_id: int, version_id: str, name) -> bool:
        return set_name(self.data_root, app_id, version_id, name)

    def remove_version(self, app_id: int, version_id: str) -> bool:
        return remove_version(self.data_root, app_id, version_id)

    def get_settings(self, app_id: int) -> dict:
        return get_game_settings(self.data_root, app_id)

    def set_keep_count(self, app_id: int, keep_count: int) -> dict:
        return set_game_setting(self.data_root, app_id, "keepCount", max(1, int(keep_count)))

    def set_auto_backup(self, app_id: int, enabled: bool) -> dict:
        return set_game_setting(self.data_root, app_id, "autoBackupOnExit", bool(enabled))

    def set_drive_mirror(self, app_id, enabled) -> dict:
        return set_game_setting(self.data_root, app_id, "driveMirror", bool(enabled))

    def do_backup_on_exit(self, game_info: dict, now_ms: int, rand_hex: str):
        acct = self._primary()
        if acct is None:
            return None
        app_id = game_info["appId"]
        settings = get_game_settings(self.data_root, app_id)
        if not settings.get("autoBackupOnExit"):
            return None
        return do_backup(self.data_root, self.steam_root, acct, game_info, now_ms, rand_hex,
                         kind="auto", reason="game-exit", keep_count=settings["keepCount"])

    def remotecache_mtime(self, app_id: int) -> float:
        accounts = self.account_ids or get_account_ids(self.steam_root)
        mtimes = []
        for acct in accounts:
            try:
                mtimes.append(os.path.getmtime(remotecache_path(self.steam_root, acct, app_id)))
            except OSError:
                pass
        return max(mtimes) if mtimes else 0.0

    def _secrets_path(self):
        return os.path.join(self.data_root, "drive_secrets.json")

    def _read_secrets(self) -> dict:
        try:
            with open(self._secrets_path()) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _write_secrets(self, secrets) -> None:
        path = self._secrets_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(secrets, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)

    def set_drive_client(self, client_id, client_secret) -> None:
        s = self._read_secrets()
        s["client_id"] = client_id
        s["client_secret"] = client_secret
        self._write_secrets(s)

    def set_drive_refresh_token(self, refresh_token) -> None:
        s = self._read_secrets()
        s["refresh_token"] = refresh_token
        self._write_secrets(s)

    def get_drive_status(self) -> dict:
        s = self._read_secrets()
        return {"hasClient": bool(s.get("client_id") and s.get("client_secret")),
                "linked": bool(s.get("refresh_token"))}

    def sync_drive_with_client(self, app_id, game_name, client, root_folder_id) -> dict:
        """Mirror one game's kept versions using an already-built DriveClient (or fake).
        The Decky layer builds the real client from the stored refresh token."""
        kept = kept_versions_for(self.data_root, app_id)
        return sync_game(client, root_folder_id, game_name, kept,
                         lambda vid: load_version_files(self.data_root, app_id, vid), app_id=app_id)

    def _find_game_folder(self, client, root_folder_id, game_name):
        return next((c["id"] for c in client.list_children(root_folder_id)
                     if c["name"] == game_name), None)

    def list_remote_versions_with_client(self, game_name, client, root_folder_id) -> list:
        folder = self._find_game_folder(client, root_folder_id, game_name)
        if folder is None:
            return []
        index, _ = read_index(client, folder)
        return list_remote_versions(index) if index else []

    def restore_from_drive_with_client(self, app_id, game_name, version_id, client, root_folder_id):
        folder = self._find_game_folder(client, root_folder_id, game_name)
        if folder is None:
            return None
        index, _ = read_index(client, folder)
        if not index or version_id not in index.get("versions", {}):
            return None
        meta = index["versions"][version_id]
        files = download_version(client, index, version_id)
        return import_version(self.data_root, app_id, version_id,
                              meta.get("label", version_id), meta.get("pinned", False), files)
