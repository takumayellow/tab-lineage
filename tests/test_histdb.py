import sqlite3

import pytest

from _util import at, make_history, visit
from tab_lineage import histdb


def test_load_visits_reads_flags_and_terms(tmp_path):
    db = tmp_path / 'History.db'
    make_history(db, [
        visit(1, 0, 'https://www.bing.com/search?q=x', 'x - Search', core=1, term='x', dur=5),
        visit(2, 10, 'https://a.example/', 'A', opener=1),
        visit(3, 20, 'https://www.bing.com/search?q=x', 'x - Search', back=True, frm=2),
    ])
    vs = histdb.load_visits([db])
    assert [v.id for v in vs] == [1, 2, 3]
    assert vs[0].core == 1 and vs[0].term == 'x' and vs[0].dur == 5
    assert vs[1].opener == 1
    assert vs[2].back and vs[2].core == 0 and vs[2].frm == 2


def test_missing_opener_column_falls_back_to_zero(tmp_path):
    db = tmp_path / 'old.db'
    make_history(db, [visit(1, 0, 'https://a.example/', opener=5)], opener_column=False)
    assert histdb.load_visits([db])[0].opener == 0


def test_later_snapshot_wins_and_order_is_by_time(tmp_path):
    a, b = tmp_path / 'a.db', tmp_path / 'b.db'
    make_history(a, [visit(1, 30, 'https://a.example/', 'old'), visit(2, 0, 'https://b.example/', 'B')])
    make_history(b, [visit(1, 30, 'https://a.example/', 'new')])
    vs = histdb.load_visits([a, b])
    assert [(v.id, v.title) for v in vs] == [(2, 'B'), (1, 'new')]


def test_open_ro_refuses_writes(tmp_path):
    db = tmp_path / 'History.db'
    make_history(db, [visit(1, 0, 'https://a.example/')])
    c = histdb.open_ro(db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            c.execute('DELETE FROM visits')
    finally:
        c.close()


def test_is_intact(tmp_path):
    good, bad = tmp_path / 'good.db', tmp_path / 'bad.db'
    make_history(good, [visit(1, 0, 'https://a.example/')])
    bad.write_bytes(b'not a database' * 100)
    assert histdb.is_intact(good)
    assert not histdb.is_intact(bad)


def test_unix_s():
    assert histdb.unix_s(at(0)) == 1_790_000_000


def test_reading_releases_the_file(tmp_path):
    # 接続を閉じ忘れると, Windows では読んだあとのコピーを消せない
    db = tmp_path / 'History.db'
    make_history(db, [visit(1, 0, 'https://a.example/')])
    histdb.load_visits([db])
    histdb.is_intact(db)
    db.unlink()
    assert not db.exists()
