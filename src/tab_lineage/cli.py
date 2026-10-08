"""コマンドライン. `tab-lineage <command> -h` で各コマンドの引数を表示する."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from dataclasses import dataclass

from . import arrange, config, episodes, histdb, lineage, plan, session, site, snapshot
from .privacy import Privacy


@dataclass(frozen=True)
class Analysis:
    cfg: dict
    graph: lineage.Graph
    timeline: episodes.Timeline
    lcfg: lineage.LineageConfig
    ecfg: episodes.EpisodeConfig
    privacy: Privacy
    last_visit: int
    visits: list[histdb.Visit]


def analyze(dbs: list[str], config_path: str | None) -> Analysis:
    cfg = config.load(config_path)
    visits = histdb.load_visits(dbs)
    if not visits:
        raise SystemExit('訪問が 1 件も無い. History のパスを確かめる')
    lcfg = lineage.LineageConfig.from_config(cfg)
    ecfg = episodes.EpisodeConfig.from_config(cfg)
    privacy = Privacy.from_config(cfg.get('privacy', {}))
    g = lineage.build(visits, lcfg, privacy)
    try:
        tz = episodes.parse_tz(cfg.get('episodes', {}).get('timezone'))
    except ValueError as e:
        raise SystemExit(str(e))
    return Analysis(cfg, g, episodes.build(g, ecfg, tz), lcfg, ecfg, privacy, visits[-1].t, visits)


def _fmt_span(ep: episodes.Episode) -> str:
    return f'{ep.id.replace("T", " ")} ({round((ep.t1 - ep.t0) / histdb.US / 60)} 分)'


def cmd_snapshot(a) -> None:
    profile = pathlib.Path(a.profile_dir) if a.profile_dir else snapshot.default_profile(a.browser, a.profile)
    for p in snapshot.snapshot(profile, pathlib.Path(a.out)):
        print(p)


def cmd_stats(a) -> None:
    r = analyze(a.db, a.config)
    print(json.dumps({'lineage': r.graph.stats, 'episodes': len(r.timeline.episodes),
                      'detours': len(r.timeline.detours), 'stale': len(r.timeline.stale)},
                     ensure_ascii=False, indent=1))


def cmd_outline(a) -> None:
    """回とスレッドの一覧. labels.toml を書くときに見る (スレッドの ID は根の訪問 ID)."""
    r = analyze(a.db, a.config)
    labels = config.load_labels(a.labels)
    eps = sorted(r.timeline.episodes, key=lambda e: -e.visits)[:a.top] if a.top else r.timeline.episodes
    for ep in eps:
        cur = labels.get(ep.id, {})
        mark = '*' if cur.get('featured') else ('-' if cur.get('hide') else ' ')
        print(f'{mark} {ep.id}  {ep.visits:4d} 訪問  {_fmt_span(ep)}  {cur.get("title") or ep.label}')
        for th in ep.threads:
            print(f'      {th.id:>8}  {th.visits:4d}  {cur.get("threads", {}).get(th.id, th.label)}')


def cmd_build(a) -> None:
    r = analyze(a.db, a.config)
    labels = config.load_labels(a.labels)
    tab_plan = None
    if a.plan:
        tab_plan = json.loads(pathlib.Path(a.plan).read_text(encoding='utf-8'))
    elif a.session:
        tab_plan = plan.build(session.load(a.session), r.graph, r.timeline, r.lcfg, r.ecfg, r.privacy, r.last_visit,
                              labels=labels)
    data = site.payload(r.graph, r.timeline, r.cfg, labels, tab_plan)
    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(site.render(data), encoding='utf-8')
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    c = data['meta']['counts']
    print(f'{out}: {c["episodes"]} 回, {c["nodes"]} 節, {c["visits"]} 訪問, {out.stat().st_size / 2**20:.1f} MB')


def cmd_arrange(a) -> None:
    r = analyze(a.db, a.config)
    acfg = arrange.ArrangeConfig.from_config(r.cfg)
    placed = plan.classify(session.load(a.session), r.graph, r.timeline, r.lcfg, r.ecfg, r.privacy, r.last_visit,
                           labels=config.load_labels(a.labels),
                           anchored=plan.anchor_labels(r.visits, acfg.anchors, r.privacy), visits=r.visits)
    arr = arrange.build(placed, acfg)
    print(arrange.outline(arr))
    if a.out:
        out = pathlib.Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(arr, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'{out}: 手で直してから apply に渡せる')


def cmd_refile(a) -> None:
    from . import vivaldi
    acfg = arrange.ArrangeConfig.from_config(config.load(a.config))
    links = vivaldi.reading_links(a.port, acfg.reading_root)
    moves = arrange.refile_moves(links, acfg)
    for m in moves:
        print(f'{"/".join([acfg.reading_root, *m["path"]])} <- {m["title"]}')
    if a.dry_run or not moves:
        print(f'振り分ける {len(moves)} 件 (直下に残す {len(links) - len(moves)} 件)')
        return
    res = vivaldi.move(a.port, acfg.reading_root, moves)
    print(f'振り分けた {res["moved"]} 件 (直下に残した {len(links) - res["moved"]} 件)')
    for s in res['skipped']:
        print(f'移さなかった: {s}')


def cmd_apply(a) -> None:
    try:
        arr = arrange.validate(json.loads(pathlib.Path(a.plan).read_text(encoding='utf-8')))
    except (OSError, ValueError) as e:
        raise SystemExit(f'{a.plan}: {e}')
    print(arrange.outline(arr))
    if a.dry_run:
        return
    from . import vivaldi
    # 移したタブや閉じたタブを戻せるように, 適用の前に全部のタブを案の隣へ控える
    backup = pathlib.Path(a.plan).with_name(f'{pathlib.Path(a.plan).stem}.before-{time.strftime("%Y%m%d-%H%M%S")}.json')
    backup.write_text(json.dumps(vivaldi.tabs(a.port), ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'{backup}: 適用する前のタブの控え')
    res = vivaldi.apply(arr, port=a.port, hibernate=not a.no_hibernate, close=a.close)
    for line in apply_report(res, a.close):
        print(line)
    if not res['switched']:
        print('ワークスペースを切り替える関数が見つからなかった. 画面左上のボタンから開く')


def apply_report(res: dict, close: bool) -> list[str]:
    """vivaldi.apply の結果を, 表示する行にする."""
    lines = [f'タブ {res["tabs"]} 枚 (開いていたのを移した {res["adopted"]} 枚, 元の場所のまま {res["already"]} 枚, '
             f'新しく開いた {res["made"]} 枚, 固定したタブのまま {res["kept"]} 枚), スタック {res["stacks"]} 個, ブックマーク {res["bookmarks"]["added"]} 件 '
             f'(既にあった {res["bookmarks"]["skipped"]} 件は足さない), 閉じた {res["closed"]} 枚, 休止 {res["hibernated"]} 枚']
    if res.get('guarded') or res.get('unknown'):
        lines.append(f'触らなかったタブ {res.get("guarded", 0)} 枚 (入力しかけ {res.get("dirty", 0)} ページ・最近見たタブ), '
                     f'中を調べられず閉じも休止もしなかったタブ {res.get("unknown", 0)} 枚')
    lines += [f'できなかった: {f}' for f in res['failed']]
    if res['created']:
        lines.append('作ったワークスペース: ' + ', '.join(res['created']))
    if res['left']:
        lines.append(f'閉じなかったタブ {len(res["left"])} 枚 (案に無い・固定した・移せなかったタブ):' if close else
                     f'残したタブ {len(res["left"])} 枚 (閉じるなら --close):')
        lines += [f'  {title}  {url}' for title, url in res['left']]
    return lines


def cmd_auto(a) -> None:
    from . import auto
    state = pathlib.Path(a.state) if a.state else auto.default_state()
    state.mkdir(parents=True, exist_ok=True)
    profile = pathlib.Path(a.profile_dir) if a.profile_dir else snapshot.default_profile(a.browser, a.profile)
    try:
        lines = auto.run(state, a.port, profile, a.config, a.labels, idle_min=a.idle, close=a.close,
                         refile=a.refile, force=a.now, dry_run=a.dry_run,
                         busy_tabs=a.busy_tabs, busy_interval=a.busy_interval)
    except auto.Skip as e:
        auto.log(state, f'skip: {e}')
        return
    except (Exception, SystemExit) as e:
        auto.log(state, f'failed: {type(e).__name__}: {e}')
        raise SystemExit(1)
    finally:
        auto.prune(state)
    auto.log(state, ('dry-run' if a.dry_run else 'applied') + '\n' + '\n'.join(lines))


def cmd_tabs(a) -> None:
    for t in session.load(a.session):
        print(f'{t.window}\t{t.index}\t{t.title}\t{t.url}')


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(prog='tab-lineage', description='ブラウザの履歴から閲覧の系統樹を作る')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('snapshot', help='使用中のブラウザから History とセッションをコピーする')
    p.add_argument('out')
    p.add_argument('--browser', default='vivaldi', choices=snapshot.BROWSERS)
    p.add_argument('--profile', default='Default')
    p.add_argument('--profile-dir', help='プロファイルのディレクトリを直接指定する')
    p.set_defaults(func=cmd_snapshot)

    def common(p):
        p.add_argument('db', nargs='+', help='History のコピー (複数なら訪問 ID で重ねる)')
        p.add_argument('--config', help='設定の TOML (既定値に重ねる)')

    p = sub.add_parser('stats', help='木と回の集計を表示する')
    common(p)
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser('outline', help='回とスレッドの一覧を表示する')
    common(p)
    p.add_argument('--labels', help='回に付けた名前の TOML')
    p.add_argument('--top', type=int, default=0, help='訪問の多い順に N 回だけ')
    p.set_defaults(func=cmd_outline)

    p = sub.add_parser('build', help='サイト (1 枚の HTML) を作る')
    common(p)
    p.add_argument('--out', required=True)
    p.add_argument('--labels', help='回に付けた名前の TOML')
    p.add_argument('--session', help='今のタブの整理案を作るセッションファイル')
    p.add_argument('--plan', help='手で作った整理案の JSON (--session より優先)')
    p.add_argument('--json', help='埋め込んだデータを JSON でも書き出す')
    p.set_defaults(func=cmd_build)

    p = sub.add_parser('arrange', help='今のタブをワークスペース・スタック・ブックマークへ並べ直す案を作る')
    common(p)
    p.add_argument('--session', required=True, help='今のタブのセッションファイル')
    p.add_argument('--labels', help='スレッドに付けた名前の TOML (スタックの名前になる)')
    p.add_argument('--out', help='案を JSON で書き出す (実際の URL と題名が入る. 公開しない)')
    p.set_defaults(func=cmd_arrange)

    p = sub.add_parser('apply', help='arrange の案を起動中の Vivaldi の最後に使ったウィンドウに適用する')
    p.add_argument('plan', help='arrange --out の JSON')
    p.add_argument('--port', type=int, default=9222, help='Vivaldi の --remote-debugging-port')
    p.add_argument('--dry-run', action='store_true', help='案を表示するだけで Vivaldi に接続しない')
    p.add_argument('--no-hibernate', action='store_true', help='ほかのワークスペースのタブを休止させない')
    p.add_argument('--close', action='store_true',
                   help='閉じる案のタブとブックマークに入れたタブを閉じる')
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser('refile', help='あとで読むの直下のブックマークを, 設定のフォルダの規則で振り分け直す')
    p.add_argument('--config', help='設定の TOML (既定値に重ねる)')
    p.add_argument('--port', type=int, default=9222, help='Vivaldi の --remote-debugging-port')
    p.add_argument('--dry-run', action='store_true', help='移す先を表示するだけで移さない')
    p.set_defaults(func=cmd_refile)

    p = sub.add_parser('auto', help='操作していない間に, 起動中の Vivaldi のタブを裏で並べ直す (タスクから定期的に呼ぶ)')
    p.add_argument('--config', help='設定の TOML (既定値に重ねる)')
    p.add_argument('--labels', help='スレッドに付けた名前の TOML (スタックの名前になる)')
    p.add_argument('--port', type=int, default=9222, help='Vivaldi の --remote-debugging-port')
    p.add_argument('--browser', default='vivaldi', choices=snapshot.BROWSERS)
    p.add_argument('--profile', default='Default')
    p.add_argument('--profile-dir', help='プロファイルのディレクトリを直接指定する')
    p.add_argument('--state', help='状態・ログ・案と控えを置くディレクトリ (既定 %%LOCALAPPDATA%%/tab-lineage/auto)')
    p.add_argument('--idle', type=float, default=30, help='この分数以上操作していないときだけ並べ直す')
    p.add_argument('--busy-tabs', type=int, default=0,
                   help='操作中でも, 前の整理のあと新しく開いたタブがこの枚数以上なら並べ直す (--idle 分以内に見たタブには触らない). 0 なら操作中は並べ直さない')
    p.add_argument('--busy-interval', type=float, default=60, help='操作中に並べ直すとき, 前の整理からあける分数')
    p.add_argument('--close', action='store_true', help='閉じる案のタブとブックマークに入れたタブを閉じる')
    p.add_argument('--refile', action='store_true', help='続けてあとで読むの直下を振り分ける')
    p.add_argument('--now', action='store_true', help='操作の有無とタブの変化を見ずに今すぐ並べ直す')
    p.add_argument('--dry-run', action='store_true', help='案を作って控えるだけで Vivaldi を変えない')
    p.set_defaults(func=cmd_auto)

    p = sub.add_parser('tabs', help='セッションファイルの開いているタブを並び順に表示する')
    p.add_argument('session')
    p.set_defaults(func=cmd_tabs)

    a = ap.parse_args(argv)
    a.func(a)
