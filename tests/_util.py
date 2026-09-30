"""テスト用の合成データ. 実際の閲覧履歴は使わない."""
from __future__ import annotations

import sqlite3
import struct

from tab_lineage.histdb import FORWARD_BACK, US, WEBKIT_TO_UNIX_S, Visit

T0 = (1_790_000_000 + WEBKIT_TO_UNIX_S) * US   # 2026-09 ごろ


def at(seconds: float) -> int:
    return T0 + int(seconds * US)


def visit(id, sec, url, title='', core=0, back=False, opener=0, frm=0, dur=0, term=None) -> Visit:
    return Visit(id=id, t=at(sec), url=url, title=title, core=core, back=back,
                 opener=opener, frm=frm, dur=dur, term=term)


def make_history(path, visits: list[Visit], opener_column: bool = True) -> None:
    """Chromium の History と同じ列を持つ最小の DB を作る."""
    c = sqlite3.connect(path)
    opener_col = ', opener_visit INTEGER' if opener_column else ''
    c.executescript(f'''
        CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT);
        CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER, from_visit INTEGER,
                             transition INTEGER, visit_duration INTEGER{opener_col});
        CREATE TABLE keyword_search_terms (keyword_id INTEGER, url_id INTEGER, term TEXT);
    ''')
    url_ids: dict[str, int] = {}
    for v in visits:
        if v.url not in url_ids:
            url_ids[v.url] = len(url_ids) + 1
            c.execute('INSERT INTO urls VALUES (?, ?, ?)', (url_ids[v.url], v.url, v.title))
            if v.term:
                c.execute('INSERT INTO keyword_search_terms VALUES (1, ?, ?)', (url_ids[v.url], v.term))
        tr = v.core | (FORWARD_BACK if v.back else 0)
        cols = [v.id, url_ids[v.url], v.t, v.frm, tr, v.dur] + ([v.opener] if opener_column else [])
        c.execute(f'INSERT INTO visits VALUES ({",".join("?" * len(cols))})', cols)
    c.commit()
    c.close()


# --- SNSS -------------------------------------------------------------------

def _pad(b: bytes) -> bytes:
    return b + b'\0' * (-len(b) % 4)


def record(cmd: int, payload: bytes) -> bytes:
    return struct.pack('<HB', len(payload) + 1, cmd) + payload


def nav(tab: int, index: int, url: str, title: str) -> bytes:
    u = url.encode('utf-8')
    t = title.encode('utf-16-le')
    body = (struct.pack('<ii', tab, index) + struct.pack('<i', len(u)) + _pad(u)
            + struct.pack('<i', len(title)) + _pad(t))
    return record(6, struct.pack('<i', len(body)) + body)


def snss(*records: bytes) -> bytes:
    return b'SNSS' + struct.pack('<i', 3) + b''.join(records)
