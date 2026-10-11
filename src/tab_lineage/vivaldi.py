"""arrange の案を, 起動中の Vivaldi に DevTools プロトコル (CDP) で適用する.

Vivaldi を --remote-debugging-port=<port> --remote-debugging-address=127.0.0.1 で起動しておく.
ポートが開いている間は, この PC のどのプログラムもブラウザを操作でき, Cookie も読める. 適用が済んだら普通に起動し直す.
ワークスペース・ブックマークは消さない. タブを閉じるのは close=True のときだけ.
  1. 案のワークスペースを名前で探し, 無ければ作る
  2. 案のタブを, どのウィンドウでも開いていればそのタブを, 無ければ新しく作ったタブを, 最後に使っていた
     ウィンドウへ集め, ワークスペースへ入れ, スタックにまとめて名前を付ける
  3. ブックマークバーにあとで読むのフォルダを作る. 同じ名前のフォルダには足し, 同じ URL は足さない
  4. close=True なら, 引き取らなかったタブのうち閉じる案のものとブックマークに入れたものを閉じる.
     案に無いタブ (案を作ったあとに開いたタブなど) には触らず, left として返す
  5. 最初のワークスペースを開き, ほかのワークスペースのタブを休止させる (メモリを使わない)
固定したタブは動かさず閉じない. 1 枚・1 スタックで失敗しても止めずに続け, failed として返す.
入力しかけのフォームがあるタブ (form_state の dirty) は, 移さず閉じず休止させない. 閉じたり休止させたりすると入力が消えるため.
中を調べられなかったタブ (unknown. 裏で凍結されたページなど) は, 移しはするが閉じず休止させない. 移しても入力は消えない.

タブの作成とスタックは Vivaldi の画面 (main.html) が使う拡張機能の API で行う. ワークスペースの作成と
切り替えはその API に無いので, 画面の内部の関数を探して呼ぶ. Vivaldi の版が変わって見つからないときは,
ワークスペースを手で作っておけば名前で照合して続ける.
"""
from __future__ import annotations

import itertools
import json
import urllib.request

UI_SUFFIX = '/main.html'

JS = r'''
(async (arr, opts) => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  // 画面の内部の関数. webpack のモジュールの中から, 関数の名前で探す
  const internals = (() => {
    try {
      let req;
      self.webpackChunkgapp_browser_react.push([[Symbol()], {}, r => { req = r; }]);
      const find = (src, fns) => {
        for (const id of Object.keys(req.m)) {
          const s = req.m[id].toString();
          if (!src.every(x => s.includes(x))) continue;
          for (const v of Object.values(req(id))) if (v && fns.every(f => typeof v[f] === 'function')) return v;
        }
      };
      return {act: find(['addWorkspaceWithId(', 'activateWorkspaceByIndex('], ['addWorkspaceWithId', 'activateWorkspaceByIndex']),
              store: find(['getWorkspaceByName'], ['getWorkspaces'])};
    } catch (e) { return {}; }
  })();
  const listed = async () => internals.store ? internals.store.getWorkspaces()
    : ((await vivaldi.prefs.get('vivaldi.workspaces.list')).value || []);

  const ids = {}, createdWs = [];
  for (const [k, w] of arr.workspaces.entries()) {
    const have = (await listed()).find(x => x.name === w.name);
    if (have) { ids[w.name] = have.id; continue; }
    if (!internals.act) throw new Error(`ワークスペース「${w.name}」が無く, 作る関数も見つからない. 手で作ってから実行し直す`);
    ids[w.name] = internals.act.addWorkspaceWithId(Date.now() + k, w.name, '', w.emoji || undefined);
    createdWs.push(w.name);
  }

  const extOf = t => { try { return JSON.parse(t.vivExtData || '{}'); } catch (e) { return {}; } };
  const urlOf = t => t.pendingUrl || t.url;
  const before = (await chrome.tabs.query({windowType: 'normal'})).map(t => ({...t, ext: extOf(t)}));
  // スクリプトから作ったウィンドウは画面の準備が終わらず, スタックもワークスペースの切り替えも効かない.
  // ワークスペースがウィンドウの中を分けるので, 開いているウィンドウへ足す. 手で適用したときは最後に使っていたウィンドウ.
  // quiet のときはタブの一番多いウィンドウ. 最後に使っていたウィンドウにすると, 大きなウィンドウが 2 つあるとき回ごとに行き来する
  const focused = await chrome.windows.getLastFocused({windowTypes: ['normal']});
  const counts = new Map();
  for (const t of before) counts.set(t.windowId, (counts.get(t.windowId) || 0) + 1);
  const largest = [...counts].sort((a, b) => b[1] - a[1] || (b[0] === focused.id) - (a[0] === focused.id) || a[0] - b[0])[0];
  const win = opts.quiet && largest ? {id: largest[0]} : focused;
  const setWorkspace = async (tabId, wsId) => {
    const ext = JSON.parse((await chrome.tabs.get(tabId)).vivExtData || '{}');
    if (ext.workspaceId !== wsId) await chrome.tabs.update(tabId, {vivExtData: JSON.stringify({...ext, workspaceId: wsId})});
  };
  // 適用先のウィンドウにあるタブを元のスタックから外して末尾へ置く. 残すと新しいスタックが元のスタックの続きになる.
  // vivExtData の group を消しても外れないので, 画面の「スタックから外す」と同じ move を使う
  const unstack = async tabId => {
    if (extOf(await chrome.tabs.get(tabId)).group) {
      await vivaldi.tabsPrivate.move({tabIds: [tabId], target: 'last', windowId: win.id, tweaks: ['ungroup']});
    }
  };

  // 開いているタブを URL ごとに並べる. 案のタブはまずここから引き取り, 無いときだけ作る
  // 固定したタブは移さず閉じず, 案にあっても開き直さない. quiet のときは各ウィンドウで選ばれているタブも同じ扱いにする.
  // ただし最後に使ったウィンドウと適用先のウィンドウの外のタブ 1 枚のウィンドウで, staleMs より前に見たきり音も出していないタブは移す.
  // 残すとそのウィンドウがいつまでも残る. 何枚もあるウィンドウは, ほかのタブを移して 1 枚になってから次の回に移す
  // 入力しかけのタブ (keep) と, recentMs 以内に見たタブ (操作中に整理するとき) も同じ扱い. 見た時刻が取れなければ見たものとする.
  // 中を調べられなかったタブ (unsure) は移すが, 閉じず休止させない. 休止中のタブはページが無いので調べられないが, 失う入力も無い
  const keep = new Set(opts.keep || []), unsure = new Set(opts.unsure || []);
  const doubtful = t => unsure.has(urlOf(t)) && !t.discarded;
  const recent = t => opts.recentMs > 0 && (t.lastAccessed === undefined || Date.now() - t.lastAccessed < opts.recentMs);
  const leftBehind = t => opts.staleMs > 0 && t.windowId !== focused.id && t.windowId !== win.id && counts.get(t.windowId) === 1 && !t.audible &&
    t.lastAccessed !== undefined && Date.now() - t.lastAccessed > opts.staleMs;
  const shownTab = t => opts.quiet && t.active && !leftBehind(t);
  const stays = t => t.pinned || shownTab(t) || keep.has(urlOf(t)) || recent(t);
  const guarded = before.filter(t => !t.pinned && !shownTab(t) && stays(t)).length;
  const doubted = before.filter(doubtful).length;
  const pinned = new Set(before.filter(stays).map(urlOf));
  const pool = new Map();
  for (const t of before.filter(t => !stays(t))) pool.set(urlOf(t), [...(pool.get(urlOf(t)) || []), t]);
  // 同じ URL が何枚もあれば, 適用先のウィンドウで同じワークスペースに入っているものから引き取る
  const take = (url, wsId) => {
    const ts = pool.get(url) || [];
    const k = Math.max(0, ts.findIndex(t => t.windowId === win.id && t.ext.workspaceId === wsId));
    return ts.splice(k, 1)[0];
  };

  // 案の順にウィンドウの末尾へ並べる. 元から適用先のワークスペースにあったタブは動かさない
  // 1 枚・1 スタックで失敗しても止めず, failed に書いて続ける. 途中で止まると半分だけ移した状態になる
  const placed = [], failed = [], left = [];
  let stacks = 0, already = 0, adopted = 0, opened = 0, kept = 0;
  const place = async (url, wsId) => {
    let t = take(url, wsId);
    if (!t && pinned.has(url)) { kept++; return null; }
    if (!t) {
      opened++;
      return chrome.tabs.create({windowId: win.id, url, active: false, vivExtData: JSON.stringify({workspaceId: wsId})});
    }
    if (t.windowId === win.id && t.ext.workspaceId === wsId) { already++; return t; }
    await chrome.tabs.move(t.id, {windowId: win.id, index: -1});
    await unstack(t.id);
    await setWorkspace(t.id, wsId);
    adopted++;
    return t;
  };
  const stack = async (tabs, wsId, title) => {
    const exts = await Promise.all(tabs.map(async t => extOf(await chrome.tabs.get(t.id))));
    if (new Set(exts.map(x => x.group)).size === 1 && exts[0].group) {
      // 適用し直したとき. まとまっていれば名前だけ合わせる
      if (exts[0].fixedGroupTitle !== title) {
        await vivaldi.tabsPrivate.setGroupProperties({groupExtId: exts[0].group, groupTitle: title});
      }
      return true;
    }
    // 元の場所のままのタブが前のスタックに入っていると, 新しいスタックがその続きになる
    for (const t of tabs) await unstack(t.id);
    const move = extra => vivaldi.tabsPrivate.move({tabIds: tabs.slice(1).map(t => t.id), target: exts[0].ext_id,
      windowId: win.id, ...extra, tweaks: ['target-is-tab', 'do-not-reparent', 'on', 'create-group']});
    let r = await move({workspaceId: wsId});
    if (!r?.group) {
      // workspaceId を渡すと ambiguous windowId で断られるウィンドウがある. 渡さないと外れるタブがあるので付け直す
      r = await move({});
      for (const t of tabs) await setWorkspace(t.id, wsId);
    }
    if (!r?.group) throw new Error(r?.message ?? 'スタックを作れない');
    await vivaldi.tabsPrivate.setGroupProperties({groupExtId: r.group, groupTitle: title});
    stacks++;
    return true;
  };
  for (const w of arr.workspaces) {
    const wsId = ids[w.name];
    for (const item of w.items) {
      const tabs = [];
      for (const [title, url] of item.tabs) {
        try {
          const t = await place(url, wsId);
          if (t) tabs.push(t);
        } catch (e) {
          failed.push(`${w.name}/${title}: ${e.message || e}`);
          left.push([title, url]);
        }
      }
      placed.push(...tabs.map(t => ({id: t.id, ws: wsId})));
      try {
        if (item.stack && tabs.length >= 2) await stack(tabs, wsId, item.stack);
        // 単独のタブが元の場所の前のスタックに入っていれば外す
        else for (const t of tabs) await unstack(t.id);
      } catch (e) {
        failed.push(`${w.name}/${item.stack}: ${e.message || e}`);
      }
    }
  }

  let marks = {added: 0, skipped: 0};
  if (arr.bookmarks) {
    const bar = (await chrome.bookmarks.getTree())[0].children[0];
    const put = async (parentId, node) => {
      const kids = await chrome.bookmarks.getChildren(parentId);
      const f = kids.find(c => !c.url && c.title === node.title) || await chrome.bookmarks.create({parentId, title: node.title});
      for (const c of node.children || []) await put(f.id, c);
      const have = new Set((await chrome.bookmarks.getChildren(f.id)).filter(c => c.url).map(c => c.url));
      for (const [title, url] of node.links || []) {
        if (have.has(url)) { marks.skipped++; continue; }
        await chrome.bookmarks.create({parentId: f.id, title, url});
        have.add(url);
        marks.added++;
      }
    };
    await put(bar.id, arr.bookmarks);
  }

  // 引き取らなかったタブのうち, 閉じる案のものとブックマークに入れたものを閉じる. 案に無いタブには触らない
  let closed = 0;
  const marked = new Set();
  const collect = node => { for (const [, url] of node.links || []) marked.add(url); (node.children || []).forEach(collect); };
  if (arr.bookmarks) collect(arr.bookmarks);
  const shut = new Set([...(arr.close || []).map(c => c.url), ...marked]);
  for (const ts of pool.values()) {
    for (const t of ts) {
      if (opts.close && shut.has(urlOf(t)) && !doubtful(t)) {
        try { await chrome.tabs.remove(t.id); closed++; } catch (e) { left.push([t.title, urlOf(t)]); }
      } else {
        left.push([t.title, urlOf(t)]);
      }
    }
  }
  for (const t of before.filter(stays)) left.push([t.title, urlOf(t)]);

  // quiet のときはワークスペースを切り替えず, 選ぶタブも変えない. 今見えているワークスペースの外へ移したタブを休止させる
  let active = null, switched = false;
  const first = arr.workspaces[0] && ids[arr.workspaces[0].name];
  if (opts.quiet) {
    const shown = before.find(t => t.windowId === win.id && t.active);
    // ワークスペースの外を見ているときは, 案のタブ (どれもワークスペースに入る) を全部休止させる
    if (shown) active = shown.ext.workspaceId ?? 'none';
  } else if (first !== undefined && internals.act && internals.store) {
    const idx = internals.store.getWorkspaces().findIndex(x => x.id === first);
    internals.act.activateWorkspaceByIndex(win.id, idx);
    active = first;
    switched = true;
    // 切り替えた直後はそのワークスペースで前に開いていたタブが選ばれるので, 待ってから選び直す.
    await sleep(500);
    const top = placed.find(t => t.ws === first);
    if (top) await chrome.tabs.update(top.id, {active: true});
  }

  let hibernated = 0;
  if (opts.hibernate && active !== null) {
    await sleep(opts.settleMs);   // 題名と favicon が読み込まれてから休止させる
    for (const t of placed) {
      if (t.ws === active) continue;
      try {
        const now = await chrome.tabs.get(t.id);
        if (now.audible || doubtful(now)) continue;   // 音を出しているタブと, 中を調べられなかったタブは止めない
        await chrome.tabs.discard(t.id);
        hibernated++;
      } catch (e) { /* 読み込み中などで休止できないタブは残す */ }
    }
  }
  return {window: win.id, workspaces: ids, created: createdWs, tabs: placed.length, made: opened, adopted, already, kept, stacks,
          failed, bookmarks: marks, closed, left, hibernated, switched, guarded, doubted};
})
'''


def _pages(port: int) -> list[dict]:
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=5) as r:
            return json.load(r)
    except OSError as e:
        raise SystemExit(f'127.0.0.1:{port} に接続できない. Vivaldi を --remote-debugging-port={port} で起動しているか確かめる ({e})')


def ui_page(port: int) -> str:
    """Vivaldi の画面 (main.html) の WebSocket の URL."""
    for p in _pages(port):
        if p.get('url', '').startswith('chrome-extension://') and p['url'].endswith(UI_SUFFIX):
            ws = p.get('webSocketDebuggerUrl', '')
            if not ws.startswith((f'ws://127.0.0.1:{port}/', f'ws://localhost:{port}/')):
                raise SystemExit(f'127.0.0.1:{port} が手元の外への接続先を返した: {ws}')
            return ws
    raise SystemExit(f'127.0.0.1:{port} に Vivaldi の画面が見つからない (Vivaldi 以外のブラウザかもしれない)')


def evaluate(ws_url: str, expr: str, timeout: float = 300) -> object:
    try:
        import websocket
    except ImportError:
        raise SystemExit('apply には websocket-client が要る: pip install "tab-lineage[apply]"')
    ws = websocket.create_connection(ws_url, timeout=timeout, suppress_origin=True)
    try:
        ws.send(json.dumps({'id': 1, 'method': 'Runtime.evaluate',
                            'params': {'expression': expr, 'awaitPromise': True, 'returnByValue': True}}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get('id') != 1:
                continue
            res = msg.get('result', {})
            if 'exceptionDetails' in res:
                ex = res['exceptionDetails'].get('exception', {})
                raise RuntimeError(ex.get('description') or res['exceptionDetails'].get('text'))
            if 'error' in msg:
                raise RuntimeError(msg['error'].get('message'))
            return res.get('result', {}).get('value')
    finally:
        ws.close()


TABS = '''(async () => (await chrome.tabs.query({windowType: 'normal'})).map(t => ({
  id: t.id, window: t.windowId, index: t.index, pinned: t.pinned, active: t.active, title: t.title,
  url: t.pendingUrl || t.url, vivExtData: t.vivExtData, lastAccessed: t.lastAccessed})))()'''

# ページで入力しかけのものがあるか. 値は返さず, 有れば true, 無ければ false, 分からなければ null を返す.
# 同じオリジンの iframe と開いた shadow root の中も見る. 別のオリジンの iframe は _probe がフレームごとに調べる.
# 選んでいない編集できる要素に文字があるときは, サイトの中身か書きかけか見分けられないので null. 調べて落ちたら true
DIRTY = r'''(() => {
  const skip = new Set(['hidden', 'submit', 'button', 'reset', 'image']);
  let maybe = false;
  const dirty = doc => {
    for (const e of doc.querySelectorAll('[contenteditable]')) {
      if (!e.isContentEditable || (e.parentElement && e.parentElement.isContentEditable)) continue;
      if (!(e.textContent || '').trim()) continue;
      if (e.matches(':focus-within')) return true;
      maybe = true;
    }
    for (const e of doc.querySelectorAll('*')) if (e.shadowRoot && dirty(e.shadowRoot)) return true;
    for (const e of doc.querySelectorAll('input, textarea, select')) {
      if (e.disabled || e.readOnly) continue;
      if (e.tagName === 'SELECT') {
        // selected の無い 1 行の select は先頭の (無効でない) 項目が選ばれた状態で始まる. defaultSelected と比べるだけだと変えたことになる.
        // 1 つ選ぶ select で何も選ばれていないのはページが外したときだけで, 人の操作ではそうならない
        const opts = [...e.options];
        let start = opts.map(o => o.defaultSelected);
        if (!e.multiple && e.size <= 1) {
          if (e.selectedIndex < 0) continue;
          const usable = o => !o.disabled && !(o.parentElement?.tagName === 'OPTGROUP' && o.parentElement.disabled);
          const k = start.lastIndexOf(true) >= 0 ? start.lastIndexOf(true) : opts.findIndex(usable);
          start = opts.map((o, i) => i === k);
        }
        if (opts.some((o, i) => o.selected !== start[i])) return true;
        continue;
      }
      const type = (e.type || '').toLowerCase();
      if (skip.has(type)) continue;
      if (type === 'checkbox' || type === 'radio') { if (e.checked !== e.defaultChecked) return true; continue; }
      if (type === 'file') { if (e.files && e.files.length) return true; continue; }
      if (e.value !== e.defaultValue) return true;
    }
    for (const f of doc.querySelectorAll('iframe, frame')) {
      let d = null;
      try { d = f.contentDocument; } catch (e) { /* 別のオリジン */ }
      if (d && dirty(d)) return true;
    }
    return false;
  };
  // onbeforeunload は入力が無くても置くサイトが多い (Amazon は全部のページ). 置いてあるだけでは決めず, 調べられなかった扱いにする
  try { return dirty(document) || (maybe || typeof window.onbeforeunload === 'function' ? null : false); } catch (e) { return true; }
})()'''
DIRTY_TIMEOUT = 2   # 応答するページは 0.1 秒もかからない. 凍結したページは待っても応答しない
DIRTY_WORKERS = 16


def tabs(port: int = 9222) -> list[dict]:
    """開いているタブの一覧. 閉じる前の控えと, auto がタブの変化を調べるのに使う."""
    return evaluate(ui_page(port), TABS)


def _call(ws, n: int, method: str, params: dict | None = None) -> dict:
    ws.send(json.dumps({'id': n, 'method': method, 'params': params or {}}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get('id') == n:
            if 'error' in msg:
                raise RuntimeError(msg['error'].get('message'))
            return msg.get('result', {})


def _child_frames(tree: dict):
    for c in tree.get('childFrames', []):
        yield c['frame']['id']
        yield from _child_frames(c)


def _probe(ws_url: str) -> bool | None:
    """入力しかけなら True, 無ければ False, 調べられなければ None.
    ページの中から読めない別のオリジンの iframe も, 同じプロセスで動くものは isolated world を作って調べる
    (別のプロセスで動く iframe は form_state が別の接続先として調べる)."""
    try:
        import websocket
        ws = websocket.create_connection(ws_url, timeout=DIRTY_TIMEOUT, suppress_origin=True)
    except (Exception, SystemExit):   # 閉じた直後など
        return None
    try:
        n = itertools.count(1)
        tree = _call(ws, next(n), 'Page.getFrameTree')['frameTree']
        contexts = [None]
        for f in _child_frames(tree):
            try:
                world = _call(ws, next(n), 'Page.createIsolatedWorld', {'frameId': f, 'worldName': 'tab-lineage'})
            except RuntimeError:   # 別のプロセスのフレーム (別に調べる) か, 消えたフレーム. 応答が無いときは下で None
                continue
            contexts.append(world['executionContextId'])
        answers = []
        for ctx in contexts:
            res = _call(ws, next(n), 'Runtime.evaluate', {'expression': DIRTY, 'returnByValue': True,
                                                         **({'contextId': ctx} if ctx is not None else {})})
            got = None if 'exceptionDetails' in res else res.get('result', {}).get('value')
            if got is True:
                return True
            answers.append(got)
    except (Exception, SystemExit):   # 裏で凍結されて応答しない・閉じた直後など
        return None
    finally:
        ws.close()
    return False if all(a is False for a in answers) else None


def form_state(port: int) -> dict[str, list[str]]:
    """{'dirty': 入力しかけのページの URL, 'unknown': 中を調べられなかったページの URL}.
    別のプロセスで動く iframe は別に調べ, 入っているページの結果にする.
    接続先や ID が無い (DevTools を開いている) ・手元の外を指すページは調べずに unknown にする.
    休止中のタブはページが無いので入らない (休止した時点で入力は残っていない)."""
    from concurrent.futures import ThreadPoolExecutor
    local = (f'ws://127.0.0.1:{port}/', f'ws://localhost:{port}/')
    targets = _pages(port)
    shown = [p for p in targets if p.get('type') == 'page' and p.get('url', '').startswith(('http://', 'https://', 'file://'))]
    pages = {p['id']: p for p in shown if p.get('id')}
    parent = {p['id']: p.get('parentId') for p in targets if p.get('type') == 'iframe' and p.get('id')}

    def page_of(tid):
        for _ in range(len(parent) + 1):   # iframe の中の iframe もたどる. 輪になっていても止まる
            if tid in pages:
                return tid
            tid = parent.get(tid)
        return None
    probes = [(pid, p['webSocketDebuggerUrl']) for p in targets if (pid := page_of(p.get('id'))) is not None
              and p.get('webSocketDebuggerUrl', '').startswith(local)]
    with ThreadPoolExecutor(DIRTY_WORKERS) as pool:
        got = list(pool.map(lambda x: _probe(x[1]), probes))
    dirty = {pages[pid]['url'] for (pid, _), d in zip(probes, got) if d}
    unknown = {pages[pid]['url'] for (pid, _), d in zip(probes, got) if d is None}
    unknown |= {p['url'] for p in shown if not p.get('id') or not p.get('webSocketDebuggerUrl', '').startswith(local)}
    return {'dirty': sorted(dirty), 'unknown': sorted(unknown - dirty)}


def expression(arr: dict, hibernate: bool = True, settle_ms: int = 4000, close: bool = False,
               quiet: bool = False, keep: list[str] = (), unsure: list[str] = (), recent_ms: int = 0,
               stale_ms: int = 0) -> str:
    """quiet: 選ばれているタブを動かさず, ワークスペースも切り替えない. タブの一番多いウィンドウへ集める (auto が使う).
    keep: 触らないタブの URL. unsure: 移すが閉じず休止させないタブの URL.
    recent_ms: これ以内に見たタブにも触らない (0 なら見ない).
    stale_ms: quiet でも, 最後に使ったウィンドウの外でこれより前に見たきりの選ばれているタブは移す (0 なら移さない)."""
    opts = {'hibernate': hibernate, 'settleMs': settle_ms, 'close': close, 'quiet': quiet,
            'keep': list(keep), 'unsure': list(unsure), 'recentMs': recent_ms, 'staleMs': stale_ms}
    # 既定の ensure_ascii で U+2028 なども ASCII のエスケープに直し, 式の中で文字列が切れないようにする
    return f'{JS}({json.dumps(arr)}, {json.dumps(opts)})'


def apply(arr: dict, port: int = 9222, hibernate: bool = True, close: bool = False, quiet: bool = False,
          recent_ms: int = 0, stale_ms: int = 0) -> dict:
    """適用の直前に入力しかけのタブを調べ, それには触らない."""
    forms = form_state(port)
    res = evaluate(ui_page(port), expression(arr, hibernate, close=close, quiet=quiet, keep=forms['dirty'],
                                             unsure=forms['unknown'], recent_ms=recent_ms,
                                             stale_ms=stale_ms))
    return {**res, 'dirty': len(forms['dirty']), 'unknown': res.get('doubted', 0)}


# ブックマークバーの root のフォルダの直下にあるリンク
READING = r'''(async root => {
  const bar = (await chrome.bookmarks.getTree())[0].children[0];
  const f = (await chrome.bookmarks.getChildren(bar.id)).find(c => !c.url && c.title === root);
  if (!f) return [];
  return (await chrome.bookmarks.getChildren(f.id)).filter(c => c.url).map(c => ({id: c.id, title: c.title, url: c.url}));
})'''

# root の直下のリンクを, root の下のフォルダ (無ければ作る) へ移す. 移す先に同じ URL があれば移さない
MOVE = r'''
(async (root, moves) => {
  const bar = (await chrome.bookmarks.getTree())[0].children[0];
  const folder = async (parentId, title) =>
    (await chrome.bookmarks.getChildren(parentId)).find(c => !c.url && c.title === title) ||
    await chrome.bookmarks.create({parentId, title});
  const top = await folder(bar.id, root);
  let moved = 0;
  const skipped = [];
  for (const m of moves) {
    try {
      const [node] = await chrome.bookmarks.get(m.id);
      if (!node || node.parentId !== top.id) { skipped.push(`${m.title}: 直下に無い`); continue; }
      let parentId = top.id;
      for (const name of m.path) parentId = (await folder(parentId, name)).id;
      if ((await chrome.bookmarks.getChildren(parentId)).some(c => c.url === node.url)) {
        skipped.push(`${m.title}: 移す先に同じ URL がある`);
        continue;
      }
      await chrome.bookmarks.move(m.id, {parentId});
      moved++;
    } catch (e) {
      skipped.push(`${m.title}: ${e.message || e}`);
    }
  }
  return {moved, skipped};
})'''


def reading_links(port: int, root: str) -> list[dict]:
    """ブックマークバーの root のフォルダの直下にあるリンク ({id, title, url})."""
    return evaluate(ui_page(port), f'{READING}({json.dumps(root)})', timeout=30)


def move_expression(root: str, moves: list[dict]) -> str:
    # expression と同じく, 既定の ensure_ascii で題名の U+2028 なども式を切らないエスケープにする
    return f'{MOVE}({json.dumps(root)}, {json.dumps(moves)})'


def move(port: int, root: str, moves: list[dict]) -> dict:
    """moves ({id, title, path}) のリンクを root/path のフォルダへ移す. {moved, skipped} を返す."""
    return evaluate(ui_page(port), move_expression(root, moves), timeout=60)
