"""今のタブを, ブラウザへそのまま戻せる形に並べ直す.

出力は次の 3 つ.
  workspaces  ワークスペース -> スタック -> タブ. 同じスレッドのタブが min_stack 枚以上あればスタックにする
  bookmarks   あとで読むページのフォルダの木. 放置したタブと, 読みもののワークスペースのタブが入る
  close       開き直さないタブ. 検索結果・重複・プライバシーの設定で消すページ
分類は plan.py (サイトに載せる整理案) と同じものを使う. ただしこちらは自分のブラウザへ戻すための案なので,
URL と題名を伏せない. 出力をサイトやリポジトリに載せない.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass

from .plan import Placed
from .privacy import site_key

WHY = {'serp': '検索結果', 'dup': '重複', 'drop': 'ログイン・決済など', 'local': 'ブラウザの画面・ローカルファイル'}
# apply で開くのは web のページだけ. javascript: のブックマークや file: のタブを作らない
SCHEMES = ('http://', 'https://')


@dataclass(frozen=True)
class Folder:
    path: tuple[str, ...]
    match: tuple[str, ...] = ()        # 'host/path' の glob
    title: re.Pattern | None = None    # 題名の正規表現

    def hits(self, url: str, title: str) -> bool:
        key = site_key(url)
        return (any(fnmatch.fnmatchcase(key, p) for p in self.match)
                or bool(self.title and self.title.search(title)))


@dataclass(frozen=True)
class ArrangeConfig:
    order: tuple[str, ...] = ()                 # ワークスペースを並べる順 (設定の [[workspace]] の順)
    emoji: tuple[tuple[str, str], ...] = ()
    reading_root: str = 'あとで読む'
    reading_workspaces: tuple[str, ...] = ()    # このワークスペースのタブはブックマークへ回す
    folders: tuple[Folder, ...] = ()
    unsorted: str = '未分類'
    min_stack: int = 2

    @classmethod
    def from_config(cls, cfg: dict) -> 'ArrangeConfig':
        a = cfg.get('arrange', {})
        folders = tuple(
            Folder(tuple(x for x in f['path'].split('/') if x), tuple(f.get('match', ())),
                   re.compile(f['title'], re.I) if f.get('title') else None)
            for f in a.get('folder', ()))
        return cls(
            order=tuple(w['name'] for w in cfg.get('workspace', ())),
            emoji=tuple(a.get('emoji', {}).items()),
            reading_root=a.get('reading_root', 'あとで読む'),
            reading_workspaces=tuple(a.get('reading_workspaces', ())),
            folders=folders,
            unsorted=a.get('unsorted', '未分類'),
            min_stack=max(2, int(a.get('min_stack', 2))),
        )

    def folder_of(self, url: str, title: str) -> tuple[str, ...]:
        return next((f.path for f in self.folders if f.hits(url, title)), ())


def _insert(tree: dict, path: tuple[str, ...], link: list[str]) -> None:
    node = tree
    for name in path:
        node = next((c for c in node['children'] if c['title'] == name), None) or \
            _append(node['children'], {'title': name, 'children': [], 'links': []})
    if link not in node['links']:
        node['links'].append(link)


def _append(xs: list, x):
    xs.append(x)
    return x


def build(placed: list[Placed], cfg: ArrangeConfig) -> dict:
    groups: dict[str, dict[str, list[list[str]]]] = {}
    bookmarks = {'title': cfg.reading_root, 'children': [], 'links': []}
    close: list[dict] = []
    for p in placed:
        link = [p.tab.title or p.tab.url, p.tab.url]
        status = p.status if _is_web(p.tab.url) else 'local'
        if status in WHY:
            close.append({'title': link[0], 'url': link[1], 'why': WHY[status]})
        elif status == 'stash' or p.workspace in cfg.reading_workspaces:
            _insert(bookmarks, cfg.folder_of(p.tab.url, link[0]), link)
        else:
            ws = p.workspace or cfg.unsorted
            groups.setdefault(ws, {}).setdefault(p.goal, []).append(link)

    rank = {w: k for k, w in enumerate(cfg.order)}
    emoji = dict(cfg.emoji)
    workspaces = []
    for ws in sorted(groups, key=lambda w: (w == cfg.unsorted, rank.get(w, len(rank)))):
        stacks = [{'stack': goal, 'tabs': tabs} for goal, tabs in groups[ws].items() if len(tabs) >= cfg.min_stack]
        singles = [{'stack': None, 'tabs': tabs} for tabs in groups[ws].values() if len(tabs) < cfg.min_stack]
        workspaces.append({'name': ws, 'emoji': emoji.get(ws, ''), 'items': stacks + singles})
    return {'workspaces': workspaces, 'bookmarks': bookmarks, 'close': close}


def _is_web(url: str) -> bool:
    return url.lower().startswith(SCHEMES)


def _is_link(x) -> bool:
    return isinstance(x, list) and len(x) == 2 and all(isinstance(s, str) for s in x) and _is_web(x[1])


def _check_folder(node, parent: str) -> None:
    if not isinstance(node, dict) or not isinstance(node.get('title'), str) or not node['title']:
        raise ValueError(f'{parent}: フォルダに title が無い')
    where = f'{parent}/{node["title"]}'
    for c in node.get('children', []):
        _check_folder(c, where)
    if not all(_is_link(x) for x in node.get('links', [])):
        raise ValueError(f'{where}: links は [題名, http(s) の URL] の並び')


def validate(arr: dict) -> dict:
    """手で直した案も受け付けるので, ブラウザへ送る前に形を確かめる."""
    if not isinstance(arr.get('workspaces'), list):
        raise ValueError('workspaces が無い')
    for i, w in enumerate(arr['workspaces']):
        if not isinstance(w, dict) or not isinstance(w.get('name'), str) or not w['name']:
            raise ValueError(f'workspaces[{i}]: name が無い')
        if not isinstance(w.get('emoji', ''), str):
            raise ValueError(f'workspaces[{i}]: emoji は文字列')
        if not isinstance(w.get('items'), list):
            raise ValueError(f'workspaces[{i}]: items が無い')
        for j, item in enumerate(w['items']):
            where = f'workspaces[{i}].items[{j}]'
            if not isinstance(item, dict) or not isinstance(item.get('tabs'), list) or not item['tabs']:
                raise ValueError(f'{where}: tabs が空')
            if not all(_is_link(x) for x in item['tabs']):
                raise ValueError(f'{where}: tabs は [題名, http(s) の URL] の並び')
            if item.get('stack') is not None and not isinstance(item['stack'], str):
                raise ValueError(f'{where}: stack は文字列か null')
    if arr.get('bookmarks') is not None:
        _check_folder(arr['bookmarks'], 'bookmarks')
    return arr


def _outline_folder(node: dict, depth: int, out: list[str]) -> None:
    out.append(f'{"  " * depth}{node["title"]}/')
    for c in node.get('children', []):
        _outline_folder(c, depth + 1, out)
    out.extend(f'{"  " * (depth + 1)}- {t}' for t, _ in node.get('links', []))


def outline(arr: dict) -> str:
    """確かめるための木の表示."""
    out: list[str] = []
    for w in arr['workspaces']:
        n = sum(len(i['tabs']) for i in w['items'])
        out.append(f'{w.get("emoji", "")} {w["name"]} ({n} 枚)'.strip())
        for item in w['items']:
            if item.get('stack'):
                out.append(f'  [{item["stack"]}] {len(item["tabs"])} 枚')
                out.extend(f'    - {t}' for t, _ in item['tabs'])
            else:
                out.extend(f'  - {t}' for t, _ in item['tabs'])
    if arr.get('bookmarks') and (arr['bookmarks'].get('children') or arr['bookmarks'].get('links')):
        _outline_folder(arr['bookmarks'], 0, out)
    close = arr.get('close', [])
    if close:
        counts: dict[str, int] = {}
        for c in close:
            counts[c['why']] = counts.get(c['why'], 0) + 1
        out.append(f'開き直さない ({len(close)} 枚): ' + ', '.join(f'{k} {v}' for k, v in counts.items()))
    return '\n'.join(out)
