import asyncio
import base64
import hashlib
import os
from typing import Any, Callable, Optional

from aiohttp import web
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

WEB_PORT = int(os.getenv("PORT", os.getenv("WEB_PORT", "8080")))
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "").strip()
COOKIE_NAME = "qcrm_auth"


def _auth_token() -> str:
    return hashlib.sha256(f"qcrm:{WEB_PASSWORD}".encode("utf-8")).hexdigest()


def _is_authorized(request: web.Request) -> bool:
    if not WEB_PASSWORD:
        return True
    return request.cookies.get(COOKIE_NAME) == _auth_token()


def _get_logo_html() -> str:
    for fname in ("logo.png", "logo.jpg", "logo.jpeg"):
        if os.path.isfile(fname):
            ext = fname.rsplit(".", 1)[-1]
            with open(fname, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            return f'<img src="data:image/{ext};base64,{b64}" alt="QCRM" class="logo-img">'
    # Фирменный SVG-значок QHEAD в чёрно-оранжевых тонах
    return """
    <svg class="logo-svg" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="2" y="2" width="60" height="60" rx="14" fill="#111113" stroke="#FF5A1F" stroke-width="3"/>
      <path d="M16 14H40C42 14 43 16 42 18C39 23 39 29 44 29C49 29 52 33 52 37C52 42 48 45 43 45C40 45 39 48 41 51H16V37C24 37 32 34 35 30C31 27 23 26 16 26V14Z" fill="#FF5A1F"/>
      <path d="M15 28C22 28 29 30 33 33C29 36 22 38 15 38V28Z" fill="#FFFFFF"/>
      <circle cx="24" cy="33" r="3.5" fill="#111113"/>
    </svg>
    """


LOGIN_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>QCRM — Вход</title>
<style>
  :root { --bg:#09090b; --card:#141417; --border:#27272a; --orange:#ff5a1f; --text:#f4f4f5; --muted:#a1a1aa; }
  * { box-sizing:border-box; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; }
  body { margin:0; background:var(--bg); color:var(--text); min-height:100vh; display:flex; align-items:center; justify-content:center; padding:20px; }
  .box { width:100%; max-width:360px; background:var(--card); border:1px solid var(--border); border-radius:20px; padding:28px; text-align:center; box-shadow:0 20px 50px rgba(0,0,0,.6); }
  .logo-svg,.logo-img { width:56px; height:56px; border-radius:14px; object-fit:cover; margin-bottom:12px; }
  h1 { margin:0 0 6px; font-size:24px; letter-spacing:.5px; }
  h1 span { color:var(--orange); }
  p { margin:0 0 20px; color:var(--muted); font-size:14px; }
  input { width:100%; padding:13px 14px; border-radius:12px; border:1px solid var(--border); background:#0d0d10; color:#fff; font-size:16px; outline:none; margin-bottom:12px; }
  input:focus { border-color:var(--orange); }
  button { width:100%; padding:13px; border:none; border-radius:12px; background:var(--orange); color:#fff; font-weight:700; font-size:15px; cursor:pointer; }
  .err { color:#ef4444; font-size:13px; margin-bottom:10px; }
</style>
</head>
<body>
  <form class="box" method="post" action="/login">
    __LOGO__
    <h1>Q<span>CRM</span></h1>
    <p>QHEAD Mobile • Панель управления</p>
    __ERR__
    <input type="password" name="password" placeholder="Пароль доступа" required autofocus>
    <button type="submit">Войти</button>
  </form>
</body>
</html>"""


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>QCRM — QHEAD Mobile</title>
<style>
  :root {
    --bg: #09090b;
    --surface: #121215;
    --card: #18181c;
    --card-2: #1f1f25;
    --border: #2a2a32;
    --orange: #ff5a1f;
    --orange-soft: rgba(255,90,31,.14);
    --green: #22c55e;
    --green-soft: rgba(34,197,94,.14);
    --red: #ef4444;
    --red-soft: rgba(239,68,68,.14);
    --blue: #3b82f6;
    --text: #f4f4f5;
    --muted: #a1a1aa;
  }
  * { box-sizing:border-box; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; }
  body { margin:0; background:var(--bg); color:var(--text); padding-bottom:60px; }
  .topbar {
    position:sticky; top:0; z-index:30;
    background:rgba(12,12,15,.92); backdrop-filter:blur(12px);
    border-bottom:1px solid var(--border);
    padding:12px 16px; display:flex; align-items:center; justify-content:space-between; gap:12px;
  }
  .brand { display:flex; align-items:center; gap:10px; }
  .logo-svg,.logo-img { width:38px; height:38px; border-radius:10px; object-fit:cover; flex-shrink:0; }
  .brand-title { font-weight:800; font-size:20px; letter-spacing:.5px; line-height:1.1; }
  .brand-title span { color:var(--orange); }
  .brand-sub { font-size:11px; color:var(--muted); letter-spacing:.8px; text-transform:uppercase; }
  .tabs { display:flex; gap:8px; padding:12px 16px 4px; max-width:1160px; margin:0 auto; overflow-x:auto; }
  .tab {
    border:1px solid var(--border); background:var(--surface); color:var(--muted);
    padding:9px 15px; border-radius:12px; font-weight:600; font-size:14px; cursor:pointer; white-space:nowrap;
  }
  .tab.active { background:var(--orange); color:#fff; border-color:var(--orange); }
  .wrap { max-width:1160px; margin:0 auto; padding:12px 16px; }
  .kpi-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(165px,1fr)); gap:10px; margin-bottom:18px; }
  .kpi { background:var(--card); border:1px solid var(--border); border-radius:16px; padding:14px; }
  .kpi-label { font-size:12px; color:var(--muted); margin-bottom:6px; }
  .kpi-val { font-size:20px; font-weight:800; }
  .kpi-val.orange { color:var(--orange); }
  .kpi-val.green { color:var(--green); }
  .toolbar { display:flex; flex-wrap:wrap; gap:10px; align-items:center; justify-content:space-between; margin-bottom:14px; }
  .search {
    flex:1; min-width:210px; background:var(--surface); border:1px solid var(--border);
    color:#fff; padding:10px 14px; border-radius:12px; font-size:14px; outline:none;
  }
  .search:focus { border-color:var(--orange); }
  .btn {
    border:none; border-radius:11px; padding:9px 14px; font-weight:600; font-size:13px;
    cursor:pointer; display:inline-flex; align-items:center; gap:6px; transition:.15s;
  }
  .btn-orange { background:var(--orange); color:#fff; }
  .btn-dark { background:var(--card-2); color:var(--text); border:1px solid var(--border); }
  .btn-green { background:var(--green-soft); color:var(--green); border:1px solid rgba(34,197,94,.3); }
  .btn-red { background:var(--red-soft); color:var(--red); border:1px solid rgba(239,68,68,.3); }
  .btn-sm { padding:6px 10px; font-size:12px; border-radius:9px; }

  .ship-list { display:flex; flex-direction:column; gap:14px; }
  .ship-card { background:var(--card); border:1px solid var(--border); border-radius:18px; overflow:hidden; }
  .ship-head {
    padding:14px 16px; background:var(--surface); border-bottom:1px solid var(--border);
    display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:10px;
  }
  .ship-title { font-size:17px; font-weight:700; display:flex; align-items:center; gap:8px; }
  .badge { font-size:11px; font-weight:700; padding:3px 8px; border-radius:999px; }
  .badge-active { background:var(--orange-soft); color:var(--orange); }
  .badge-arch { background:#27272a; color:var(--muted); }
  .badge-sold { background:var(--green-soft); color:var(--green); }
  .ship-actions { display:flex; flex-wrap:wrap; gap:6px; }
  .items-grid {
    padding:12px; display:grid; grid-template-columns:repeat(auto-fill,minmax(250px,1fr)); gap:10px;
  }
  .micro-card {
    background:var(--card-2); border:1px solid var(--border); border-radius:14px;
    padding:12px; display:flex; flex-direction:column; justify-content:space-between; gap:8px;
  }
  .micro-top { display:flex; justify-content:space-between; align-items:flex-start; gap:8px; }
  .micro-name { font-weight:700; font-size:14px; line-height:1.3; }
  .track-pill {
    display:inline-block; background:#0d0d10; border:1px solid var(--border);
    padding:4px 8px; border-radius:8px; font-family:ui-monospace,SFMono-Regular,monospace;
    font-size:12px; color:#e4e4e7; cursor:pointer; user-select:all;
  }
  .meta-row { font-size:12px; color:var(--muted); display:flex; flex-wrap:wrap; gap:6px; align-items:center; }
  .owner-my { color:var(--orange); font-weight:600; }
  .owner-client { color:#60a5fa; font-weight:600; }
  .micro-actions { display:flex; flex-wrap:wrap; gap:6px; margin-top:2px; }

  .posts-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(290px,1fr)); gap:12px; }
  .post-card {
    background:var(--card); border:1px solid var(--border); border-radius:16px; padding:14px;
    display:flex; flex-direction:column; justify-content:space-between; gap:12px;
  }
  .post-text { font-size:13px; line-height:1.45; white-space:pre-wrap; color:#e4e4e7; max-height:190px; overflow:auto; }
  .post-foot { display:flex; align-items:center; justify-content:space-between; gap:8px; border-top:1px solid var(--border); padding-top:10px; }

  .modal-bg {
    position:fixed; inset:0; background:rgba(0,0,0,.72); z-index:50;
    display:none; align-items:center; justify-content:center; padding:16px;
  }
  .modal-bg.open { display:flex; }
  .modal {
    width:100%; max-width:420px; background:var(--card); border:1px solid var(--border);
    border-radius:18px; padding:18px;
  }
  .modal h3 { margin:0 0 12px; font-size:17px; }
  .field { margin-bottom:10px; }
  .field label { display:block; font-size:12px; color:var(--muted); margin-bottom:4px; }
  .field input, .field select {
    width:100%; padding:10px 12px; border-radius:10px; border:1px solid var(--border);
    background:#0d0d10; color:#fff; font-size:14px; outline:none;
  }
  .modal-btns { display:flex; justify-content:flex-end; gap:8px; margin-top:14px; }
  .toast {
    position:fixed; bottom:20px; left:50%; transform:translateX(-50%);
    background:#27272a; color:#fff; padding:10px 16px; border-radius:12px;
    border:1px solid var(--orange); font-size:13px; z-index:60; display:none;
  }
  .empty { text-align:center; padding:34px 16px; color:var(--muted); background:var(--card); border:1px dashed var(--border); border-radius:16px; }
</style>
</head>
<body>
  <header class="topbar">
    <div class="brand">
      __LOGO__
      <div>
        <div class="brand-title">Q<span>CRM</span></div>
        <div class="brand-sub">QHEAD Mobile</div>
      </div>
    </div>
    <button class="btn btn-dark btn-sm" onclick="loadAll()">🔄 Обновить</button>
  </header>

  <nav class="tabs">
    <button class="tab active" onclick="switchTab('crm', this)">📦 CRM Отправки</button>
    <button class="tab" onclick="switchTab('direct', this)">🏷 Свои товары</button>
    <button class="tab" onclick="switchTab('posts', this)">📝 В наличии (Посты)</button>
    <button class="tab" onclick="switchTab('archive', this)">🗄 Архив</button>
  </nav>

  <main class="wrap">
    <section id="kpiBar" class="kpi-grid"></section>

    <section id="view-crm">
      <div class="toolbar">
        <input class="search" id="crmSearch" placeholder="Поиск по отправке, товару, треку или клиенту..." oninput="render()">
        <button class="btn btn-orange" onclick="openNewShipment()">+ Новая отправка</button>
      </div>
      <div id="crmList" class="ship-list"></div>
    </section>

    <section id="view-direct" style="display:none">
      <div class="toolbar">
        <input class="search" id="directSearch" placeholder="Поиск по названию или состоянию..." oninput="render()">
        <button class="btn btn-orange" onclick="openNewDirect()">+ Добавить свой товар</button>
      </div>
      <div id="directList" class="posts-grid"></div>
    </section>

    <section id="view-posts" style="display:none">
      <div class="toolbar">
        <input class="search" id="postsSearch" placeholder="Поиск по тексту объявления в наличии..." oninput="render()">
      </div>
      <div id="postsList" class="posts-grid"></div>
    </section>

    <section id="view-archive" style="display:none">
      <div class="toolbar">
        <input class="search" id="archSearch" placeholder="Поиск в архиве..." oninput="render()">
      </div>
      <div id="archList" class="ship-list"></div>
    </section>
  </main>

  <div id="modalBg" class="modal-bg" onclick="if(event.target===this) closeModal()">
    <div class="modal" id="modalBody"></div>
  </div>
  <div id="toast" class="toast"></div>

<script>
let STATE = { finances: {}, shipments: [], posts: [], direct_items: [] };
let CURRENT_TAB = 'crm';

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function fmtMoney(v) {
  const n = Number(v || 0);
  return Math.round(n).toLocaleString('ru-RU') + ' ₽';
}
function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', 2400);
}
function copyText(txt) {
  if (!txt) return;
  navigator.clipboard?.writeText(txt);
  toast('Скопировано: ' + txt);
}
function switchTab(tab, btn) {
  CURRENT_TAB = tab;
  document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('view-crm').style.display = tab === 'crm' ? '' : 'none';
  document.getElementById('view-direct').style.display = tab === 'direct' ? '' : 'none';
  document.getElementById('view-posts').style.display = tab === 'posts' ? '' : 'none';
  document.getElementById('view-archive').style.display = tab === 'archive' ? '' : 'none';
  render();
}

async function api(url, method='GET', body=null) {
  const opts = { method, headers: {'Content-Type': 'application/json'} };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(url, opts);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'Ошибка запроса');
  return data;
}

async function loadAll() {
  try {
    STATE = await api('/api/dashboard');
    render();
  } catch (e) {
    toast(e.message);
  }
}

function render() {
  const f = STATE.finances || {};
  document.getElementById('kpiBar').innerHTML = `
    <div class="kpi"><div class="kpi-label">Вложено (товары + доставка)</div><div class="kpi-val orange">${fmtMoney(f.total_spent)}</div></div>
    <div class="kpi"><div class="kpi-label">В пути (мои товары)</div><div class="kpi-val">${fmtMoney(f.invested_active)}</div></div>
    <div class="kpi"><div class="kpi-label">Доставка (архив)</div><div class="kpi-val">${fmtMoney(f.delivery_total)}</div></div>
    <div class="kpi"><div class="kpi-label">Выручка с продаж</div><div class="kpi-val green">${fmtMoney(f.revenue_total)}</div></div>
    <div class="kpi"><div class="kpi-label">Прибыль по проданным</div><div class="kpi-val green">${fmtMoney(f.sold_profit_items)}</div></div>
  `;

  if (CURRENT_TAB === 'crm') renderShipments('active', 'crmList', 'crmSearch');
  if (CURRENT_TAB === 'archive') renderShipments('archived', 'archList', 'archSearch');
  if (CURRENT_TAB === 'direct') renderDirect();
  if (CURRENT_TAB === 'posts') renderPosts();
}

function renderShipments(status, listId, searchId) {
  const q = (document.getElementById(searchId).value || '').toLowerCase();
  const ships = STATE.shipments.filter(s => s.status === status).filter(s => {
    if (!q) return true;
    if (s.name.toLowerCase().includes(q)) return true;
    return s.items.some(it =>
      it.name.toLowerCase().includes(q) ||
      (it.track_number || '').toLowerCase().includes(q) ||
      (it.client_contact || '').toLowerCase().includes(q)
    );
  });

  const el = document.getElementById(listId);
  if (!ships.length) {
    el.innerHTML = `<div class="empty">Ничего не найдено</div>`;
    return;
  }

  el.innerHTML = ships.map(s => {
    const itemsHtml = s.items.length ? s.items.map(it => {
      let ownerHtml = '';
      if (it.owner_type === 'my') {
        const soldTag = it.sold_price ? ` <span class="badge badge-sold">Продано: ${esc(it.sold_price)}</span>` : '';
        ownerHtml = `<span class="owner-my">Мой • ${esc(it.price || '0 ₽')}</span>${soldTag}`;
      } else if (it.owner_type === 'client') {
        ownerHtml = `<span class="owner-client">Клиент • ${esc(it.client_contact || '—')}</span>`;
      } else {
        ownerHtml = `<span>Владелец не указан</span>`;
      }
      const sellBtn = (it.owner_type === 'my' && !it.sold_price)
        ? `<button class="btn btn-green btn-sm" onclick="openSellItem(${it.id}, '${esc(it.name)}')">💰 Продан</button>`
        : '';
      return `
        <div class="micro-card">
          <div>
            <div class="micro-top">
              <div class="micro-name">${esc(it.name)}</div>
            </div>
            <div style="margin:6px 0">
              <span class="track-pill" onclick="copyText('${esc(it.track_number)}')" title="Нажмите, чтобы скопировать">${esc(it.track_number || 'без трека')}</span>
            </div>
            <div class="meta-row">${ownerHtml}</div>
          </div>
          <div class="micro-actions">
            ${sellBtn}
            <button class="btn btn-dark btn-sm" onclick="openEditItem(${it.id})">✏️</button>
            <button class="btn btn-red btn-sm" onclick="delItem(${it.id})">🗑</button>
          </div>
        </div>
      `;
    }).join('') : `<div style="padding:14px;color:var(--muted);font-size:13px">Товаров пока нет</div>`;

    const arriveBtn = status === 'active'
      ? `<button class="btn btn-green btn-sm" onclick="openArrive(${s.id}, '${esc(s.name)}')">🚚 Приехал</button>`
      : `<span class="badge badge-arch">Доставка: ${fmtMoney(s.delivery_cost)}</span>`;

    return `
      <div class="ship-card">
        <div class="ship-head">
          <div class="ship-title">
            📦 ${esc(s.name)}
            <span class="badge ${status==='active'?'badge-active':'badge-arch'}">${s.items.length} шт.</span>
          </div>
          <div class="ship-actions">
            <button class="btn btn-dark btn-sm" onclick="copyTracks(${s.id})">📋 Список треков</button>
            <button class="btn btn-orange btn-sm" onclick="openAddItem(${s.id})">+ Товар</button>
            ${arriveBtn}
          </div>
        </div>
        <div class="items-grid">${itemsHtml}</div>
      </div>
    `;
  }).join('');
}

function renderPosts() {
  const q = (document.getElementById('postsSearch').value || '').toLowerCase();
  const activePosts = STATE.posts.filter(p => p.status === 'active' && (!q || p.text.toLowerCase().includes(q)));
  const el = document.getElementById('postsList');
  if (!activePosts.length) {
    el.innerHTML = `<div class="empty">Активных постов в наличии пока нет.<br><br>Создайте пост через бота — он сразу появится здесь для быстрого закрытия!</div>`;
    return;
  }
  el.innerHTML = activePosts.map(p => {
    const linked = p.linked_item_name ? `<span class="badge badge-active">🔗 ${esc(p.linked_item_name)}</span>` : '';
    return `
      <div class="post-card">
        <div class="post-text">${esc(p.text)}</div>
        <div class="post-foot">
          <div>${linked}</div>
          <button class="btn btn-red btn-sm" onclick="openMarkPostSold(${p.id}, ${p.linked_item_id || 'null'})">❌ ПРОДАНО</button>
        </div>
      </div>
    `;
  }).join('');
}

function copyTracks(shipId) {
  const s = STATE.shipments.find(x => x.id === shipId);
  if (!s) return;
  const lines = [`📦 ${s.name}`, ''];
  s.items.forEach((it, i) => lines.push(`${i+1}. ${it.name} — ${it.track_number || 'без трека'}`));
  copyText(lines.join('\\n'));
}

function openModal(html) {
  document.getElementById('modalBody').innerHTML = html;
  document.getElementById('modalBg').classList.add('open');
}
function closeModal() {
  document.getElementById('modalBg').classList.remove('open');
}

function openNewShipment() {
  openModal(`
    <h3>📦 Новая отправка</h3>
    <div class="field"><label>Название отправки</label><input id="mShipName" placeholder="Например: Ноябрь №2"></div>
    <div class="modal-btns">
      <button class="btn btn-dark" onclick="closeModal()">Отмена</button>
      <button class="btn btn-orange" onclick="submitNewShipment()">Создать</button>
    </div>
  `);
}
async function submitNewShipment() {
  const name = document.getElementById('mShipName').value.trim();
  if (!name) return;
  await api('/api/shipments', 'POST', { name });
  closeModal(); toast('Отправка создана'); 
function renderDirect() {
  const q = (document.getElementById('directSearch').value || '').toLowerCase();
  const items = (STATE.direct_items || []).filter(it =>
    !q || it.title.toLowerCase().includes(q) || (it.condition || '').toLowerCase().includes(q)
  );
  const el = document.getElementById('directList');
  if (!items.length) {
    el.innerHTML = `<div class="empty">Своих товаров вне отправок пока нет.<br><br>Нажмите «+ Добавить свой товар», чтобы вести учёт.</div>`;
    return;
  }
  el.innerHTML = items.map(it => {
    const img = it.photo_url ? `<img src="${esc(it.photo_url)}" style="width:100%;max-height:190px;object-fit:cover;border-radius:12px;margin-bottom:8px">` : '';
    const cond = it.condition ? `<span class="badge badge-arch">${esc(it.condition)}</span>` : '';
    const soldBadge = it.status === 'sold'
      ? `<span class="badge badge-sold">Продано: ${fmtMoney(it.sell_price)} (+${fmtMoney((it.sell_price||0)-(it.buy_price||0))})</span>`
      : `<button class="btn btn-green btn-sm" onclick="openSellDirect(${it.id}, '${esc(it.title)}')">💰 Продан</button>`;
    return `
      <div class="post-card">
        <div>
          ${img}
          <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:6px">
            <strong style="font-size:15px">${esc(it.title)}</strong>
            ${cond}
          </div>
          <div class="meta-row"><span class="owner-my">Заход: ${fmtMoney(it.buy_price)}</span></div>
        </div>
        <div class="post-foot">
          ${soldBadge}
          <button class="btn btn-red btn-sm" onclick="delDirect(${it.id})">🗑</button>
        </div>
      </div>
    `;
  }).join('');
}

function compressImage(file) {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = (e) => {
      const img = new Image();
      img.onload = () => {
        const maxSide = 700;
        let w = img.width, h = img.height;
        if (w > maxSide || h > maxSide) {
          if (w > h) { h = Math.round(h * maxSide / w); w = maxSide; }
          else { w = Math.round(w * maxSide / h); h = maxSide; }
        }
        const c = document.createElement('canvas');
        c.width = w; c.height = h;
        c.getContext('2d').drawImage(img, 0, 0, w, h);
        resolve(c.toDataURL('image/jpeg', 0.72));
      };
      img.src = e.target.result;
    };
    reader.readAsDataURL(file);
  });
}

function openNewDirect() {
  openModal(`
    <h3>🏷 Добавить свой товар</h3>
    <div class="field"><label>Название</label><input id="dTitle" placeholder="Nintendo 3DS XL"></div>
    <div class="field"><label>Состояние</label><input id="dCond" placeholder="9/10 (отличное)"></div>
    <div class="field"><label>Цена захода (₽)</label><input id="dBuy" placeholder="8500"></div>
    <div class="field">
      <label>Фото (необязательно)</label>
      <div style="background:rgba(255,90,31,.12);border:1px solid rgba(255,90,31,.4);color:#fdba74;padding:8px 10px;border-radius:10px;font-size:12px;margin-bottom:6px">
        ⚠️ Если фото не нужно — лучше не добавляйте, чтобы не грузить сервер и бота.
      </div>
      <input type="file" id="dPhoto" accept="image/*">
    </div>
    <div class="modal-btns">
      <button class="btn btn-dark" onclick="closeModal()">Отмена</button>
      <button class="btn btn-orange" onclick="submitNewDirect()">Сохранить</button>
    </div>
  `);
}

async function submitNewDirect() {
  const title = document.getElementById('dTitle').value.trim();
  const condition = document.getElementById('dCond').value.trim();
  const buy_price = document.getElementById('dBuy').value.trim();
  if (!title) return;
  let photo_b64 = '';
  const f = document.getElementById('dPhoto').files[0];
  if (f) photo_b64 = await compressImage(f);
  await api('/api/direct', 'POST', { title, condition, buy_price, photo_b64 });
  closeModal(); toast('Товар добавлен'); loadAll();
}

function openSellDirect(id, title) {
  openModal(`
    <h3>💰 Продажа товара</h3>
    <p style="font-size:13px;color:var(--muted);margin-top:0">${esc(title)}</p>
    <div class="field"><label>Цена продажи (₽)</label><input id="dSellPrice" placeholder="14500"></div>
    <div class="modal-btns">
      <button class="btn btn-dark" onclick="closeModal()">Отмена</button>
      <button class="btn btn-orange" onclick="submitSellDirect(${id})">Сохранить</button>
    </div>
  `);
}
async function submitSellDirect(id) {
  const sell_price = document.getElementById('dSellPrice').value.trim();
  if (!sell_price) return;
  await api(`/api/direct/${id}/sell`, 'POST', { sell_price });
  closeModal(); toast('Продажа записана'); loadAll();
}
async function delDirect(id) {
  if (!confirm('Удалить товар?')) return;
  await api(`/api/direct/${id}`, 'DELETE');
  toast('Удалено'); loadAll();
}

loadAll();
}

function openAddItem(shipId) {
  openModal(`
    <h3>➕ Новый товар</h3>
    <div class="field"><label>Название товара</label><input id="mItemName" placeholder="Nintendo 3DS XL"></div>
    <div class="field"><label>Трек-номер</label><input id="mItemTrack" placeholder="YT1234567890"></div>
    <div class="field"><label>Чей товар</label>
      <select id="mItemOwner" onchange="toggleOwnerField()">
        <option value="my">👤 Мой (в базу закупа)</option>
        <option value="client">🧑‍💼 Клиент</option>
      </select>
    </div>
    <div class="field" id="mOwnerValWrap"><label id="mOwnerValLbl">Цена закупки</label><input id="mItemVal" placeholder="10500 ₽"></div>
    <div class="modal-btns">
      <button class="btn btn-dark" onclick="closeModal()">Отмена</button>
      <button class="btn btn-orange" onclick="submitAddItem(${shipId})">Сохранить</button>
    </div>
  `);
}
function toggleOwnerField() {
  const t = document.getElementById('mItemOwner').value;
  document.getElementById('mOwnerValLbl').textContent = t === 'my' ? 'Цена закупки' : 'Telegram ID / имя клиента';
  document.getElementById('mItemVal').placeholder = t === 'my' ? '10500 ₽' : '@username';
}
async function submitAddItem(shipId) {
  const name = document.getElementById('mItemName').value.trim();
  const track_number = document.getElementById('mItemTrack').value.trim();
  const owner_type = document.getElementById('mItemOwner').value;
  const val = document.getElementById('mItemVal').value.trim();
  if (!name) return;
  await api(`/api/shipments/${shipId}/items`, 'POST', { name, track_number, owner_type, val });
  closeModal(); toast('Товар добавлен'); loadAll();
}

function openEditItem(itemId) {
  let item = null;
  for (const s of STATE.shipments) {
    item = s.items.find(i => i.id === itemId);
    if (item) break;
  }
  if (!item) return;
  const val = item.owner_type === 'my' ? item.price : item.client_contact;
  openModal(`
    <h3>✏️ Редактировать товар</h3>
    <div class="field"><label>Название</label><input id="eItemName" value="${esc(item.name)}"></div>
    <div class="field"><label>Трек-номер</label><input id="eItemTrack" value="${esc(item.track_number)}"></div>
    <div class="field"><label>Чей товар</label>
      <select id="eItemOwner">
        <option value="my" ${item.owner_type==='my'?'selected':''}>👤 Мой</option>
        <option value="client" ${item.owner_type==='client'?'selected':''}>🧑‍💼 Клиент</option>
      </select>
    </div>
    <div class="field"><label>Цена закупки / Клиент</label><input id="eItemVal" value="${esc(val || '')}"></div>
    <div class="modal-btns">
      <button class="btn btn-dark" onclick="closeModal()">Отмена</button>
      <button class="btn btn-orange" onclick="submitEditItem(${itemId})">Сохранить</button>
    </div>
  `);
}
async function submitEditItem(itemId) {
  await api(`/api/items/${itemId}`, 'PUT', {
    name: document.getElementById('eItemName').value.trim(),
    track_number: document.getElementById('eItemTrack').value.trim(),
    owner_type: document.getElementById('eItemOwner').value,
    val: document.getElementById('eItemVal').value.trim(),
  });
  closeModal(); toast('Обновлено'); loadAll();
}

async function delItem(itemId) {
  if (!confirm('Удалить этот товар?')) return;
  await api(`/api/items/${itemId}`, 'DELETE');
  toast('Товар удалён'); loadAll();
}

function openSellItem(itemId, name) {
  openModal(`
    <h3>💰 Продажа товара</h3>
    <p style="font-size:13px;color:var(--muted);margin-top:0">${esc(name)}</p>
    <div class="field"><label>Цена продажи</label><input id="mSellPrice" placeholder="14990 ₽"></div>
    <div class="modal-btns">
      <button class="btn btn-dark" onclick="closeModal()">Отмена</button>
      <button class="btn btn-orange" onclick="submitSellItem(${itemId})">Сохранить</button>
    </div>
  `);
}
async function submitSellItem(itemId) {
  const sold_price = document.getElementById('mSellPrice').value.trim();
  if (!sold_price) return;
  await api(`/api/items/${itemId}/sell`, 'POST', { sold_price });
  closeModal(); toast('Продажа записана'); loadAll();
}

function openArrive(shipId, name) {
  openModal(`
    <h3>🚚 Груз приехал</h3>
    <p style="font-size:13px;color:var(--muted);margin-top:0">${esc(name)} отправится в архив.</p>
    <div class="field"><label>Цена доставки (₽)</label><input id="mDelivCost" placeholder="3500" value="0"></div>
    <div class="modal-btns">
      <button class="btn btn-dark" onclick="closeModal()">Отмена</button>
      <button class="btn btn-orange" onclick="submitArrive(${shipId})">В архив</button>
    </div>
  `);
}
async function submitArrive(shipId) {
  const delivery_cost = document.getElementById('mDelivCost').value.trim();
  await api(`/api/shipments/${shipId}/arrive`, 'POST', { delivery_cost });
  closeModal(); toast('Перенесено в архив'); loadAll();
}

function openMarkPostSold(postId, linkedItemId) {
  const extra = linkedItemId
    ? `<div class="field"><label>Цена продажи для CRM (необязательно)</label><input id="mPostSell" placeholder="14990 ₽"></div>`
    : '';
  openModal(`
    <h3>❌ Закрыть объявление</h3>
    <p style="font-size:13px;color:var(--muted);margin-top:0">Бот удалит #НАЛИЧИЕ, зачеркнёт текст в канале (сохранив премиум-стикеры) и поменяет кнопку на ПРОДАНО❌.</p>
    ${extra}
    <div class="modal-btns">
      <button class="btn btn-dark" onclick="closeModal()">Отмена</button>
      <button class="btn btn-red" onclick="submitMarkPostSold(${postId})">Отметить ПРОДАНО</button>
    </div>
  `);
}
async function submitMarkPostSold(postId) {
  const el = document.getElementById('mPostSell');
  const sold_price = el ? el.value.trim() : '';
  await api(`/api/posts/${postId}/sold`, 'POST', { sold_price });
  closeModal(); toast('Пост обновлён в канале!'); loadAll();
}

loadAll();
</script>
</body>
</html>"""


def create_web_app(bot: Bot, deps: dict[str, Callable[..., Any]]) -> web.Application:
    app = web.Application()

    @web.middleware
    async def auth_mw(request: web.Request, handler):
        if request.path in ("/login", "/health"):
            return await handler(request)
        if not _is_authorized(request):
            if request.path.startswith("/api/"):
                return web.json_response({"error": "Unauthorized"}, status=401)
            raise web.HTTPFound("/login")
        return await handler(request)

    app.middlewares.append(auth_mw)

    async def handle_health(_: web.Request) -> web.Response:
        return web.Response(text="OK")

    async def handle_login_get(_: web.Request) -> web.Response:
        html = LOGIN_HTML.replace("__LOGO__", _get_logo_html()).replace("__ERR__", "")
        return web.Response(text=html, content_type="text/html")

    async def handle_login_post(request: web.Request) -> web.Response:
        data = await request.post()
        pwd = str(data.get("password", "")).strip()
        if not WEB_PASSWORD or pwd == WEB_PASSWORD:
            resp = web.HTTPFound("/")
            resp.set_cookie(COOKIE_NAME, _auth_token(), httponly=True, max_age=86400 * 30)
            raise resp
        html = LOGIN_HTML.replace("__LOGO__", _get_logo_html()).replace(
            "__ERR__", '<div class="err">Неверный пароль</div>'
        )
        return web.Response(text=html, content_type="text/html", status=401)

    async def handle_index(_: web.Request) -> web.Response:
        html = DASHBOARD_HTML.replace("__LOGO__", _get_logo_html())
        return web.Response(text=html, content_type="text/html")

    async def handle_dashboard(_: web.Request) -> web.Response:
        finances = deps["get_crm_finances"]()
        all_ships = []
        for status in ("active", "archived"):
            for s in deps["get_shipments"](status):
                sd = dict(s)
                sd["items"] = [dict(i) for i in deps["get_items"](s["id"])]
                all_ships.append(sd)

        posts = [dict(p) for p in deps["get_all_posts"]()]
        owner_hash = deps["get_owner_hash"]()
        direct_items = []
        if owner_hash:
            for de in deps["get_personal_entries_by_hash"](owner_hash):
                d = dict(de)
                pref = d.get("photo_ref") or ""
                d["photo_url"] = f"/api/photo/{pref}" if pref.startswith("web_") else ""
                direct_items.append(d)
        return web.json_response({
            "finances": finances,
            "shipments": all_ships,
            "posts": posts,
            "direct_items": direct_items,
        })

    async def handle_create_shipment(request: web.Request) -> web.Response:
        body = await request.json()
        name = str(body.get("name", "")).strip()
        if not name:
            return web.json_response({"error": "Введите название"}, status=400)
        sid = deps["create_shipment"](0, name)
        return web.json_response({"id": sid})

    async def handle_arrive_shipment(request: web.Request) -> web.Response:
        ship_id = int(request.match_info["id"])
        body = await request.json()
        cost = deps["parse_amount"](str(body.get("delivery_cost", "0")))
        deps["archive_shipment"](ship_id, cost)
        return web.json_response({"ok": True})

    async def handle_add_item(request: web.Request) -> web.Response:
        ship_id = int(request.match_info["id"])
        body = await request.json()
        iid = deps["create_item"](ship_id, str(body.get("name", "")), str(body.get("track_number", "")))
        owner_type = body.get("owner_type", "my")
        val = str(body.get("val", ""))
        if owner_type == "my":
            deps["update_item_my_price"](iid, val)
        else:
            deps["update_item_client"](iid, val)
        return web.json_response({"id": iid})

    async def handle_update_item(request: web.Request) -> web.Response:
        item_id = int(request.match_info["id"])
        body = await request.json()
        deps["update_item_name"](item_id, str(body.get("name", "")))
        deps["update_item_track"](item_id, str(body.get("track_number", "")))
        owner_type = body.get("owner_type", "my")
        val = str(body.get("val", ""))
        if owner_type == "my":
            deps["update_item_my_price"](item_id, val)
        else:
            deps["update_item_client"](item_id, val)
        return web.json_response({"ok": True})

    async def handle_delete_item(request: web.Request) -> web.Response:
        item_id = int(request.match_info["id"])
        deps["delete_item"](item_id)
        return web.json_response({"ok": True})

    async def handle_sell_item(request: web.Request) -> web.Response:
        item_id = int(request.match_info["id"])
        body = await request.json()
        deps["mark_item_sold"](item_id, str(body.get("sold_price", "")))
        return web.json_response({"ok": True})

    async def handle_mark_post_sold(request: web.Request) -> web.Response:
        post_id = int(request.match_info["id"])
        body = await request.json()
        post = deps["get_post_by_id"](post_id)
        if not post:
            return web.json_response({"error": "Пост не найден"}, status=404)

        entities = deps["deserialize_entities"](post["entities_json"])
        new_text, new_entities = deps["build_sold_text_and_entities"](post["text"], entities)
        sold_kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="ПРОДАНО❌", url="https://t.me/QQHead")]]
        )

        try:
            await bot.edit_message_text(
                chat_id=post["channel_id"],
                message_id=post["message_id"],
                text=new_text,
                entities=new_entities or None,
                parse_mode=None,
                reply_markup=sold_kb,
                disable_web_page_preview=True,
            )
            deps["mark_post_sold"](
                post["channel_id"],
                post["message_id"],
                new_text,
                deps["serialize_entities"](new_entities),
            )
        except TelegramBadRequest as e:
            try:
                await bot.edit_message_reply_markup(
                    chat_id=post["channel_id"],
                    message_id=post["message_id"],
                    reply_markup=sold_kb,
                )
                deps["mark_post_sold"](
                    post["channel_id"],
                    post["message_id"],
                    post["text"],
                    post["entities_json"],
                )
            except TelegramBadRequest as e2:
                return web.json_response({"error": str(e2)}, status=400)

        sold_price = str(body.get("sold_price", "")).strip()
        if post["linked_item_id"] and sold_price:
            deps["mark_item_sold"](post["linked_item_id"], sold_price)

        return web.json_response({"ok": True})


    async def handle_create_direct(request: web.Request) -> web.Response:
        owner_hash = deps["get_owner_hash"]()
        if not owner_hash:
            return web.json_response({"error": "Сначала нажмите /start в боте"}, status=400)
        body = await request.json()
        title = str(body.get("title", "")).strip()
        if not title:
            return web.json_response({"error": "Введите название"}, status=400)
        condition = str(body.get("condition", "")).strip()
        buy_price = deps["parse_amount"](str(body.get("buy_price", "0")))
        photo_b64 = str(body.get("photo_b64", "")).strip()
        photo_ref = ""
        if photo_b64.startswith("data:image/"):
            import uuid
            b64_part = photo_b64.split(",", 1)[1]
            data_dir = os.getenv("DATA_DIR", "/app/data")
            photos_dir = os.path.join(data_dir, "photos") if os.path.isdir(data_dir) else "photos"
            os.makedirs(photos_dir, exist_ok=True)
            fname = f"web_{uuid.uuid4().hex[:12]}.jpg"
            with open(os.path.join(photos_dir, fname), "wb") as f:
                f.write(base64.b64decode(b64_part))
            photo_ref = fname
        did = deps["add_personal_entry_by_hash"](owner_hash, title, condition, buy_price, photo_ref)
        return web.json_response({"id": did})

    async def handle_sell_direct(request: web.Request) -> web.Response:
        owner_hash = deps["get_owner_hash"]()
        if not owner_hash:
            return web.json_response({"error": "Owner not set"}, status=400)
        did = int(request.match_info["id"])
        body = await request.json()
        sell_price = deps["parse_amount"](str(body.get("sell_price", "0")))
        deps["mark_personal_sold_by_hash"](did, owner_hash, sell_price)
        return web.json_response({"ok": True})

    async def handle_delete_direct(request: web.Request) -> web.Response:
        owner_hash = deps["get_owner_hash"]()
        if not owner_hash:
            return web.json_response({"error": "Owner not set"}, status=400)
        did = int(request.match_info["id"])
        deps["delete_personal_entry_by_hash"](did, owner_hash)
        return web.json_response({"ok": True})

    async def handle_get_photo(request: web.Request) -> web.Response:
        fname = os.path.basename(request.match_info["name"])
        data_dir = os.getenv("DATA_DIR", "/app/data")
        photos_dir = os.path.join(data_dir, "photos") if os.path.isdir(data_dir) else "photos"
        fpath = os.path.join(photos_dir, fname)
        if not os.path.isfile(fpath):
            return web.Response(status=404)
        return web.FileResponse(fpath)

    app.router.add_get("/health", handle_health)
    app.router.add_get("/login", handle_login_get)
    app.router.add_post("/login", handle_login_post)
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/dashboard", handle_dashboard)
    app.router.add_post("/api/shipments", handle_create_shipment)
    app.router.add_post("/api/shipments/{id}/arrive", handle_arrive_shipment)
    app.router.add_post("/api/shipments/{id}/items", handle_add_item)
    app.router.add_put("/api/items/{id}", handle_update_item)
    app.router.add_delete("/api/items/{id}", handle_delete_item)
    app.router.add_post("/api/items/{id}/sell", handle_sell_item)
    app.router.add_post("/api/posts/{id}/sold", handle_mark_post_sold)
    app.router.add_post("/api/direct", handle_create_direct)
    app.router.add_post("/api/direct/{id}/sell", handle_sell_direct)
    app.router.add_delete("/api/direct/{id}", handle_delete_direct)
    app.router.add_get("/api/photo/{name}", handle_get_photo)
    return app


async def start_web_server(bot: Bot, deps: dict[str, Callable[..., Any]]) -> web.AppRunner:
    app = create_web_app(bot, deps)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEB_PORT)
    await site.start()
    logger.info(f"QCRM Web Interface started on port {WEB_PORT}")
    return runner