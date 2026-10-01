"""出力に載せない訪問を決める.

- drop:        一致した訪問を消す (子は消した訪問の親につなぎ直す). ログイン画面や決済画面向け.
- mask:        一致した訪問の題名とパスを伏せ, 決めた名前だけ残す. メールや DM 向け.
               パターンの host が '*.' で始まるなら, host もその後ろの部分だけ残す.
- drop_titles: 題名がこの正規表現に一致した訪問を消す.
- drop_terms:  検索語がこの正規表現に一致したら検索語だけ消す. メールアドレスや長い数字は常に消す.
URL のクエリとフラグメントは keep_query が真でない限り常に落とす.
パスのうち, トークンや文書 ID らしい部分とメールアドレスは '…' に置き換える.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

_ALWAYS_DROP_TERMS = (
    re.compile(r'[\w.+-]+@[\w-]+\.[\w.]+'),          # メールアドレス
    re.compile(r'\d[\d\s-]{6,}\d'),                  # 電話番号や口座番号らしい数字の並び
    re.compile(r'https?://', re.I),
)
# パスの 1 区切りがこれに一致したら伏せる. 大文字・小文字・数字の混じった長い文字列 (トークン,
# Google ドキュメントの ID など), 区切りの無い英小文字と数字の長い並び (Box の共有リンクなど),
# 長い 16 進 (ハッシュ), 長い数字 (注文番号など), メールアドレス.
_SECRET_SEGMENT = re.compile(
    r'(?=[\w-]*[a-z])(?=[\w-]*[A-Z])(?=[\w-]*\d)[\w-]{20,}'
    r'|(?=[a-z0-9]*\d)(?=[a-z0-9]*[a-z])[a-z0-9]{24,}'
    r'|[0-9a-fA-F]{32,}|\d{12,}|[^/]*@[^/]*\.[^/]*'
)


@dataclass(frozen=True)
class Privacy:
    drop: tuple[str, ...] = ()
    mask: tuple[tuple[str, str], ...] = ()
    drop_titles: tuple[re.Pattern, ...] = ()
    drop_terms: tuple[re.Pattern, ...] = ()
    keep_query: bool = False
    term_max: int = 40

    @classmethod
    def from_config(cls, cfg: dict) -> 'Privacy':
        return cls(
            drop=tuple(cfg.get('drop', ())),
            mask=tuple((k, v) for k, v in cfg.get('mask', {}).items()),
            drop_titles=tuple(re.compile(p, re.I) for p in cfg.get('drop_titles', ())),
            drop_terms=tuple(re.compile(p, re.I) for p in cfg.get('drop_terms', ())),
            keep_query=bool(cfg.get('keep_query', False)),
            term_max=int(cfg.get('term_max', 40)),
        )

    def dropped(self, url: str, title: str) -> bool:
        key = site_key(url)
        return any(fnmatch.fnmatchcase(key, p) for p in self.drop) or any(r.search(title) for r in self.drop_titles)

    def mask_of(self, url: str) -> tuple[str, str] | None:
        """一致した mask の (名前, 載せる host). どれにも一致しなければ None."""
        key = site_key(url)
        for p, label in self.mask:
            if fnmatch.fnmatchcase(key, p):
                pat_host = p.split('/', 1)[0]
                host = pat_host[2:] if pat_host.startswith('*.') and '*' not in pat_host[2:] else host_of(url)
                return label, host
        return None

    def clean_term(self, term: str | None) -> str | None:
        if not term:
            return None
        term = ' '.join(term.split())
        if len(term) > self.term_max or any(r.search(term) for r in _ALWAYS_DROP_TERMS + self.drop_terms):
            return None
        return term


def site_key(url: str) -> str:
    """照合に使う 'host/path' (クエリなし, 小文字の host). file: などは 'file:/<名前>'."""
    s = urlsplit(url)
    if s.scheme in ('http', 'https'):
        return (s.hostname or '') + (f':{s.port}' if s.port else '') + (s.path or '/')
    return f'{s.scheme}:/' + s.path.rsplit('/', 1)[-1]


def clean_url(url: str, keep_query: bool = False) -> str:
    """ユーザー名・パスワード・クエリ・フラグメントを落とす. ローカルファイルはファイル名だけ残す."""
    s = urlsplit(url)
    if s.scheme in ('http', 'https'):
        netloc = (s.hostname or '') + (f':{s.port}' if s.port else '')
        return urlunsplit((s.scheme, netloc, scrub_path(s.path), s.query if keep_query else '', ''))
    if s.scheme == 'file':
        return 'file:///' + s.path.rsplit('/', 1)[-1]
    return f'{s.scheme}:'


_URL_TITLE = re.compile(r'(?:https?://)?(?:[\w-]+\.)+[a-z]{2,}(?::\d+)?[/?#]\S*', re.I)


def scrub_title(title: str) -> str:
    """題名の無いページは URL が題名になる. その形の題名はクエリを落としパスを伏せる."""
    t = title.strip()
    if not _URL_TITLE.fullmatch(t):
        return title
    return clean_url(t if '://' in t else 'https://' + t).split('://', 1)[1]


def scrub_path(path: str) -> str:
    return '/'.join('…' if _SECRET_SEGMENT.fullmatch(seg) else seg for seg in path.split('/'))


def host_of(url: str) -> str:
    s = urlsplit(url)
    if s.scheme in ('http', 'https'):
        return (s.hostname or '') + (f':{s.port}' if s.port else '')
    return s.scheme
