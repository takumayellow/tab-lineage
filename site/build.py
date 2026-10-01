"""解説サイトを作る. 例は examples/sample の架空の履歴だけから作り, 実際の閲覧履歴は使わない.

  python site/build.py _site

  _site/index.html       解説 (site/index.html にサンプルの整理案を差し込む)
  _site/demo/index.html  サンプルから作った系統樹のサイト (tab-lineage build の出力)
"""
from __future__ import annotations

import contextlib
import html
import importlib.util
import io
import pathlib
import re
import sys
import tempfile
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[1]
SAMPLE = ROOT / 'examples' / 'sample'
TEMPLATE = ROOT / 'site' / 'index.html'
sys.path.insert(0, str(ROOT / 'src'))

from tab_lineage import __version__, arrange, cli, config, plan, session  # noqa: E402

# 1 枚のタブの行き先の種類. 色と凡例の名前
KINDS = {'ws': 'ワークスペース', 'read': 'あとで読む', 'close': '閉じる'}


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def _load_sample():
    spec = importlib.util.spec_from_file_location('make_sample', SAMPLE / 'make_sample.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _host(url: str) -> str:
    if '://' not in url:
        return url
    scheme, rest = url.split('://', 1)
    host = rest.split('/', 1)[0]
    return f'{scheme}://{host}' if scheme not in ('http', 'https') else host


def fates(placed: list[plan.Placed], arr: dict, acfg: arrange.ArrangeConfig) -> list[dict]:
    """タブごとに, 案のどこへ行くかと, その理由."""
    where: dict[tuple[str, str], tuple[str, str | None]] = {}
    for w in arr['workspaces']:
        for item in w['items']:
            for title, url in item['tabs']:
                where[(title, url)] = (w['name'], item['stack'])
    out = []
    for p in placed:
        link = (p.tab.title or p.tab.url, p.tab.url)
        status = p.status if arrange._is_web(p.tab.url) else 'local'
        if status in arrange.WHY:
            reason = {'serp': '検索結果のページ．ここから開いたページはもう別のタブにある',
                      'dup': '同じページが前のタブにある',
                      'drop': 'ログインの画面．設定の privacy.drop に当たる',
                      'local': 'ブラウザの設定画面．開き直す必要が無い'}[status]
            out.append({'p': p, 'kind': 'close', 'dest': arrange.WHY[status], 'reason': reason})
        elif status == 'stash' or p.workspace in acfg.reading_workspaces:
            folder = '/'.join((acfg.reading_root, *acfg.folder_of(p.tab.url, link[0])))
            reason = ('最後に見てから 3 日以上たっている' if status == 'stash'
                      else f'読みもののワークスペース「{p.workspace}」のタブ')
            out.append({'p': p, 'kind': 'read', 'dest': folder, 'reason': reason})
        else:
            ws, stack = where[link]
            emoji = dict(acfg.emoji).get(ws, '')
            dest = f'{emoji}{ws}' + (f' › {stack}' if stack else '')
            if p.workspace is None:
                reason = '設定のどのワークスペースにも当たらない'
            else:
                reason = f'{_host(p.tab.url)} は設定で「{ws}」'
            if stack and stack.startswith(plan.DETOUR):
                reason += '．本筋から外れた枝なので寄り道のスタック'
            elif stack:
                reason += '．同じスレッドのタブとスタックに'
            out.append({'p': p, 'kind': 'ws', 'dest': dest, 'reason': reason})
    return out


def render_before(rows: list[dict]) -> str:
    windows: dict[int, list[dict]] = {}
    for r in rows:
        windows.setdefault(r['p'].tab.window, []).append(r)
    parts = []
    for n, (win, items) in enumerate(sorted(windows.items()), 1):
        lis = ''.join(
            f'<li class="tab k-{r["kind"]}"><span class="dot" aria-hidden="true"></span>'
            f'<span class="tt">{esc(r["p"].tab.title or r["p"].tab.url)}</span>'
            f'<span class="host">{esc(_host(r["p"].tab.url))}</span></li>'
            for r in sorted(items, key=lambda r: r['p'].tab.index))
        parts.append(f'<figure class="win"><figcaption><span class="lights" aria-hidden="true"></span>'
                     f'ウィンドウ {n}<span class="count">{len(items)} 枚</span></figcaption><ol>{lis}</ol></figure>')
    return ''.join(parts)


def render_workspaces(arr: dict) -> str:
    parts = []
    for w in arr['workspaces']:
        n = sum(len(i['tabs']) for i in w['items'])
        items = []
        for item in w['items']:
            tabs = ''.join(f'<li>{esc(t)}</li>' for t, _ in item['tabs'])
            if item['stack']:
                cls = 'stack detour' if item['stack'].startswith(plan.DETOUR) else 'stack'
                items.append(f'<div class="{cls}"><p class="sname">{esc(item["stack"])}'
                             f'<span class="count">{len(item["tabs"])} 枚</span></p><ul>{tabs}</ul></div>')
            else:
                items.append(f'<ul class="single">{tabs}</ul>')
        parts.append(f'<section class="ws"><h4><span class="emoji" aria-hidden="true">{esc(w["emoji"])}</span>'
                     f'{esc(w["name"])}<span class="count">{n} 枚</span></h4>{"".join(items)}</section>')
    return ''.join(parts)


def _folder(node: dict) -> str:
    kids = ''.join(_folder(c) for c in node.get('children', []))
    links = ''.join(f'<li class="bm">{esc(t)}</li>' for t, _ in node.get('links', []))
    return f'<li class="folder"><span class="fname">{esc(node["title"])}</span><ul>{kids}{links}</ul></li>'


def render_bookmarks(arr: dict) -> str:
    return f'<ul class="bmtree">{_folder(arr["bookmarks"])}</ul>'


def render_close(arr: dict) -> str:
    return ''.join(f'<li><span class="why">{esc(c["why"])}</span>{esc(c["title"])}</li>' for c in arr['close'])


def render_fates(rows: list[dict]) -> str:
    out = []
    for r in rows:
        p = r['p']
        out.append(f'<tr class="k-{r["kind"]}"><td class="num">{p.tab.window}</td>'
                   f'<td><span class="tt">{esc(p.tab.title or p.tab.url)}</span>'
                   f'<span class="host">{esc(_host(p.tab.url))}</span></td>'
                   f'<td><span class="chip">{KINDS[r["kind"]]}</span> {esc(r["dest"])}</td>'
                   f'<td class="reason">{esc(r["reason"])}</td></tr>')
    return ''.join(out)


def _run(argv: list[str]) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli.main(argv)
    return buf.getvalue()


def build(out_dir: pathlib.Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    conf, labels = SAMPLE / 'config.toml', SAMPLE / 'labels.toml'
    with tempfile.TemporaryDirectory() as tmp:
        history, sess = _load_sample().make(tmp)
        common = [str(history), '--config', str(conf)]
        _run(['build', *common, '--session', str(sess), '--labels', str(labels),
              '--out', str(out_dir / 'demo' / 'index.html')])
        outline_text = _run(['outline', *common, '--labels', str(labels)])
        r = cli.analyze([str(history)], str(conf))
        tabs = session.load(str(sess))
    placed = plan.classify(tabs, r.graph, r.timeline, r.lcfg, r.ecfg, r.privacy, r.last_visit,
                           labels=config.load_labels(str(labels)))
    acfg = arrange.ArrangeConfig.from_config(r.cfg)
    arr = arrange.build(placed, acfg)
    rows = fates(placed, arr, acfg)

    def count_links(node: dict) -> int:
        return len(node['links']) + sum(count_links(c) for c in node['children'])

    kinds = Counter(r['kind'] for r in rows)
    values = {
        'VERSION': __version__,
        'N_TABS': str(len(rows)),
        'N_WINDOWS': str(len({r['p'].tab.window for r in rows})),
        'N_WS': str(len(arr['workspaces'])),
        'N_STACKS': str(sum(1 for w in arr['workspaces'] for i in w['items'] if i['stack'])),
        'N_KEEP': str(kinds['ws']),
        'N_BOOKMARKS': str(count_links(arr['bookmarks'])),
        'N_CLOSE': str(len(arr['close'])),
        'BEFORE': render_before(rows),
        'AFTER_WS': render_workspaces(arr),
        'AFTER_BM': render_bookmarks(arr),
        'AFTER_CLOSE': render_close(arr),
        'FATES': render_fates(rows),
        'ARRANGE_OUT': esc(arrange.outline(arr)),
        'OUTLINE_OUT': esc(outline_text.rstrip()),
        'CONFIG': esc(conf.read_text(encoding='utf-8').rstrip()),
        'LABELS': esc(labels.read_text(encoding='utf-8').rstrip()),
    }
    page = TEMPLATE.read_text(encoding='utf-8')
    # 差し込んだ中身にたまたま {{X}} があっても展開しないよう, 型紙の印を 1 回で置き換える
    page = re.sub(r'\{\{([A-Z_]+)\}\}', lambda m: values[m.group(1)], page)
    (out_dir / 'index.html').write_text(page, encoding='utf-8')
    (out_dir / '.nojekyll').write_text('', encoding='utf-8')
    print(f'{out_dir / "index.html"}: タブ {len(rows)} 枚 -> ワークスペース {values["N_WS"]} つ, '
          f'スタック {values["N_STACKS"]} 個, ブックマーク {values["N_BOOKMARKS"]} 件, 閉じる {values["N_CLOSE"]} 枚')


if __name__ == '__main__':
    build(pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else '_site'))
