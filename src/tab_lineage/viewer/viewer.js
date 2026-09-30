// tab-lineage viewer. #data の JSON を読み, hash で画面を切り替える.
// 文字列は textContent でだけ入れ, リンクは http(s) の URL だけにする.
(() => {
  'use strict';
  const D = JSON.parse(document.getElementById('data').textContent);
  const M = D.meta;
  const EPS = D.episodes;
  const BY_ID = new Map(EPS.map((e, i) => [e.id, i]));
  // UTC からのずれ (秒). 夏時間の切り替えごとに [この時刻から, 分] が並ぶ
  const TZS = (M.tz || []).map(([t, m]) => [t, m * 60]);
  const off = (s) => { let o = TZS.length ? TZS[0][1] : 0; for (const [t, v] of TZS) if (t <= s) o = v; return o; };
  const WS = M.workspaces || [];
  const OTHER = 'そのほか';
  const app = document.getElementById('app');
  const NS = 'http://www.w3.org/2000/svg';

  // ---- 小道具 ----
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };
  const svgEl = (tag, attrs, text) => {
    const e = document.createElementNS(NS, tag);
    Object.entries(attrs || {}).forEach(([k, v]) => e.setAttribute(k, v));
    if (text != null) e.textContent = text;
    return e;
  };
  const link = (href, cls, text) => { const a = el('a', cls, text); a.href = href; return a; };
  const safeHref = (u) => (typeof u === 'string' && /^https?:\/\//i.test(u) ? u : null);
  const num = (n) => Number(n || 0).toLocaleString('ja-JP');
  const pad2 = (n) => String(n).padStart(2, '0');
  const WD = '日月火水木金土';
  const local = (s) => new Date((s + off(s)) * 1000);            // UTC の getter で現地時刻を読む
  const md = (s) => { const d = local(s); return `${d.getUTCMonth() + 1}/${d.getUTCDate()}`; };
  const mdw = (s) => `${md(s)}（${WD[local(s).getUTCDay()]}）`;
  const hm = (s) => { const d = local(s); return `${d.getUTCHours()}:${pad2(d.getUTCMinutes())}`; };
  const ymd = (s) => { const d = local(s); return `${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())}`; };
  const dayNo = (s) => Math.floor((s + off(s)) / 86400);
  const dayStart = (d) => d * 86400 - off(d * 86400);
  const span = (sec) => {
    const m = Math.round(sec / 60);
    if (m < 60) return `${m} 分`;
    const h = Math.floor(m / 60);
    return m % 60 ? `${h} 時間 ${m % 60} 分` : `${h} 時間`;
  };
  const when = (e) => `${mdw(e.t0)} ${hm(e.t0)} – ${dayNo(e.t0) === dayNo(e.t1) ? '' : md(e.t1) + ' '}${hm(e.t1)}`;
  const wsColor = (w) => (w == null ? 'var(--b0)' : `var(--b${(w % 7) + 1})`);
  const laneColor = (th, k) => (th.label === OTHER ? 'var(--b0)' : `var(--b${(k % 7) + 1})`);
  const wsName = (w) => (w == null ? '未分類' : WS[w] || '未分類');

  const head = (eyebrow, title, lede) => {
    const h = el('header', 'head');
    if (eyebrow) h.append(el('span', 'eyebrow', eyebrow));
    h.append(el('h1', null, title));
    if (lede) h.append(el('p', 'lede', lede));
    return h;
  };
  const section = (eyebrow, title, lede) => {
    const s = el('section');
    if (eyebrow) s.append(el('span', 'eyebrow', eyebrow));
    if (title) s.append(el('h2', null, title));
    if (lede) s.append(el('p', null, lede));
    return s;
  };
  const figures = (items) => {
    const f = el('div', 'figures');
    items.forEach(([v, label]) => { const d = el('div', 'fig'); d.append(el('b', null, v), el('span', null, label)); f.append(d); });
    return f;
  };
  const rangeText = () => (M.range ? `${ymd(M.range[0])} 〜 ${ymd(M.range[1])}` : '');
  const footer = () => el('footer', null,
    `集計元：ブラウザの History（${rangeText()}）。作成 ${M.built_at.replace('T', ' ')}。URL のクエリと、設定で伏せたサイトの中身は載せていません。`);

  // 回の中のスレッドの割合
  const meter = (e) => {
    const m = el('div', 'meter');
    m.setAttribute('aria-hidden', 'true');
    const total = e.th.reduce((a, t) => a + t.v, 0) || 1;
    e.th.forEach((t, k) => { const i = el('i'); i.style.setProperty('--c', laneColor(t, k)); i.style.width = `${(100 * t.v) / total}%`; m.append(i); });
    return m;
  };
  const card = (e) => {
    const a = link(`#/ep/${encodeURIComponent(e.id)}`, 'card panel');
    a.append(el('span', 'eyebrow mono', `${when(e)} · ${span(e.t1 - e.t0)}`), el('h3', null, e.label));
    if (e.note) a.append(el('p', null, e.note));
    else a.append(el('p', null, e.th.map((t) => t.label).filter((l) => l !== OTHER).join('、')));
    a.append(meter(e), el('span', 'caption mono', `${num(e.v)} 訪問 · ${e.th.length} スレッド · ${wsName(e.ws)}`));
    return a;
  };

  // ---- 概観 ----
  function calendar() {
    const box = el('div', 'cal');
    if (!EPS.length) return box;
    const d0 = dayNo(M.range[0]);
    const days = dayNo(M.range[1]) - d0 + 1;
    const W = 1000, L = 78, R = 10, top = 22, rowH = 17, H = top + days * rowH + 6;
    const x = (sec) => L + (sec / 86400) * (W - L - R);
    const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': '日ごとの回（横軸は時刻）' });
    for (let h = 0; h <= 24; h += 3) {
      svg.append(svgEl('line', { x1: x(h * 3600), x2: x(h * 3600), y1: top - 4, y2: H - 6, stroke: 'var(--line)', 'stroke-width': 1 }));
      svg.append(svgEl('text', { x: x(h * 3600), y: 12, 'text-anchor': 'middle' }, `${h}時`));
    }
    for (let i = 0; i < days; i++) {
      const s = dayStart(d0 + i);
      const wd = local(s).getUTCDay();
      if (wd === 0 || wd === 6) svg.append(svgEl('rect', { x: L, y: top + i * rowH, width: W - L - R, height: rowH, fill: 'var(--lane)' }));
      svg.append(svgEl('text', { x: L - 8, y: top + i * rowH + 12, 'text-anchor': 'end' }, mdw(s)));
    }
    const vmax = Math.log(1 + Math.max(...EPS.map((e) => e.v)));
    EPS.forEach((e) => {
      const g = svgEl('a', { class: 'blk', href: `#/ep/${encodeURIComponent(e.id)}` });
      g.append(svgEl('title', {}, `${when(e)}  ${e.label}（${num(e.v)} 訪問）`));
      const op = (0.35 + 0.65 * (Math.log(1 + e.v) / vmax)).toFixed(2);
      for (let d = dayNo(e.t0); d <= dayNo(e.t1); d++) {
        const base = dayStart(d);
        const a = Math.max(e.t0, base) - base, b = Math.min(e.t1, base + 86400) - base;
        g.append(svgEl('rect', { x: x(a), y: top + (d - d0) * rowH + 3, width: Math.max(3, x(b) - x(a)), height: rowH - 6,
          rx: 2, fill: wsColor(e.ws), 'fill-opacity': op }));
      }
      svg.append(g);
    });
    box.append(svg);
    return box;
  }

  function wsLegend() {
    const lg = el('div', 'legend');
    const used = new Set(EPS.map((e) => e.ws));
    [...WS.keys()].filter((w) => used.has(w)).concat(used.has(null) ? [null] : []).forEach((w) => {
      const c = el('span', 'chip');
      const d = el('i', 'dot'); d.style.setProperty('--c', wsColor(w));
      c.append(d, document.createTextNode(`${wsName(w)}（${EPS.filter((e) => e.ws === w).length}）`));
      lg.append(c);
    });
    return lg;
  }

  function viewHome() {
    const c = M.counts;
    const h = head(`閲覧履歴 · ${rangeText()}`, M.title, M.lede ||
      'ブラウザで開いたページを「どこから来たか」でつなぎ、間が空いたところで回に区切りました。回を開くと、話題ごとの流れと、ページの系統樹が見られます。');
    h.append(figures([[num(c.visits), '訪問'], [num(c.episodes), '回'], [num(c.nodes), 'ページ（畳んだあと）'],
      [num(c.detours), '寄り道の枝'], [num(c.stale), `${M.params.stale_hours} 時間以上開いたままのページ`]]));
    const cal = section('いつ調べていたか', '回の分布', 'ひとつの帯がひとつの回です。色はワークスペース、濃さは訪問の数を表します。帯を押すとその回を開きます。');
    cal.append(wsLegend(), calendar());
    const out = [h, cal];
    const featured = EPS.filter((e) => e.featured);
    if (featured.length) {
      const s = section('読みもの', '流れを読み解いた回', '名前と所見を手で付けた回です。');
      const cards = el('div', 'cards'); featured.forEach((e) => cards.append(card(e))); s.append(cards);
      out.push(s);
    }
    const big = EPS.filter((e) => !e.featured).sort((a, b) => b.v - a.v).slice(0, 9);
    const s = section('大きい回', '訪問の多かった回', null);
    const cards = el('div', 'cards'); big.forEach((e) => cards.append(card(e))); s.append(cards);
    s.append(link('#/list', null, `${num(EPS.length)} 回すべてを一覧で見る`));
    out.push(s, footer());
    return out;
  }

  // ---- 一覧 ----
  const state = { q: '', ws: 'all', sort: 'new' };
  const haystack = new Map();
  const hay = (e) => {
    if (!haystack.has(e.id)) {
      haystack.set(e.id, [e.label, e.auto, e.note, ...e.th.map((t) => t.label), ...e.nodes.map((n) => `${n.t} ${n.q || ''} ${n.h}`)]
        .join('\n').toLowerCase());
    }
    return haystack.get(e.id);
  };
  function rows() {
    const q = state.q.trim().toLowerCase().split(/\s+/).filter(Boolean);
    let list = EPS.filter((e) => (state.ws === 'all' || String(e.ws) === state.ws) && q.every((w) => hay(e).includes(w)));
    const key = { new: (e) => -e.t0, old: (e) => e.t0, big: (e) => -e.v, long: (e) => e.t0 - e.t1 }[state.sort];
    list = list.slice().sort((a, b) => key(a) - key(b));
    const ul = el('ul', 'rows');
    list.forEach((e) => {
      const a = link(`#/ep/${encodeURIComponent(e.id)}`);
      const what = el('span', 'what');
      const b = el('b');
      if (e.featured) b.append(el('span', 'star', '★'));
      b.append(document.createTextNode(e.label));
      what.append(b, meter(e));
      a.append(el('span', 'when', `${when(e)}\n${span(e.t1 - e.t0)}`), what, el('span', 'n', `${num(e.v)} 訪問`));
      const li = el('li'); li.append(a); ul.append(li);
    });
    const count = el('p', 'caption', `${num(list.length)} 回`);
    return [count, ul];
  }
  function viewList() {
    const h = head('回の一覧', 'すべての回', 'ページの題名・検索語・サイト名でも絞り込めます。');
    const f = el('div', 'filters');
    const q = el('input'); q.id = 'q'; q.type = 'search'; q.placeholder = '例：統計 github'; q.value = state.q;
    q.setAttribute('aria-label', '語で絞り込む');
    const ws = el('select'); ws.id = 'ws'; ws.setAttribute('aria-label', 'ワークスペース');
    [['all', 'すべてのワークスペース'], ...WS.map((n, i) => [String(i), n]), ['null', '未分類']].forEach(([v, t]) => {
      const o = el('option', null, t); o.value = v; ws.append(o);
    });
    ws.value = state.ws;
    const sort = el('select'); sort.id = 'sort'; sort.setAttribute('aria-label', '並び順');
    [['new', '新しい順'], ['old', '古い順'], ['big', '訪問の多い順'], ['long', '長い順']].forEach(([v, t]) => {
      const o = el('option', null, t); o.value = v; sort.append(o);
    });
    sort.value = state.sort;
    f.append(q, ws, sort);
    const holder = el('div');
    holder.style.display = 'grid'; holder.style.gap = '8px';
    const refresh = () => holder.replaceChildren(...rows());
    q.addEventListener('input', () => { state.q = q.value; refresh(); });
    ws.addEventListener('change', () => { state.ws = ws.value; refresh(); });
    sort.addEventListener('change', () => { state.sort = sort.value; refresh(); });
    refresh();
    const s = el('section'); s.append(f, holder);
    return [h, s, footer()];
  }

  // ---- 回 ----
  function niceStep(total) {
    const steps = [60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600];
    return steps.find((s) => total / s <= 8) || 43200;
  }
  function lanes(e, onPick) {
    const box = el('div', 'lanes');
    const total = Math.max(60, e.t1 - e.t0);
    const W = 760, L = 150, R = 14, laneH = 26, top = 8, H = top + e.th.length * laneH + 26;
    const x = (s) => L + (s / total) * (W - L - R);
    const y = (k) => top + k * laneH + laneH / 2;
    const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': '訪問の時刻とスレッド' });
    e.th.forEach((t, k) => {
      svg.append(svgEl('rect', { x: L, y: top + k * laneH + 3, width: W - L - R, height: laneH - 6, rx: 3, fill: 'var(--lane)' }));
      const name = t.label.length > 13 ? t.label.slice(0, 12) + '…' : t.label;
      const tx = svgEl('text', { x: L - 8, y: y(k) + 4, 'text-anchor': 'end' }, name);
      tx.append(svgEl('title', {}, t.label));
      svg.append(tx);
    });
    const axis = svgEl('g', { class: 'axis' });
    const step = niceStep(total);
    const tz = off(e.t0);
    const first = Math.ceil((e.t0 + tz) / step) * step - tz - e.t0;
    for (let s = first; s <= total; s += step) {
      axis.append(svgEl('line', { x1: x(s), x2: x(s), y1: top, y2: H - 20, stroke: 'var(--line)', 'stroke-width': 1 }));
      axis.append(svgEl('text', { x: x(s), y: H - 6, 'text-anchor': 'middle' }, hm(e.t0 + s)));
    }
    svg.append(axis);
    const pts = [];
    e.nodes.forEach((n) => n.a.forEach((s) => pts.push([s, n])));
    pts.sort((a, b) => a[0] - b[0]);
    if (pts.length > 1) {
      const d = pts.map(([s, n], i) => `${i ? 'L' : 'M'}${x(s).toFixed(1)},${y(n.th)}`).join(' ');
      svg.append(svgEl('path', { d, fill: 'none', stroke: 'var(--muted)', 'stroke-width': 1, 'stroke-opacity': 0.4 }));
    }
    const r = pts.length > 400 ? 2.5 : 3.5;
    pts.forEach(([s, n]) => {
      const c = laneColor(e.th[n.th], n.th);
      const dot = svgEl('circle', n.o
        ? { cx: x(s), cy: y(n.th), r: r + 1, fill: 'var(--surface)', stroke: c, 'stroke-width': 2 }
        : { cx: x(s), cy: y(n.th), r, fill: c });
      dot.append(svgEl('title', {}, `${hm(e.t0 + s)}  ${label(n)}`));
      dot.addEventListener('click', () => onPick(n.i));
      svg.append(dot);
    });
    box.append(svg);
    return box;
  }

  const label = (n) => (n.k && n.q ? `検索「${n.q}」` : n.t || n.h);
  function pageRow(n) {
    const row = el('div', 'page' + (n.m ? ' masked' : ''));
    row.id = `n${n.i}`;
    const dot = el('i', 'dot'); dot.style.setProperty('--c', n.k ? 'var(--serp)' : 'var(--b0)');
    const href = n.m ? null : safeHref(n.u);
    const t = href ? link(href, 't', label(n)) : el('span', 't', label(n));
    if (href) { t.target = '_blank'; t.rel = 'noopener noreferrer'; }
    row.append(dot, t, el('span', 'h', n.h));
    if (n.n > 1) row.append(el('span', 'tag fold', `${n.n} 回`));
    if (n.v === 's') row.append(el('span', 'tag srch', '検索から'));
    if (n.d) row.append(el('span', 'tag detour', '寄り道'));
    if (n.o) row.append(el('span', 'tag open', `開いたまま ${n.o} 日`));
    if (n.x && n.x.length) row.append(el('span', 'alt', `ほかに：${n.x.join(' / ')}`));
    return row;
  }
  function tree(e, k, kids) {
    const inThread = (n) => n.th === k;
    const roots = e.nodes.filter((n) => inThread(n) && (n.p == null || !kids.byId.has(n.p) || kids.byId.get(n.p).th !== k));
    const item = (n) => {
      const li = el('li');
      const sub = (kids.of.get(n.i) || []).filter(inThread);
      const ul = sub.length ? el('ul') : null;
      if (ul) sub.forEach((c) => ul.append(item(c)));
      if (n.d && ul) {
        const det = el('details');
        const sum = el('summary'); sum.append(pageRow(n));
        det.append(sum, ul);
        li.append(det);
      } else {
        li.append(pageRow(n));
        if (ul) li.append(ul);
      }
      return li;
    };
    const ul = el('ul', 'tree');
    roots.forEach((n) => ul.append(item(n)));
    return ul;
  }
  function viewEpisode(id) {
    const idx = BY_ID.get(id);
    if (idx == null) return viewMissing();
    const e = EPS[idx];
    const prev = EPS[idx - 1], next = EPS[idx + 1];
    const nav = () => {
      const n = el('div', 'ep-nav');
      n.append(prev ? link(`#/ep/${encodeURIComponent(prev.id)}`, null, `← ${md(prev.t0)} ${prev.label}`) : el('span'),
        link('#/list', null, '回の一覧'),
        next ? link(`#/ep/${encodeURIComponent(next.id)}`, null, `${md(next.t0)} ${next.label} →`) : el('span'));
      return n;
    };
    const h = head(`${when(e)} · ${span(e.t1 - e.t0)} · ${num(e.v)} 訪問 · ${wsName(e.ws)}`, e.label, null);
    if (e.label !== e.auto) h.append(el('p', 'caption', `自動で付いた名前：${e.auto}`));
    if (e.note) h.append(el('p', 'verdict', e.note));
    if (e.cont.length) {
      const p = el('p', 'caption', '前の回の続き：');
      e.cont.forEach((c, i) => {
        const j = BY_ID.get(c);
        if (i) p.append(document.createTextNode('、'));
        p.append(j == null ? document.createTextNode(c) : link(`#/ep/${encodeURIComponent(c)}`, null, `${mdw(EPS[j].t0)} ${EPS[j].label}`));
      });
      h.append(p);
    }
    const kids = { byId: new Map(e.nodes.map((n) => [n.i, n])), of: new Map() };
    e.nodes.forEach((n) => { if (n.p != null) { if (!kids.of.has(n.p)) kids.of.set(n.p, []); kids.of.get(n.p).push(n); } });
    const pick = (i) => {
      const row = document.getElementById(`n${i}`);
      if (!row) return;
      let d = row.closest('details');
      while (d) { d.open = true; d = d.parentElement.closest('details'); }
      document.querySelectorAll('.page.hl').forEach((r) => r.classList.remove('hl'));
      row.classList.add('hl');
      row.scrollIntoView({ block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
    };
    const flow = section('流れ', 'スレッドごとの訪問',
      '横軸は時刻、段は話題のまとまり（スレッド）です。点が 1 回の訪問、線はその順番、白抜きの点は長く開いたままだったページです。点を押すと下の木の該当ページへ移ります。');
    flow.append(lanes(e, pick));
    const lg = el('div', 'legend');
    [['var(--serp)', '検索結果のページ'], ['var(--b0)', 'ページ']].forEach(([c, t]) => {
      const s = el('span', 'chip'); const d = el('i', 'dot'); d.style.setProperty('--c', c); s.append(d, document.createTextNode(t)); lg.append(s);
    });
    const threads = section('系統樹', 'どこから来たか', '字下げが「このページから開いた」を表します。寄り道の枝は畳んであります。');
    threads.append(lg);
    const wrap = el('div', 'threads');
    e.th.forEach((t, k) => {
      const box = el('div', 'thread');
      const h3 = el('h3');
      const d = el('i', 'dot'); d.style.setProperty('--c', laneColor(t, k));
      h3.append(d, document.createTextNode(t.label), el('span', 'n', `${num(t.v)} 訪問`));
      const tb = el('div', 'treebox'); tb.append(tree(e, k, kids));
      box.append(h3, tb);
      wrap.append(box);
    });
    threads.append(wrap);
    return [nav(), h, flow, threads, nav(), footer()];
  }

  // ---- 今のタブ ----
  const STATUS = [['keep', '残す'], ['hold', '用途を確認したい'], ['dup', '重複なので閉じる'], ['serp', '検索結果なので閉じる'], ['stash', '保存して閉じる']];
  const OFF = new Set(['dup', 'serp', 'stash']);
  function viewTabs() {
    const P = D.plan;
    if (!P || !Array.isArray(P.tabs)) {
      return [head('今のタブ', '整理案はまだありません', 'build に --session（セッションファイル）か --plan（手で作った整理案の JSON）を渡すと、ここに今開いているタブの整理案が出ます。')];
    }
    const TABS = P.tabs;
    const cnt = (s) => TABS.filter((t) => t[1] === s).length;
    const open = TABS.length - TABS.filter((t) => OFF.has(t[1])).length;
    const h = head(P.eyebrow || `いまの ${TABS.length} 枚`, P.title || '並びのままと、木にしたとき', P.lede ||
      '左はタブバーの並び順、右は同じタブを「ワークスペース → 目的 → ページ」に並べ替えたものです。どちらかにカーソルを乗せると、もう一方の同じタブが光ります。');
    h.append(figures([[`${TABS.length} → ${open}`, '開いておくタブ'], [cnt('dup'), '重複'], [cnt('serp'), '検索結果'], [cnt('stash'), '保存して閉じる']]));
    const lg = el('div', 'legend');
    STATUS.filter(([s]) => cnt(s)).forEach(([s, t]) => {
      const c = el('span', `chip f-${s}`); c.append(el('i', 'dot'), document.createTextNode(`${t}（${cnt(s)}）`)); lg.append(c);
    });
    const strip = el('ol', 'strip');
    TABS.forEach(([title, f], i) => {
      const li = el('li', `f-${f}${OFF.has(f) ? ' off' : ''}`);
      li.dataset.t = i + 1; li.title = title;
      li.append(el('i', 'dot'), el('span', 'tt', title));
      strip.append(li);
    });
    const row = (n, extra) => {
      const tab = TABS[n - 1];
      if (!tab) return el('div', 'page', `（${n} 番のタブは無い）`);
      const [title, f] = tab;
      const r = el('div', `page f-${f}${OFF.has(f) ? ' off' : ''}`);
      r.dataset.t = n;
      r.append(el('i', 'dot'), el('span', 'n', String(n)), el('span', 't', title));
      if (extra) r.append(el('span', 'tag fold', extra));
      return r;
    };
    const build = (items) => {
      const ul = el('ul');
      (items || []).forEach((it) => {
        const li = el('li', it.k === 'ws' || it.k === 'goal' ? it.k : '');
        if (it.shelf) li.classList.add('shelf');
        if (it.label) {
          const node = el('div', 'node', it.label);
          if (it.note) node.append(el('span', 'note', it.note));
          li.append(node);
        } else if (Array.isArray(it.t) && it.t.length) {
          const [first, ...rest] = it.t;
          li.append(row(first, rest.length ? `ほか ${rest.length} 枚を閉じる` : null));
          rest.forEach((n) => { const r = row(n); r.style.paddingLeft = '28px'; li.append(r); });
          if (it.note) li.append(el('div', 'note', it.note));
        }
        if (it.sub) li.append(build(it.sub));
        ul.append(li);
      });
      return ul;
    };
    const t = build(P.tree); t.className = 'tree';
    const sheet = el('div', 'sheet');
    const p1 = el('div', 'panel'); p1.append(el('h3', null, 'タブバーの並び'), strip);
    const p2 = el('div', 'panel'); p2.append(el('h3', null, '系統樹'), t);
    sheet.append(p1, p2);
    const s = el('section'); s.append(lg, sheet);
    s.append(el('p', 'caption', P.caption || (P.source === 'auto'
      ? '分類は履歴から自動で付けたものです。目的の名前はそのページが属するスレッドの名前です。'
      : '分類は手で付けたものです。')));
    const mark = (n) => document.querySelectorAll('[data-t]').forEach((x) => x.classList.toggle('hl', n != null && x.dataset.t === n));
    s.addEventListener('mouseover', (ev) => { const x = ev.target.closest('[data-t]'); mark(x ? x.dataset.t : null); });
    s.addEventListener('mouseleave', () => mark(null));
    return [h, s, footer()];
  }

  // ---- 規則 ----
  function viewRules() {
    const S = M.lineage || {};
    const P = M.params;
    const c = M.counts;
    const searches = EPS.reduce((a, e) => a + e.nodes.filter((n) => n.k).length, 0);
    const R = [
      ['訪問をつなぐ', '履歴に残る「新しいタブで開いた元」（opener）と「同じタブで来た元」（from）で親子にする。リロードとサブフレームは節にしない。',
        `${num(S.visits)} 訪問が対象。`, [['ext', 'tab-lineage']]],
      ['同じサイト内の遷移を畳む', '地図や SNS のようにページ内で URL が変わり続けるものは親に畳む。題名の違うページを同じサイトで次々に開いたときは、そのサイトに入った最初のページの子として並べる。',
        `畳んだ訪問 ${num(S.folded)}、並べ直した節 ${num(S.flattened)}。`, [['ext', 'tab-lineage']]],
      ['戻る・重複をまとめる', '戻る・進むで同じページに戻った訪問と、親が同じで URL か題名が同じ兄弟は 1 つの節にする。',
        `戻る・進む ${num(S.back)}、同じ URL の開き直し ${num(S.same_url)}、兄弟の重複 ${num(S.deduped)}。`, [['ext', '拡張'], ['ext', 'tab-lineage']]],
      ['検索結果は節として残す', '検索結果のページは検索語を書いた節にする。タブのほうは子を開いた時点で役目を終える。',
        `検索の節 ${num(searches)}。`, [['ext', 'tab-lineage']]],
      ['親が無いページを検索につなぐ', `親が記録されていないリンクの訪問は、直前 ${P.search_window_seconds} 秒以内の検索を親にする。検索語が URL に残らない検索サイトで効く。`,
        `補った親 ${num(S.search_parent)}。`, [['ext', 'tab-lineage']]],
      ['回に区切る', `${P.gap_minutes} 分以上訪問が途切れたら別の回にする。前の回のページから開いたものは「続き」としてつなぐ。`,
        `${num(c.episodes)} 回。`, [['ext', 'tab-lineage']]],
      ['話題でスレッドに分ける', `回の中の木を、題名と検索語の近さ（TF-IDF の余弦が ${P.theta} 以上）か同じサイトで束ねる。段が ${P.max_lanes} を超えたら残りを「そのほか」にする。`,
        '名前は訪問の多い節の題名か検索語から付ける。手で付け直せる。', [['ext', 'tab-lineage']]],
      ['寄り道に印を付けて畳む', '木の中で、残りの部分と話題がほとんど重ならない枝を寄り道として畳む。',
        `寄り道の枝 ${num(c.detours)}。`, [['ext', 'tab-lineage']]],
      ['放っておいたページに印を付ける', `${P.stale_hours} 時間以上開いたままだったページに日数を付ける。今のタブなら保存して閉じる候補にする。`,
        `${num(c.stale)} ページ。`, [['native', '標準'], ['ext', 'tab-lineage']]],
      ['ワークスペースへ振り分ける', 'ドメインやリポジトリ名の規則で、木とタブをワークスペースに振り分ける。',
        WS.length ? `${WS.join('・')}。` : '設定に規則がありません。', [['native', '標準'], ['ext', 'tab-lineage']]],
    ];
    const h = head('仕組み', '木を作る規則', '履歴から系統樹を作るまでの処理を、実行する順に並べました。数字はこのサイトのデータでの件数です。右端は、ブラウザの標準機能や拡張で足りるものと、このツールが担うものの区別です。');
    const ol = el('ol', 'rules');
    R.forEach(([b, text, ev, where]) => {
      const li = el('li');
      const d = el('div'); d.append(el('b', null, b), document.createTextNode(text));
      const e = el('div', 'ev', ev);
      d.append(e);
      const w = el('div', 'where');
      where.forEach(([k, t]) => w.append(el('span', `tag ${k}`, t)));
      li.append(d, w);
      ol.append(li);
    });
    const s = el('section'); s.append(ol);
    return [h, s, footer()];
  }

  function viewMissing() {
    return [head('見つかりません', 'その回はありません', null), link('#/list', null, '回の一覧へ')];
  }

  // ---- 画面の切り替え ----
  document.getElementById('brand').textContent = M.title;
  function route() {
    const hash = location.hash.replace(/^#/, '') || '/';
    const [, name = '', arg = ''] = hash.split('/');
    let nodes, key;
    if (name === 'ep') { nodes = viewEpisode(decodeURIComponent(arg)); key = 'list'; }
    else if (name === 'list') { nodes = viewList(); key = 'list'; }
    else if (name === 'tabs') { nodes = viewTabs(); key = 'tabs'; }
    else if (name === 'rules') { nodes = viewRules(); key = 'rules'; }
    else { nodes = viewHome(); key = 'home'; }
    app.replaceChildren(...nodes);
    document.querySelectorAll('.bar [data-route]').forEach((a) => {
      if (a.dataset.route === key) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
    const h1 = app.querySelector('h1');
    document.title = name && h1 ? `${h1.textContent} · ${M.title}` : M.title;
    window.scrollTo(0, 0);
  }
  window.addEventListener('hashchange', route);
  route();
})();
