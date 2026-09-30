import datetime as dt
import json
import re
import struct

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
    tabs = [Tab(1, 'https://docs.example/merge?utm=a', 'pandas merge'),
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
