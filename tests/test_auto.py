import json
import sys
import types

import pytest
from _util import make_history, visit
from tab_lineage import auto, cli, vivaldi

TABS = [{'id': 1, 'window': 1, 'index': 1, 'pinned': False, 'active': True, 'title': 'pandas merge',
         'url': 'https://docs.example/merge', 'vivExtData': '{"workspaceId": 5}'},
        {'id': 2, 'window': 1, 'index': 0, 'pinned': True, 'active': False, 'title': 'mail',
         'url': 'https://mail.example/', 'vivExtData': ''},
        {'id': 3, 'window': 1, 'index': 2, 'pinned': False, 'active': False, 'title': 'x',
         'url': 'https://www.bing.com/search?q=x', 'vivExtData': 'not json'}]

RESULT = {'tabs': 1, 'made': 0, 'adopted': 1, 'already': 0, 'kept': 1, 'stacks': 0, 'failed': [], 'created': [],
          'bookmarks': {'added': 0, 'skipped': 0}, 'closed': 1, 'hibernated': 0, 'switched': False, 'left': []}


@pytest.fixture
def profile(tmp_path):
    p = tmp_path / 'profile'
    p.mkdir()
    make_history(p / 'History', [visit(1, 0, 'https://docs.example/merge', 'pandas merge', core=1)])
    return p


@pytest.fixture
def live(monkeypatch):
    """起動中の Vivaldi の代わり. apply に渡されたものを記録する."""
    calls = []
    monkeypatch.setattr(auto, 'idle_seconds', lambda: 3600)
    monkeypatch.setattr(vivaldi, 'tabs', lambda port: calls.append(('tabs', port)) or TABS)

    def apply(arr, port, close, quiet, recent_ms=0):
        calls.append(('apply', port, close, quiet))
        calls.append(('recent', recent_ms))
        return RESULT
    monkeypatch.setattr(vivaldi, 'apply', apply)
    return calls


def test_fingerprint_ignores_order_pinned_tabs_and_active_state():
    fp = auto.fingerprint(TABS)
    moved = [dict(t, index=9 - t['index'], active=not t['active']) for t in reversed(TABS)]
    assert auto.fingerprint(moved) == fp
    assert auto.fingerprint([t for t in TABS if not t['pinned']]) == fp
    assert auto.fingerprint(TABS + [{'url': 'https://new.example/'}]) != fp


def test_to_tabs_sorts_by_position_and_tolerates_broken_ext_data():
    tabs = auto.to_tabs(TABS)
    assert [t.tab for t in tabs] == [2, 1, 3]
    assert tabs[1].ext == {'workspaceId': 5}
    assert tabs[0].ext == {} and tabs[2].ext == {}


def test_check_skips_while_the_user_is_active(tmp_path, monkeypatch):
    monkeypatch.setattr(auto, 'idle_seconds', lambda: 120)
    with pytest.raises(auto.Skip, match='操作されている'):
        auto.check(tmp_path, 9242, 30, force=False)


def test_check_skips_when_idle_time_is_unknown_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(auto, 'idle_seconds', lambda: None)
    monkeypatch.setattr(auto.os, 'name', 'nt')
    with pytest.raises(auto.Skip, match='取れない'):
        auto.check(tmp_path, 9242, 30, force=False)


def test_check_skips_when_the_port_is_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(auto, 'idle_seconds', lambda: 3600)

    def closed(port):
        raise SystemExit('ポート 9242 に繋がらない')
    monkeypatch.setattr(vivaldi, 'tabs', closed)
    with pytest.raises(auto.Skip, match='9242'):
        auto.check(tmp_path, 9242, 30, force=False)


def test_check_skips_when_nothing_changed_unless_forced(tmp_path, live):
    auto.save_state(tmp_path, {'fingerprint': auto.fingerprint(TABS)})
    with pytest.raises(auto.Skip, match='変わっていない'):
        auto.check(tmp_path, 9242, 30, force=False)
    assert auto.check(tmp_path, 9242, 30, force=True) == (TABS, auto.fingerprint(TABS), False)


def test_load_state_tolerates_a_missing_or_broken_file(tmp_path):
    assert auto.load_state(tmp_path) == {}
    (tmp_path / 'state.json').write_text('{', encoding='utf-8')
    assert auto.load_state(tmp_path) == {}


def test_prune_keeps_the_newest_runs(tmp_path):
    auto.prune(tmp_path)   # runs がまだ無くても落ちない
    for name in ['20261001-000000', '20261002-000000', '20261003-000000']:
        (tmp_path / 'runs' / name).mkdir(parents=True)
    auto.prune(tmp_path, keep=2)
    assert sorted(p.name for p in (tmp_path / 'runs').iterdir()) == ['20261002-000000', '20261003-000000']


def test_dry_run_writes_the_plan_and_backup_but_applies_nothing(tmp_path, profile, live):
    state = tmp_path / 'state'
    state.mkdir()
    lines = auto.run(state, 9242, profile, dry_run=True)
    assert not any(c[0] == 'apply' for c in live)
    [run] = (state / 'runs').iterdir()
    assert sorted(p.name for p in run.iterdir()) == ['before.json', 'plan.json']   # History のコピーは消す
    arr = json.loads((run / 'plan.json').read_text(encoding='utf-8'))
    assert 'https://docs.example/merge' in json.dumps(arr) and arr['close'][0]['why'] == '検索結果'
    assert json.loads((run / 'before.json').read_text(encoding='utf-8')) == TABS
    assert 'pandas merge' in lines[0]
    assert auto.load_state(state) == {}   # 適用していないので次の回もやり直す


def test_run_applies_quietly_and_remembers_the_tabs(tmp_path, profile, live):
    state = tmp_path / 'state'
    state.mkdir()
    lines = auto.run(state, 9242, profile, close=True)
    assert ('apply', 9242, True, True) in live
    assert any('閉じた 1 枚' in line for line in lines)
    saved = auto.load_state(state)
    assert saved['fingerprint'] == auto.fingerprint(TABS) and saved['run']
    assert saved['urls'] == auto.url_marks(TABS) and len(saved['urls']) == 2   # 固定したタブは数えない
    assert not any('docs.example' in u for u in saved['urls'])   # URL そのものは残さない
    assert ('recent', 0) in live   # 操作していないので, 最近見たタブも整理する
    with pytest.raises(auto.Skip, match='変わっていない'):
        auto.run(state, 9242, profile)


def test_cli_auto_logs_skips_and_applies(tmp_path, profile, live, monkeypatch):
    state = tmp_path / 'state'
    argv = ['auto', '--profile-dir', str(profile), '--state', str(state), '--port', '9242']
    monkeypatch.setattr(auto, 'idle_seconds', lambda: 60)
    cli.main(argv)
    assert 'skip: 操作されている' in (state / 'auto.log').read_text(encoding='utf-8')
    cli.main(argv + ['--now'])
    log = (state / 'auto.log').read_text(encoding='utf-8')
    assert 'applied\n' in log and '閉じた 1 枚' in log
    assert ('apply', 9242, False, True) in live


def test_cli_auto_logs_failures_and_exits_nonzero(tmp_path, live):
    state = tmp_path / 'state'
    with pytest.raises(SystemExit) as e:
        cli.main(['auto', '--profile-dir', str(tmp_path / 'none'), '--state', str(state), '--now'])
    assert e.value.code == 1
    assert 'failed: FileNotFoundError' in (state / 'auto.log').read_text(encoding='utf-8')


def test_log_keeps_one_old_generation(tmp_path, monkeypatch):
    monkeypatch.setattr(auto, 'LOG_MAX', 10)
    auto.log(tmp_path, 'first line')
    auto.log(tmp_path, 'second line')
    assert 'first' in (tmp_path / 'auto.log.1').read_text(encoding='utf-8')
    assert 'second' in (tmp_path / 'auto.log').read_text(encoding='utf-8')


def test_expression_passes_quiet():
    assert '"quiet": false' in vivaldi.expression({'workspaces': []})
    assert '"quiet": true' in vivaldi.expression({'workspaces': []}, quiet=True)


def test_apply_report_matches_what_apply_prints():
    lines = cli.apply_report(dict(RESULT, left=[['t', 'https://a.example/']]), close=False)
    assert lines[0].startswith('タブ 1 枚') and '残したタブ 1 枚 (閉じるなら --close):' in lines
    assert lines[-1] == '  t  https://a.example/'


def test_run_remembers_the_tabs_after_apply_not_before(tmp_path, profile, live, monkeypatch):
    state = tmp_path / 'state'
    state.mkdir()
    after = [t for t in TABS if 'bing.com' not in t['url']]   # 閉じる案のタブが閉じた後
    seen = iter([TABS, after])
    monkeypatch.setattr(vivaldi, 'tabs', lambda port: next(seen))
    auto.run(state, 9242, profile, close=True)
    assert auto.load_state(state)['fingerprint'] == auto.fingerprint(after) != auto.fingerprint(TABS)


def test_run_does_not_remember_an_apply_that_placed_nothing(tmp_path, profile, live, monkeypatch):
    state = tmp_path / 'state'
    state.mkdir()
    monkeypatch.setattr(vivaldi, 'apply', lambda arr, port, close, quiet, recent_ms=0: dict(
        RESULT, adopted=0, failed=['pandas merge: 開けない']))
    with pytest.raises(RuntimeError, match='1 枚も置けなかった'):
        auto.run(state, 9242, profile)
    assert auto.load_state(state) == {}


def test_run_refiles_the_reading_folder(tmp_path, profile, live, monkeypatch):
    state = tmp_path / 'state'
    state.mkdir()
    moves = [{'id': '7', 'folder': 'AI'}]
    monkeypatch.setattr(vivaldi, 'reading_links', lambda port, root: [{'id': '7', 'title': 'c', 'url': 'https://c.example/'}])
    monkeypatch.setattr(auto.arrange, 'refile_moves', lambda links, acfg: moves)
    monkeypatch.setattr(vivaldi, 'move', lambda port, root, ms: live.append(('move', ms)) or {'moved': len(ms)})
    lines = auto.run(state, 9242, profile, refile=True)
    assert ('move', moves) in live and 'あとで読むを振り分けた 1 件' in lines


def test_cli_apply_still_warns_when_it_could_not_switch(tmp_path, monkeypatch, capsys):
    plan = tmp_path / 'plan.json'
    plan.write_text(json.dumps({'workspaces': []}), encoding='utf-8')
    monkeypatch.setattr(vivaldi, 'tabs', lambda port: [])
    monkeypatch.setattr(vivaldi, 'apply', lambda arr, port, hibernate, close: RESULT)
    cli.main(['apply', str(plan)])
    assert 'ワークスペースを切り替える関数が見つからなかった' in capsys.readouterr().out


def many(n):
    return [{'id': 100 + i, 'window': 1, 'index': 10 + i, 'pinned': False, 'active': False, 'title': f'page {i}',
             'url': f'https://site.example/{i}', 'vivExtData': ''} for i in range(n)]


def busy_state(tmp_path, tabs, minutes_ago):
    import datetime as dt
    at = dt.datetime.now() - dt.timedelta(minutes=minutes_ago)
    auto.save_state(tmp_path, {'fingerprint': auto.fingerprint(tabs), 'urls': auto.url_marks(tabs),
                               'applied': at.isoformat(timespec='seconds')})


def test_new_tabs_counts_urls_not_seen_at_the_last_apply():
    saved = {'urls': auto.url_marks(TABS)}
    assert auto.new_tabs(TABS, saved) == 0
    assert auto.new_tabs(TABS + many(3), saved) == 3
    assert auto.new_tabs(TABS, {}) == 2   # 覚えていなければ, 固定していないタブを全部数える


def test_minutes_since_tolerates_a_missing_or_broken_time():
    assert auto.minutes_since({}) is None and auto.minutes_since({'applied': 'x'}) is None
    assert auto.minutes_since({'applied': '2026-01-01T00:00:00'}) > 0


def test_check_while_active_needs_enough_new_tabs(tmp_path, monkeypatch):
    monkeypatch.setattr(auto, 'idle_seconds', lambda: 120)
    busy_state(tmp_path, TABS, minutes_ago=90)
    monkeypatch.setattr(vivaldi, 'tabs', lambda port: TABS + many(19))
    with pytest.raises(auto.Skip, match=r'新しいタブ 19 枚 \(20 枚から'):
        auto.check(tmp_path, 9242, 30, False, busy_tabs=20)
    monkeypatch.setattr(vivaldi, 'tabs', lambda port: TABS + many(20))
    tabs, fp, busy = auto.check(tmp_path, 9242, 30, False, busy_tabs=20)
    assert busy and len(tabs) == 23


def test_check_while_active_waits_for_the_interval(tmp_path, monkeypatch):
    monkeypatch.setattr(auto, 'idle_seconds', lambda: 120)
    busy_state(tmp_path, TABS, minutes_ago=10)
    monkeypatch.setattr(vivaldi, 'tabs', lambda port: TABS + many(30))
    with pytest.raises(auto.Skip, match=r'前の整理から 10 分 \(60 分あける'):
        auto.check(tmp_path, 9242, 30, False, busy_tabs=20, busy_interval=60)


def test_check_while_active_is_off_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(auto, 'idle_seconds', lambda: 120)
    monkeypatch.setattr(vivaldi, 'tabs', lambda port: pytest.fail('操作中はタブを読まない'))
    with pytest.raises(auto.Skip, match=r'^操作されている \(無操作 2 分\)$'):
        auto.check(tmp_path, 9242, 30, False)


def test_check_when_idle_ignores_the_busy_limits(tmp_path, live, monkeypatch):
    busy_state(tmp_path, TABS, minutes_ago=1)
    monkeypatch.setattr(vivaldi, 'tabs', lambda port: TABS + many(1))
    assert auto.check(tmp_path, 9242, 30, False, busy_tabs=20)[2] is False


def test_run_while_active_spares_recently_viewed_tabs(tmp_path, profile, live, monkeypatch):
    state = tmp_path / 'state'
    state.mkdir()
    monkeypatch.setattr(auto, 'idle_seconds', lambda: 120)
    lines = auto.run(state, 9242, profile, idle_min=30, busy_tabs=2)
    assert ('recent', 30 * 60_000) in live
    assert lines[0] == '操作中なので, 30 分以内に見たタブには触らない'


def test_cli_auto_passes_the_busy_options(tmp_path, profile, live, monkeypatch):
    state = tmp_path / 'state'
    monkeypatch.setattr(auto, 'idle_seconds', lambda: 60)
    cli.main(['auto', '--profile-dir', str(profile), '--state', str(state), '--busy-tabs', '2', '--busy-interval', '5'])
    assert 'applied\n' in (state / 'auto.log').read_text(encoding='utf-8')
    assert ('recent', 30 * 60_000) in live


def test_apply_report_says_what_it_left_alone():
    lines = cli.apply_report(dict(RESULT, guarded=3, dirty=1, unknown=40), close=True)
    assert '触らなかったタブ 3 枚 (入力しかけ 1 ページ・最近見たタブ), 中を調べられず閉じも休止もしなかったページ 40 枚' in lines
    assert not any('触らなかった' in line for line in cli.apply_report(RESULT, close=True))   # 前の版の結果でも落ちない


def test_expression_passes_the_tabs_to_leave_alone():
    expr = vivaldi.expression({'workspaces': []}, keep=['https://a.example/'], unsure=['https://b.example/'],
                              recent_ms=1800000)
    assert '"keep": ["https://a.example/"]' in expr and '"unsure": ["https://b.example/"]' in expr
    assert '"recentMs": 1800000' in expr


def test_form_state_sorts_pages_into_dirty_and_unknown(monkeypatch):
    def page(n, url, host='127.0.0.1', kind='page', **kw):
        return {'id': str(n), 'type': kind, 'url': url, 'webSocketDebuggerUrl': f'ws://{host}:9242/devtools/page/{n}', **kw}
    no_ws = page(8, 'https://devtools-open.example/')
    del no_ws['webSocketDebuggerUrl']   # DevTools を開いているページには接続先が無い
    no_id = page(13, 'https://no-id.example/')
    del no_id['id']
    pages = [page(1, 'https://form.example/'), page(2, 'https://plain.example/'), page(3, 'https://frozen.example/'),
             page(4, 'https://far.example/', host='evil.example'), page(5, 'chrome-extension://x/main.html'),
             page(6, 'https://worker.example/', kind='service_worker'), page(7, 'https://host.example/'), no_ws,
             page(9, 'https://pay.example/frame', kind='iframe', parentId='10'),
             page(10, 'https://shop.example/frame', kind='iframe', parentId='7'),
             page(11, 'https://ad.example/', kind='iframe', parentId='2'),
             page(12, 'https://loop.example/', kind='iframe', parentId='12'), no_id]
    monkeypatch.setattr(vivaldi, '_pages', lambda port: pages)
    answers = {'1': True, '2': False, '3': None, '7': False, '9': True, '10': False, '11': False}
    probed = []
    monkeypatch.setattr(vivaldi, '_probe', lambda ws: probed.append(ws) or answers[ws.rsplit('/', 1)[1]])
    assert vivaldi.form_state(9242) == {
        'dirty': ['https://form.example/', 'https://host.example/'],   # iframe の中の iframe の入力は, 入っているページのもの
        'unknown': ['https://devtools-open.example/', 'https://far.example/', 'https://frozen.example/',
                    'https://no-id.example/']}
    # 手元の外の接続先・ページでないもの・どのページにも入っていない iframe は調べない
    assert sorted(int(ws.rsplit('/', 1)[1]) for ws in probed) == [1, 2, 3, 7, 9, 10, 11]


class FakeSocket:
    """DevTools の接続の作り物. frames は子フレームの ID, answers は文脈 (None が本体) ごとの DIRTY の答え."""

    def __init__(self, frames=(), answers=None, fail=None, gone=()):
        self.frames, self.answers, self.fail, self.gone, self.out, self.sent = frames, answers or {}, fail, gone, [], []

    def send(self, raw):
        msg = json.loads(raw)
        self.sent.append(msg)
        method, params = msg['method'], msg['params']
        if method == self.fail:
            raise TimeoutError()
        if method == 'Page.getFrameTree':
            result = {'frameTree': {'frame': {'id': 'top'}, 'childFrames': [
                {'frame': {'id': f}, 'childFrames': [{'frame': {'id': f + '/inner'}}]} for f in self.frames]}}
        elif method == 'Page.createIsolatedWorld' and params['frameId'] in self.gone:
            self.out.append(json.dumps({'id': msg['id'], 'error': {'message': 'No frame for given id found'}}))
            return
        elif method == 'Page.createIsolatedWorld':
            result = {'executionContextId': params['frameId']}
        else:
            got = self.answers.get(params.get('contextId'), False)
            result = {'exceptionDetails': {}} if got == 'throw' else {'result': {'value': got}}
        self.out += [json.dumps({'method': 'Runtime.consoleAPICalled'}), json.dumps({'id': msg['id'], 'result': result})]

    def recv(self):
        return self.out.pop(0)

    def close(self):
        pass


def probe_with(monkeypatch, sock):
    monkeypatch.setitem(sys.modules, 'websocket', types.SimpleNamespace(create_connection=lambda *a, **k: sock))
    return vivaldi._probe('ws://127.0.0.1:1/x')


def test_probe_checks_every_frame_in_the_page_process(monkeypatch):
    assert probe_with(monkeypatch, FakeSocket(frames=['a', 'b'])) is False
    sock = FakeSocket(frames=['a', 'b'], answers={'b/inner': True})
    assert probe_with(monkeypatch, sock) is True   # 中から読めない iframe の中の iframe の入力も見つける
    worlds = [m['params']['frameId'] for m in sock.sent if m['method'] == 'Page.createIsolatedWorld']
    assert worlds == ['a', 'a/inner', 'b', 'b/inner']
    assert probe_with(monkeypatch, FakeSocket(answers={None: True})) is True
    # 別のプロセスのフレームと消えたフレームは飛ばし, 残りで決める
    assert probe_with(monkeypatch, FakeSocket(frames=['a', 'b'], gone={'a'})) is False
    assert probe_with(monkeypatch, FakeSocket(frames=['a', 'b'], gone={'a'}, answers={'b/inner': True})) is True


def test_probe_treats_a_failure_or_odd_answer_as_unknown(monkeypatch):
    def refused(*a, **k):
        raise ConnectionRefusedError()
    monkeypatch.setitem(sys.modules, 'websocket', types.SimpleNamespace(create_connection=refused))
    assert vivaldi._probe('ws://127.0.0.1:1/x') is None
    assert probe_with(monkeypatch, FakeSocket(fail='Page.getFrameTree')) is None   # 凍結して応答しない
    assert probe_with(monkeypatch, FakeSocket(frames=['a'], fail='Page.createIsolatedWorld')) is None
    assert probe_with(monkeypatch, FakeSocket(frames=['a'], answers={'a': None})) is None
    assert probe_with(monkeypatch, FakeSocket(frames=['a'], answers={'a': 'yes'})) is None
    assert probe_with(monkeypatch, FakeSocket(frames=['a'], answers={'a': 'throw'})) is None
    assert probe_with(monkeypatch, FakeSocket(frames=['a'], answers={'a': None, 'a/inner': True})) is True


def test_apply_leaves_dirty_tabs_alone_and_reports_the_counts(monkeypatch):
    monkeypatch.setattr(vivaldi, 'form_state',
                        lambda port: {'dirty': ['https://a.example/'], 'unknown': ['https://b.example/']})
    monkeypatch.setattr(vivaldi, 'ui_page', lambda port: 'ws://127.0.0.1:9242/ui')
    sent = []
    monkeypatch.setattr(vivaldi, 'evaluate', lambda ws, expr: sent.append(expr) or dict(RESULT, guarded=1))
    res = vivaldi.apply({'workspaces': []}, port=9242, quiet=True, recent_ms=5)
    assert res['dirty'] == 1 and res['unknown'] == 1 and res['guarded'] == 1
    assert '"keep": ["https://a.example/"]' in sent[0] and '"recentMs": 5' in sent[0]


def test_check_while_active_goes_ahead_when_it_never_applied(tmp_path, monkeypatch):
    monkeypatch.setattr(auto, 'idle_seconds', lambda: 120)
    monkeypatch.setattr(vivaldi, 'tabs', lambda port: TABS + many(20))
    assert auto.check(tmp_path, 9242, 30, False, busy_tabs=20)[2] is True


def test_run_remembers_the_tabs_before_apply_when_it_cannot_read_them_after(tmp_path, profile, live, monkeypatch):
    state = tmp_path / 'state'
    state.mkdir()
    seen = iter([TABS])

    def tabs(port):
        try:
            return next(seen)
        except StopIteration:
            raise SystemExit('ポート 9242 に繋がらない')
    monkeypatch.setattr(vivaldi, 'tabs', tabs)
    lines = auto.run(state, 9242, profile)
    assert any('適用の後のタブを読めなかった' in line for line in lines)
    assert auto.load_state(state)['urls'] == auto.url_marks(TABS)
