import pathlib
import json
import subprocess

import pytest
from tab_lineage import arrange, config, judge
from tab_lineage.histdb import US, WEBKIT_TO_UNIX_S, Visit
from tab_lineage.plan import Placed
from tab_lineage.privacy import Privacy
from tab_lineage.session import Tab

CFG = config._merge(config.default(), {
    'workspace': [{'name': '大学', 'match': ['lms.example/*']}, {'name': '競プロ', 'match': ['atcoder.jp/*']}],
    'arrange': {'order': ['大学', '競プロ'], 'unsorted': '', 'reading_workspaces': ['読みもの'],
                'close_title': '^エラー'},
    'privacy': {'mask': {'mail.example/*': 'メール'}},
})
ACFG = arrange.ArrangeConfig.from_config(CFG)
PRIVACY = Privacy.from_config(CFG['privacy'])
NOW = 1_800_000_000.0
ON = judge.JudgeConfig(enabled=True)


def placed(k, url, title, status='keep', ws=None, goal='g'):
    return Placed(Tab(k, url, title), title, status, ws, goal, k)


def visit(url, days_ago):
    t = int((NOW - days_ago * 86400 + WEBKIT_TO_UNIX_S) * US)
    return Visit(0, t, url, '', 0, False, 0, 0, 0)


@pytest.fixture
def asked(monkeypatch):
    """ask の代わり. 呼ばれた items を記録し, answer で返す判定を決める."""
    calls = []

    def fake(items, known, workspaces, jcfg, cwd, folders=frozenset()):
        calls.append({'items': items, 'known': list(known), 'workspaces': workspaces})
        return [fake.answer(x) for x in items]
    fake.answer = lambda x: {'id': x['id'], 'workspace': '大学', 'stack': '応数B DFT', 'action': 'keep'}
    monkeypatch.setattr(judge, 'ask', fake)
    fake.calls = calls
    return fake


def test_disabled_does_nothing(tmp_path, asked):
    v, line = judge.run([placed(0, 'https://atcoder.jp/x', 'FFT')], ACFG, PRIVACY, [], judge.JudgeConfig(), tmp_path)
    assert (v, line, asked.calls) == ({}, '', [])
    assert not (tmp_path / 'judge.json').exists()


def test_sends_only_kept_web_tabs_that_are_not_masked_or_closed(tmp_path, asked):
    ps = [placed(0, 'https://atcoder.jp/fft', '競プロのための FFT', ws='競プロ', goal='ABC'),
          placed(1, 'https://atcoder.jp/fft', '競プロのための FFT', status='dup', ws='競プロ'),
          placed(2, 'https://mail.example/inbox', '受信箱'),
          placed(3, 'https://lms.example/err', 'エラー | LMS'),
          placed(4, 'file:///C:/a.pdf', 'a.pdf'),
          placed(5, 'https://news.example/', 'ニュース', status='stash'),
          placed(6, 'https://atcoder.jp/fft#top', '競プロのための FFT'),
          placed(7, 'https://search.example/?q=秘密&token=x', '検索')]
    v, line = judge.run(ps, ACFG, PRIVACY, [visit('https://atcoder.jp/fft', 2)], ON, tmp_path, NOW)
    [call] = asked.calls
    assert [x['title'] for x in call['items']] == ['競プロのための FFT', '検索']
    assert call['items'][1]['url'] == 'https://search.example/'
    assert call['items'][0] | {} == {'id': 0, 'title': '競プロのための FFT', 'url': 'https://atcoder.jp/fft',
                                     'rule': '競プロ', 'flow': 'ABC', 'days': 2.0}
    assert call['workspaces'] == ['大学', '競プロ', '読みもの']
    assert v['https://atcoder.jp/fft']['workspace'] == '大学'
    assert '2 枚' in line


def test_remembers_verdicts_and_does_not_ask_again(tmp_path, asked):
    ps = [placed(0, 'https://atcoder.jp/fft', 'FFT')]
    judge.run(ps, ACFG, PRIVACY, [], ON, tmp_path, NOW)
    v, line = judge.run(ps, ACFG, PRIVACY, [], ON, tmp_path, NOW + 3600)
    assert len(asked.calls) == 1 and line == '' and v['https://atcoder.jp/fft']['stack'] == '応数B DFT'


def test_asks_again_after_ttl_or_when_the_background_changes(tmp_path, asked):
    ps = [placed(0, 'https://atcoder.jp/fft', 'FFT')]
    judge.run(ps, ACFG, PRIVACY, [], ON, tmp_path, NOW)
    judge.run(ps, ACFG, PRIVACY, [], ON, tmp_path, NOW + judge.TTL + 1)
    assert len(asked.calls) == 2
    bg = tmp_path / 'bg.md'
    bg.write_text('今期の授業', encoding='utf-8')
    judge.run(ps, ACFG, PRIVACY, [], judge.JudgeConfig(True, context=(str(bg),)), tmp_path, NOW + judge.TTL + 2)
    assert len(asked.calls) == 3


def test_known_names_are_passed_so_stacks_line_up(tmp_path, asked):
    judge.run([placed(0, 'https://atcoder.jp/a', 'A')], ACFG, PRIVACY, [], ON, tmp_path, NOW)
    judge.run([placed(0, 'https://atcoder.jp/a', 'A'), placed(1, 'https://atcoder.jp/b', 'B')],
              ACFG, PRIVACY, [], ON, tmp_path, NOW + 60)
    assert asked.calls[1]['known'] == ['大学 / 応数B DFT / A']
    assert [x['title'] for x in asked.calls[1]['items']] == ['B']


def test_drops_answers_with_unknown_ids_workspaces_or_actions_and_trims_stacks(tmp_path, asked):
    answers = iter([{'id': 9, 'workspace': '大学', 'stack': None, 'action': 'keep'},
                    {'id': 1, 'workspace': '娯楽', 'stack': None, 'action': 'keep'},
                    {'id': 2, 'workspace': '大学', 'stack': None, 'action': 'archive'},
                    {'id': 3, 'workspace': '競プロ', 'stack': '  ' + 'あ' * 30, 'action': 'later'}])
    asked.answer = lambda x: next(answers)
    ps = [placed(k, f'https://atcoder.jp/{k}', str(k)) for k in range(4)]
    v, _ = judge.run(ps, ACFG, PRIVACY, [], ON, tmp_path, NOW)
    assert list(v) == ['https://atcoder.jp/3']
    assert v['https://atcoder.jp/3']['stack'] == 'あ' * judge.STACK_MAX
    assert v['https://atcoder.jp/3']['action'] == 'later'


def test_failure_keeps_rules_and_backs_off(tmp_path, monkeypatch):
    calls = []

    def boom(*a):
        calls.append(1)
        raise RuntimeError('usage limit')
    monkeypatch.setattr(judge, 'ask', boom)
    ps = [placed(0, 'https://atcoder.jp/fft', 'FFT')]
    v, line = judge.run(ps, ACFG, PRIVACY, [], ON, tmp_path, NOW)
    assert v == {} and '失敗' in line
    v, line = judge.run(ps, ACFG, PRIVACY, [], ON, tmp_path, NOW + 60)
    assert len(calls) == 1 and '呼ばない' in line
    judge.run(ps, ACFG, PRIVACY, [], ON, tmp_path, NOW + judge.BACKOFF + 1)
    assert len(calls) == 2


def test_ask_parses_structured_output_and_reports_errors(monkeypatch, tmp_path):
    out = {'is_error': False, 'structured_output': {'tabs': [{'id': 0, 'workspace': '大学', 'stack': None,
                                                              'action': 'done'}]}}
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, input=kw['input'])
        return subprocess.CompletedProcess(cmd, 0, json.dumps(out), '')
    monkeypatch.setattr(judge.subprocess, 'run', fake_run)
    monkeypatch.setattr(judge, '_exe', lambda: 'claude')
    items = [{'id': 0, 'title': 'T', 'url': 'https://a.example/', 'rule': None, 'flow': None, 'days': None}]
    assert judge.ask(items, [], ['大学'], judge.JudgeConfig(True, 'haiku'), tmp_path)[0]['action'] == 'done'
    assert seen['cmd'][seen['cmd'].index('--model') + 1] == 'haiku'
    assert seen['cmd'][seen['cmd'].index('--tools') + 1] == ''
    assert 'https://a.example/' in seen['input'] and '- 大学' in seen['input']
    out = {'is_error': True, 'result': 'session limit'}
    with pytest.raises(RuntimeError, match='session limit'):
        judge.ask(items, [], ['大学'], ON, tmp_path)
    monkeypatch.setattr(judge.subprocess, 'run', lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, 'oops', 'bad'))
    with pytest.raises(RuntimeError, match='JSON'):
        judge.ask(items, [], ['大学'], ON, tmp_path)


def test_from_config_resolves_context_relative_to_the_config(tmp_path):
    (tmp_path / 'judge.md').write_text('背景', encoding='utf-8')
    j = judge.JudgeConfig.from_config({'judge': {'enabled': True, 'context': ['judge.md', 'missing.md']}}, tmp_path)
    assert j.enabled and j.model == 'haiku'
    assert j.background() == f'## {tmp_path / "judge.md"}\n背景'


def test_build_uses_verdicts_over_rules():
    ps = [placed(0, 'https://atcoder.jp/fft', 'FFT', ws='競プロ', goal='ABC'),
          placed(1, 'https://lms.example/dft', 'DFT', ws='大学', goal='応数B'),
          placed(2, 'https://atcoder.jp/old', '古い', ws='競プロ', goal='ABC'),
          placed(3, 'https://atcoder.jp/blog', '記事', ws='競プロ', goal='ABC'),
          placed(4, 'https://atcoder.jp/one', '1 枚', ws='競プロ', goal='ABC'),
          placed(5, 'https://atcoder.jp/rule', '規則のまま', ws='競プロ', goal='ABC')]
    vs = {'https://atcoder.jp/fft': {'workspace': '大学', 'stack': '応数B DFT', 'action': 'keep'},
          'https://lms.example/dft': {'workspace': '大学', 'stack': '応数B DFT', 'action': 'keep'},
          'https://atcoder.jp/old': {'workspace': '競プロ', 'stack': None, 'action': 'done'},
          'https://atcoder.jp/blog': {'workspace': '競プロ', 'stack': None, 'action': 'later'},
          'https://atcoder.jp/one': {'workspace': '競プロ', 'stack': None, 'action': 'keep'}}
    arr = arrange.build(ps, ACFG, vs)
    ws = {w['name']: w['items'] for w in arr['workspaces']}
    assert ws['大学'] == [{'stack': '応数B DFT', 'tabs': [['FFT', 'https://atcoder.jp/fft'],
                                                          ['DFT', 'https://lms.example/dft']]}]
    # AI がまとめないとした 1 枚は, 規則で同じ流れのタブがあっても別に置く
    assert sorted(len(i['tabs']) for i in ws['競プロ']) == [1, 1] and all(i['stack'] is None for i in ws['競プロ'])
    assert [c['url'] for c in arr['close']] == ['https://atcoder.jp/old']
    assert arr['bookmarks']['links'] == [['記事', 'https://atcoder.jp/blog']]
    arrange.validate(arr)


def test_dropped_pages_are_not_sent(tmp_path, asked):
    cfg = config._merge(CFG, {'privacy': {'drop': ['github.com/settings/*']}})
    ps = [placed(0, 'https://github.com/settings/tokens', 'Tokens'), placed(1, 'https://atcoder.jp/a', 'A')]
    judge.run(ps, ACFG, Privacy.from_config(cfg['privacy']), [], ON, tmp_path, NOW)
    assert [x['title'] for x in asked.calls[0]['items']] == ['A']


def test_broken_cache_entries_are_ignored(tmp_path):
    data = {'basis': 'k', 'tabs': {'a': {'workspace': '大学', 'action': 'keep', 'at': NOW},
                                   'b': {'action': 'keep', 'at': NOW}, 'c': {'workspace': '大学', 'action': 'x', 'at': NOW},
                                   'd': 'oops', 'e': {'workspace': '大学', 'action': 'done'}}}
    assert list(judge.verdicts(data, 'k', NOW)) == ['a']


def test_cli_judged_falls_back_to_rules_on_any_error(tmp_path, monkeypatch):
    from tab_lineage import cli

    def boom(*a, **kw):
        raise UnicodeDecodeError('utf-8', b'', 0, 1, 'bad')
    monkeypatch.setattr(judge, 'run', boom)
    r = type('R', (), {'cfg': {'judge': {'enabled': True}}, 'privacy': PRIVACY, 'visits': []})()
    v, line = cli.judged(r, [], ACFG, None, tmp_path / 's')
    assert v == {} and 'UnicodeDecodeError' in line
    r.cfg = {}
    assert cli.judged(r, [], ACFG, None, tmp_path / 'off') == ({}, '')
    assert not (tmp_path / 'off').exists()


def test_context_only_takes_documents(tmp_path):
    for n in ('a.md', 'b.txt', '.env', 'k.pem'):
        (tmp_path / n).write_text('x', encoding='utf-8')
    j = judge.JudgeConfig.from_config({'judge': {'context': ['*', '.*']}}, tmp_path)
    assert sorted(pathlib.Path(p).name for p in j.context) == ['a.md', 'b.txt']


FOLDERS = {('大学',), ('学習',), ('学習', '数学')}


def test_fit_folder_takes_known_folders_and_one_new_level_below_them():
    assert judge.fit_folder('大学', FOLDERS) == ('大学',)
    assert judge.fit_folder(' 大学 / 応数B ', FOLDERS) == ('大学', '応数B')
    assert judge.fit_folder('学習/数学/複素解析', FOLDERS) == ('学習', '数学', '複素解析')
    assert judge.fit_folder('大学/応数B/第8回', FOLDERS) is None          # 2 段の新しいフォルダ
    assert judge.fit_folder('娯楽', FOLDERS) is None                      # 一覧に無い 1 段目
    assert judge.fit_folder('大学/' + 'あ' * (judge.FOLDER_MAX + 1), FOLDERS) is None
    assert judge.fit_folder('大学/応数B/DFT', FOLDERS | {('大学', '応数B')}, FOLDERS) is None   # AI が作ったフォルダの下
    assert judge.fit_folder('大学/応数B', FOLDERS | {('大学', '応数B')}, FOLDERS) == ('大学', '応数B')
    assert judge._bare('https://u:p@docs.example/document/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/edit?x=1')         == 'https://docs.example/document/d/…/edit'
    assert judge.fit_folder(None, FOLDERS) is None and judge.fit_folder('', FOLDERS) is None


def test_a_later_tab_keeps_the_folder_the_ai_chose_and_remembers_new_folders(tmp_path, asked):
    acfg = arrange.ArrangeConfig.from_config(config._merge(CFG, {'arrange': {'folder': [{'path': '大学', 'title': 'x'}]}}))
    asked.answer = lambda x: {'id': x['id'], 'workspace': '大学', 'stack': None, 'action': 'later', 'folder': '大学/応数B'}
    v, _ = judge.run([placed(0, 'https://atcoder.jp/fft', 'FFT')], acfg, PRIVACY, [], ON, tmp_path, NOW)
    assert v['https://atcoder.jp/fft']['folder'] == ['大学', '応数B']
    assert json.loads((tmp_path / 'judge.json').read_text(encoding='utf-8'))['folders'] == [['大学', '応数B']]
    assert judge.known_folders(acfg, judge.load(tmp_path / 'judge.json')) == {('大学',), ('大学', '応数B')}


@pytest.fixture
def called(monkeypatch):
    """call の代わり (ブックマークの振り分け). 送ったプロンプトを記録し, answer で答える."""
    calls = []

    def fake(prompt, out_schema, jcfg, cwd):
        items = [json.loads(line) for line in prompt.split('# 振り分けるリンク')[1].splitlines()[1:] if line.startswith('{')]
        calls.append(items)
        return {'links': [fake.answer(x) for x in items]}
    fake.answer = lambda x: {'id': x['id'], 'folder': '大学/応数B'}
    monkeypatch.setattr(judge, 'call', fake)
    fake.calls = calls
    return fake


BM_ACFG = arrange.ArrangeConfig.from_config(config._merge(CFG, {'arrange': {'folder': [{'path': '大学', 'title': 'x'}]}}))
SPLIT = judge.JudgeConfig(enabled=True, split=('大学',))


def link(i, url, path=(), title='t'):
    return {'id': str(i), 'title': title, 'url': url, 'path': list(path)}


def test_refile_asks_only_for_top_links_and_split_folders_and_moves_them(tmp_path, called):
    links = [link(1, 'https://a.example/dft'), link(2, 'https://b.example/x', ['大学']),
             link(3, 'https://c.example/y', ['動画']), link(4, 'https://d.example/z', ['大学', '応数B']),
             link(5, 'https://mail.example/inbox'), link(6, 'file:///C:/a.pdf')]
    moves, line = judge.refile(links, BM_ACFG, PRIVACY, SPLIT, tmp_path, NOW)
    assert [x['url'] for x in called.calls[0]] == ['https://a.example/dft', 'https://b.example/x']
    assert moves == [{'id': '1', 'title': 't', 'from': [], 'path': ['大学', '応数B']},
                     {'id': '2', 'title': 't', 'from': ['大学'], 'path': ['大学', '応数B']}]
    assert '2 件' in line
    # 覚えた判定で, 次は聞かずに同じ移す先を返す. 移した後 (path が移す先) なら移さない
    again, _ = judge.refile([link(1, 'https://a.example/dft', ['大学', '応数B'])], BM_ACFG, PRIVACY, SPLIT, tmp_path, NOW)
    assert again == [] and len(called.calls) == 1


def test_refile_ignores_folders_outside_the_list_and_keeps_null_at_the_top(tmp_path, called):
    called.answer = lambda x: {'id': x['id'], 'folder': None if x['url'].endswith('keep') else '娯楽/漫画'}
    moves, _ = judge.refile([link(1, 'https://a.example/keep'), link(2, 'https://a.example/bad', ['大学'])],
                            BM_ACFG, PRIVACY, SPLIT, tmp_path, NOW)
    assert moves == []
    assert judge.load(tmp_path / 'judge.json')['bookmarks'] == {judge._memo_key('https://a.example/keep'): {'folder': [], 'at': NOW}}


def test_refile_backs_off_after_a_failure_and_keeps_the_tab_verdicts(tmp_path, monkeypatch):
    judge.save(tmp_path / 'judge.json', {'basis': 'k', 'tabs': {'u': {}}})
    monkeypatch.setattr(judge, 'call', lambda *a: (_ for _ in ()).throw(RuntimeError('上限')))
    moves, line = judge.refile([link(1, 'https://a.example/')], BM_ACFG, PRIVACY, SPLIT, tmp_path, NOW)
    assert moves == [] and '失敗' in line
    data = judge.load(tmp_path / 'judge.json')
    assert data['tabs'] == {'u': {}} and data['bm_failed'] == NOW
    monkeypatch.setattr(judge, 'call', lambda *a: pytest.fail('BACKOFF の間に呼んだ'))
    _, line = judge.refile([link(1, 'https://a.example/')], BM_ACFG, PRIVACY, SPLIT, tmp_path, NOW + 60)
    assert '呼ばない' in line


def test_refile_disabled_does_nothing(tmp_path, called):
    assert judge.refile([link(1, 'https://a.example/')], BM_ACFG, PRIVACY, judge.JudgeConfig(), tmp_path) == ([], '')
    assert called.calls == [] and not (tmp_path / 'judge.json').exists()
