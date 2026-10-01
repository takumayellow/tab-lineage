"""解説サイトとテストで使う架空の閲覧履歴を作る.

架空の学生が 4 日前に記事を読みかけて放置し, 今日は実験レポートと個人開発をしながら
フーリエからエジプト旅行へ脱線した, という History と Session を書き出す.
公開のドキュメント (Python・NumPy・MDN・Wikipedia など) 以外のサイトは *.example にして, 実在の人や組織を指さない.

  python examples/sample/make_sample.py out/sample
"""
from __future__ import annotations

import pathlib
import sqlite3
import struct
import sys
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
WEBKIT_TO_UNIX_S = 11_644_473_600
US = 1_000_000
LINK, TYPED = 0, 1
SESSION_NAME = 'Session_13403000000000000'

# 訪問: (id, 日時, URL, 題名, 遷移, opener, from, 開いていた分, 検索語)
# opener は新しいタブで開いた元の訪問, from は同じタブで来た元の訪問.
V = []


def v(id, when, url, title, core=LINK, opener=0, frm=0, minutes=2, term=None):
    V.append((id, when, url, title, core, opener, frm, minutes, term))


def d(day, hm):
    h, m = map(int, hm.split(':'))
    return datetime(2026, 9, day, h, m, tzinfo=JST)


# --- 4 日前の夜. 記事を読みかけて開いたまま --------------------------------------
v(1, d(24, '21:02'), 'https://blog.example.org/', 'ゆるやか開発ノート', TYPED)
v(2, d(24, '21:03'), 'https://blog.example.org/2026/09/rust-ownership', 'Rust の所有権を図で理解する', opener=1, minutes=900)
v(3, d(24, '21:15'), 'https://doc.rust-lang.org/book/ch04-01-what-is-ownership.html',
  'What is Ownership? - The Rust Programming Language', opener=2, minutes=900)
v(4, d(24, '21:40'), 'https://developer.mozilla.org/ja/docs/Web/CSS/CSS_grid_layout', 'CSS グリッドレイアウト - CSS | MDN', TYPED, minutes=880)
v(5, d(24, '21:44'), 'https://developer.mozilla.org/ja/docs/Web/CSS/CSS_grid_layout/Subgrid', 'サブグリッド - CSS | MDN', opener=4, minutes=870)
v(6, d(24, '22:10'), 'https://docs.python.org/ja/3/howto/descriptor.html', 'デスクリプタ ガイド — Python 3 ドキュメント', TYPED, minutes=840)

# --- 今日の昼. 実験レポート (離散フーリエ変換) -----------------------------------
v(10, d(28, '13:00'), 'https://lms.univ.example/course/view.php?id=101', '信号処理実験', TYPED, minutes=5)
v(11, d(28, '13:02'), 'https://lms.univ.example/mod/assign/view.php?id=2201', '第3回レポート: 離散フーリエ変換', opener=10, minutes=60)
v(12, d(28, '13:05'), 'https://duckduckgo.com/?q=numpy+fft+%E4%BD%BF%E3%81%84%E6%96%B9', 'numpy fft 使い方 at DuckDuckGo',
  TYPED, term='numpy fft 使い方')
v(13, d(28, '13:05') + timedelta(seconds=10), 'https://numpy.org/doc/stable/reference/routines.fft.html',
  'Discrete Fourier Transform (numpy.fft) — NumPy Manual', minutes=40)
v(14, d(28, '13:08'), 'https://numpy.org/doc/stable/reference/generated/numpy.fft.rfft.html', 'numpy.fft.rfft — NumPy Manual',
  opener=13, minutes=30)
v(15, d(28, '13:20'), 'https://duckduckgo.com/?q=%E7%AA%93%E9%96%A2%E6%95%B0+%E3%83%8F%E3%83%B3%E7%AA%93', '窓関数 ハン窓 at DuckDuckGo',
  TYPED, term='窓関数 ハン窓')
v(16, d(28, '13:20') + timedelta(seconds=8), 'https://ja.wikipedia.org/wiki/%E7%AA%93%E9%96%A2%E6%95%B0', '窓関数 - Wikipedia', minutes=20)
v(17, d(28, '13:26'), 'https://ja.wikipedia.org/wiki/%E9%AB%98%E9%80%9F%E3%83%95%E3%83%BC%E3%83%AA%E3%82%A8%E5%A4%89%E6%8F%9B',
  '高速フーリエ変換 - Wikipedia', opener=16, minutes=15)
v(40, d(28, '13:28'), 'https://dsp.example.jp/window-functions', '窓関数の選び方 ハン窓とハミング窓 | 信号処理ノート',
  opener=16, minutes=12)
v(41, d(28, '13:29'), 'https://dsp.example.jp/spectral-leakage', 'スペクトル漏れと窓関数 | 信号処理ノート', opener=40, minutes=10)
# 脱線: フーリエの伝記 (エジプト遠征に同行した) -> ピラミッドの動画 -> 旅行の計画
v(18, d(28, '13:31'), 'https://ja.wikipedia.org/wiki/%E3%82%B8%E3%83%A7%E3%82%BC%E3%83%95%E3%83%BB%E3%83%95%E3%83%BC%E3%83%AA%E3%82%A8',
  'ジョゼフ・フーリエ - Wikipedia', opener=17, minutes=4)
v(20, d(28, '13:38'), 'https://video.example.com/watch/pyramid-in-3min', 'ピラミッドは どう積んだのか 3 分で - ExampleTube', opener=18, minutes=6)
v(21, d(28, '13:44'), 'https://video.example.com/watch/sphinx-nose', 'スフィンクスの鼻はどこへ消えたか - ExampleTube', opener=20, minutes=7)
v(22, d(28, '13:51'), 'https://travel.example.jp/egypt/', 'エジプト旅行 ベストシーズンと費用の目安 | たびの手帖', opener=21, minutes=5)
v(23, d(28, '13:55'), 'https://travel.example.jp/egypt/cairo-hotels', 'カイロのホテル 人気ランキング | たびの手帖', opener=22, minutes=3)
v(24, d(28, '13:58'), 'https://food.example.jp/recipe/koshari', 'エジプトの国民食 コシャリの作り方 | ごはん帖', opener=22, minutes=4)
# 本筋へ戻る
v(25, d(28, '14:05'), 'https://matplotlib.org/stable/gallery/lines_bars_and_markers/spectrum_demo.html',
  'Spectrum representations — Matplotlib documentation', TYPED, minutes=25)
v(26, d(28, '14:09'), 'https://matplotlib.org/stable/api/_as_gen/matplotlib.pyplot.specgram.html',
  'matplotlib.pyplot.specgram — Matplotlib documentation', opener=25, minutes=20)
v(27, d(28, '14:30'), 'https://numpy.org/doc/stable/reference/routines.fft.html',
  'Discrete Fourier Transform (numpy.fft) — NumPy Manual', TYPED, minutes=10)

# --- 夕方. 個人開発 (ターミナルの ToDo アプリ) ----------------------------------
v(30, d(28, '16:10'), 'https://git.example.com/sample/tasknote', 'sample/tasknote: ターミナルで使う ToDo 管理', TYPED, minutes=40)
v(31, d(28, '16:12'), 'https://git.example.com/sample/tasknote/issues/12', '期限の近い順に並べたい · Issue #12 · sample/tasknote',
  opener=30, minutes=30)
v(32, d(28, '16:15'), 'https://duckduckgo.com/?q=python+argparse+%E3%82%B5%E3%83%96%E3%82%B3%E3%83%9E%E3%83%B3%E3%83%89',
  'python argparse サブコマンド at DuckDuckGo', TYPED, term='python argparse サブコマンド')
v(33, d(28, '16:15') + timedelta(seconds=12), 'https://docs.python.org/ja/3/library/argparse.html',
  'argparse --- コマンドラインオプション、引数、サブコマンドのパーサー — Python 3 ドキュメント', minutes=30)
v(34, d(28, '16:22'), 'https://docs.python.org/ja/3/library/datetime.html', 'datetime --- 基本的な日付型および時間型 — Python 3 ドキュメント',
  opener=33, minutes=25)
v(35, d(28, '16:40'), 'https://pypi.org/project/rich/', 'rich · PyPI', TYPED, minutes=10)
v(36, d(28, '16:42'), 'https://rich.readthedocs.io/en/stable/tables.html', 'Tables — Rich documentation', opener=35, minutes=15)
v(37, d(28, '16:58'), 'https://accounts.example.com/login', 'ログイン | Example アカウント', TYPED, minutes=1)
v(38, d(28, '17:05'), 'https://blog.example.org/2026/09/terminal-colors', 'ターミナルの 256 色を使いこなす', TYPED, minutes=8)

# 今のタブ: ウィンドウごとに (訪問 ID か URL, 題名). 3 つのウィンドウに混ざって開いている
TABS = {
    1: [11, 13, 30, 33, 12, 14, 38],
    2: [16, 17, 20, 22, 25, 26, 27, ('vivaldi://settings/', '設定')],
    3: [2, 3, 4, 5, 6, 31, 34, 35, 36, 37, 32, 24],
}


def webkit_us(t: datetime) -> int:
    return int((t.timestamp() + WEBKIT_TO_UNIX_S) * US)


def write_history(path: pathlib.Path) -> None:
    c = sqlite3.connect(path)
    c.executescript('''
        CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT);
        CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER, from_visit INTEGER,
                             transition INTEGER, visit_duration INTEGER, opener_visit INTEGER);
        CREATE TABLE keyword_search_terms (keyword_id INTEGER, url_id INTEGER, term TEXT);
    ''')
    url_ids: dict[str, int] = {}
    for id, when, url, title, core, opener, frm, minutes, term in V:
        if url not in url_ids:
            url_ids[url] = len(url_ids) + 1
            c.execute('INSERT INTO urls VALUES (?, ?, ?)', (url_ids[url], url, title))
            if term:
                c.execute('INSERT INTO keyword_search_terms VALUES (1, ?, ?)', (url_ids[url], term))
        c.execute('INSERT INTO visits VALUES (?, ?, ?, ?, ?, ?, ?)',
                  (id, url_ids[url], webkit_us(when), frm, core, minutes * 60 * US, opener))
    c.commit()
    c.close()


def _pad(b: bytes) -> bytes:
    return b + b'\0' * (-len(b) % 4)


def _record(cmd: int, payload: bytes) -> bytes:
    return struct.pack('<HB', len(payload) + 1, cmd) + payload


def _nav(tab: int, url: str, title: str) -> bytes:
    u, t = url.encode('utf-8'), title.encode('utf-16-le')
    body = struct.pack('<iii', tab, 0, len(u)) + _pad(u) + struct.pack('<i', len(title)) + _pad(t)
    return _record(6, struct.pack('<i', len(body)) + body)


def write_session(path: pathlib.Path) -> None:
    """Chromium の SNSS 形式. タブごとに SetTabWindow, SetTabIndexInWindow, UpdateTabNavigation を書く."""
    by_id = {x[0]: x for x in V}
    out = [b'SNSS', struct.pack('<i', 3)]
    tab = 100
    for window, items in TABS.items():
        for index, item in enumerate(items):
            url, title = item if isinstance(item, tuple) else (by_id[item][2], by_id[item][3])
            tab += 1
            out += [_record(0, struct.pack('<ii', window, tab)), _record(2, struct.pack('<ii', tab, index)),
                    _nav(tab, url, title)]
    path.write_bytes(b''.join(out))


def make(out_dir: str | pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    history, session = out / 'History', out / SESSION_NAME
    history.unlink(missing_ok=True)
    write_history(history)
    write_session(session)
    return history, session


if __name__ == '__main__':
    for p in make(sys.argv[1] if len(sys.argv) > 1 else 'out/sample'):
        print(p)
