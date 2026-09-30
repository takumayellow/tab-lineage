"""題名から話題の近さを測る. 日本語は文字 bigram, 英数字は単語を使い, TF-IDF の余弦で比べる."""
from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable

_SEP = r'[-|–—｜:：・]'
_SUFFIX = re.compile(rf'\s+{_SEP}\s+[^-|–—｜]{{1,40}}$|\s*[|｜]\s*[^|｜]{{1,40}}$')
_WORD = re.compile(r'[a-z0-9][a-z0-9+#]*(?:[._-][a-z0-9+#]+)*')
_CJK = re.compile(r'[぀-ヿ㐀-鿿豈-﫿ｦ-ﾟ々ー]+')
_STOP = frozenset('''
a an and are as at be by com for from how in is it of on or the to what why with www http https
you your this that de la le der die das und
'''.split())

Vector = dict[str, float]


def clean_title(title: str) -> str:
    """末尾のサイト名 (' - YouTube', ' | Qiita' など) を 1 つ落とす. 残りが短すぎるなら元のまま."""
    title = ' '.join(title.split())
    stripped = _SUFFIX.sub('', title)
    return stripped if len(stripped) >= 4 else title


def tokens(text: str) -> list[str]:
    text = text.lower()
    words = [w for w in _WORD.findall(text) if len(w) >= 2 and w not in _STOP and not w.isdigit()]
    grams = []
    for run in _CJK.findall(text):
        grams.extend(run[i:i + 2] for i in range(len(run) - 1))
    return words + grams


class Vectorizer:
    """文書集合から IDF を作り, 語の袋を正規化した TF-IDF ベクトルにする."""

    def __init__(self, docs: Iterable[Iterable[str]]):
        df: Counter[str] = Counter()
        n = 0
        for doc in docs:
            df.update(set(doc))
            n += 1
        self.idf = {t: math.log((1 + n) / (1 + c)) + 1 for t, c in df.items()}
        self.default_idf = math.log(1 + n) + 1

    def vector(self, toks: Iterable[str]) -> Vector:
        tf = Counter(toks)
        v = {t: (1 + math.log(c)) * self.idf.get(t, self.default_idf) for t, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values()))
        return {t: x / norm for t, x in v.items()} if norm else {}


def cosine(a: Vector, b: Vector) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(t, 0.0) for t, x in a.items())


def add(a: Vector, b: Vector) -> Vector:
    """2 つのベクトルを足して正規化し直した新しいベクトル."""
    s = dict(a)
    for t, x in b.items():
        s[t] = s.get(t, 0.0) + x
    norm = math.sqrt(sum(x * x for x in s.values()))
    return {t: x / norm for t, x in s.items()} if norm else {}
