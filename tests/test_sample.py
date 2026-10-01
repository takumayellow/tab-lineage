"""解説サイトに載せる架空のサンプルが, 説明どおりの案になることを確かめる."""
import importlib.util
import json
import pathlib

from tab_lineage import cli

SAMPLE = pathlib.Path(__file__).resolve().parents[1] / 'examples' / 'sample'


def _make(tmp_path):
    spec = importlib.util.spec_from_file_location('make_sample', SAMPLE / 'make_sample.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.make(tmp_path / 'sample')


def test_sample_arranges_into_workspaces_stacks_and_bookmarks(tmp_path, capsys):
    history, sess = _make(tmp_path)
    out = tmp_path / 'arrange.json'
    cli.main(['arrange', str(history), '--session', str(sess), '--config', str(SAMPLE / 'config.toml'),
              '--labels', str(SAMPLE / 'labels.toml'), '--out', str(out)])
    arr = json.loads(out.read_text(encoding='utf-8'))
    ws = {w['name']: w for w in arr['workspaces']}
    assert list(ws) == ['大学', '個人開発', '未分類']
    stacks = {i['stack']: len(i['tabs']) for w in arr['workspaces'] for i in w['items'] if i['stack']}
    assert stacks['窓関数とレポート'] == 3
    assert stacks['numpy.fft の使い方'] == 2
    assert stacks['スペクトルの描き方'] == 2
    assert ws['未分類']['items'][0]['stack'].startswith('寄り道: ')
    folders = {f['title']: f for f in arr['bookmarks']['children']}
    dev = {f['title']: len(f['links']) for f in folders['開発']['children']}
    assert dev == {'Rust': 2, 'Web': 2, 'Python': 1}
    assert [t for t, _ in arr['bookmarks']['links']] == ['ターミナルの 256 色を使いこなす']
    why = sorted(c['why'] for c in arr['close'])
    assert why == sorted(['検索結果', '検索結果', '重複', 'ブラウザの画面', 'ログイン・決済など'])


def test_sample_builds_the_viewer(tmp_path):
    history, sess = _make(tmp_path)
    out = tmp_path / 'demo' / 'index.html'
    cli.main(['build', str(history), '--session', str(sess), '--config', str(SAMPLE / 'config.toml'),
              '--labels', str(SAMPLE / 'labels.toml'), '--out', str(out)])
    page = out.read_text(encoding='utf-8')
    assert '実験レポート: 離散フーリエ変換' in page
    assert 'accounts.example.com/login' not in page
