import re

from tab_lineage import config, text
from tab_lineage.privacy import Privacy, clean_url, host_of, scrub_title, site_key


def test_site_key_and_clean_url():
    assert site_key('https://User:pw@Example.COM:8080/a/b?q=1#x') == 'example.com:8080/a/b'
    assert site_key('file:///C:/Users/me/secret/notes.pdf') == 'file:/notes.pdf'
    assert clean_url('https://user:pw@a.example/p?token=abc#frag') == 'https://a.example/p'
    assert clean_url('https://a.example/p?x=1', keep_query=True) == 'https://a.example/p?x=1'
    assert clean_url('file:///C:/Users/me/secret/notes.pdf') == 'file:///notes.pdf'
    assert clean_url('chrome://settings/passwords') == 'chrome:'
    assert host_of('vivaldi://history') == 'vivaldi'


def test_clean_term_drops_personal_looking_terms():
    p = Privacy(drop_terms=(re.compile('secret'),), term_max=20)
    assert p.clean_term('  pandas   merge ') == 'pandas merge'
    assert p.clean_term('me@example.com') is None
    assert p.clean_term('090-1234-5678') is None
    assert p.clean_term('https://a.example') is None
    assert p.clean_term('my secret') is None
    assert p.clean_term('x' * 21) is None
    assert p.clean_term(None) is None


def test_default_privacy_drops_login_and_masks_mail():
    p = Privacy.from_config(config.default()['privacy'])
    assert p.dropped('https://accounts.google.com/signin', 'Sign in')
    assert p.dropped('https://a.example/', 'Just a moment...')
    assert not p.dropped('https://a.example/', 'Just a moment... more')
    assert p.dropped('https://www.google.com/url?url=https%3A%2F%2Fshop.example%2F', 'Shop')
    assert p.mask_of('https://mail.google.com/mail/u/0/#inbox/abc') == ('メール', 'mail.google.com')
    assert p.mask_of('https://www.google.com/maps/place/somewhere') == ('地図', 'www.google.com')
    assert p.mask_of('https://a.example/') is None


def test_mask_with_a_wildcard_host_hides_the_subdomain():
    p = Privacy(mask=(('*.client.example/*', '取引先'), ('*.slack.*/*', 'Slack')))
    assert p.mask_of('https://project-x.stg.client.example/admin') == ('取引先', 'client.example')
    assert p.mask_of('https://acme.slack.com/') == ('Slack', 'acme.slack.com')


def test_merge_is_recursive_and_does_not_mutate():
    base = {'a': {'x': 1, 'y': 2}, 'b': 1}
    out = config._merge(base, {'a': {'y': 3}, 'c': 4})
    assert out == {'a': {'x': 1, 'y': 3}, 'b': 1, 'c': 4}
    assert base == {'a': {'x': 1, 'y': 2}, 'b': 1}


def test_load_config_and_labels(tmp_path):
    cfg = tmp_path / 'c.toml'
    cfg.write_text('[episodes]\ngap_minutes = 45\n', encoding='utf-8')
    assert config.load(cfg)['episodes'] == {'gap_minutes': 45, 'stale_hours': 12, 'ws_share': 0.5,
                                                 'ws_cover': 0.1, 'ws_infer_days': 7}
    labels = tmp_path / 'l.toml'
    labels.write_text('[[episode]]\nid = "2026-09-01T10:00"\ntitle = "t"\nhide = true\n', encoding='utf-8')
    assert config.load_labels(labels)['2026-09-01T10:00']['hide'] is True
    assert config.load_labels(None) == {}


def test_clean_title():
    assert text.clean_title('How to merge - YouTube') == 'How to merge'
    assert text.clean_title('pandas merge | Qiita') == 'pandas merge'
    assert text.clean_title('ab - YouTube') == 'ab - YouTube'


def test_tokens_mix_words_and_bigrams():
    toks = text.tokens('The pandas 結合する 2026')
    assert 'pandas' in toks and 'the' not in toks and '2026' not in toks
    assert toks.count('結合') == 1 and '合す' in toks


def test_vectors_and_cosine():
    docs = [text.tokens('pandas merge'), text.tokens('pandas join'), text.tokens('エアコン 掃除')]
    v = text.Vectorizer(docs)
    a, b, c = (v.vector(d) for d in docs)
    assert abs(text.cosine(a, a) - 1) < 1e-9
    assert text.cosine(a, b) > 0
    assert text.cosine(a, c) == 0
    s = text.add(a, c)
    assert abs(sum(x * x for x in s.values()) - 1) < 1e-9
    assert v.vector([]) == {}


def test_clean_url_scrubs_tokens_and_addresses_in_the_path():
    assert clean_url('https://docs.google.com/document/d/1aBcD3fGhIjKlMnOpQrStUvWxYz0123456789/edit') == \
        'https://docs.google.com/document/d/…/edit'
    assert clean_url('https://a.example/reset/9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08') == \
        'https://a.example/reset/…'
    assert clean_url('https://a.example/u/someone@example.com/') == 'https://a.example/u/…/'
    assert clean_url('https://app.box.com/s/k3f8q2zm7xw1n0pvt5yr9bdh4gc6sj0a') == 'https://app.box.com/s/…'
    assert clean_url('https://shop.example/orders/250930123456789/') == 'https://shop.example/orders/…/'
    # 普通のパスは残す
    assert clean_url('https://github.com/takumayellow/tab-lineage/pull/12') == \
        'https://github.com/takumayellow/tab-lineage/pull/12'
    assert clean_url('https://qiita.com/someone/items/0123abcd4567ef89') == 'https://qiita.com/someone/items/0123abcd4567ef89'
    assert clean_url('https://a.example/i/abcdefghijklmnopqrstuvw1') == 'https://a.example/i/…'


def test_labels_without_an_id_are_reported(tmp_path):
    import pytest
    labels = tmp_path / 'labels.toml'
    labels.write_text('[[episode]]\ntitle = "名前だけ"\n', encoding='utf-8')
    with pytest.raises(ValueError, match='1 番目'):
        config.load_labels(labels)


def test_a_url_used_as_the_title_loses_its_query():
    assert scrub_title('startpage.com/do/search?q=my+secret&segment=x') == 'startpage.com/do/search'
    assert scrub_title('https://a.example/reset/9f86d081884c7d659a2feaa0c55ad015#t') == 'a.example/reset/…'
    assert scrub_title('Node.js') == 'Node.js'
    assert scrub_title('pandas merge | Qiita') == 'pandas merge | Qiita'


def test_share_links_are_masked_by_default():
    p = Privacy.from_config(config.default()['privacy'])
    assert p.mask_of('https://app.box.com/s/abcdefghijklmnopqrstuvwxyzabcdef') == ('共有リンク', 'box.com')
    assert p.mask_of('https://www.dropbox.com/scl/fi/xyz/a.pdf') == ('共有リンク', 'www.dropbox.com')
