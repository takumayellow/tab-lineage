"""設定 (TOML) を読む. 既定値に利用者のファイルを重ねる."""
from __future__ import annotations

import pathlib
import tomllib
from importlib import resources


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def default() -> dict:
    return tomllib.loads(resources.files(__package__).joinpath('data/default.toml').read_text(encoding='utf-8'))


def load(path: str | pathlib.Path | None = None) -> dict:
    cfg = default()
    if path:
        with open(path, 'rb') as f:
            cfg = _merge(cfg, tomllib.load(f))
    return cfg


def load_labels(path: str | pathlib.Path | None) -> dict:
    """回とスレッドに人が付けた名前. [[episode]] id, title, note, featured, hide, threads = {根の ID = 名前}."""
    if not path:
        return {}
    with open(path, 'rb') as f:
        data = tomllib.load(f)
    entries = data.get('episode', ())
    for k, e in enumerate(entries, 1):
        if 'id' not in e:
            raise ValueError(f'{path}: {k} 番目の [[episode]] に id がありません')
    return {e['id']: e for e in entries}
