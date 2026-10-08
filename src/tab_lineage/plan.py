"""今開いているタブの整理案を作る.

各タブに次のどれかを付け, ワークスペース -> スレッド -> タブ の木に並べる.
  serp   検索結果のページ. 子を開いたら役目は終わっている
  dup    前にあるタブと同じページ
  stash  最後に見てから stash_days 日以上たったページ. 保存して閉じる候補
  keep   それ以外
出力は viewer がそのまま描ける dict. 手で作った整理案 (同じ形の JSON) で丸ごと置き換えてもよい.
"""
from __future__ import annotations

import fnmatch
import re
from collections import defaultdict
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .episodes import LABEL_MAX, EpisodeConfig, Timeline, thread_members
from .histdb import US, Visit
from .lineage import Graph, LineageConfig
from .privacy import Privacy, clean_url, host_of, scrub_title, site_key
from .session import Tab
from .text import clean_title

SHELF = '棚へ（保存して閉じる）'
UNSORTED = '分類待ち'
DETOUR = '寄り道: '
STASH_DAYS = 3  # 最後に見てからこの日数を過ぎたタブを放置とみなす
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


def _thread_workspaces(g: Graph, tl: Timeline) -> dict[int, str]:
    """節 -> 属するスレッドのワークスペース. スレッドで決まらなければ回のワークスペース."""
    out: dict[int, str] = {}
    for ep in tl.episodes:
        for i, k in thread_members(g, ep).items():
            if ws := ep.threads[k].workspace or ep.workspace:
                out[i] = ws
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
class Anchor:
    """このページから (たどって) 開いたタブを, このページの名前のスタックにまとめる. 授業のコースのページなど."""
    match: tuple[str, ...]              # 'host/path' の glob
    name: re.Pattern | None = None      # 題名から名前を取り出す正規表現 (最初のグループ, 無ければ全体)

    def label(self, url: str, title: str) -> str | None:
        if not any(fnmatch.fnmatchcase(site_key(url), p) for p in self.match):
            return None
        title = ' '.join(scrub_title(title).split())
        m = self.name.search(title) if self.name else None
        got = (m.group(1) if m.groups() else m.group(0)) if m else clean_title(title)
        return (got or '').strip()[:LABEL_MAX] or None


def anchor_labels(visits: list[Visit], anchors: tuple[Anchor, ...], privacy: Privacy) -> dict[str, str]:
    """page_key -> その URL の訪問から, 開いた元 (opener, 無ければ同じタブの前) をたどって最初に当たった Anchor の名前.

    系統樹は同じサイトの中の移動を畳み, クエリだけ違うページを 1 節にまとめるので, 生の訪問でたどる.
    同じ URL を何度も訪れたら, 名前の付いた最後の訪問の名前にする (再起動で戻したタブは開いた元が無いので名前を消さない).
    伏せる・除くページは名前に使わず, そこでたどるのも止める.
    """
    if not anchors:
        return {}
    by_visit: dict[int, str | None] = {}
    out: dict[str, str] = {}
    for v in sorted(visits, key=lambda v: (v.t, v.id)):
        if privacy.dropped(v.url, v.title) or privacy.mask_of(v.url):
            by_visit[v.id] = None
            continue
        own = next((x for a in anchors if (x := a.label(v.url, v.title))), None)
        got = own or by_visit.get(v.opener or v.frm)
        by_visit[v.id] = got
        if got:
            out[page_key(v.url)] = got
    return out


def _folded_nodes(visits: list[Visit], g: Graph, privacy: Privacy, node_of: dict[str, int]) -> dict[str, int]:
    """節の URL に無い URL -> その URL の最後の訪問をまとめた節. 伏せる・除くページは使わない."""
    out: dict[str, int] = {}
    for v in sorted(visits, key=lambda v: (v.t, v.id)):
        if privacy.dropped(v.url, v.title) or privacy.mask_of(v.url):
            continue
        url = clean_url(v.url, privacy.keep_query)
        if url not in node_of and (nid := g.owner.get(v.id)) is not None:
            out[url] = nid
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
             privacy: Privacy, now_us: int, stash_days: float = STASH_DAYS, labels: dict | None = None,
             anchored: dict[str, str] | None = None, visits: list[Visit] | None = None) -> list[Placed]:
    """labels は labels.toml を読んだもの. 名前を付けたスレッドはその名前を goal にする.
    anchored は anchor_labels の結果. 当たったタブはスレッドより先にその名前を goal にする.
    visits を渡すと, 節の URL に当たらないタブ (同じサイトの中でたどって畳まれたページ) も,
    同じ URL の最後の訪問をまとめた節から流れを引く."""
    last_seen: dict[str, int] = {}
    node_of: dict[str, int] = {}
    for i, n in g.nodes.items():
        if n.t1 >= last_seen.get(n.url, -1):
            last_seen[n.url], node_of[n.url] = n.t1, i
    folded = _folded_nodes(visits or [], g, privacy, node_of)
    detours = _detour_labels(g, tl)
    goals = _thread_labels(g, tl, labels or {}) | detours
    # 寄り道の枝は流れから外れているので, ワークスペースを引き継がない
    spaces = {i: w for i, w in _thread_workspaces(g, tl).items() if i not in detours}

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
        nid = node_of.get(url, folded.get(url, -1))
        goal = (None if masked else (anchored or {}).get(page_key(tab.url))) or goals.get(nid) or host
        # URL の規則に当たらないタブは, 開いた流れ (スレッド, 無ければ回) のワークスペースに入れる
        workspace = ecfg.workspace_of(tab.url) or spaces.get(nid)
        out.append(Placed(tab, title, status, workspace, goal, first))
    return out


def build(tabs: list[Tab], g: Graph, tl: Timeline, lcfg: LineageConfig, ecfg: EpisodeConfig,
          privacy: Privacy, now_us: int, stash_days: float = STASH_DAYS, labels: dict | None = None) -> dict:
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
