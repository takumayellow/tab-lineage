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

タブの作成とスタックは Vivaldi の画面 (main.html) が使う拡張機能の API で行う. ワークスペースの作成と
切り替えはその API に無いので, 画面の内部の関数を探して呼ぶ. Vivaldi の版が変わって見つからないときは,
ワークスペースを手で作っておけば名前で照合して続ける.
"""
from __future__ import annotations

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

  // スクリプトから作ったウィンドウは画面の準備が終わらず, スタックもワークスペースの切り替えも効かない.
  // ワークスペースがウィンドウの中を分けるので, 最後に使っていたウィンドウへ足す
  const win = await chrome.windows.getLastFocused({windowTypes: ['normal']});
  const setWorkspace = async (tabId, wsId) => {
    const ext = JSON.parse((await chrome.tabs.get(tabId)).vivExtData || '{}');
    if (ext.workspaceId !== wsId) await chrome.tabs.update(tabId, {vivExtData: JSON.stringify({...ext, workspaceId: wsId})});
  };
  const extOf = t => { try { return JSON.parse(t.vivExtData || '{}'); } catch (e) { return {}; } };
  // 適用先のウィンドウにあるタブを元のスタックから外して末尾へ置く. 残すと新しいスタックが元のスタックの続きになる.
  // vivExtData の group を消しても外れないので, 画面の「スタックから外す」と同じ move を使う
  const unstack = async tabId => {
    if (extOf(await chrome.tabs.get(tabId)).group) {
      await vivaldi.tabsPrivate.move({tabIds: [tabId], target: 'last', windowId: win.id, tweaks: ['ungroup']});
    }
  };

  // 開いているタブを URL ごとに並べる. 案のタブはまずここから引き取り, 無いときだけ作る
  const urlOf = t => t.pendingUrl || t.url;
  const before = (await chrome.tabs.query({windowType: 'normal'})).map(t => ({...t, ext: extOf(t)}));
  // 固定したタブは移さず閉じず, 案にあっても開き直さない
  const pinned = new Set(before.filter(t => t.pinned).map(urlOf));
  const pool = new Map();
  for (const t of before.filter(t => !t.pinned)) pool.set(urlOf(t), [...(pool.get(urlOf(t)) || []), t]);
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
      if (opts.close && shut.has(urlOf(t))) {
        try { await chrome.tabs.remove(t.id); closed++; } catch (e) { left.push([t.title, urlOf(t)]); }
      } else {
        left.push([t.title, urlOf(t)]);
      }
    }
  }
  for (const t of before.filter(t => t.pinned)) left.push([t.title, urlOf(t)]);

  let active = null;
  const first = arr.workspaces[0] && ids[arr.workspaces[0].name];
  if (first !== undefined && internals.act && internals.store) {
    const idx = internals.store.getWorkspaces().findIndex(x => x.id === first);
    internals.act.activateWorkspaceByIndex(win.id, idx);
    active = first;
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
      try { await chrome.tabs.discard(t.id); hibernated++; } catch (e) { /* 読み込み中などで休止できないタブは残す */ }
    }
  }
  return {window: win.id, workspaces: ids, created: createdWs, tabs: placed.length, made: opened, adopted, already, kept, stacks,
          failed, bookmarks: marks, closed, left, hibernated, switched: active !== null};
})
'''


def ui_page(port: int) -> str:
    """Vivaldi の画面 (main.html) の WebSocket の URL."""
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=5) as r:
            pages = json.load(r)
    except OSError as e:
        raise SystemExit(f'127.0.0.1:{port} に接続できない. Vivaldi を --remote-debugging-port={port} で起動しているか確かめる ({e})')
    for p in pages:
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
  id: t.id, window: t.windowId, index: t.index, pinned: t.pinned, title: t.title, url: t.pendingUrl || t.url,
  vivExtData: t.vivExtData})))()'''


def tabs(port: int = 9222) -> list[dict]:
    """開いているタブの一覧. 閉じる前の控えに使う."""
    return evaluate(ui_page(port), TABS)


def expression(arr: dict, hibernate: bool = True, settle_ms: int = 4000, close: bool = False) -> str:
    opts = {'hibernate': hibernate, 'settleMs': settle_ms, 'close': close}
    # 既定の ensure_ascii で U+2028 なども ASCII のエスケープに直し, 式の中で文字列が切れないようにする
    return f'{JS}({json.dumps(arr)}, {json.dumps(opts)})'


def apply(arr: dict, port: int = 9222, hibernate: bool = True, close: bool = False) -> dict:
    return evaluate(ui_page(port), expression(arr, hibernate, close=close))
