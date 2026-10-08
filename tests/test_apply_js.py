"""適用の JS を実際に動かし, 触らないタブの扱いを確かめる.

適用の JS は Node で, Vivaldi の API の代わりの作り物の上で動かす. 入力しかけを調べる JS は headless の
ブラウザでフォームのあるページに対して動かす. Node・Edge / Chrome が無い環境では飛ばす.
"""
import contextlib
import functools
import http.server
import json
import shutil
import subprocess
import threading
import time
import urllib.request

import pytest
from test_viewer_e2e import BROWSER, SANDBOX
from tab_lineage import vivaldi

NODE = shutil.which('node')

FAKE = r'''
const log = {moved: [], removed: [], discarded: [], created: []};
const tabs = new Map(TABS.map(t => [t.id, {...t}]));
globalThis.self = globalThis;
globalThis.vivaldi = {
  prefs: {get: async () => ({value: [{id: 1, name: 'A'}]})},
  tabsPrivate: {move: async () => ({group: 'g'}), setGroupProperties: async () => {}},
};
globalThis.chrome = {
  windows: {getLastFocused: async () => ({id: 1})},
  tabs: {
    query: async () => [...tabs.values()].map(t => ({...t})),
    get: async id => ({...tabs.get(id)}),
    update: async (id, p) => { Object.assign(tabs.get(id), p); },
    move: async (id, p) => { log.moved.push(id); },
    remove: async id => { log.removed.push(id); tabs.delete(id); },
    discard: async id => { log.discarded.push(id); },
    create: async p => { log.created.push(p.url); const t = {id: 100 + log.created.length, ...p}; tabs.set(t.id, t); return t; },
  },
};
'''

OLD = 1_000   # ずっと前に見たタブの lastAccessed (ミリ秒)


def tab(i, url, **kw):
    return {'id': i, 'windowId': 1, 'index': i, 'pinned': False, 'active': False, 'audible': False, 'title': url,
            'url': url, 'vivExtData': json.dumps({'workspaceId': 9}), 'lastAccessed': OLD, **kw}


TABS = [tab(1, 'https://shown.example/', active=True),
        tab(2, 'https://form.example/'),
        tab(3, 'https://frozen.example/'),
        tab(4, 'https://recent.example/', lastAccessed=None),   # 今見たタブ. 下で今の時刻に置き換える
        tab(5, 'https://noaccess.example/', lastAccessed=None),
        tab(6, 'https://frozen-search.example/'),
        tab(7, 'https://plain.example/'),
        tab(8, 'https://search.example/')]
ARR = {'workspaces': [{'name': 'A', 'items': [{'stack': None, 'tabs': [
           ['form', 'https://form.example/'], ['frozen', 'https://frozen.example/'], ['plain', 'https://plain.example/']]}]}],
       'close': [{'url': u} for u in ['https://form.example/', 'https://frozen-search.example/', 'https://search.example/']]}


def run(recent_ms: int) -> dict:
    expr = vivaldi.expression(ARR, settle_ms=0, close=True, quiet=True, keep=['https://form.example/'],
                              unsure=['https://frozen.example/', 'https://frozen-search.example/'], recent_ms=recent_ms)
    src = (f'const TABS = {json.dumps(TABS)};\n'
           'TABS.forEach(t => { if (t.url === "https://recent.example/") t.lastAccessed = Date.now() - 1000;'
           ' else if (t.lastAccessed === null) delete t.lastAccessed; });\n'
           f'{FAKE}\n{expr}.then(r => console.log(JSON.stringify({{r, log}})));')
    out = subprocess.run([NODE, '-'], input=src, capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(NODE is None, reason='node が見つからない')
def test_apply_never_touches_a_tab_with_unsaved_input_and_only_moves_unprobed_ones():
    got = run(recent_ms=0)
    log, r = got['log'], got['r']
    assert 2 not in log['moved'] + log['removed'] + log['discarded']   # 入力しかけ: 案にも閉じる案にもあるが触らない
    assert 3 in log['moved'] and 3 not in log['discarded']   # 調べられなかった: 移すが休止させない
    assert 6 not in log['removed'] and 8 in log['removed']   # 調べられなかったものは閉じる案でも閉じない
    assert 7 in log['moved'] and 7 in log['discarded']   # ふつうのタブは移して休止させる
    assert 'https://form.example/' not in log['created']   # 残したタブを開き直さない
    assert r['guarded'] == 1 and r['kept'] == 1


@pytest.mark.skipif(NODE is None, reason='node が見つからない')
def test_apply_while_active_spares_recent_tabs_and_tabs_without_an_access_time():
    plain = run(recent_ms=0)['r']
    busy = run(recent_ms=30 * 60_000)['r']
    assert busy['guarded'] == 3 and plain['guarded'] == 1   # 入力しかけ + 今見た + 見た時刻が無い
    left = {u for _, u in busy['left']}
    assert {'https://recent.example/', 'https://noaccess.example/'} <= left
    assert busy['adopted'] == plain['adopted'] == 2   # ずっと前に見たタブは操作中でも移す


FORMS = r'''<!doctype html><meta charset="utf-8"><body><pre id="out"></pre><script>
const DIRTY = %s;
const cases = {
  empty: '<input value="a"><textarea>b</textarea><select><option>x</option><option selected>y</option></select>',
  typed: '<input id="e" value="a">',
  textarea: '<textarea id="e">b</textarea>',
  checked: '<input id="e" type="checkbox">',
  selected: '<select id="e"><option>x</option><option>y</option></select>',
  hidden: '<input id="e" type="hidden" value="a">',
  readonly: '<input id="e" value="a" readonly>',
  editable: '<div id="e" contenteditable>draft</div>',
  blurred: '<div contenteditable>draft</div>',
  blank: '<div contenteditable> </div><div contenteditable="false">text</div>',
  shadow: '<div id="h"></div>',
  frame: '<iframe id="f" srcdoc="<input id=e value=a>"></iframe>',
  unload: '',
};
const change = {
  typed: d => { d.getElementById('e').value = 'a!'; },
  textarea: d => { d.getElementById('e').value = 'b!'; },
  checked: d => { d.getElementById('e').checked = true; },
  selected: d => { d.getElementById('e').selectedIndex = 1; },
  hidden: d => { d.getElementById('e').value = 'z'; },
  readonly: d => { d.getElementById('e').value = 'z'; },
  editable: d => { d.getElementById('e').focus(); },
  shadow: d => { const r = d.getElementById('h').attachShadow({mode: 'open'}); r.innerHTML = '<input value="a">';
                 r.querySelector('input').value = 'z'; },
  frame: d => { d.getElementById('f').contentDocument.getElementById('e').value = 'z'; },
  unload: (d, w) => { w.onbeforeunload = () => 'x'; },
};
(async () => {
  const res = {};
  for (const [name, body] of Object.entries(cases)) {
    const f = document.createElement('iframe');
    f.srcdoc = body;
    const loaded = new Promise(r => f.onload = r);
    document.body.appendChild(f);
    await loaded;
    const inner = f.contentDocument.querySelector('iframe');
    if (inner && !inner.contentDocument.getElementById('e')) await new Promise(r => inner.onload = r);
    (change[name] || (() => {}))(f.contentDocument, f.contentWindow);
    res[name] = f.contentWindow.eval(DIRTY);
  }
  document.getElementById('out').textContent = JSON.stringify(res);
})();
</script>'''


@pytest.mark.skipif(BROWSER is None, reason='Edge / Chrome が見つからない')
def test_dirty_finds_unsaved_input_and_ignores_untouched_forms(tmp_path):
    page = tmp_path / 'forms.html'
    page.write_text(FORMS % json.dumps(vivaldi.DIRTY), encoding='utf-8')
    r = subprocess.run([BROWSER, *SANDBOX, '--headless=new', '--disable-gpu', '--no-first-run',
                        f'--user-data-dir={tmp_path / "profile"}', '--virtual-time-budget=5000',
                        '--dump-dom', page.as_uri()], capture_output=True, text=True, encoding='utf-8', timeout=60)
    dom = r.stdout
    got = json.loads(dom[dom.index('<pre id="out">') + len('<pre id="out">'):dom.index('</pre>')])
    assert got == {'empty': False, 'typed': True, 'textarea': True, 'checked': True, 'selected': True,
                   'hidden': False, 'readonly': False, 'editable': True, 'blurred': None, 'blank': False,
                   'shadow': True, 'frame': True, 'unload': True}


# 同じホストの別のポートは別のオリジンだが同じサイトなので, iframe は同じプロセスで動き, ページの中から読めない
TOP = '<!doctype html><meta charset="utf-8"><body><iframe src="http://127.0.0.1:%d/%s"></iframe>'
INNER = {'typed.html': '<input id=e value=a><script>document.getElementById("e").value = "z";</script>',
         'clean.html': '<input id=e value=a>'}


def wait_for(check, tries=60):
    for _ in range(tries):
        if (got := check()) is not None:
            return got
        time.sleep(0.5)
    raise AssertionError('時間内に済まなかった')


@contextlib.contextmanager
def serve(root):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.skipif(BROWSER is None, reason='Edge / Chrome が見つからない')
def test_form_state_finds_input_in_a_frame_the_page_cannot_read(tmp_path):
    pytest.importorskip('websocket')
    top, inner, profile = tmp_path / 'top', tmp_path / 'inner', tmp_path / 'profile'
    top.mkdir()
    inner.mkdir()
    for name, body in INNER.items():
        (inner / name).write_text(body, encoding='utf-8')
    with serve(inner) as inner_port, serve(top) as top_port:
        for name in INNER:
            (top / name).write_text(TOP % (inner_port, name), encoding='utf-8')
        typed, clean = (f'http://127.0.0.1:{top_port}/{name}' for name in INNER)
        # headless は URL を 2 つ渡すと起動しないので, 2 枚目は DevTools から開く
        proc = subprocess.Popen([BROWSER, *SANDBOX, '--headless=new', '--disable-gpu', '--no-first-run',
                                 f'--user-data-dir={profile}', '--remote-debugging-port=0', typed],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            active = profile / 'DevToolsActivePort'
            port = wait_for(lambda: int(active.read_text().split()[0]) if active.exists() else None)
            urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{port}/json/new?{clean}', method='PUT'),
                                   timeout=10).close()

            def settled():
                got = vivaldi.form_state(port)
                return got if got['dirty'] and not got['unknown'] else None
            assert wait_for(settled) == {'dirty': [typed], 'unknown': []}
            # iframe は別の接続先になっていない (ページの接続から調べるしかない)
            assert not [t for t in vivaldi._pages(port) if t.get('type') == 'iframe']
        finally:
            proc.kill()
            proc.wait(timeout=30)
