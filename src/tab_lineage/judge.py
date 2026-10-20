"""AI (Claude Code の `claude -p`) に, タブを置くワークスペース・まとめるスタックの名前・片付け方を決めさせる.

規則 (URL と題名) と開いた流れで作った案の上に重ねる. 規則は言葉に引きずられる (「競プロのための高速フーリエ変換」を
競プロへ入れる) ので, 授業の範囲など利用者の背景を渡して, 中身が何に使われているかで決めさせる.
判定は URL ごとに judge.json へ覚え, まだ判定していないタブ (と古くなった判定) があるときだけ呼ぶ.
背景かワークスペースの一覧が変わったら全部判定し直す. 呼べないとき (使用の上限・時間切れ) は規則の案のまま進め,
次に呼ぶのは BACKOFF 秒あと. 伏せるページ (メール・SNS・AI との会話など) は送らない.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

TTL = 3 * 86400        # この秒数より前の判定は, 古くなったかもしれないので判定し直す
BACKOFF = 3600
BATCH = 80             # 1 回に送るタブの数
CONTEXT_MAX = 8000
CONTEXT_SUFFIXES = ('.md', '.txt')  # 背景として AI に送ってよいのは文書だけ (鍵や設定を誤って送らない)     # 背景のファイル 1 つから送る文字数
STACK_MAX = 12         # スタックの名前の字数. タブバーには先頭しか見えない
NOWIN = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
ACTIONS = ('keep', 'later', 'done')   # 残す / あとで読むへ移す / 閉じる


@dataclass(frozen=True)
class JudgeConfig:
    enabled: bool = False
    model: str = 'haiku'
    context: tuple[str, ...] = ()     # 背景のファイル (glob 可. 相対パスは設定のファイルから)
    timeout: int = 600
    config_dir: str | None = None     # 使う Claude のアカウントの設定ディレクトリ (CLAUDE_CONFIG_DIR). 無ければ既定

    @classmethod
    def from_config(cls, cfg: dict, base: pathlib.Path | None = None) -> 'JudgeConfig':
        j = cfg.get('judge', {})
        root = base or pathlib.Path.cwd()
        paths = []
        for p in j.get('context', ()):
            p = os.path.expanduser(p)
            found = sorted(glob.glob(p if os.path.isabs(p) else str(root / p))) or [p]
            paths += [f for f in found if pathlib.Path(f).suffix.lower() in CONTEXT_SUFFIXES]
        config_dir = j.get('config_dir')
        return cls(bool(j.get('enabled', False)), j.get('model', 'haiku'), tuple(paths), int(j.get('timeout', 600)),
                   os.path.expanduser(config_dir) if config_dir else None)

    def background(self) -> str:
        parts = []
        for p in self.context:
            try:
                text = pathlib.Path(p).read_text(encoding='utf-8')
            except OSError:
                continue
            parts.append(f'## {p}\n{text[:CONTEXT_MAX]}')
        return '\n\n'.join(parts)


def load(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def save(path: pathlib.Path, data: dict) -> None:
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    tmp.replace(path)


def basis(workspaces: list[str], background: str) -> str:
    return hashlib.sha256(json.dumps([workspaces, background], ensure_ascii=False).encode()).hexdigest()[:16]


def verdicts(data: dict, key: str, now: float | None = None) -> dict[str, dict]:
    """覚えている判定のうち, 今の背景で TTL 以内のもの. URL (page_key) -> {workspace, stack, action}."""
    if data.get('basis') != key:
        return {}
    now = time.time() if now is None else now
    return {u: v for u, v in data.get('tabs', {}).items()
            if isinstance(v, dict) and isinstance(v.get('workspace'), str) and v.get('action') in ACTIONS
            and isinstance(v.get('at'), (int, float)) and now - v['at'] < TTL}


def schema(workspaces: list[str]) -> dict:
    return {
        'type': 'object', 'required': ['tabs'],
        'properties': {'tabs': {'type': 'array', 'items': {
            'type': 'object', 'required': ['id', 'workspace', 'stack', 'action'],
            'properties': {
                'id': {'type': 'integer'},
                'workspace': {'type': 'string', 'enum': workspaces},
                'stack': {'type': ['string', 'null']},
                'action': {'type': 'string', 'enum': list(ACTIONS)},
            }}}},
    }


PROMPT = '''ブラウザ (Vivaldi) の開いているタブを整理する. 「判定するタブ」の各タブについて, 置くワークスペース (workspace),
まとめるスタックの名前 (stack), 片付け方 (action) を決め, JSON で返す.

決め方:
- ワークスペースは, 題名や開いた流れに出てくる言葉ではなく, そのページが利用者の何の用事に使われているかで決める.
  例: 「競プロのための高速フーリエ変換」でも, 利用者が今期の授業でフーリエ変換を習っているなら, その科目の予習・復習として大学に置く.
  背景にある授業の計画と照らし, 授業の範囲の内容ならその科目として扱う.
- stack は, 同じ用事のタブをまとめるスタックの名前. タブバーには先頭の 2〜4 字しか見えないので, {max} 字以内で,
  先頭の数文字で何のまとまりか分かるようにする. 決まった略称が背景にあればそれを使う. 例: 「応数B DFT」「実験2 T4」「ABC480」「OOP 継承」.
  同じ用事のタブには必ず同じ名前を付け, まとめる相手が無い 1 枚だけのタブは null. 「判定済みのタブ」の名前と合うものはその名前を使う.
  「そのほか」「Home」「資料」のような何でも入る名前や, 同じ先頭で始まる紛らわしい名前は付けない.
- action:
  - keep: 今の用事で使っている, またはすぐ使う.
  - later: 今の用事ではないが残す価値がある読みもの・調べもの・買い物の候補. 長く見ていない授業の外の記事もここ. ブックマークへ移して閉じる.
  - done: 用が済んで残す価値が無い. 提出・申し込みの完了画面, エラーのページ, ログインの画面, 中身の無いトップページ,
    同じ内容の別のページで足りるもの, 終わったコンテストの自分の提出の結果など. 迷ったら later にする.
- 最後に見てからの日数 (days) が大きいほど later・done に寄せる. ただし授業の資料・課題・提出先・今期の科目のページは, 学期中は keep.

ワークスペース (この中から選ぶ. 読みもの・娯楽はブックマークへ回す):
{workspaces}

# 利用者の背景
{background}

# 判定済みのタブ (名前をそろえるための参考. 判定し直さない)
{known}

# 判定するタブ (1 行 1 枚の JSON. rule は規則で決めた置き場所, flow は開いた流れの名前, days は最後に見てからの日数)
{items}
'''


def _exe() -> str:
    """claude のコマンド. npm の claude.cmd は cmd.exe を通り引数の引用符が崩れるので, 隣の claude.exe を使う."""
    found = shutil.which('claude')
    if not found:
        raise RuntimeError('claude コマンドが見つからない')
    p = pathlib.Path(found)
    if p.suffix.lower() in ('.cmd', '.bat'):
        exe = p.parent / 'node_modules' / '@anthropic-ai' / 'claude-code' / 'bin' / 'claude.exe'
        if exe.exists():
            return str(exe)
    return found


def ask(items: list[dict], known: list[str], workspaces: list[str], jcfg: JudgeConfig, cwd: pathlib.Path) -> list[dict]:
    prompt = PROMPT.format(max=STACK_MAX, workspaces='\n'.join(f'- {w}' for w in workspaces),
                           background=jcfg.background() or '(無し)', known='\n'.join(known) or '(無し)',
                           items='\n'.join(json.dumps(x, ensure_ascii=False) for x in items))
    cmd = [_exe(), '-p', '--model', jcfg.model, '--tools', '', '--no-session-persistence', '--setting-sources', '',
           '--strict-mcp-config', '--output-format', 'json', '--json-schema', json.dumps(schema(workspaces))]
    env = {**os.environ, 'CLAUDE_CONFIG_DIR': jcfg.config_dir} if jcfg.config_dir else None
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding='utf-8', timeout=jcfg.timeout,
                       cwd=cwd, env=env, creationflags=NOWIN)
    try:
        out = json.loads(r.stdout)
    except ValueError:
        raise RuntimeError(f'claude の出力が JSON ではない (終了 {r.returncode}): {(r.stderr or r.stdout)[-300:]}')
    if out.get('is_error') or not isinstance(out.get('structured_output'), dict):
        raise RuntimeError(f'claude が失敗した: {str(out.get("result"))[:300]}')
    return out['structured_output'].get('tabs', [])


def judge(pending: list[dict], current: dict[str, dict], data: dict, key: str, workspaces: list[str],
          jcfg: JudgeConfig, cwd: pathlib.Path, now: float | None = None) -> tuple[dict, str]:
    """pending ({key, title, url, rule, flow, days}) を判定して data に足す. (新しい data, ログの 1 行) を返す."""
    now = time.time() if now is None else now
    if not pending:
        return data, ''
    if now - data.get('failed', 0) < BACKOFF:
        return data, f'AI の判定: 前の失敗から {int((now - data["failed"]) // 60)} 分なので呼ばない (未判定 {len(pending)} 枚)'
    tabs = dict(data.get('tabs', {})) if data.get('basis') == key else {}
    known = [f'{v["workspace"]} / {v.get("stack") or "-"} / {v.get("title", "")[:60]}' for v in current.values()]
    done = 0
    try:
        for k in range(0, len(pending), BATCH):
            chunk = pending[k:k + BATCH]
            items = [{'id': i, 'title': x['title'][:120], 'url': x['url'][:200], 'rule': x.get('rule'),
                      'flow': x.get('flow'), 'days': x.get('days')} for i, x in enumerate(chunk)]
            for v in ask(items, known, workspaces, jcfg, cwd):
                if not (isinstance(v.get('id'), int) and 0 <= v['id'] < len(chunk)):
                    continue
                x = chunk[v['id']]
                if v.get('workspace') not in workspaces or v.get('action') not in ACTIONS:
                    continue
                stack = v.get('stack')
                stack = stack.strip()[:STACK_MAX] or None if isinstance(stack, str) else None
                tabs[x['key']] = {'workspace': v['workspace'], 'stack': stack, 'action': v['action'],
                                  'title': x['title'][:120], 'at': now}
                known.append(f'{v["workspace"]} / {stack or "-"} / {x["title"][:60]}')
                done += 1
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as e:
        return {**data, 'basis': key, 'tabs': tabs, 'failed': now}, f'AI の判定に失敗した ({done} 枚まで判定): {e}'
    # 長く見ないページの判定を溜め続けない
    tabs = {u: v for u, v in tabs.items() if now - v.get('at', 0) < 10 * TTL}
    return {'basis': key, 'tabs': tabs}, f'AI の判定: {done} 枚 (未判定だった {len(pending)} 枚)'


def _bare(url: str) -> str:
    """送る URL. クエリには検索語やトークンが入り得るので, host と path だけにする."""
    s = urlsplit(url)
    return f'{s.scheme}://{s.netloc}{s.path}'


def run(placed: list, acfg, privacy, visits: list, jcfg: JudgeConfig, state: pathlib.Path,
        now: float | None = None) -> tuple[dict[str, dict], str]:
    """規則の案 (placed) のうちまだ判定していないタブを AI に判定させ, (今使える判定, ログの 1 行) を返す.
    jcfg.enabled でなければ何もしない. 判定は state/judge.json に覚える."""
    from .histdb import US, WEBKIT_TO_UNIX_S
    from .plan import page_key
    if not jcfg.enabled:
        return {}, ''
    now = time.time() if now is None else now
    now_us = int((now + WEBKIT_TO_UNIX_S) * US)
    workspaces = list(dict.fromkeys([*acfg.order, *acfg.reading_workspaces]))
    key = basis(workspaces, jcfg.background())
    path = state / 'judge.json'
    data = load(path)
    current = verdicts(data, key, now)
    last: dict[str, int] = {}
    for v in visits:
        last[v.url] = max(last.get(v.url, 0), v.t)
    pending, seen = [], set()
    for p in placed:
        url, title = p.tab.url, p.tab.title or ''
        k = page_key(url)
        if (p.status != 'keep' or not url.lower().startswith(('http://', 'https://')) or privacy.mask_of(url)
                or privacy.dropped(url, title) or k in current or k in seen
                or (acfg.close_title and acfg.close_title.search(title))):
            continue
        seen.add(k)
        days = round((now_us - last[url]) / US / 86400, 1) if url in last else None
        rule = acfg.topic_of(url, title) or p.goal
        pending.append({'key': k, 'title': title, 'url': _bare(url), 'rule': p.workspace, 'flow': rule, 'days': days})
    if not pending:
        return current, ''
    data, line = judge(pending, current, data, key, workspaces, jcfg, state, now)
    save(path, data)
    return verdicts(data, key, now), line
