"""使用中のブラウザから History とセッションファイルをコピーする.

ブラウザは DB をロックしているので SQLite のバックアップ API は待たされて使えない.
ファイルをそのまま写し, 書き込み途中を写して壊れていたら少し待って取り直す.
"""
from __future__ import annotations

import os
import pathlib
import platform
import shutil
import time

from .histdb import is_intact

_WIN = {'vivaldi': 'Vivaldi/User Data', 'chrome': 'Google/Chrome/User Data',
        'edge': 'Microsoft/Edge/User Data', 'brave': 'BraveSoftware/Brave-Browser/User Data'}
_MAC = {'vivaldi': 'Vivaldi', 'chrome': 'Google/Chrome', 'edge': 'Microsoft Edge',
        'brave': 'BraveSoftware/Brave-Browser'}
_LINUX = {'vivaldi': 'vivaldi', 'chrome': 'google-chrome', 'edge': 'microsoft-edge',
          'brave': 'BraveSoftware/Brave-Browser'}
BROWSERS = tuple(_WIN)


def default_profile(browser: str = 'vivaldi', profile: str = 'Default') -> pathlib.Path:
    if browser not in _WIN:
        raise ValueError(f'未対応のブラウザ: {browser} (対応: {", ".join(BROWSERS)})')
    system = platform.system()
    if system == 'Windows':
        base = pathlib.Path(os.environ.get('LOCALAPPDATA', pathlib.Path.home() / 'AppData/Local')) / _WIN[browser]
    elif system == 'Darwin':
        base = pathlib.Path.home() / 'Library/Application Support' / _MAC[browser]
    else:
        base = pathlib.Path(os.environ.get('XDG_CONFIG_HOME', pathlib.Path.home() / '.config')) / _LINUX[browser]
    return base / profile


def copy_history(profile: pathlib.Path, out_dir: pathlib.Path, tries: int = 3, wait: float = 2.0) -> pathlib.Path:
    src = profile / 'History'
    if not src.is_file():
        raise FileNotFoundError(f'History が見つからない: {src}')
    dst = out_dir / 'History.db'
    for attempt in range(1, tries + 1):
        shutil.copyfile(src, dst)
        if is_intact(dst):
            return dst
        if attempt < tries:
            time.sleep(wait)
    raise RuntimeError(f'History のコピーが {tries} 回とも壊れていた')


def copy_latest_session(profile: pathlib.Path, out_dir: pathlib.Path) -> pathlib.Path | None:
    """読めるうち一番新しいセッションファイルをコピーする. 使用中の最新ファイルは読めないことがある."""
    sessions = sorted((profile / 'Sessions').glob('Session_*'), key=lambda p: p.stat().st_mtime, reverse=True)
    for s in sessions:
        try:
            return pathlib.Path(shutil.copy2(s, out_dir / s.name))
        except OSError:
            continue
    return None


def snapshot(profile: pathlib.Path, out_dir: pathlib.Path) -> list[pathlib.Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    copied = [copy_history(profile, out_dir)]
    session = copy_latest_session(profile, out_dir)
    return copied + ([session] if session else [])
