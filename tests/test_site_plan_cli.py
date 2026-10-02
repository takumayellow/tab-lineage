import dataclasses
import datetime as dt
import json
import re
import struct

import pytest

from _util import T0, at, make_history, nav, record, snss, visit
from tab_lineage import cli, config, episodes, lineage, plan, site
from tab_lineage.histdb import US
from tab_lineage.privacy import Privacy
from tab_lineage.session import Tab

UTC = dt.timezone.utc
CFG = config.default()
LCFG = lineage.LineageConfig.from_config(CFG)
ECFG = episodes.EpisodeConfig.from_config(CFG)
PRIVACY = Privacy.from_config(CFG['privacy'])

VISITS = [
    visit(1, 0, 'https://www.bing.com/search?q=pandas', 'pandas - Search', core=1, term='pandas'),
    visit(2, 10, 'https://docs.example/merge', 'pandas merge </script><!--', opener=1),
    visit(3, 20, 'https://mail.google.com/mail/u/0/#inbox/1', 'private', core=1),
]


def analysed():
    g = lineage.build(VISITS, LCFG, PRIVACY)
    return g, episodes.build(g, ECFG, UTC)


def embedded(page: str) -> dict:
    m = re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.S)
    return json.loads(m.group(1))


def test_payload_and_render_round_trip():
    g, tl = analysed()
    data = site.payload(g, tl, CFG, built_at=dt.datetime(2026, 9, 30, tzinfo=UTC))
    assert data['meta']['counts'] == {'visits': 3, 'nodes': 3, 'episodes': 1, 'detours': 0, 'stale': 0}
    nodes = data['episodes'][0]['nodes']
    assert nodes[0]['k'] == 1 and nodes[0]['q'] == 'pandas'
    assert nodes[2]['m'] == 1 and nodes[2]['t'] == 'メール'
    page = site.render(data)
    assert '</script><!--' not in page.split('<script id="data"', 1)[1].split('</script>', 1)[0]
    assert embedded(page) == data
    assert '{{DATA}}' not in page and '/*{{JS}}*/' not in page


def test_render_escapes_title_and_does_not_expand_markers_in_data():
    g, tl = analysed()
    cfg = config._merge(CFG, {'site': {'title': '<b>Tabs</b> & /*{{JS}}*/'}})
    data = site.payload(g, tl, cfg)
    page = site.render(data)
    assert '<title>&lt;b&gt;Tabs&lt;/b&gt; &amp; /*{{JS}}*/</title>' in page
    assert embedded(page)['meta']['title'] == '<b>Tabs</b> & /*{{JS}}*/'


def test_labels_rename_and_hide():
    g, tl = analysed()
    ep_id = tl.episodes[0].id
    th_id = tl.episodes[0].threads[0].id
    data = site.payload(g, tl, CFG, {ep_id: {'title': '名前', 'note': 'メモ', 'featured': True,
                                             'threads': {th_id: 'スレッド'}}})
    ep = data['episodes'][0]
    assert (ep['label'], ep['note'], ep['featured'], ep['th'][0]['label']) == ('名前', 'メモ', True, 'スレッド')
    assert site.payload(g, tl, CFG, {ep_id: {'hide': True}})['episodes'] == []


def test_plan_marks_duplicates_searches_and_stale_tabs():
    g, tl = analysed()
    tabs = [Tab(1, 'https://docs.example/merge?utm_source=a', 'pandas merge'),
            Tab(2, 'https://docs.example/merge', 'pandas merge'),
            Tab(3, 'https://www.bing.com/search?q=x', 'x - Search'),
            Tab(4, 'https://accounts.google.com/', 'Sign in')]
    p = plan.build(tabs, g, tl, LCFG, ECFG, PRIVACY, now_us=at(60))
    assert [s for _, s in p['tabs']] == ['keep', 'dup', 'serp']
    later = plan.build(tabs[:1], g, tl, LCFG, ECFG, PRIVACY, now_us=at(10) + 4 * 86400 * US)
    assert later['tabs'][0][1] == 'stash'
    assert later['tree'][0]['shelf'] is True


def test_cli_build_end_to_end(tmp_path, capsys):
    db = tmp_path / 'History.db'
    make_history(db, VISITS)
    sess = tmp_path / 'Session_1'
    sess.write_bytes(snss(nav(1, 0, 'https://docs.example/merge', 'pandas merge'),
                          record(0, struct.pack('<ii', 1, 1))))
    out, js = tmp_path / 'out' / 'site.html', tmp_path / 'site.json'
    cli.main(['build', str(db), '--session', str(sess), '--out', str(out), '--json', str(js)])
    data = embedded(out.read_text(encoding='utf-8'))
    assert data == json.loads(js.read_text(encoding='utf-8'))
    assert data['plan']['tabs'] == [['pandas merge', 'keep']]
    assert '1 回' in capsys.readouterr().out


def test_cli_outline(tmp_path, capsys):
    db = tmp_path / 'History.db'
    make_history(db, VISITS)
    cli.main(['outline', str(db)])
    assert 'pandas' in capsys.readouterr().out


def test_t0_is_in_2026():
    assert dt.datetime.fromtimestamp(T0 / US - 11_644_473_600, UTC).year == 2026


def test_plan_finds_the_node_of_a_masked_tab():
    g, tl = analysed()
    tab = Tab(1, 'https://mail.google.com/mail/u/0/#inbox/2', 'another private mail')
    p = plan.build([tab], g, tl, LCFG, ECFG, PRIVACY, now_us=at(20) + 4 * 86400 * US)
    assert p['tabs'] == [['メール', 'stash']]


def test_plan_does_not_take_pages_that_differ_in_the_query_for_duplicates():
    g, tl = analysed()
    tabs = [Tab(1, 'https://lms.example/view.php?id=101', '講義 A'),
            Tab(2, 'https://lms.example/view.php?id=202', '講義 B'),
            Tab(3, 'https://www.bing.com/search?q=a', 'a - Search'),
            Tab(4, 'https://www.bing.com/search?q=b', 'b - Search'),
            Tab(5, 'https://lms.example/view.php?id=101&utm_source=mail#top', '講義 A')]
    p = plan.build(tabs, g, tl, LCFG, ECFG, PRIVACY, now_us=at(60))
    assert [s for _, s in p['tabs']] == ['keep', 'keep', 'serp', 'serp', 'dup']


DETOUR = [
    visit(1, 0, 'https://docs.example/merge', 'pandas dataframe merge', core=1),
    visit(2, 10, 'https://pydata.example/join', 'pandas dataframe join', opener=1),
    visit(3, 20, 'https://qa.example/merge-error', 'pandas dataframe merge error', opener=1),
    visit(4, 30, 'https://blog.example/groupby', 'pandas dataframe groupby', opener=1),
    visit(5, 40, 'https://aircon.example/', 'エアコン カビ 掃除', opener=4),
    visit(6, 50, 'https://kaden.example/', 'エアコン カビ 対策', opener=5),
]


def test_tabs_in_a_detour_get_the_detour_as_their_goal():
    g = lineage.build(DETOUR, LCFG, PRIVACY)
    tl = episodes.build(g, ECFG, UTC)
    tabs = [Tab(1, 'https://docs.example/merge', 'merge'), Tab(2, 'https://kaden.example/', 'エアコン カビ 対策'),
            Tab(3, 'https://aircon.example/', 'エアコン カビ 掃除')]
    goals = [p.goal for p in plan.classify(tabs, g, tl, LCFG, ECFG, PRIVACY, now_us=at(60))]
    assert goals[0] != goals[1] and goals[1] == goals[2] == '寄り道: エアコン カビ 掃除'


def test_labels_rename_the_goal_of_a_thread():
    g = lineage.build(DETOUR, LCFG, PRIVACY)
    tl = episodes.build(g, ECFG, UTC)
    ep = tl.episodes[0]
    labels = {ep.id: {'threads': {ep.threads[0].id: 'pandas の結合'}}}
    p = plan.classify([Tab(1, 'https://pydata.example/join', 'join')], g, tl, LCFG, ECFG, PRIVACY,
                      now_us=at(60), labels=labels)
    assert p[0].goal == 'pandas の結合'


def test_timezone_in_the_config_fixes_the_episode_ids(tmp_path, capsys):
    db = tmp_path / 'History.db'
    make_history(db, VISITS)
    conf = tmp_path / 'c.toml'
    conf.write_text('[episodes]\ntimezone = "+09:00"\n', encoding='utf-8')
    cli.main(['outline', str(db), '--config', str(conf)])
    jst = dt.datetime.fromtimestamp(T0 / US - 11_644_473_600, dt.timezone(dt.timedelta(hours=9)))
    assert jst.strftime('%Y-%m-%dT%H:%M') in capsys.readouterr().out


def test_parse_timezone():
    assert episodes.parse_tz(None) is None
    assert episodes.parse_tz('UTC') == UTC
    assert episodes.parse_tz('-05:30').utcoffset(None) == -dt.timedelta(hours=5, minutes=30)
    assert episodes.parse_tz('Asia/Tokyo').utcoffset(dt.datetime(2026, 1, 1)) == dt.timedelta(hours=9)
    with pytest.raises(ValueError):
        episodes.parse_tz('+9')
    for bad in ('+09:60', '+24:00'):
        with pytest.raises(ValueError, match='読めない'):
            episodes.parse_tz(bad)


COURSE = [
    visit(1, 0, 'https://lms.example/course', 'ネットワーク', core=1),
    visit(2, 10, 'https://lms.example/quiz', '小テスト', opener=1),
    visit(3, 20, 'https://www.bing.com/search?q=arp', 'arp - Search', opener=2, term='arp'),
    visit(4, 30, 'https://arp.example/', 'ARP とは', opener=3),
    visit(5, 100_000, 'https://other.example/', 'ほかのこと', core=1),
]


def test_a_tab_without_a_rule_takes_the_workspace_of_its_thread():
    ecfg = dataclasses.replace(ECFG, workspaces=(('大学', ('lms.example/*',)),))
    g = lineage.build(COURSE, LCFG, PRIVACY)
    tl = episodes.build(g, ecfg, UTC)
    tabs = [Tab(1, 'https://arp.example/', 'ARP とは'), Tab(2, 'https://other.example/', 'ほかのこと')]
    ws = [p.workspace for p in plan.classify(tabs, g, tl, LCFG, ecfg, PRIVACY, now_us=at(100_010))]
    assert ws == ['大学', None]
