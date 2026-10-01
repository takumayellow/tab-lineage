"""Chromium 系ブラウザ (Vivaldi / Chrome / Edge / Brave) の History DB を読む."""
from __future__ import annotations

import contextlib
import pathlib
import sqlite3
from dataclasses import dataclass

WEBKIT_TO_UNIX_S = 11_644_473_600   # 1601-01-01 から 1970-01-01 までの秒数
US = 1_000_000

CORE_LINK, CORE_TYPED, CORE_BOOKMARK, CORE_SUBFRAME = 0, 1, 2, 3
CORE_GENERATED, CORE_FORM, CORE_RELOAD = 5, 7, 8
SKIP_CORES = frozenset({CORE_SUBFRAME, CORE_RELOAD})   # 木に入れない遷移
FORWARD_BACK = 0x01000000                               # 戻る・進むで来た訪問


@dataclass(frozen=True)
class Visit:
    id: int
    t: int            # 1601-01-01 起点の UTC µs
    url: str
    title: str
    core: int         # transition & 0xFF
    back: bool        # 戻る・進むによる訪問
    opener: int       # 新しいタブで開いた元の訪問 (無ければ 0)
    frm: int          # 同じタブで来た元の訪問 (無ければ 0)
    dur: int          # 開いていた時間 (µs)
    term: str | None = None   # 検索語 (keyword_search_terms にあれば)


def unix_s(t_us: int) -> float:
    return t_us / US - WEBKIT_TO_UNIX_S


def open_ro(path: str | pathlib.Path) -> sqlite3.Connection:
    """解析対象を書き換えないよう読み取り専用で開く."""
    uri = pathlib.Path(path).resolve().as_uri() + '?mode=ro'
    return sqlite3.connect(uri, uri=True)


def is_intact(path: str | pathlib.Path) -> bool:
    """コピーした DB が壊れていないか確かめる."""
    try:
        with contextlib.closing(open_ro(path)) as c:
            return c.execute('pragma quick_check').fetchone()[0] == 'ok'
    except sqlite3.DatabaseError:
        return False


def _columns(c: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in c.execute(f'pragma table_info({table})')}


def _read(path: str | pathlib.Path) -> list[Visit]:
    with contextlib.closing(open_ro(path)) as c:
        opener = 'v.opener_visit' if 'opener_visit' in _columns(c, 'visits') else '0'
        has_terms = bool(_columns(c, 'keyword_search_terms'))
        term = ('(select k.term from keyword_search_terms k where k.url_id = u.id limit 1)'
                if has_terms else 'null')
        rows = c.execute(f'''
            select v.id, v.visit_time, u.url, coalesce(u.title, ''), v.transition,
                   coalesce({opener}, 0), coalesce(v.from_visit, 0), coalesce(v.visit_duration, 0), {term}
            from visits v join urls u on u.id = v.url''').fetchall()
    return [Visit(id=vid, t=t, url=url, title=title, core=tr & 0xFF, back=bool(tr & FORWARD_BACK),
                  opener=op, frm=frm, dur=dur, term=term)
            for vid, t, url, title, tr, op, frm, dur, term in rows]


def load_visits(paths: list[str | pathlib.Path]) -> list[Visit]:
    """複数のスナップショットを訪問 ID で重ね合わせ, 時刻順に返す.

    History は古い訪問から消えていくので, 日付の違うコピーを重ねると長い期間を扱える.
    同じ ID は後に渡したファイルの値を使う (同じプロファイルのコピー同士に限る).
    リロードとサブフレームも返す. 親をたどるのに要るため.
    """
    merged: dict[int, Visit] = {}
    for p in paths:
        merged.update((v.id, v) for v in _read(p))
    return sorted(merged.values(), key=lambda v: (v.t, v.id))
