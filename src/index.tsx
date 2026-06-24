import { callable, definePlugin, toaster } from "@decky/api";
import {
  ButtonItem,
  Field,
  PanelSection,
  PanelSectionRow,
  Router,
  SliderField,
  TextField,
  ToggleField,
  staticClasses,
} from "@decky/ui";
import { Fragment, useEffect, useState } from "react";
import { FaClockRotateLeft } from "react-icons/fa6";

interface GameInfo { appId: number; name: string; }
interface VersionEntry {
  versionId: string; createdAt: number; name: string | null; pinned: boolean; reason: string; kind?: string;
}
interface Listing { head: { versionId: string | null }; versions: VersionEntry[]; }
interface Settings { keepCount: number; autoBackupOnExit: boolean; driveMirror: boolean; }
interface CurrentState { matchedVersionId: string | null; matchedLabel: string | null; createdAt: number | null; isHead: boolean; modified: boolean; resolvable: boolean; }
interface Diag { steamRoot: string; steamRootExists: boolean; deckyUserHome: string | null; accounts: number[]; }

const setAccountId = callable<[number], null>("set_account_id");
const getSupportedGames = callable<[], GameInfo[]>("get_supported_games");
const doBackup = callable<[GameInfo], VersionEntry | null>("do_backup");
const getVersions = callable<[number], Listing>("get_versions");
const getCurrentState = callable<[number], CurrentState>("get_current_state");
const revert = callable<[GameInfo, string], { versionId: string } | null>("revert");
const forceBackup = callable<[GameInfo], { status: string; entry?: VersionEntry | null }>("force_backup");
const forceRestore = callable<[GameInfo, string], { status: string }>("force_restore");
const setPinned = callable<[number, string, boolean], boolean>("set_pinned");
const setName = callable<[number, string, string], boolean>("set_name");
const removeVersion = callable<[number, string], boolean>("remove_version");
const getSettings = callable<[number], Settings>("get_settings");
const setKeepCount = callable<[number, number], Settings>("set_keep_count");
const setAutoBackup = callable<[number, boolean], Settings>("set_auto_backup");
const backupOnExit = callable<[GameInfo], null>("backup_on_exit");
const getDiag = callable<[], Diag>("get_diag");

function isRunning(appId: number): boolean {
  try {
    // @ts-ignore - Steam internal
    return (Router.RunningApps ?? []).some((a: any) => Number(a.appid) === appId);
  } catch {
    return false;
  }
}

function relTime(ms: number): string {
  const s = Math.max(0, (Date.now() - ms) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function labelOf(v: VersionEntry): string {
  return v.name ?? new Date(v.createdAt).toLocaleString();
}

const toast = (title: string, body?: string) => toaster.toast({ title, body: body ?? "" });

function GameList({ onPick }: { onPick: (g: GameInfo) => void }) {
  const [games, setGames] = useState<GameInfo[] | null>(null);
  const [diag, setDiag] = useState<Diag | null>(null);

  useEffect(() => {
    getSupportedGames().then(setGames).catch((e) => { console.error(e); setGames([]); });
    getDiag().then(setDiag).catch(() => { });
  }, []);

  return (
    <PanelSection title="Games">
      {games === null && <PanelSectionRow>Loading…</PanelSectionRow>}
      {games?.length === 0 && (
        <PanelSectionRow>
          No Steam-Cloud games found.
          {diag && ` (steamRoot ${diag.steamRootExists ? "ok" : "MISSING"}, accounts ${diag.accounts.length})`}
        </PanelSectionRow>
      )}
      {games?.map((g) => (
        <PanelSectionRow key={g.appId}>
          <ButtonItem layout="below" onClick={() => onPick(g)}>{g.name}</ButtonItem>
        </PanelSectionRow>
      ))}
    </PanelSection>
  );
}

function GamePanel({ game, onBack }: { game: GameInfo; onBack: () => void }) {
  const [listing, setListing] = useState<Listing | null>(null);
  const [state, setState] = useState<CurrentState | null>(null);
  const [keepCount, setKeep] = useState<number>(20);
  const [autoBackup, setAuto] = useState<boolean>(false);
  const [showOpts, setShowOpts] = useState<boolean>(false);
  const [busy, setBusy] = useState<boolean>(false);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameText, setRenameText] = useState<string>("");
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [confirmRestoreId, setConfirmRestoreId] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const refresh = () => {
    getVersions(game.appId).then(setListing).catch(console.error);
    getSettings(game.appId).then((s) => { setKeep(s.keepCount); setAuto(s.autoBackupOnExit); }).catch(console.error);
    getCurrentState(game.appId).then(setState).catch(() => setState(null));
  };
  useEffect(refresh, [game.appId]);

  const running = isRunning(game.appId);
  const head = listing?.head.versionId ?? null;
  const headEntry = listing?.versions.find((v) => v.versionId === head) ?? null;
  const versions = listing?.versions ?? [];

  // All actions stay inside the QAM (no full-screen modals, which would close it).
  const backup = async () => {
    setBusy(true);
    try {
      if (running) {
        const r = await forceBackup(game);
        if (r.status === "ok") toast("Backed up", "Snapshot taken while playing.");
        else if (r.status === "nochange") toast("No change since last backup");
        else if (r.status === "writing") toast("Save is being written", "Try again in a moment.");
        else toast("Couldn't read the save");
      } else {
        const e = await doBackup(game);
        toast(e ? "Backed up" : "No change since last backup");
      }
    } catch (err) {
      console.error("SaveManager: backup failed", err);
      toast("Backup failed", "See the plugin log for details.");
    } finally { setBusy(false); refresh(); }
  };
  const doRestore = async (v: VersionEntry) => {
    if (isRunning(game.appId)) { setConfirmRestoreId(v.versionId); return; }   // -> inline confirm
    try {
      await revert(game, v.versionId);
      toast(`Restored “${labelOf(v)}”`, "Your previous save was snapshotted — undo anytime.");
    } catch (err) {
      console.error("SaveManager: restore failed", err);
      toast("Restore failed", "Your save was not changed.");
    } finally { refresh(); }
  };
  const doForceRestore = async (v: VersionEntry) => {
    setConfirmRestoreId(null);
    try {
      const r = await forceRestore(game, v.versionId);
      if (r.status === "ok")
        toast(`Restored “${labelOf(v)}” to disk`,
          `Load your save in-game or restart ${game.name}; don't let it autosave first.`);
      else if (r.status === "writing") toast("Save is being written", "Try again in a moment.");
      else if (r.status === "unresolvable") toast("Couldn't read the save");
      else toast("Restore failed", "That version was not found.");
    } catch (err) {
      console.error("SaveManager: force restore failed", err);
      toast("Restore failed", "Your save was not changed.");
    } finally { refresh(); }
  };
  const doPin = async (v: VersionEntry) => {
    try {
      await setPinned(game.appId, v.versionId, !v.pinned);
      toast(v.pinned ? "Unpinned" : "Pinned");
    } catch (err) { console.error("SaveManager: pin failed", err); toast("Couldn’t update pin"); }
    finally { refresh(); }
  };
  const startRename = (v: VersionEntry) => { setConfirmDeleteId(null); setConfirmRestoreId(null); setRenamingId(v.versionId); setRenameText(v.name ?? ""); };
  const saveRename = async (v: VersionEntry) => {
    try {
      await setName(game.appId, v.versionId, renameText);
    } catch (err) { console.error("SaveManager: rename failed", err); toast("Rename failed"); }
    finally { setRenamingId(null); refresh(); } // always clear so the edit box never sticks open
  };
  const doDelete = async (v: VersionEntry) => {
    try {
      const ok = await removeVersion(game.appId, v.versionId);
      toast(ok ? "Deleted" : "Couldn’t delete — unpin it first.");
    } catch (err) { console.error("SaveManager: delete failed", err); toast("Delete failed"); }
    finally { setConfirmDeleteId(null); refresh(); }
  };

  const stateName = state?.matchedVersionId
    ? (state.matchedLabel ?? new Date(state.createdAt ?? 0).toLocaleString())
    : null;
  const currentDesc = !headEntry
    ? "No backups yet — tap “Back up now”."
    : !state?.resolvable
      ? labelOf(headEntry)
      : state.modified
        ? "✎ Modified — not backed up yet"
        : state.isHead
          ? `${stateName}  ·  up to date ✓`
          : `${stateName}  ·  an earlier backup (not your latest)`;

  return (
    <PanelSection title={game.name}>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={onBack}>← All games</ButtonItem>
      </PanelSectionRow>

      <Field label="Current save" description={currentDesc} bottomSeparator="none" />
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={refresh}>⟳  Re-check current save</ButtonItem>
      </PanelSectionRow>

      <PanelSectionRow>
        <ButtonItem layout="below" disabled={busy} onClick={backup}>
          {busy ? "Backing up…" : running ? "⬇  Back up now (while playing)" : "⬇  Back up now"}
        </ButtonItem>
      </PanelSectionRow>

      {versions.length > 0 && <Field label="Versions" bottomSeparator="none" />}
      {versions.map((v) => {
        const isHead = v.versionId === head;
        // "Current" = the version the LIVE save actually equals by hash. After revert→play the save
        // is modified (matches nothing) so no row is current; fall back to HEAD only if state is
        // unreadable. Delete still keys off real HEAD below — the backend refuses to delete HEAD.
        const isCurrent = !state || !state.resolvable ? isHead : v.versionId === state.matchedVersionId;

        if (renamingId === v.versionId) {
          return (
            <Fragment key={v.versionId}>
              <PanelSectionRow>
                <TextField label="New name" value={renameText} onChange={(e) => setRenameText(e.target.value)} />
              </PanelSectionRow>
              <PanelSectionRow><ButtonItem layout="below" onClick={() => saveRename(v)}>Save name</ButtonItem></PanelSectionRow>
              <PanelSectionRow><ButtonItem layout="below" onClick={() => setRenamingId(null)}>Cancel</ButtonItem></PanelSectionRow>
            </Fragment>
          );
        }
        if (confirmDeleteId === v.versionId) {
          return (
            <Fragment key={v.versionId}>
              <Field label={`Delete “${labelOf(v)}”?`} description="This can’t be undone." bottomSeparator="none" />
              <PanelSectionRow><ButtonItem layout="below" onClick={() => doDelete(v)}>Delete permanently</ButtonItem></PanelSectionRow>
              <PanelSectionRow><ButtonItem layout="below" onClick={() => setConfirmDeleteId(null)}>Cancel</ButtonItem></PanelSectionRow>
            </Fragment>
          );
        }
        if (confirmRestoreId === v.versionId) {
          return (
            <Fragment key={v.versionId}>
              <Field label={`Restore “${labelOf(v)}” while playing?`}
                description={`⚠ ${game.name} is running. This overwrites the save on disk, but the game still has the old save in memory and may overwrite this on its next autosave or when you quit.`}
                bottomSeparator="none" />
              <PanelSectionRow><ButtonItem layout="below" onClick={() => doForceRestore(v)}>Restore anyway</ButtonItem></PanelSectionRow>
              <PanelSectionRow><ButtonItem layout="below" onClick={() => setConfirmRestoreId(null)}>Cancel</ButtonItem></PanelSectionRow>
            </Fragment>
          );
        }
        const expanded = expandedId === v.versionId;
        const badges = (isCurrent ? "● " : "") + (v.pinned ? "★ " : "");
        return (
          <Fragment key={v.versionId}>
            <PanelSectionRow>
              <ButtonItem layout="below" bottomSeparator={expanded ? "none" : "standard"}
                onClick={() => setExpandedId(expanded ? null : v.versionId)}>
                {`${badges}${labelOf(v)}   ·   ${relTime(v.createdAt)}    ${expanded ? "▾" : "▸"}`}
              </ButtonItem>
            </PanelSectionRow>
            {expanded && (
              <>
                {!isCurrent && (
                  <PanelSectionRow><ButtonItem layout="below" onClick={() => doRestore(v)}>↩  Restore this save</ButtonItem></PanelSectionRow>
                )}
                <PanelSectionRow><ButtonItem layout="below" onClick={() => doPin(v)}>{v.pinned ? "★  Unpin" : "☆  Pin (protect)"}</ButtonItem></PanelSectionRow>
                <PanelSectionRow><ButtonItem layout="below" onClick={() => startRename(v)}>✎  Rename…</ButtonItem></PanelSectionRow>
                {!isHead && !v.pinned && (
                  <PanelSectionRow><ButtonItem layout="below" onClick={() => setConfirmDeleteId(v.versionId)}>🗑  Delete…</ButtonItem></PanelSectionRow>
                )}
              </>
            )}
          </Fragment>
        );
      })}

      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => setShowOpts((s) => !s)}>
          {`⚙  Options  ${showOpts ? "▾" : "▸"}`}
        </ButtonItem>
      </PanelSectionRow>
      {showOpts && (
        <>
          <PanelSectionRow>
            <ToggleField label="Auto-backup on exit"
              description="Snapshot automatically each time you quit this game"
              checked={autoBackup}
              onChange={(val: boolean) => { setAuto(val); setAutoBackup(game.appId, val).catch(console.error); }} />
          </PanelSectionRow>
          <PanelSectionRow>
            <SliderField label="Keep last N" value={keepCount} min={5} max={100} step={5}
              showValue notchTicksVisible
              description="Pinned versions don’t count and are never auto-deleted"
              onChange={(val: number) => { setKeep(val); setKeepCount(game.appId, val).catch(console.error); }} />
          </PanelSectionRow>
        </>
      )}
    </PanelSection>
  );
}

// Steam can re-mount the QAM panel on focus changes (e.g. right after an action),
// which would reset component state and drop you back to the game list. Persist the
// open game at module scope so a remount restores the game panel instead.
let lastSelected: GameInfo | null = null;

function Content() {
  const [selected, setSel] = useState<GameInfo | null>(lastSelected);
  const setSelected = (g: GameInfo | null) => { lastSelected = g; setSel(g); };
  return selected
    ? <GamePanel key={selected.appId} game={selected} onBack={() => setSelected(null)} />
    : <GameList onPick={setSelected} />;
}

export default definePlugin(() => {
  try {
    // @ts-ignore - Steam internal
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
    icon: <FaClockRotateLeft />,
    onDismount() { hook?.unregister?.(); },
  };
});
