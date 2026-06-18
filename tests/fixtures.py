# tests/fixtures.py
import os

REMOTECACHE = '''"281990"
{
\t"ChangeNumber"\t\t"1"
\t"save1.sav"
\t{
\t\t"root"\t\t"0"
\t\t"size"\t\t"5"
\t\t"time"\t\t"1671427180"
\t}
\t"profile.bin"
\t{
\t\t"root"\t\t"0"
\t\t"size"\t\t"4"
\t\t"time"\t\t"1671427181"
\t}
}
'''


def make_steam_tree(tmp_path, account_id=123, app_id=281990, with_saves=True):
    """Build a minimal fake Steam dir. Returns (steam_root, account_id, app_id)."""
    steam_root = os.path.join(str(tmp_path), "Steam")
    ud = os.path.join(steam_root, "userdata", str(account_id), str(app_id))
    os.makedirs(ud, exist_ok=True)
    with open(os.path.join(ud, "remotecache.vdf"), "w") as f:
        f.write(REMOTECACHE)
    if with_saves:
        remote = os.path.join(ud, "remote")
        os.makedirs(remote, exist_ok=True)
        with open(os.path.join(remote, "save1.sav"), "w") as f:
            f.write("AAAAA")
        with open(os.path.join(remote, "profile.bin"), "w") as f:
            f.write("BBBB")
    # appmanifest so parse_installdir works
    sa = os.path.join(steam_root, "steamapps")
    os.makedirs(sa, exist_ok=True)
    with open(os.path.join(sa, f"appmanifest_{app_id}.acf"), "w") as f:
        f.write('"AppState"\n{\n\t"installdir"\t\t"XCOM 2"\n}\n')
    return steam_root, account_id, app_id
