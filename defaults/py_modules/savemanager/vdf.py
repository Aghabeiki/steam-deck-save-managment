# defaults/py_modules/savemanager/vdf.py
import re
from dataclasses import dataclass


@dataclass
class RcfEntry:
    path: str
    root: int
    size: int
    mtime: int  # seconds, from the "time" field


_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|\{|\}')


def _tokenize(text):
    for m in _TOKEN.finditer(text):
        tok = m.group(0)
        if tok == "{":
            yield ("open", None)
        elif tok == "}":
            yield ("close", None)
        else:
            yield ("str", m.group(1))


def _parse_block(tokens):
    """Parse key/value pairs until a 'close' (or EOF). Values are str or dict."""
    out = {}
    for kind, val in tokens:
        if kind == "close":
            return out
        key = val
        try:
            kind2, val2 = next(tokens)
        except StopIteration:
            break
        out[key] = _parse_block(tokens) if kind2 == "open" else val2
    return out


def parse_remotecache(text: str) -> list[RcfEntry]:
    """Parse a Valve remotecache.vdf into RcfEntry rows (one per synced file)."""
    root = _parse_block(_tokenize(text))
    entries: list[RcfEntry] = []
    for _appid, appblock in root.items():
        if not isinstance(appblock, dict):
            continue
        for key, val in appblock.items():
            if isinstance(val, dict) and ("size" in val or "root" in val):
                entries.append(
                    RcfEntry(
                        path=key,
                        root=int(val.get("root", 0)),
                        size=int(val.get("size", 0)),
                        mtime=int(val.get("time", 0)),
                    )
                )
    return entries
