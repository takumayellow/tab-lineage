"""起動中の Vivaldi のタブを, 裏で並べ直す (auto).

タスクスケジューラなどから定期的に呼ぶ. 1 回の呼び出しで次を確かめ, そろったときだけ arrange と apply をする.
  1. PC を idle 分以上操作していない (Windows だけ確かめる).
     操作中でも, 前の整理のあと新しく開いたタブが busy_tabs 枚以上あり, 前の整理から busy_interval 分たっていれば整理する.
     そのときは idle 分以内に見たタブに触らない
  2. Vivaldi がデバッグのポートを開いている
  3. 前の整理のあと, 開いているタブの URL が変わった. 手でタブを並べ替えただけなら整理し直さない
適用は quiet で行う. 各ウィンドウで選ばれているタブと固定したタブは動かさず, ワークスペースも切り替えない.
入力しかけのページは閉じも休止もしない (vivaldi.apply).
セッションファイルの代わりに, 起動中の Vivaldi から今のタブを読む (起動中はセッションファイルを読めない).
案と適用前の控えは <state>/runs/<日時>/ に残す. URL と題名がそのまま入るので公開しない.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import shutil
import sys

from . import arrange, config, plan, session, snapshot, vivaldi

KEEP_RUNS = 14
LOG_MAX = 512 * 1024


class Skip(Exception):
    """条件がそろわないので今回は何もしない."""


def default_state() -> pathlib.Path:
    if os.name == 'nt':
        base = pathlib.Path(os.environ.get('LOCALAPPDATA', pathlib.Path.home() / 'AppData/Local'))
    else:
        base = pathlib.Path(os.environ.get('XDG_STATE_HOME', pathlib.Path.home() / '.local/state'))
    return base / 'tab-lineage' / 'auto'


def idle_seconds() -> float | None:
    """最後にキーボードかマウスを触ってからの秒数. Windows 以外は None (確かめない)."""
    if os.name != 'nt':
        return None
    import ctypes

    class LastInput(ctypes.Structure):
        _fields_ = [('cbSize', ctypes.c_uint), ('dwTime', ctypes.c_uint)]

    li = LastInput(ctypes.sizeof(LastInput), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
        return None
    # どちらも起動からのミリ秒で 49.7 日で一周する. 差を 32 ビットで取れば一周をまたいでも合う
    return ((ctypes.windll.kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000


def fingerprint(tabs: list[dict]) -> str:
    """固定していないタブの URL の集まり. 並べ替え・ワークスペースの移動・選ぶタブの変化では変わらない."""
    urls = sorted(t.get('url') or '' for t in tabs if not t.get('pinned'))
    return hashlib.sha256('\n'.join(urls).encode('utf-8')).hexdigest()


def _mark(url: str) -> str:
    return hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]


def url_marks(tabs: list[dict]) -> list[str]:
    """固定していないタブの URL のハッシュ. 状態のファイルに URL そのものを残さない."""
    return sorted({_mark(t.get('url') or '') for t in tabs if not t.get('pinned')})


def new_tabs(tabs: list[dict], saved: dict) -> int:
    """前の整理のときに無かった URL の, 固定していないタブの枚数. 覚えていなければ全部数える."""
    seen = set(saved.get('urls') or ())
    return sum(1 for t in tabs if not t.get('pinned') and _mark(t.get('url') or '') not in seen)


def minutes_since(saved: dict) -> float | None:
    """前の整理からの分数. 整理したことが無ければ None."""
    try:
        return (dt.datetime.now() - dt.datetime.fromisoformat(saved['applied'])).total_seconds() / 60
    except (KeyError, TypeError, ValueError):
        return None


def to_tabs(tabs: list[dict]) -> list[session.Tab]:
    def ext(t):
        try:
            return json.loads(t.get('vivExtData') or '{}')
        except ValueError:
            return {}
    return [session.Tab(t['id'], t.get('url') or '', t.get('title') or '', window=t.get('window', 0),
                        index=t.get('index', -1), ext=ext(t))
            for t in sorted(tabs, key=lambda t: (t.get('window', 0), t.get('index', -1)))]


def load_state(state: pathlib.Path) -> dict:
    try:
        return json.loads((state / 'state.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def save_state(state: pathlib.Path, data: dict) -> None:
    tmp = state / 'state.json.tmp'
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    tmp.replace(state / 'state.json')


def prune(state: pathlib.Path, keep: int = KEEP_RUNS) -> None:
    if not (state / 'runs').is_dir():
        return
    runs = sorted(p for p in (state / 'runs').iterdir() if p.is_dir())
    for old in runs[:-keep] if keep else runs:
        shutil.rmtree(old, ignore_errors=True)


def make_plan(history: pathlib.Path, tabs: list[dict], config_path: str | None, labels_path: str | None) -> dict:
    from .cli import analyze
    r = analyze([str(history)], config_path)
    acfg = arrange.ArrangeConfig.from_config(r.cfg)
    placed = plan.classify(to_tabs(tabs), r.graph, r.timeline, r.lcfg, r.ecfg, r.privacy, r.last_visit,
                           labels=config.load_labels(labels_path),
                           anchored=plan.anchor_labels(r.visits, acfg.anchors, r.privacy), visits=r.visits)
    return arrange.validate(arrange.build(placed, acfg))


def check(state: pathlib.Path, port: int, idle_min: float, force: bool,
          busy_tabs: int = 0, busy_interval: float = 60) -> tuple[list[dict], str, bool]:
    """整理してよければ, 今のタブ・その fingerprint・操作中に整理するか を返す. だめなら Skip.
    busy_tabs が 0 なら操作中には整理しない."""
    busy = ''
    if not force:
        idle = idle_seconds()
        if idle is None and os.name == 'nt':   # 取れないときは操作されているものとして扱う
            raise Skip('無操作の時間を取れない')
        if idle is not None and idle < idle_min * 60:
            busy = f'操作されている (無操作 {int(idle // 60)} 分)'
            if busy_tabs <= 0:
                raise Skip(busy)
    try:
        tabs = vivaldi.tabs(port)
    except SystemExit as e:   # ポートが開いていない・Vivaldi ではない
        raise Skip(str(e))
    fp = fingerprint(tabs)
    saved = load_state(state)
    if busy:
        n, since = new_tabs(tabs, saved), minutes_since(saved)
        if n < busy_tabs:
            raise Skip(f'{busy}. 新しいタブ {n} 枚 ({busy_tabs} 枚から整理する)')
        if since is not None and since < busy_interval:
            raise Skip(f'{busy}. 新しいタブ {n} 枚, 前の整理から {int(since)} 分 ({busy_interval:g} 分あける)')
    if not force and saved.get('fingerprint') == fp:
        raise Skip('前の整理のあとタブが変わっていない')
    return tabs, fp, bool(busy)


def run(state: pathlib.Path, port: int, profile: pathlib.Path, config_path: str | None = None,
        labels_path: str | None = None, idle_min: float = 30, close: bool = False, refile: bool = False,
        force: bool = False, dry_run: bool = False, busy_tabs: int = 0, busy_interval: float = 60) -> list[str]:
    """1 回分. 報告の行を返す. 条件がそろわなければ Skip."""
    from .cli import apply_report
    tabs, fp, busy = check(state, port, idle_min, force, busy_tabs, busy_interval)
    run_dir = state / 'runs' / f'{dt.datetime.now():%Y%m%d-%H%M%S}'
    try:
        run_dir.mkdir(parents=True)
    except FileExistsError:   # 同じ秒に別の回が走った. 前の回の控えを上書きしない
        raise Skip(f'{run_dir.name} が既にある')
    history = snapshot.copy_history(profile, run_dir)
    try:
        arr = make_plan(history, tabs, config_path, labels_path)
    finally:
        history.unlink(missing_ok=True)   # 大きい. 案と控えがあれば足りる
    (run_dir / 'plan.json').write_text(json.dumps(arr, ensure_ascii=False, indent=1), encoding='utf-8')
    (run_dir / 'before.json').write_text(json.dumps(tabs, ensure_ascii=False, indent=1), encoding='utf-8')
    lines = ([f'操作中なので, {idle_min:g} 分以内に見たタブには触らない'] if busy else []) + [arrange.outline(arr)]
    if dry_run:
        return lines
    res = vivaldi.apply(arr, port=port, close=close, quiet=True, recent_ms=int(idle_min * 60_000) if busy else 0)
    lines += apply_report(res, close)
    if res['failed'] and not (res['adopted'] or res['already'] or res['made']):
        # 1 枚も置けなかった. 済んだことにせず, 次の回にやり直す
        raise RuntimeError(f'案のタブを 1 枚も置けなかった ({len(res["failed"])} 件失敗)')
    if refile:
        acfg = arrange.ArrangeConfig.from_config(config.load(config_path))
        moves = arrange.refile_moves(vivaldi.reading_links(port, acfg.reading_root), acfg)
        if moves:
            lines.append(f'あとで読むを振り分けた {vivaldi.move(port, acfg.reading_root, moves)["moved"]} 件')
    # 適用で変わった後のタブを覚える. 次の回はここから URL が変わったときだけ整理する
    try:
        after = vivaldi.tabs(port)
    except (Exception, SystemExit) as e:   # 読めなければ前の値で覚える. 次の回にもう一度整理するだけで済む
        lines.append(f'適用の後のタブを読めなかった: {e}')
        after = tabs
    save_state(state, {'fingerprint': fingerprint(after), 'urls': url_marks(after),
                       'applied': dt.datetime.now().isoformat(timespec='seconds'), 'run': run_dir.name})
    return lines


def log(state: pathlib.Path, msg: str) -> None:
    line = f'{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}'
    if sys.stdout is not None:
        print(line, flush=True)
    path = state / 'auto.log'
    if path.exists() and path.stat().st_size > LOG_MAX:   # 15 分おきの skip で伸び続けないよう 1 世代だけ残す
        path.replace(path.with_name('auto.log.1'))
    with path.open('a', encoding='utf-8') as f:
        f.write(line + '\n')
