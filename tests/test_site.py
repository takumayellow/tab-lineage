"""解説サイト (site/build.py) が型紙の印を全部埋め, 架空のサンプルから作られることを確かめる."""
import importlib.util
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _build(out):
    spec = importlib.util.spec_from_file_location('site_build', ROOT / 'site' / 'build.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.build(out)


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
