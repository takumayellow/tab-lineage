"""解説サイト (site/build.py) が型紙の印を全部埋め, 架空のサンプルから作られることを確かめる."""
import importlib.util
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _module():
    spec = importlib.util.spec_from_file_location('site_build', ROOT / 'site' / 'build.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _build(out):
    _module().build(out)


def test_site_builds_from_the_sample(tmp_path, capsys):
    out = tmp_path / '_site'
    _build(out)
    page = (out / 'index.html').read_text(encoding='utf-8')
    assert (out / 'demo' / 'index.html').exists() and (out / '.nojekyll').exists()
    assert '{{' not in page
    assert 'タブ 27 枚' in page
    assert '寄り道: ピラミッドは どう積んだのか 3 分で' in page
    assert 'あとで読む/開発/Rust' in page
    # 目次とページ内のリンクの行き先が全部ある
    ids = set(re.findall(r'\bid="([^"]+)"', page))
    assert {h for h in re.findall(r'href="#([^"]+)"', page)} <= ids
    # 本文の既定値は default.toml から入る
    assert '直前 30 秒以内の検索の子にする' in page
    assert '訪問の 4 分の 1（<code>ws_share</code>）' in page
    assert '最後に見てから 3 日以上たったタブ' in page


def test_fill_rejects_missing_and_unused_values():
    mod = _module()
    assert mod.fill('{{A}} と {{B}}', {'A': '{{B}}', 'B': 'b'}) == '{{B}} と b'
    with pytest.raises(SystemExit, match='値が無い'):
        mod.fill('{{A}} と {{B}}', {'A': 'a'})
    with pytest.raises(SystemExit, match='使われない値'):
        mod.fill('{{A}}', {'A': 'a', 'C': 'c'})


def test_share_reads_as_a_fraction():
    mod = _module()
    assert mod._share(0.25) == '4 分の 1'
    assert mod._share(0.3) == '10 分の 3'
    assert mod._share(0.123) == '12%'
