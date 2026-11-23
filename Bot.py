import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
from contextlib import closing
from html import escape as h
from typing import Optional

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
    MessageEntity,
    TelegramObject,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATA_DIR = os.getenv("DATA_DIR", "/app/data")
DEFAULT_DB = os.path.join(DATA_DIR, "shipments.db") if os.path.isdir(DATA_DIR) else "shipments.db"
DB_PATH = os.getenv("DB_PATH", DEFAULT_DB)
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ENV_ADMIN_ID = os.getenv("ADMIN_ID", "").strip()
ENV_CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()

BUY_URL = "https://t.me/QQHead"

# Временный буфер для сбора альбомов (media_group) при загрузке фото
_media_group_locks: dict[str, asyncio.Lock] = {}


def hash_user_id(user_id: int) -> str:
    """Односторонний SHA-256 хэш Telegram ID для анонимного хранения в БД."""
    return hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()


def utf16_len(text: str) -> int:
    """Длина строки в UTF-16 code units (формат offset/length в Telegram Bot API)."""
    return len(text.encode("utf-16-le")) // 2


def serialize_entities(entities: Optional[list[MessageEntity]]) -> str:
    if not entities:
        return "[]"
    return json.dumps([e.model_dump(exclude_none=True) for e in entities], ensure_ascii=False)


def deserialize_entities(raw: str) -> list[MessageEntity]:
    if not raw:
        return []
    return [MessageEntity(**item) for item in json.loads(raw)]


def build_sold_text_and_entities(
    text: str, entities: list[MessageEntity]
) -> tuple[str, list[MessageEntity]]:
    """
    Убирает #НАЛИЧИЕ (вместе с лишним переносом строки), пересчитывает offset всех
    MessageEntity (сохраняя custom_emoji и остальное форматирование) и добавляет
    strikethrough на весь оставшийся текст.
    """
    match = re.search(r"[ \t]*#НАЛИЧИЕ[ \t]*\n?", text, flags=re.IGNORECASE)
    if match:
        start_char, end_char = match.start(), match.end()
        # Если удаление оставило завершающий перенос строки перед концом, чистим аккуратно
        start_u16 = utf16_len(text[:start_char])
        end_u16 = utf16_len(text[:end_char])
        removed_u16 = end_u16 - start_u16
        new_text = (text[:start_char] + text[end_char:]).strip("\n")

        # Учитываем возможное обрезание начальных \n после strip("\n")
        leading_trimmed = len(text[:start_char] + text[end_char:]) - len(
            (text[:start_char] + text[end_char:]).lstrip("\n")
        )
        lead_u16 = utf16_len((text[:start_char] + text[end_char:])[:leading_trimmed])

        new_entities: list[MessageEntity] = []
        for ent in entities:
            ent_start = ent.offset
            ent_end = ent.offset + ent.length

            # Если entity целиком внутри удаляемого #НАЛИЧИЕ — пропускаем
            if ent_start >= start_u16 and ent_end <= end_u16:
                continue

            if ent_end <= start_u16:
                adj_offset = ent_start - lead_u16
                adj_length = ent.length
            elif ent_start >= end_u16:
                adj_offset = ent_start - removed_u16 - lead_u16
                adj_length = ent.length
            else:
                # Частичное пересечение (редкий случай)
                new_start = min(ent_start, start_u16)
                new_end = max(ent_end - removed_u16, start_u16)
                adj_offset = new_start - lead_u16
                adj_length = new_end - new_start

            max_len = utf16_len(new_text)
            if adj_offset < 0:
                adj_length += adj_offset
                adj_offset = 0
            if adj_offset + adj_length > max_len:
                adj_length = max_len - adj_offset
            if adj_length > 0:
                data = ent.model_dump(exclude_none=True)
                data["offset"] = adj_offset
                data["length"] = adj_length
                new_entities.append(MessageEntity(**data))
    else:
        new_text = text
        new_entities = [MessageEntity(**e.model_dump(exclude_none=True)) for e in entities]

    # Убираем старые strikethrough, чтобы не дублировать, и зачёркиваем весь текст
    new_entities = [e for e in new_entities if e.type != "strikethrough"]
    total_u16 = utf16_len(new_text)
    if total_u16 > 0:
        new_entities.append(MessageEntity(type="strikethrough", offset=0, length=total_u16))

    return new_text, new_entities


# ---------------------------------------------------------------------------
# База данных (SQLite)
# ---------------------------------------------------------------------------

def init_db() -> None:
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_hash TEXT UNIQUE NOT NULL,
                label TEXT NOT NULL DEFAULT 'Админ',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shipments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shipment_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                track_number TEXT DEFAULT '',
                owner_type TEXT DEFAULT '',
                price TEXT DEFAULT '',
                client_contact TEXT DEFAULT '',
                FOREIGN KEY (shipment_id) REFERENCES shipments(id) ON DELETE CASCADE
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                entities_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(channel_id, message_id)
            );
            """
        )
        cols = {row[1] for row in conn.execute("PRAGMA table_info(items)").fetchall()}
        if "owner_type" not in cols:
            conn.execute("ALTER TABLE items ADD COLUMN owner_type TEXT DEFAULT ''")
        if "price" not in cols:
            conn.execute("ALTER TABLE items ADD COLUMN price TEXT DEFAULT ''")
        if "client_contact" not in cols:
            conn.execute("ALTER TABLE items ADD COLUMN client_contact TEXT DEFAULT ''")

        if ENV_ADMIN_ID.isdigit():
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('owner_hash', ?)",
                (hash_user_id(int(ENV_ADMIN_ID)),),
            )
        if ENV_CHANNEL_ID:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('channel_id', ?)",
                (ENV_CHANNEL_ID,),
            )
        conn.commit()


def get_setting(key: str) -> Optional[str]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None


def set_setting(key: str, value: str) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        conn.commit()


def get_owner_hash() -> Optional[str]:
    return get_setting("owner_hash")


def set_owner_if_empty(user_id: int) -> str:
    uid_hash = hash_user_id(user_id)
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'owner_hash'").fetchone()
        if not row:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('owner_hash', ?)",
                (uid_hash,),
            )
            conn.commit()
            return uid_hash
        return row[0]


def is_owner(user_id: int) -> bool:
    owner_hash = get_owner_hash()
    return owner_hash is not None and hash_user_id(user_id) == owner_hash


def is_admin(user_id: int) -> bool:
    if is_owner(user_id):
        return True
    uid_hash = hash_user_id(user_id)
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute("SELECT 1 FROM admins WHERE user_hash = ?", (uid_hash,)).fetchone()
        return row is not None


def add_admin(target_user_id: int, label: str) -> None:
    uid_hash = hash_user_id(target_user_id)
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO admins (user_hash, label) VALUES (?, ?)",
            (uid_hash, label.strip() or "Админ"),
        )
        conn.commit()


def list_admins() -> list[sqlite3.Row]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM admins ORDER BY id ASC").fetchall()


def remove_admin(admin_row_id: int) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("DELETE FROM admins WHERE id = ?", (admin_row_id,))
        conn.commit()


# --- Работа с постами ---

def save_post(channel_id: str, message_id: int, text: str, entities_json: str) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO posts (channel_id, message_id, text, entities_json, status)
            VALUES (?, ?, ?, ?, 'active')
            """,
            (str(channel_id), message_id, text, entities_json),
        )
        conn.commit()


def get_post(channel_id: str, message_id: int) -> Optional[sqlite3.Row]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT * FROM posts WHERE channel_id = ? AND message_id = ?",
            (str(channel_id), message_id),
        ).fetchone()


def mark_post_sold(channel_id: str, message_id: int, new_text: str, new_entities_json: str) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            """
            UPDATE posts
            SET text = ?, entities_json = ?, status = 'sold'
            WHERE channel_id = ? AND message_id = ?
            """,
            (new_text, new_entities_json, str(channel_id), message_id),
        )
        conn.commit()


# --- Работа с отправками (общая база для всех админов) ---

def create_shipment(user_id: int, name: str) -> int:
    uid_hash = hash_user_id(user_id)
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute(
            "INSERT INTO shipments (user_id, name, status) VALUES (?, ?, 'active')",
            (uid_hash, name.strip()),
        )
        conn.commit()
        return cur.lastrowid


def get_shipment(shipment_id: int) -> Optional[sqlite3.Row]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM shipments WHERE id = ?", (shipment_id,)).fetchone()


def get_shipments(status: str = "active") -> list[sqlite3.Row]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            """
            SELECT s.*, COUNT(i.id) AS item_count
            FROM shipments s
            LEFT JOIN items i ON i.shipment_id = s.id
            WHERE s.status = ?
            GROUP BY s.id
            ORDER BY s.id DESC
            """,
            (status,),
        ).fetchall()


def update_shipment_name(shipment_id: int, new_name: str) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            "UPDATE shipments SET name = ? WHERE id = ?",
            (new_name.strip(), shipment_id),
        )
        conn.commit()


def archive_shipment(shipment_id: int) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            "UPDATE shipments SET status = 'archived' WHERE id = ?",
            (shipment_id,),
        )
        conn.commit()


def create_item(shipment_id: int, name: str, track_number: str = "") -> int:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute(
            "INSERT INTO items (shipment_id, name, track_number) VALUES (?, ?, ?)",
            (shipment_id, name.strip(), track_number.strip()),
        )
        conn.commit()
        return cur.lastrowid


def get_items(shipment_id: int) -> list[sqlite3.Row]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT * FROM items WHERE shipment_id = ? ORDER BY id ASC",
            (shipment_id,),
        ).fetchall()


def get_item(item_id: int) -> Optional[sqlite3.Row]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()


def update_item_name(item_id: int, new_name: str) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("UPDATE items SET name = ? WHERE id = ?", (new_name.strip(), item_id))
        conn.commit()


def update_item_track(item_id: int, track_number: str) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            "UPDATE items SET track_number = ? WHERE id = ?",
            (track_number.strip(), item_id),
        )
        conn.commit()


def update_item_my_price(item_id: int, price: str) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            "UPDATE items SET owner_type = 'my', price = ?, client_contact = '' WHERE id = ?",
            (price.strip(), item_id),
        )
        conn.commit()


def update_item_client(item_id: int, client_contact: str) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            "UPDATE items SET owner_type = 'client', client_contact = ?, price = '' WHERE id = ?",
            (client_contact.strip(), item_id),
        )
        conn.commit()


def delete_item(item_id: int) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        conn.commit()


# ---------------------------------------------------------------------------
# Middleware проверки доступа
# ---------------------------------------------------------------------------

class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = data.get("event_from_user")
        if not user:
            return
        if get_owner_hash() is None and isinstance(event, Message) and event.text and event.text.startswith("/start"):
            set_owner_if_empty(user.id)
        if not is_admin(user.id):
            return
        return await handler(event, data)


# ---------------------------------------------------------------------------
# Состояния FSM
# ---------------------------------------------------------------------------

class ShipmentFSM(StatesGroup):
    waiting_shipment_name = State()
    waiting_rename_shipment = State()
    waiting_item_name = State()
    waiting_rename_item = State()
    waiting_item_track = State()
    waiting_item_price = State()
    waiting_item_client = State()
    waiting_edit_item_name = State()
    waiting_edit_item_track = State()
    waiting_new_admin_id = State()
    waiting_new_admin_label = State()


class PostFSM(StatesGroup):
    waiting_channel = State()
    waiting_photos = State()
    waiting_description = State()
    waiting_confirm_publish = State()
    waiting_edit_post = State()


# ---------------------------------------------------------------------------
# Клавиатуры
# ---------------------------------------------------------------------------

def root_menu_kb(user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📦 CRM", callback_data="crm_menu")],
        [InlineKeyboardButton(text="📝 Создать/Редактировать пост", callback_data="posts_menu")],
    ]
    if is_owner(user_id):
        rows.append([InlineKeyboardButton(text="👥 Доступ (Админы)", callback_data="admins_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def crm_menu_kb() -> InlineKeyboardMarkup:
    active = get_shipments("active")
    archived = get_shipments("archived")

    rows = [[InlineKeyboardButton(text="📦 Создать отправку", callback_data="create_shipment")]]

    if active:
        rows.append([InlineKeyboardButton(text="✅ Завершить отправку", callback_data="finish_list")])
        rows.append([InlineKeyboardButton(text="✏️ Изменить отправку", callback_data="edit_list")])
        rows.append([InlineKeyboardButton(text="🚚 Груз приехал", callback_data="arrive_list")])

    if archived:
        rows.append([InlineKeyboardButton(text="🗄 Архив", callback_data="archive_list")])

    rows.append([InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def posts_menu_kb(user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="➕ Создать пост", callback_data="post_create")],
        [InlineKeyboardButton(text="❌ Редактировать пост (ПРОДАНО)", callback_data="post_edit_sold")],
    ]
    if is_owner(user_id):
        rows.append([InlineKeyboardButton(text="⚙️ Канал для постов", callback_data="post_set_channel")])
    rows.append([InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def buy_button_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="КУПИТЬ✅", url=BUY_URL)]]
    )


def sold_button_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="ПРОДАНО❌", url=BUY_URL)]]
    )


def admins_menu_kb() -> InlineKeyboardMarkup:
    admins = list_admins()
    rows = [[InlineKeyboardButton(text="➕ Добавить админа", callback_data="add_admin")]]
    for adm in admins:
        rows.append(
            [InlineKeyboardButton(text=f"🗑 Удалить: {adm['label']}", callback_data=f"del_admin:{adm['id']}")]
        )
    rows.append([InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def after_shipment_created_kb(shipment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить название", callback_data=f"rename_ship:{shipment_id}")],
            [InlineKeyboardButton(text="➕ Добавить товары", callback_data=f"add_item:{shipment_id}")],
            [InlineKeyboardButton(text="⬅️ В меню CRM", callback_data="crm_menu")],
        ]
    )


def after_item_name_kb(shipment_id: int, item_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить название", callback_data=f"rename_item:{shipment_id}:{item_id}")],
            [InlineKeyboardButton(text="🔢 Добавить трек-номер", callback_data=f"add_track:{shipment_id}:{item_id}")],
        ]
    )


def owner_choice_kb(shipment_id: int, item_id: int, return_to: str = "add") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👤 Мой", callback_data=f"owner_my:{shipment_id}:{item_id}:{return_to}"),
                InlineKeyboardButton(text="🧑‍💼 Клиент", callback_data=f"owner_client:{shipment_id}:{item_id}:{return_to}"),
            ]
        ]
    )


def after_item_completed_kb(shipment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить ещё товар", callback_data=f"add_item:{shipment_id}")],
            [InlineKeyboardButton(text="✏️ Изменить отправку", callback_data=f"edit_ship:{shipment_id}")],
            [InlineKeyboardButton(text="⬅️ В меню CRM", callback_data="crm_menu")],
        ]
    )


def edit_shipment_kb(shipment_id: int) -> InlineKeyboardMarkup:
    items = get_items(shipment_id)
    rows = [
        [InlineKeyboardButton(text="✏️ Изменить название отправки", callback_data=f"rename_ship_from_edit:{shipment_id}")]
    ]
    for idx, item in enumerate(items, start=1):
        track_label = item["track_number"] if item["track_number"] else "без трека"
        btn_text = f"✏️ {idx}. {item['name'][:22]} ({track_label[:14]})"
        rows.append([InlineKeyboardButton(text=btn_text, callback_data=f"edit_item_menu:{shipment_id}:{item['id']}")])

    rows.append([InlineKeyboardButton(text="➕ Добавить ещё товар", callback_data=f"add_item:{shipment_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ В меню CRM", callback_data="crm_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def edit_item_kb(shipment_id: int, item_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить название товара", callback_data=f"edit_item_name:{shipment_id}:{item_id}")],
            [InlineKeyboardButton(text="🔢 Изменить трек-номер", callback_data=f"edit_item_track:{shipment_id}:{item_id}")],
            [InlineKeyboardButton(text="👤 Изменить (Мой / Клиент)", callback_data=f"edit_item_owner:{shipment_id}:{item_id}")],
            [InlineKeyboardButton(text="🗑 Удалить товар", callback_data=f"delete_item:{shipment_id}:{item_id}")],
            [InlineKeyboardButton(text="⬅️ Назад к отправке", callback_data=f"edit_ship:{shipment_id}")],
        ]
    )


def shipments_pick_kb(shipments: list[sqlite3.Row], prefix: str) -> InlineKeyboardMarkup:
    rows = []
    for s in shipments:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"📦 {s['name']} ({s['item_count']} шт.)",
                    callback_data=f"{prefix}:{s['id']}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ В меню CRM", callback_data="crm_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# Форматирование текста CRM
# ---------------------------------------------------------------------------

def format_owner_tag(item: sqlite3.Row) -> str:
    if item["owner_type"] == "my":
        price = h(item["price"]) if item["price"] else "цена не указана"
        return f"Мой • {price}"
    if item["owner_type"] == "client":
        client = h(item["client_contact"]) if item["client_contact"] else "клиент не указан"
        return f"Клиент • {client}"
    return "владелец не указан"


def format_shipment_summary(shipment: sqlite3.Row, include_owner: bool = True) -> str:
    items = get_items(shipment["id"])
    lines = [f"📦 <b>{h(shipment['name'])}</b>", ""]
    if not items:
        lines.append("<i>Товаров пока нет.</i>")
    else:
        for idx, item in enumerate(items, start=1):
            track = f"<code>{h(item['track_number'])}</code>" if item["track_number"] else "<i>трек не указан</i>"
            if include_owner:
                owner_info = format_owner_tag(item)
                lines.append(f"{idx}. {h(item['name'])} — {track} ({owner_info})")
            else:
                lines.append(f"{idx}. {h(item['name'])} — {track}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Обработчики
# ---------------------------------------------------------------------------

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "👋 <b>Главное меню</b>\nВыберите раздел:",
        reply_markup=root_menu_kb(message.from_user.id),
    )


@router.callback_query(F.data == "main_menu")
async def cb_main_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "👋 <b>Главное меню</b>\nВыберите раздел:",
        reply_markup=root_menu_kb(callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data == "crm_menu")
async def cb_crm_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "📦 <b>CRM — Отправки и трек-номера</b>\nВыберите действие:",
        reply_markup=crm_menu_kb(),
    )
    await callback.answer()


# ===========================================================================
# РАЗДЕЛ 2: АВТОПОСТИНГ И РЕДАКТИРОВАНИЕ ПОСТОВ
# ===========================================================================

@router.callback_query(F.data == "posts_menu")
async def cb_posts_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    channel_id = get_setting("channel_id")
    ch_status = f"<code>{h(channel_id)}</code>" if channel_id else "<i>не настроен</i>"
    await callback.message.edit_text(
        f"📝 <b>Постинг в канал</b>\n"
        f"Подключённый канал: {ch_status}\n\n"
        f"Выберите действие:",
        reply_markup=posts_menu_kb(callback.from_user.id),
    )
    await callback.answer()


# --- Настройка канала для постов ---

@router.callback_query(F.data == "post_set_channel")
async def cb_post_set_channel(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("Только для главного админа", show_alert=True)
        return
    await state.set_state(PostFSM.waiting_channel)
    await callback.message.edit_text(
        "⚙️ <b>Настройка канала</b>\n\n"
        "1. Сначала добавьте этого бота в ваш канал <b>администратором</b> (с правом публикации и редактирования сообщений).\n"
        "2. Затем <b>перешлите сюда любой пост из канала</b> или отправьте его <code>@username</code> (или ID вида <code>-100...</code>).",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="posts_menu")]]
        ),
    )
    await callback.answer()


@router.message(PostFSM.waiting_channel)
async def msg_set_channel(message: Message, state: FSMContext) -> None:
    if not is_owner(message.from_user.id):
        return

    channel_ref: Optional[str] = None
    if message.forward_from_chat and message.forward_from_chat.type == "channel":
        channel_ref = str(message.forward_from_chat.id)
    elif message.text:
        txt = message.text.strip()
        if txt.startswith("@") or txt.lstrip("-").isdigit():
            channel_ref = txt

    if not channel_ref:
        await message.answer("⚠️ Перешлите пост из канала или отправьте <code>@username</code> / ID канала (например <code>-1001234567890</code>).")
        return

    set_setting("channel_id", channel_ref)
    await state.clear()
    await message.answer(
        f"✅ Канал сохранён: <code>{h(channel_ref)}</code>\nТеперь можно создавать посты!",
        reply_markup=posts_menu_kb(message.from_user.id),
    )


# --- Создание поста (Фото до 10 шт -> Описание -> Кнопка "Добавить КУПИТЬ✅" -> Публикация) ---

@router.callback_query(F.data == "post_create")
async def cb_post_create(callback: CallbackQuery, state: FSMContext) -> None:
    channel_id = get_setting("channel_id")
    if not channel_id:
        await callback.message.edit_text(
            "⚠️ Сначала главный админ должен подключить канал в разделе <b>«⚙️ Канал для постов»</b>.",
            reply_markup=posts_menu_kb(callback.from_user.id),
        )
        await callback.answer()
        return

    await state.clear()
    await state.set_state(PostFSM.waiting_photos)
    await state.update_data(photos=[])
    await callback.message.edit_text(
        "📸 <b>Шаг 1 из 3 — Фото товара</b>\n\n"
        "Отправьте до <b>10 фотографий</b> товара (можно сразу альбомом или по одной).\n"
        "Когда все фото загрузятся, нажмите кнопку <b>«➡️ Далее (к описанию)»</b>:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="➡️ Далее (к описанию)", callback_data="post_photos_done")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="posts_menu")],
            ]
        ),
    )
    await callback.answer()


@router.message(PostFSM.waiting_photos, F.photo)
async def msg_post_photo(message: Message, state: FSMContext) -> None:
    file_id = message.photo[-1].file_id
    group_id = message.media_group_id or f"single_{message.message_id}"
    lock = _media_group_locks.setdefault(group_id, asyncio.Lock())

    async with lock:
        data = await state.get_data()
        photos: list[str] = data.get("photos", [])
        if len(photos) >= 10:
            if not message.media_group_id:
                await message.answer("⚠️ Максимум 10 фото. Нажмите «➡️ Далее (к описанию)».")
            return

        photos.append(file_id)
        await state.update_data(photos=photos)

        # Если это альбом, даём другим фото из альбома прийти и отвечаем один раз после паузы
        if message.media_group_id:
            last_notified = data.get("last_group_notified")
            if last_notified != message.media_group_id:
                await state.update_data(last_group_notified=message.media_group_id)
                await asyncio.sleep(0.7)
                updated_data = await state.get_data()
                count = len(updated_data.get("photos", []))
                await message.answer(
                    f"📸 Загружено фото: <b>{count}/10</b>.\n"
                    f"Можете докинуть ещё или нажать <b>«➡️ Далее (к описанию)»</b>:",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [InlineKeyboardButton(text="➡️ Далее (к описанию)", callback_data="post_photos_done")],
                            [InlineKeyboardButton(text="❌ Отмена", callback_data="posts_menu")],
                        ]
                    ),
                )
        else:
            await message.answer(
                f"📸 Фото добавлено (<b>{len(photos)}/10</b>). Отправьте ещё или нажмите далее:",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="➡️ Далее (к описанию)", callback_data="post_photos_done")],
                        [InlineKeyboardButton(text="❌ Отмена", callback_data="posts_menu")],
                    ]
                ),
            )


@router.callback_query(PostFSM.waiting_photos, F.data == "post_photos_done")
async def cb_post_photos_done(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    photos = data.get("photos", [])
    if not photos:
        await callback.answer("Сначала отправьте хотя бы 1 фото!", show_alert=True)
        return
    await state.set_state(PostFSM.waiting_description)
    await callback.message.edit_text(
        f"✅ Фото принято: <b>{len(photos)} шт.</b>\n\n"
        f"📝 <b>Шаг 2 из 3 — Описание товара</b>\n"
        f"Отправьте текст поста (вместе с вашими премиум-стикерами, курсивом и <code>#НАЛИЧИЕ</code>):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="posts_menu")]]
        ),
    )
    await callback.answer()


@router.message(PostFSM.waiting_description, F.text)
async def msg_post_description(message: Message, state: FSMContext) -> None:
    entities_json = serialize_entities(message.entities)
    await state.update_data(post_text=message.text, post_entities_json=entities_json)
    await state.set_state(PostFSM.waiting_confirm_publish)

    await message.answer(
        "✅ Описание сохранено!\n\n"
        "<b>Шаг 3 из 3</b> — нажмите кнопку ниже, чтобы добавить <b>КУПИТЬ✅</b> и сразу опубликовать пост в канал:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Добавить КУПИТЬ✅ и выложить", callback_data="post_publish")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="posts_menu")],
            ]
        ),
    )


@router.callback_query(PostFSM.waiting_confirm_publish, F.data == "post_publish")
async def cb_post_publish(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    channel_id = get_setting("channel_id")
    if not channel_id:
        await callback.answer("Канал не настроен!", show_alert=True)
        return

    data = await state.get_data()
    photos: list[str] = data.get("photos", [])
    post_text: str = data.get("post_text", "")
    entities = deserialize_entities(data.get("post_entities_json", "[]"))

    try:
        # 1. Сначала отправляем фото (альбомом или 1 фото) — БЕЗ текста, чтобы фото были сверху
        if len(photos) == 1:
            await bot.send_photo(chat_id=channel_id, photo=photos[0])
        elif len(photos) > 1:
            media = [InputMediaPhoto(media=fid) for fid in photos[:10]]
            await bot.send_media_group(chat_id=channel_id, media=media)

        # 2. Следом отправляем текст с премиум-стикерами (entities) и кнопкой КУПИТЬ✅
        sent_msg = await bot.send_message(
            chat_id=channel_id,
            text=post_text,
            entities=entities or None,
            parse_mode=None,
            reply_markup=buy_button_kb(),
            disable_web_page_preview=True,
        )
        save_post(channel_id, sent_msg.message_id, post_text, data.get("post_entities_json", "[]"))
    except TelegramBadRequest as e:
        await callback.message.edit_text(
            f"❌ Ошибка публикации в канал:\n<code>{h(str(e))}</code>\n\n"
            f"Проверьте, что бот добавлен в канал <b>администратором</b> и указан правильный ID/username канала.",
            reply_markup=posts_menu_kb(callback.from_user.id),
        )
        await state.clear()
        await callback.answer()
        return

    await state.clear()
    await callback.message.edit_text(
        "🚀 <b>Пост успешно опубликован в канале!</b>",
        reply_markup=posts_menu_kb(callback.from_user.id),
    )
    await callback.answer("Опубликовано!")


# --- Редактирование поста (ПРОДАНО❌) ---

@router.callback_query(F.data == "post_edit_sold")
async def cb_post_edit_sold(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(PostFSM.waiting_edit_post)
    await callback.message.edit_text(
        "❌ <b>Отметить товар как ПРОДАНО</b>\n\n"
        "<b>Перешлите сюда текстовое сообщение</b> с описанием товара прямо из вашего канала "
        "(или вставьте ссылку на пост из канала).\n\n"
        "Бот уберёт <code>#НАЛИЧИЕ</code>, зачеркнёт текст (не трогая премиум-стикеры) и поменяет кнопку на <b>ПРОДАНО❌</b>.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="posts_menu")]]
        ),
    )
    await callback.answer()


@router.message(PostFSM.waiting_edit_post)
async def msg_edit_post(message: Message, state: FSMContext, bot: Bot) -> None:
    target_channel: Optional[str] = None
    target_msg_id: Optional[int] = None
    src_text: Optional[str] = None
    src_entities: list[MessageEntity] = []

    # Вариант 1: пересланное сообщение из канала (самый точный — содержит свежие entities и премиум-стикеры)
    if message.forward_from_chat and message.forward_from_message_id:
        target_channel = str(message.forward_from_chat.id)
        target_msg_id = message.forward_from_message_id
        src_text = message.text or message.caption
        src_entities = message.entities or message.caption_entities or []
    # Вариант 2: ссылка на пост вида https://t.me/channel/123 или https://t.me/c/123456789/123
    elif message.text:
        m_c = re.search(r"t\.me/c/(\d+)/(\d+)", message.text)
        m_u = re.search(r"t\.me/([A-Za-z0-9_]+)/(\d+)", message.text)
        if m_c:
            target_channel = f"-100{m_c.group(1)}"
            target_msg_id = int(m_c.group(2))
        elif m_u:
            target_channel = get_setting("channel_id") or f"@{m_u.group(1)}"
            target_msg_id = int(m_u.group(2))

        if target_channel and target_msg_id:
            saved = get_post(target_channel, target_msg_id)
            if saved:
                src_text = saved["text"]
                src_entities = deserialize_entities(saved["entities_json"])

    if not target_channel or not target_msg_id:
        await message.answer(
            "⚠️ Не удалось определить пост. Перешлите сюда именно <b>текстовый пост</b> из канала (не фото!)."
        )
        return

    # Если текст не получен (например, скинули ссылку на старый пост не из БД) — меняем хотя бы кнопку
    if not src_text:
        try:
            await bot.edit_message_reply_markup(
                chat_id=target_channel,
                message_id=target_msg_id,
                reply_markup=sold_button_kb(),
            )
            await state.clear()
            await message.answer(
                "✅ Кнопка изменена на <b>ПРОДАНО❌</b> (текст не менялся, так как пост не найден в базе — пересылайте сам пост из канала, чтобы зачёркивался и текст).",
                reply_markup=posts_menu_kb(message.from_user.id),
            )
        except TelegramBadRequest as e:
            await message.answer(f"❌ Не удалось изменить пост: <code>{h(str(e))}</code>")
        return

    new_text, new_entities = build_sold_text_and_entities(src_text, src_entities)

    try:
        await bot.edit_message_text(
            chat_id=target_channel,
            message_id=target_msg_id,
            text=new_text,
            entities=new_entities or None,
            parse_mode=None,
            reply_markup=sold_button_kb(),
            disable_web_page_preview=True,
        )
        mark_post_sold(target_channel, target_msg_id, new_text, serialize_entities(new_entities))
    except TelegramBadRequest:
        # Fallback: если Telegram отклонил редактирование текста/стикеров — хотя бы меняем кнопку
        try:
            await bot.edit_message_reply_markup(
                chat_id=target_channel,
                message_id=target_msg_id,
                reply_markup=sold_button_kb(),
            )
            await state.clear()
            await message.answer(
                "⚠️ Кнопка изменена на <b>ПРОДАНО❌</b>, но текст и #НАЛИЧИЕ поправьте, пожалуйста, вручную (Telegram не разрешил обновить разметку этого сообщения).",
                reply_markup=posts_menu_kb(message.from_user.id),
            )
            return
        except TelegramBadRequest as e2:
            await message.answer(f"❌ Ошибка редактирования поста: <code>{h(str(e2))}</code>")
            return

    await state.clear()
    await message.answer(
        "✅ Пост обновлён: <code>#НАЛИЧИЕ</code> убрано, текст зачёркнут, премиум-стикеры сохранены, кнопка изменена на <b>ПРОДАНО❌</b>!",
        reply_markup=posts_menu_kb(message.from_user.id),
    )


# ===========================================================================
# УПРАВЛЕНИЕ АДМИНАМИ (ТОЛЬКО ДЛЯ ГЛАВНОГО АДМИНА)
# ===========================================================================

@router.callback_query(F.data == "admins_menu")
async def cb_admins_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("Только для главного админа", show_alert=True)
        return
    await state.clear()
    admins = list_admins()
    text = (
        "👥 <b>Управление доступом</b>\n\n"
        f"Добавлено админов: <b>{len(admins)}</b>\n"
        "Все админы видят общие отправки, трек-номера и могут публиковать/редактировать посты."
    )
    await callback.message.edit_text(text, reply_markup=admins_menu_kb())
    await callback.answer()


@router.callback_query(F.data == "add_admin")
async def cb_add_admin(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("Только для главного админа", show_alert=True)
        return
    await state.set_state(ShipmentFSM.waiting_new_admin_id)
    await callback.message.edit_text(
        "🔢 Отправьте <b>цифровой Telegram ID</b> человека (второго аккаунта или напарника).\n"
        "<i>Узнать свой ID человек может в боте @userinfobot.</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admins_menu")]]
        ),
    )
    await callback.answer()


@router.message(ShipmentFSM.waiting_new_admin_id, F.text)
async def msg_new_admin_id(message: Message, state: FSMContext) -> None:
    if not is_owner(message.from_user.id):
        return
    raw = message.text.strip()
    if not raw.isdigit():
        await message.answer("⚠️ Нужно отправить именно цифры Telegram ID (например: <code>123456789</code>). Попробуйте ещё раз:")
        return
    await state.update_data(new_admin_id=int(raw))
    await state.set_state(ShipmentFSM.waiting_new_admin_label)
    await message.answer("📝 Напишите <b>имя или подпись</b> для этого админа (например: <i>Второй акк</i> или <i>Напарник</i>):")


@router.message(ShipmentFSM.waiting_new_admin_label, F.text)
async def msg_new_admin_label(message: Message, state: FSMContext) -> None:
    if not is_owner(message.from_user.id):
        return
    data = await state.get_data()
    add_admin(data["new_admin_id"], message.text)
    await state.clear()
    await message.answer(
        f"✅ Админ <b>{h(message.text.strip())}</b> добавлен! Теперь он может открыть этого бота и работать с общей базой и постами.",
        reply_markup=admins_menu_kb(),
    )


@router.callback_query(F.data.startswith("del_admin:"))
async def cb_del_admin(callback: CallbackQuery) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("Только для главного админа", show_alert=True)
        return
    admin_row_id = int(callback.data.split(":")[1])
    remove_admin(admin_row_id)
    await callback.message.edit_text(
        "🗑 Доступ удалён.",
        reply_markup=admins_menu_kb(),
    )
    await callback.answer("Удалено")


# ===========================================================================
# РАЗДЕЛ 1: CRM (ОТПРАВКИ, ТОВАРЫ, ТРЕК-НОМЕРА, АРХИВ)
# ===========================================================================

@router.callback_query(F.data == "create_shipment")
async def cb_create_shipment(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(ShipmentFSM.waiting_shipment_name)
    await callback.message.edit_text(
        "📝 Напишите <b>название для отправки</b> (например: <i>Октябрь №1</i> или <i>Чехлы 24.11</i>):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="crm_menu")]]
        ),
    )
    await callback.answer()


@router.message(ShipmentFSM.waiting_shipment_name, F.text)
async def msg_shipment_name(message: Message, state: FSMContext) -> None:
    shipment_id = create_shipment(message.from_user.id, message.text)
    await state.clear()
    await message.answer(
        f"✅ Отправка <b>{h(message.text.strip())}</b> создана.\nЧто делаем дальше?",
        reply_markup=after_shipment_created_kb(shipment_id),
    )


@router.callback_query(F.data.startswith("rename_ship:"))
async def cb_rename_ship(callback: CallbackQuery, state: FSMContext) -> None:
    shipment_id = int(callback.data.split(":")[1])
    await state.set_state(ShipmentFSM.waiting_rename_shipment)
    await state.update_data(shipment_id=shipment_id, return_to="created")
    await callback.message.edit_text("✏️ Введите новое <b>название отправки</b>:")
    await callback.answer()


@router.callback_query(F.data.startswith("rename_ship_from_edit:"))
async def cb_rename_ship_from_edit(callback: CallbackQuery, state: FSMContext) -> None:
    shipment_id = int(callback.data.split(":")[1])
    await state.set_state(ShipmentFSM.waiting_rename_shipment)
    await state.update_data(shipment_id=shipment_id, return_to="edit")
    await callback.message.edit_text("✏️ Введите новое <b>название отправки</b>:")
    await callback.answer()


@router.message(ShipmentFSM.waiting_rename_shipment, F.text)
async def msg_rename_shipment(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    shipment_id = data["shipment_id"]
    return_to = data.get("return_to", "created")
    update_shipment_name(shipment_id, message.text)
    await state.clear()

    if return_to == "edit":
        shipment = get_shipment(shipment_id)
        await message.answer(
            f"✅ Название изменено!\n\n{format_shipment_summary(shipment)}\n\nВыберите, что изменить:",
            reply_markup=edit_shipment_kb(shipment_id),
        )
    else:
        await message.answer(
            f"✅ Название отправки изменено на <b>{h(message.text.strip())}</b>.",
            reply_markup=after_shipment_created_kb(shipment_id),
        )


@router.callback_query(F.data.startswith("add_item:"))
async def cb_add_item(callback: CallbackQuery, state: FSMContext) -> None:
    shipment_id = int(callback.data.split(":")[1])
    await state.set_state(ShipmentFSM.waiting_item_name)
    await state.update_data(shipment_id=shipment_id)
    await callback.message.edit_text("📦 Напишите <b>название товара</b>:")
    await callback.answer()


@router.message(ShipmentFSM.waiting_item_name, F.text)
async def msg_item_name(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    shipment_id = data["shipment_id"]
    item_id = create_item(shipment_id, message.text)
    await state.clear()
    await message.answer(
        f"Товар: <b>{h(message.text.strip())}</b>\nТеперь можно изменить название или добавить трек-номер:",
        reply_markup=after_item_name_kb(shipment_id, item_id),
    )


@router.callback_query(F.data.startswith("rename_item:"))
async def cb_rename_item(callback: CallbackQuery, state: FSMContext) -> None:
    _, shipment_id, item_id = callback.data.split(":")
    await state.set_state(ShipmentFSM.waiting_rename_item)
    await state.update_data(shipment_id=int(shipment_id), item_id=int(item_id))
    await callback.message.edit_text("✏️ Введите новое <b>название товара</b>:")
    await callback.answer()


@router.message(ShipmentFSM.waiting_rename_item, F.text)
async def msg_rename_item(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    shipment_id = data["shipment_id"]
    item_id = data["item_id"]
    update_item_name(item_id, message.text)
    await state.clear()
    await message.answer(
        f"✅ Название товара изменено на <b>{h(message.text.strip())}</b>.",
        reply_markup=after_item_name_kb(shipment_id, item_id),
    )


@router.callback_query(F.data.startswith("add_track:"))
async def cb_add_track(callback: CallbackQuery, state: FSMContext) -> None:
    _, shipment_id, item_id = callback.data.split(":")
    await state.set_state(ShipmentFSM.waiting_item_track)
    await state.update_data(shipment_id=int(shipment_id), item_id=int(item_id))
    await callback.message.edit_text("🔢 Отправьте <b>трек-номер</b> для этого товара:")
    await callback.answer()


@router.message(ShipmentFSM.waiting_item_track, F.text)
async def msg_item_track(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    shipment_id = data["shipment_id"]
    item_id = data["item_id"]
    update_item_track(item_id, message.text)
    await state.clear()

    item = get_item(item_id)
    await message.answer(
        f"🔢 Трек-номер <code>{h(item['track_number'])}</code> сохранён.\n\n"
        f"<b>Чей это товар?</b>",
        reply_markup=owner_choice_kb(shipment_id, item_id, return_to="add"),
    )


@router.callback_query(F.data.startswith("owner_my:"))
async def cb_owner_my(callback: CallbackQuery, state: FSMContext) -> None:
    _, shipment_id, item_id, return_to = callback.data.split(":")
    await state.set_state(ShipmentFSM.waiting_item_price)
    await state.update_data(shipment_id=int(shipment_id), item_id=int(item_id), return_to=return_to)
    await callback.message.edit_text("💰 Укажите <b>цену товара</b> для базы данных (например: <i>1500 ₽</i> или <i>120 ¥</i>):")
    await callback.answer()


@router.message(ShipmentFSM.waiting_item_price, F.text)
async def msg_item_price(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    shipment_id = data["shipment_id"]
    item_id = data["item_id"]
    return_to = data.get("return_to", "add")
    update_item_my_price(item_id, message.text)
    await state.clear()

    item = get_item(item_id)
    shipment = get_shipment(shipment_id)
    if return_to == "edit":
        await message.answer(
            f"✅ Данные обновлены!\n\n{format_shipment_summary(shipment)}",
            reply_markup=edit_shipment_kb(shipment_id),
        )
    else:
        await message.answer(
            f"✅ Товар сохранён!\n"
            f"📦 <b>{h(item['name'])}</b> — <code>{h(item['track_number'])}</code>\n"
            f"👤 Мой • 💰 {h(item['price'])}\n\n"
            f"Всего позиций в «{h(shipment['name'])}»: <b>{len(get_items(shipment_id))}</b>",
            reply_markup=after_item_completed_kb(shipment_id),
        )


@router.callback_query(F.data.startswith("owner_client:"))
async def cb_owner_client(callback: CallbackQuery, state: FSMContext) -> None:
    _, shipment_id, item_id, return_to = callback.data.split(":")
    await state.set_state(ShipmentFSM.waiting_item_client)
    await state.update_data(shipment_id=int(shipment_id), item_id=int(item_id), return_to=return_to)
    await callback.message.edit_text("🧑‍💼 Укажите <b>Telegram ID или имя клиента</b> (например: <i>@username</i> или <i>Иван 123456</i>):")
    await callback.answer()


@router.message(ShipmentFSM.waiting_item_client, F.text)
async def msg_item_client(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    shipment_id = data["shipment_id"]
    item_id = data["item_id"]
    return_to = data.get("return_to", "add")
    update_item_client(item_id, message.text)
    await state.clear()

    item = get_item(item_id)
    shipment = get_shipment(shipment_id)
    if return_to == "edit":
        await message.answer(
            f"✅ Данные обновлены!\n\n{format_shipment_summary(shipment)}",
            reply_markup=edit_shipment_kb(shipment_id),
        )
    else:
        await message.answer(
            f"✅ Товар сохранён!\n"
            f"📦 <b>{h(item['name'])}</b> — <code>{h(item['track_number'])}</code>\n"
            f"🧑‍💼 Клиент: {h(item['client_contact'])}\n\n"
            f"Всего позиций в «{h(shipment['name'])}»: <b>{len(get_items(shipment_id))}</b>",
            reply_markup=after_item_completed_kb(shipment_id),
        )


@router.callback_query(F.data == "edit_list")
async def cb_edit_list(callback: CallbackQuery) -> None:
    shipments = get_shipments("active")
    if not shipments:
        await callback.message.edit_text(
            "Активных отправок пока нет.",
            reply_markup=crm_menu_kb(),
        )
        await callback.answer()
        return
    await callback.message.edit_text(
        "✏️ Выберите отправку, которую хотите изменить:",
        reply_markup=shipments_pick_kb(shipments, "edit_ship"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("edit_ship:"))
async def cb_edit_ship(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    shipment_id = int(callback.data.split(":")[1])
    shipment = get_shipment(shipment_id)
    if not shipment:
        await callback.answer("Отправка не найдена", show_alert=True)
        return
    await callback.message.edit_text(
        f"{format_shipment_summary(shipment)}\n\nВыберите, что хотите изменить:",
        reply_markup=edit_shipment_kb(shipment_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("edit_item_menu:"))
async def cb_edit_item_menu(callback: CallbackQuery) -> None:
    _, shipment_id, item_id = callback.data.split(":")
    item = get_item(int(item_id))
    if not item:
        await callback.answer("Товар не найден", show_alert=True)
        return
    track = f"<code>{h(item['track_number'])}</code>" if item["track_number"] else "<i>не указан</i>"
    await callback.message.edit_text(
        f"📦 Товар: <b>{h(item['name'])}</b>\n"
        f"🔢 Трек-номер: {track}\n"
        f"👤 Принадлежность: {format_owner_tag(item)}\n\n"
        f"Что изменить?",
        reply_markup=edit_item_kb(int(shipment_id), int(item_id)),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("edit_item_name:"))
async def cb_edit_item_name(callback: CallbackQuery, state: FSMContext) -> None:
    _, shipment_id, item_id = callback.data.split(":")
    await state.set_state(ShipmentFSM.waiting_edit_item_name)
    await state.update_data(shipment_id=int(shipment_id), item_id=int(item_id))
    await callback.message.edit_text("✏️ Введите новое <b>название товара</b>:")
    await callback.answer()


@router.message(ShipmentFSM.waiting_edit_item_name, F.text)
async def msg_edit_item_name(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    shipment_id = data["shipment_id"]
    item_id = data["item_id"]
    update_item_name(item_id, message.text)
    await state.clear()
    shipment = get_shipment(shipment_id)
    await message.answer(
        f"✅ Название товара обновлено!\n\n{format_shipment_summary(shipment)}",
        reply_markup=edit_shipment_kb(shipment_id),
    )


@router.callback_query(F.data.startswith("edit_item_track:"))
async def cb_edit_item_track(callback: CallbackQuery, state: FSMContext) -> None:
    _, shipment_id, item_id = callback.data.split(":")
    await state.set_state(ShipmentFSM.waiting_edit_item_track)
    await state.update_data(shipment_id=int(shipment_id), item_id=int(item_id))
    await callback.message.edit_text("🔢 Введите новый <b>трек-номер</b>:")
    await callback.answer()


@router.message(ShipmentFSM.waiting_edit_item_track, F.text)
async def msg_edit_item_track(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    shipment_id = data["shipment_id"]
    item_id = data["item_id"]
    update_item_track(item_id, message.text)
    await state.clear()
    shipment = get_shipment(shipment_id)
    await message.answer(
        f"✅ Трек-номер обновлён!\n\n{format_shipment_summary(shipment)}",
        reply_markup=edit_shipment_kb(shipment_id),
    )


@router.callback_query(F.data.startswith("edit_item_owner:"))
async def cb_edit_item_owner(callback: CallbackQuery) -> None:
    _, shipment_id, item_id = callback.data.split(":")
    await callback.message.edit_text(
        "<b>Чей это товар?</b>",
        reply_markup=owner_choice_kb(int(shipment_id), int(item_id), return_to="edit"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("delete_item:"))
async def cb_delete_item(callback: CallbackQuery) -> None:
    _, shipment_id, item_id = callback.data.split(":")
    delete_item(int(item_id))
    shipment = get_shipment(int(shipment_id))
    await callback.message.edit_text(
        f"🗑 Товар удалён.\n\n{format_shipment_summary(shipment)}",
        reply_markup=edit_shipment_kb(int(shipment_id)),
    )
    await callback.answer("Товар удалён")


@router.callback_query(F.data == "finish_list")
async def cb_finish_list(callback: CallbackQuery) -> None:
    shipments = get_shipments("active")
    if not shipments:
        await callback.message.edit_text(
            "Активных отправок пока нет.",
            reply_markup=crm_menu_kb(),
        )
        await callback.answer()
        return
    await callback.message.edit_text(
        "✅ Выберите отправку, чтобы получить готовый список товаров и трек-номеров:",
        reply_markup=shipments_pick_kb(shipments, "finish_ship"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("finish_ship:"))
async def cb_finish_ship(callback: CallbackQuery) -> None:
    shipment_id = int(callback.data.split(":")[1])
    shipment = get_shipment(shipment_id)
    if not shipment:
        await callback.answer("Отправка не найдена", show_alert=True)
        return
    await callback.message.edit_text(
        format_shipment_summary(shipment, include_owner=False),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✏️ Изменить отправку", callback_data=f"edit_ship:{shipment_id}")],
                [InlineKeyboardButton(text="⬅️ В меню CRM", callback_data="crm_menu")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "arrive_list")
async def cb_arrive_list(callback: CallbackQuery) -> None:
    shipments = get_shipments("active")
    if not shipments:
        await callback.message.edit_text(
            "Активных отправок сейчас нет.",
            reply_markup=crm_menu_kb(),
        )
        await callback.answer()
        return
    await callback.message.edit_text(
        "🚚 Выберите груз, который приехал (он уйдёт в архив):",
        reply_markup=shipments_pick_kb(shipments, "arrive_ship"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("arrive_ship:"))
async def cb_arrive_ship(callback: CallbackQuery) -> None:
    shipment_id = int(callback.data.split(":")[1])
    shipment = get_shipment(shipment_id)
    if not shipment:
        await callback.answer("Отправка не найдена", show_alert=True)
        return
    archive_shipment(shipment_id)
    await callback.message.edit_text(
        f"📥 Груз <b>{h(shipment['name'])}</b> отмечен как прибывший и перенесён в архив.",
        reply_markup=crm_menu_kb(),
    )
    await callback.answer("Перенесено в архив")


@router.callback_query(F.data == "archive_list")
async def cb_archive_list(callback: CallbackQuery) -> None:
    archived = get_shipments("archived")
    if not archived:
        await callback.message.edit_text(
            "Архив пуст.",
            reply_markup=crm_menu_kb(),
        )
        await callback.answer()
        return
    await callback.message.edit_text(
        "🗄 <b>Архив доставок</b>\nВыберите отправку, чтобы посмотреть список:",
        reply_markup=shipments_pick_kb(archived, "view_archive"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("view_archive:"))
async def cb_view_archive(callback: CallbackQuery) -> None:
    shipment_id = int(callback.data.split(":")[1])
    shipment = get_shipment(shipment_id)
    if not shipment:
        await callback.answer("Отправка не найдена", show_alert=True)
        return
    await callback.message.edit_text(
        f"🗄 <i>(В архиве)</i>\n{format_shipment_summary(shipment)}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад в архив", callback_data="archive_list")],
                [InlineKeyboardButton(text="⬅️ В меню CRM", callback_data="crm_menu")],
            ]
        ),
    )
    await callback.answer()


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Не задана переменная окружения BOT_TOKEN")
    init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(AccessMiddleware())
    dp.callback_query.middleware(AccessMiddleware())
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())