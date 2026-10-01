"""生成したサイトを headless のブラウザで描画し, 画面ごとに出る文字を確かめる.

Edge か Chrome が無い環境では飛ばす. TAB_LINEAGE_BROWSER でブラウザの実行ファイルを指定できる.
"""
import html
import os
import re
import shutil
import struct
import subprocess

import pytest

from _util import make_history, nav, record, snss, visit
from tab_lineage import cli

CANDIDATES = [
    os.environ.get('TAB_LINEAGE_BROWSER', ''),
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    *(shutil.which(n) or '' for n in ('msedge', 'microsoft-edge', 'google-chrome', 'chromium', 'chromium-browser')),
]
BROWSER = next((p for p in CANDIDATES if p and os.path.isfile(p)), None)
pytestmark = pytest.mark.skipif(BROWSER is None, reason='Edge / Chrome が見つからない')

VISITS = [
    visit(1, 0, 'https://www.bing.com/search?q=pandas', 'pandas - Search', core=1, term='pandas'),
    visit(2, 10, 'https://docs.example/merge', 'pandas merge <b>bold</b>', opener=1),
    visit(3, 20, 'https://mail.google.com/mail/u/0/#inbox/1', 'private subject', core=1),
]


@pytest.fixture(scope='module')
def page(tmp_path_factory):
    d = tmp_path_factory.mktemp('site')
    db = d / 'History.db'
    make_history(db, VISITS)
    sess = d / 'Session_1'
    sess.write_bytes(snss(nav(1, 0, 'https://docs.example/merge', 'pandas merge'),
                          record(0, struct.pack('<ii', 1, 1))))
    out = d / 'index.html'
    cli.main(['build', str(db), '--session', str(sess), '--out', str(out)])
    return out, d


def render(page, route: str) -> str:
    """route を開いたあとの <main> の中身."""
    out, d = page
    r = subprocess.run([BROWSER, '--headless=new', '--disable-gpu', '--no-first-run',
                        f'--user-data-dir={d / "profile"}', '--virtual-time-budget=3000',
                        '--dump-dom', out.as_uri() + route],
                       capture_output=True, text=True, encoding='utf-8', timeout=120)
    m = re.search(r'<main id="app"[^>]*>(.*)</main>', r.stdout, re.S)
    assert m, r.stderr[-2000:]
    return m.group(1)


def text(fragment: str) -> str:
    return html.unescape(re.sub(r'<[^>]+>', ' ', fragment))


def test_home_lists_the_episode(page):
    main = render(page, '#/')
    assert '1 回すべてを一覧で見る' in text(main)
    assert 'href="#/ep/' in main


def test_episode_shows_search_term_and_hides_the_masked_page(page):
    main = render(page, '#/list')
    ep = re.search(r'href="(#/ep/[^"]+)"', main).group(1)
    main = render(page, ep)
    body = text(main)
    assert '検索「pandas」' in body
    assert 'pandas merge <b>bold</b>' in body          # 題名は文字として出る (タグとして解釈しない)
    assert '<b>bold</b>' not in main
    assert 'メール' in body and 'private subject' not in body
    assert 'href="https://mail.google.com' not in main  # 伏せた節はリンクにしない


def test_tabs_and_rules_render(page):
    tabs = text(render(page, '#/tabs'))
    assert '見つかりません' not in tabs and 'pandas merge' in tabs
    assert '木を作る規則' in text(render(page, '#/rules'))


def test_unknown_episode_says_so(page):
    assert 'その回はありません' in text(render(page, '#/ep/no-such-id'))
