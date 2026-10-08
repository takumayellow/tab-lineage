import datetime as dt

from _util import visit
from tab_lineage import config, episodes, lineage
from tab_lineage.histdb import US
from tab_lineage.privacy import Privacy

UTC = dt.timezone.utc


def timeline(visits, extra: dict | None = None):
    cfg = config._merge(config.default(), extra or {})
    g = lineage.build(visits, lineage.LineageConfig.from_config(cfg))
    return g, episodes.build(g, episodes.EpisodeConfig.from_config(cfg), UTC)


def test_idle_gap_splits_episodes_and_records_continuation():
    g, tl = timeline([
        visit(1, 0, 'https://a.example/', 'Alpha', core=1),
        visit(2, 60, 'https://b.example/', 'Beta', opener=1),
        visit(3, 3 * 3600, 'https://c.example/', 'Gamma', opener=2),
    ])
    assert [len(e.nodes) for e in tl.episodes] == [2, 1]
    first, second = tl.episodes
    assert first.id == dt.datetime.fromtimestamp(1_790_000_000, UTC).strftime('%Y-%m-%dT%H:%M')
    assert second.cont == (first.id,)
    assert tl.tz_offsets == ((1_790_000_000, 0),)


def test_offsets_follow_daylight_saving_time():
    from zoneinfo import ZoneInfo
    ny = ZoneInfo('America/New_York')
    t0 = 1_790_000_000
    summer = dt.datetime(2026, 9, 25, 12, tzinfo=ny).timestamp() - t0
    winter = dt.datetime(2026, 11, 10, 12, tzinfo=ny).timestamp() - t0
    g = lineage.build([visit(1, summer, 'https://a.example/', 'A'), visit(2, winter, 'https://b.example/', 'B')],
                      lineage.LineageConfig.from_config(config.default()))
    tl = episodes.build(g, episodes.EpisodeConfig.from_config(config.default()), ny)
    assert [e.id for e in tl.episodes] == ['2026-09-25T12:00', '2026-11-10T12:00']
    assert [o for _, o in tl.tz_offsets] == [-240, -300]


def test_stale_pages_are_marked():
    _, tl = timeline([visit(1, 0, 'https://a.example/', 'A', core=1, dur=13 * 3600 * US),
                      visit(2, 5, 'https://b.example/', 'B', core=1, dur=60 * US)])
    assert tl.stale == {1: 0.5}


def test_workspace_by_majority():
    ws = {'workspace': [{'name': 'Work', 'match': ['work.example/*']}]}
    _, tl = timeline([visit(1, 0, 'https://work.example/a', 'Report', core=1),
                      visit(2, 5, 'https://work.example/b', 'Report two', opener=1),
                      visit(3, 9, 'https://news.example/', 'News', core=1)], ws)
    (ep,) = tl.episodes
    assert ep.workspace == 'Work'
    assert tl.workspace == {1: 'Work', 2: 'Work'}


def _one_episode(n_other: int, matched: list[str]):
    ws = {'workspace': [{'name': w, 'match': [f'{w.lower()}.example/*']} for w in ('Work', 'Home', 'School')]}
    vs = [visit(k + 1, 5 * k, f'https://{w.lower()}.example/{k}', f'{w} {k}', core=1) for k, w in enumerate(matched)]
    vs += [visit(100 + k, 5 * (len(vs) + k), f'https://news.example/{k}', f'News {k}', core=1) for k in range(n_other)]
    (ep,) = timeline(vs, ws)[1].episodes
    return ep.workspace


def test_visits_without_a_rule_do_not_dilute_the_vote():
    assert _one_episode(4, ['Work']) == 'Work'


def test_workspace_needs_rules_to_cover_some_of_the_visits():
    assert _one_episode(10, ['Work']) is None


def test_workspace_needs_half_of_the_matched_visits():
    assert _one_episode(0, ['Work', 'Home', 'Home', 'School']) == 'Home'
    assert _one_episode(0, ['Work', 'Home', 'School']) is None


DAY = 86400
INFER_WS = {'workspace': [{'name': 'Work', 'match': ['work.example/*']}]}


def test_a_thread_without_a_workspace_takes_it_from_a_similar_thread_on_a_nearby_day():
    _, tl = timeline([
        visit(1, 0, 'https://work.example/a', 'pandas merge dataframe', core=1),
        visit(2, 2 * DAY, 'https://blog.example/a', 'pandas merge guide', core=1),
        visit(3, 2 * DAY + 60, 'https://kaden.example/', 'エアコン カビ 掃除', core=1),
        visit(4, 20 * DAY, 'https://blog.example/b', 'pandas merge dataframe howto', core=1),
    ], INFER_WS)
    first, second, far = tl.episodes
    assert second.workspace is None
    got = {t.label: t.workspace for t in second.threads}
    assert got == {'pandas merge guide': 'Work', 'エアコン カビ 掃除': None}
    assert [t.workspace for t in far.threads] == [None]


def test_inferring_from_other_episodes_can_be_turned_off():
    _, tl = timeline([
        visit(1, 0, 'https://work.example/a', 'pandas merge dataframe', core=1),
        visit(2, 2 * DAY, 'https://blog.example/a', 'pandas merge dataframe tutorial', core=1),
    ], INFER_WS | {'episodes': {'ws_infer_days': 0}})
    assert [t.workspace for t in tl.episodes[1].threads] == [None]


def test_inferred_workspaces_are_not_passed_on():
    _, tl = timeline([
        visit(1, 0, 'https://work.example/a', 'pandas merge dataframe', core=1),
        visit(2, 6 * DAY, 'https://blog.example/a', 'pandas merge dataframe tutorial', core=1),
        visit(3, 12 * DAY, 'https://blog.example/b', 'pandas merge dataframe tutorial', core=1),
    ], INFER_WS)
    assert [t.workspace for e in tl.episodes for t in e.threads] == ['Work', 'Work', None]


def test_unrelated_roots_become_separate_threads_and_same_site_roots_join():
    _, tl = timeline([
        visit(1, 0, 'https://docs.example/pandas', 'pandas merge dataframe', core=1),
        visit(2, 30, 'https://kaden.example/', 'エアコン カビ 掃除', core=1),
        visit(3, 60, 'https://docs.example/numpy', 'numpy array broadcasting', core=1),
    ])
    (ep,) = tl.episodes
    assert sorted(len(t.roots) for t in ep.threads) == [1, 2]
    assert ep.label


def test_off_topic_branch_is_a_detour():
    _, tl = timeline([
        visit(1, 0, 'https://docs.example/merge', 'pandas dataframe merge', core=1),
        visit(2, 10, 'https://pydata.example/join', 'pandas dataframe join', opener=1),
        visit(3, 20, 'https://qa.example/merge-error', 'pandas dataframe merge error', opener=1),
        visit(4, 30, 'https://blog.example/groupby', 'pandas dataframe groupby', opener=1),
        visit(5, 40, 'https://aircon.example/', 'エアコン カビ 掃除', opener=4),
        visit(6, 50, 'https://kaden.example/', 'エアコン カビ 対策', opener=5),
    ])
    assert tl.detours == frozenset({5})


def test_too_many_threads_go_to_other():
    titles = ['alpha river', 'bravo mountain', 'charlie ocean', 'delta forest']
    _, tl = timeline([visit(k + 1, k * 10, f'https://s{k}.example/', t, core=1) for k, t in enumerate(titles)],
                     {'threads': {'max_lanes': 3}})
    (ep,) = tl.episodes
    assert len(ep.threads) == 3
    assert episodes.OTHER in [t.label for t in ep.threads]


def test_thread_members_cover_every_node():
    g, tl = timeline([visit(1, 0, 'https://a.example/', 'A', core=1),
                      visit(2, 5, 'https://b.example/', 'B', opener=1)])
    (ep,) = tl.episodes
    assert set(episodes.thread_members(g, ep)) == set(ep.nodes)


def test_masked_page_is_still_classified_by_its_real_url():
    extra = {'workspace': [{'name': 'Work', 'match': ['git.example/org/*']}],
             'privacy': {'mask': {'git.example/org/*': 'Work repo'}}}
    cfg = config._merge(config.default(), extra)
    g = lineage.build([visit(1, 0, 'https://git.example/org/secret-repo', 'Secret PR', core=1)],
                      lineage.LineageConfig.from_config(cfg), Privacy.from_config(cfg['privacy']))
    tl = episodes.build(g, episodes.EpisodeConfig.from_config(cfg), UTC)
    assert g.nodes[1].url == 'https://git.example/' and g.nodes[1].title == 'Work repo'
    assert tl.workspace == {1: 'Work'}


def test_masked_page_keeps_its_real_titles_out_of_the_thread_name():
    extra = {'privacy': {'mask': {'git.example/org/*': 'Work repo'}}}
    cfg = config._merge(config.default(), extra)
    g = lineage.build([visit(1, 0, 'https://git.example/org/a', 'Confidential merger plan', core=1),
                       visit(2, 60, 'https://git.example/org/b', 'Confidential merger memo', core=1)],
                      lineage.LineageConfig.from_config(cfg), Privacy.from_config(cfg['privacy']))
    tl = episodes.build(g, episodes.EpisodeConfig.from_config(cfg), UTC)
    names = [tl.episodes[0].label] + [t.label for t in tl.episodes[0].threads]
    assert not any('onfidential' in s or 'merger' in s for s in names)
