"""訪問を「どこから来たか」でつなぎ, 読める大きさの木に畳む (整理の規則 1〜5).

1. opener (新しいタブで開いた元), 無ければ from (同じタブで来た元) を親にする.
   リロードとサブフレームは節にせず, その子を親へつなぎ直す.
2. 同じタブでの同じサイト内の遷移は 1 つの節に畳む (地図や SNS はページ内で URL が変わり続ける).
3. 戻る・進むで同じ URL に戻った訪問と, 親が同じで URL も同じ兄弟は 1 つの節にまとめる.
4. 検索結果のページは kind='search' の節として残す.
5. 親が記録されていないリンク訪問は, 直前 (既定 30 秒以内) の検索を親にする.

入力の Visit は変更しない. 節は関数の中で組み立てて Graph として返す.
"""
from __future__ import annotations

import bisect
import fnmatch
from collections import Counter
from dataclasses import dataclass, field

from .histdb import CORE_LINK, SKIP_CORES, US, Visit
from .privacy import Privacy, clean_url, host_of, site_key
from .text import clean_title

MAX_TITLES = 8


@dataclass(frozen=True)
class LineageConfig:
    gap_s: int = 1800                  # これより離れた訪問は畳まない (回の区切りと同じ)
    search_window_s: int = 30          # 規則 5 の窓
    search: tuple[str, ...] = ()       # 検索結果ページの 'host/path' パターン
    fold_depth: tuple[tuple[str, int], ...] = ()   # 同じサイトとみなすパスの深さ (host パターン, 深さ)

    @classmethod
    def from_config(cls, cfg: dict) -> 'LineageConfig':
        lin = cfg.get('lineage', {})
        return cls(
            gap_s=int(cfg.get('episodes', {}).get('gap_minutes', 30)) * 60,
            search_window_s=int(lin.get('search_window_seconds', 30)),
            search=tuple(lin.get('search', ())),
            fold_depth=tuple((k, int(v)) for k, v in lin.get('fold_depth', {}).items()),
        )

    def is_search(self, url: str) -> bool:
        key = site_key(url)
        return any(fnmatch.fnmatchcase(key, p) for p in self.search)

    def section(self, url: str) -> str:
        """同じサイトとみなす範囲. 既定は host だけ. github.com なら深さ 2 で 'github.com/owner/repo'."""
        host = host_of(url)
        depth = next((d for p, d in self.fold_depth if fnmatch.fnmatchcase(host, p)), 0)
        if depth <= 0:
            return host
        parts = [p for p in site_key(url)[len(host):].split('/') if p][:depth]
        return '/'.join([host, *parts])


@dataclass
class Node:
    id: int                 # 最初の訪問の ID
    url: str                # クエリを落とした URL
    host: str
    title: str
    kind: str               # 'page' | 'search'
    core: int
    times: list[int]        # この節にまとめた訪問の時刻 (µs)
    dur: int                # 最も長く開いていた訪問の時間 (µs)
    parent: int | None
    via: str                # 'tab' | 'same' | 'search' (規則 5 で補った) | ''
    section: str
    term: str | None = None
    masked: bool = False
    src: str = ''           # 伏せる前の URL (クエリなし). ワークスペースの判定だけに使い, 出力には載せない
    titles: list[str] = field(default_factory=list)   # 畳んだ別の題名
    kids: list[int] = field(default_factory=list)

    @property
    def t0(self) -> int:
        return self.times[0]

    @property
    def t1(self) -> int:
        return self.times[-1]


@dataclass(frozen=True)
class Graph:
    nodes: dict[int, Node]
    roots: tuple[int, ...]
    stats: dict[str, int]


class _Builder:
    def __init__(self, cfg: LineageConfig, privacy: Privacy):
        self.cfg = cfg
        self.privacy = privacy
        self.gap = cfg.gap_s * US
        self.nodes: dict[int, Node] = {}
        self.rep: dict[int, int | None] = {}      # 訪問 ID -> それを代表する節の ID (消したものは親)
        self.last_by_url: dict[str, int] = {}
        self.search_times: list[tuple[int, int]] = []
        self.stats: Counter[str] = Counter()

    def find(self, vid: int | None) -> int | None:
        path = []
        while vid is not None and vid not in self.nodes:
            if vid not in self.rep:
                vid = None
                break
            path.append(vid)
            vid = self.rep[vid]
        for p in path:
            self.rep[p] = vid
        return vid

    def absorb(self, target: Node, other_times: list[int], dur: int, title: str, term: str | None) -> None:
        target.times.extend(other_times)
        target.dur = max(target.dur, dur)
        if title and title != target.title and title not in target.titles and len(target.titles) < MAX_TITLES:
            target.titles.append(title)
        target.term = target.term or term

    def add(self, v: Visit) -> None:
        raw = v.opener or v.frm
        parent = self.find(raw) if raw and raw != v.id else None
        if v.core in SKIP_CORES:
            self.rep[v.id] = parent
            return
        if self.privacy.dropped(v.url, v.title):
            self.rep[v.id] = parent
            self.stats['dropped'] += 1
            return
        masked = self.privacy.mask_of(v.url)
        src = clean_url(v.url)
        url = clean_url(v.url, self.privacy.keep_query)
        host = host_of(v.url)
        title = v.title
        term = self.privacy.clean_term(v.term)
        if masked is not None:
            title, host = masked
            url, term = clean_url(f'{v.url.split("://", 1)[0]}://{host}/'), None
            self.stats['masked'] += 1
        search = self.cfg.is_search(v.url)

        target = self._alias_target(v, url, parent)
        if target is not None:
            node = self.nodes[target]
            self.absorb(node, [v.t], v.dur, title, term)
            self.rep[v.id] = target
            self.stats['back' if v.back else 'same_url'] += 1
            self._note(node, v.t)
            return

        via = 'tab' if v.opener and parent else ('same' if parent else '')
        if parent is None and v.core == CORE_LINK and not search:
            parent = self._recent_search(v.t)
            if parent is not None:
                via = 'search'
                self.stats['search_parent'] += 1
        node = Node(id=v.id, url=url, host=host, title=title, kind='search' if search else 'page',
                    core=v.core, times=[v.t], dur=v.dur, parent=parent, via=via,
                    section=('search:' + host_of(v.url)) if search else self.cfg.section(url),
                    term=term, masked=masked is not None, src=src)
        self.nodes[v.id] = node
        self._note(node, v.t)

    def _alias_target(self, v: Visit, url: str, parent: int | None) -> int | None:
        """戻る・進む, または同じタブで同じ URL を開き直した訪問は, 既存の節に重ねる."""
        if v.back:
            cand = self.find(self.last_by_url.get(url))
        elif parent is not None and not v.opener and self.nodes[parent].url == url:
            cand = parent
        else:
            return None
        if cand is None or v.t - self.nodes[cand].t1 > self.gap:
            return None
        return cand

    def _recent_search(self, t: int) -> int | None:
        i = bisect.bisect_right(self.search_times, (t, float('inf'))) - 1
        if i < 0 or t - self.search_times[i][0] > self.cfg.search_window_s * US:
            return None
        return self.find(self.search_times[i][1])

    def _note(self, node: Node, t: int) -> None:
        self.last_by_url[node.url] = node.id
        if node.kind == 'search':
            self.search_times.append((t, node.id))   # 訪問は時刻順に来るので並びは保たれる

    def merge(self, src: Node, dst: Node, stat: str) -> None:
        self.absorb(dst, src.times, src.dur, src.title, src.term)
        for t in src.titles:
            self.absorb(dst, [], 0, t, None)
        del self.nodes[src.id]
        self.rep[src.id] = dst.id
        self.stats[stat] += 1

    def fold_same_site(self) -> None:
        """規則 2: 同じサイトの中を移っただけの節は親に畳む.

        同じタブでの遷移は常に畳む. 新しいタブで開いた場合は, 題名が親と同じとき (PR のタブ切り替えなど) だけ畳む.
        題名の違うページを同じサイトの中で次々に開いた場合は, 畳まずにそのサイトに入った最初の節の子として並べる.
        地図の店や SNS の投稿を渡り歩いても木が深くならず, 深さはサイトをまたいだ脱線だけを表す.
        """
        entry: dict[int, int] = {}
        for node in sorted(self.nodes.values(), key=lambda n: n.t0):
            pid = self.find(node.parent)
            parent = self.nodes.get(pid) if pid is not None else None
            if (node.via not in ('same', 'tab') or parent is None or parent.section != node.section
                    or parent.kind != node.kind or node.t0 - max(parent.times) > self.gap):
                entry[node.id] = node.id
                continue
            if node.via == 'same' or clean_title(node.title) == clean_title(parent.title):
                self.merge(node, parent, 'folded')
                continue
            top = entry.get(parent.id, parent.id)
            entry[node.id] = top
            if top != parent.id:
                node.parent = top
                self.stats['flattened'] += 1

    def dedup_siblings(self) -> None:
        """規則 3: 親が同じ兄弟 (根同士を含む) で, URL が同じか, 同じサイトで題名が同じものをまとめる.

        地図や SNS は同じページでも座標や追跡用のパスで URL が変わるので, 題名でも照合する.
        """
        seen: dict[tuple, Node] = {}
        for node in sorted(self.nodes.values(), key=lambda n: n.t0):
            pid = self.find(node.parent)
            keys = [(pid, 'url', node.url)]
            if node.title:
                keys.append((pid, 'title', node.section, node.kind, clean_title(node.title)))
            first = next((seen[k] for k in keys if k in seen and seen[k].id in self.nodes), None)
            if first is not None and node.t0 - max(first.times) <= self.gap:
                self.merge(node, first, 'deduped')
            else:
                seen.update((k, node) for k in keys)

    def finish(self) -> Graph:
        for node in self.nodes.values():
            node.times.sort()
            pid = self.find(node.parent)
            node.parent = pid if pid != node.id else None
            if node.parent is None and node.via != '':
                node.via = ''
            node.kids = []
        roots = []
        for node in sorted(self.nodes.values(), key=lambda n: n.t0):
            if node.parent is None:
                roots.append(node.id)
            else:
                self.nodes[node.parent].kids.append(node.id)
        self.stats['nodes'] = len(self.nodes)
        self.stats['roots'] = len(roots)
        return Graph(nodes=self.nodes, roots=tuple(roots), stats=dict(self.stats))


def build(visits: list[Visit], cfg: LineageConfig, privacy: Privacy | None = None) -> Graph:
    b = _Builder(cfg, privacy or Privacy())
    for v in visits:
        b.add(v)
    b.stats['visits'] = sum(1 for v in visits if v.core not in SKIP_CORES)
    b.fold_same_site()
    b.dedup_siblings()
    return b.finish()


def walk(graph: Graph, root: int):
    """根から深さ優先で (深さ, 節) を返す. 再帰しない."""
    stack = [(root, 0)]
    while stack:
        nid, depth = stack.pop()
        node = graph.nodes[nid]
        yield depth, node
        stack.extend((k, depth + 1) for k in reversed(node.kids))
