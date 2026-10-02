"""解説サイトを headless のブラウザで描画し, 手順の段の中身が本文の幅に収まることを確かめる.

段は左に番号, 右に本文の 2 列. 本文の要素が番号の列に回り込むと, 幅の無い縦長の箱になる.
"""
import importlib.util
import pathlib
import re
import subprocess

import pytest

from test_viewer_e2e import BROWSER, SANDBOX

ROOT = pathlib.Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(BROWSER is None, reason='Edge / Chrome が見つからない')

MEASURE = '''<script>addEventListener("load", () => {
  document.querySelectorAll("details").forEach(d => d.open = true);
  const w = [...document.querySelectorAll("ol.flow > li > *")].map(e => Math.round(e.getBoundingClientRect().width));
  document.body.setAttribute("data-widths", w.join(","));
})</script>'''


def test_flow_steps_keep_their_body_in_the_text_column(tmp_path):
    spec = importlib.util.spec_from_file_location('site_build', ROOT / 'site' / 'build.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.build(tmp_path / '_site')
    page = tmp_path / '_site' / 'check.html'
    page.write_text((tmp_path / '_site' / 'index.html').read_text(encoding='utf-8') + MEASURE, encoding='utf-8')
    r = subprocess.run([BROWSER, *SANDBOX, '--headless=new', '--disable-gpu', '--no-first-run',
                        f'--user-data-dir={tmp_path / "profile"}', '--window-size=1280,1000',
                        '--virtual-time-budget=3000', '--dump-dom', page.as_uri()],
                       capture_output=True, text=True, encoding='utf-8', timeout=120)
    m = re.search(r'data-widths="([^"]*)"', r.stdout)
    assert m, r.stderr[-2000:]
    widths = [int(w) for w in m.group(1).split(',')]
    assert len(widths) >= 10
    assert min(widths) > 200, widths
