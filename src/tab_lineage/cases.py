"""ほかの道具 (chronofit の週のふり返り) が選んだ事例を, その時刻を含む回に付ける.

事例の JSON は配列で, 1 件が {week, start, end, reason, note, browser} を持つ (start / end は ISO 8601).
ブラウザを使った事例だけを, 時間の重なりが最も大きい回に付ける. 付けた回は読みもの (featured) になり,
所見 (note) が無ければ事例の解説が入る. 人が labels.toml に書いた名前と所見はそのまま残す.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib

from .episodes import Timeline
from .histdb import unix_s


def load(path: str | pathlib.Path | None) -> list[dict]:
    """事例の配列. ファイルが無い・形が違うときは空 (サイトの組み立ては止めない)."""
    if not path:
        return []
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    return [c for c in data if isinstance(c, dict)] if isinstance(data, list) else []


def _span(case: dict) -> tuple[float, float] | None:
    try:
        start = dt.datetime.fromisoformat(case['start']).timestamp()
        end = dt.datetime.fromisoformat(case['end']).timestamp()
    except (KeyError, TypeError, ValueError):
        return None
    return (start, end) if end >= start else None


def episode_of(tl: Timeline, case: dict) -> str | None:
    """事例の時間と重なりが最も大きい回の ID. 重なる回が無ければ None."""
    span = _span(case)
    if span is None:
        return None
    best, best_overlap = None, 0.0
    for ep in tl.episodes:
        # 訪問が 1 回だけの回は長さが 0 なので, 事例の中にあれば重なり 0 でも候補にする
        t0, t1 = unix_s(ep.t0), unix_s(ep.t1)
        overlap = min(span[1], t1) - max(span[0], t0)
        point_inside = t0 == t1 and span[0] <= t0 <= span[1]
        if overlap > best_overlap or (best is None and point_inside):
            best, best_overlap = ep.id, overlap
    return best


def _text(case: dict) -> str:
    head = '・'.join(str(case[k]) for k in ('week', 'reason') if case.get(k))
    note = str(case.get('note') or '').strip()
    return f'{head}: {note}' if head and note else head or note


def attach(labels: dict, tl: Timeline, cases: list[dict]) -> dict:
    """事例を付けた新しい labels (元の labels は書き換えない)."""
    notes: dict[str, list[str]] = {}
    for case in cases:
        if not case.get('browser'):
            continue
        ep_id = episode_of(tl, case)
        text = _text(case)
        if ep_id and text:
            notes.setdefault(ep_id, []).append(text)
    out = dict(labels)
    for ep_id, texts in notes.items():
        cur = out.get(ep_id, {})
        if cur.get('hide'):
            continue
        out[ep_id] = {**cur, 'id': ep_id, 'featured': True, 'note': cur.get('note') or ' / '.join(texts)}
    return out
