"""今開いているタブの整理案を作る.

各タブに次のどれかを付け, ワークスペース -> スレッド -> タブ の木に並べる.
  serp   検索結果のページ. 子を開いたら役目は終わっている
  dup    前にあるタブと同じページ
  stash  最後に見てから stash_days 日以上たったページ. 保存して閉じる候補
  keep   それ以外
出力は viewer がそのまま描ける dict. 手で作った整理案 (同じ形の JSON) で丸ごと置き換えてもよい.
"""
from __future__ import annotations

from collections import defaultdict

from .episodes import EpisodeConfig, Timeline, thread_members
from .histdb import US
from .lineage import Graph, LineageConfig
from .privacy import Privacy, clean_url, host_of
from .session import Tab
from .text import clean_title

SHELF = '棚へ（保存して閉じる）'
UNSORTED = '分類待ち'


def _thread_labels(g: Graph, tl: Timeline) -> dict[int, str]:
    out: dict[int, str] = {}
    for ep in tl.episodes:
        for i, k in thread_members(g, ep).items():
            out[i] = ep.threads[k].label
    return out


def build(tabs: list[Tab], g: Graph, tl: Timeline, lcfg: LineageConfig, ecfg: EpisodeConfig,
          privacy: Privacy, now_us: int, stash_days: float = 3) -> dict:
    last_seen: dict[str, int] = {}
    node_of: dict[str, int] = {}
    for i, n in g.nodes.items():
        if n.t1 >= last_seen.get(n.url, -1):
            last_seen[n.url], node_of[n.url] = n.t1, i
    labels = _thread_labels(g, tl)

    rows: list[list[str]] = []
    groups: dict[tuple[str, str], list[list[int]]] = defaultdict(list)
    first_of: dict[str, list[int]] = {}
    for tab in tabs:
        if privacy.dropped(tab.url, tab.title):
            continue
        masked = privacy.mask_of(tab.url)
        host = masked[1] if masked else host_of(tab.url)
        # 伏せたタブは lineage と同じく host の URL で節を引く
        url = clean_url(f'{tab.url.split("://", 1)[0]}://{host}/') if masked else clean_url(tab.url, privacy.keep_query)
        title = masked[0] if masked else (clean_title(tab.title) or host)
        key = f'{host}\n{title}' if masked else url
        n = len(rows) + 1
        if key in first_of:
            status = 'dup'
        elif lcfg.is_search(tab.url):
            status = 'serp'
        elif url in last_seen and now_us - last_seen[url] > stash_days * 86400 * US:
            status = 'stash'
        else:
            status = 'keep'
        rows.append([title, status])
        if status == 'dup':
            first_of[key].append(n)
            continue
        entry = [n]
        first_of[key] = entry
        ws = SHELF if status == 'stash' else (ecfg.workspace_of(tab.url) or UNSORTED)
        goal = labels.get(node_of.get(url, -1)) or host
        groups[(ws, goal)].append(entry)

    tree: dict[str, list[dict]] = defaultdict(list)
    for (ws, goal), entries in groups.items():
        tree[ws].append({'k': 'goal', 'label': goal, 'sub': [{'t': e} for e in entries]})
    order = sorted(tree, key=lambda w: (w == SHELF, w == UNSORTED, w))
    return {
        'source': 'auto',
        'tabs': rows,
        'tree': [{'k': 'ws', 'label': w, 'shelf': w == SHELF, 'sub': tree[w]} for w in order],
    }
