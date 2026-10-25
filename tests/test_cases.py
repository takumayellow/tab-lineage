"""ほかの道具が選んだ事例を回に付ける. 事例と履歴はすべて架空."""
import datetime as dt
import json

from _util import make_history, visit
from tab_lineage import cases, cli, config, episodes, lineage
from tab_lineage.privacy import Privacy

UTC = dt.timezone.utc
CFG = config.default()
START = dt.datetime.fromtimestamp(1_790_000_000, UTC)       # _util.T0 と同じ時刻

VISITS = [
    visit(1, 0, 'https://www.bing.com/search?q=pandas', 'pandas - Search', core=1, term='pandas'),
    visit(2, 60, 'https://docs.example/merge', 'pandas merge', opener=1),
    visit(3, 3 * 3600, 'https://video.example/watch', 'video', core=1),     # 間が空くので別の回
]


def timeline():
    g = lineage.build(VISITS, lineage.LineageConfig.from_config(CFG), Privacy.from_config(CFG['privacy']))
    return episodes.build(g, episodes.EpisodeConfig.from_config(CFG), UTC)


def case(start_min, end_min, browser=True, note='解説'):
    iso = lambda m: (START + dt.timedelta(minutes=m)).isoformat()
    return {'week': '2026-W38', 'start': iso(start_min), 'end': iso(end_min), 'reason': '最長の娯楽',
            'note': note, 'browser': browser}


def test_重なりが最も大きい回に付ける():
    tl = timeline()
    first, second = (ep.id for ep in tl.episodes)
    assert cases.episode_of(tl, case(-5, 2)) == first
    assert cases.episode_of(tl, case(170, 200)) == second
    assert cases.episode_of(tl, case(60, 90)) is None
    assert cases.episode_of(tl, case(1, 30)) is None      # 回の終わりに接しているだけ


def test_付けた回を読みものにし_人の所見は残す():
    tl = timeline()
    first, second = (ep.id for ep in tl.episodes)
    labels = {second: {'id': second, 'note': '人の所見'}}
    got = cases.attach(labels, tl, [case(-5, 2), case(0, 1, note='別の解説'), case(170, 200), case(0, 2, browser=False)])
    assert got[first] == {'id': first, 'featured': True, 'note': '2026-W38・最長の娯楽: 解説 / 2026-W38・最長の娯楽: 別の解説'}
    assert got[second]['note'] == '人の所見' and got[second]['featured'] is True
    assert labels[second] == {'id': second, 'note': '人の所見'}


def test_隠した回には付けない():
    tl = timeline()
    first = tl.episodes[0].id
    assert cases.attach({first: {'id': first, 'hide': True}}, tl, [case(-5, 2)])[first] == {'id': first, 'hide': True}


def test_読めない事例は無視する(tmp_path):
    assert cases.load(None) == [] and cases.load(tmp_path / 'none.json') == []
    (tmp_path / 'bad.json').write_text('{"a": 1}', encoding='utf-8')
    assert cases.load(tmp_path / 'bad.json') == []
    assert cases.attach({}, timeline(), [{'start': 'x', 'end': 'y', 'browser': True}]) == {}


def test_build_に事例を渡すと読みものに出る(tmp_path):
    db = tmp_path / 'History.db'
    make_history(db, VISITS)
    path = tmp_path / 'cases.json'
    path.write_text(json.dumps([case(-5, 2)]), encoding='utf-8')
    out, data = tmp_path / 'index.html', tmp_path / 'data.json'
    cli.main(['build', str(db), '--out', str(out), '--json', str(data), '--cases', str(path)])
    featured = [e for e in json.loads(data.read_text(encoding='utf-8'))['episodes'] if e['featured']]
    assert [e['note'] for e in featured] == ['2026-W38・最長の娯楽: 解説']
