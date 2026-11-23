import asyncio
import base64
import hashlib
import logging
import os
import uuid
from typing import Any, Callable, Optional

from aiohttp import web
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
)

logger = logging.getLogger(__name__)

WEB_PORT = int(os.getenv("PORT", os.getenv("WEB_PORT", "8080")))
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "").strip()
COOKIE_NAME = "qcrm_auth"

# Встроенный сжатый логотип QHEAD Mobile
_DEFAULT_LOGO_B64 = "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"


def _get_logo_html() -> str:
    for fname in ("logo.png", "logo.jpg", "logo.jpeg"):
        if os.path.isfile(fname):
            ext = fname.rsplit(".", 1)[-1]
            with open(fname, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            return f'<img src="data:image/{ext};base64,{b64}" alt="QCRM" class="logo-img">'
    if _DEFAULT_LOGO_B64:
        return f'<img src="data:image/jpeg;base64,{_DEFAULT_LOGO_B64}" alt="QCRM" class="logo-img">'
    return '<div class="logo-fallback">Q</div>'


def _owner_token() -> str:
    return "owner:" + hashlib.sha256(f"qcrm_owner:{WEB_PASSWORD}".encode("utf-8")).hexdigest()


def _admin_token(user_hash: str) -> str:
    sig = hashlib.sha256(f"qcrm_adm:{user_hash}:{WEB_PASSWORD}".encode("utf-8")).hexdigest()[:24]
    return f"adm:{user_hash}:{sig}"


def _get_session(request: web.Request) -> Optional[dict]:
    cookie = request.cookies.get(COOKIE_NAME, "")
    if cookie == _owner_token():
        return {"role": "owner"}
    if cookie.startswith("adm:"):
        parts = cookie.split(":", 2)
        if len(parts) == 3 and cookie == _admin_token(parts[1]):
            return {"role": "admin_personal", "user_hash": parts[1]}
    if not WEB_PASSWORD and not cookie:
        return {"role": "owner"}
    return None


FALLING_BG_JS = """
(function(){
  const icons = [
    '<svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="#ff5a1f" stroke-width="1.5"><rect x="6" y="2" width="12" height="20" rx="2.5"/><line x1="10" y1="18" x2="14" y2="18"/></svg>',
    '<svg width="38" height="38" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="1.4"><rect x="2" y="6" width="20" height="12" rx="4"/><path d="M6 12h4M8 10v4"/><circle cx="15.5" cy="10.5" r="1"/><circle cx="17.5" cy="12.5" r="1"/></svg>',
    '<svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="#ff5a1f" stroke-width="1.4"><rect x="3" y="4" width="18" height="16" rx="2"/><line x1="3" y1="12" x2="21" y2="12"/><rect x="7" y="6" width="10" height="4" rx="1"/><rect x="7" y="14" width="10" height="4" rx="1"/></svg>',
    '<svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="1.4"><path d="M21 8l-9-5-9 5v8l9 5 9-5V8z"/><path d="M3.3 7L12 12l8.7-5M12 22V12"/></svg>'
  ];
  const wrap = document.getElementById('bgFall');
  if (!wrap) return;
  for (let i = 0; i < 16; i++) {
    const d = document.createElement('div');
    d.className = 'fall-item';
    d.innerHTML = icons[i % icons.length];
    d.style.left = ((i * 6.5 + (i % 3) * 2) % 94) + '%';
    d.style.animationDuration = (12 + (i % 5) * 2.5) + 's';
    d.style.animationDelay = (-1 * (i * 1.7)) + 's';
    wrap.appendChild(d);
  }
})();
"""


LOGIN_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>QCRM — Вход</title>
<style>
  :root { --bg:#070709; --border:rgba(255,255,255,.12); --orange:#ff5a1f; --text:#f4f4f5; --muted:#a1a1aa; }
  * { box-sizing:border-box; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; }
  body {
    margin:0; background:radial-gradient(circle at 20% 20%, rgba(255,90,31,.18), transparent 45%),
    radial-gradient(circle at 80% 80%, rgba(255,90,31,.12), transparent 50%), var(--bg);
    color:var(--text); min-height:100vh; display:flex; align-items:center; justify-content:center;
    padding:20px; overflow:hidden; position:relative;
  }
  .bg-fall { position:fixed; inset:0; pointer-events:none; overflow:hidden; z-index:0; }
  .fall-item {
    position:absolute; top:-80px; opacity:0; will-change:transform,opacity;
    animation:fallDown linear infinite;
  }
  @keyframes fallDown {
    0% { transform:translate3d(0,-80px,0) rotate(0deg); opacity:0; }
    12% { opacity:.15; }
    85% { opacity:.12; }
    100% { transform:translate3d(24px,112vh,0) rotate(320deg); opacity:0; }
  }
  @keyframes cardIn {
    from { opacity:0; transform:translateY(18px) scale(.97); }
    to { opacity:1; transform:translateY(0) scale(1); }
  }
  .box {
    position:relative; z-index:2; width:100%; max-width:360px;
    background:rgba(20,20,24,.64);
    backdrop-filter:blur(22px); -webkit-backdrop-filter:blur(22px);
    border:1px solid var(--border);
    box-shadow:0 24px 60px rgba(0,0,0,.65), inset 0 1px 0 rgba(255,255,255,.08);
    border-radius:24px; padding:30px 26px; text-align:center;
    animation:cardIn .45s cubic-bezier(.16,1,.3,1);
  }
  .logo-img {
    width:66px; height:66px; border-radius:16px; object-fit:cover; margin-bottom:12px;
    border:1px solid rgba(255,90,31,.45); box-shadow:0 8px 24px rgba(255,90,31,.25);
  }
  h1 { margin:0 0 6px; font-size:26px; letter-spacing:.6px; font-weight:800; }
  h1 span { color:var(--orange); }
  p { margin:0 0 22px; color:var(--muted); font-size:13px; }
  input {
    width:100%; padding:13px 15px; border-radius:13px; border:1px solid rgba(255,255,255,.12);
    background:rgba(10,10,13,.65); color:#fff; font-size:16px; outline:none; margin-bottom:14px;
  }
  input:focus { border-color:var(--orange); box-shadow:0 0 0 3px rgba(255,90,31,.2); }
  button {
    width:100%; padding:13px; border:none; border-radius:13px;
    background:linear-gradient(135deg,#ff6b35,#ff4d0d); color:#fff;
    font-weight:700; font-size:15px; cursor:pointer;
    box-shadow:0 10px 25px rgba(255,90,31,.3);
  }
  .err { color:#fca5a5; background:rgba(239,68,68,.15); border:1px solid rgba(239,68,68,.3); padding:8px; border-radius:10px; font-size:13px; margin-bottom:12px; }
</style>
</head>
<body>
  <div class="bg-fall" id="bgFall"></div>
  <canvas id="boomCanvas" style="position:fixed;inset:0;pointer-events:none;z-index:99;display:none"></canvas>
  <form class="box" method="post" action="/login">
    __LOGO__
    <h1>Q<span>CRM</span></h1>
    <p>QHEAD Mobile • Панель управления</p>
    __ERR__
    <input type="password" name="password" placeholder="Пароль доступа" required autofocus>
    <button type="submit">Войти</button>
    <div style="margin-top:16px;padding:10px 12px;border-radius:12px;background:rgba(255,90,31,.1);border:1px solid rgba(255,90,31,.28);font-size:12px;color:#e4e4e7;line-height:1.4;text-align:left">
      💡 <b>Нет пароля?</b><br>
      Откройте бота в Telegram → перейдите во вкладку <b>«💼 Мой учёт»</b> на главном экране → нажмите <b>«🔑 Указать пароль для Веб-CRM»</b>.
    </div>
  </form>
<script>__FALL_JS__</script>
</body>
</html>"""


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, viewport-fit=cover">
<title>QCRM — QHEAD Mobile</title>
<style>
  :root {
    --bg: #070709;
    --surface: rgba(20, 20, 26, .74);
    --card: rgba(24, 24, 32, .68);
    --card-2: rgba(34, 34, 44, .64);
    --border: rgba(255, 255, 255, .09);
    --orange: #ff5a1f;
    --orange-soft: rgba(255,90,31,.16);
    --green: #22c55e;
    --green-soft: rgba(34,197,94,.16);
    --red: #ef4444;
    --red-soft: rgba(239,68,68,.16);
    --text: #f4f4f5;
    --muted: #a1a1aa;
  }
  * { box-sizing:border-box; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; -webkit-tap-highlight-color:transparent; }
  body {
    margin:0; color:var(--text); padding-bottom:92px; min-height:100vh; position:relative;
    background:
      radial-gradient(circle at 15% 10%, rgba(255,90,31,.16), transparent 40%),
      radial-gradient(circle at 85% 40%, rgba(255,90,31,.1), transparent 45%),
      var(--bg);
  }
  .bg-fall { position:fixed; inset:0; pointer-events:none; overflow:hidden; z-index:0; transition:opacity .35s; }
  .fall-item { position:absolute; top:-80px; opacity:0; will-change:transform,opacity; animation:fallDown linear infinite; }
  @keyframes fallDown {
    0% { transform:translate3d(0,-80px,0) rotate(0deg); opacity:0; }
    12% { opacity:.11; }
    85% { opacity:.09; }
    100% { transform:translate3d(24px,112vh,0) rotate(320deg); opacity:0; }
  }
  @keyframes fadeUp {
    from { opacity:0; transform:translateY(12px); }
    to { opacity:1; transform:translateY(0); }
  }
  @keyframes modalIn {
    from { opacity:0; transform:scale(.95) translateY(8px); }
    to { opacity:1; transform:scale(1) translateY(0); }
  }
  @keyframes pulseDot {
    0%, 100% { opacity:1; transform:scale(1); }
    50% { opacity:.45; transform:scale(1.3); }
  }
  .topbar {
    position:sticky; top:0; z-index:30;
    background:rgba(12,12,16,.78);
    backdrop-filter:blur(18px); -webkit-backdrop-filter:blur(18px);
    border-bottom:1px solid var(--border);
    padding:10px 16px; display:flex; align-items:center; justify-content:space-between; gap:10px;
  }
  .brand { display:flex; align-items:center; gap:10px; }
  .logo-img {
    width:38px; height:38px; border-radius:10px; object-fit:cover; flex-shrink:0;
    border:1px solid rgba(255,90,31,.45); box-shadow:0 4px 16px rgba(255,90,31,.25);
  }
  .brand-title { font-weight:800; font-size:19px; letter-spacing:.5px; line-height:1.1; display:flex; align-items:center; gap:7px; }
  .brand-title span { color:var(--orange); }
  .live-dot { width:7px; height:7px; border-radius:50%; background:var(--green); display:inline-block; animation:pulseDot 2s infinite; }
  .brand-sub { font-size:10px; color:var(--muted); letter-spacing:.8px; text-transform:uppercase; }
  .wrap { position:relative; z-index:2; max-width:1120px; margin:0 auto; padding:14px 14px; }

  .hero-card {
    background:linear-gradient(135deg, rgba(255,90,31,.22), rgba(24,24,32,.78));
    backdrop-filter:blur(18px); -webkit-backdrop-filter:blur(18px);
    border:1px solid rgba(255,90,31,.38); border-radius:22px; padding:18px;
    box-shadow:0 16px 40px rgba(0,0,0,.45), inset 0 1px 0 rgba(255,255,255,.1);
    margin-bottom:14px; animation:fadeUp .35s ease;
  }
  .hero-row { display:flex; justify-content:space-between; align-items:flex-end; gap:12px; flex-wrap:wrap; }
  .hero-lbl { font-size:12px; color:#fed7aa; margin-bottom:4px; }
  .hero-val { font-size:28px; font-weight:800; letter-spacing:-.5px; }
  .hero-sub { font-size:13px; color:var(--green); font-weight:700; }

  .kpi-grid { display:grid; grid-template-columns:repeat(2,1fr); gap:10px; margin-bottom:16px; }
  .kpi {
    background:var(--card); border:1px solid var(--border); border-radius:16px; padding:14px;
    backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
    box-shadow:inset 0 1px 0 rgba(255,255,255,.06);
    animation:fadeUp .35s ease backwards;
  }
  .kpi-label { font-size:11px; color:var(--muted); margin-bottom:6px; }
  .kpi-val { font-size:18px; font-weight:800; }
  .kpi-val.orange { color:var(--orange); }
  .kpi-val.green { color:var(--green); }

  .quick-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:8px; margin-bottom:18px; }
  .quick-btn {
    background:var(--card-2); border:1px solid var(--border); border-radius:16px;
    padding:12px 8px; text-align:center; color:#fff; font-weight:700; font-size:12px;
    cursor:pointer; backdrop-filter:blur(12px); -webkit-backdrop-filter:blur(12px);
    transition:transform .12s, border-color .2s;
  }
  .quick-btn:active { transform:scale(.95); }
  .quick-btn .ico { font-size:20px; display:block; margin-bottom:5px; }

  .sec-title { font-size:14px; font-weight:700; color:var(--muted); margin:6px 0 10px; text-transform:uppercase; letter-spacing:.6px; }

  .toolbar { display:flex; flex-wrap:wrap; gap:10px; align-items:center; justify-content:space-between; margin-bottom:14px; }
  .search {
    flex:1; min-width:180px; background:var(--surface); border:1px solid var(--border);
    backdrop-filter:blur(12px); -webkit-backdrop-filter:blur(12px);
    color:#fff; padding:10px 14px; border-radius:12px; font-size:14px; outline:none;
  }
  .search:focus { border-color:var(--orange); }
  .btn {
    border:none; border-radius:11px; padding:9px 14px; font-weight:600; font-size:13px;
    cursor:pointer; display:inline-flex; align-items:center; gap:6px; transition:transform .12s;
  }
  .btn:active { transform:scale(.95); }
  .btn-orange { background:linear-gradient(135deg,#ff6b35,#ff4d0d); color:#fff; box-shadow:0 4px 16px rgba(255,90,31,.28); }
  .btn-dark { background:var(--card-2); color:var(--text); border:1px solid var(--border); }
  .btn-green { background:var(--green-soft); color:var(--green); border:1px solid rgba(34,197,94,.3); }
  .btn-red { background:var(--red-soft); color:var(--red); border:1px solid rgba(239,68,68,.3); }
  .btn-sm { padding:6px 10px; font-size:12px; border-radius:9px; }

  .ship-list { display:flex; flex-direction:column; gap:12px; }
  .ship-card {
    background:var(--card); border:1px solid var(--border); border-radius:18px; overflow:hidden;
    backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
    animation:fadeUp .35s ease backwards;
  }
  .ship-head {
    padding:13px 15px; background:rgba(16,16,22,.62); border-bottom:1px solid var(--border);
    display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:10px;
  }
  .ship-title { font-size:16px; font-weight:700; display:flex; align-items:center; gap:8px; }
  .badge { font-size:11px; font-weight:700; padding:3px 9px; border-radius:999px; }
  .badge-active { background:var(--orange-soft); color:var(--orange); }
  .badge-arch { background:rgba(255,255,255,.08); color:var(--muted); }
  .badge-sold { background:var(--green-soft); color:var(--green); }
  .ship-actions { display:flex; flex-wrap:wrap; gap:6px; }
  .items-grid { padding:12px; display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:10px; }
  .micro-card {
    background:var(--card-2); border:1px solid var(--border); border-radius:14px;
    padding:12px; display:flex; flex-direction:column; justify-content:space-between; gap:8px;
  }
  .micro-name { font-weight:700; font-size:14px; }
  .track-pill {
    display:inline-block; background:rgba(9,9,11,.78); border:1px solid var(--border);
    padding:4px 8px; border-radius:8px; font-family:ui-monospace,SFMono-Regular,monospace;
    font-size:12px; color:#e4e4e7; cursor:pointer; user-select:all;
  }
  .meta-row { font-size:12px; color:var(--muted); display:flex; flex-wrap:wrap; gap:6px; align-items:center; }
  .owner-my { color:var(--orange); font-weight:600; }
  .owner-client { color:#60a5fa; font-weight:600; }
  .micro-actions { display:flex; flex-wrap:wrap; gap:6px; }

  /* Красивые карточки постов в наличии */
  .posts-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(290px,1fr)); gap:12px; }
  .post-card {
    background:var(--card); border:1px solid var(--border); border-radius:18px; padding:15px;
    backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
    display:flex; flex-direction:column; justify-content:space-between; gap:12px;
    animation:fadeUp .35s ease backwards;
  }
  .post-head { display:flex; justify-content:space-between; align-items:flex-start; gap:10px; }
  .post-title { font-size:16px; font-weight:800; line-height:1.25; }
  .post-price {
    background:var(--orange-soft); color:var(--orange); border:1px solid rgba(255,90,31,.35);
    padding:4px 10px; border-radius:10px; font-weight:800; font-size:13px; white-space:nowrap;
  }
  .post-body { font-size:13px; line-height:1.45; color:#d4d4d8; white-space:pre-wrap; }
  .post-foot { display:flex; align-items:center; justify-content:space-between; gap:8px; border-top:1px solid var(--border); padding-top:10px; }
  .status-live { font-size:11px; font-weight:700; color:var(--green); display:flex; align-items:center; gap:5px; }

  /* Нижний стеклянный Dock (Tab Bar) */
  .dock {
    position:fixed; bottom:0; left:0; right:0; z-index:40;
    background:rgba(14,14,18,.84);
    backdrop-filter:blur(22px); -webkit-backdrop-filter:blur(22px);
    border-top:1px solid var(--border);
    display:flex; justify-content:space-around; align-items:center;
    padding:8px 6px calc(8px + env(safe-area-inset-bottom));
  }
  .dock-btn {
    background:none; border:none; color:var(--muted); font-size:11px; font-weight:600;
    display:flex; flex-direction:column; align-items:center; gap:3px; cursor:pointer;
    padding:4px 8px; border-radius:12px; transition:color .15s, transform .12s;
  }
  .dock-btn .d-ico { font-size:19px; line-height:1; }
  .dock-btn.active { color:var(--orange); }
  .dock-btn:active { transform:scale(.92); }

  .modal-bg {
    position:fixed; inset:0; background:rgba(0,0,0,.72);
    backdrop-filter:blur(6px); -webkit-backdrop-filter:blur(6px);
    z-index:50; display:none; align-items:center; justify-content:center; padding:16px;
  }
  .modal-bg.open { display:flex; }
  .modal {
    width:100%; max-width:430px; max-height:90vh; overflow-y:auto;
    background:rgba(24,24,30,.92);
    backdrop-filter:blur(22px); -webkit-backdrop-filter:blur(22px);
    border:1px solid rgba(255,255,255,.12); border-radius:20px; padding:20px;
    box-shadow:0 24px 60px rgba(0,0,0,.65);
    animation:modalIn .22s cubic-bezier(.16,1,.3,1);
  }
  .modal h3 { margin:0 0 12px; font-size:17px; }
  .field { margin-bottom:10px; }
  .field label { display:block; font-size:12px; color:var(--muted); margin-bottom:4px; }
  .field input, .field select, .field textarea {
    width:100%; padding:10px 12px; border-radius:10px; border:1px solid var(--border);
    background:rgba(10,10,14,.72); color:#fff; font-size:14px; outline:none;
  }
  .field textarea { min-height:110px; resize:vertical; }
  .field input:focus, .field textarea:focus { border-color:var(--orange); }
  .modal-btns { display:flex; justify-content:flex-end; gap:8px; margin-top:14px; }
  .toast {
    position:fixed; bottom:84px; left:50%; transform:translateX(-50%);
    background:rgba(24,24,30,.95); backdrop-filter:blur(12px);
    color:#fff; padding:10px 16px; border-radius:12px;
    border:1px solid var(--orange); font-size:13px; z-index:60; display:none;
  }
  .empty { text-align:center; padding:32px 16px; color:var(--muted); background:var(--card); border:1px dashed var(--border); border-radius:16px; }
</style>
</head>
<body>
  <div class="bg-fall" id="bgFall"></div>

  <header class="topbar">
    <div class="brand">
      __LOGO__
      <div>
        <div class="brand-title">Q<span>CRM</span><span class="live-dot" title="Online"></span></div>
        <div class="brand-sub">QHEAD Mobile</div>
      </div>
    </div>
    <div style="display:flex;gap:8px">
      <button class="btn btn-dark btn-sm" onclick="loadAll()">🔄</button>
      <a href="/logout" class="btn btn-dark btn-sm" style="text-decoration:none">🚪 Выйти</a>
    </div>
  </header>

  <main class="wrap">
    <!-- ГЛАВНАЯ -->
    <section id="view-home"></section>

    <!-- CRM ОТПРАВКИ -->
    <section id="view-crm" style="display:none">
      <div class="toolbar">
        <input class="search" id="crmSearch" placeholder="Поиск по отправке, товару, треку..." oninput="render()">
        <button class="btn btn-orange" onclick="openNewShipment()">+ Отправка</button>
      </div>
      <div id="crmList" class="ship-list"></div>
    </section>

    <!-- СВОИ ТОВАРЫ -->
    <section id="view-direct" style="display:none">
      <div class="toolbar">
        <input class="search" id="directSearch" placeholder="Поиск по названию или состоянию..." oninput="render()">
        <button class="btn btn-orange" onclick="openNewDirect()">+ Свой товар</button>
      </div>
      <div id="directList" class="posts-grid"></div>
    </section>

    <!-- ПОСТЫ В НАЛИЧИИ -->
    <section id="view-posts" style="display:none">
      <div class="toolbar">
        <input class="search" id="postsSearch" placeholder="Поиск по объявлениям в наличии..." oninput="render()">
        <button class="btn btn-orange" onclick="openNewPost()">+ Создать пост</button>
      </div>
      <div id="postsList" class="posts-grid"></div>
    </section>

    <!-- АРХИВ -->
    <section id="view-archive" style="display:none">
      <div class="toolbar">
        <input class="search" id="archSearch" placeholder="Поиск в архиве..." oninput="render()">
      </div>
      <div id="archList" class="ship-list"></div>
    </section>
  </main>

  <nav class="dock" id="dockBar">
    <button class="dock-btn active" data-tab="home" onclick="switchTab('home')"><span class="d-ico">🏠</span>Главная</button>
    <button class="dock-btn" data-tab="crm" onclick="switchTab('crm')"><span class="d-ico">📦</span>CRM</button>
    <button class="dock-btn" data-tab="direct" onclick="switchTab('direct')"><span class="d-ico">🏷</span>Свои</button>
    <button class="dock-btn" data-tab="posts" onclick="switchTab('posts')"><span class="d-ico">📝</span>Посты</button>
    <button class="dock-btn" data-tab="archive" onclick="switchTab('archive')"><span class="d-ico">🗄</span>Архив</button>
  </nav>

  <div id="modalBg" class="modal-bg" onclick="if(event.target===this) closeModal()">
    <div class="modal" id="modalBody"></div>
  </div>
  <div id="toast" class="toast"></div>

<script>
__FALL_JS__

let STATE = { role: 'owner', finances: {}, shipments: [], posts: [], direct_items: [], unsold_my: [] };
let CURRENT_TAB = 'home';

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
  setTimeout(() => t.style.display = 'none', 2500);
}
function copyText(txt) {
  if (!txt) return;
  navigator.clipboard?.writeText(txt);
  toast('Скопировано: ' + txt);
}
function switchTab(tab) {
  CURRENT_TAB = tab;
  document.querySelectorAll('.dock-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  ['home','crm','direct','posts','archive'].forEach(t => {
    document.getElementById('view-' + t).style.display = (t === tab) ? '' : 'none';
  });
  document.getElementById('bgFall').style.opacity = (tab === 'home') ? '1' : '0.25';
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
  const isOwner = STATE.role === 'owner';
  const perms = STATE.perms || { can_crm: isOwner, can_post: isOwner, can_personal_ledger: isOwner };
  document.querySelectorAll('.dock-btn').forEach(b => {
    const t = b.dataset.tab;
    let show = true;
    if (t === 'crm' || t === 'archive') show = !!perms.can_crm;
    if (t === 'posts') show = !!perms.can_post;
    if (t === 'direct') show = !!perms.can_personal_ledger;
    b.style.display = show ? '' : 'none';
  });

  if (CURRENT_TAB === 'home') renderHome(!isOwner, perms);
  if (CURRENT_TAB === 'crm') renderShipments('active', 'crmList', 'crmSearch');
  if (CURRENT_TAB === 'archive') renderShipments('archived', 'archList', 'archSearch');
  if (CURRENT_TAB === 'direct') renderDirect();
  if (CURRENT_TAB === 'posts') renderPosts();
}

function renderHome(isAdmin, perms) {
  const f = STATE.finances || {};
  const el = document.getElementById('view-home');

  if (isAdmin) {
    const crmCard = perms.can_crm ? `<button class="quick-btn" onclick="switchTab('crm')"><span class="ico">📦</span>CRM Треки</button>` : '';
    const postCard = perms.can_post ? `<button class="quick-btn" onclick="switchTab('posts')"><span class="ico">📝</span>Посты</button>` : '';
    const dirCard = perms.can_personal_ledger ? `<button class="quick-btn" onclick="openNewDirect()"><span class="ico">➕</span>Свой товар</button>` : '';
    const finHero = perms.can_personal_ledger ? `
      <div class="hero-card">
        <div class="hero-row">
          <div>
            <div class="hero-lbl">💼 Ваш личный бюджет (вложено)</div>
            <div class="hero-val">${fmtMoney(f.invested)}</div>
          </div>
          <div style="text-align:right">
            <div class="hero-lbl">📈 Прибыль</div>
            <div class="hero-sub">${fmtMoney(f.profit)}</div>
          </div>
        </div>
      </div>
      <div class="kpi-grid">
        <div class="kpi"><div class="kpi-label">📦 В наличии</div><div class="kpi-val">${f.in_stock_count || 0} шт.</div></div>
        <div class="kpi"><div class="kpi-label">💵 Выручка</div><div class="kpi-val green">${fmtMoney(f.revenue)}</div></div>
      </div>` : '';
    el.innerHTML = `
      ${finHero}
      <div class="sec-title">Доступные разделы</div>
      <div class="quick-grid">
        ${crmCard}
        ${postCard}
        ${dirCard}
      </div>
    `;
    return;
  }

  if (false) {
    el.innerHTML = `
      <div class="hero-card">
        <div class="hero-row">
          <div>
            <div class="hero-lbl">💼 Ваш личный бюджет (вложено)</div>
            <div class="hero-val">${fmtMoney(f.invested)}</div>
          </div>
          <div style="text-align:right">
            <div class="hero-lbl">📈 Прибыль</div>
            <div class="hero-sub">${fmtMoney(f.profit)}</div>
          </div>
        </div>
      </div>
      <div class="kpi-grid">
        <div class="kpi"><div class="kpi-label">📦 В наличии</div><div class="kpi-val">${f.in_stock_count || 0} шт.</div></div>
        <div class="kpi"><div class="kpi-label">💵 Выручка</div><div class="kpi-val green">${fmtMoney(f.revenue)}</div></div>
      </div>
      <div class="sec-title">Быстрые действия</div>
      <div class="quick-grid">
        <button class="quick-btn" onclick="openNewDirect()"><span class="ico">➕</span>Добавить товар</button>
        <button class="quick-btn" onclick="switchTab('direct')"><span class="ico">🏷</span>Мои товары</button>
        <button class="quick-btn" onclick="loadAll()"><span class="ico">🔄</span>Обновить</button>
      </div>
    `;
    return;
  }

  const activeShips = STATE.shipments.filter(s => s.status === 'active').length;
  const activePosts = STATE.posts.filter(p => p.status === 'active').length;
  const directInStock = (STATE.direct_items || []).filter(d => d.status !== 'sold').length;

  el.innerHTML = `
    <div class="hero-card">
      <div class="hero-row">
        <div>
          <div class="hero-lbl">💳 Всего вложено (товары + доставка)</div>
          <div class="hero-val">${fmtMoney(f.total_spent)}</div>
        </div>
        <div style="text-align:right">
          <div class="hero-lbl">📈 Прибыль по проданным</div>
          <div class="hero-sub">${fmtMoney(f.sold_profit_items)}</div>
        </div>
      </div>
    </div>

    <div class="kpi-grid">
      <div class="kpi"><div class="kpi-label">📦 В пути (мои товары)</div><div class="kpi-val orange">${fmtMoney(f.invested_active)}</div></div>
      <div class="kpi"><div class="kpi-label">🚚 Доставка (архив)</div><div class="kpi-val">${fmtMoney(f.delivery_total)}</div></div>
      <div class="kpi"><div class="kpi-label">💵 Выручка с продаж</div><div class="kpi-val green">${fmtMoney(f.revenue_total)}</div></div>
      <div class="kpi"><div class="kpi-label">🏦 Общий баланс</div><div class="kpi-val ${Number(f.net_overall||0)>=0?'green':'orange'}">${fmtMoney(f.net_overall)}</div></div>
    </div>

    <div class="sec-title">Быстрые действия</div>
    <div class="quick-grid">
      <button class="quick-btn" onclick="openNewShipment()"><span class="ico">📦</span>+ Отправка</button>
      <button class="quick-btn" onclick="openNewDirect()"><span class="ico">🏷</span>+ Свой товар</button>
      <button class="quick-btn" onclick="openNewPost()"><span class="ico">📝</span>+ Пост в канал</button>
    </div>

    <div class="sec-title">Сводка сейчас</div>
    <div class="kpi-grid">
      <div class="kpi" onclick="switchTab('crm')" style="cursor:pointer"><div class="kpi-label">Активных отправок</div><div class="kpi-val orange">${activeShips} шт.</div></div>
      <div class="kpi" onclick="switchTab('posts')" style="cursor:pointer"><div class="kpi-label">Постов #НАЛИЧИЕ</div><div class="kpi-val green">${activePosts} шт.</div></div>
      <div class="kpi" onclick="switchTab('direct')" style="cursor:pointer"><div class="kpi-label">Своих в наличии</div><div class="kpi-val">${directInStock} шт.</div></div>
      <div class="kpi"><div class="kpi-label">Продано всего (моих)</div><div class="kpi-val green">${f.sold_count || 0} шт.</div></div>
    </div>
  `;
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
        ownerHtml = `<span class="owner-my">Мой${it.price ? ' • ' + esc(it.price) : ' (склад)'}</span>${soldTag}`;
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
            <div class="micro-name">${esc(it.name)}</div>
            <div style="margin:6px 0">
              <span class="track-pill" onclick="copyText('${esc(it.track_number)}')">${esc(it.track_number || 'без трека')}</span>
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
            <button class="btn btn-dark btn-sm" onclick="copyTracks(${s.id})">📋 Треки</button>
            <button class="btn btn-orange btn-sm" onclick="openAddItem(${s.id})">+ Товар</button>
            ${arriveBtn}
          </div>
        </div>
        <div class="items-grid">${itemsHtml}</div>
      </div>
    `;
  }).join('');
}

function parsePostCard(rawText) {
  const lines = String(rawText || '').split('\\n').map(l => l.trim()).filter(Boolean);
  const title = lines[0] || 'Объявление';
  let price = '';
  const bodyLines = [];
  for (let i = 1; i < lines.length; i++) {
    const l = lines[i];
    if (/^#наличие$/i.test(l)) continue;
    const pm = l.match(/цена\s*:\s*([\\d\\s.,]+)/i);
    if (pm) {
      price = pm[1].trim() + ' ₽';
    } else {
      bodyLines.push(l);
    }
  }
  return { title, price, body: bodyLines.join('\\n') };
}

function renderPosts() {
  const q = (document.getElementById('postsSearch').value || '').toLowerCase();
  const activePosts = STATE.posts.filter(p => p.status === 'active' && (!q || p.text.toLowerCase().includes(q)));
  const el = document.getElementById('postsList');
  if (!activePosts.length) {
    el.innerHTML = `<div class="empty">Активных постов в наличии пока нет.<br><br>Нажмите «+ Создать пост» сверху, чтобы выложить товар в канал!</div>`;
    return;
  }
  el.innerHTML = activePosts.map(p => {
    const parsed = parsePostCard(p.text);
    const priceHtml = parsed.price ? `<span class="post-price">${esc(parsed.price)}</span>` : '';
    const linked = p.linked_item_name ? `<span class="badge badge-active">🔗 ${esc(p.linked_item_name)}</span>` : '';
    return `
      <div class="post-card">
        <div>
          <div class="post-head">
            <div class="post-title">${esc(parsed.title)}</div>
            ${priceHtml}
          </div>
          <div class="post-body" style="margin-top:8px">${esc(parsed.body)}</div>
        </div>
        <div class="post-foot">
          <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
            <span class="status-live">● В наличии</span>
            ${linked}
          </div>
          <button class="btn btn-red btn-sm" onclick="openMarkPostSold(${p.id}, ${p.linked_item_id || 'null'})">❌ ПРОДАНО</button>
        </div>
      </div>
    `;
  }).join('');
}

function renderDirect() {
  const q = (document.getElementById('directSearch').value || '').toLowerCase();
  const items = (STATE.direct_items || []).filter(it =>
    !q || it.title.toLowerCase().includes(q) || (it.condition || '').toLowerCase().includes(q)
  );
  const el = document.getElementById('directList');
  if (!items.length) {
    el.innerHTML = `<div class="empty">Своих товаров пока нет.<br><br>Нажмите «+ Свой товар», чтобы добавить.</div>`;
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

function compressImage(file, maxSide=700, quality=0.72) {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = (e) => {
      const img = new Image();
      img.onload = () => {
        let w = img.width, h = img.height;
        if (w > maxSide || h > maxSide) {
          if (w > h) { h = Math.round(h * maxSide / w); w = maxSide; }
          else { w = Math.round(w * maxSide / h); h = maxSide; }
        }
        const c = document.createElement('canvas');
        c.width = w; c.height = h;
        c.getContext('2d').drawImage(img, 0, 0, w, h);
        resolve(c.toDataURL('image/jpeg', quality));
      };
      img.src = e.target.result;
    };
    reader.readAsDataURL(file);
  });
}

function openNewPost() {
  const opts = (STATE.unsold_my || []).map(it =>
    `<option value="${it.id}">${esc(it.name)} (${esc(it.shipment_name)})</option>`
  ).join('');
  openModal(`
    <h3>📝 Новый пост в канал</h3>
    <div class="field"><label>Фото товара (до 10 шт.)</label><input type="file" id="pPhotos" accept="image/*" multiple></div>
    <div class="field"><label>Текст поста (с #НАЛИЧИЕ и ценой)</label>
      <textarea id="pText" placeholder="🎮 Nintendo 3DS XL&#10;• Состояние: 9/10&#10;• Комплект: консоль, кабель, SD 32GB&#10;#НАЛИЧИЕ&#10;• Цена: 14.990 ₽"></textarea>
    </div>
    <div class="field"><label>Привязать к товару в CRM (для учёта прибыли)</label>
      <select id="pLink"><option value="">— Без привязки —</option>${opts}</select>
    </div>
    <div class="modal-btns">
      <button class="btn btn-dark" onclick="closeModal()">Отмена</button>
      <button class="btn btn-orange" id="pSendBtn" onclick="submitNewPost()">✅ Добавить КУПИТЬ✅ и выложить</button>
    </div>
  `);
}
async function submitNewPost() {
  const files = Array.from(document.getElementById('pPhotos').files || []).slice(0, 10);
  const text = document.getElementById('pText').value.trim();
  const linked_item_id = document.getElementById('pLink').value || null;
  if (!files.length || !text) { toast('Добавьте фото и текст поста'); return; }
  const btn = document.getElementById('pSendBtn');
  btn.disabled = true; btn.textContent = 'Публикация...';
  try {
    const photos = [];
    for (const f of files) photos.push(await compressImage(f, 1280, 0.82));
    await api('/api/posts', 'POST', { photos, text, linked_item_id });
    closeModal(); toast('Пост опубликован в канал!'); 
(function(){
  if (!new URLSearchParams(location.search).has('welcome')) return;
  history.replaceState(null, '', '/');
  const cv = document.getElementById('boomCanvas');
  if (!cv) return;
  cv.style.display = 'block';
  const ctx = cv.getContext('2d');
  const W = cv.width = window.innerWidth;
  const H = cv.height = window.innerHeight;
  const cx = W / 2, cy = H / 2;
  const colors = ['#ff5a1f', '#ff8c42', '#ffd166', '#ffffff', '#ff3b30'];
  const parts = [];
  for (let i = 0; i < 95; i++) {
    const ang = Math.random() * Math.PI * 2;
    const spd = 3 + Math.random() * 11;
    parts.push({
      x: cx, y: cy,
      vx: Math.cos(ang) * spd,
      vy: Math.sin(ang) * spd - 1.5,
      r: 2.5 + Math.random() * 4.5,
      c: colors[i % colors.length],
      life: 1,
      decay: 0.015 + Math.random() * 0.018
    });
  }
  let ringR = 10, ringA = 0.9;
  function frame() {
    ctx.clearRect(0, 0, W, H);
    if (ringA > 0.02) {
      ctx.beginPath();
      ctx.arc(cx, cy, ringR, 0, Math.PI * 2);
      ctx.strokeStyle = 'rgba(255,90,31,' + ringA + ')';
      ctx.lineWidth = 4;
      ctx.stroke();
      ringR += 16;
      ringA *= 0.9;
    }
    let alive = false;
    for (const p of parts) {
      if (p.life <= 0) continue;
      alive = true;
      p.x += p.vx;
      p.y += p.vy;
      p.vy += 0.22;
      p.vx *= 0.985;
      p.life -= p.decay;
      ctx.globalAlpha = Math.max(0, p.life);
      ctx.fillStyle = p.c;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
    if (alive || ringA > 0.02) requestAnimationFrame(frame);
    else cv.style.display = 'none';
  }
  requestAnimationFrame(frame);
})();

loadAll();
  } catch (e) {
    toast(e.message); btn.disabled = false; btn.textContent = '✅ Добавить КУПИТЬ✅ и выложить';
  }
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
  closeModal(); toast('Отправка создана'); loadAll();
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
    <div class="field"><label id="mOwnerValLbl">Цена закупки</label><input id="mItemVal" placeholder="10500 ₽"></div>
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
  if (f) photo_b64 = await compressImage(f, 700, 0.72);
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
</script>
</body>
</html>"""


def create_web_app(bot: Bot, deps: dict[str, Callable[..., Any]]) -> web.Application:
    app = web.Application(client_max_size=25 * 1024 * 1024)
    no_cache = {"Cache-Control": "no-store, no-cache, must-revalidate"}

    @web.middleware
    async def auth_mw(request: web.Request, handler):
        if request.path in ("/login", "/logout", "/health"):
            return await handler(request)
        sess = _get_session(request)
        if not sess:
            if request.path.startswith("/api/"):
                return web.json_response({"error": "Unauthorized"}, status=401)
            raise web.HTTPFound("/login")
        request["session"] = sess
        return await handler(request)

    app.middlewares.append(auth_mw)

    def _target_hash(request: web.Request) -> Optional[str]:
        sess = request["session"]
        if sess["role"] == "admin_personal":
            return sess["user_hash"]
        return deps["get_owner_hash"]()

    async def handle_health(_: web.Request) -> web.Response:
        return web.Response(text="OK")

    async def handle_logout(_: web.Request) -> web.Response:
        resp = web.HTTPFound("/login")
        resp.del_cookie(COOKIE_NAME)
        raise resp

    async def handle_login_get(_: web.Request) -> web.Response:
        html = (
            LOGIN_HTML.replace("__LOGO__", _get_logo_html())
            .replace("__ERR__", "")
            .replace("__FALL_JS__", FALLING_BG_JS)
        )
        return web.Response(text=html, content_type="text/html", headers=no_cache)

    async def handle_login_post(request: web.Request) -> web.Response:
        data = await request.post()
        pwd = str(data.get("password", "")).strip()
        if not WEB_PASSWORD or pwd == WEB_PASSWORD:
            resp = web.HTTPFound("/?welcome=1")
            resp.set_cookie(COOKIE_NAME, _owner_token(), httponly=True, max_age=86400 * 30)
            raise resp
        adm = deps["find_admin_by_web_password"](pwd)
        if adm:
            resp = web.HTTPFound("/?welcome=1")
            resp.set_cookie(COOKIE_NAME, _admin_token(adm["user_hash"]), httponly=True, max_age=86400 * 30)
            raise resp
        html = (
            LOGIN_HTML.replace("__LOGO__", _get_logo_html())
            .replace("__ERR__", '<div class="err">Неверный пароль</div>')
            .replace("__FALL_JS__", FALLING_BG_JS)
        )
        return web.Response(text=html, content_type="text/html", status=401, headers=no_cache)

    async def handle_index(_: web.Request) -> web.Response:
        html = DASHBOARD_HTML.replace("__LOGO__", _get_logo_html()).replace("__FALL_JS__", FALLING_BG_JS)
        return web.Response(text=html, content_type="text/html", headers=no_cache)

    async def handle_dashboard(request: web.Request) -> web.Response:
        sess = request["session"]
        if sess["role"] == "admin_personal":
            uhash = sess["user_hash"]
            adm = deps["get_admin_by_hash"](uhash)
            perms = {
                "can_crm": bool(adm["can_crm"]) if adm else False,
                "can_post": bool(adm["can_post"]) if adm else False,
                "can_personal_ledger": bool(adm["can_personal_ledger"]) if adm else False,
            }
            finances = deps["get_personal_stats_by_hash"](uhash) if perms["can_personal_ledger"] else {}

            all_ships = []
            if perms["can_crm"]:
                for status in ("active", "archived"):
                    for s in deps["get_shipments"](status):
                        sd = dict(s)
                        sd["delivery_cost"] = 0
                        s_items = []
                        for i in deps["get_items"](s["id"]):
                            it = dict(i)
                            # Скрываем ваши деньги (цены закупки и продажи) от админов
                            if it.get("owner_type") == "my":
                                it["price"] = ""
                                it["sold_price"] = ""
                            s_items.append(it)
                        sd["items"] = s_items
                        all_ships.append(sd)

            posts = [dict(p) for p in deps["get_all_posts"]()] if perms["can_post"] else []
            direct_items = []
            if perms["can_personal_ledger"]:
                for de in deps["get_personal_entries_by_hash"](uhash):
                    d = dict(de)
                    pref = d.get("photo_ref") or ""
                    d["photo_url"] = f"/api/photo/{pref}" if pref.startswith("web_") else ""
                    direct_items.append(d)

            return web.json_response({
                "role": "admin",
                "perms": perms,
                "finances": finances,
                "shipments": all_ships,
                "posts": posts,
                "direct_items": direct_items,
                "unsold_my": [],
            })

        finances = deps["get_crm_finances"]()
        all_ships = []
        unsold_my = []
        for status in ("active", "archived"):
            for s in deps["get_shipments"](status):
                sd = dict(s)
                s_items = [dict(i) for i in deps["get_items"](s["id"])]
                sd["items"] = s_items
                all_ships.append(sd)
                for it in s_items:
                    if it.get("owner_type") == "my" and not it.get("sold_price"):
                        unsold_my.append({"id": it["id"], "name": it["name"], "shipment_name": s["name"]})

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
            "role": "owner",
            "perms": {"can_crm": True, "can_post": True, "can_personal_ledger": True},
            "finances": finances,
            "shipments": all_ships,
            "posts": posts,
            "direct_items": direct_items,
            "unsold_my": unsold_my[:25],
        })

    async def handle_create_post(request: web.Request) -> web.Response:
        if request["session"]["role"] == "admin_personal":
            adm = deps["get_admin_by_hash"](request["session"]["user_hash"])
            if not adm or not adm["can_post"]:
                return web.json_response({"error": "Forbidden"}, status=403)
        channel_id = deps["get_setting"]("channel_id")
        if not channel_id:
            return web.json_response({"error": "Сначала подключите канал в боте (⚙️ Канал для постов)"}, status=400)
        body = await request.json()
        photos_b64 = body.get("photos", [])[:10]
        post_text = str(body.get("text", "")).strip()
        linked_raw = body.get("linked_item_id")
        linked_item_id = int(linked_raw) if linked_raw else None
        if not photos_b64 or not post_text:
            return web.json_response({"error": "Нужны фото и текст"}, status=400)

        files = []
        for idx, b64 in enumerate(photos_b64):
            raw_bytes = base64.b64decode(b64.split(",", 1)[1])
            files.append(BufferedInputFile(raw_bytes, filename=f"photo_{idx}.jpg"))

        buy_kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="КУПИТЬ✅", url="https://t.me/QQHead")]]
        )
        try:
            if len(files) == 1:
                await bot.send_photo(chat_id=channel_id, photo=files[0])
            else:
                await bot.send_media_group(
                    chat_id=channel_id,
                    media=[InputMediaPhoto(media=f) for f in files],
                )
            sent = await bot.send_message(
                chat_id=channel_id,
                text=post_text,
                parse_mode=None,
                reply_markup=buy_kb,
                disable_web_page_preview=True,
            )
            deps["save_post"](channel_id, sent.message_id, post_text, "[]", linked_item_id=linked_item_id)
        except TelegramBadRequest as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response({"ok": True})

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
        except TelegramBadRequest:
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
        owner_hash = _target_hash(request)
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
        owner_hash = _target_hash(request)
        if not owner_hash:
            return web.json_response({"error": "Owner not set"}, status=400)
        did = int(request.match_info["id"])
        body = await request.json()
        sell_price = deps["parse_amount"](str(body.get("sell_price", "0")))
        deps["mark_personal_sold_by_hash"](did, owner_hash, sell_price)
        return web.json_response({"ok": True})

    async def handle_delete_direct(request: web.Request) -> web.Response:
        owner_hash = _target_hash(request)
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
    app.router.add_get("/logout", handle_logout)
    app.router.add_get("/login", handle_login_get)
    app.router.add_post("/login", handle_login_post)
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/dashboard", handle_dashboard)
    app.router.add_post("/api/posts", handle_create_post)
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