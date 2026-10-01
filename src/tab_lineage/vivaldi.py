"""arrange の案を, 起動中の Vivaldi に DevTools プロトコル (CDP) で適用する.

Vivaldi を --remote-debugging-port=<port> --remote-debugging-address=127.0.0.1 で起動しておく.
ポートが開いている間は, この PC のどのプログラムもブラウザを操作でき, Cookie も読める. 適用が済んだら普通に起動し直す.
適用は足すだけで, 既存のタブ・ウィンドウ・ワークスペース・ブックマークは閉じない・消さない.
  1. 案のワークスペースを名前で探し, 無ければ作る
  2. 最後に使っていたウィンドウにタブを作り, ワークスペースへ入れ, スタックにまとめて名前を付ける
     (今のタブはワークスペースの外に残る. ワークスペースを切り替えると見えなくなるだけで, 閉じない)
  3. ブックマークバーにあとで読むのフォルダを作る. 同じ名前のフォルダには足し, 同じ URL は足さない
  4. 最初のワークスペースを開き, ほかのワークスペースに作ったタブを休止させる (メモリを使わない)

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

  const ids = {}, created = [];
  for (const [k, w] of arr.workspaces.entries()) {
    const have = (await listed()).find(x => x.name === w.name);
    if (have) { ids[w.name] = have.id; continue; }
    if (!internals.act) throw new Error(`ワークスペース「${w.name}」が無く, 作る関数も見つからない. 手で作ってから実行し直す`);
    ids[w.name] = internals.act.addWorkspaceWithId(Date.now() + k, w.name, '', w.emoji || undefined);
    created.push(w.name);
  }

  // スクリプトから作ったウィンドウは画面の準備が終わらず, スタックもワークスペースの切り替えも効かない.
  // ワークスペースがウィンドウの中を分けるので, 最後に使っていたウィンドウへ足す
  const win = await chrome.windows.getLastFocused({windowTypes: ['normal']});
  const setWorkspace = async (tabId, wsId) => {
    const ext = JSON.parse((await chrome.tabs.get(tabId)).vivExtData || '{}');
    if (ext.workspaceId !== wsId) await chrome.tabs.update(tabId, {vivExtData: JSON.stringify({...ext, workspaceId: wsId})});
  };

  // 同じワークスペースに同じ URL のタブがあれば作らない. 適用し直しても増えない
  const open = new Set((await chrome.tabs.query({windowId: win.id})).map(t => {
    try { return `${JSON.parse(t.vivExtData || '{}').workspaceId}\n${t.pendingUrl || t.url}`; } catch (e) { return ''; }
  }));

  // 新しいタブは先頭寄りに入るので, 後ろから作ると案の順に並ぶ
  const made = [], failed = [];
  let stacks = 0, already = 0;
  for (const w of [...arr.workspaces].reverse()) {
    const wsId = ids[w.name];
    for (const item of [...w.items].reverse()) {
      const tabs = [];
      for (const [, url] of [...item.tabs].reverse()) {
        if (open.has(`${wsId}\n${url}`)) { already++; continue; }
        tabs.push(await chrome.tabs.create({windowId: win.id, url, active: false, index: 9999,
          vivExtData: JSON.stringify({workspaceId: wsId})}));
      }
      made.push(...tabs.map(t => ({id: t.id, ws: wsId})));
      if (item.stack && tabs.length > 1) {
        const ext = JSON.parse((await chrome.tabs.get(tabs.at(-1).id)).vivExtData).ext_id;
        const move = extra => vivaldi.tabsPrivate.move({tabIds: tabs.slice(0, -1).map(t => t.id), target: ext,
          windowId: win.id, ...extra, tweaks: ['target-is-tab', 'do-not-reparent', 'on', 'create-group']});
        let r = await move({workspaceId: wsId});
        if (!r?.group) {
          // workspaceId を渡すと ambiguous windowId で断られるウィンドウがある. 渡さないと外れるタブがあるので付け直す
          r = await move({});
          for (const t of tabs) await setWorkspace(t.id, wsId);
        }
        if (r?.group) {
          await vivaldi.tabsPrivate.setGroupProperties({groupExtId: r.group, groupTitle: item.stack});
          stacks++;
        } else {
          failed.push(`${w.name}/${item.stack}: ${r?.message ?? 'スタックを作れない'}`);
        }
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

  let active = null;
  const first = arr.workspaces[0] && ids[arr.workspaces[0].name];
  if (first !== undefined && internals.act && internals.store) {
    const idx = internals.store.getWorkspaces().findIndex(x => x.id === first);
    internals.act.activateWorkspaceByIndex(win.id, idx);
    active = first;
    // 切り替えた直後はそのワークスペースで前に開いていたタブが選ばれるので, 待ってから選び直す.
    // 最後に作ったタブが, 案の最初のワークスペースの最初のタブ
    await sleep(500);
    if (made.length) await chrome.tabs.update(made.at(-1).id, {active: true});
  }

  let hibernated = 0;
  if (opts.hibernate && active !== null) {
    await sleep(opts.settleMs);   // 題名と favicon が読み込まれてから休止させる
    for (const t of made) {
      if (t.ws === active) continue;
      try { await chrome.tabs.discard(t.id); hibernated++; } catch (e) { /* 読み込み中などで休止できないタブは残す */ }
    }
  }
  return {window: win.id, workspaces: ids, created, tabs: made.length, already, stacks, failed, bookmarks: marks, hibernated,
          switched: active !== null};
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


def expression(arr: dict, hibernate: bool = True, settle_ms: int = 4000) -> str:
    opts = {'hibernate': hibernate, 'settleMs': settle_ms}
    # 既定の ensure_ascii で U+2028 なども ASCII のエスケープに直し, 式の中で文字列が切れないようにする
    return f'{JS}({json.dumps(arr)}, {json.dumps(opts)})'


def apply(arr: dict, port: int = 9222, hibernate: bool = True) -> dict:
    return evaluate(ui_page(port), expression(arr, hibernate))
