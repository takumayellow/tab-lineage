"""Chromium の SNSS セッションファイル (Sessions/Session_*) から開いているタブを復元する.

レコードは uint16 サイズ + uint8 コマンド + 本体. 使うコマンドは次の 5 つ.
  0  SetTabWindow         window_id, tab_id
  2  SetTabIndexInWindow  tab_id, index (タブバーでの位置)
  6  UpdateTabNavigation  pickle: 長さ, tab_id, index, url (UTF-8), title (UTF-16)
  7  SelectedNavIndex     tab_id, index
  16 TabClosed            tab_id
Vivaldi はこれとは別に独自の JSON (ext_id, parent_ext_id など) を載せる.
"""
from __future__ import annotations

import json
import struct
from collections import defaultdict
from dataclasses import dataclass, field

CMD_WINDOW, CMD_INDEX, CMD_NAV, CMD_SELECTED, CMD_CLOSED = 0, 2, 6, 7, 16
_EXT_MARK = b'{"ext_id"'


@dataclass(frozen=True)
class Tab:
    tab: int
    url: str
    title: str
    back: tuple[str, ...] = ()            # 同じタブで直前に見ていた URL (新しい順に最大 5)
    window: int = 0
    index: int = -1                       # タブバーでの位置 (記録が無ければ -1)
    ext: dict = field(default_factory=dict, hash=False, compare=False)


def _str8(p: bytes, off: int) -> tuple[str, int]:
    n = struct.unpack_from('<i', p, off)[0]
    off += 4
    return p[off:off + n].decode('utf-8', 'replace'), off + ((n + 3) & ~3)


def _str16(p: bytes, off: int) -> tuple[str, int]:
    n = struct.unpack_from('<i', p, off)[0]
    off += 4
    return p[off:off + n * 2].decode('utf-16-le', 'replace'), off + ((n * 2 + 3) & ~3)


def _records(b: bytes):
    off = 8   # 先頭はマジックとバージョン
    while off + 3 <= len(b):
        size = struct.unpack_from('<H', b, off)[0]
        if size == 0:
            break
        yield b[off + 2], b[off + 3: off + 2 + size]
        off += 2 + size


def _ext_json(p: bytes) -> dict | None:
    at = p.find(_EXT_MARK)
    if at < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(p[at:].decode('utf-8', 'replace'))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def parse(data: bytes) -> list[Tab]:
    navs: dict[int, dict[int, tuple[str, str]]] = defaultdict(dict)
    selected: dict[int, int] = {}
    closed: set[int] = set()
    ext: dict[int, dict] = {}
    window: dict[int, int] = {}
    index: dict[int, int] = {}
    for cmd, p in _records(data):
        try:
            if cmd == CMD_WINDOW:
                w, tab = struct.unpack_from('<ii', p, 0)
                window[tab] = w
            elif cmd == CMD_INDEX:
                tab, idx = struct.unpack_from('<ii', p, 0)
                index[tab] = idx
            elif cmd == CMD_NAV:
                tab, idx = struct.unpack_from('<ii', p, 4)
                url, off = _str8(p, 12)
                title, _ = _str16(p, off)
                navs[tab][idx] = (url, title)
            elif cmd == CMD_SELECTED:
                tab, idx = struct.unpack_from('<ii', p, 0)
                selected[tab] = idx
            elif cmd == CMD_CLOSED:
                closed.add(struct.unpack_from('<i', p, 0)[0])
            elif _EXT_MARK in p and len(p) >= 8:
                obj = _ext_json(p)
                if obj is not None:
                    ext[struct.unpack_from('<i', p, 4)[0]] = obj
        except struct.error:
            continue
    tabs = []
    for tab, nv in navs.items():
        if tab in closed:
            continue
        idx = selected.get(tab, max(nv))
        url, title = nv.get(idx, nv[max(nv)])
        back = tuple(nv[i][0] for i in sorted(nv, reverse=True) if i < idx)[:5]
        tabs.append(Tab(tab=tab, url=url, title=title, back=back, window=window.get(tab, 0),
                        index=index.get(tab, -1), ext=ext.get(tab, {})))
    # ウィンドウは最初に現れた順, その中はタブバーの位置の順. 位置の記録が無いタブは現れた順で後ろへ
    first = {w: k for k, w in reversed(list(enumerate(t.window for t in tabs)))}
    order = {t.tab: k for k, t in enumerate(tabs)}
    return sorted(tabs, key=lambda t: (first[t.window], t.index < 0, t.index, order[t.tab]))


def load(path) -> list[Tab]:
    with open(path, 'rb') as f:
        return parse(f.read())
