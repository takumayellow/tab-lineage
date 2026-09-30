import os

import pytest

from _util import make_history, visit
from tab_lineage import snapshot


def test_snapshot_copies_history_and_newest_session(tmp_path):
    profile = tmp_path / 'profile'
    (profile / 'Sessions').mkdir(parents=True)
    make_history(profile / 'History', [visit(1, 0, 'https://a.example/')])
    old, new = profile / 'Sessions' / 'Session_1', profile / 'Sessions' / 'Session_2'
    old.write_bytes(b'old')
    new.write_bytes(b'new')
    os.utime(old, (1, 1))
    out = snapshot.snapshot(profile, tmp_path / 'out')
    assert [p.name for p in out] == ['History.db', 'Session_2']


def test_broken_copy_is_retried_then_rejected(tmp_path):
    profile = tmp_path / 'profile'
    profile.mkdir()
    (profile / 'History').write_bytes(b'garbage' * 100)
    (tmp_path / 'out').mkdir()
    with pytest.raises(RuntimeError):
        snapshot.copy_history(profile, tmp_path / 'out', tries=2, wait=0)


def test_missing_history_and_unknown_browser(tmp_path):
    with pytest.raises(FileNotFoundError):
        snapshot.copy_history(tmp_path, tmp_path)
    with pytest.raises(ValueError):
        snapshot.default_profile('netscape')
    assert snapshot.default_profile('vivaldi', 'Profile 2').name == 'Profile 2'
