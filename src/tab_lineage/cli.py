"""コマンドライン. `tab-lineage <command> -h` で各コマンドの引数を表示する."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
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


def analyze(dbs: list[str], config_path: str | None) -> Analysis:
    cfg = config.load(config_path)
    visits = histdb.load_visits(dbs)
    if not visits:
        raise SystemExit('訪問が 1 件も無い. History のパスを確かめる')
    lcfg = lineage.LineageConfig.from_config(cfg)
    ecfg = episodes.EpisodeConfig.from_config(cfg)
    privacy = Privacy.from_config(cfg.get('privacy', {}))
    g = lineage.build(visits, lcfg, privacy)
    return Analysis(cfg, g, episodes.build(g, ecfg), lcfg, ecfg, privacy, visits[-1].t)


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
        tab_plan = plan.build(session.load(a.session), r.graph, r.timeline, r.lcfg, r.ecfg, r.privacy, r.last_visit)
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
    placed = plan.classify(session.load(a.session), r.graph, r.timeline, r.lcfg, r.ecfg, r.privacy, r.last_visit)
    arr = arrange.build(placed, arrange.ArrangeConfig.from_config(r.cfg))
    print(arrange.outline(arr))
    if a.out:
        out = pathlib.Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(arr, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'{out}: 手で直してから apply に渡せる')


def cmd_apply(a) -> None:
    try:
        arr = arrange.validate(json.loads(pathlib.Path(a.plan).read_text(encoding='utf-8')))
    except (OSError, ValueError) as e:
        raise SystemExit(f'{a.plan}: {e}')
    print(arrange.outline(arr))
    if a.dry_run:
        return
    from . import vivaldi
    res = vivaldi.apply(arr, port=a.port, hibernate=not a.no_hibernate)
    print(f'タブ {res["tabs"]} 枚 (開いてあった {res["already"]} 枚は作らない), スタック {res["stacks"]} 個, ブックマーク {res["bookmarks"]["added"]} 件 '
          f'(既にあった {res["bookmarks"]["skipped"]} 件は足さない), 休止 {res["hibernated"]} 枚')
    for f in res['failed']:
        print(f'スタックにできなかった: {f}')
    if res['created']:
        print('作ったワークスペース: ' + ', '.join(res['created']))
    if not res['switched']:
        print('ワークスペースを切り替える関数が見つからなかった. 画面左上のボタンから開く')


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
    p.add_argument('--out', help='案を JSON で書き出す (実際の URL と題名が入る. 公開しない)')
    p.set_defaults(func=cmd_arrange)

    p = sub.add_parser('apply', help='arrange の案を起動中の Vivaldi の最後に使ったウィンドウに適用する (足すだけで, 閉じない)')
    p.add_argument('plan', help='arrange --out の JSON')
    p.add_argument('--port', type=int, default=9222, help='Vivaldi の --remote-debugging-port')
    p.add_argument('--dry-run', action='store_true', help='案を表示するだけで Vivaldi に接続しない')
    p.add_argument('--no-hibernate', action='store_true', help='ほかのワークスペースのタブを休止させない')
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser('tabs', help='セッションファイルの開いているタブを並び順に表示する')
    p.add_argument('session')
    p.set_defaults(func=cmd_tabs)

    a = ap.parse_args(argv)
    a.func(a)
