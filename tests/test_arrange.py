import json
import struct

import pytest
from _util import make_history, nav, record, snss, visit
from tab_lineage import arrange, cli, config, vivaldi
from tab_lineage.plan import Placed
from tab_lineage.session import Tab

CFG = config._merge(config.default(), {
    'workspace': [{'name': '大学', 'match': ['letus.example/*']}, {'name': '開発', 'match': ['github.com/*']},
                  {'name': '読みもの', 'match': ['blog.example/*']}],
    'arrange': {'emoji': {'大学': '🎓'}, 'reading_workspaces': ['読みもの'],
                'folder': [{'path': '言語/Python', 'match': ['docs.python.org/*']},
                           {'path': 'AI', 'title': 'claude'}]},
})
ACFG = arrange.ArrangeConfig.from_config(CFG)


def placed(k, url, title, status='keep', ws=None, goal='g'):
    return Placed(Tab(k, url, title), title, status, ws, goal, k)


def test_build_groups_stacks_singles_bookmarks_and_close():
    ps = [placed(0, 'https://github.com/a/b', 'repo', ws='開発', goal='tab-lineage'),
          placed(1, 'https://github.com/a/b/pull/1', 'PR', ws='開発', goal='tab-lineage'),
          placed(2, 'https://github.com/c/d', 'other', ws='開発', goal='c/d'),
          placed(3, 'https://letus.example/course', '講義', ws='大学', goal='講義'),
          placed(4, 'https://news.example/', 'ニュース'),
          placed(5, 'https://docs.python.org/3/x', 'x', status='stash'),
          placed(6, 'https://blog.example/claude', 'Claude の話', ws='読みもの'),
          placed(7, 'https://misc.example/', 'misc', status='stash'),
          placed(8, 'https://www.bing.com/search?q=a', 'a', status='serp'),
          placed(9, 'https://github.com/a/b', 'repo', status='dup', ws='開発'),
          placed(10, 'https://accounts.example/', '', status='drop')]
    arr = arrange.build(ps, ACFG)
    assert [(w['name'], w['emoji']) for w in arr['workspaces']] == [('大学', '🎓'), ('開発', ''), ('未分類', '')]
    dev = arr['workspaces'][1]['items']
    assert dev[0] == {'stack': 'tab-lineage', 'tabs': [['repo', 'https://github.com/a/b'],
                                                       ['PR', 'https://github.com/a/b/pull/1']]}
    assert dev[1] == {'stack': None, 'tabs': [['other', 'https://github.com/c/d']]}
    bm = arr['bookmarks']
    assert bm['title'] == 'あとで読む' and bm['links'] == [['misc', 'https://misc.example/']]
    assert [c['title'] for c in bm['children']] == ['言語', 'AI']
    assert bm['children'][0]['children'][0] == {'title': 'Python', 'children': [],
                                                'links': [['x', 'https://docs.python.org/3/x']]}
    assert bm['children'][1]['links'] == [['Claude の話', 'https://blog.example/claude']]
    assert [c['why'] for c in arr['close']] == ['検索結果', '重複', 'ログイン・決済など']
    assert arr['close'][2]['title'] == 'https://accounts.example/'   # 題名が無ければ URL
    assert arrange.validate(json.loads(json.dumps(arr))) is not None


def test_singles_come_after_stacks_and_min_stack_is_configurable():
    ps = [placed(0, 'https://a.example/1', 'single', goal='x'),
          placed(1, 'https://a.example/2', 'p', goal='y'),
          placed(2, 'https://a.example/3', 'q', goal='y'),
          placed(3, 'https://a.example/4', 'r', goal='y')]
    items = arrange.build(ps, ACFG)['workspaces'][0]['items']
    assert [i['stack'] for i in items] == ['y', None]
    three = arrange.ArrangeConfig.from_config(config._merge(CFG, {'arrange': {'min_stack': 4}}))
    assert [i['stack'] for i in arrange.build(ps, three)['workspaces'][0]['items']] == [None, None]


def test_same_link_is_bookmarked_once():
    ps = [placed(0, 'https://misc.example/', 'm', status='stash'), placed(1, 'https://misc.example/', 'm', status='stash')]
    assert arrange.build(ps, ACFG)['bookmarks']['links'] == [['m', 'https://misc.example/']]


@pytest.mark.parametrize('arr, msg', [
    ({}, 'workspaces が無い'),
    ({'workspaces': [{'items': []}]}, r'workspaces\[0\]: name'),
    ({'workspaces': [{'name': 'a', 'items': [{'tabs': []}]}]}, 'tabs が空'),
    ({'workspaces': [{'name': 'a', 'items': [{'tabs': ['https://x']}]}]}, r'\[題名, http'),
    ({'workspaces': [{'name': 'a', 'items': [{'stack': 1, 'tabs': [['t', 'https://x']]}]}]}, 'stack'),
    ({'workspaces': [], 'bookmarks': {'children': []}}, 'title が無い'),
    ({'workspaces': [], 'bookmarks': {'title': 'r', 'children': [{'title': 'c', 'links': [['t']]}]}}, 'bookmarks/r/c:'),
])
def test_validate_points_at_the_broken_place(arr, msg):
    with pytest.raises(ValueError, match=msg):
        arrange.validate(arr)


def test_outline_shows_the_tree_and_close_counts():
    arr = {'workspaces': [{'name': '大学', 'emoji': '🎓', 'items': [
        {'stack': '実験', 'tabs': [['a', 'u'], ['b', 'u']]}, {'stack': None, 'tabs': [['c', 'u']]}]}],
        'bookmarks': {'title': 'あとで読む', 'children': [{'title': 'AI', 'children': [], 'links': [['d', 'u']]}],
                      'links': []},
        'close': [{'title': 'x', 'url': 'u', 'why': '重複'}, {'title': 'y', 'url': 'u', 'why': '重複'}]}
    assert arrange.outline(arr).splitlines() == [
        '🎓 大学 (3 枚)', '  [実験] 2 枚', '    - a', '    - b', '  - c',
        'あとで読む/', '  AI/', '    - d', '開き直さない (2 枚): 重複 2']


def test_cli_arrange_and_apply_dry_run(tmp_path, capsys):
    db = tmp_path / 'History.db'
    make_history(db, [visit(1, 0, 'https://docs.example/merge', 'pandas merge', core=1)])
    sess = tmp_path / 'Session_1'
    sess.write_bytes(snss(nav(1, 0, 'https://docs.example/merge', 'pandas merge'),
                          nav(2, 0, 'https://www.bing.com/search?q=x', 'x'),
                          record(0, struct.pack('<ii', 1, 1))))
    out = tmp_path / 'data' / 'arrange.json'
    cli.main(['arrange', str(db), '--session', str(sess), '--out', str(out)])
    arr = json.loads(out.read_text(encoding='utf-8'))
    assert arr['workspaces'][0]['items'] == [{'stack': None, 'tabs': [['pandas merge', 'https://docs.example/merge']]}]
    assert arr['close'][0]['why'] == '検索結果'
    cli.main(['apply', str(out), '--dry-run'])
    assert '- pandas merge' in capsys.readouterr().out


def test_cli_apply_rejects_a_broken_plan(tmp_path):
    p = tmp_path / 'bad.json'
    p.write_text('{"workspaces": [{"name": ""}]}', encoding='utf-8')
    with pytest.raises(SystemExit, match='name'):
        cli.main(['apply', str(p), '--dry-run'])


class FakeResponse:
    def __init__(self, data):
        self.data = json.dumps(data).encode()

    def read(self, *a):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_ui_page_picks_the_vivaldi_window_page(monkeypatch):
    pages = [{'url': 'https://example.com/', 'webSocketDebuggerUrl': 'ws://127.0.0.1:9333/devtools/page/1'},
             {'url': 'chrome-extension://abc/window.html', 'webSocketDebuggerUrl': 'ws://127.0.0.1:9333/devtools/page/2'},
             {'url': 'chrome-extension://abc/main.html', 'webSocketDebuggerUrl': 'ws://127.0.0.1:9333/devtools/page/3'}]
    seen = []
    monkeypatch.setattr(vivaldi.urllib.request, 'urlopen', lambda url, timeout: seen.append(url) or FakeResponse(pages))
    assert vivaldi.ui_page(9333) == 'ws://127.0.0.1:9333/devtools/page/3'
    assert seen == ['http://127.0.0.1:9333/json/list']


def test_ui_page_explains_what_is_missing(monkeypatch):
    monkeypatch.setattr(vivaldi.urllib.request, 'urlopen', lambda url, timeout: FakeResponse([]))
    with pytest.raises(SystemExit, match='画面が見つからない'):
        vivaldi.ui_page(9222)

    def refuse(url, timeout):
        raise ConnectionRefusedError('refused')
    monkeypatch.setattr(vivaldi.urllib.request, 'urlopen', refuse)
    with pytest.raises(SystemExit, match='remote-debugging-port=9222'):
        vivaldi.ui_page(9222)


def test_expression_embeds_the_plan_as_json():
    arr = {'workspaces': [{'name': '</script>"', 'items': []}]}
    expr = vivaldi.expression(arr, hibernate=False)
    tail = expr[len(vivaldi.JS):]
    assert tail.startswith('(') and json.loads(tail[1:tail.index(', {"hibernate"')]) == arr
    assert '"hibernate": false' in tail


def test_browser_pages_and_local_files_are_not_reopened():
    ps = [placed(0, 'vivaldi://settings', '設定'), placed(1, 'file:///C:/a.pdf', 'a.pdf', status='stash'),
          placed(2, 'https://a.example/', 'a')]
    arr = arrange.build(ps, ACFG)
    assert [c['url'] for c in arr['close']] == ['vivaldi://settings', 'file:///C:/a.pdf']
    assert arr['bookmarks']['links'] == []


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'file:///C:/x', 'chrome://settings'])
def test_validate_accepts_only_web_urls(url):
    with pytest.raises(ValueError, match='http'):
        arrange.validate({'workspaces': [{'name': 'a', 'items': [{'tabs': [['t', url]]}]}]})
    with pytest.raises(ValueError, match='http'):
        arrange.validate({'workspaces': [], 'bookmarks': {'title': 'r', 'links': [['t', url]]}})


def test_ui_page_refuses_a_socket_elsewhere(monkeypatch):
    pages = [{'url': 'chrome-extension://abc/main.html', 'webSocketDebuggerUrl': 'ws://evil.example:9222/x'}]
    monkeypatch.setattr(vivaldi.urllib.request, 'urlopen', lambda url, timeout: FakeResponse(pages))
    with pytest.raises(SystemExit, match='手元の外'):
        vivaldi.ui_page(9222)


def test_expression_escapes_line_separators():
    expr = vivaldi.expression({'workspaces': [{'name': 'a\u2028b', 'items': []}]})
    assert '\u2028' not in expr and r'\u2028' in expr


@pytest.mark.parametrize('w, msg', [({'name': 'a'}, 'items が無い'), ({'name': 'a', 'items': 3}, 'items が無い'),
                                    ({'name': 'a', 'emoji': 1, 'items': []}, 'emoji')])
def test_validate_checks_each_workspace(w, msg):
    with pytest.raises(ValueError, match=msg):
        arrange.validate({'workspaces': [w]})
