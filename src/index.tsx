import { callable, definePlugin, addEventListener, removeEventListener, toaster } from "@decky/api";
import {
  ButtonItem,
  ConfirmModal,
  PanelSection,
  PanelSectionRow,
  Router,
  showModal,
  SliderField,
  TextField,
  ToggleField,
  staticClasses,
} from "@decky/ui";
import { useEffect, useRef, useState } from "react";
import { FaDownload } from "react-icons/fa";

interface GameInfo { appId: number; name: string; }
interface VersionEntry {
  versionId: string; createdAt: number; name: string | null; pinned: boolean; reason: string;
}
interface Listing { head: { versionId: string | null }; versions: VersionEntry[]; }
interface Settings { keepCount: number; autoBackupOnExit: boolean; driveMirror: boolean; }
interface DriveStatus { hasClient: boolean; linked: boolean; }

const setAccountId = callable<[number], null>("set_account_id");
const findSupported = callable<[GameInfo[]], GameInfo[]>("find_supported");
const doBackup = callable<[GameInfo], VersionEntry | null>("do_backup");
const getVersions = callable<[number], Listing>("get_versions");
const revert = callable<[GameInfo, string], { versionId: string } | null>("revert");
const setPinned = callable<[number, string, boolean], boolean>("set_pinned");
const setName = callable<[number, string, string], boolean>("set_name");
const removeVersion = callable<[number, string], boolean>("remove_version");
const getSettings = callable<[number], Settings>("get_settings");
const setKeepCount = callable<[number, number], Settings>("set_keep_count");
const setAutoBackup = callable<[number, boolean], Settings>("set_auto_backup");
const backupOnExit = callable<[GameInfo], null>("backup_on_exit");
const setDriveMirrorSetting = callable<[number, boolean], Settings>("set_drive_mirror");
const setDriveClient = callable<[string, string], null>("set_drive_client");
const getDriveStatus = callable<[], DriveStatus>("get_drive_status");
const linkDriveStart = callable<[], { user_code: string; verification_url: string }>("link_drive_start");
const linkDrivePoll = callable<[], { status: string }>("link_drive_poll");
const syncDrive = callable<[GameInfo], null>("sync_drive");

interface RemoteVersion { versionId: string; label: string; pinned: boolean; }
const listRemoteVersions = callable<[GameInfo], RemoteVersion[]>("list_remote_versions");
const restoreFromDrive = callable<[GameInfo, string], null>("restore_from_drive");

function isRunning(appId: number): boolean {
  try {
    // @ts-ignore - Steam internal
    return (Router.RunningApps ?? []).some((a: any) => Number(a.appid) === appId);
  } catch {
    return false;
  }
}

function installedGames(): GameInfo[] {
  try {
    // @ts-ignore - Steam internal
    const folders = SteamClient.InstallFolder.GetInstallFolders();
    const out: GameInfo[] = [];
    // @ts-ignore
    for (const f of folders) for (const a of f.vecApps) {
      try {
        // @ts-ignore
        const ov = appStore.GetAppOverviewByGameID(a.nAppID);
        out.push({ appId: a.nAppID, name: ov?.display_name ?? String(a.nAppID) });
      } catch (e) {
        console.error("SaveManager: skipping app", a?.nAppID, e);
      }
    }
    return out;
  } catch (e) {
    console.error("SaveManager: cannot list games", e);
    return [];
  }
}

function RenameModal({ initial, onSave, closeModal }:
  { initial: string; onSave: (v: string) => void; closeModal?: () => void }) {
  const [value, setValue] = useState(initial);
  return (
    <ConfirmModal
      strTitle="Name this version"
      onOK={() => { onSave(value); closeModal?.(); }}
      onCancel={() => closeModal?.()}
    >
      <TextField value={value} onChange={(e) => setValue(e.target.value)} />
    </ConfirmModal>
  );
}

function DriveSection() {
  const [status, setStatus] = useState<DriveStatus | null>(null);
  const [cid, setCid] = useState("");
  const [secret, setSecret] = useState("");
  const [code, setCode] = useState<{ user_code: string; verification_url: string } | null>(null);
  const timerRef = useRef<any>(null);

  useEffect(() => { getDriveStatus().then(setStatus).catch(console.error); }, []);

  // FIX I3: clear poll interval on unmount to avoid setState on unmounted component
  useEffect(() => () => { if (timerRef.current) clearInterval(timerRef.current); }, []);

  // FIX I1: listen for backend drive events and surface them as toasts
  useEffect(() => {
    const done = addEventListener("drive_sync_done", (_appId: number, n: number) =>
      toaster.toast({ title: "Drive sync complete", body: `${n} version(s) mirrored` }));
    const err = addEventListener("drive_sync_error", (_appId: number, msg: string) =>
      toaster.toast({ title: "Drive sync failed", body: String(msg) }));
    const relink = addEventListener("drive_needs_relink", () => {
      toaster.toast({ title: "Google Drive", body: "Please re-link your account" });
      getDriveStatus().then(setStatus).catch(console.error);
    });
    return () => {
      removeEventListener("drive_sync_done", done);
      removeEventListener("drive_sync_error", err);
      removeEventListener("drive_needs_relink", relink);
    };
  }, []);

  const save = async () => { await setDriveClient(cid, secret); getDriveStatus().then(setStatus); };
  // FIX I3: store interval in ref so it can be cleared on unmount
  const link = async () => {
    const c = await linkDriveStart(); setCode(c);
    timerRef.current = setInterval(async () => {
      const r = await linkDrivePoll().catch(() => ({ status: "error" }));
      if (r.status === "ok") { clearInterval(timerRef.current); setCode(null); getDriveStatus().then(setStatus); }
      else if (r.status === "denied" || r.status === "expired" || r.status === "error") { clearInterval(timerRef.current); setCode(null); }
    }, 5000);
  };

  return (
    <PanelSection title="Google Drive">
      {!status?.hasClient && (
        <>
          <PanelSectionRow><TextField label="Client ID" value={cid} onChange={(e) => setCid(e.target.value)} /></PanelSectionRow>
          <PanelSectionRow><TextField label="Client secret" value={secret} bIsPassword onChange={(e) => setSecret(e.target.value)} /></PanelSectionRow>
          <PanelSectionRow><ButtonItem layout="below" onClick={save}>Save Google client</ButtonItem></PanelSectionRow>
        </>
      )}
      {status?.hasClient && !status.linked && !code && (
        <PanelSectionRow><ButtonItem layout="below" onClick={link}>Link Google account</ButtonItem></PanelSectionRow>
      )}
      {code && (
        <PanelSectionRow>
          Go to {code.verification_url} and enter code: <b>{code.user_code}</b>
        </PanelSectionRow>
      )}
      {status?.linked && <PanelSectionRow>✓ Google Drive linked</PanelSectionRow>}
    </PanelSection>
  );
}

function Content() {
  const [supported, setSupported] = useState<GameInfo[]>([]);
  const [selected, setSelected] = useState<GameInfo | null>(null);
  const [listing, setListing] = useState<Listing | null>(null);
  const [keepCount, setKeep] = useState<number>(20);
  const [autoBackup, setAuto] = useState<boolean>(false);
  const [driveMirror, setDriveMirror] = useState<boolean>(false);
  const [syncing, setSyncing] = useState<boolean>(false);
  const [remote, setRemote] = useState<RemoteVersion[] | null>(null);

  useEffect(() => { findSupported(installedGames()).then(setSupported).catch(console.error); }, []);

  useEffect(() => {
    const done = addEventListener("drive_restore_done", () => {
      toaster.toast({ title: "Restored from Drive", body: "Version downloaded into your list" });
      if (selected) { refresh(selected); listRemoteVersions(selected).then(setRemote).catch(console.error); }
    });
    const err = addEventListener("drive_restore_error", (_a: number, msg: string) =>
      toaster.toast({ title: "Restore failed", body: String(msg) }));
    const relink = addEventListener("drive_needs_relink", () =>
      toaster.toast({ title: "Google Drive", body: "Please re-link your account in the Drive section" }));
    return () => {
      removeEventListener("drive_restore_done", done);
      removeEventListener("drive_restore_error", err);
      removeEventListener("drive_needs_relink", relink);
    };
  }, [selected]);

  const refresh = (g: GameInfo) => {
    getVersions(g.appId).then(setListing).catch(console.error);
    getSettings(g.appId).then((s) => { setKeep(s.keepCount); setAuto(s.autoBackupOnExit); setDriveMirror(s.driveMirror); }).catch(console.error);
  };
  const open = (g: GameInfo) => { setSelected(g); refresh(g); setRemote(null); };

  if (!selected) {
    return (
      <>
        <PanelSection title="Supported games">
          {supported.map((g) => (
            <PanelSectionRow key={g.appId}>
              <ButtonItem layout="below" onClick={() => open(g)}>{g.name}</ButtonItem>
            </PanelSectionRow>
          ))}
        </PanelSection>
        <DriveSection />
      </>
    );
  }

  const running = isRunning(selected.appId);
  const head = listing?.head.versionId ?? null;

  return (
    <PanelSection title={selected.name}>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => { setSelected(null); setListing(null); setRemote(null); }}>
          ← Back
        </ButtonItem>
      </PanelSectionRow>

      <PanelSectionRow>
        <ButtonItem layout="below" disabled={running}
          onClick={async () => { await doBackup(selected); refresh(selected); }}>
          {running ? "Stop the game to back up" : "Back up now"}
        </ButtonItem>
      </PanelSectionRow>

      <PanelSectionRow>
        <ToggleField label="Auto-backup on exit" checked={autoBackup}
          onChange={(v: boolean) => { setAuto(v); setAutoBackup(selected.appId, v).catch(console.error); }} />
      </PanelSectionRow>

      <PanelSectionRow>
        <ToggleField label="Mirror to Drive" checked={driveMirror}
          onChange={(v: boolean) => { setDriveMirror(v); setDriveMirrorSetting(selected.appId, v).catch(console.error); }} />
      </PanelSectionRow>
      <PanelSectionRow>
        {/* FIX M5: disable while in-flight to prevent double-tap spam */}
        <ButtonItem layout="below" disabled={syncing}
          onClick={() => { setSyncing(true); syncDrive(selected).catch(console.error); setTimeout(() => setSyncing(false), 4000); }}>
          {syncing ? "Syncing…" : "Sync to Drive now"}
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => listRemoteVersions(selected).then(setRemote).catch(console.error)}>
          Restore from Drive…
        </ButtonItem>
      </PanelSectionRow>
      {remote && remote
        .filter((rv) => !(listing?.versions ?? []).some((v) => v.versionId === rv.versionId))
        .map((rv) => (
          <PanelSectionRow key={rv.versionId}>
            <ButtonItem layout="below"
              onClick={() => restoreFromDrive(selected, rv.versionId).catch(console.error)}>
              ⬇ {rv.pinned ? "★ " : ""}{rv.label}
            </ButtonItem>
          </PanelSectionRow>
        ))}
      {remote && remote.filter((rv) => !(listing?.versions ?? []).some((v) => v.versionId === rv.versionId)).length === 0 && (
        <PanelSectionRow>No Drive-only versions to restore.</PanelSectionRow>
      )}

      <PanelSectionRow>
        <SliderField label="Keep last N" value={keepCount} min={5} max={100} step={5}
          showValue notchTicksVisible
          onChange={(v: number) => { setKeep(v); setKeepCount(selected.appId, v).catch(console.error); }} />
      </PanelSectionRow>

      {listing?.versions.map((v) => {
        const label = v.name ?? new Date(v.createdAt).toLocaleString();
        const isHead = v.versionId === head;
        return (
          <PanelSectionRow key={v.versionId}>
            <ButtonItem layout="below" disabled={running}
              label={`${v.pinned ? "★ " : ""}${label}${isHead ? "  ●" : ""}`}
              onClick={() => showModal(
                <ConfirmModal strTitle={`Restore "${label}"?`} bDestructiveWarning
                  strDescription="Your current save is snapshotted first, so you can revert this."
                  strOKButtonText={running ? "Game is running" : "Restore"}
                  onOK={async () => {
                    if (isRunning(selected.appId)) return;
                    await revert(selected, v.versionId); refresh(selected);
                  }} />
              )}>
              Restore
            </ButtonItem>
            <ButtonItem layout="below"
              onClick={async () => { await setPinned(selected.appId, v.versionId, !v.pinned); refresh(selected); }}>
              {v.pinned ? "Unpin" : "Pin"}
            </ButtonItem>
            <ButtonItem layout="below"
              onClick={() => showModal(
                <RenameModal initial={v.name ?? ""}
                  onSave={async (name) => { await setName(selected.appId, v.versionId, name); refresh(selected); }} />
              )}>
              Rename
            </ButtonItem>
            {!isHead && (
              <ButtonItem layout="below"
                onClick={() => showModal(
                  <ConfirmModal strTitle={`Delete "${label}"?`} bDestructiveWarning
                    onOK={async () => { await removeVersion(selected.appId, v.versionId); refresh(selected); }} />
                )}>
                Delete
              </ButtonItem>
            )}
          </PanelSectionRow>
        );
      })}
    </PanelSection>
  );
}

export default definePlugin(() => {
  try {
    // @ts-ignore
    const steam64 = BigInt(App.m_CurrentUser.strSteamID);
    setAccountId(Number(steam64 & 0xffffffffn)).catch(console.error);
  } catch (e) {
    console.error("SaveManager: cannot read account id", e);
  }
  // @ts-ignore - Steam internal
  const hook = SteamClient.GameSessions.RegisterForAppLifetimeNotifications((n: any) => {
    if (n.bRunning) return;                         // only act on EXIT
    let name = String(n.unAppID);
    try {
      // @ts-ignore - Steam internal
      name = appStore.GetAppOverviewByGameID(n.unAppID)?.display_name ?? name;
    } catch (e) { /* keep the appId as the name */ }
    // Backend no-ops if this game's auto-backup toggle is off.
    backupOnExit({ appId: n.unAppID, name }).catch(console.error);
  });
  return {
    name: "Save Manager",
    title: <div className={staticClasses.Title}>Save Manager</div>,
    content: <Content />,
    icon: <FaDownload />,
    onDismount() { hook?.unregister?.(); },
  };
});
