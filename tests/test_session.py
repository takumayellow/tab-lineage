import struct

from _util import nav, record, snss
from tab_lineage import session


def test_open_tabs_in_tab_bar_order():
    data = snss(
        nav(10, 0, 'https://a.example/', 'A'),
        nav(11, 0, 'https://b.example/', 'B'),
        nav(12, 0, 'https://c.example/', 'C'),
        record(0, struct.pack('<ii', 1, 10)), record(0, struct.pack('<ii', 1, 11)),
        record(0, struct.pack('<ii', 1, 12)),
        record(2, struct.pack('<ii', 10, 1)), record(2, struct.pack('<ii', 11, 0)),
        record(16, struct.pack('<i', 12)),
    )
    tabs = session.parse(data)
    assert [(t.tab, t.url, t.window, t.index) for t in tabs] == [
        (11, 'https://b.example/', 1, 0), (10, 'https://a.example/', 1, 1)]


def test_selected_navigation_and_back_list():
    data = snss(
        nav(1, 0, 'https://a.example/0', 'zero'),
        nav(1, 1, 'https://a.example/1', 'いち'),
        nav(1, 2, 'https://a.example/2', 'two'),
        record(7, struct.pack('<ii', 1, 1)),
    )
    (tab,) = session.parse(data)
    assert tab.url == 'https://a.example/1'
    assert tab.title == 'いち'
    assert tab.back == ('https://a.example/0',)


def test_last_navigation_when_nothing_selected():
    (tab,) = session.parse(snss(nav(1, 0, 'https://a.example/0', 'a'), nav(1, 1, 'https://a.example/1', 'b')))
    assert tab.url == 'https://a.example/1'


def test_windows_keep_first_seen_order_and_unknown_index_goes_last():
    data = snss(
        nav(1, 0, 'https://w2.example/', 'x'), nav(2, 0, 'https://w1.example/', 'y'),
        nav(3, 0, 'https://w2.example/b', 'z'),
        record(0, struct.pack('<ii', 9, 1)), record(0, struct.pack('<ii', 4, 2)),
        record(0, struct.pack('<ii', 9, 3)), record(2, struct.pack('<ii', 3, 0)),
    )
    assert [t.tab for t in session.parse(data)] == [3, 1, 2]


def test_truncated_records_are_skipped():
    data = snss(nav(1, 0, 'https://a.example/', 'a'), record(7, b'\x01'))
    assert [t.url for t in session.parse(data)] == ['https://a.example/']
    assert session.parse(b'SNSS\x03\0\0\0') == []


def test_load_reads_file(tmp_path):
    p = tmp_path / 'Session_1'
    p.write_bytes(snss(nav(1, 0, 'https://a.example/', 'a')))
    assert session.load(p)[0].title == 'a'
