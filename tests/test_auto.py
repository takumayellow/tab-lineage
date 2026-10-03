import json

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

    def apply(arr, port, close, quiet):
        calls.append(('apply', port, close, quiet))
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
    assert auto.check(tmp_path, 9242, 30, force=True) == (TABS, auto.fingerprint(TABS))


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
    monkeypatch.setattr(vivaldi, 'apply', lambda arr, port, close, quiet: dict(
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
