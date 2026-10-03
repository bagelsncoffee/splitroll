'use strict';
/* splitroll UI — talks to the local server in server.py */

const FILMS = ['Kodak Portra 160','Kodak Portra 400','Kodak Portra 800','Kodak Gold 200','Kodak UltraMax 400',
  'Kodak Ektar 100','Kodak ColorPlus 200','Kodak Tri-X 400','Kodak T-Max 400','Kodak Ektachrome E100',
  'Fuji Superia 400','Fuji C200','Fujicolor 200','Fuji Velvia 50','Fuji Provia 100F','Ilford HP5+','Ilford FP4+',
  'Ilford Delta 3200','Ilford XP2 Super','Ilford Pan F+','Kentmere 400','Cinestill 800T','Cinestill 400D',
  'Cinestill 50D','Lomography 400','Lomography 800','Harman Phoenix 200'];

const S = {
  rolls: [], settings: {}, active: null, tab: 'contact', idx: 0,
  sel: {}, anchor: {},                       // per-roll selection (Set of frame ids) and shift-click anchor
  zoom: 1, panX: 0, panY: 0, space: false,
  jobs: {}, undo: [], redo: [], exporting: null,
};
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const pad = (n, w = 2) => String(n).padStart(w, '0');
const T = () => window.SR?.token || '';

// ── API ────────────────────────────────────────────────────────────────────
async function api(method, path, body) {
  const o = { method, headers: { 'X-Token': T() } };
  if (body !== undefined) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(body); }
  const r = await fetch(path, o);
  let data = null;
  try { data = await r.json(); } catch (e) { /* empty */ }
  if (!r.ok) throw new Error(data?.error || `Request failed (${r.status})`);
  return data;
}
const GET = p => api('GET', p), POST = (p, b) => api('POST', p, b ?? {}), PATCH = (p, b) => api('PATCH', p, b);
const img = (kind, f, rot) => `/api/${kind}/${f.id}?r=${rot ?? f.rot}&t=${T()}`;

// ── model helpers ──────────────────────────────────────────────────────────
const roll = () => S.rolls.find(r => r.id === S.active) || null;
const rollById = id => S.rolls.find(r => r.id === id) || null;
const eff = (r, f) => (f.state === 'pending' && r.fullFrame) ? 'full' : f.state;
const rotW = f => (f.rot === 90 || f.rot === 270) ? f.h : f.w;
const rotH = f => (f.rot === 90 || f.rot === 270) ? f.w : f.h;
const sel = r => (S.sel[r.id] ||= new Set());
function splitBounds(width, pct, border) {           // mirrors imaging.split_bounds
  pct = Math.min(95, Math.max(5, pct)); border = Math.max(0, border | 0);
  const cx = Math.floor(pct / 100 * width + 0.5);
  const le = Math.max(1, cx - Math.floor(border / 2));
  let rs = Math.min(width - 1, cx + Math.ceil(border / 2));
  if (rs <= le) rs = le;
  return [le, rs];
}
function counts(r) {
  const c = { split: 0, full: 0, pending: 0, missing: 0 };
  r.frames.forEach(f => { c[eff(r, f)]++; if (f.missing) c.missing++; });
  return c;
}

// ── saving + undo ──────────────────────────────────────────────────────────
const FIELDS = ['rot', 'cut', 'auto', 'gap', 'state', 'lRot', 'rRot', 'export'];
const pick = (f, keys = FIELDS) => Object.fromEntries(keys.map(k => [k, f[k]]));
const _pending = {}; let _flushT = null;

function queue(r, f) {
  (_pending[r.id] ||= {})[f.id] = { id: f.id, ...pick(f) };
  clearTimeout(_flushT); _flushT = setTimeout(flush, 250);
}
async function flush() {
  clearTimeout(_flushT); _flushT = null;
  for (const rid of Object.keys(_pending)) {
    const frames = Object.values(_pending[rid]); delete _pending[rid];
    if (!frames.length) continue;
    try { await PATCH(`/api/rolls/${rid}/frames`, { frames }); }
    catch (e) { toast('Could not save changes: ' + e.message, { err: true }); }
  }
}
/** Apply fn to frames, record undo, persist. */
function edit(r, frames, fn, { undo = true } = {}) {
  const items = frames.map(f => ({ id: f.id, before: pick(f) }));
  frames.forEach(f => { fn(f); queue(r, f); });
  items.forEach((it, i) => it.after = pick(frames[i]));
  if (undo) pushUndo({ rid: r.id, frames: items });
}
function pushUndo(entry) {
  S.undo.push(entry); if (S.undo.length > 200) S.undo.shift(); S.redo = [];
}
async function patchRoll(r, changes, { undo = true } = {}) {
  if (undo) {
    const before = Object.fromEntries(Object.keys(changes).map(k => [k, r[k]]));
    pushUndo({ rid: r.id, roll: { before, after: { ...changes } } });
  }
  Object.assign(r, changes);
  try { await PATCH(`/api/rolls/${r.id}`, changes); }
  catch (e) { toast('Could not save roll: ' + e.message, { err: true }); }
}
function applyHistory(entry, which) {
  const r = rollById(entry.rid); if (!r) return;
  if (entry.frames) entry.frames.forEach(it => {
    const f = r.frames.find(x => x.id === it.id); if (!f) return;
    Object.assign(f, it[which]); queue(r, f);
  });
  if (entry.roll) patchRoll(r, entry.roll[which], { undo: false });
  if (S.active !== r.id) { S.active = r.id; }
  renderAll();
}
function undo() { const e = S.undo.pop(); if (!e) return toast('Nothing to undo'); applyHistory(e, 'before'); S.redo.push(e); toast('Undone'); }
function redo() { const e = S.redo.pop(); if (!e) return toast('Nothing to redo'); applyHistory(e, 'after'); S.undo.push(e); toast('Redone'); }

// ── toasts, modals, menu ───────────────────────────────────────────────────
function toast(msg, { err = false, action = null, ms = 3200 } = {}) {
  const t = document.createElement('div');
  t.className = 'toast' + (err ? ' err' : '');
  t.innerHTML = `<span>${esc(msg)}</span>`;
  if (action) {
    const b = document.createElement('button'); b.className = 'btn sm'; b.textContent = action.label;
    b.onclick = () => { action.fn(); t.remove(); }; t.appendChild(b); ms = Math.max(ms, 7000);
  }
  $('toasts').appendChild(t);
  while ($('toasts').children.length > 4) $('toasts').firstChild.remove();
  setTimeout(() => t.remove(), ms);
  return t;
}
function ask({ title, body, buttons }) {
  return new Promise(res => {
    const root = $('modalRoot');
    root.innerHTML = `<div class="modal" role="dialog"><h3>${esc(title)}</h3><div class="mb">${body}</div>
      <div class="mf">${buttons.map((b, i) => `<button class="btn ${b.pri ? 'pri' : ''} ${b.danger ? 'danger' : ''}" data-i="${i}">${esc(b.label)}</button>`).join('')}</div></div>`;
    const done = v => { root.innerHTML = ''; document.removeEventListener('keydown', onKey, true); res(v); };
    const onKey = e => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); done(null); }
      if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); const p = buttons.find(b => b.pri); done(p ? p.value : null); }
    };
    document.addEventListener('keydown', onKey, true);
    root.onclick = e => { if (e.target === root) done(null); };
    root.querySelectorAll('[data-i]').forEach(b => b.onclick = () => done(buttons[+b.dataset.i].value));
    root.querySelector('.btn.pri')?.focus();
  });
}
const confirmBox = (title, body, label = 'OK', danger = false) =>
  ask({ title, body, buttons: [{ label: 'Cancel', value: false }, { label, value: true, pri: true, danger }] });

function toggleMenu(force) {
  const m = $('menu'); m.hidden = force === undefined ? !m.hidden : !force;
}

// ── loading ────────────────────────────────────────────────────────────────
async function loadState() {
  const d = await GET('/api/state');
  S.settings = d.settings; S.rolls = d.rolls;
  if (!rollById(S.active)) S.active = S.rolls[0]?.id || null;
}
async function refreshRoll(rid) {
  const fresh = await GET(`/api/rolls/${rid}`);
  const i = S.rolls.findIndex(r => r.id === rid);
  if (i < 0) S.rolls.push(fresh);
  else {                         // keep unsaved local edits for frames queued for save
    const q = _pending[rid] || {};
    fresh.frames = fresh.frames.map(f => q[f.id] ? { ...f, ...q[f.id] } : f);
    S.rolls[i] = fresh;
  }
}

// ── sidebar ────────────────────────────────────────────────────────────────
function renderSidebar() {
  const html = S.rolls.map(r => {
    const c = counts(r), job = Object.values(S.jobs).find(j => j.rollId === r.id && j.kind === 'import' && j.running);
    const done = c.split + c.full;
    const prog = job ? `<div class="r-prog" style="width:${job.total ? Math.round(job.done / job.total * 100) : 5}%"></div>` : '';
    return `<div class="roll${r.id === S.active ? ' active' : ''}" data-rid="${r.id}">
      <div class="r-top">
        <input class="r-in r-name" data-f="name" value="${esc(r.name)}" spellcheck="false" title="Roll name">
        <button class="icon-btn r-del" data-act="deleteRoll" title="Delete roll">✕</button>
      </div>
      <input class="r-in r-film" data-f="film" list="films" value="${esc(r.film)}" placeholder="film stock" spellcheck="false" title="Film stock">
      <div class="r-meta">
        <input class="r-in r-date" data-f="date" type="date" value="${esc(r.date)}" title="Date">
        <label class="r-ff${r.fullFrame ? ' on' : ''}" title="Full-frame roll: every frame is kept whole unless you split it">
          <input type="checkbox" data-f="fullFrame" ${r.fullFrame ? 'checked' : ''}>FULL FRAME</label>
      </div>
      <div class="r-stats">${job ? `importing ${job.done} / ${job.total}…` : `<b>${r.frames.length}</b> frames · <b>${done}</b> done${c.pending ? ` · <b>${c.pending}</b> to do` : ''}`}</div>${prog}
    </div>`;
  }).join('');
  $('rolls').innerHTML = html || `<div class="empty small" style="padding:30px 16px">No rolls yet.<br>Import a folder of scans to start.</div>`;
}

function onSidebarClick(e) {
  const item = e.target.closest('.roll'); if (!item) return;
  if (e.target.closest('input,label,button')) return;
  selectRoll(item.dataset.rid);
}
function onSidebarChange(e) {
  const item = e.target.closest('.roll'), f = e.target.dataset.f; if (!item || !f) return;
  const r = rollById(item.dataset.rid); if (!r) return;
  let v = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
  if (f === 'name') v = v.trim() || 'Untitled roll';
  if (r[f] === v) return;
  patchRoll(r, { [f]: v });
  if (f === 'fullFrame') { renderSidebar(); renderPanel(); }
}
function selectRoll(id, keepSidebar) {
  if (S.active === id) return;
  flush();
  S.active = id; S.idx = 0; resetView();
  if (keepSidebar) {                 // don't rebuild the sidebar while an input in it has focus
    document.querySelectorAll('.roll').forEach(el => el.classList.toggle('active', el.dataset.rid === id));
    renderPanel();
  } else renderAll();
}

// ── tabs / layout ──────────────────────────────────────────────────────────
function setTab(t) {
  if (S.tab === 'crop' && t !== 'crop') flush();
  S.tab = t; resetView(); renderAll();
}
function renderAll() { renderSidebar(); renderPanel(); }
function renderPanel() {
  const r = roll();
  document.querySelectorAll('.tab').forEach(el => {
    el.classList.toggle('active', el.dataset.tab === S.tab);
    el.classList.toggle('disabled', !r || !r.frames.length);
  });
  $('undoHint').textContent = S.undo.length ? 'Ctrl+Z to undo' : '';
  renderWarn(r);
  const p = $('panel');
  if (!r) {
    p.innerHTML = `<div class="empty"><img src="/ui/logo.png" alt=""><div class="big">Welcome to splitroll</div>
      <div>Import a folder of half-frame scans — each one becomes a roll.<br>You can also drag a folder onto this window.</div>
      <button class="btn pri" data-act="importNew">+ IMPORT FOLDER</button></div>`;
    return;
  }
  if (!r.frames.length) {
    const job = Object.values(S.jobs).find(j => j.rollId === r.id && j.running);
    p.innerHTML = `<div class="empty"><div class="big">${job ? 'Importing scans…' : 'This roll is empty'}</div>
      ${job ? `<div>${esc(job.msg || '')}</div>` : `<div>Add a folder of scans to this roll.</div><button class="btn pri" data-act="importInto">+ ADD SCANS</button>`}</div>`;
    return;
  }
  S.idx = Math.max(0, Math.min(S.idx, r.frames.length - 1));
  if (S.tab === 'contact') renderContact(p, r);
  else if (S.tab === 'crop') renderCrop(p, r);
  else renderExport(p, r);
}
function renderWarn(r) {
  const w = $('warn');
  const miss = r ? r.frames.filter(f => f.missing).length : 0;
  w.innerHTML = miss ? `<span>⚠</span><span style="flex:1">${miss} scan${miss > 1 ? 's are' : ' is'} missing — the folder may have moved or been renamed. Your edits are safe.</span>
    <button class="btn sm" data-act="relink">LOCATE FOLDER…</button>` : '';
}

// ── contact sheet ──────────────────────────────────────────────────────────
function picStyle(f) {
  const a = rotW(f) / rotH(f);
  return `aspect-ratio:${a.toFixed(5)};${a >= 1.5 ? 'width:100%' : 'height:100%'}`;
}
function renderContact(p, r) {
  p.innerHTML = contactBar(r) + `<div class="grid-wrap" id="gridWrap"><div class="grid" id="grid">${r.frames.map((f, i) => cardHtml(r, f, i)).join('')}</div></div>`;
}
function contactBar(r) {
  const c = counts(r), s = sel(r);
  const selActions = s.size ? `<div class="selbar"><span class="stat"><b>${s.size}</b> SELECTED</span>
      <button class="btn" data-act="selSplit" title="Split selected frames at their current cut">SPLIT</button>
      <button class="btn" data-act="selFull" title="Keep selected frames whole">FULL</button>
      <button class="btn" data-act="selReset" title="Back to pending">RESET</button>
      <button class="btn" data-act="selAuto" title="Re-run cut detection">AUTO-DETECT</button>
      <button class="btn" data-act="selRotate" title="Rotate selected scans 90°">↻ ROTATE</button>
      <button class="btn danger" data-act="selDelete" title="Remove from roll (files on disk are untouched)">REMOVE</button>
      <button class="btn" data-act="selClear">CLEAR</button></div>` : '';
  const all = s.size === r.frames.length;
  return `<div class="bar" id="csBar">
      ${s.size ? selActions : `<span class="stat"><b>${r.frames.length}</b> FRAMES · <b>${c.split}</b> SPLIT · <b>${c.full}</b> FULL · <b>${c.pending}</b> PENDING</span>`}
      <div class="sp"></div>
      <button class="btn" data-act="selAll">${all ? 'SELECT NONE' : 'SELECT ALL'}</button>
      ${s.size ? '' : `<div class="vd"></div>
      <button class="btn" data-act="importInto">+ ADD SCANS</button>
      <button class="btn" data-act="sheet" title="Save the contact sheet as a PNG">SAVE SHEET…</button>`}
    </div>`;
}
function refreshSelection() {
  const r = roll(), s = sel(r), bar = $('csBar');
  document.querySelectorAll('#grid .fc').forEach(el => el.classList.toggle('sel', s.has(r.frames[+el.dataset.i].id)));
  if (bar) bar.outerHTML = contactBar(r);
}
function cardHtml(r, f, i) {
  const st = eff(r, f), s = sel(r);
  let overlay = '';
  if (st !== 'full' && !f.missing) {
    const w = rotW(f), [le, rs] = splitBounds(w, f.cut, r.border);
    const L = le / w * 100, R = rs / w * 100;
    overlay = (st === 'split' && R > L ? `<div class="fc-gap" style="left:${L}%;width:${R - L}%"></div>` : '') +
      `<div class="fc-cut${st === 'pending' ? ' pend' : ''}" style="left:${(L + R) / 2}%"></div>`;
  }
  const body = f.missing ? `<div class="fc-miss">⚠ MISSING</div>`
    : `<div class="fc-pic" style="${picStyle(f)}"><img src="${img('thumb', f)}" loading="lazy" alt="">${overlay}</div>`;
  const off = st !== 'pending' && !f.export;
  return `<div class="fc${s.has(f.id) ? ' sel' : ''}${off ? ' off' : ''}" data-i="${i}" title="${esc(f.name)}${off ? ' · excluded from export' : ''}">
    <div class="fc-box">${body}</div>
    <div class="fc-foot"><span class="fc-num">${pad(i + 1)}</span><span class="fc-name">${esc(f.name)}</span>
    <span class="chip st-${st}">${st.toUpperCase()}</span></div></div>`;
}
function onGridClick(e) {
  const card = e.target.closest('.fc'); if (!card) return;
  const r = roll(), i = +card.dataset.i, f = r.frames[i], s = sel(r);
  if (e.shiftKey && S.anchor[r.id] != null) {
    const a = S.anchor[r.id], [lo, hi] = a < i ? [a, i] : [i, a];
    for (let k = lo; k <= hi; k++) s.add(r.frames[k].id);
  } else {
    s.has(f.id) ? s.delete(f.id) : s.add(f.id);
    S.anchor[r.id] = i;
  }
  refreshSelection();
}
function renderContactKeepScroll() {
  const g = $('gridWrap'), top = g ? g.scrollTop : 0;
  renderPanel();
  const g2 = $('gridWrap'); if (g2) g2.scrollTop = top;
}
function openCrop(i) { S.idx = i; setTab('crop'); }
function selFrames(r) { const s = sel(r); return r.frames.filter(f => s.has(f.id)); }

async function selAction(kind) {
  const r = roll(); if (!r) return;
  const fs = selFrames(r);
  if (kind === 'all') { const s = sel(r); if (s.size === r.frames.length) s.clear(); else r.frames.forEach(f => s.add(f.id)); return refreshSelection(); }
  if (kind === 'clear') { sel(r).clear(); return refreshSelection(); }
  if (!fs.length) return toast('Select frames first');
  if (kind === 'split') edit(r, fs, f => { f.state = 'split'; f.export = true; });
  if (kind === 'full') edit(r, fs, f => { f.state = 'full'; f.export = true; });
  if (kind === 'reset') edit(r, fs, f => { f.state = 'pending'; });
  if (kind === 'rotate') edit(r, fs, f => { f.rot = (f.rot + 90) % 360; });
  if (kind === 'auto') return autoDetect(r, fs);
  if (kind === 'delete') return removeFrames(r, fs);
  renderAll();
}
async function autoDetect(r, fs) {
  const before = fs.map(f => ({ id: f.id, before: pick(f) }));
  await flush();
  try {
    const res = await POST(`/api/rolls/${r.id}/detect`, { ids: fs.map(f => f.id) });
    res.frames.forEach(nf => { const f = r.frames.find(x => x.id === nf.id); if (f) Object.assign(f, pick(nf)); });
    before.forEach(it => it.after = pick(r.frames.find(x => x.id === it.id)));
    pushUndo({ rid: r.id, frames: before });
    toast(res.found ? `Found the gap on ${res.found} of ${fs.length} frame${fs.length > 1 ? 's' : ''}` : 'No clear gap found — set the cut by hand');
  } catch (e) { toast(e.message, { err: true }); }
  renderAll();
}
async function removeFrames(r, fs) {
  const ok = await confirmBox(`Remove ${fs.length} frame${fs.length > 1 ? 's' : ''}?`,
    `They'll be removed from <code>${esc(r.name)}</code>. Your scan files on disk are not touched. This can't be undone.`, 'REMOVE', true);
  if (!ok) return;
  await flush();
  await POST(`/api/rolls/${r.id}/frames/delete`, { ids: fs.map(f => f.id) });
  const ids = new Set(fs.map(f => f.id));
  r.frames = r.frames.filter(f => !ids.has(f.id));
  fs.forEach(f => sel(r).delete(f.id));
  S.undo = S.undo.filter(u => u.rid !== r.id); S.redo = [];
  toast(`Removed ${fs.length} frame${fs.length > 1 ? 's' : ''}`);
  renderAll();
}

// ── crop ───────────────────────────────────────────────────────────────────
const V = { img: null, key: null, mode: null, before: null, lastX: 0, lastY: 0, raf: 0 };
function resetView() { S.zoom = 1; S.panX = 0; S.panY = 0; }

function renderCrop(p, r) {
  const f = r.frames[S.idx], st = eff(r, f), n = r.frames.length, w = n > 99 ? 3 : 2;
  const full = st === 'full';
  p.innerHTML = `<div class="bar">
      <button class="btn" data-act="prev" title="Previous (←)">◀</button>
      <span class="stat" style="min-width:96px;text-align:center"><b>FRAME ${pad(S.idx + 1, w)}</b> / ${n}</span>
      <button class="btn" data-act="next" title="Next (→)">▶</button>
      <div class="vd"></div>
      <span class="lbl">CUT</span><span class="val" id="cutD">${full ? '—' : f.cut.toFixed(1) + '%'}</span>
      <button class="btn sm" data-act="auto" title="Auto-detect the gap (A)" ${full ? 'disabled' : ''}>AUTO</button>
      <div class="vd"></div>
      <span class="lbl">BORDER</span><input class="num-in" id="borderIn" type="number" min="0" max="5000" step="2" value="${r.border || 0}" title="Pixels removed around the cut, for the whole roll"><span class="lbl">PX</span>
      <div class="vd"></div>
      <button class="btn sm" data-act="rotScan" title="Rotate scan 90° (R)">↻ ROTATE</button>
      <div class="sp"></div>
      <span class="chip st-${st}">${st.toUpperCase()}${st !== 'pending' && !f.export ? ' · OFF' : ''}</span>
      ${f.state !== 'pending' ? `<button class="btn" data-act="resetFrame" title="Back to pending (U)">RESET</button>` : ''}
      <button class="btn" data-act="batch" title="Split every pending frame in this roll">SPLIT ALL…</button>
      <button class="btn${full ? ' on' : ''}" data-act="full" title="Keep this frame whole (F)">FULL</button>
      <button class="btn pri" data-act="split" title="Split at the cut and go to the next frame (Enter)">SPLIT ✓</button>
    </div>
    <div class="stage" id="stage"><canvas id="cv"></canvas>
      <div class="zoombox"><button class="icon-btn" data-act="zoomOut" title="Zoom out (−)">−</button><span id="zoomD">${Math.round(S.zoom * 100)}%</span><button class="icon-btn" data-act="zoomIn" title="Zoom in (+)">+</button><button class="icon-btn small" data-act="zoomFit" title="Fit (0)">FIT</button></div>
      <div class="hint">${full ? 'full frame — Enter splits it instead' : 'drag the line · double-click to place · , . nudge (Shift ×10) · A auto'}<br>scroll zoom · space/drag pan · ? all shortcuts</div></div>
    <div class="halves" id="halves"></div>`;
  loadCropImage(r, f);
  preload(r, S.idx + 1); preload(r, S.idx - 1);
}
function preload(r, i) { const f = r.frames[i]; if (f && !f.missing) { const im = new Image(); im.src = img('display', f); } }

function loadCropImage(r, f) {
  const key = f.id + '@' + f.rot;
  if (f.missing) { V.img = null; V.key = null; return drawMissing(); }
  if (V.key === key && V.img) { bindCanvas(); return drawCrop(); }
  V.img = null; V.key = key;
  sizeCanvas(); const ctx = $('cv').getContext('2d'); ctx.fillStyle = '#0b0b09'; ctx.fillRect(0, 0, 9999, 9999);
  const im = new Image();
  im.onload = () => { if (V.key !== key) return; V.img = im; bindCanvas(); drawCrop(); };
  im.onerror = () => { if (V.key === key) toast(`Couldn't open ${f.name}`, { err: true }); };
  im.src = img('display', f);
}
function sizeCanvas() {
  const st = $('stage'), cv = $('cv'); if (!st || !cv) return null;
  const dpr = window.devicePixelRatio || 1, w = st.clientWidth, h = st.clientHeight;
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
    cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
    cv.style.width = w + 'px'; cv.style.height = h + 'px';
  }
  const ctx = cv.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { w, h, ctx };
}
function drawMissing() {
  const s = sizeCanvas(); if (!s) return;
  const { ctx, w, h } = s;
  ctx.fillStyle = '#0b0b09'; ctx.fillRect(0, 0, w, h);
  ctx.textAlign = 'center'; ctx.fillStyle = '#d08050'; ctx.font = '14px ' + getComputedStyle(document.body).fontFamily;
  ctx.fillText('⚠  This scan is missing', w / 2, h / 2 - 8);
  ctx.fillStyle = '#8a6a50'; ctx.font = '11px ' + getComputedStyle(document.body).fontFamily;
  ctx.fillText('Use LOCATE FOLDER above to find it again', w / 2, h / 2 + 14);
  $('halves') && ($('halves').innerHTML = '');
}
function geo() {
  const st = $('stage'); if (!st || !V.img) return null;
  const cw = st.clientWidth, ch = st.clientHeight, P = 14;
  const iw = V.img.width, ih = V.img.height, ia = iw / ih, ca = (cw - 2 * P) / (ch - 2 * P);
  let dW, dH;
  if (ia > ca) { dW = cw - 2 * P; dH = dW / ia; } else { dH = ch - 2 * P; dW = dH * ia; }
  const dX = (cw - dW) / 2, dY = (ch - dH) / 2;
  const srcW = iw / S.zoom, srcH = ih / S.zoom;
  S.panX = Math.max(0, Math.min(iw - srcW, S.panX)); S.panY = Math.max(0, Math.min(ih - srcH, S.panY));
  return { dX, dY, dW, dH, iw, ih, srcW, srcH, k: dW / srcW };
}
function cutGeom(r, f, g) {                       // cut and border in display-image px
  const w = rotW(f), [le, rs] = splitBounds(w, f.cut, r.border), s = g.iw / w;
  return { le: le * s, rs: rs * s, mid: (le + rs) / 2 * s };
}
function drawCrop() {
  const r = roll(); if (!r || S.tab !== 'crop') return;
  const f = r.frames[S.idx], s = sizeCanvas(), g = geo(); if (!s || !g) return;
  const { ctx, w, h } = s, st = eff(r, f);
  ctx.fillStyle = '#0b0b09'; ctx.fillRect(0, 0, w, h);
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(V.img, S.panX, S.panY, g.srcW, g.srcH, g.dX, g.dY, g.dW, g.dH);
  if (st !== 'full') {
    const c = cutGeom(r, f, g), X = x => g.dX + (x - S.panX) * g.k;
    const xl = X(c.le), xr = X(c.rs), xm = X(c.mid), y0 = g.dY, y1 = g.dY + g.dH;
    ctx.save(); ctx.beginPath(); ctx.rect(g.dX, g.dY, g.dW, g.dH); ctx.clip();
    if (xr - xl > 0.5) { ctx.fillStyle = 'rgba(0,0,0,0.55)'; ctx.fillRect(xl, y0, xr - xl, y1 - y0); }
    const col = st === 'split' ? 'rgba(232,160,48,0.95)' : 'rgba(255,255,255,0.85)';
    ctx.strokeStyle = col; ctx.lineWidth = 1.5;
    if (xr - xl > 3) {
      ctx.setLineDash([6, 4]);
      [xl, xr].forEach(x => { ctx.beginPath(); ctx.moveTo(x, y0); ctx.lineTo(x, y1); ctx.stroke(); });
      ctx.setLineDash([]);
    } else { ctx.beginPath(); ctx.moveTo(xm, y0); ctx.lineTo(xm, y1); ctx.stroke(); }
    ctx.restore();
    if (xm >= g.dX - 1 && xm <= g.dX + g.dW + 1) {        // grab handle
      const hy = g.dY + g.dH / 2;
      ctx.fillStyle = col; ctx.strokeStyle = 'rgba(0,0,0,.5)'; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.roundRect ? ctx.roundRect(xm - 7, hy - 18, 14, 36, 7) : ctx.rect(xm - 7, hy - 18, 14, 36); ctx.fill(); ctx.stroke();
      ctx.fillStyle = '#141410'; ctx.fillRect(xm - 2.5, hy - 8, 1.2, 16); ctx.fillRect(xm + 1.3, hy - 8, 1.2, 16);
    }
  }
  scheduleHalves();
  const zd = $('zoomD'); if (zd) zd.textContent = Math.round(S.zoom * 100) + '%';
}
function scheduleHalves() { cancelAnimationFrame(V.raf); V.raf = requestAnimationFrame(drawHalves); }
function drawHalves() {
  const r = roll(), box = $('halves'); if (!r || !box || !V.img) return;
  const f = r.frames[S.idx], st = eff(r, f), g = { iw: V.img.width };
  const H = box.clientHeight - 44, ext = { tiff: 'tif', png: 'png', jpeg: 'jpg' }[S.settings.exportFormat || 'tiff'];
  const num = pad(S.idx + 1, r.frames.length > 99 ? 3 : 2), base = safeName(r.name), stem = safeName(f.name.replace(/\.[^.]+$/, ''));
  const src = S.settings.naming === 'source';
  if (st === 'full') {
    box.innerHTML = `<div class="half"><div class="half-top"><span class="lbl">FULL FRAME</span></div><div class="half-box"><canvas id="hvF"></canvas></div></div>
      <div class="half-info"><span class="muted" title="${esc(f.path)}">${esc(f.name)}</span><br>→ ${esc(src ? `${stem}.${ext}` : `${base}_${num}_full.${ext}`)}<br>${rotW(f)}×${rotH(f)} px${f.bits === 16 ? ' · 16-bit' : ''}</div>`;
    paintHalf($('hvF'), 0, V.img.width, 0, H, 520);
    return;
  }
  const c = cutGeom(r, f, g), w = rotW(f), [le, rs] = splitBounds(w, f.cut, r.border);
  const lw = f.lRot % 180 ? rotH(f) : le, lh = f.lRot % 180 ? le : rotH(f);
  const rw = f.rRot % 180 ? rotH(f) : w - rs, rh = f.rRot % 180 ? w - rs : rotH(f);
  box.innerHTML = `<div class="half"><div class="half-top"><span class="lbl">LEFT</span><button class="icon-btn" data-act="rotL" title="Rotate left half ([)">↻</button></div><div class="half-box"><canvas id="hvL"></canvas></div></div>
    <div class="half"><div class="half-top"><span class="lbl">RIGHT</span><button class="icon-btn" data-act="rotR" title="Rotate right half (])">↻</button></div><div class="half-box"><canvas id="hvR"></canvas></div></div>
    <div class="half-info"><span class="muted" title="${esc(f.path)}">${esc(f.name)}</span><br>→ ${esc(src ? `${stem}_L.${ext}` : `${base}_${num}L.${ext}`)} · ${lw}×${lh}<br>→ ${esc(src ? `${stem}_R.${ext}` : `${base}_${num}R.${ext}`)} · ${rw}×${rh}${f.bits === 16 ? '<br>16-bit' : ''}</div>`;
  paintHalf($('hvL'), 0, c.le, f.lRot, H, 300);
  paintHalf($('hvR'), c.rs, V.img.width, f.rRot, H, 300);
}
function paintHalf(cv, x0, x1, rot, maxH, maxW) {
  if (!cv || x1 - x0 < 1) return;
  const sw = x1 - x0, sh = V.img.height, rotd = rot % 180 !== 0;
  const ow = rotd ? sh : sw, oh = rotd ? sw : sh, dpr = window.devicePixelRatio || 1;
  const sc = Math.min(maxH / oh, maxW / ow), cw = Math.max(1, Math.round(ow * sc)), ch = Math.max(1, Math.round(oh * sc));
  cv.width = cw * dpr; cv.height = ch * dpr; cv.style.width = cw + 'px'; cv.style.height = ch + 'px';
  const ctx = cv.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.imageSmoothingQuality = 'high';
  ctx.translate(cw / 2, ch / 2); ctx.rotate(rot * Math.PI / 180);
  const dw = rotd ? ch : cw, dh = rotd ? cw : ch;
  ctx.drawImage(V.img, x0, 0, sw, sh, -dw / 2, -dh / 2, dw, dh);
}
function safeName(s) { return (String(s).replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').trim().replace(/[. ]+$/, '')) || 'roll'; }

function bindCanvas() {
  const cv = $('cv'); if (!cv || cv._bound) return; cv._bound = true;
  const mx = e => { const b = cv.getBoundingClientRect(); return [e.clientX - b.left, e.clientY - b.top]; };
  const nearLine = x => {
    const r = roll(), f = r.frames[S.idx], g = geo();
    if (!g || eff(r, f) === 'full') return false;
    return Math.abs(g.dX + (cutGeom(r, f, g).mid - S.panX) * g.k - x) <= 12;
  };
  cv.onmousedown = e => {
    if (!V.img) return;
    const [x, y] = mx(e);
    if (e.button === 1 || S.space) { V.mode = 'pan'; }
    else if (e.button === 0 && nearLine(x)) { const f = roll().frames[S.idx]; V.mode = 'cut'; V.before = pick(f); }
    else if (e.button === 0 && S.zoom > 1) { V.mode = 'pan'; }
    else return;
    e.preventDefault(); V.lastX = e.clientX; V.lastY = e.clientY;
    cv.style.cursor = V.mode === 'pan' ? 'grabbing' : 'ew-resize';
  };
  window.addEventListener('mousemove', e => {
    if (S.tab !== 'crop' || !V.img || !$('cv')) return;
    if (!V.mode) {
      if (e.target !== $('cv')) return;
      const [x] = mx(e);
      $('cv').style.cursor = S.space ? 'grab' : nearLine(x) ? 'ew-resize' : S.zoom > 1 ? 'grab' : 'default';
      return;
    }
    const g = geo(); if (!g) return;
    if (V.mode === 'pan') {
      S.panX -= (e.clientX - V.lastX) / g.k; S.panY -= (e.clientY - V.lastY) / g.k;
      V.lastX = e.clientX; V.lastY = e.clientY; drawCrop();
    } else if (V.mode === 'cut') {
      const [x] = mx(e); setCutFromX(x, g, false);
    }
  });
  window.addEventListener('mouseup', () => {
    if (!V.mode) return;
    if (V.mode === 'cut') {
      const r = roll(), f = r.frames[S.idx];
      if (V.before && V.before.cut !== f.cut) { pushUndo({ rid: r.id, frames: [{ id: f.id, before: V.before, after: pick(f) }] }); queue(r, f); }
    }
    V.mode = null; V.before = null; const c = $('cv'); if (c) c.style.cursor = 'default';
  });
  cv.ondblclick = e => {
    const r = roll(), f = r.frames[S.idx], g = geo(); if (!g || eff(r, f) === 'full') return;
    const [x] = mx(e); edit(r, [f], () => setCutFromX(x, g, true));
  };
  cv.onwheel = e => {
    e.preventDefault(); const g = geo(); if (!g) return;
    const [x, y] = mx(e), f = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    zoomAt(Math.max(1, Math.min(16, S.zoom * f)), x, y, g);
  };
}
function setCutFromX(x, g, silent) {
  const r = roll(), f = r.frames[S.idx], w = rotW(f);
  const imgX = S.panX + (x - g.dX) / g.k;
  f.cut = Math.max(5, Math.min(95, imgX / g.iw * 100)); f.auto = false;
  if (!silent) queue(r, f);
  const d = $('cutD'); if (d) d.textContent = f.cut.toFixed(1) + '%';
  drawCrop();
}
function zoomAt(nz, x, y, g) {
  g = g || geo(); if (!g) return;
  if (x === undefined) { x = g.dX + g.dW / 2; y = g.dY + g.dH / 2; }
  const ix = S.panX + (x - g.dX) / g.k, iy = S.panY + (y - g.dY) / g.k;
  S.zoom = nz;
  const k2 = g.dW / (g.iw / nz);
  S.panX = ix - (x - g.dX) / k2; S.panY = iy - (y - g.dY) / k2;
  if (nz === 1) { S.panX = 0; S.panY = 0; }
  drawCrop();
}
function nav(d) {
  const r = roll(); if (!r) return;
  const ni = S.idx + d; if (ni < 0 || ni >= r.frames.length) return;
  S.idx = ni; resetView(); renderPanel();
}
function nudge(delta) {
  const r = roll(), f = r.frames[S.idx]; if (eff(r, f) === 'full') return;
  const top = S.undo[S.undo.length - 1], now = Date.now();
  const before = pick(f);
  f.cut = Math.max(5, Math.min(95, +(f.cut + delta).toFixed(3))); f.auto = false; queue(r, f);
  if (top && top.kind === 'nudge' && top.fid === f.id && now - top.t < 1500) { top.frames[0].after = pick(f); top.t = now; }
  else { pushUndo({ rid: r.id, kind: 'nudge', fid: f.id, t: now, frames: [{ id: f.id, before, after: pick(f) }] }); }
  $('cutD').textContent = f.cut.toFixed(1) + '%'; drawCrop();
}
function setState(state) {
  const r = roll(), f = r.frames[S.idx];
  edit(r, [f], x => { x.state = state; if (state !== 'pending') x.export = true; });
  if (state !== 'pending' && S.idx < r.frames.length - 1) { S.idx++; resetView(); }
  else if (state !== 'pending') {
    const left = r.frames.filter(x => eff(r, x) === 'pending').length;
    toast(left ? `Last frame — ${left} still pending` : 'Roll done ✓', { action: { label: left ? 'NEXT PENDING' : 'EXPORT →', fn: () => left ? goPending() : setTab('export') } });
  }
  renderAll();
}
function goPending() {
  const r = roll(); const i = r.frames.findIndex(x => eff(r, x) === 'pending');
  if (i >= 0) { S.idx = i; setTab('crop'); }
}
async function batchSplit() {
  const r = roll(), f = r.frames[S.idx];
  const pend = r.frames.filter(x => x.state === 'pending' && !(r.fullFrame));
  const targets = r.fullFrame ? r.frames.filter(x => x.state === 'pending') : pend;
  if (!targets.length) return toast('No pending frames');
  const autoN = targets.filter(x => x.auto).length;
  const btns = [{ label: 'Cancel', value: null }, { label: `USE ${f.cut.toFixed(1)}% FOR ALL`, value: 'same', pri: !autoN }];
  if (autoN) btns.push({ label: 'KEEP DETECTED CUTS', value: 'auto', pri: true });
  const v = await ask({
    title: `Split ${targets.length} pending frame${targets.length > 1 ? 's' : ''}?`,
    body: `${autoN ? `${autoN} of them have an auto-detected cut. Keep those, and use ${f.cut.toFixed(1)}% for the rest — or use ${f.cut.toFixed(1)}% everywhere.` : `Every pending frame will be split at ${f.cut.toFixed(1)}%.`}<br>Border: <code>${r.border || 0}px</code>. You can still adjust any frame afterwards.`,
    buttons: btns,
  });
  if (!v) return;
  const cut = f.cut;
  edit(r, targets, x => { if (v === 'same' || !x.auto) x.cut = cut; x.state = 'split'; x.export = true; });
  toast(`Split ${targets.length} frames`); renderAll();
}
async function rotScan() {
  const r = roll(), f = r.frames[S.idx];
  edit(r, [f], x => { x.rot = (x.rot + 90) % 360; });
  resetView(); renderPanel();
  if (f.state === 'pending') {                     // a new orientation needs a new cut
    await flush();
    try {
      const res = await POST(`/api/rolls/${r.id}/detect`, { ids: [f.id] });
      const nf = res.frames[0]; if (nf) Object.assign(f, pick(nf));
      if (S.tab === 'crop') { $('cutD') && ($('cutD').textContent = f.cut.toFixed(1) + '%'); drawCrop(); }
    } catch (e) { /* keep the old cut */ }
  }
}
function rotHalf(side) {
  const r = roll(), f = r.frames[S.idx], k = side === 'l' ? 'lRot' : 'rRot';
  edit(r, [f], x => { x[k] = (x[k] + 90) % 360; }); drawHalves();
}
function setBorder(v, commit) {
  const r = roll(); v = Math.max(0, Math.min(5000, parseInt(v, 10) || 0));
  if (commit) { if (v !== (r._borderStart ?? r.border)) { const b = r._borderStart ?? r.border; r.border = b; patchRoll(r, { border: v }); } delete r._borderStart; renderContactIfVisible(); }
  else { if (r._borderStart === undefined) r._borderStart = r.border; r.border = v; }
  drawCrop();
}
function renderContactIfVisible() { if (S.tab === 'contact') renderContactKeepScroll(); }

// ── export ─────────────────────────────────────────────────────────────────
const FMT = {
  tiff: ['TIFF', 'lossless · keeps 16-bit and the colour profile'],
  png: ['PNG', 'lossless · keeps 16-bit'],
  jpeg: ['JPEG', '8-bit · quality 100, no chroma subsampling'],
};
function exportItems(r) { return r.frames.map((f, i) => ({ f, i, st: eff(r, f) })).filter(x => x.st !== 'pending'); }
function renderExport(p, r) {
  const items = exportItems(r), pend = r.frames.length - items.length;
  if (!items.length) {
    p.innerHTML = `<div class="empty"><div class="big">Nothing to export yet</div><div>Split frames — or mark them as full — in the Crop tab.</div>
      <button class="btn pri" data-act="goPending">GO TO CROP →</button></div>`;
    return;
  }
  const on = items.filter(x => x.f.export), files = on.reduce((n, x) => n + (x.st === 'split' ? 2 : 1), 0);
  const fmt = S.settings.exportFormat || 'tiff', naming = S.settings.naming || 'roll', dir = S.settings.exportDir || '';
  const has16 = r.frames.some(f => f.bits === 16);
  const w = r.frames.length > 99 ? 3 : 2, ext = { tiff: 'tif', png: 'png', jpeg: 'jpg' }[fmt], base = safeName(r.name);
  const job = S.exporting && S.jobs[S.exporting];
  p.innerHTML = `<div class="bar">
      <span class="stat"><b>${on.length}</b> OF ${items.length} FRAMES · <b>${files}</b> FILES</span>
      ${pend ? `<span class="stat" style="color:#c9a060">· ${pend} PENDING NOT INCLUDED</span><button class="btn sm" data-act="goPending">REVIEW</button>` : ''}
      <div class="sp"></div>
      <button class="btn" data-act="expAll">ALL</button><button class="btn" data-act="expNone">NONE</button>
    </div>
    <div class="exp-list" id="expList">${items.map(({ f, i, st }) => {
      const num = pad(i + 1, w), stem = safeName(f.name.replace(/\.[^.]+$/, ''));
      let names, dims;
      if (st === 'split') {
        const W = rotW(f), [le, rs] = splitBounds(W, f.cut, r.border), H = rotH(f);
        names = naming === 'source' ? `${stem}_L.${ext} · ${stem}_R.${ext}` : `${base}_${num}L.${ext} · ${base}_${num}R.${ext}`;
        dims = `${f.lRot % 180 ? `${H}×${le}` : `${le}×${H}`} + ${f.rRot % 180 ? `${H}×${W - rs}` : `${W - rs}×${H}`} px · cut ${f.cut.toFixed(1)}%`;
      } else {
        names = naming === 'source' ? `${stem}.${ext}` : `${base}_${num}_full.${ext}`;
        dims = `full frame · ${rotW(f)}×${rotH(f)} px`;
      }
      return `<div class="exp-row${f.export ? '' : ' off'}" data-i="${i}">
        <input type="checkbox" data-exp="${f.id}" ${f.export ? 'checked' : ''} title="Include in export">
        <div class="exp-thumbs" data-thumb="${i}"></div>
        <div class="exp-info"><div class="exp-name">${esc(names)}</div>
          <div class="exp-sub">${pad(i + 1, w)} · ${esc(f.name)} · ${dims}${f.bits === 16 ? ' · 16-bit' : ''}${f.missing ? ' · <span style="color:#d08050">MISSING</span>' : ''}</div></div>
        <button class="btn sm" data-act="editFrame" data-i="${i}">EDIT</button>
      </div>`;
    }).join('')}</div>
    <div class="exp-foot">
      <span class="lbl">FORMAT</span>
      <div class="seg">${Object.entries(FMT).map(([k, v]) => `<button data-fmt="${k}" class="${k === fmt ? 'on' : ''}">${v[0]}</button>`).join('')}</div>
      <span class="small dim" style="max-width:220px">${FMT[fmt][1]}${has16 && fmt === 'jpeg' ? ' — your 16-bit scans will be reduced to 8-bit' : ''}</span>
      <div class="vd"></div>
      <span class="lbl">NAMES</span>
      <div class="seg"><button data-naming="roll" class="${naming === 'roll' ? 'on' : ''}" title="${esc(base)}_01L.${ext}">ROLL_01L</button><button data-naming="source" class="${naming === 'source' ? 'on' : ''}" title="original filename + _L / _R">ORIGINAL_L</button></div>
      <div class="sp"></div>
      ${job && job.running ? `<div class="prog"><i style="width:${job.total ? Math.round(job.done / job.total * 100) : 0}%"></i></div><span class="small muted">${job.done}/${job.total}</span>` : ''}
      <span class="exp-dir" title="${esc(dir)}">${dir ? esc(dir) : '<span class="dim">no folder chosen</span>'}</span>
      <button class="btn" data-act="chooseDir">${dir ? 'CHANGE…' : 'CHOOSE FOLDER…'}</button>
      <button class="btn pri" data-act="export" ${(!files || (job && job.running)) ? 'disabled' : ''}>EXPORT ${files} FILE${files === 1 ? '' : 'S'}</button>
    </div>`;
  drawExportThumbs(r);
}
function drawExportThumbs(r) {
  document.querySelectorAll('[data-thumb]').forEach(box => {
    const f = r.frames[+box.dataset.thumb], st = eff(r, f);
    if (f.missing) { box.innerHTML = '<span class="small" style="color:#d08050">missing</span>'; return; }
    const im = new Image();
    im.onload = () => {
      const mk = (x0, x1, rot, mw, mh) => {
        const cv = document.createElement('canvas'), sw = x1 - x0, rd = rot % 180 !== 0;
        const ow = rd ? im.height : sw, oh = rd ? sw : im.height, sc = Math.min(mw / ow, mh / oh), dpr = window.devicePixelRatio || 1;
        const cw = Math.max(1, Math.round(ow * sc)), ch = Math.max(1, Math.round(oh * sc));
        cv.width = cw * dpr; cv.height = ch * dpr; cv.style.width = cw + 'px'; cv.style.height = ch + 'px';
        const c = cv.getContext('2d'); c.setTransform(dpr, 0, 0, dpr, 0, 0); c.translate(cw / 2, ch / 2); c.rotate(rot * Math.PI / 180);
        const dw = rd ? ch : cw, dh = rd ? cw : ch; c.drawImage(im, x0, 0, sw, im.height, -dw / 2, -dh / 2, dw, dh);
        box.appendChild(cv);
      };
      if (st === 'split') {
        const W = rotW(f), [le, rs] = splitBounds(W, f.cut, r.border), s = im.width / W;
        mk(0, le * s, f.lRot, 50, 64); mk(rs * s, im.width, f.rRot, 50, 64);
      } else mk(0, im.width, 0, 104, 64);
    };
    im.src = img('thumb', f);
  });
}
async function setSetting(k, v) {
  S.settings[k] = v; renderPanel();
  try { await PATCH('/api/settings', { [k]: v }); } catch (e) { /* non-fatal */ }
}
async function pickFolder(title, dir) {
  try { return (await POST('/api/pick', { kind: 'folder', title, dir: dir || '' })).path || null; }
  catch (e) { toast(e.message, { err: true }); return null; }
}
async function chooseExportDir() {
  const d = await pickFolder('Export to…', S.settings.exportDir || S.settings.lastImportDir);
  if (d) await setSetting('exportDir', d);
  return d;
}
async function doExport() {
  const r = roll(); if (!r) return;
  await flush();
  const ids = exportItems(r).filter(x => x.f.export).map(x => x.f.id);
  if (!ids.length) return toast('Nothing selected to export');
  let dir = S.settings.exportDir;
  if (!dir) { dir = await chooseExportDir(); if (!dir) return; }
  const fmt = S.settings.exportFormat || 'tiff', naming = S.settings.naming || 'roll';
  let conflict = 'overwrite';
  try {
    const chk = await POST(`/api/rolls/${r.id}/export/check`, { dir, format: fmt, naming, ids });
    if (chk.conflictCount) {
      const v = await ask({
        title: 'Some files already exist',
        body: `${chk.conflictCount} of ${chk.files} files are already in <code>${esc(dir)}</code>, e.g. <code>${esc(chk.conflicts[0])}</code>.`,
        buttons: [{ label: 'Cancel', value: null }, { label: 'OVERWRITE', value: 'overwrite', danger: true }, { label: 'SKIP EXISTING', value: 'skip', pri: true }],
      });
      if (!v) return; conflict = v;
    }
    const res = await POST(`/api/rolls/${r.id}/export`, { dir, format: fmt, naming, ids, conflict });
    S.exporting = res.job.id;
    track(res.job, j => {
      S.exporting = null;
      const failed = j.failed ? { err: true } : {};
      toast(j.msg, { ...failed, action: j.result?.dir ? { label: 'OPEN FOLDER', fn: () => POST('/api/reveal', { path: j.result.dir }) } : null });
      if (j.errors?.length) console.warn(j.errors);
      if (S.tab === 'export') renderPanel();
    });
    renderPanel();
  } catch (e) { toast(e.message, { err: true }); }
}

// ── jobs ───────────────────────────────────────────────────────────────────
const _done = {}; let _pollT = null;
function track(job, onDone) {
  S.jobs[job.id] = job; if (onDone) _done[job.id] = onDone;
  if (!_pollT) _pollT = setTimeout(poll, 350);
}
async function poll() {
  _pollT = null;
  const running = Object.values(S.jobs).filter(j => j.running);
  for (const j of running) {
    let n;
    try { n = await GET(`/api/jobs/${j.id}`); } catch (e) { n = { ...j, running: false, msg: 'Lost track of a background task' }; }
    const progressed = n.done !== j.done;
    S.jobs[j.id] = n;
    if (n.kind === 'import' && (progressed || !n.running)) {
      try { await refreshRoll(n.rollId); } catch (e) { /* roll deleted */ }
      renderSidebar();
      if (S.active === n.rollId && (S.tab === 'contact' || !roll()?.frames.length)) renderContactKeepScroll();
    } else if (n.kind === 'export' && S.tab === 'export') {
      const pr = document.querySelector('.prog i'); if (pr) pr.style.width = (n.total ? n.done / n.total * 100 : 0) + '%';
      else renderPanel();
    }
    if (!n.running) {
      const cb = _done[n.id]; delete _done[n.id];
      if (cb) cb(n);
      setTimeout(() => { delete S.jobs[n.id]; }, 1000);
    }
  }
  if (Object.values(S.jobs).some(j => j.running)) _pollT = setTimeout(poll, 450);
  else renderSidebar();
}

// ── import / relink / sheet / library ──────────────────────────────────────
async function startImport(path, rollId) {
  try {
    const res = await POST('/api/import', { path, rollId: rollId || null });
    await refreshRoll(res.rollId);
    S.active = res.rollId; S.tab = 'contact'; S.idx = 0;
    track(res.job, j => {
      const r = rollById(res.rollId);
      toast((r ? r.name + ': ' : '') + j.msg, j.failed ? { err: true } : {});
      if (j.errors?.length) console.warn(j.errors);
      renderAll();
    });
    renderAll();
  } catch (e) { toast(e.message, { err: true }); }
}
async function importNew() {
  const p = await pickFolder('Choose a folder of scans', S.settings.lastImportDir);
  if (p) startImport(p, null);
}
async function importInto() {
  const r = roll(); if (!r) return importNew();
  const p = await pickFolder(`Add scans to ${r.name}`, S.settings.lastImportDir);
  if (p) startImport(p, r.id);
}
async function relink() {
  const r = roll(); if (!r) return;
  const p = await pickFolder('Choose the folder that now holds these scans', S.settings.lastImportDir);
  if (!p) return;
  try {
    const res = await POST(`/api/rolls/${r.id}/relink`, { path: p });
    await refreshRoll(r.id); V.key = null; renderAll();
    if (res.relinked && !res.missing) toast(`Found all ${res.relinked} scans ✓`);
    else if (res.relinked) toast(`Found ${res.relinked} · ${res.missing} still missing`);
    else toast('No matching filenames in that folder', { err: true });
  } catch (e) { toast(e.message, { err: true }); }
}
async function saveSheet() {
  const r = roll(); if (!r) return;
  await flush();
  const p = await pickFolder('Save contact sheet to…', S.settings.exportDir || S.settings.lastImportDir);
  if (!p) return;
  try {
    const res = await POST(`/api/rolls/${r.id}/contact-sheet`, { dir: p });
    toast('Building contact sheet…');
    track(res.job, j => toast(j.msg, { err: !j.result, action: j.result?.path ? { label: 'SHOW', fn: () => POST('/api/reveal', { path: j.result.path }) } : null }));
  } catch (e) { toast(e.message, { err: true }); }
}
async function importLibrary() {
  toggleMenu(false);
  try {
    const p = (await POST('/api/pick', { kind: 'file', title: 'Choose halfframe_data.json', types: ['Library (*.json)'] })).path;
    if (!p) return;
    const res = await POST('/api/library/import', { path: p });
    await loadState();
    if (!S.active && S.rolls.length) S.active = S.rolls[0].id;
    renderAll();
    toast(`Imported ${res.rolls} roll${res.rolls === 1 ? '' : 's'} (${res.frames} frames). Use LOCATE FOLDER if scans live somewhere else now.`, { ms: 8000 });
  } catch (e) { toast(e.message, { err: true }); }
}
async function newRoll() {
  toggleMenu(false);
  const r = await POST('/api/rolls', {});
  S.rolls.push(r); S.active = r.id; S.tab = 'contact'; renderAll();
  setTimeout(() => document.querySelector(`.roll[data-rid="${r.id}"] .r-name`)?.select(), 30);
}
async function deleteRoll(rid) {
  const r = rollById(rid);
  const ok = await confirmBox('Delete roll?', `<code>${esc(r.name)}</code> and all its crop settings will be removed. Your scan files are not touched.`, 'DELETE', true);
  if (!ok) return;
  await flush();
  await api('DELETE', `/api/rolls/${rid}`);
  S.rolls = S.rolls.filter(x => x.id !== rid);
  S.undo = S.undo.filter(u => u.rid !== rid); S.redo = [];
  if (S.active === rid) { S.active = S.rolls[0]?.id || null; S.idx = 0; }
  renderAll();
}
function showShortcuts() {
  toggleMenu(false);
  const k = (keys, what) => `<div>${keys.map(x => `<kbd>${x}</kbd>`).join(' ')}</div><div>${what}</div>`;
  ask({
    title: 'Keyboard shortcuts', buttons: [{ label: 'CLOSE', value: true, pri: true }],
    body: `<div class="keys">
      <h4>EVERYWHERE</h4>${k(['1', '2', '3'], 'Contact sheet / Crop / Export')}${k(['Ctrl', 'Z'], 'Undo')}${k(['Ctrl', 'Shift', 'Z'], 'Redo')}${k(['Ctrl', 'I'], 'Import folder as new roll')}${k(['Ctrl', '+', '−', '0'], 'Interface size bigger / smaller / reset')}${k(['?'], 'This list')}
      <h4>CONTACT SHEET</h4>${k(['Click'], 'Select · Shift-click selects a range')}${k(['Double-click'], 'Open in Crop')}${k(['Ctrl', 'A'], 'Select all')}${k(['Esc'], 'Clear selection')}${k(['Del'], 'Remove selected from roll')}
      <h4>CROP</h4>${k(['←', '→'], 'Previous / next frame')}${k(['Enter'], 'Split and go to next')}${k(['F'], 'Full frame and go to next')}${k(['U'], 'Back to pending')}
      ${k([',', '.'], 'Nudge cut (hold Shift for ×10)')}${k(['A'], 'Auto-detect cut')}${k(['R'], 'Rotate scan')}${k(['[', ']'], 'Rotate left / right half')}
      ${k(['+', '−', '0'], 'Zoom in / out / fit')}${k(['Space', 'drag'], 'Pan')}</div>`,
  });
}
function showAbout() {
  toggleMenu(false);
  ask({
    title: `splitroll ${window.SR?.version || ''}`, buttons: [{ label: 'CLOSE', value: true, pri: true }],
    body: `Split half-frame 35mm scans into pairs, build contact sheets and export lossless files.<br><br>
      Your library is stored locally. Scans are never modified.<br>
      <button class="btn sm" style="margin-top:10px" data-act="openData">OPEN DATA FOLDER</button>`,
  });
}

// ── interface size (WebView2 zoom on Windows; browser zoom elsewhere) ─────
const ZOOMS = [0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2];
async function uiZoom(dir) {
  const cur = S.settings.uiZoom || 1;
  let i = ZOOMS.findIndex(z => z >= cur - 1e-6); if (i < 0) i = ZOOMS.length - 1;
  const next = dir === 0 ? 1 : ZOOMS[Math.max(0, Math.min(ZOOMS.length - 1, i + dir))];
  try {
    const r = await POST('/api/zoom', { factor: next });
    S.settings.uiZoom = r.factor;
    toast(r.native ? `Interface size ${Math.round(r.factor * 100)}%` : 'Use your browser zoom (Ctrl + / −) here');
  } catch (e) { toast(e.message, { err: true }); }
}

// ── native drag & drop (paths arrive from the app shell) ───────────────────
const SRApp = window.SRApp = {
  onNativeDrop(paths) {
    document.body.classList.remove('dragging');
    const r = roll();
    paths.forEach((p, k) => startImport(p, (k === 0 && paths.length === 1 && r && !r.frames.length) ? r.id : null));
  },
};
let _dragDepth = 0;
document.addEventListener('dragenter', e => { if (e.dataTransfer?.types?.includes('Files')) { _dragDepth++; document.body.classList.add('dragging'); } });
document.addEventListener('dragleave', () => { if (--_dragDepth <= 0) { _dragDepth = 0; document.body.classList.remove('dragging'); } });
document.addEventListener('dragover', e => e.preventDefault());
document.addEventListener('drop', e => {
  e.preventDefault(); _dragDepth = 0; document.body.classList.remove('dragging');
  if (!window.pywebview) toast('Drag & drop works in the app window — use IMPORT FOLDER here');
});

// ── actions + keyboard ─────────────────────────────────────────────────────
const ACTIONS = {
  zoomUi: () => { toggleMenu(false); ask({ title: 'Interface size', body: `Currently ${Math.round((S.settings.uiZoom || 1) * 100)}%.<br>Use <code>Ctrl +</code> / <code>Ctrl −</code> to change it and <code>Ctrl 0</code> to reset.`, buttons: [{ label: 'SMALLER', value: -1 }, { label: 'RESET', value: 0 }, { label: 'BIGGER', value: 1, pri: true }] }).then(v => v !== null && v !== undefined && uiZoom(v)); },
  importNew, importInto, relink, menu: () => toggleMenu(), newRoll, importLibrary, shortcuts: showShortcuts, about: showAbout,
  openData: () => { toggleMenu(false); POST('/api/reveal', { path: '@data' }); },
  sheet: saveSheet, selAll: () => selAction('all'), selClear: () => selAction('clear'),
  selSplit: () => selAction('split'), selFull: () => selAction('full'), selReset: () => selAction('reset'),
  selRotate: () => selAction('rotate'), selAuto: () => selAction('auto'), selDelete: () => selAction('delete'),
  prev: () => nav(-1), next: () => nav(1), split: () => setState('split'), full: () => setState('full'),
  resetFrame: () => setState('pending'), auto: () => { const r = roll(); autoDetect(r, [r.frames[S.idx]]); },
  batch: batchSplit, rotScan, rotL: () => rotHalf('l'), rotR: () => rotHalf('r'),
  zoomIn: () => zoomAt(Math.min(16, S.zoom * 1.25)), zoomOut: () => zoomAt(Math.max(1, S.zoom / 1.25)), zoomFit: () => zoomAt(1),
  goPending, chooseDir: chooseExportDir, export: doExport,
  expAll: () => { const r = roll(); edit(r, exportItems(r).map(x => x.f), f => f.export = true); renderPanel(); },
  expNone: () => { const r = roll(); edit(r, exportItems(r).map(x => x.f), f => f.export = false); renderPanel(); },
};
document.addEventListener('click', e => {
  const m = $('menu'); if (!m.hidden && !e.target.closest('#menu,[data-act="menu"]')) toggleMenu(false);
  const t = e.target.closest('[data-act]');
  if (t && !t.disabled) {
    const a = t.dataset.act;
    if (a === 'deleteRoll') return deleteRoll(t.closest('.roll').dataset.rid);
    if (a === 'editFrame') { S.idx = +t.dataset.i; return setTab('crop'); }
    ACTIONS[a]?.(); return;
  }
  const tab = e.target.closest('.tab'); if (tab) return setTab(tab.dataset.tab);
  if (e.target.closest('#grid')) return onGridClick(e);
  const fmt = e.target.closest('[data-fmt]'); if (fmt) return setSetting('exportFormat', fmt.dataset.fmt);
  const nm = e.target.closest('[data-naming]'); if (nm) return setSetting('naming', nm.dataset.naming);
  const row = e.target.closest('.exp-row');
  if (row && !e.target.closest('input')) { const cb = row.querySelector('input'); cb.checked = !cb.checked; cb.dispatchEvent(new Event('change', { bubbles: true })); }
});
document.addEventListener('dblclick', e => {
  const card = e.target.closest('#grid .fc'); if (card) openCrop(+card.dataset.i);
});
document.addEventListener('change', e => {
  if (e.target.closest('#rolls')) return onSidebarChange(e);
  if (e.target.dataset.exp) {
    const r = roll(), f = r.frames.find(x => x.id === e.target.dataset.exp);
    edit(r, [f], x => x.export = e.target.checked); renderPanel();
  }
  if (e.target.id === 'borderIn') setBorder(e.target.value, true);
});
document.addEventListener('input', e => { if (e.target.id === 'borderIn') setBorder(e.target.value, false); });
document.addEventListener('keydown', e => {
  if (e.target.closest?.('input, select, textarea')) {
    if (e.key === 'Enter' || e.key === 'Escape') e.target.blur();
    return;
  }
  if ($('modalRoot').innerHTML) return;
  if (e.key === 'Escape' && !$('menu').hidden) return toggleMenu(false);
  const ctrl = e.ctrlKey || e.metaKey, key = e.key;
  if (ctrl && key.toLowerCase() === 'z') { e.preventDefault(); return e.shiftKey ? redo() : undo(); }
  if (ctrl && key.toLowerCase() === 'y') { e.preventDefault(); return redo(); }
  if (ctrl && key.toLowerCase() === 'i') { e.preventDefault(); return importNew(); }
  if (ctrl && (key === '=' || key === '+')) { e.preventDefault(); return uiZoom(1); }
  if (ctrl && key === '-') { e.preventDefault(); return uiZoom(-1); }
  if (ctrl && key === '0') { e.preventDefault(); return uiZoom(0); }
  if (key === '?') return showShortcuts();
  const r = roll(); if (!r || !r.frames.length) return;
  if (!ctrl && ['1', '2', '3'].includes(key)) return setTab(['contact', 'crop', 'export'][+key - 1]);
  if (S.tab === 'contact') {
    if (ctrl && key.toLowerCase() === 'a') { e.preventDefault(); r.frames.forEach(f => sel(r).add(f.id)); return refreshSelection(); }
    if (key === 'Escape') return selAction('clear');
    if (key === 'Delete' || key === 'Backspace') return selAction('delete');
    if (key === 'Enter') { const s = sel(r); const i = r.frames.findIndex(f => s.has(f.id)); return openCrop(Math.max(0, i)); }
    return;
  }
  if (S.tab !== 'crop' || ctrl) return;
  if (key === ' ') { e.preventDefault(); S.space = true; const c = $('cv'); if (c) c.style.cursor = 'grab'; return; }
  const map = {
    ArrowLeft: () => nav(-1), ArrowRight: () => nav(1), Enter: () => setState('split'), f: () => setState('full'),
    u: () => setState('pending'), a: ACTIONS.auto, r: rotScan, '[': () => rotHalf('l'), ']': () => rotHalf('r'),
    ',': () => nudge(e.shiftKey ? -0.5 : -0.05), '<': () => nudge(-0.5), '.': () => nudge(e.shiftKey ? 0.5 : 0.05), '>': () => nudge(0.5),
    '+': ACTIONS.zoomIn, '=': ACTIONS.zoomIn, '-': ACTIONS.zoomOut, '0': ACTIONS.zoomFit,
  };
  const fn = map[key] || map[key.toLowerCase()];
  if (fn) { e.preventDefault(); fn(); }
});
document.addEventListener('keyup', e => { if (e.key === ' ') { S.space = false; const c = $('cv'); if (c) c.style.cursor = 'default'; } });
window.addEventListener('resize', () => { if (S.tab === 'crop') drawCrop(); });
window.addEventListener('beforeunload', () => { flush(); });

// ── boot ───────────────────────────────────────────────────────────────────
(async function init() {
  $('films').innerHTML = FILMS.map(f => `<option value="${esc(f)}">`).join('');
  $('rolls').addEventListener('click', onSidebarClick);
  $('rolls').addEventListener('focusin', e => { const it = e.target.closest('.roll'); if (it) selectRoll(it.dataset.rid, true); });
  try { await loadState(); }
  catch (e) { toast('Could not load your library: ' + e.message, { err: true, ms: 15000 }); }
  renderAll();
  try { (await GET('/api/jobs')).filter(j => j.running).forEach(j => track(j)); } catch (e) { /* none */ }
})();
