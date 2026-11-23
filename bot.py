import asyncio
import hashlib
import hmac
import secrets
import json
import logging
import os
import re
import sqlite3
from contextlib import closing
from html import escape as h
from acts import generate_act_pdf
from devices_catalog import search_devices
from repair import match_work_price, search_parts_all
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
    BufferedInputFile,
    TelegramObject,
    WebAppInfo,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATA_DIR = os.getenv("DATA_DIR", "/app/data")
DEFAULT_DB = os.path.join(DATA_DIR, "shipments.db") if os.path.isdir(DATA_DIR) else "shipments.db"
DB_PATH = os.getenv("DB_PATH", DEFAULT_DB)
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ENV_ADMIN_ID = os.getenv("ADMIN_ID", "").strip()
ENV_CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()
_RAW_WEB_URL = os.getenv("WEB_URL", "").strip()
WEB_URL = (_RAW_WEB_URL if _RAW_WEB_URL.startswith("http") else f"https://{_RAW_WEB_URL}") if _RAW_WEB_URL else ""

BUY_URL = "https://t.me/QQHead"

_media_group_locks: dict[str, asyncio.Lock] = {}


def hash_user_id(user_id: int) -> str:
    """Односторонний SHA-256 хэш Telegram ID для анонимного хранения в БД."""
    return hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()


def parse_amount(raw: str) -> float:
    """Извлекает число из строки вроде '14.990 ₽', '14 990', '1500.50'."""
    if not raw:
        return 0.0
    cleaned = raw.replace(" ", "").replace("\u00a0", "")
    # Оставляем только цифры, точки и запятые
    m = re.search(r"[\d.,]+", cleaned)
    if not m:
        return 0.0
    num_str = m.group(0)
    # Если формат 14.990 или 14,990 (ровно 3 цифры после разделителя) — это разделитель тысяч
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", num_str):
        num_str = num_str.replace(".", "").replace(",", "")
    else:
        num_str = num_str.replace(",", ".")
    try:
        return float(num_str)
    except ValueError:
        return 0.0


def fmt_money(val: float) -> str:
    if val == int(val):
        return f"{int(val):,}".replace(",", " ") + " ₽"
    return f"{val:,.2f}".replace(",", " ") + " ₽"


def utf16_len(text: str) -> int:
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
    match = re.search(r"[ \t]*#НАЛИЧИЕ[ \t]*\n?", text, flags=re.IGNORECASE)
    if match:
        start_char, end_char = match.start(), match.end()
        start_u16 = utf16_len(text[:start_char])
        end_u16 = utf16_len(text[:end_char])
        removed_u16 = end_u16 - start_u16
        raw_joined = text[:start_char] + text[end_char:]
        new_text = raw_joined.strip("\n")

        leading_trimmed = len(raw_joined) - len(raw_joined.lstrip("\n"))
        lead_u16 = utf16_len(raw_joined[:leading_trimmed])

        new_entities: list[MessageEntity] = []
        for ent in entities:
            ent_start = ent.offset
            ent_end = ent.offset + ent.length

            if ent_start >= start_u16 and ent_end <= end_u16:
                continue

            if ent_end <= start_u16:
                adj_offset = ent_start - lead_u16
                adj_length = ent.length
            elif ent_start >= end_u16:
                adj_offset = ent_start - removed_u16 - lead_u16
                adj_length = ent.length
            else:
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
                can_crm INTEGER NOT NULL DEFAULT 1,
                can_post INTEGER NOT NULL DEFAULT 1,
                can_personal_ledger INTEGER NOT NULL DEFAULT 0,
                can_repair INTEGER NOT NULL DEFAULT 0,
                can_parts_search INTEGER NOT NULL DEFAULT 0,
                can_repair_crm INTEGER NOT NULL DEFAULT 0,
                web_password_hash TEXT DEFAULT '',
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
                delivery_cost REAL NOT NULL DEFAULT 0,
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
                sold_price TEXT DEFAULT '',
                sold_at TIMESTAMP DEFAULT NULL,
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
                linked_item_id INTEGER DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(channel_id, message_id)
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS repair_prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                keywords TEXT NOT NULL DEFAULT '',
                work_price REAL NOT NULL DEFAULT 0
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS repair_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_name TEXT NOT NULL DEFAULT '',
                client_phone TEXT NOT NULL DEFAULT '',
                device_model TEXT NOT NULL,
                imei_sn TEXT NOT NULL DEFAULT '',
                issue_desc TEXT NOT NULL DEFAULT '',
                appearance TEXT NOT NULL DEFAULT '',
                completeness TEXT NOT NULL DEFAULT '',
                passcode_note TEXT NOT NULL DEFAULT '',
                service_name TEXT NOT NULL DEFAULT '',
                client_info TEXT NOT NULL DEFAULT '',
                part_cost REAL NOT NULL DEFAULT 0,
                prepayment REAL NOT NULL DEFAULT 0,
                total_price REAL NOT NULL DEFAULT 0,
                warranty_days INTEGER NOT NULL DEFAULT 30,
                status TEXT NOT NULL DEFAULT 'in_work',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                done_at TIMESTAMP DEFAULT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS personal_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_hash TEXT NOT NULL,
                title TEXT NOT NULL,
                condition TEXT DEFAULT '',
                buy_price REAL NOT NULL DEFAULT 0,
                sell_price REAL DEFAULT NULL,
                photo_ref TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'in_stock',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                sold_at TIMESTAMP DEFAULT NULL
            );
            """
        )

        # Мягкие миграции для существующих таблиц
        admin_cols = {r[1] for r in conn.execute("PRAGMA table_info(admins)").fetchall()}
        if "can_crm" not in admin_cols:
            conn.execute("ALTER TABLE admins ADD COLUMN can_crm INTEGER NOT NULL DEFAULT 1")
        if "can_post" not in admin_cols:
            conn.execute("ALTER TABLE admins ADD COLUMN can_post INTEGER NOT NULL DEFAULT 1")
        if "can_personal_ledger" not in admin_cols:
            conn.execute("ALTER TABLE admins ADD COLUMN can_personal_ledger INTEGER NOT NULL DEFAULT 0")
        if "web_password_hash" not in admin_cols:
            conn.execute("ALTER TABLE admins ADD COLUMN web_password_hash TEXT DEFAULT ''")
        if "can_repair" not in admin_cols:
            conn.execute("ALTER TABLE admins ADD COLUMN can_repair INTEGER NOT NULL DEFAULT 0")
        if "can_parts_search" not in admin_cols:
            conn.execute("ALTER TABLE admins ADD COLUMN can_parts_search INTEGER NOT NULL DEFAULT 0")
            conn.execute("UPDATE admins SET can_parts_search = can_repair WHERE can_repair = 1")
        if "can_repair_crm" not in admin_cols:
            conn.execute("ALTER TABLE admins ADD COLUMN can_repair_crm INTEGER NOT NULL DEFAULT 0")
            conn.execute("UPDATE admins SET can_repair_crm = can_repair WHERE can_repair = 1")

        ro_cols = {r[1] for r in conn.execute("PRAGMA table_info(repair_orders)").fetchall()}
        for col, ddl in [
            ("client_name", "TEXT NOT NULL DEFAULT ''"),
            ("client_phone", "TEXT NOT NULL DEFAULT ''"),
            ("imei_sn", "TEXT NOT NULL DEFAULT ''"),
            ("issue_desc", "TEXT NOT NULL DEFAULT ''"),
            ("appearance", "TEXT NOT NULL DEFAULT ''"),
            ("completeness", "TEXT NOT NULL DEFAULT ''"),
            ("passcode_note", "TEXT NOT NULL DEFAULT ''"),
            ("prepayment", "REAL NOT NULL DEFAULT 0"),
            ("warranty_days", "INTEGER NOT NULL DEFAULT 30"),
        ]:
            if col not in ro_cols:
                conn.execute(f"ALTER TABLE repair_orders ADD COLUMN {col} {ddl}")

        if not conn.execute("SELECT value FROM settings WHERE key = 'web_secret'").fetchone():
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('web_secret', ?)",
                (secrets.token_hex(32),),
            )

        ship_cols = {r[1] for r in conn.execute("PRAGMA table_info(shipments)").fetchall()}
        if "delivery_cost" not in ship_cols:
            conn.execute("ALTER TABLE shipments ADD COLUMN delivery_cost REAL NOT NULL DEFAULT 0")

        item_cols = {r[1] for r in conn.execute("PRAGMA table_info(items)").fetchall()}
        if "owner_type" not in item_cols:
            conn.execute("ALTER TABLE items ADD COLUMN owner_type TEXT DEFAULT ''")
        if "price" not in item_cols:
            conn.execute("ALTER TABLE items ADD COLUMN price TEXT DEFAULT ''")
        if "client_contact" not in item_cols:
            conn.execute("ALTER TABLE items ADD COLUMN client_contact TEXT DEFAULT ''")
        if "sold_price" not in item_cols:
            conn.execute("ALTER TABLE items ADD COLUMN sold_price TEXT DEFAULT ''")
        if "sold_at" not in item_cols:
            conn.execute("ALTER TABLE items ADD COLUMN sold_at TIMESTAMP DEFAULT NULL")

        post_cols = {r[1] for r in conn.execute("PRAGMA table_info(posts)").fetchall()}
        if "linked_item_id" not in post_cols:
            conn.execute("ALTER TABLE posts ADD COLUMN linked_item_id INTEGER DEFAULT NULL")

        pe_cols = {r[1] for r in conn.execute("PRAGMA table_info(personal_entries)").fetchall()}
        if "condition" not in pe_cols:
            conn.execute("ALTER TABLE personal_entries ADD COLUMN condition TEXT DEFAULT ''")
        if "photo_ref" not in pe_cols:
            conn.execute("ALTER TABLE personal_entries ADD COLUMN photo_ref TEXT DEFAULT ''")

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
        if conn.execute("SELECT COUNT(*) FROM repair_prices").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO repair_prices (title, keywords, work_price) VALUES (?, ?, ?)",
                [
                    ("Замена дисплея (базовая)", "экран,дисплей,модуль,матрица,lcd,oled", 2500.0),
                    ("Замена аккумулятора (АКБ)", "акб,батарея,аккумулятор", 1500.0),
                    ("Замена разъёма зарядки", "разъем,гнездо,зарядка,шлейф", 2000.0),
                    ("Прочий ремонт", "ремонт,камера,стекло,корпус,динамик", 2000.0),
                ],
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


def get_admin_record(user_id: int) -> Optional[sqlite3.Row]:
    uid_hash = hash_user_id(user_id)
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM admins WHERE user_hash = ?", (uid_hash,)).fetchone()


def get_admin_by_hash(uid_hash: str) -> Optional[sqlite3.Row]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM admins WHERE user_hash = ?", (uid_hash,)).fetchone()


def get_admin_by_id(admin_row_id: int) -> Optional[sqlite3.Row]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM admins WHERE id = ?", (admin_row_id,)).fetchone()


def is_admin(user_id: int) -> bool:
    return is_owner(user_id) or (get_admin_record(user_id) is not None)


def has_perm(user_id: int, perm: str) -> bool:
    if is_owner(user_id):
        return perm in ("can_crm", "can_post", "can_finances", "can_parts_search", "can_repair_crm")
    rec = get_admin_record(user_id)
    if not rec:
        return False
    return bool(rec[perm]) if perm in rec.keys() else False


def add_admin(target_user_id: int, label: str) -> None:
    uid_hash = hash_user_id(target_user_id)
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            """
            INSERT INTO admins (user_hash, label, can_crm, can_post, can_personal_ledger)
            VALUES (?, ?, 1, 1, 0)
            ON CONFLICT(user_hash) DO UPDATE SET label = excluded.label
            """,
            (uid_hash, label.strip() or "Админ"),
        )
        conn.commit()


def toggle_admin_perm(admin_row_id: int, perm: str) -> None:
    if perm not in ("can_crm", "can_post", "can_personal_ledger", "can_parts_search", "can_repair_crm"):
        return
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            f"UPDATE admins SET {perm} = CASE WHEN {perm} = 1 THEN 0 ELSE 1 END WHERE id = ?",
            (admin_row_id,),
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

def save_post(
    channel_id: str,
    message_id: int,
    text: str,
    entities_json: str,
    linked_item_id: Optional[int] = None,
) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO posts (channel_id, message_id, text, entities_json, status, linked_item_id)
            VALUES (?, ?, ?, ?, 'active', ?)
            """,
            (str(channel_id), message_id, text, entities_json, linked_item_id),
        )
        conn.commit()


def get_post(channel_id: str, message_id: int) -> Optional[sqlite3.Row]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT * FROM posts WHERE channel_id = ? AND message_id = ?",
            (str(channel_id), message_id),
        ).fetchone()


def get_post_by_id(post_id: int) -> Optional[sqlite3.Row]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()


def get_all_posts() -> list[sqlite3.Row]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            """
            SELECT p.*, i.name AS linked_item_name
            FROM posts p
            LEFT JOIN items i ON i.id = p.linked_item_id
            ORDER BY p.id DESC
            """
        ).fetchall()


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


# --- Работа с отправками и товарами CRM ---

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


def archive_shipment(shipment_id: int, delivery_cost: float = 0.0) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            "UPDATE shipments SET status = 'archived', delivery_cost = ? WHERE id = ?",
            (delivery_cost, shipment_id),
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


def get_unsold_my_items() -> list[sqlite3.Row]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            """
            SELECT i.*, s.name AS shipment_name
            FROM items i
            JOIN shipments s ON s.id = i.shipment_id
            WHERE i.owner_type = 'my' AND (i.sold_price IS NULL OR i.sold_price = '')
            ORDER BY i.id DESC
            LIMIT 25
            """
        ).fetchall()


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


def mark_item_sold(item_id: int, sold_price: str) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            "UPDATE items SET sold_price = ?, sold_at = CURRENT_TIMESTAMP WHERE id = ?",
            (sold_price.strip(), item_id),
        )
        conn.commit()


def delete_item(item_id: int) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        conn.commit()


def get_crm_finances() -> dict:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        my_items = conn.execute(
            """
            SELECT i.*, s.status AS shipment_status
            FROM items i
            JOIN shipments s ON s.id = i.shipment_id
            WHERE i.owner_type = 'my'
            """
        ).fetchall()
        deliveries = conn.execute(
            "SELECT COALESCE(SUM(delivery_cost), 0) AS total_delivery FROM shipments WHERE status = 'archived'"
        ).fetchone()

    invested_active = 0.0
    invested_total = 0.0
    revenue_total = 0.0
    sold_cost = 0.0
    sold_count = 0
    in_stock_count = 0

    for it in my_items:
        buy = parse_amount(it["price"])
        invested_total += buy
        if it["shipment_status"] == "active":
            invested_active += buy
        if it["sold_price"]:
            sell = parse_amount(it["sold_price"])
            revenue_total += sell
            sold_cost += buy
            sold_count += 1
        else:
            in_stock_count += 1

    # Добавляем прямые товары владельца (не из отправок с треками)
    owner_hash = get_owner_hash()
    direct_entries = []
    if owner_hash:
        with closing(sqlite3.connect(DB_PATH)) as conn:
            conn.row_factory = sqlite3.Row
            direct_entries = conn.execute(
                "SELECT * FROM personal_entries WHERE user_hash = ?",
                (owner_hash,),
            ).fetchall()

    for de in direct_entries:
        buy = float(de["buy_price"] or 0.0)
        invested_total += buy
        if de["status"] == "sold" and de["sell_price"] is not None:
            sell = float(de["sell_price"] or 0.0)
            revenue_total += sell
            sold_cost += buy
            sold_count += 1
        else:
            in_stock_count += 1

    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rep_rows = conn.execute("SELECT * FROM repair_orders WHERE status = 'done'").fetchall()
    for rr in rep_rows:
        pcost = float(rr["part_cost"] or 0.0)
        tprice = float(rr["total_price"] or 0.0)
        invested_total += pcost
        sold_cost += pcost
        revenue_total += tprice
        sold_count += 1

    delivery_total = float(deliveries["total_delivery"] or 0.0)
    return {
        "invested_items": invested_total,
        "invested_active": invested_active,
        "delivery_total": delivery_total,
        "total_spent": invested_total + delivery_total,
        "revenue_total": revenue_total,
        "sold_profit_items": revenue_total - sold_cost,
        "net_overall": revenue_total - (invested_total + delivery_total),
        "sold_count": sold_count,
        "in_stock_count": in_stock_count,
    }


# --- Личный учёт админов (полностью изолирован по user_hash) ---

def add_personal_entry_by_hash(
    uid_hash: str, title: str, condition: str, buy_price: float, photo_ref: str = ""
) -> int:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute(
            """
            INSERT INTO personal_entries (user_hash, title, condition, buy_price, photo_ref, status)
            VALUES (?, ?, ?, ?, ?, 'in_stock')
            """,
            (uid_hash, title.strip(), condition.strip(), buy_price, photo_ref),
        )
        conn.commit()
        return cur.lastrowid


def add_personal_entry(
    user_id: int, title: str, buy_price: float, condition: str = "", photo_ref: str = ""
) -> int:
    return add_personal_entry_by_hash(hash_user_id(user_id), title, condition, buy_price, photo_ref)


def get_personal_entries_by_hash(uid_hash: str, status: Optional[str] = None) -> list[sqlite3.Row]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        if status:
            return conn.execute(
                "SELECT * FROM personal_entries WHERE user_hash = ? AND status = ? ORDER BY id DESC",
                (uid_hash, status),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM personal_entries WHERE user_hash = ? ORDER BY id DESC",
            (uid_hash,),
        ).fetchall()


def get_personal_entries(user_id: int, status: Optional[str] = None) -> list[sqlite3.Row]:
    return get_personal_entries_by_hash(hash_user_id(user_id), status)


def mark_personal_sold_by_hash(entry_id: int, uid_hash: str, sell_price: float) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            """
            UPDATE personal_entries
            SET sell_price = ?, status = 'sold', sold_at = CURRENT_TIMESTAMP
            WHERE id = ? AND user_hash = ?
            """,
            (sell_price, entry_id, uid_hash),
        )
        conn.commit()


def mark_personal_sold(entry_id: int, user_id: int, sell_price: float) -> None:
    mark_personal_sold_by_hash(entry_id, hash_user_id(user_id), sell_price)


def delete_personal_entry_by_hash(entry_id: int, uid_hash: str) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("DELETE FROM personal_entries WHERE id = ? AND user_hash = ?", (entry_id, uid_hash))
        conn.commit()


def set_admin_web_password(user_id: int, raw_password: str) -> None:
    uid_hash = hash_user_id(user_id)
    pwd_hash = hashlib.sha256(raw_password.encode("utf-8")).hexdigest()
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            "UPDATE admins SET web_password_hash = ? WHERE user_hash = ?",
            (pwd_hash, uid_hash),
        )
        conn.commit()


def find_admin_by_web_password(raw_password: str) -> Optional[sqlite3.Row]:
    if not raw_password:
        return None
    pwd_hash = hashlib.sha256(raw_password.encode("utf-8")).hexdigest()
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT * FROM admins WHERE web_password_hash = ?",
            (pwd_hash,),
        ).fetchone()


def get_personal_stats_by_hash(uid_hash: str) -> dict:
    entries = get_personal_entries_by_hash(uid_hash)
    invested = sum(e["buy_price"] for e in entries)
    sold_entries = [e for e in entries if e["status"] == "sold" and e["sell_price"] is not None]
    revenue = sum(e["sell_price"] for e in sold_entries)
    sold_buy = sum(e["buy_price"] for e in sold_entries)
    return {
        "total_count": len(entries),
        "in_stock_count": len(entries) - len(sold_entries),
        "sold_count": len(sold_entries),
        "invested": invested,
        "revenue": revenue,
        "profit": revenue - sold_buy,
    }


def get_personal_stats(user_id: int) -> dict:
    entries = get_personal_entries(user_id)
    invested = sum(e["buy_price"] for e in entries)
    sold_entries = [e for e in entries if e["status"] == "sold" and e["sell_price"] is not None]
    revenue = sum(e["sell_price"] for e in sold_entries)
    sold_buy = sum(e["buy_price"] for e in sold_entries)
    return {
        "total_count": len(entries),
        "in_stock_count": len(entries) - len(sold_entries),
        "sold_count": len(sold_entries),
        "invested": invested,
        "revenue": revenue,
        "profit": revenue - sold_buy,
    }


# --- Ремонт: прайс работ и заказы ---

def get_repair_prices() -> list[sqlite3.Row]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM repair_prices ORDER BY id ASC").fetchall()


def add_repair_price(title: str, keywords: str, work_price: float) -> int:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute(
            "INSERT INTO repair_prices (title, keywords, work_price) VALUES (?, ?, ?)",
            (title.strip(), keywords.strip(), work_price),
        )
        conn.commit()
        return cur.lastrowid


def update_repair_price(price_id: int, work_price: float) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("UPDATE repair_prices SET work_price = ? WHERE id = ?", (work_price, price_id))
        conn.commit()


def delete_repair_price(price_id: int) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("DELETE FROM repair_prices WHERE id = ?", (price_id,))
        conn.commit()


def get_repair_orders() -> list[sqlite3.Row]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM repair_orders ORDER BY id DESC LIMIT 40").fetchall()


def get_repair_order_by_id(order_id: int) -> Optional[sqlite3.Row]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM repair_orders WHERE id = ?", (order_id,)).fetchone()


def add_repair_order(
    device_model: str,
    service_name: str,
    client_info: str,
    part_cost: float,
    total_price: float,
    client_name: str = "",
    client_phone: str = "",
    imei_sn: str = "",
    issue_desc: str = "",
    appearance: str = "",
    completeness: str = "",
    passcode_note: str = "",
    prepayment: float = 0.0,
    warranty_days: int = 30,
) -> int:
    cname = client_name.strip() or client_info.strip()
    cinfo = f"{cname} {client_phone.strip()}".strip()
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.execute(
            """
            INSERT INTO repair_orders (
                client_name, client_phone, device_model, imei_sn, issue_desc,
                appearance, completeness, passcode_note, service_name, client_info,
                part_cost, prepayment, total_price, warranty_days, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'in_work')
            """,
            (
                cname,
                client_phone.strip(),
                device_model.strip(),
                imei_sn.strip(),
                issue_desc.strip(),
                appearance.strip(),
                completeness.strip(),
                passcode_note.strip(),
                service_name.strip(),
                cinfo,
                part_cost,
                prepayment,
                total_price,
                warranty_days,
            ),
        )
        conn.commit()
        return cur.lastrowid


def mark_repair_done(order_id: int) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            "UPDATE repair_orders SET status = 'done', done_at = CURRENT_TIMESTAMP WHERE id = ?",
            (order_id,),
        )
        conn.commit()


def delete_repair_order(order_id: int) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("DELETE FROM repair_orders WHERE id = ?", (order_id,))
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
    waiting_delivery_cost = State()
    waiting_item_name = State()
    waiting_rename_item = State()
    waiting_item_track = State()
    waiting_item_price = State()
    waiting_item_client = State()
    waiting_item_sold_price = State()
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
    waiting_post_sold_price = State()


class RepairFSM(StatesGroup):
    waiting_search_query = State()
    waiting_new_price_title = State()
    waiting_new_price_keywords = State()
    waiting_new_price_amount = State()
    waiting_edit_price_amount = State()
    waiting_order_client_name = State()
    waiting_order_client_phone = State()
    waiting_order_device = State()
    waiting_order_imei = State()
    waiting_order_issue = State()
    waiting_order_appearance = State()
    waiting_order_completeness = State()
    waiting_order_passcode = State()
    waiting_order_part_cost = State()
    waiting_order_prepay = State()
    waiting_order_total = State()
    waiting_order_warranty = State()


class PersonalFSM(StatesGroup):
    waiting_entry_title = State()
    waiting_entry_condition = State()
    waiting_entry_buy_price = State()
    waiting_entry_photo = State()
    waiting_entry_sell_price = State()
    waiting_web_password = State()


# ---------------------------------------------------------------------------
# Клавиатуры
# ---------------------------------------------------------------------------

def root_menu_kb(user_id: int) -> InlineKeyboardMarkup:
    rows = []
    if has_perm(user_id, "can_crm"):
        rows.append([InlineKeyboardButton(text="📦 CRM", callback_data="crm_menu")])
    if has_perm(user_id, "can_post"):
        rows.append([InlineKeyboardButton(text="📝 Создать/Редактировать пост", callback_data="posts_menu")])
    if has_perm(user_id, "can_parts_search"):
        rows.append([InlineKeyboardButton(text="🔍 Поиск запчастей", callback_data="parts_menu")])
    if has_perm(user_id, "can_repair_crm"):
        rows.append([InlineKeyboardButton(text="🛠 СРМ Ремонт", callback_data="repair_crm_menu")])
    if has_perm(user_id, "can_personal_ledger"):
        rows.append([InlineKeyboardButton(text="💼 Мой учёт", callback_data="personal_menu")])
    if WEB_URL:
        rows.append([InlineKeyboardButton(text="🌐 Веб-QCRM", web_app=WebAppInfo(url=WEB_URL))])
    if is_owner(user_id):
        rows.append([InlineKeyboardButton(text="👥 Доступ (Админы)", callback_data="admins_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def crm_menu_kb(user_id: int) -> InlineKeyboardMarkup:
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
        rows.append([InlineKeyboardButton(text="📊 Финансы / Учёт", callback_data="crm_finances")])

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


def personal_menu_kb(user_id: Optional[int] = None) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="➕ Записать заход", callback_data="pers_add")],
        [InlineKeyboardButton(text="💰 Отметить продажу", callback_data="pers_sell_list")],
        [InlineKeyboardButton(text="📊 Моя статистика и список", callback_data="pers_stats")],
        [InlineKeyboardButton(text="🔑 Указать пароль для Веб-CRM", callback_data="pers_set_webpwd")],
    ]
    if WEB_URL and user_id is not None:
        rec = get_admin_record(user_id)
        if rec and rec["web_password_hash"]:
            rows.append([InlineKeyboardButton(text="🌐 Открыть Веб-учёт", web_app=WebAppInfo(url=WEB_URL))])
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
            [InlineKeyboardButton(text=f"⚙️ {adm['label']}", callback_data=f"cfg_admin:{adm['id']}")]
        )
    rows.append([InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_config_kb(adm: sqlite3.Row) -> InlineKeyboardMarkup:
    crm_flag = "✅" if adm["can_crm"] else "❌"
    post_flag = "✅" if adm["can_post"] else "❌"
    pers_flag = "✅" if adm["can_personal_ledger"] else "❌"
    parts_flag = "✅" if ("can_parts_search" in adm.keys() and adm["can_parts_search"]) else "❌"
    repcrm_flag = "✅" if ("can_repair_crm" in adm.keys() and adm["can_repair_crm"]) else "❌"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"{crm_flag} Общая CRM (треки и отправки)", callback_data=f"tperm:{adm['id']}:can_crm")],
            [InlineKeyboardButton(text=f"{post_flag} Постинг в канал", callback_data=f"tperm:{adm['id']}:can_post")],
            [InlineKeyboardButton(text=f"{parts_flag} Поиск запчастей и прайс работы", callback_data=f"tperm:{adm['id']}:can_parts_search")],
            [InlineKeyboardButton(text=f"{repcrm_flag} СРМ Ремонт (клиенты и акты)", callback_data=f"tperm:{adm['id']}:can_repair_crm")],
            [InlineKeyboardButton(text=f"{pers_flag} Свой личный учёт (заход/продажа)", callback_data=f"tperm:{adm['id']}:can_personal_ledger")],
            [InlineKeyboardButton(text="🗑 Удалить админа", callback_data=f"del_admin:{adm['id']}")],
            [InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="admins_menu")],
        ]
    )


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
        sold_mark = " 💰" if item["sold_price"] else ""
        btn_text = f"✏️ {idx}. {item['name'][:20]} ({track_label[:12]}){sold_mark}"
        rows.append([InlineKeyboardButton(text=btn_text, callback_data=f"edit_item_menu:{shipment_id}:{item['id']}")])

    rows.append([InlineKeyboardButton(text="➕ Добавить ещё товар", callback_data=f"add_item:{shipment_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ В меню CRM", callback_data="crm_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def edit_item_kb(shipment_id: int, item_id: int, user_id: int, item: sqlite3.Row) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="✏️ Изменить название товара", callback_data=f"edit_item_name:{shipment_id}:{item_id}")],
        [InlineKeyboardButton(text="🔢 Изменить трек-номер", callback_data=f"edit_item_track:{shipment_id}:{item_id}")],
        [InlineKeyboardButton(text="👤 Изменить (Мой / Клиент)", callback_data=f"edit_item_owner:{shipment_id}:{item_id}")],
    ]
    if is_owner(user_id) and item["owner_type"] == "my":
        rows.append([InlineKeyboardButton(text="💰 Отметить / изменить продажу", callback_data=f"sell_crm_item:{shipment_id}:{item_id}")])
    rows.append([InlineKeyboardButton(text="🗑 Удалить товар", callback_data=f"delete_item:{shipment_id}:{item_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад к отправке", callback_data=f"edit_ship:{shipment_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
# Форматирование текста CRM (с защитой финансов от обычных админов)
# ---------------------------------------------------------------------------

def format_owner_tag(item: sqlite3.Row, show_finances: bool) -> str:
    if item["owner_type"] == "my":
        if not show_finances:
            return "Мой (склад)"
        price = h(item["price"]) if item["price"] else "цена не указана"
        if item["sold_price"]:
            profit = parse_amount(item["sold_price"]) - parse_amount(item["price"])
            return f"Мой • закуп {price} • продано {h(item['sold_price'])} (+{fmt_money(profit)})"
        return f"Мой • {price}"
    if item["owner_type"] == "client":
        client = h(item["client_contact"]) if item["client_contact"] else "клиент не указан"
        return f"Клиент • {client}"
    return "владелец не указан"


def format_shipment_summary(
    shipment: sqlite3.Row, include_owner: bool = True, show_finances: bool = False
) -> str:
    items = get_items(shipment["id"])
    lines = [f"📦 <b>{h(shipment['name'])}</b>"]
    if show_finances and shipment["status"] == "archived":
        lines.append(f"🚚 Доставка: <b>{fmt_money(float(shipment['delivery_cost'] or 0))}</b>")
    lines.append("")
    if not items:
        lines.append("<i>Товаров пока нет.</i>")
    else:
        for idx, item in enumerate(items, start=1):
            track = f"<code>{h(item['track_number'])}</code>" if item["track_number"] else "<i>трек не указан</i>"
            if include_owner:
                owner_info = format_owner_tag(item, show_finances=show_finances)
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
    if not has_perm(callback.from_user.id, "can_crm"):
        await callback.answer("Нет доступа к CRM", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        "📦 <b>CRM — Отправки и трек-номера</b>\nВыберите действие:",
        reply_markup=crm_menu_kb(callback.from_user.id),
    )
    await callback.answer()


# ===========================================================================
# ФИНАНСЫ / УЧЁТ (ТОЛЬКО ГЛАВНЫЙ АДМИН)
# ===========================================================================

@router.callback_query(F.data == "crm_finances")
async def cb_crm_finances(callback: CallbackQuery) -> None:
    if not is_owner(callback.from_user.id):
        await callback.answer("Доступно только главному админу", show_alert=True)
        return
    f = get_crm_finances()
    text = (
        "📊 <b>Финансы CRM (только ваши товары)</b>\n\n"
        f"📦 Товаров «Мой» в наличии/пути: <b>{f['in_stock_count']} шт.</b>\n"
        f"💰 Продано товаров «Мой»: <b>{f['sold_count']} шт.</b>\n\n"
        f"📥 Вложено в товары (всего): <b>{fmt_money(f['invested_items'])}</b>\n"
        f"   └ в пути (активные): <b>{fmt_money(f['invested_active'])}</b>\n"
        f"🚚 Расходы на доставку: <b>{fmt_money(f['delivery_total'])}</b>\n"
        f"💳 <b>Всего вложено (закуп + доставка): {fmt_money(f['total_spent'])}</b>\n\n"
        f"💵 Выручка с продаж: <b>{fmt_money(f['revenue_total'])}</b>\n"
        f"📈 Прибыль по проданным товарам: <b>{fmt_money(f['sold_profit_items'])}</b>\n"
        f"🏦 Общий баланс (выручка − все вложения): <b>{fmt_money(f['net_overall'])}</b>"
    )
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="➕ Добавить свой товар", callback_data="owner_direct_add")],
                [InlineKeyboardButton(text="💰 Продан свой товар", callback_data="owner_direct_sell_list")],
                [InlineKeyboardButton(text="📋 Мои прямые товары", callback_data="owner_direct_list")],
                [InlineKeyboardButton(text="⬅️ В меню CRM", callback_data="crm_menu")],
            ]
        ),
    )
    await callback.answer()


# ===========================================================================
# ЛИЧНЫЙ УЧЁТ АДМИНОВ (ИЗОЛИРОВАННЫЙ)
# ===========================================================================

@router.callback_query(F.data == "personal_menu")
async def cb_personal_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if not has_perm(callback.from_user.id, "can_personal_ledger"):
        await callback.answer("Нет доступа к личному учёту", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        "💼 <b>Ваш личный учёт техники</b>\nЭти записи видите только вы.",
        reply_markup=personal_menu_kb(callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data == "owner_direct_add")
async def cb_owner_direct_add(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        return
    await state.set_state(PersonalFSM.waiting_entry_title)
    await state.update_data(return_to="finances")
    await callback.message.edit_text(
        "📝 Напишите <b>название товара</b> (например: <i>Nintendo 3DS XL</i>):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="crm_finances")]]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "owner_direct_sell_list")
async def cb_owner_direct_sell_list(callback: CallbackQuery) -> None:
    if not is_owner(callback.from_user.id):
        return
    items = get_personal_entries(callback.from_user.id, status="in_stock")
    if not items:
        await callback.answer("Нет непроданных прямых товаров", show_alert=True)
        return
    rows = [
        [InlineKeyboardButton(text=f"📦 {it['title'][:24]} ({fmt_money(it['buy_price'])})", callback_data=f"pers_sell:{it['id']}:finances")]
        for it in items[:25]
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="crm_finances")])
    await callback.message.edit_text(
        "💰 Выберите свой товар, который продали:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data == "owner_direct_list")
async def cb_owner_direct_list(callback: CallbackQuery) -> None:
    if not is_owner(callback.from_user.id):
        return
    items = get_personal_entries(callback.from_user.id)[:15]
    lines = ["📋 <b>Ваши прямые товары (вне отправок)</b>\n"]
    if not items:
        lines.append("<i>Пока пусто.</i>")
    for it in items:
        cond = f" ({h(it['condition'])})" if it["condition"] else ""
        if it["status"] == "sold":
            lines.append(f"• {h(it['title'])}{cond}: заход {fmt_money(it['buy_price'])} → продано {fmt_money(it['sell_price'] or 0)}")
        else:
            lines.append(f"• {h(it['title'])}{cond}: заход {fmt_money(it['buy_price'])} <i>(в наличии)</i>")
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="crm_finances")]]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "pers_set_webpwd")
async def cb_pers_set_webpwd(callback: CallbackQuery, state: FSMContext) -> None:
    if not has_perm(callback.from_user.id, "can_personal_ledger"):
        return
    await state.set_state(PersonalFSM.waiting_web_password)
    await callback.message.edit_text(
        "🔑 Введите <b>ваш личный пароль для входа в Веб-CRM</b> (минимум 4 символа):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="personal_menu")]]
        ),
    )
    await callback.answer()


@router.message(PersonalFSM.waiting_web_password, F.text)
async def msg_pers_webpwd(message: Message, state: FSMContext) -> None:
    pwd = message.text.strip()
    if len(pwd) < 4:
        await message.answer("⚠️ Пароль должен быть минимум 4 символа. Попробуйте ещё раз:")
        return
    main_pwd = os.getenv("WEB_PASSWORD", "").strip()
    if main_pwd and pwd == main_pwd:
        await message.answer("⚠️ Этот пароль уже занят, придумайте другой:")
        return
    set_admin_web_password(message.from_user.id, pwd)
    await state.clear()
    await message.answer(
        "✅ Личный пароль для Веб-CRM сохранён! Теперь вы можете войти по нему в веб-интерфейс (там будет только ваш личный раздел «Свои товары»).",
        reply_markup=personal_menu_kb(message.from_user.id),
    )


@router.callback_query(F.data == "pers_add")
async def cb_pers_add(callback: CallbackQuery, state: FSMContext) -> None:
    if not has_perm(callback.from_user.id, "can_personal_ledger"):
        return
    await state.set_state(PersonalFSM.waiting_entry_title)
    await state.update_data(return_to="personal")
    await callback.message.edit_text(
        "📝 Напишите <b>название техники/товара</b> (например: <i>Nintendo 3DS XL</i>):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="personal_menu")]]
        ),
    )
    await callback.answer()


@router.message(PersonalFSM.waiting_entry_title, F.text)
async def msg_pers_title(message: Message, state: FSMContext) -> None:
    await state.update_data(title=message.text.strip())
    await state.set_state(PersonalFSM.waiting_entry_condition)
    await message.answer("🔍 Укажите <b>состояние товара</b> (например: <i>9/10</i> или <i>Новый</i>):")


@router.message(PersonalFSM.waiting_entry_condition, F.text)
async def msg_pers_condition(message: Message, state: FSMContext) -> None:
    await state.update_data(condition=message.text.strip())
    await state.set_state(PersonalFSM.waiting_entry_buy_price)
    await message.answer("💰 Укажите <b>цену захода</b> (сколько вложили, например: <i>8500</i>):")


@router.message(PersonalFSM.waiting_entry_buy_price, F.text)
async def msg_pers_buy(message: Message, state: FSMContext) -> None:
    buy = parse_amount(message.text)
    await state.update_data(buy_price=buy)
    await state.set_state(PersonalFSM.waiting_entry_photo)
    await message.answer(
        "📸 Отправьте <b>1 фото товара</b> или нажмите «Пропустить».\n\n"
        "⚠️ <i>Предупреждение: если фото не нужно — лучше не добавляйте, чтобы не грузить бота и сервер.</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⏭ Пропустить фото", callback_data="pers_skip_photo")]]
        ),
    )


@router.callback_query(PersonalFSM.waiting_entry_photo, F.data == "pers_skip_photo")
async def cb_pers_skip_photo(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    add_personal_entry(
        callback.from_user.id,
        data["title"],
        data["buy_price"],
        condition=data.get("condition", ""),
        photo_ref="",
    )
    ret = data.get("return_to", "personal")
    await state.clear()
    kb = (
        InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📊 К финансам", callback_data="crm_finances")]])
        if ret == "finances"
        else personal_menu_kb()
    )
    await callback.message.edit_text(
        f"✅ Товар сохранён: <b>{h(data['title'])}</b> ({h(data.get('condition', ''))}) — {fmt_money(data['buy_price'])}",
        reply_markup=kb,
    )
    await callback.answer()


@router.message(PersonalFSM.waiting_entry_photo, F.photo)
async def msg_pers_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    file_id = message.photo[-1].file_id
    add_personal_entry(
        message.from_user.id,
        data["title"],
        data["buy_price"],
        condition=data.get("condition", ""),
        photo_ref=file_id,
    )
    ret = data.get("return_to", "personal")
    await state.clear()
    kb = (
        InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📊 К финансам", callback_data="crm_finances")]])
        if ret == "finances"
        else personal_menu_kb()
    )
    await message.answer(
        f"✅ Товар с фото сохранён: <b>{h(data['title'])}</b> — {fmt_money(data['buy_price'])}",
        reply_markup=kb,
    )


@router.callback_query(F.data == "pers_sell_list")
async def cb_pers_sell_list(callback: CallbackQuery) -> None:
    if not has_perm(callback.from_user.id, "can_personal_ledger"):
        return
    items = get_personal_entries(callback.from_user.id, status="in_stock")
    if not items:
        await callback.message.edit_text(
            "У вас нет непроданных позиций.",
            reply_markup=personal_menu_kb(),
        )
        await callback.answer()
        return
    rows = [
        [InlineKeyboardButton(text=f"📦 {it['title'][:24]} ({fmt_money(it['buy_price'])})", callback_data=f"pers_sell:{it['id']}")]
        for it in items[:25]
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="personal_menu")])
    await callback.message.edit_text(
        "💰 Выберите товар, который продали:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pers_sell:"))
async def cb_pers_sell(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    entry_id = int(parts[1])
    ret = parts[2] if len(parts) > 2 else "personal"
    await state.set_state(PersonalFSM.waiting_entry_sell_price)
    await state.update_data(entry_id=entry_id, return_to=ret)
    await callback.message.edit_text("💵 Введите <b>цену продажи</b> (например: <i>14500</i>):")
    await callback.answer()


@router.message(PersonalFSM.waiting_entry_sell_price, F.text)
async def msg_pers_sell_price(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    sell = parse_amount(message.text)
    mark_personal_sold(data["entry_id"], message.from_user.id, sell)
    ret = data.get("return_to", "personal")
    await state.clear()
    kb = (
        InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📊 К финансам", callback_data="crm_finances")]])
        if ret == "finances"
        else personal_menu_kb()
    )
    await message.answer(
        f"✅ Продажа записана: <b>{fmt_money(sell)}</b>!",
        reply_markup=kb,
    )


@router.callback_query(F.data == "pers_stats")
async def cb_pers_stats(callback: CallbackQuery) -> None:
    if not has_perm(callback.from_user.id, "can_personal_ledger"):
        return
    st = get_personal_stats(callback.from_user.id)
    entries = get_personal_entries(callback.from_user.id)[:10]
    lines = [
        "📊 <b>Ваш личный учёт</b>\n",
        f"📦 В наличии: <b>{st['in_stock_count']} шт.</b> | Продано: <b>{st['sold_count']} шт.</b>",
        f"📥 Всего вложено: <b>{fmt_money(st['invested'])}</b>",
        f"💵 Выручка: <b>{fmt_money(st['revenue'])}</b>",
        f"📈 Прибыль по проданным: <b>{fmt_money(st['profit'])}</b>\n",
    ]
    if entries:
        lines.append("<b>Последние записи:</b>")
        for e in entries:
            if e["status"] == "sold":
                lines.append(f"• {h(e['title'])}: заход {fmt_money(e['buy_price'])} → продано {fmt_money(e['sell_price'] or 0)}")
            else:
                lines.append(f"• {h(e['title'])}: заход {fmt_money(e['buy_price'])} (в наличии)")
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="personal_menu")]]
        ),
    )
    await callback.answer()



# ===========================================================================
# РАЗДЕЛ 3: ПОИСК ЗАПЧАСТЕЙ И СРМ РЕМОНТ (РАЗДЕЛЬНЫЕ ВКЛАДКИ И ПРАВА)
# ===========================================================================

def parts_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔍 Найти запчасть (MM / Moba / MEMS)", callback_data="rep_search")],
            [InlineKeyboardButton(text="💰 Прайс за нашу работу", callback_data="rep_prices")],
            [InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu")],
        ]
    )


def repair_crm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Принять устройство в ремонт", callback_data="rep_new_order")],
            [InlineKeyboardButton(text="📋 Заказы в ремонте и акты", callback_data="rep_orders")],
            [InlineKeyboardButton(text="🏠 В главное меню", callback_data="main_menu")],
        ]
    )


@router.callback_query(F.data == "parts_menu")
async def cb_parts_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if not has_perm(callback.from_user.id, "can_parts_search"):
        await callback.answer("Нет доступа к поиску запчастей", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        "🔍 <b>Поиск запчастей (Москва)</b>\n"
        "Поиск по <b>Master Mobile</b>, <b>Moba</b> и <b>MEMS</b> + расчёт цены с вашей работой:",
        reply_markup=parts_menu_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "repair_crm_menu")
async def cb_repair_crm_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if not has_perm(callback.from_user.id, "can_repair_crm"):
        await callback.answer("Нет доступа к СРМ Ремонт", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        "🛠 <b>СРМ Ремонт (Qhead Mobile)</b>\n"
        "Запись клиентов, учёт ремонтов и автоматические PDF-акты приёма и выдачи:",
        reply_markup=repair_crm_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "rep_search")
async def cb_rep_search(callback: CallbackQuery, state: FSMContext) -> None:
    if not has_perm(callback.from_user.id, "can_parts_search"):
        return
    await state.set_state(RepairFSM.waiting_search_query)
    await callback.message.edit_text(
        "🔍 Напишите, что ищем (например: <i>экран 11 айфон</i> или <i>акб iphone 13</i>):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="parts_menu")]]
        ),
    )
    await callback.answer()


@router.message(RepairFSM.waiting_search_query, F.text)
async def msg_rep_search(message: Message, state: FSMContext) -> None:
    if not has_perm(message.from_user.id, "can_parts_search"):
        return
    query = message.text.strip()
    wait_msg = await message.answer("⏳ Ищу запчасти на Master Mobile, Moba и MEMS...")

    res = await search_parts_all(query)
    prices = [dict(r) for r in get_repair_prices()]
    work_title, work_price = match_work_price(query, prices)

    lines = [
        f"🔍 Результаты по запросу: <b>{h(query)}</b> (<i>{h(res['normalized_query'])}</i>)",
        f"🛠 Работа ({h(work_title)}): <b>{fmt_money(work_price)}</b>\n",
    ]
    for site_name, items in res["groups"].items():
        lines.append(f"🏬 <b>{site_name}</b>")
        if not items:
            st = res.get("statuses", {}).get(site_name, 0)
            note = f" (HTTP {st})" if st and st != 200 else ""
            lines.append(f"<i>Карточки не найдены{note} — откройте поиск напрямую по кнопке ниже.</i>")
        else:
            for it in items[:4]:
                total = it["price"] + work_price
                stock = "✅" if it["in_stock"] else "⏳"
                lines.append(
                    f"{stock} <a href=\"{h(it['url'])}\">{h(it['title'])}</a>\n"
                    f"   Качество: <b>{h(it['quality'])}</b>\n"
                    f"   <sub>Запчасть: {fmt_money(it['price'])}</sub> + Работа: {fmt_money(work_price)} → <b>Итого: {fmt_money(total)}</b>"
                )
        lines.append("")

    urls = res["search_urls"]
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Master Mobile", url=urls["master_mobile"]),
                InlineKeyboardButton(text="Moba", url=urls["moba"]),
                InlineKeyboardButton(text="MEMS", url=urls["mems"]),
            ],
            [InlineKeyboardButton(text="🔍 Новый поиск", callback_data="rep_search")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="parts_menu")],
        ]
    )
    await state.clear()
    await wait_msg.edit_text("\n".join(lines), reply_markup=kb, disable_web_page_preview=True)


@router.callback_query(F.data == "rep_prices")
async def cb_rep_prices(callback: CallbackQuery) -> None:
    if not has_perm(callback.from_user.id, "can_parts_search"):
        return
    prices = get_repair_prices()
    rows = [
        [InlineKeyboardButton(text=f"✏️ {r['title']} — {fmt_money(r['work_price'])}", callback_data=f"rep_edit_p:{r['id']}")]
        for r in prices
    ]
    rows.append([InlineKeyboardButton(text="➕ Добавить позицию прайса", callback_data="rep_add_p")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="parts_menu")])
    await callback.message.edit_text(
        "💰 <b>Прайс за нашу работу</b>\nНажмите на строку, чтобы изменить цену:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("rep_edit_p:"))
async def cb_rep_edit_p(callback: CallbackQuery, state: FSMContext) -> None:
    if not has_perm(callback.from_user.id, "can_parts_search"):
        return
    pid = int(callback.data.split(":")[1])
    await state.set_state(RepairFSM.waiting_edit_price_amount)
    await state.update_data(price_id=pid)
    await callback.message.edit_text(
        "💰 Введите новую <b>цену работы</b> (₽):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🗑 Удалить позицию", callback_data=f"rep_del_p:{pid}")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="rep_prices")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("rep_del_p:"))
async def cb_rep_del_p(callback: CallbackQuery, state: FSMContext) -> None:
    if not has_perm(callback.from_user.id, "can_parts_search"):
        return
    pid = int(callback.data.split(":")[1])
    delete_repair_price(pid)
    await state.clear()
    await cb_rep_prices(callback)


@router.message(RepairFSM.waiting_edit_price_amount, F.text)
async def msg_rep_edit_p(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    val = parse_amount(message.text)
    update_repair_price(data["price_id"], val)
    await state.clear()
    await message.answer(
        f"✅ Цена работы обновлена: <b>{fmt_money(val)}</b>",
        reply_markup=parts_menu_kb(),
    )


@router.callback_query(F.data == "rep_add_p")
async def cb_rep_add_p(callback: CallbackQuery, state: FSMContext) -> None:
    if not has_perm(callback.from_user.id, "can_parts_search"):
        return
    await state.set_state(RepairFSM.waiting_new_price_title)
    await callback.message.edit_text("📝 Название услуги (например: <i>Замена экрана iPhone 14 Pro</i>):")
    await callback.answer()


@router.message(RepairFSM.waiting_new_price_title, F.text)
async def msg_rep_new_p_title(message: Message, state: FSMContext) -> None:
    await state.update_data(title=message.text.strip())
    await state.set_state(RepairFSM.waiting_new_price_keywords)
    await message.answer("🏷 Ключевые слова через запятую (например: <i>экран,дисплей,14 pro</i>):")


@router.message(RepairFSM.waiting_new_price_keywords, F.text)
async def msg_rep_new_p_kw(message: Message, state: FSMContext) -> None:
    await state.update_data(keywords=message.text.strip())
    await state.set_state(RepairFSM.waiting_new_price_amount)
    await message.answer("💰 Укажите цену за работу (₽):")


@router.message(RepairFSM.waiting_new_price_amount, F.text)
async def msg_rep_new_p_amt(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    val = parse_amount(message.text)
    add_repair_price(data["title"], data["keywords"], val)
    await state.clear()
    await message.answer(
        f"✅ Добавлено в прайс: <b>{h(data['title'])}</b> — {fmt_money(val)}",
        reply_markup=parts_menu_kb(),
    )


# --- СРМ Ремонт: заказы и PDF-акты ---

@router.callback_query(F.data == "rep_orders")
async def cb_rep_orders(callback: CallbackQuery) -> None:
    if not has_perm(callback.from_user.id, "can_repair_crm"):
        return
    orders = get_repair_orders()
    lines = ["📋 <b>Заказы СРМ Ремонт</b>\n"]
    rows = [[InlineKeyboardButton(text="➕ Принять устройство", callback_data="rep_new_order")]]
    if not orders:
        lines.append("<i>Заказов пока нет.</i>")
    else:
        for o in orders[:10]:
            st = "✅ Выдан" if o["status"] == "done" else "🛠 В работе"
            num = str(o["id"]).zfill(4)
            lines.append(f"• <b>№{num} {h(o['device_model'])}</b> ({h(o['client_name'] or o['client_info'])}) — {fmt_money(o['total_price'])} [{st}]")
            if o["status"] == "in_work":
                rows.append([InlineKeyboardButton(text=f"✅ Закрыть №{num} + Акт работ", callback_data=f"rep_done:{o['id']}")])
            else:
                rows.append([InlineKeyboardButton(text=f"📄 Акт работ №{num}", callback_data=f"rep_pdf_done:{o['id']}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="repair_crm_menu")])
    await callback.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data == "rep_new_order")
async def cb_rep_new_order(callback: CallbackQuery, state: FSMContext) -> None:
    if not has_perm(callback.from_user.id, "can_repair_crm"):
        return
    await state.set_state(RepairFSM.waiting_order_client_name)
    await callback.message.edit_text(
        "👤 Введите <b>Имя клиента</b>:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="repair_crm_menu")]]
        ),
    )
    await callback.answer()


@router.message(RepairFSM.waiting_order_client_name, F.text)
async def msg_rep_cname(message: Message, state: FSMContext) -> None:
    await state.update_data(client_name=message.text.strip())
    await state.set_state(RepairFSM.waiting_order_client_phone)
    await message.answer("📞 Введите <b>номер телефона клиента</b>:")


@router.message(RepairFSM.waiting_order_client_phone, F.text)
async def msg_rep_cphone(message: Message, state: FSMContext) -> None:
    await state.update_data(client_phone=message.text.strip())
    await state.set_state(RepairFSM.waiting_order_device)
    await message.answer("📱 Устройство / Модель (например: <i>Apple iPhone 13 128GB</i>):")


@router.message(RepairFSM.waiting_order_device, F.text)
async def msg_rep_odev(message: Message, state: FSMContext) -> None:
    raw = message.text.strip()
    matches = search_devices(raw, limit=6)
    # Если точное совпадение одно или пользователь ввёл подробное название — сразу идём дальше
    if not matches or (len(matches) == 1 and matches[0].lower() == raw.lower()) or len(raw) > 22:
        await state.update_data(device_model=raw)
        await state.set_state(RepairFSM.waiting_order_imei)
        await message.answer("🔢 IMEI или серийный номер (или <code>-</code>, чтобы пропустить):")
        return
    await state.update_data(device_candidates=matches, raw_device=raw)
    rows = [[InlineKeyboardButton(text=m, callback_data=f"rep_pick_dev:{idx}")] for idx, m in enumerate(matches)]
    rows.append([InlineKeyboardButton(text=f"Оставить: {raw[:24]}", callback_data="rep_pick_dev:raw")])
    await message.answer(
        "📱 Выберите точную модель из справочника (или оставьте как написали):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(RepairFSM.waiting_order_device, F.data.startswith("rep_pick_dev:"))
async def cb_rep_pick_dev(callback: CallbackQuery, state: FSMContext) -> None:
    choice = callback.data.split(":")[1]
    data = await state.get_data()
    if choice == "raw":
        model = data.get("raw_device", "")
    else:
        cands = data.get("device_candidates", [])
        idx = int(choice)
        model = cands[idx] if 0 <= idx < len(cands) else data.get("raw_device", "")
    await state.update_data(device_model=model)
    await state.set_state(RepairFSM.waiting_order_imei)
    await callback.message.edit_text(f"📱 Выбрано: <b>{h(model)}</b>\n\n🔢 IMEI или серийный номер (или <code>-</code>, чтобы пропустить):")
    await callback.answer()


@router.message(RepairFSM.waiting_order_imei, F.text)
async def msg_rep_oimei(message: Message, state: FSMContext) -> None:
    val = "" if message.text.strip() == "-" else message.text.strip()
    await state.update_data(imei_sn=val)
    await state.set_state(RepairFSM.waiting_order_issue)
    await message.answer("🛠 Неисправность / что нужно сделать:")


@router.message(RepairFSM.waiting_order_issue, F.text)
async def msg_rep_oissue(message: Message, state: FSMContext) -> None:
    await state.update_data(issue_desc=message.text.strip())
    await state.set_state(RepairFSM.waiting_order_appearance)
    await message.answer("👀 Внешний вид (например: <i>Потёртости, сколы</i> или <code>-</code>):")


@router.message(RepairFSM.waiting_order_appearance, F.text)
async def msg_rep_oapp(message: Message, state: FSMContext) -> None:
    val = "Б/у, следы эксплуатации" if message.text.strip() == "-" else message.text.strip()
    await state.update_data(appearance=val)
    await state.set_state(RepairFSM.waiting_order_completeness)
    await message.answer("📦 Комплектация (например: <i>Только устройство</i> или <code>-</code>):")


@router.message(RepairFSM.waiting_order_completeness, F.text)
async def msg_rep_ocomp(message: Message, state: FSMContext) -> None:
    val = "Только устройство" if message.text.strip() == "-" else message.text.strip()
    await state.update_data(completeness=val)
    await state.set_state(RepairFSM.waiting_order_passcode)
    await message.answer("🔐 Пароль от устройства / примечание (или <code>-</code>):")


@router.message(RepairFSM.waiting_order_passcode, F.text)
async def msg_rep_opass(message: Message, state: FSMContext) -> None:
    val = "—" if message.text.strip() == "-" else message.text.strip()
    await state.update_data(passcode_note=val)
    await state.set_state(RepairFSM.waiting_order_part_cost)
    await message.answer("🔩 Себестоимость запчасти (₽, или <code>0</code>):")


@router.message(RepairFSM.waiting_order_part_cost, F.text)
async def msg_rep_opcost(message: Message, state: FSMContext) -> None:
    await state.update_data(part_cost=parse_amount(message.text))
    await state.set_state(RepairFSM.waiting_order_prepay)
    await message.answer("💳 Предоплата от клиента (₽, или <code>0</code>):")


@router.message(RepairFSM.waiting_order_prepay, F.text)
async def msg_rep_oprep(message: Message, state: FSMContext) -> None:
    await state.update_data(prepayment=parse_amount(message.text))
    await state.set_state(RepairFSM.waiting_order_total)
    await message.answer("💵 Ориентировочная итоговая цена для клиента (₽):")


@router.message(RepairFSM.waiting_order_total, F.text)
async def msg_rep_otot(message: Message, state: FSMContext) -> None:
    await state.update_data(total_price=parse_amount(message.text))
    await state.set_state(RepairFSM.waiting_order_warranty)
    await message.answer(
        "🛡 Укажите <b>срок гарантии в днях</b> (например: <code>30</code>, <code>90</code> или <code>0</code>):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="30 дней (стандарт)", callback_data="rep_warr:30")],
                [InlineKeyboardButton(text="90 дней", callback_data="rep_warr:90")],
                [InlineKeyboardButton(text="Без гарантии (0)", callback_data="rep_warr:0")],
            ]
        ),
    )


async def _finalize_repair_order(target, state: FSMContext, warranty_days: int) -> None:
    data = await state.get_data()
    oid = add_repair_order(
        device_model=data["device_model"],
        service_name=data["issue_desc"],
        client_info=data["client_name"],
        part_cost=data["part_cost"],
        total_price=data["total_price"],
        client_name=data["client_name"],
        client_phone=data["client_phone"],
        imei_sn=data["imei_sn"],
        issue_desc=data["issue_desc"],
        appearance=data["appearance"],
        completeness=data["completeness"],
        passcode_note=data["passcode_note"],
        prepayment=data["prepayment"],
        warranty_days=warranty_days,
    )
    await state.clear()
    order = dict(get_repair_order_by_id(oid))
    pdf_bytes = generate_act_pdf(order, "intake")
    num = str(oid).zfill(4)
    msg_obj = target.message if isinstance(target, CallbackQuery) else target
    await msg_obj.answer_document(
        BufferedInputFile(pdf_bytes, filename=f"Akt_Priema_{num}.pdf"),
        caption=f"✅ Заказ <b>№{num}</b> принят в работу (гарантия: {warranty_days} дн.)!\nАкт приёма сформирован.",
        reply_markup=repair_crm_kb(),
    )


@router.callback_query(RepairFSM.waiting_order_warranty, F.data.startswith("rep_warr:"))
async def cb_rep_warr(callback: CallbackQuery, state: FSMContext) -> None:
    days = int(callback.data.split(":")[1])
    await _finalize_repair_order(callback, state, days)
    await callback.answer()


@router.message(RepairFSM.waiting_order_warranty, F.text)
async def msg_rep_warr(message: Message, state: FSMContext) -> None:
    days = int(parse_amount(message.text))
    await _finalize_repair_order(message, state, max(0, days))


@router.callback_query(F.data.startswith("rep_done:"))
async def cb_rep_done(callback: CallbackQuery) -> None:
    if not has_perm(callback.from_user.id, "can_repair_crm"):
        return
    oid = int(callback.data.split(":")[1])
    mark_repair_done(oid)
    order = dict(get_repair_order_by_id(oid))
    pdf_bytes = generate_act_pdf(order, "done")
    num = str(oid).zfill(4)
    await callback.message.answer_document(
        BufferedInputFile(pdf_bytes, filename=f"Akt_Vypolnennyh_Rabot_{num}.pdf"),
        caption=f"✅ Ремонт <b>№{num}</b> закрыт! Вот акт выполненных работ.",
        reply_markup=repair_crm_kb(),
    )
    await callback.answer("Готово!")


@router.callback_query(F.data.startswith("rep_pdf_done:"))
async def cb_rep_pdf_done(callback: CallbackQuery) -> None:
    if not has_perm(callback.from_user.id, "can_repair_crm"):
        return
    oid = int(callback.data.split(":")[1])
    order = dict(get_repair_order_by_id(oid))
    pdf_bytes = generate_act_pdf(order, "done")
    num = str(oid).zfill(4)
    await callback.message.answer_document(
        BufferedInputFile(pdf_bytes, filename=f"Akt_Vypolnennyh_Rabot_{num}.pdf"),
    )
    await callback.answer()


# ===========================================================================
# РАЗДЕЛ 2: АВТОПОСТИНГ И РЕДАКТИРОВАНИЕ ПОСТОВ
# ===========================================================================

@router.callback_query(F.data == "posts_menu")
async def cb_posts_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if not has_perm(callback.from_user.id, "can_post"):
        await callback.answer("Нет доступа к постингу", show_alert=True)
        return
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


@router.callback_query(F.data == "post_create")
async def cb_post_create(callback: CallbackQuery, state: FSMContext) -> None:
    if not has_perm(callback.from_user.id, "can_post"):
        return
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
    await state.update_data(photos=[], linked_item_id=None)
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

    rows = [
        [InlineKeyboardButton(text="✅ Добавить КУПИТЬ✅ и выложить", callback_data="post_publish")]
    ]
    if is_owner(message.from_user.id):
        rows.append([InlineKeyboardButton(text="🔗 Привязать к товару из CRM", callback_data="post_link_crm")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="posts_menu")])

    await message.answer(
        "✅ Описание сохранено!\n\n"
        "<b>Шаг 3 из 3</b> — можете сразу выложить с кнопкой <b>КУПИТЬ✅</b> или сначала привязать товар из CRM (чтобы при продаже посчиталась прибыль):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(PostFSM.waiting_confirm_publish, F.data == "post_link_crm")
async def cb_post_link_crm(callback: CallbackQuery) -> None:
    if not is_owner(callback.from_user.id):
        return
    items = get_unsold_my_items()
    if not items:
        await callback.answer("Нет непроданных товаров «Мой» в CRM", show_alert=True)
        return
    rows = [
        [
            InlineKeyboardButton(
                text=f"📦 {it['name'][:22]} ({it['shipment_name'][:12]})",
                callback_data=f"post_set_crm:{it['id']}",
            )
        ]
        for it in items
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="post_back_publish")])
    await callback.message.edit_text(
        "🔗 Выберите товар из CRM, к которому привязать этот пост:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(PostFSM.waiting_confirm_publish, F.data.startswith("post_set_crm:"))
async def cb_post_set_crm(callback: CallbackQuery, state: FSMContext) -> None:
    item_id = int(callback.data.split(":")[1])
    item = get_item(item_id)
    await state.update_data(linked_item_id=item_id)
    await callback.message.edit_text(
        f"🔗 Пост привязан к товару: <b>{h(item['name'])}</b> (закуп: {h(item['price'] or '0')}).\n\n"
        f"Нажмите кнопку ниже, чтобы выложить:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Добавить КУПИТЬ✅ и выложить", callback_data="post_publish")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="posts_menu")],
            ]
        ),
    )
    await callback.answer("Привязано!")


@router.callback_query(PostFSM.waiting_confirm_publish, F.data == "post_back_publish")
async def cb_post_back_publish(callback: CallbackQuery) -> None:
    rows = [
        [InlineKeyboardButton(text="✅ Добавить КУПИТЬ✅ и выложить", callback_data="post_publish")],
        [InlineKeyboardButton(text="🔗 Привязать к товару из CRM", callback_data="post_link_crm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="posts_menu")],
    ]
    await callback.message.edit_text(
        "Нажмите кнопку ниже, чтобы опубликовать пост:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


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
    linked_item_id = data.get("linked_item_id")

    try:
        if len(photos) == 1:
            await bot.send_photo(chat_id=channel_id, photo=photos[0])
        elif len(photos) > 1:
            media = [InputMediaPhoto(media=fid) for fid in photos[:10]]
            await bot.send_media_group(chat_id=channel_id, media=media)

        sent_msg = await bot.send_message(
            chat_id=channel_id,
            text=post_text,
            entities=entities or None,
            parse_mode=None,
            reply_markup=buy_button_kb(),
            disable_web_page_preview=True,
        )
        save_post(
            channel_id,
            sent_msg.message_id,
            post_text,
            data.get("post_entities_json", "[]"),
            linked_item_id=linked_item_id,
        )
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


# --- Редактирование поста (ПРОДАНО❌ + фиксация продажи в CRM) ---

@router.callback_query(F.data == "post_edit_sold")
async def cb_post_edit_sold(callback: CallbackQuery, state: FSMContext) -> None:
    if not has_perm(callback.from_user.id, "can_post"):
        return
    await state.clear()
    await state.set_state(PostFSM.waiting_edit_post)
    await callback.message.edit_text(
        "❌ <b>Отметить товар как ПРОДАНО</b>\n\n"
        "<b>Перешлите сюда текстовое сообщение</b> с описанием товара прямо из вашего канала "
        "(или вставьте ссылку на пост из канала).",
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

    if message.forward_from_chat and message.forward_from_message_id:
        target_channel = str(message.forward_from_chat.id)
        target_msg_id = message.forward_from_message_id
        src_text = message.text or message.caption
        src_entities = message.entities or message.caption_entities or []
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
            saved_lookup = get_post(target_channel, target_msg_id)
            if saved_lookup:
                src_text = saved_lookup["text"]
                src_entities = deserialize_entities(saved_lookup["entities_json"])

    if not target_channel or not target_msg_id:
        await message.answer(
            "⚠️ Не удалось определить пост. Перешлите сюда именно <b>текстовый пост</b> из канала (не фото!)."
        )
        return

    saved_post = get_post(target_channel, target_msg_id)
    linked_item_id = saved_post["linked_item_id"] if saved_post else None

    if not src_text:
        try:
            await bot.edit_message_reply_markup(
                chat_id=target_channel,
                message_id=target_msg_id,
                reply_markup=sold_button_kb(),
            )
            await state.clear()
            await message.answer(
                "✅ Кнопка изменена на <b>ПРОДАНО❌</b>.",
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
        try:
            await bot.edit_message_reply_markup(
                chat_id=target_channel,
                message_id=target_msg_id,
                reply_markup=sold_button_kb(),
            )
        except TelegramBadRequest as e2:
            await message.answer(f"❌ Ошибка редактирования поста: <code>{h(str(e2))}</code>")
            return

    if linked_item_id and is_owner(message.from_user.id):
        linked_item = get_item(linked_item_id)
        await state.set_state(PostFSM.waiting_post_sold_price)
        await state.update_data(linked_item_id=linked_item_id)
        await message.answer(
            f"✅ Пост помечен как <b>ПРОДАНО❌</b>!\n\n"
            f"🔗 К посту привязан товар из CRM: <b>{h(linked_item['name'])}</b>.\n"
            f"💰 Напишите, <b>за сколько он продан</b> (чтобы посчитать прибыль), или нажмите «Пропустить»:",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⏭ Пропустить", callback_data="posts_menu")]]
            ),
        )
        return

    await state.clear()
    await message.answer(
        "✅ Пост обновлён: <code>#НАЛИЧИЕ</code> убрано, текст зачёркнут, премиум-стикеры сохранены, кнопка изменена на <b>ПРОДАНО❌</b>!",
        reply_markup=posts_menu_kb(message.from_user.id),
    )


@router.message(PostFSM.waiting_post_sold_price, F.text)
async def msg_post_sold_price(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    item_id = data["linked_item_id"]
    mark_item_sold(item_id, message.text)
    item = get_item(item_id)
    await state.clear()

    buy = parse_amount(item["price"])
    sell = parse_amount(item["sold_price"])
    profit = sell - buy
    await message.answer(
        f"💰 Продажа зафиксирована в CRM!\n"
        f"📦 <b>{h(item['name'])}</b>\n"
        f"Закуп: {fmt_money(buy)} → Продано: {fmt_money(sell)}\n"
        f"📈 Прибыль: <b>{fmt_money(profit)}</b>",
        reply_markup=posts_menu_kb(message.from_user.id),
    )


# ===========================================================================
# УПРАВЛЕНИЕ АДМИНАМИ И ИХ ПРАВАМИ (ТОЛЬКО ДЛЯ ГЛАВНОГО АДМИНА)
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
        "Нажмите на админа, чтобы настроить его права (CRM, посты или личный учёт).\n"
        "<i>Ваши цены закупки, доставка и финансы скрыты от других админов.</i>"
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
        f"✅ Админ <b>{h(message.text.strip())}</b> добавлен! Нажмите на него в списке, если хотите изменить его права.",
        reply_markup=admins_menu_kb(),
    )


@router.callback_query(F.data.startswith("cfg_admin:"))
async def cb_cfg_admin(callback: CallbackQuery) -> None:
    if not is_owner(callback.from_user.id):
        return
    admin_row_id = int(callback.data.split(":")[1])
    adm = get_admin_by_id(admin_row_id)
    if not adm:
        await callback.answer("Админ не найден", show_alert=True)
        return
    await callback.message.edit_text(
        f"⚙️ Права для <b>{h(adm['label'])}</b>:\nНажимайте на кнопки, чтобы включить или выключить доступ:",
        reply_markup=admin_config_kb(adm),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tperm:"))
async def cb_toggle_perm(callback: CallbackQuery) -> None:
    if not is_owner(callback.from_user.id):
        return
    _, admin_id_str, perm = callback.data.split(":")
    admin_row_id = int(admin_id_str)
    toggle_admin_perm(admin_row_id, perm)
    adm = get_admin_by_id(admin_row_id)
    if not adm:
        await callback.answer("Админ не найден", show_alert=True)
        return
    await callback.message.edit_reply_markup(reply_markup=admin_config_kb(adm))
    await callback.answer("Права обновлены")


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
    if not has_perm(callback.from_user.id, "can_crm"):
        return
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
        show_fin = is_owner(message.from_user.id)
        await message.answer(
            f"✅ Название изменено!\n\n{format_shipment_summary(shipment, show_finances=show_fin)}\n\nВыберите, что изменить:",
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
    show_fin = is_owner(message.from_user.id)
    if return_to == "edit":
        await message.answer(
            f"✅ Данные обновлены!\n\n{format_shipment_summary(shipment, show_finances=show_fin)}",
            reply_markup=edit_shipment_kb(shipment_id),
        )
    else:
        price_line = f"👤 Мой • 💰 {h(item['price'])}\n\n" if show_fin else "👤 Мой\n\n"
        await message.answer(
            f"✅ Товар сохранён!\n"
            f"📦 <b>{h(item['name'])}</b> — <code>{h(item['track_number'])}</code>\n"
            f"{price_line}"
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
    show_fin = is_owner(message.from_user.id)
    if return_to == "edit":
        await message.answer(
            f"✅ Данные обновлены!\n\n{format_shipment_summary(shipment, show_finances=show_fin)}",
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
            reply_markup=crm_menu_kb(callback.from_user.id),
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
    show_fin = is_owner(callback.from_user.id)
    await callback.message.edit_text(
        f"{format_shipment_summary(shipment, show_finances=show_fin)}\n\nВыберите, что хотите изменить:",
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
    show_fin = is_owner(callback.from_user.id)
    await callback.message.edit_text(
        f"📦 Товар: <b>{h(item['name'])}</b>\n"
        f"🔢 Трек-номер: {track}\n"
        f"👤 Принадлежность: {format_owner_tag(item, show_finances=show_fin)}\n\n"
        f"Что изменить?",
        reply_markup=edit_item_kb(int(shipment_id), int(item_id), callback.from_user.id, item),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sell_crm_item:"))
async def cb_sell_crm_item(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_owner(callback.from_user.id):
        return
    _, shipment_id, item_id = callback.data.split(":")
    await state.set_state(ShipmentFSM.waiting_item_sold_price)
    await state.update_data(shipment_id=int(shipment_id), item_id=int(item_id))
    await callback.message.edit_text("💰 Введите <b>цену продажи</b> товара (например: <i>14990</i>):")
    await callback.answer()


@router.message(ShipmentFSM.waiting_item_sold_price, F.text)
async def msg_item_sold_price(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    shipment_id = data["shipment_id"]
    item_id = data["item_id"]
    mark_item_sold(item_id, message.text)
    await state.clear()
    shipment = get_shipment(shipment_id)
    await message.answer(
        f"💰 Продажа сохранена!\n\n{format_shipment_summary(shipment, show_finances=True)}",
        reply_markup=edit_shipment_kb(shipment_id),
    )


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
    show_fin = is_owner(message.from_user.id)
    await message.answer(
        f"✅ Название товара обновлено!\n\n{format_shipment_summary(shipment, show_finances=show_fin)}",
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
    show_fin = is_owner(message.from_user.id)
    await message.answer(
        f"✅ Трек-номер обновлён!\n\n{format_shipment_summary(shipment, show_finances=show_fin)}",
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
    show_fin = is_owner(callback.from_user.id)
    await callback.message.edit_text(
        f"🗑 Товар удалён.\n\n{format_shipment_summary(shipment, show_finances=show_fin)}",
        reply_markup=edit_shipment_kb(int(shipment_id)),
    )
    await callback.answer("Товар удалён")


@router.callback_query(F.data == "finish_list")
async def cb_finish_list(callback: CallbackQuery) -> None:
    shipments = get_shipments("active")
    if not shipments:
        await callback.message.edit_text(
            "Активных отправок пока нет.",
            reply_markup=crm_menu_kb(callback.from_user.id),
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


# --- Груз приехал (запрос цены доставки -> перенос в архив) ---

@router.callback_query(F.data == "arrive_list")
async def cb_arrive_list(callback: CallbackQuery) -> None:
    shipments = get_shipments("active")
    if not shipments:
        await callback.message.edit_text(
            "Активных отправок сейчас нет.",
            reply_markup=crm_menu_kb(callback.from_user.id),
        )
        await callback.answer()
        return
    await callback.message.edit_text(
        "🚚 Выберите груз, который приехал:",
        reply_markup=shipments_pick_kb(shipments, "arrive_ship"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("arrive_ship:"))
async def cb_arrive_ship(callback: CallbackQuery, state: FSMContext) -> None:
    shipment_id = int(callback.data.split(":")[1])
    shipment = get_shipment(shipment_id)
    if not shipment:
        await callback.answer("Отправка не найдена", show_alert=True)
        return

    # Если не главный админ — просто архивируем без запроса цены доставки
    if not is_owner(callback.from_user.id):
        archive_shipment(shipment_id, 0.0)
        await callback.message.edit_text(
            f"📥 Груз <b>{h(shipment['name'])}</b> отмечен как прибывший и перенесён в архив.",
            reply_markup=crm_menu_kb(callback.from_user.id),
        )
        await callback.answer("Перенесено в архив")
        return

    await state.set_state(ShipmentFSM.waiting_delivery_cost)
    await state.update_data(shipment_id=shipment_id)
    await callback.message.edit_text(
        f"🚚 Груз <b>{h(shipment['name'])}</b>\n\n"
        f"💰 <b>Какая была цена доставки?</b> (введите сумму, например <i>3500</i>, или нажмите «Без цены»):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⏭ Без цены (0 ₽)", callback_data=f"arrive_skip_cost:{shipment_id}")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="crm_menu")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(ShipmentFSM.waiting_delivery_cost, F.data.startswith("arrive_skip_cost:"))
async def cb_arrive_skip_cost(callback: CallbackQuery, state: FSMContext) -> None:
    shipment_id = int(callback.data.split(":")[1])
    shipment = get_shipment(shipment_id)
    archive_shipment(shipment_id, 0.0)
    await state.clear()
    await callback.message.edit_text(
        f"📥 Груз <b>{h(shipment['name'])}</b> перенесён в архив (доставка: 0 ₽).",
        reply_markup=crm_menu_kb(callback.from_user.id),
    )
    await callback.answer("Перенесено в архив")


@router.message(ShipmentFSM.waiting_delivery_cost, F.text)
async def msg_delivery_cost(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    shipment_id = data["shipment_id"]
    cost = parse_amount(message.text)
    shipment = get_shipment(shipment_id)
    archive_shipment(shipment_id, cost)
    await state.clear()
    await message.answer(
        f"📥 Груз <b>{h(shipment['name'])}</b> перенесён в архив.\n"
        f"🚚 Цена доставки учтена в CRM: <b>{fmt_money(cost)}</b>",
        reply_markup=crm_menu_kb(message.from_user.id),
    )


@router.callback_query(F.data == "archive_list")
async def cb_archive_list(callback: CallbackQuery) -> None:
    archived = get_shipments("archived")
    if not archived:
        await callback.message.edit_text(
            "Архив пуст.",
            reply_markup=crm_menu_kb(callback.from_user.id),
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
    show_fin = is_owner(callback.from_user.id)
    await callback.message.edit_text(
        f"🗄 <i>(В архиве)</i>\n{format_shipment_summary(shipment, show_finances=show_fin)}",
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

    from web_app import start_web_server

    web_deps = {
        "get_crm_finances": get_crm_finances,
        "get_owner_hash": get_owner_hash,
        "find_admin_by_web_password": find_admin_by_web_password,
        "get_admin_by_hash": get_admin_by_hash,
        "get_setting": get_setting,
        "get_repair_order_by_id": get_repair_order_by_id,
        "generate_act_pdf": generate_act_pdf,
        "get_repair_prices": get_repair_prices,
        "add_repair_price": add_repair_price,
        "update_repair_price": update_repair_price,
        "delete_repair_price": delete_repair_price,
        "get_repair_orders": get_repair_orders,
        "add_repair_order": add_repair_order,
        "mark_repair_done": mark_repair_done,
        "delete_repair_order": delete_repair_order,
        "search_parts_all": search_parts_all,
        "match_work_price": match_work_price,
        "get_personal_stats_by_hash": get_personal_stats_by_hash,
        "get_personal_entries_by_hash": get_personal_entries_by_hash,
        "add_personal_entry_by_hash": add_personal_entry_by_hash,
        "mark_personal_sold_by_hash": mark_personal_sold_by_hash,
        "delete_personal_entry_by_hash": delete_personal_entry_by_hash,
        "get_shipments": get_shipments,
        "get_items": get_items,
        "get_setting": get_setting,
        "save_post": save_post,
        "get_all_posts": get_all_posts,
        "get_post_by_id": get_post_by_id,
        "create_shipment": create_shipment,
        "archive_shipment": archive_shipment,
        "create_item": create_item,
        "update_item_my_price": update_item_my_price,
        "update_item_client": update_item_client,
        "update_item_name": update_item_name,
        "update_item_track": update_item_track,
        "delete_item": delete_item,
        "mark_item_sold": mark_item_sold,
        "mark_post_sold": mark_post_sold,
        "parse_amount": parse_amount,
        "serialize_entities": serialize_entities,
        "deserialize_entities": deserialize_entities,
        "build_sold_text_and_entities": build_sold_text_and_entities,
    }
    runner = await start_web_server(bot, web_deps)
    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())