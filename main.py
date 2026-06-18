# main.py
import asyncio
import os
import time
from typing import Optional

import certifi
import requests

import decky  # provided by Decky at runtime

from savemanager.api import Engine
from savemanager import drive as drive_mod
from savemanager.drive import DriveClient
from savemanager.drive_transport import make_requests_http

_DRIVE_ROOT_FOLDER = "SteamDeckSaveManager"

# Max seconds to wait for Steam's post-exit remotecache.vdf to settle before snapshotting.
_EXIT_SETTLE_MAX_SECONDS = 8

_engine = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        data_root = os.environ["DECKY_PLUGIN_RUNTIME_DIR"]
        steam_root = os.path.join(os.path.expanduser("~"), ".local", "share", "Steam")
        _engine = Engine(data_root, steam_root)
    return _engine


def _now_ms() -> int:
    return int(round(time.time() * 1000))


def _rand_hex() -> str:
    return os.urandom(3).hex()


def _drive_http():
    session = requests.Session()
    session.verify = certifi.where()
    return make_requests_http(session)


class Plugin:
    async def set_account_id(self, account_id: int):
        get_engine().set_account_id(account_id)
        return None

    async def find_supported(self, game_infos: list) -> list:
        return get_engine().find_supported(game_infos)

    async def do_backup(self, game_info: dict) -> Optional[dict]:
        entry = get_engine().do_backup(game_info, _now_ms(), _rand_hex())
        if entry:
            self._maybe_mirror(game_info)
        return entry

    def _maybe_mirror(self, game_info: dict):
        eng = get_engine()
        if eng.get_settings(game_info["appId"]).get("driveMirror") and eng.get_drive_status()["linked"]:
            self.loop.create_task(self._do_sync_drive(game_info))

    def _drive_client_and_root(self):
        secrets = get_engine()._read_secrets()
        http = _drive_http()
        access = drive_mod.refresh_access_token(http, secrets["client_id"],
                                                secrets["client_secret"], secrets["refresh_token"])
        client = DriveClient(http, access)
        return client, client.find_or_create_folder(_DRIVE_ROOT_FOLDER, "root")

    async def get_versions(self, app_id: int) -> dict:
        return get_engine().get_versions(app_id)

    async def revert(self, game_info: dict, target_id: str):
        return get_engine().revert(game_info, target_id, _now_ms(), _rand_hex())

    async def set_pinned(self, app_id: int, version_id: str, pinned: bool):
        return get_engine().set_pinned(app_id, version_id, pinned)

    async def set_name(self, app_id: int, version_id: str, name: str):
        return get_engine().set_name(app_id, version_id, name)

    async def remove_version(self, app_id: int, version_id: str):
        return get_engine().remove_version(app_id, version_id)

    async def get_settings(self, app_id: int) -> dict:
        return get_engine().get_settings(app_id)

    async def set_keep_count(self, app_id: int, keep_count: int) -> dict:
        return get_engine().set_keep_count(app_id, keep_count)

    async def set_auto_backup(self, app_id: int, enabled: bool) -> dict:
        return get_engine().set_auto_backup(app_id, enabled)

    async def set_drive_mirror(self, app_id: int, enabled: bool):
        return get_engine().set_drive_mirror(app_id, enabled)

    async def backup_on_exit(self, game_info: dict):
        # Fast-return; do the debounce + backup off the RPC path so the socket never blocks.
        # INVARIANT: every Engine mutation (do_backup / revert / cull / curation) is SYNCHRONOUS
        # and commits refs.json atomically, so this background task cannot interleave mid-write
        # with a concurrent manual op on the single-threaded loop. Keep those engine calls
        # synchronous (no await inside a mutation); a cross-process guard is the locking.py follow-up.
        loop = getattr(self, "loop", None) or asyncio.get_running_loop()
        loop.create_task(self._debounced_backup(game_info))
        return None

    async def _debounced_backup(self, game_info: dict):
        try:
            engine = get_engine()
            app_id = game_info["appId"]
            if not engine.get_settings(app_id).get("autoBackupOnExit"):
                return                                  # toggle off -> nothing to do (skip polling)
            # Wait until Steam's post-exit remotecache.vdf mtime stops advancing (bounded).
            prev = engine.remotecache_mtime(app_id)
            for _ in range(_EXIT_SETTLE_MAX_SECONDS):
                await asyncio.sleep(1.0)
                cur = engine.remotecache_mtime(app_id)
                if cur == prev:
                    break
                prev = cur
            result = engine.do_backup_on_exit(game_info, _now_ms(), _rand_hex())
            decky.logger.info(f"SaveManager auto-backup on exit: {game_info.get('appId')} -> {result}")
            if result:
                self._maybe_mirror(game_info)
        except Exception as e:
            decky.logger.error(f"SaveManager auto-backup failed: {e}")

    async def set_drive_client(self, client_id: str, client_secret: str):
        get_engine().set_drive_client(client_id, client_secret)
        return None

    async def get_drive_status(self) -> dict:
        return get_engine().get_drive_status()

    async def link_drive_start(self) -> dict:
        secrets = get_engine()._read_secrets()
        http = _drive_http()
        dc = drive_mod.request_device_code(http, secrets["client_id"])
        self._drive_device = {"device_code": dc["device_code"], "interval": dc.get("interval", 5)}
        return {"user_code": dc["user_code"], "verification_url": dc.get("verification_url"),
                "expires_in": dc.get("expires_in")}

    async def link_drive_poll(self) -> dict:
        if not getattr(self, "_drive_device", None):
            return {"status": "error"}
        eng = get_engine()
        secrets = eng._read_secrets()
        http = _drive_http()
        out = drive_mod.poll_token(http, secrets["client_id"], secrets["client_secret"],
                                   self._drive_device["device_code"])
        if out["status"] == "ok":
            eng.set_drive_refresh_token(out["tokens"]["refresh_token"])
        return {"status": out["status"]}

    async def sync_drive(self, game_info: dict):
        self.loop.create_task(self._do_sync_drive(game_info))
        return None

    async def _do_sync_drive(self, game_info: dict):
        async with self._drive_lock:
            try:
                eng = get_engine()
                secrets = eng._read_secrets()
                http = _drive_http()
                access = drive_mod.refresh_access_token(http, secrets["client_id"],
                                                        secrets["client_secret"], secrets["refresh_token"])
                client = DriveClient(http, access)
                root = client.find_or_create_folder(_DRIVE_ROOT_FOLDER, "root")
                idx = eng.sync_drive_with_client(game_info["appId"], game_info["name"], client, root)
                await decky.emit("drive_sync_done", game_info["appId"], len(idx["versions"]))
            except drive_mod.DriveAuthError:
                get_engine().set_drive_refresh_token(None)      # revoked -> force re-link
                await decky.emit("drive_needs_relink", game_info.get("appId"))
            except Exception as e:
                decky.logger.error(f"SaveManager drive sync failed: {e}")
                await decky.emit("drive_sync_error", game_info.get("appId"), str(e))

    async def list_remote_versions(self, game_info: dict) -> list:
        try:
            client, root = self._drive_client_and_root()
            return get_engine().list_remote_versions_with_client(game_info["name"], client, root)
        except drive_mod.DriveAuthError:
            get_engine().set_drive_refresh_token(None)
            await decky.emit("drive_needs_relink", game_info.get("appId"))
            return []
        except Exception as e:
            decky.logger.error(f"SaveManager list remote versions failed: {e}")
            return []

    async def restore_from_drive(self, game_info: dict, version_id: str):
        self.loop.create_task(self._do_restore_from_drive(game_info, version_id))
        return None

    async def _do_restore_from_drive(self, game_info: dict, version_id: str):
        async with self._drive_lock:
            try:
                client, root = self._drive_client_and_root()
                result = get_engine().restore_from_drive_with_client(game_info["appId"], game_info["name"],
                                                                     version_id, client, root)
                if result is None:
                    await decky.emit("drive_restore_error", game_info.get("appId"), "version not found on Drive")
                else:
                    await decky.emit("drive_restore_done", game_info["appId"], version_id)
            except drive_mod.DriveAuthError:
                get_engine().set_drive_refresh_token(None)
                await decky.emit("drive_needs_relink", game_info.get("appId"))
            except Exception as e:
                decky.logger.error(f"SaveManager drive restore failed: {e}")
                await decky.emit("drive_restore_error", game_info.get("appId"), str(e))

    async def _main(self):
        self.loop = asyncio.get_event_loop()
        self._drive_lock = asyncio.Lock()
        decky.logger.info("SaveManager loaded")

    async def _unload(self):
        decky.logger.info("SaveManager unloaded")
