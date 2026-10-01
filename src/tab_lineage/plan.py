"""今開いているタブの整理案を作る.

各タブに次のどれかを付け, ワークスペース -> スレッド -> タブ の木に並べる.
  serp   検索結果のページ. 子を開いたら役目は終わっている
  dup    前にあるタブと同じページ
  stash  最後に見てから stash_days 日以上たったページ. 保存して閉じる候補
  keep   それ以外
出力は viewer がそのまま描ける dict. 手で作った整理案 (同じ形の JSON) で丸ごと置き換えてもよい.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .episodes import LABEL_MAX, EpisodeConfig, Timeline, thread_members
from .histdb import US
from .lineage import Graph, LineageConfig
from .privacy import Privacy, clean_url, host_of, scrub_title
from .session import Tab
from .text import clean_title

SHELF = '棚へ（保存して閉じる）'
UNSORTED = '分類待ち'
DETOUR = '寄り道: '
# 同じページかを比べるときに無視するクエリ (広告やメールの計測用). ほかのクエリが違えば別のページ
_TRACKING = re.compile(r'utm_\w+|fbclid|gclid|dclid|msclkid|yclid|igshid|mc_cid|mc_eid|_ga|_gl')


def page_key(url: str) -> str:
    """重複を見分けるための URL. フラグメントと計測用のクエリだけを落とす.

    ?id=101 と ?id=202 のようにクエリで中身が変わるページを同じページとして閉じないよう,
    サイトに載せる URL (クエリを全部落とす) とは別に作る.
    """
    s = urlsplit(url)
    query = urlencode([(k, v) for k, v in parse_qsl(s.query, keep_blank_values=True) if not _TRACKING.fullmatch(k)])
    return urlunsplit((s.scheme, s.netloc.lower(), s.path, query, ''))


def _thread_labels(g: Graph, tl: Timeline, labels: dict) -> dict[int, str]:
    """節 -> 属するスレッドの名前. labels.toml で名前を付けたスレッドはその名前."""
    out: dict[int, str] = {}
    for ep in tl.episodes:
        named = labels.get(ep.id, {}).get('threads', {})
        for i, k in thread_members(g, ep).items():
            out[i] = named.get(ep.threads[k].id) or ep.threads[k].label
    return out


def _detour_labels(g: Graph, tl: Timeline) -> dict[int, str]:
    """寄り道の枝の中の節 -> '寄り道: 枝の根の題名'. 寄り道の中の寄り道は内側の枝の名前."""
    out: dict[int, str] = {}
    for r in tl.detours:
        n = g.nodes[r]
        text = (n.term if n.kind == 'search' and n.term else clean_title(n.title)) or n.host
        label = DETOUR + (text if len(text) <= LABEL_MAX else text[:LABEL_MAX - 1] + '…')
        stack = [r]
        while stack:
            i = stack.pop()
            out.setdefault(i, label)
            stack.extend(k for k in g.nodes[i].kids if k not in tl.detours)
    return out


@dataclass(frozen=True)
class Placed:
    """1 枚のタブの分類. status は keep / dup / serp / stash と, プライバシーの設定で消す drop."""
    tab: Tab
    title: str            # 伏せたあとの題名 (サイトに出す)
    status: str
    workspace: str | None
    goal: str             # 属するスレッドの名前. 無ければ host
    first: int            # dup のとき, 同じページの最初のタブの位置. それ以外は自分の位置


def classify(tabs: list[Tab], g: Graph, tl: Timeline, lcfg: LineageConfig, ecfg: EpisodeConfig,
             privacy: Privacy, now_us: int, stash_days: float = 3, labels: dict | None = None) -> list[Placed]:
    """labels は labels.toml を読んだもの. 名前を付けたスレッドはその名前を goal にする."""
    last_seen: dict[str, int] = {}
    node_of: dict[str, int] = {}
    for i, n in g.nodes.items():
        if n.t1 >= last_seen.get(n.url, -1):
            last_seen[n.url], node_of[n.url] = n.t1, i
    goals = _thread_labels(g, tl, labels or {}) | _detour_labels(g, tl)

    out: list[Placed] = []
    first_of: dict[str, int] = {}
    for tab in tabs:
        if privacy.dropped(tab.url, tab.title):
            out.append(Placed(tab, '', 'drop', None, '', len(out)))
            continue
        masked = privacy.mask_of(tab.url)
        host = masked[1] if masked else host_of(tab.url)
        # 伏せたタブは lineage と同じく host の URL で節を引く
        url = clean_url(f'{tab.url.split("://", 1)[0]}://{host}/') if masked else clean_url(tab.url, privacy.keep_query)
        title = masked[0] if masked else (clean_title(scrub_title(tab.title)) or host)
        key = f'{host}\n{title}' if masked else page_key(tab.url)
        if key in first_of:
            status = 'dup'
        elif lcfg.is_search(tab.url):
            status = 'serp'
        elif url in last_seen and now_us - last_seen[url] > stash_days * 86400 * US:
            status = 'stash'
        else:
            status = 'keep'
        first = first_of.setdefault(key, len(out))
        goal = goals.get(node_of.get(url, -1)) or host
        out.append(Placed(tab, title, status, ecfg.workspace_of(tab.url), goal, first))
    return out


def build(tabs: list[Tab], g: Graph, tl: Timeline, lcfg: LineageConfig, ecfg: EpisodeConfig,
          privacy: Privacy, now_us: int, stash_days: float = 3, labels: dict | None = None) -> dict:
    rows: list[list[str]] = []
    groups: dict[tuple[str, str], list[list[int]]] = defaultdict(list)
    entries: dict[int, list[int]] = {}
    for k, p in enumerate(classify(tabs, g, tl, lcfg, ecfg, privacy, now_us, stash_days, labels)):
        if p.status == 'drop':
            continue
        rows.append([p.title, p.status])
        n = len(rows)
        if p.status == 'dup':
            entries[p.first].append(n)
            continue
        entries[k] = [n]
        ws = SHELF if p.status == 'stash' else (p.workspace or UNSORTED)
        groups[(ws, p.goal)].append(entries[k])

    tree: dict[str, list[dict]] = defaultdict(list)
    for (ws, goal), members in groups.items():
        tree[ws].append({'k': 'goal', 'label': goal, 'sub': [{'t': e} for e in members]})
    order = sorted(tree, key=lambda w: (w == SHELF, w == UNSORTED, w))
    return {
        'source': 'auto',
        'tabs': rows,
        'tree': [{'k': 'ws', 'label': w, 'shelf': w == SHELF, 'sub': tree[w]} for w in order],
    }
