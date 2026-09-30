from _util import visit
from tab_lineage import config, lineage
from tab_lineage.privacy import Privacy

CFG = lineage.LineageConfig.from_config(config.default())
PRIVACY = Privacy.from_config(config.default()['privacy'])


def build(*visits):
    return lineage.build(list(visits), CFG, PRIVACY)


def test_opener_then_from_is_parent():
    g = build(visit(1, 0, 'https://a.example/', 'A', core=1),
              visit(2, 5, 'https://b.example/', 'B', opener=1),
              visit(3, 9, 'https://c.example/', 'C', frm=2))
    assert g.roots == (1,)
    assert (g.nodes[2].parent, g.nodes[2].via) == (1, 'tab')
    assert (g.nodes[3].parent, g.nodes[3].via) == (2, 'same')


def test_reload_is_not_a_node_and_children_are_reattached():
    g = build(visit(1, 0, 'https://a.example/', 'A', core=1),
              visit(2, 5, 'https://a.example/', 'A', core=8, frm=1),
              visit(3, 9, 'https://c.example/', 'C', frm=2))
    assert set(g.nodes) == {1, 3}
    assert g.nodes[3].parent == 1
    assert g.stats['visits'] == 2


def test_same_tab_moves_inside_a_site_fold_into_one_node():
    g = build(visit(1, 0, 'https://map.example/a', 'Place A', core=1),
              visit(2, 5, 'https://map.example/b', 'Place B', frm=1),
              visit(3, 9, 'https://map.example/c', 'Place C', frm=2))
    assert list(g.nodes) == [1]
    assert len(g.nodes[1].times) == 3
    assert g.nodes[1].titles == ['Place B', 'Place C']
    assert g.stats['folded'] == 2


def test_new_tabs_inside_a_site_are_flattened_under_the_entry():
    g = build(visit(1, 0, 'https://hub.example/', 'Hub', core=1),
              visit(2, 5, 'https://wiki.example/1', 'Page one', opener=1),
              visit(3, 9, 'https://wiki.example/2', 'Page two', opener=2),
              visit(4, 12, 'https://wiki.example/3', 'Page three', opener=3))
    assert g.nodes[3].parent == 2
    assert g.nodes[4].parent == 2
    assert g.stats['flattened'] == 1
    assert [d for d, _ in lineage.walk(g, 1)] == [0, 1, 2, 2]


def test_back_navigation_is_an_alias():
    g = build(visit(1, 0, 'https://a.example/p', 'P', core=1),
              visit(2, 5, 'https://b.example/q', 'Q', frm=1),
              visit(3, 9, 'https://a.example/p', 'P', back=True, frm=2))
    assert set(g.nodes) == {1, 2}
    assert len(g.nodes[1].times) == 2
    assert g.stats['back'] == 1


def test_same_url_siblings_are_deduplicated():
    g = build(visit(1, 0, 'https://a.example/', 'A', core=1),
              visit(2, 5, 'https://b.example/x?utm=1', 'X', opener=1),
              visit(3, 9, 'https://b.example/x?utm=2', 'X', opener=1))
    assert g.nodes[1].kids == [2]
    assert g.nodes[2].url == 'https://b.example/x'
    assert g.stats['deduped'] == 1


def test_orphan_link_right_after_a_search_gets_the_search_as_parent():
    g = build(visit(1, 0, 'https://www.bing.com/search?q=pandas', 'pandas - Search', core=1, term='pandas'),
              visit(2, 10, 'https://pandas.example/', 'pandas docs'),
              visit(3, 200, 'https://other.example/', 'later'))
    assert g.nodes[1].kind == 'search' and g.nodes[1].term == 'pandas'
    assert (g.nodes[2].parent, g.nodes[2].via) == (1, 'search')
    assert g.nodes[3].parent is None


def test_dropped_page_is_removed_and_its_child_reattached():
    g = build(visit(1, 0, 'https://a.example/', 'A', core=1),
              visit(2, 5, 'https://accounts.google.com/signin', 'Sign in', frm=1),
              visit(3, 9, 'https://d.example/', 'D', frm=2))
    assert set(g.nodes) == {1, 3}
    assert g.nodes[3].parent == 1
    assert g.stats['dropped'] == 1


def test_masked_page_keeps_only_the_label():
    g = build(visit(1, 0, 'https://mail.google.com/mail/u/0/#inbox/abc', 'Re: private subject', core=1,
                    term='private'))
    n = g.nodes[1]
    assert (n.url, n.title, n.term, n.masked) == ('https://mail.google.com/', 'メール', None, True)


def test_query_is_removed_from_urls():
    g = build(visit(1, 0, 'https://a.example/p?session=secret#x', 'A', core=1))
    assert g.nodes[1].url == 'https://a.example/p'


def test_section_uses_fold_depth():
    assert CFG.section('https://github.com/owner/repo/pull/1') == 'github.com/owner/repo'
    assert CFG.section('https://a.example/x/y') == 'a.example'
