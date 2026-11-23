import asyncio
import hashlib
from html import escape as h
import logging
import os
import sqlite3
from contextlib import closing
from typing import Optional

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, TelegramObject

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATA_DIR = os.getenv("DATA_DIR", "/app/data")
DEFAULT_DB = os.path.join(DATA_DIR, "shipments.db") if os.path.isdir(DATA_DIR) else "shipments.db"
DB_PATH = os.getenv("DB_PATH", DEFAULT_DB)
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ENV_ADMIN_ID = os.getenv("ADMIN_ID", "").strip()


def hash_user_id(user_id: int) -> str:
    """Односторонний SHA-256 хэш Telegram ID для анонимного хранения в БД."""
    return hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()


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
        conn.commit()


def get_owner_hash() -> Optional[str]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'owner_hash'").fetchone()
        return row[0] if row else None


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
        # Если главный админ ещё не назначен, первый кто напишет /start станет им
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


# ---------------------------------------------------------------------------
# Клавиатуры
# ---------------------------------------------------------------------------

def main_menu_kb(user_id: int) -> InlineKeyboardMarkup:
    active = get_shipments("active")
    archived = get_shipments("archived")

    rows = [[InlineKeyboardButton(text="📦 Создать отправку", callback_data="create_shipment")]]

    if active:
        rows.append([InlineKeyboardButton(text="✅ Завершить отправку", callback_data="finish_list")])
        rows.append([InlineKeyboardButton(text="✏️ Изменить отправку", callback_data="edit_list")])
        rows.append([InlineKeyboardButton(text="🚚 Груз приехал", callback_data="arrive_list")])

    if archived:
        rows.append([InlineKeyboardButton(text="🗄 Архив", callback_data="archive_list")])

    if is_owner(user_id):
        rows.append([InlineKeyboardButton(text="👥 Доступ (Админы)", callback_data="admins_menu")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


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
            [InlineKeyboardButton(text="🏠 Выйти в главное меню", callback_data="main_menu")],
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
            [InlineKeyboardButton(text="🏠 Выйти в главное меню", callback_data="main_menu")],
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
    rows.append([InlineKeyboardButton(text="🏠 Выйти в главное меню", callback_data="main_menu")])
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
    rows.append([InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# Форматирование текста
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
        "👋 <b>Главное меню</b>\nВыберите действие:",
        reply_markup=main_menu_kb(message.from_user.id),
    )


@router.callback_query(F.data == "main_menu")
async def cb_main_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "👋 <b>Главное меню</b>\nВыберите действие:",
        reply_markup=main_menu_kb(callback.from_user.id),
    )
    await callback.answer()


# --- Управление админами (только для Главного админа) ---

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
        "Все админы видят общие отправки и могут добавлять товары и трек-номера."
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
        f"✅ Админ <b>{message.text.strip()}</b> добавлен! Теперь он может открыть этого бота и работать с общей базой.",
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


# --- Создание отправки ---

@router.callback_query(F.data == "create_shipment")
async def cb_create_shipment(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(ShipmentFSM.waiting_shipment_name)
    await callback.message.edit_text(
        "📝 Напишите <b>название для отправки</b> (например: <i>Октябрь №1</i> или <i>Чехлы 24.11</i>):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="main_menu")]]
        ),
    )
    await callback.answer()


@router.message(ShipmentFSM.waiting_shipment_name, F.text)
async def msg_shipment_name(message: Message, state: FSMContext) -> None:
    shipment_id = create_shipment(message.from_user.id, message.text)
    await state.clear()
    await message.answer(
        f"✅ Отправка <b>{message.text.strip()}</b> создана.\nЧто делаем дальше?",
        reply_markup=after_shipment_created_kb(shipment_id),
    )


# --- Изменить название отправки ---

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
            f"✅ Название отправки изменено на <b>{message.text.strip()}</b>.",
            reply_markup=after_shipment_created_kb(shipment_id),
        )


# --- Добавление товара ---

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
        f"Товар: <b>{message.text.strip()}</b>\nТеперь можно изменить название или добавить трек-номер:",
        reply_markup=after_item_name_kb(shipment_id, item_id),
    )


# --- Изменить название только что добавленного товара ---

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
        f"✅ Название товара изменено на <b>{message.text.strip()}</b>.",
        reply_markup=after_item_name_kb(shipment_id, item_id),
    )


# --- Добавление трек-номера и выбор «Мой / Клиент» ---

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
        f"🔢 Трек-номер <code>{item['track_number']}</code> сохранён.\n\n"
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
            f"📦 <b>{item['name']}</b> — <code>{item['track_number']}</code>\n"
            f"👤 Мой • 💰 {item['price']}\n\n"
            f"Всего позиций в «{shipment['name']}»: <b>{len(get_items(shipment_id))}</b>",
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
            f"📦 <b>{item['name']}</b> — <code>{item['track_number']}</code>\n"
            f"🧑‍💼 Клиент: {item['client_contact']}\n\n"
            f"Всего позиций в «{shipment['name']}»: <b>{len(get_items(shipment_id))}</b>",
            reply_markup=after_item_completed_kb(shipment_id),
        )


# --- Редактирование отправки и товаров ---

@router.callback_query(F.data == "edit_list")
async def cb_edit_list(callback: CallbackQuery) -> None:
    shipments = get_shipments("active")
    if not shipments:
        await callback.message.edit_text(
            "Активных отправок пока нет.",
            reply_markup=main_menu_kb(callback.from_user.id),
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
    track = f"<code>{item['track_number']}</code>" if item["track_number"] else "<i>не указан</i>"
    await callback.message.edit_text(
        f"📦 Товар: <b>{item['name']}</b>\n"
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


# --- Завершить отправку (выдача единого списка) ---

@router.callback_query(F.data == "finish_list")
async def cb_finish_list(callback: CallbackQuery) -> None:
    shipments = get_shipments("active")
    if not shipments:
        await callback.message.edit_text(
            "Активных отправок пока нет.",
            reply_markup=main_menu_kb(callback.from_user.id),
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
                [InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu")],
            ]
        ),
    )
    await callback.answer()


# --- Груз приехал (перенос в архив) ---

@router.callback_query(F.data == "arrive_list")
async def cb_arrive_list(callback: CallbackQuery) -> None:
    shipments = get_shipments("active")
    if not shipments:
        await callback.message.edit_text(
            "Активных отправок сейчас нет.",
            reply_markup=main_menu_kb(callback.from_user.id),
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
        f"📥 Груз <b>{shipment['name']}</b> отмечен как прибывший и перенесён в архив.",
        reply_markup=main_menu_kb(callback.from_user.id),
    )
    await callback.answer("Перенесено в архив")


# --- Просмотр архива ---

@router.callback_query(F.data == "archive_list")
async def cb_archive_list(callback: CallbackQuery) -> None:
    archived = get_shipments("archived")
    if not archived:
        await callback.message.edit_text(
            "Архив пуст.",
            reply_markup=main_menu_kb(callback.from_user.id),
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
                [InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu")],
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