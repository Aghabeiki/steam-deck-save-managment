# tests/test_vdf.py
from savemanager.vdf import parse_remotecache, RcfEntry

SAMPLE = '''"281990"
{
\t"ChangeNumber"\t\t"-6703994677807818784"
\t"ostype"\t\t"-184"
\t"my games/XCOM2/XComGame/SaveData/profile.bin"
\t{
\t\t"root"\t\t"2"
\t\t"size"\t\t"15741"
\t\t"localtime"\t\t"1671427173"
\t\t"time"\t\t"1671427172"
\t\t"sha"\t\t"df59d8d7b2f0c7ddd25e966493d61c1b107f9b7a"
\t}
\t"my games/XCOM2/XComGame/SaveData/save1.sav"
\t{
\t\t"root"\t\t"2"
\t\t"size"\t\t"1048576"
\t\t"time"\t\t"1671427180"
\t}
}
'''

def test_parses_two_file_entries_and_ignores_scalars():
    entries = parse_remotecache(SAMPLE)
    assert len(entries) == 2
    by_path = {e.path: e for e in entries}
    p = by_path["my games/XCOM2/XComGame/SaveData/profile.bin"]
    assert isinstance(p, RcfEntry)
    assert p.root == 2 and p.size == 15741 and p.mtime == 1671427172
    s = by_path["my games/XCOM2/XComGame/SaveData/save1.sav"]
    assert s.size == 1048576 and s.mtime == 1671427180

def test_empty_text_returns_empty_list():
    assert parse_remotecache("") == []
