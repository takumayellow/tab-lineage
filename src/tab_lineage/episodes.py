"""木を「回」と「話題のスレッド」にまとめる (整理の規則 6〜9).

- 回:        無操作が gap_minutes 続いたら区切る. 節は最初の訪問の時刻で回に属する.
             親が前の回にある節は, その回では根として扱い cont に前の回を記録する.
- スレッド:   同じ回の木を, 題名の近さ (TF-IDF の余弦) か入口のサイトが同じかでまとめる (規則 6).
- 寄り道:     木の中で, 部分木の話題がそれ以外とほとんど重ならない枝に印を付ける (規則 7).
- 放置:       stale_hours 以上開いていたページに印を付ける (規則 8).
- ワークスペース: 設定の glob で訪問を分類し, 多数決で回とスレッドに付ける (規則 9).
"""
from __future__ import annotations

import bisect
import datetime as dt
import fnmatch
import math
import re
import zoneinfo
from collections import Counter
from dataclasses import dataclass

from .histdb import US, unix_s
from .lineage import Graph, Node
from .privacy import site_key
from .text import Vector, Vectorizer, clean_title, cosine, tokens

LABEL_MAX = 30
OTHER = 'そのほか'


@dataclass(frozen=True)
class EpisodeConfig:
    gap_s: int = 1800
    theta: float = 0.12            # スレッドに合流させる余弦の下限
    max_lanes: int = 8
    detour_theta: float = 0.04     # 寄り道とみなす余弦の上限
    detour_min: int = 5            # 寄り道を探す木の大きさ (節の数)
    detour_max: int = 400
    lone_search_s: int = 120       # 子の無い検索をこの秒数以内に始まる次の木へ付ける
    stale_hours: float = 12.0
    ws_share: float = 0.25         # 最多のワークスペースが訪問のこの割合に届かなければ未分類
    workspaces: tuple[tuple[str, tuple[str, ...]], ...] = ()

    @classmethod
    def from_config(cls, cfg: dict) -> 'EpisodeConfig':
        th = cfg.get('threads', {})
        return cls(
            gap_s=int(cfg.get('episodes', {}).get('gap_minutes', 30)) * 60,
            theta=float(th.get('theta', 0.12)),
            max_lanes=max(2, int(th.get('max_lanes', 8))),   # 1 だと全部が「そのほか」になる
            detour_theta=float(th.get('detour_theta', 0.04)),
            stale_hours=float(cfg.get('episodes', {}).get('stale_hours', 12)),
            ws_share=float(cfg.get('episodes', {}).get('ws_share', 0.25)),
            workspaces=tuple((w['name'], tuple(w.get('match', ()))) for w in cfg.get('workspace', ())),
        )

    def workspace_of(self, url: str) -> str | None:
        key = site_key(url)
        return next((name for name, pats in self.workspaces if any(fnmatch.fnmatchcase(key, p) for p in pats)), None)


@dataclass(frozen=True)
class Thread:
    id: str                    # 最初の木の根の ID (文字列). ラベルの上書きに使う
    label: str
    roots: tuple[int, ...]
    visits: int
    workspace: str | None


@dataclass(frozen=True)
class Episode:
    id: str                    # 開始時刻 (現地時刻, 分まで)
    t0: int                    # µs
    t1: int
    threads: tuple[Thread, ...]
    nodes: frozenset[int]
    cont: tuple[str, ...]      # 続きになっている前の回
    visits: int
    label: str
    workspace: str | None


@dataclass(frozen=True)
class Timeline:
    episodes: tuple[Episode, ...]
    detours: frozenset[int]    # 寄り道の枝の根
    stale: dict[int, float]    # 節 -> 開いていた日数
    workspace: dict[int, str]  # 節 -> ワークスペース
    tz_offsets: tuple[tuple[int, int], ...]  # (この時刻 [unix 秒] から, UTC からのずれ [分]). 夏時間の切り替えを表す


_OFFSET = re.compile(r'([+-])([01]\d|2[0-3]):([0-5]\d)')


def parse_tz(text: str | None) -> dt.tzinfo | None:
    """設定の [episodes] timezone. 'UTC', '+09:00' の形か IANA の名前 ('Asia/Tokyo'). 無ければ None (この環境の現地時刻)."""
    if not text:
        return None
    if text.upper() == 'UTC':
        return dt.timezone.utc
    if m := _OFFSET.fullmatch(text):
        delta = dt.timedelta(hours=int(m[2]), minutes=int(m[3]))
        return dt.timezone(-delta if m[1] == '-' else delta)
    try:
        return zoneinfo.ZoneInfo(text)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        raise ValueError(f'timezone = {text!r} が読めない. "UTC", "+09:00" か "Asia/Tokyo" の形で書く') from None


def _local(t_us: int, tz: dt.tzinfo | None) -> dt.datetime:
    """tz が無ければ, その時刻でのこの環境の現地時刻 (夏時間を含む)."""
    s = unix_s(t_us)
    return dt.datetime.fromtimestamp(s, tz) if tz else dt.datetime.fromtimestamp(s).astimezone()


def _offsets(spans: list[tuple[int, int]], tz: dt.tzinfo | None) -> tuple[tuple[int, int], ...]:
    out: list[tuple[int, int]] = []
    for t in (t for span in spans for t in span):
        off = int(_local(t, tz).utcoffset().total_seconds() // 60)
        if not out or out[-1][1] != off:
            out.append((round(unix_s(t)), off))
    return tuple(out)


def _node_tokens(n: Node) -> list[str]:
    # 伏せた節は畳んだ実題名も使わない (スレッド名や自動の名前に出るため)
    if n.masked:
        return tokens(n.term) if n.term else []
    toks = tokens(clean_title(n.title))
    if n.term:
        toks += tokens(n.term)
    for t in n.titles[:3]:
        toks += tokens(clean_title(t))
    return toks


def _spans(g: Graph, gap: int) -> list[tuple[int, int]]:
    times = sorted(t for n in g.nodes.values() for t in n.times)
    spans: list[tuple[int, int]] = []
    for t in times:
        if spans and t - spans[-1][1] <= gap:
            spans[-1] = (spans[-1][0], t)
        else:
            spans.append((t, t))
    return spans


class _Scorer:
    """節ごとの語とベクトルを 1 度だけ作って使い回す."""

    def __init__(self, g: Graph):
        self.g = g
        self.toks = {i: _node_tokens(n) for i, n in g.nodes.items()}
        self.vec = Vectorizer(self.toks.values())
        self._node_vec: dict[int, Vector] = {}

    def node_vec(self, i: int) -> Vector:
        if i not in self._node_vec:
            self._node_vec[i] = self.vec.vector(self.toks[i])
        return self._node_vec[i]

    def bag(self, ids) -> Counter[str]:
        c: Counter[str] = Counter()
        for i in ids:
            c.update(self.toks[i])
        return c

    def vector(self, ids) -> Vector:
        return self.vec.vector(self.bag(ids).elements())


def _members(g: Graph, root: int, inside: set[int]) -> list[int]:
    """回の中だけで根から子をたどる. 次の回に始まった子はそちらの根になる."""
    out, stack = [], [root]
    while stack:
        i = stack.pop()
        out.append(i)
        stack.extend(k for k in reversed(g.nodes[i].kids) if k in inside)
    return out


def _units(g: Graph, roots: list[int], sizes: dict[int, int], window: int) -> list[list[int]]:
    """子の無い単独の検索は, すぐ後に始まる木と組にする (検索してから別タブで開いた場合)."""
    units: list[list[int]] = []
    pending: list[int] = []
    for r in roots:
        node = g.nodes[r]
        if pending and node.t0 - g.nodes[pending[-1]].t0 > window:
            units.append(pending)
            pending = []
        pending.append(r)
        if not (sizes[r] == 1 and node.kind == 'search'):
            units.append(pending)
            pending = []
    if pending:
        units.append(pending)
    return units


def _label(sc: _Scorer, ids: list[int], weights: dict[int, int]) -> str:
    center = sc.vector(ids)
    best, best_score = None, -1.0
    for i in ids:
        n = sc.g.nodes[i]
        score = (cosine(sc.node_vec(i), center) + 1e-6) * (1 + math.log(weights[i]))
        if score > best_score:
            best, best_score = n, score
    if best is None:
        return ''
    text = (best.term if best.kind == 'search' and best.term else clean_title(best.title) or best.host) or '無題'
    return text if len(text) <= LABEL_MAX else text[:LABEL_MAX - 1] + '…'


def _majority(ws: dict[int, str], ids, weights: dict[int, int], share: float) -> str | None:
    """訪問数の最も多いワークスペース. それが全体の share に届かなければ None."""
    c: Counter[str] = Counter()
    total = 0
    for i in ids:
        total += weights[i]
        if i in ws:
            c[ws[i]] += weights[i]
    if not c:
        return None
    name, n = c.most_common(1)[0]
    return name if n >= total * share else None


def _threads(sc: _Scorer, cfg: EpisodeConfig, units: list[list[int]], trees: dict[int, list[int]],
             weights: dict[int, int], ws: dict[int, str]) -> tuple[Thread, ...]:
    groups: list[dict] = []
    for unit in units:
        ids = [i for r in unit for i in trees[r]]
        vec = sc.vector(ids)
        sections = {sc.g.nodes[r].section for r in unit if sc.g.nodes[r].kind != 'search'}
        best, best_cos = None, cfg.theta
        for grp in groups:
            c = cosine(vec, grp['vec'])
            if sections & grp['sections']:
                c = max(c, 1.0)
            if c >= best_cos:
                best, best_cos = grp, c
        if best is None:
            groups.append({'roots': list(unit), 'ids': ids, 'vec': vec, 'bag': sc.bag(ids), 'sections': sections})
        else:
            best['roots'] += unit
            best['ids'] += ids
            best['bag'].update(sc.bag(ids))
            best['vec'] = sc.vec.vector(best['bag'].elements())
            best['sections'] |= sections
    for grp in groups:
        grp['visits'] = sum(weights[i] for i in grp['ids'])
    groups.sort(key=lambda g_: -g_['visits'])
    if len(groups) > cfg.max_lanes:
        rest = groups[cfg.max_lanes - 1:]
        groups = groups[:cfg.max_lanes - 1] + [{
            'roots': sorted((r for g_ in rest for r in g_['roots']), key=lambda r: sc.g.nodes[r].t0),
            'ids': [i for g_ in rest for i in g_['ids']], 'visits': sum(g_['visits'] for g_ in rest), 'other': True}]
    out = []
    for grp in groups:
        roots = tuple(grp['roots'])
        label = OTHER if grp.get('other') else _label(sc, grp['ids'], weights)
        out.append(Thread(id=str(roots[0]), label=label, roots=roots, visits=grp['visits'],
                          workspace=_majority(ws, grp['ids'], weights, cfg.ws_share)))
    # レーンは始まった順に並べる
    return tuple(sorted(out, key=lambda t: sc.g.nodes[t.roots[0]].t0))


def _detours(sc: _Scorer, cfg: EpisodeConfig, tree: list[int], inside: set[int]) -> list[int]:
    """話題がそれ以外と重ならない枝の根を返す (サイトが変わった所だけを候補にする)."""
    if not cfg.detour_min <= len(tree) <= cfg.detour_max:
        return []
    g = sc.g
    total = sc.bag(tree)
    sub: dict[int, Counter[str]] = {}
    size: dict[int, int] = {}
    for i in reversed(tree):   # 前順の逆 = 子が先
        c = Counter(sc.toks[i])
        s = 1
        for k in g.nodes[i].kids:
            if k in inside and k in sub:
                c.update(sub[k])
                s += size[k]
        sub[i], size[i] = c, s
    found: list[int] = []
    skip: set[int] = set()
    for i in tree[1:]:
        n = g.nodes[i]
        if n.parent in skip or i in skip:
            skip.add(i)
            continue
        parent = g.nodes[n.parent]
        if n.section == parent.section or size[i] * 2 > len(tree) or not sub[i]:
            continue
        rest = total - sub[i]
        if not rest:
            continue
        if cosine(sc.vec.vector(sub[i].elements()), sc.vec.vector(rest.elements())) < cfg.detour_theta:
            found.append(i)
            skip.add(i)
    return found


def build(g: Graph, cfg: EpisodeConfig, tz: dt.tzinfo | None = None) -> Timeline:
    sc = _Scorer(g)
    gap = cfg.gap_s * US
    spans = _spans(g, gap)
    starts = [s for s, _ in spans]

    ep_of = {i: bisect.bisect_right(starts, n.t0) - 1 for i, n in g.nodes.items()}
    weights = {i: len(n.times) for i, n in g.nodes.items()}
    ws = {i: w for i, n in g.nodes.items() if (w := cfg.workspace_of(n.src or n.url))}
    stale = {i: round(n.dur / US / 86400, 1) for i, n in g.nodes.items() if n.dur >= cfg.stale_hours * 3600 * US}

    by_ep: dict[int, list[int]] = {}
    for i in sorted(g.nodes, key=lambda i: g.nodes[i].t0):
        by_ep.setdefault(ep_of[i], []).append(i)

    ids = [_local(s, tz).strftime('%Y-%m-%dT%H:%M') for s in starts]
    episodes, detours = [], set()
    for k, members in sorted(by_ep.items()):
        inside = set(members)
        roots = [i for i in members if g.nodes[i].parent is None or g.nodes[i].parent not in inside]
        trees = {r: _members(g, r, inside) for r in roots}
        for r, tree in trees.items():
            detours.update(_detours(sc, cfg, tree, inside))
        units = _units(g, roots, {r: len(t) for r, t in trees.items()}, cfg.lone_search_s * US)
        threads = _threads(sc, cfg, units, trees, weights, ws)
        cont = sorted({ids[ep_of[g.nodes[r].parent]] for r in roots
                       if g.nodes[r].parent is not None and ep_of[g.nodes[r].parent] != k})
        top = sorted(threads, key=lambda t: -t.visits)
        label = ' / '.join([t.label for t in top if t.label != OTHER][:2])
        episodes.append(Episode(
            id=ids[k], t0=spans[k][0], t1=spans[k][1], threads=threads, nodes=frozenset(members),
            cont=tuple(cont), visits=sum(weights[i] for i in members), label=label,
            workspace=_majority(ws, members, weights, cfg.ws_share)))
    return Timeline(episodes=tuple(episodes), detours=frozenset(detours), stale=stale, workspace=ws,
                    tz_offsets=_offsets(spans, tz))


def thread_members(g: Graph, ep: Episode) -> dict[int, int]:
    """回の中の節 -> スレッドの番号 (ep.threads の添字)."""
    inside = set(ep.nodes)
    out: dict[int, int] = {}
    for k, th in enumerate(ep.threads):
        for r in th.roots:
            out.update((i, k) for i in _members(g, r, inside))
    return out
