import json
import re
import struct

import pytest
from _util import make_history, nav, record, snss, visit
from tab_lineage import arrange, cli, config, judge, plan, snapshot, vivaldi
from tab_lineage.histdb import Visit
from tab_lineage.plan import Placed
from tab_lineage.privacy import Privacy
from tab_lineage.session import Tab

CFG = config._merge(config.default(), {
    'workspace': [{'name': '大学', 'match': ['lms.example/*']}, {'name': '開発', 'match': ['github.com/*']},
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
          placed(3, 'https://lms.example/course', '講義', ws='大学', goal='講義'),
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
    assert '"hibernate": false' in tail and '"close": false' in tail
    assert '"close": true' in vivaldi.expression(arr, close=True)


def test_apply_close_backs_up_the_tabs_first(tmp_path, monkeypatch, capsys):
    plan = tmp_path / 'plan.json'
    plan.write_text(json.dumps({'workspaces': [{'name': 'a', 'items': [{'tabs': [['t', 'https://a.example/']]}]}],
                                'close': [{'title': 's', 'url': 'https://s.example/', 'why': '検索結果'}]}), encoding='utf-8')
    calls = []
    monkeypatch.setattr(vivaldi, 'tabs', lambda port: calls.append('tabs') or [{'id': 1, 'url': 'https://s.example/'}])

    def apply(arr, port, hibernate, close):
        calls.append(('apply', close))
        return {'tabs': 1, 'made': 0, 'adopted': 1, 'already': 0, 'kept': 0, 'stacks': 0, 'failed': [], 'created': [],
                'bookmarks': {'added': 0, 'skipped': 0}, 'closed': 1, 'hibernated': 0, 'switched': True,
                'left': [['新しいタブ', 'https://new.example/']]}
    monkeypatch.setattr(vivaldi, 'apply', apply)
    cli.main(['apply', str(plan), '--close'])
    assert calls == ['tabs', ('apply', True)]
    [backup] = tmp_path.glob('plan.before-*.json')
    assert json.loads(backup.read_text(encoding='utf-8')) == [{'id': 1, 'url': 'https://s.example/'}]
    out = capsys.readouterr().out
    assert '閉じた 1 枚' in out and '閉じなかったタブ 1 枚' in out and 'https://new.example/' in out


def test_apply_without_close_also_backs_up_and_lists_what_it_left(tmp_path, monkeypatch, capsys):
    plan = tmp_path / 'plan.json'
    plan.write_text(json.dumps({'workspaces': []}), encoding='utf-8')
    monkeypatch.setattr(vivaldi, 'tabs', lambda port: [])
    monkeypatch.setattr(vivaldi, 'apply', lambda arr, port, hibernate, close: {
        'tabs': 0, 'made': 0, 'adopted': 0, 'already': 0, 'kept': 1, 'stacks': 0, 'created': [], 'closed': 0,
        'failed': ['a/b: スタックを作れない'], 'bookmarks': {'added': 0, 'skipped': 0}, 'hibernated': 0,
        'switched': True, 'left': [['固定', 'https://pin.example/']]})
    cli.main(['apply', str(plan)])
    assert len(list(tmp_path.glob('plan.before-*.json'))) == 1
    out = capsys.readouterr().out
    assert '固定したタブのまま 1 枚' in out and 'できなかった: a/b' in out and '残したタブ 1 枚 (閉じるなら --close)' in out


def test_browser_pages_are_closed_and_local_files_kept():
    ps = [placed(0, 'vivaldi://settings', '設定'), placed(1, 'file:///C:/a.pdf', 'a.pdf', status='stash'),
          placed(2, 'https://a.example/', 'a'), placed(3, 'chrome://newtab/', '')]
    arr = arrange.build(ps, ACFG)
    assert [c['url'] for c in arr['close']] == ['vivaldi://settings', 'chrome://newtab/']
    assert {c['why'] for c in arr['close']} == {'ブラウザの画面'}
    assert arr['bookmarks']['links'] == [['a.pdf', 'file:///C:/a.pdf']]


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'data:text/html,x', 'chrome://settings', 'file://host/share/x'])
def test_validate_accepts_only_web_urls_and_local_files(url):
    with pytest.raises(ValueError, match='http'):
        arrange.validate({'workspaces': [{'name': 'a', 'items': [{'tabs': [['t', url]]}]}]})
    with pytest.raises(ValueError, match='http'):
        arrange.validate({'workspaces': [], 'bookmarks': {'title': 'r', 'links': [['t', url]]}})


def test_validate_accepts_a_local_file_and_checks_the_close_list():
    ok = {'workspaces': [{'name': 'a', 'items': [{'tabs': [['t', 'file:///C:/r.html']]}]}],
          'close': [{'title': 's', 'url': 'chrome://newtab/', 'why': 'ブラウザの画面'}]}
    assert arrange.validate(ok) is ok
    for close in ({'url': 'x'}, [{'title': 's'}], [{'url': 1}]):
        with pytest.raises(ValueError, match='close'):
            arrange.validate({**ok, 'close': close})


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


def test_refile_moves_only_links_a_folder_rule_hits():
    links = [{'id': '1', 'title': 'Claude の使い方', 'url': 'https://blog.example/claude', 'path': []},
             {'id': '2', 'title': 'どこにも当たらない', 'url': 'https://other.example/', 'path': []},
             {'id': '3', 'title': 'tutorial', 'url': 'https://docs.python.org/3/tutorial/', 'path': []},
             {'id': '4', 'title': 'Claude 入門', 'url': 'https://blog.example/c2', 'path': ['動画']}]
    assert arrange.refile_moves(links, ACFG) == [
        {'id': '1', 'title': 'Claude の使い方', 'url': 'https://blog.example/claude', 'from': [], 'path': ['AI']},
        {'id': '3', 'title': 'tutorial', 'url': 'https://docs.python.org/3/tutorial/', 'from': [],
         'path': ['言語', 'Python']}]


def test_build_puts_a_later_tab_in_the_folder_the_ai_chose():
    tabs = [placed(0, 'https://blog.example/claude', 'Claude の使い方', ws='大学')]
    v = {plan.page_key('https://blog.example/claude'): {'workspace': '大学', 'stack': None, 'action': 'later',
                                                    'folder': ['大学', '応数B']}}
    assert arrange.build(tabs, ACFG, v)['bookmarks']['children'][0]['title'] == '大学'


def write_config(tmp_path):
    path = tmp_path / 'config.toml'
    path.write_text('[arrange]\nreading_root = "後で"\n[[arrange.folder]]\npath = "AI"\ntitle = "claude"\n',
                    encoding='utf-8')
    return path


def test_cli_refile_dry_run_shows_the_moves_without_moving(tmp_path, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(vivaldi, 'reading_links', lambda port, root: seen.append((port, root)) or [
        {'id': '7', 'title': 'Claude Code 入門', 'url': 'https://blog.example/c', 'path': []}])
    monkeypatch.setattr(vivaldi, 'move', lambda *a: pytest.fail('dry-run で移した'))
    cli.main(['refile', '--config', str(write_config(tmp_path)), '--port', '9300', '--dry-run',
              '--state', str(tmp_path / 'state')])
    assert seen == [(9300, '後で')]
    assert '後で -> 後で/AI: Claude Code 入門' in capsys.readouterr().out


def test_cli_refile_moves_and_reports(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(vivaldi, 'reading_links', lambda port, root: [
        {'id': '7', 'title': 'Claude Code 入門', 'url': 'https://blog.example/c', 'path': []},
        {'id': '8', 'title': 'そのまま', 'url': 'https://other.example/', 'path': []}])
    calls = []
    monkeypatch.setattr(vivaldi, 'move', lambda port, root, moves: calls.append((root, moves)) or
                        {'moved': 1, 'skipped': []})
    profile = tmp_path / 'profile'
    profile.mkdir()
    (profile / 'Bookmarks').write_text('{"roots": {}}', encoding='utf-8')
    state = tmp_path / 'state'
    cli.main(['refile', '--config', str(write_config(tmp_path)), '--profile-dir', str(profile), '--state', str(state)])
    assert calls == [('後で', [{'id': '7', 'title': 'Claude Code 入門', 'url': 'https://blog.example/c', 'path': ['AI'],
                                'from': []}])]
    assert '振り分けた 1 件, 題名を直した 0 件, 消した 0 件 (触らなかった 1 件)' in capsys.readouterr().out
    # 移す前にブックマークのファイルを控える
    assert [p.read_text(encoding='utf-8') for p in (state / 'bookmarks').glob('Bookmarks-*.json')] == ['{"roots": {}}']


def test_cli_refile_records_dropped_links_and_does_not_drop_them_again(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(vivaldi, 'reading_links', lambda port, root: [])
    moves = [{'id': '7', 'title': '小テスト', 'url': 'https://letus.example/quiz', 'from': ['大学'], 'drop': True}]
    monkeypatch.setattr(cli, 'refile_plan', lambda links, config_path, state: (moves, ''))
    monkeypatch.setattr(vivaldi, 'move', lambda port, root, ms: {
        'moved': 0, 'renamed': 0, 'skipped': [], 'removed': [{'id': '7', 'title': '小テスト', 'url': 'https://letus.example/quiz'}]})
    profile = tmp_path / 'profile'
    profile.mkdir()
    (profile / 'Bookmarks').write_text('{}', encoding='utf-8')
    state = tmp_path / 'state'
    cli.main(['refile', '--config', str(write_config(tmp_path)), '--profile-dir', str(profile), '--state', str(state)])
    assert '消した 1 件' in capsys.readouterr().out
    rec = [json.loads(x) for x in (state / 'bookmarks' / 'dropped.jsonl').read_text(encoding='utf-8').splitlines()]
    assert [(r['title'], r['url'], r['path']) for r in rec] == [('小テスト', 'https://letus.example/quiz', ['後で', '大学'])]
    memo = judge.load(state / 'judge.json')['bookmarks'][judge._memo_key('https://letus.example/quiz')]
    assert memo['keep'] is True and memo['restored'] is True


def test_cli_refile_protects_and_records_drops_when_the_move_fails(tmp_path, monkeypatch):
    def boom(port, root, ms):
        raise TimeoutError('cdp')
    monkeypatch.setattr(vivaldi, 'move', boom)
    profile = tmp_path / 'profile'
    profile.mkdir()
    (profile / 'Bookmarks').write_text('{}', encoding='utf-8')
    state = tmp_path / 'state'
    moves = [{'id': '7', 'title': '小テスト', 'url': 'https://letus.example/quiz', 'from': ['大学'], 'drop': True}]
    with pytest.raises(TimeoutError):
        cli.move_bookmarks(9242, '後で', moves, profile, state)
    rec = [json.loads(x) for x in (state / 'bookmarks' / 'dropped.jsonl').read_text(encoding='utf-8').splitlines()]
    assert [(r['url'], r['unsure']) for r in rec] == [('https://letus.example/quiz', True)]
    assert judge.load(state / 'judge.json')['bookmarks'][judge._memo_key('https://letus.example/quiz')]['restored'] is True


def test_describe_shows_drops_moves_and_renames():
    assert cli.describe('後で', {'id': '1', 'title': 'x', 'from': ['大学'], 'drop': True}) == '消す (後で/大学): x'
    assert cli.describe('後で', {'id': '1', 'title': 'a.pdf', 'from': [], 'rename': '講義 3'}) == '後で: a.pdf => 題名を「講義 3」に'
    assert cli.describe('後で', {'id': '1', 'title': 'x', 'from': [], 'path': ['AI']}) == '後で -> 後で/AI: x'


def test_cli_refile_does_not_move_without_a_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(vivaldi, 'reading_links', lambda port, root: [
        {'id': '7', 'title': 'Claude Code 入門', 'url': 'https://blog.example/c', 'path': []}])
    monkeypatch.setattr(vivaldi, 'move', lambda *a: pytest.fail('控えの無いまま移した'))
    with pytest.raises(OSError):
        cli.main(['refile', '--config', str(write_config(tmp_path)), '--profile-dir', str(tmp_path / 'none'),
                  '--state', str(tmp_path / 'state')])


def test_backup_bookmarks_keeps_the_newest_generations(tmp_path):
    profile = tmp_path / 'p'
    profile.mkdir()
    (profile / 'Bookmarks').write_text('new', encoding='utf-8')
    out = tmp_path / 'b'
    out.mkdir()
    for k in range(3):
        (out / f'Bookmarks-2026010{k}-000000.json').write_text(str(k), encoding='utf-8')
    snapshot.backup_bookmarks(profile, out, keep=2)
    kept = sorted(p.read_text(encoding='utf-8') for p in out.glob('Bookmarks-*.json'))
    assert kept == ['2', 'new']


def test_move_expression_embeds_root_and_moves_as_json():
    moves = [{'id': '1', 'path': ['</script>"\u2028']}]
    expr = vivaldi.move_expression('後で', moves)
    assert expr == f'{vivaldi.MOVE}({json.dumps("後で")}, {json.dumps(moves)})' and '\u2028' not in expr


def test_cli_arrange_stacks_tabs_under_the_anchor_page_they_came_from(tmp_path):
    db = tmp_path / 'History.db'
    make_history(db, [
        visit(1, 0, 'https://lms.example.edu/', 'Home | LMS', core=1),
        visit(2, 10, 'https://lms.example.edu/course/view.php?id=1', 'コース: 線形代数 (A1) | LMS', opener=1),
        visit(3, 20, 'https://lms.example.edu/course/view.php?id=2', 'コース: 情報理論 (B2) | LMS', opener=1),
        visit(4, 30, 'https://lms.example.edu/mod/resource/view.php?id=7', '第1回 資料', opener=2),
        visit(5, 40, 'https://lms.example.edu/pluginfile.php/7/slide.pdf', 'slide.pdf', opener=4),
        visit(6, 50, 'https://lms.example.edu/mod/assign/view.php?id=8', '第1回 課題', opener=3),
        visit(7, 60, 'https://lms.example.edu/mod/quiz/view.php?id=9', '小テスト', opener=3),
    ])
    sess = tmp_path / 'Session_1'
    sess.write_bytes(snss(*(nav(t, 0, u, '') for t, u in enumerate([
        'https://lms.example.edu/course/view.php?id=1', 'https://lms.example.edu/pluginfile.php/7/slide.pdf',
        'https://lms.example.edu/mod/assign/view.php?id=8', 'https://lms.example.edu/mod/quiz/view.php?id=9'], 1))))
    cfg = tmp_path / 'c.toml'
    cfg.write_text('[[arrange.anchor]]\nmatch = ["lms.example.edu/course/view.php"]\n'
                   r"name = '^コース: (.+?) \('" '\n', encoding='utf-8')
    out = tmp_path / 'arrange.json'
    cli.main(['arrange', str(db), '--session', str(sess), '--config', str(cfg), '--out', str(out)])
    items = json.loads(out.read_text(encoding='utf-8'))['workspaces'][0]['items']
    assert [(i['stack'], [u.rsplit('/', 1)[-1] for _, u in i['tabs']]) for i in items] == [
        ('線形代数', ['view.php?id=1', 'slide.pdf']), ('情報理論', ['view.php?id=8', 'view.php?id=9'])]


def test_anchor_name_falls_back_to_the_title_without_the_site_name():
    a = arrange.ArrangeConfig.from_config({'arrange': {'anchor': [{'match': ['x.example/c/*'], 'name': '^nomatch(.)'}]}})
    assert a.anchors[0].label('https://x.example/c/1?id=3', 'Algebra notes - Example') == 'Algebra notes'
    assert a.anchors[0].label('https://x.example/d/1', 'Algebra notes - Example') is None


def test_anchor_labels_follow_the_raw_visits_keep_names_over_restored_tabs_and_skip_hidden_pages():
    anchors = (plan.Anchor(('x.example/c/*',), re.compile(r'^Course (\w+)')),)
    privacy = Privacy.from_config({'mask': {'x.example/c/secret': '伏せた'}, 'drop': ['bank.example/*']})
    visits = [Visit(1, 0, 'https://x.example/c/1?id=1', 'Course Algebra', 1, False, 0, 0, 0),
              Visit(2, 1, 'https://x.example/f.pdf#p2', 'f.pdf', 1, False, 1, 0, 0),
              Visit(3, 2, 'https://x.example/c/secret', 'Course Hidden', 1, False, 0, 0, 0),
              Visit(4, 3, 'https://x.example/g', 'g', 1, False, 3, 0, 0),
              Visit(5, 4, 'https://bank.example/a', 'Course Money', 1, False, 0, 0, 0),
              Visit(6, 5, 'https://x.example/h', 'h', 1, False, 0, 2, 0),
              Visit(7, 6, 'https://x.example/f.pdf', 'f.pdf', 1, False, 0, 0, 0)]
    assert plan.anchor_labels(visits, anchors, privacy) == {
        'https://x.example/c/1?id=1': 'Algebra', 'https://x.example/f.pdf': 'Algebra', 'https://x.example/h': 'Algebra'}


def test_a_workspace_written_twice_is_ordered_where_it_first_appears():
    a = arrange.ArrangeConfig.from_config({'workspace': [{'name': '大学'}, {'name': '開発', 'match': ['x.example/*']},
                                                         {'name': '学習'}, {'name': '開発', 'match': ['*']}]})
    assert a.order == ('大学', '開発', '学習')


def test_topics_name_stacks_from_the_title_or_the_decoded_url():
    cfg = arrange.ArrangeConfig.from_config(config._merge(CFG, {'arrange': {'topic': [
        {'pattern': r'atcoder\.jp/contests/abc(\d+)', 'name': 'ABC{0}'},
        {'pattern': '(情報工学実験)'},
        {'pattern': 'ｆｏｕｒｉｅｒ|フーリエ'}]}}))
    ps = [placed(0, 'https://atcoder.jp/contests/abc479/tasks/abc479_d', 'D - Back', goal='x'),
          placed(1, 'https://atcoder.jp/contests/ABC479/submissions/1', '提出', goal='y'),
          placed(2, 'https://example.com/%E6%83%85%E5%A0%B1%E5%B7%A5%E5%AD%A6%E5%AE%9F%E9%A8%93/a', 'a', goal='z'),
          placed(3, 'https://example.com/b', '情報工学実験2 のコード', goal='w'),
          placed(4, 'https://example.com/c', 'フーリエ変換', goal='v'),
          placed(5, 'https://example.com/d', 'ほか', goal='v')]
    assert cfg.topic_of('https://example.com/', 'Fourier') is None   # 全角の字は NFKC してから名前にするだけ
    items = arrange.build(ps, cfg)['workspaces'][0]['items']
    assert [(i['stack'], len(i['tabs'])) for i in items] == [('ABC479', 2), ('情報工学実験', 2), (None, 1), (None, 1)]
    assert {i['tabs'][0][0] for i in items if not i['stack']} == {'フーリエ変換', 'ほか'}


def test_topic_name_is_normalized_with_nfkc():
    t = arrange.Topic(re.compile(r'(ＡＢＣ\d+)'), None)
    assert t.label('https://x.example/', 'ＡＢＣ１２') == 'ABC12'
    assert arrange.Topic(re.compile('nothing'), None).label('https://x.example/', 't') is None
    assert arrange.Topic(re.compile('abc'), 'ABC{0}').label('https://x.example/', 'abc') is None   # 差し込むグループが無い
    assert len(arrange.Topic(re.compile('.+'), None).label('https://x.example/', 'あ' * 80)) == 30


def test_order_and_an_empty_unsorted_leave_unmatched_tabs_alone():
    cfg = arrange.ArrangeConfig.from_config(config._merge(CFG, {'arrange': {'order': ['読みもの', '開発'], 'unsorted': ''}}))
    ps = [placed(0, 'https://lms.example/a', '講義', ws='大学'), placed(1, 'https://github.com/a', 'repo', ws='開発'),
          placed(2, 'https://misc.example/', 'misc'), placed(3, 'https://misc.example/old', 'old', status='stash')]
    arr = arrange.build(ps, cfg)
    assert [w['name'] for w in arr['workspaces']] == ['開発', '大学']
    assert arr['bookmarks']['links'] == [['old', 'https://misc.example/old']]
    assert arr['close'] == []


def test_close_title_leaves_other_statuses_alone():
    cfg = arrange.ArrangeConfig.from_config(config._merge(CFG, {'arrange': {'close_title': '^404'}}))
    ps = [placed(0, 'https://a.example/', '404', status='dup'), placed(1, 'chrome://newtab/', '404')]
    assert [c['why'] for c in arrange.build(ps, cfg)['close']] == ['重複', 'ブラウザの画面']


def test_close_title_sends_error_pages_to_close():
    cfg = arrange.ArrangeConfig.from_config(config._merge(CFG, {'arrange': {'close_title': r'^(404|エラー)( |$|\|)'}}))
    ps = [placed(0, 'https://a.example/x', '404 | site'), placed(1, 'https://a.example/y', 'エラー | ITC', status='stash'),
          placed(2, 'https://a.example/z', '4040 の話')]
    arr = arrange.build(ps, cfg)
    assert [(c['title'], c['why']) for c in arr['close']] == [('404 | site', 'エラーのページ'), ('エラー | ITC', 'エラーのページ')]
    assert arr['workspaces'][0]['items'][0]['tabs'] == [['4040 の話', 'https://a.example/z']]


def test_build_passes_the_workspace_order_and_check_rejects_a_bad_one():
    arr = arrange.build([], arrange.ArrangeConfig(order=('大学', '競プロ')))
    assert arr['order'] == ['大学', '競プロ']
    arrange.validate(arr)
    with pytest.raises(ValueError):
        arrange.validate({**arr, 'order': [1]})
