"""回と木を 1 枚の HTML にする. viewer のテンプレートにデータの JSON を埋め込むだけで, 外部の読み込みは書体だけ."""
from __future__ import annotations

import datetime as dt
import json
from importlib import resources

from .episodes import Timeline, thread_members
from .histdb import unix_s
from .lineage import Graph
from .text import clean_title

MAX_BYTES = 16 * 1024 * 1024
ALT_TITLES = 3


def _node(g: Graph, tl: Timeline, i: int, ep, thread: int, ws_index: dict[str, int]) -> dict:
    n = g.nodes[i]
    t0 = unix_s(ep.t0)
    parent = n.parent if n.parent in ep.nodes else None
    d: dict = {'i': i, 'p': parent, 'th': thread, 'u': n.url, 'h': n.host,
               't': n.title if n.masked else clean_title(n.title),
               'n': len(n.times), 'ts': round(unix_s(n.t0) - t0),
               'a': [round(unix_s(t) - t0) for t in n.times if ep.t0 <= t <= ep.t1]}
    if n.kind == 'search':
        d['k'] = 1
    if n.term:
        d['q'] = n.term
    if n.via:
        d['v'] = n.via[0]
    if i in tl.stale:
        d['o'] = tl.stale[i]
    if i in tl.detours:
        d['d'] = 1
    if n.masked:
        d['m'] = 1
    if n.titles and not n.masked:
        d['x'] = [clean_title(t) for t in n.titles[:ALT_TITLES]]
    if i in tl.workspace:
        d['w'] = ws_index[tl.workspace[i]]
    return d


def payload(g: Graph, tl: Timeline, cfg: dict, labels: dict | None = None, plan: dict | None = None,
            built_at: dt.datetime | None = None) -> dict:
    labels = labels or {}
    ws_names = [w['name'] for w in cfg.get('workspace', ())]
    ws_index = {w: k for k, w in enumerate(ws_names)}
    episodes = []
    for ep in tl.episodes:
        cur = labels.get(ep.id, {})
        if cur.get('hide'):
            continue
        member = thread_members(g, ep)
        over = cur.get('threads', {})
        nodes = sorted(ep.nodes, key=lambda i: (g.nodes[i].t0, i))
        episodes.append({
            'id': ep.id, 't0': round(unix_s(ep.t0)), 't1': round(unix_s(ep.t1)),
            'label': cur.get('title') or ep.label, 'auto': ep.label,
            'note': cur.get('note', ''), 'featured': bool(cur.get('featured')),
            'v': ep.visits, 'ws': ws_index.get(ep.workspace) if ep.workspace else None,
            'cont': list(ep.cont),
            'th': [{'id': th.id, 'label': over.get(th.id, th.label), 'v': th.visits,
                    'ws': ws_index.get(th.workspace) if th.workspace else None} for th in ep.threads],
            'nodes': [_node(g, tl, i, ep, member.get(i, 0), ws_index) for i in nodes],
        })
    built_at = built_at or dt.datetime.now().astimezone()
    visits = sum(e['v'] for e in episodes)
    return {
        'meta': {
            'title': cfg.get('site', {}).get('title', 'Tab Lineage'),
            'lede': cfg.get('site', {}).get('lede', ''),
            'built_at': built_at.isoformat(timespec='minutes'),
            'tz': [list(o) for o in tl.tz_offsets],
            'range': [episodes[0]['t0'], episodes[-1]['t1']] if episodes else None,
            'counts': {'visits': visits, 'nodes': sum(len(e['nodes']) for e in episodes),
                       'episodes': len(episodes), 'detours': len(tl.detours), 'stale': len(tl.stale)},
            'lineage': g.stats,
            'params': {'gap_minutes': cfg['episodes']['gap_minutes'],
                       'stale_hours': cfg['episodes'].get('stale_hours', 12),
                       'search_window_seconds': cfg['lineage']['search_window_seconds'],
                       'theta': cfg['threads']['theta'], 'max_lanes': cfg['threads']['max_lanes']},
            'workspaces': ws_names,
        },
        'episodes': episodes,
        'plan': plan,
    }


def _embed(obj) -> str:
    """<script type="application/json"> に入れても閉じタグやコメントとして解釈されないようにする."""
    s = json.dumps(obj, ensure_ascii=False, separators=(',', ':'))
    return s.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')


def render(data: dict) -> str:
    viewer = resources.files(__package__).joinpath('viewer')
    html = viewer.joinpath('index.html').read_text(encoding='utf-8')
    css = viewer.joinpath('viewer.css').read_text(encoding='utf-8')
    js = viewer.joinpath('viewer.js').read_text(encoding='utf-8')
    title = data['meta']['title'].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    # 置き換えた文字列の中に次の目印が現れても二重に置き換えないよう, 1 回の分割で埋める
    parts = {'{{TITLE}}': title, '/*{{CSS}}*/': css, '{{DATA}}': _embed(data), '/*{{JS}}*/': js}
    out = []
    rest = html
    for mark, value in parts.items():
        head, sep, rest = rest.partition(mark)
        if not sep:
            raise ValueError(f'viewer/index.html に {mark} が無い')
        out += [head, value]
    out.append(rest)
    page = ''.join(out)
    if len(page.encode('utf-8')) > MAX_BYTES:
        raise ValueError(f'出力が {MAX_BYTES // 2**20} MB を超えた. 期間を絞るか gap を広げる')
    return page
