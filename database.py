"""
Единая SQL (SQLite) база данных для кофейни.
Используется buyer_bot, barista_bot и admin_bot.
"""
import aiosqlite

try:
    import config
    # Используем существующие переменные из config или задаём значение по умолчанию
    if hasattr(config, 'DB_PATH'):
        DB_PATH = config.DB_PATH
    else:
        # Если DB_PATH нет в config, используем стандартный путь
        DB_PATH = "coffee_menu.db"
except ImportError:
    import os
    DB_PATH = os.environ.get("DB_PATH", "coffee_menu.db")


def parse_price(price_str: str) -> int:
    """Из строки вида '100р' или '100' извлекает число."""
    return int("".join(filter(str.isdigit, str(price_str))))


async def init_db():
    """Создаёт все таблицы, если их нет."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS menu (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                name TEXT NOT NULL,
                volume TEXT,
                price TEXT,
                on_stop_list INTEGER DEFAULT 0
            )
        """)
        # Добавляем колонку стоп-листа для существующих БД
        try:
            await db.execute("ALTER TABLE menu ADD COLUMN on_stop_list INTEGER DEFAULT 0")
        except Exception:
            pass
        await db.execute("""
            CREATE TABLE IF NOT EXISTS cart (
                user_id INTEGER,
                item TEXT,
                price INTEGER,
                comment TEXT
            )
        """)
        try:
            await db.execute("ALTER TABLE cart ADD COLUMN comment TEXT")
        except Exception:
            pass
        await db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                items TEXT,
                total INTEGER,
                status TEXT DEFAULT 'ожидает'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS barista_break (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                break_until REAL,
                notify_back INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS buyer_users (
                user_id INTEGER PRIMARY KEY
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                text TEXT NOT NULL,
                created_at REAL
            )
        """)
        await db.commit()


# ---------- Меню (для покупателя и админа) ----------

async def get_menu():
    """Возвращает меню для покупателей: только позиции НЕ в стоп-листе."""
    menu = {}
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT category, name, volume, price FROM menu
               WHERE COALESCE(on_stop_list, 0) = 0
               ORDER BY category, name, volume"""
        ) as cur:
            rows = await cur.fetchall()
            for category, name, volume, price in rows:
                menu.setdefault(category, []).append({
                    "name": name,
                    "volume": volume or "",
                    "price": price or "0",
                    "price_int": parse_price(price or "0"),
                })
    return menu


async def get_menu_with_ids():
    """Возвращает список (id, category, name, volume, price, on_stop_list) для админки."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT id, category, name, volume, price, COALESCE(on_stop_list, 0)
               FROM menu ORDER BY category, name, volume"""
        ) as cur:
            return await cur.fetchall()


async def add_menu_item(category: str, name: str, volume: str, price: str, on_stop_list: int = 0):
    """Добавляет позицию в меню."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO menu (category, name, volume, price, on_stop_list) VALUES (?, ?, ?, ?, ?)",
            (category, name, volume, price, on_stop_list),
        )
        await db.commit()


async def update_menu_item(item_id: int, category: str, name: str, volume: str, price: str, on_stop_list: int | None = None):
    """Обновляет позицию меню по id. on_stop_list=None — не менять."""
    async with aiosqlite.connect(DB_PATH) as db:
        if on_stop_list is not None:
            await db.execute(
                "UPDATE menu SET category=?, name=?, volume=?, price=?, on_stop_list=? WHERE id=?",
                (category, name, volume, price, on_stop_list, item_id),
            )
        else:
            await db.execute(
                "UPDATE menu SET category=?, name=?, volume=?, price=? WHERE id=?",
                (category, name, volume, price, item_id),
            )
        await db.commit()


async def delete_menu_item(item_id: int):
    """Удаляет позицию из меню по id."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM menu WHERE id=?", (item_id,))
        await db.commit()


async def get_categories():
    """Список уникальных категорий (из меню)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT DISTINCT category FROM menu ORDER BY category"
        ) as cur:
            rows = await cur.fetchall()
            return [r[0] for r in rows]


async def get_menu_items_by_category(category: str):
    """Напитки выбранной категории: (id, name, volume, price, on_stop_list)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT id, name, volume, price, COALESCE(on_stop_list, 0)
               FROM menu WHERE category = ? ORDER BY name, volume""",
            (category,),
        ) as cur:
            return await cur.fetchall()


async def rename_category(old_name: str, new_name: str):
    """Переименовать категорию."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE menu SET category = ? WHERE category = ?", (new_name, old_name))
        await db.commit()


async def delete_category(category: str):
    """Удалить категорию и все напитки в ней."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM menu WHERE category = ?", (category,))
        await db.commit()


async def set_menu_item_stop_list(item_id: int, on_stop_list: bool):
    """Включить/выключить позицию в стоп-листе."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE menu SET on_stop_list = ? WHERE id = ?",
            (1 if on_stop_list else 0, item_id),
        )
        await db.commit()


async def set_stop_list_by_name(category: str, name: str, on_stop_list: bool):
    """Включить/выключить в стоп-листе все размеры напитка по имени (категория + название)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE menu SET on_stop_list = ? WHERE category = ? AND name = ?",
            (1 if on_stop_list else 0, category, name),
        )
        await db.commit()


async def get_unique_drink_names():
    """Список уникальных (category, name) для стоп-листа по названию напитка."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT DISTINCT category, name FROM menu ORDER BY category, name"
        ) as cur:
            return await cur.fetchall()


async def is_drink_name_on_stop_list(category: str, name: str) -> bool:
    """Есть ли хотя бы один размер напитка в стоп-листе."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM menu WHERE category = ? AND name = ? AND COALESCE(on_stop_list, 0) = 1 LIMIT 1",
            (category, name),
        ) as cur:
            row = await cur.fetchone()
            return row is not None


# ---------- Корзина ----------

async def add_to_cart(user_id: int, item: str, price: int, comment: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO cart (user_id, item, price, comment) VALUES (?, ?, ?, ?)",
            (user_id, item, price, comment or ""),
        )
        await db.commit()


async def get_cart(user_id: int):
    """Возвращает список (item, price, comment)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT item, price, COALESCE(comment, '') FROM cart WHERE user_id=?", (user_id,)
        ) as cur:
            return await cur.fetchall()


async def clear_cart(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM cart WHERE user_id=?", (user_id,))
        await db.commit()


# ---------- Заказы ----------

async def create_order(user_id: int, items: str, total: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO orders (user_id, items, total, status) VALUES (?, ?, ?, 'ожидает')",
            (user_id, items, total),
        )
        await db.commit()
        return cur.lastrowid


async def get_last_order(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, status FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ) as cur:
            return await cur.fetchone()


async def get_orders_by_user(user_id: int, limit: int = 15):
    """Последние заказы пользователя: (id, items, total, status)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT id, items, total, status
               FROM orders
               WHERE user_id=?
               ORDER BY id DESC
               LIMIT ?""",
            (user_id, limit),
        ) as cur:
            return await cur.fetchall()


async def get_all_orders():
    """Все заказы для баристы."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, user_id, items, total, status FROM orders ORDER BY id"
        ) as cur:
            return await cur.fetchall()


async def update_order_status(order_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE orders SET status=? WHERE id=?", (status, order_id)
        )
        await db.commit()


async def get_order_user_id(order_id: int):
    """user_id заказа (для уведомления клиента)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id FROM orders WHERE id=?", (order_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def clear_delivered_orders() -> int:
    """Удаляет заказы со статусом «выдан». Возвращает количество удалённых."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM orders WHERE status = 'выдан'",
        )
        await db.commit()
        return cur.rowcount


# ---------- Перерыв баристы ----------

async def get_barista_break():
    """(break_until: float | None, notify_back: bool). break_until — Unix timestamp или None."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO barista_break (id, break_until, notify_back) VALUES (1, NULL, 0)"
        )
        await db.commit()
        async with db.execute(
            "SELECT break_until, COALESCE(notify_back, 0) FROM barista_break WHERE id = 1"
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None, False
            return (row[0], bool(row[1])) if row[0] is not None else (None, bool(row[1]))


async def set_barista_break_until(break_until: float | None):
    """Установить время окончания перерыва (Unix) или None (перерыв снят)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO barista_break (id, break_until, notify_back) VALUES (1, ?, 0)",
            (break_until,),
        )
        await db.commit()


async def set_barista_notify_back(value: bool):
    """Установить флаг «уведомить пользователей, что бариста вернулся»."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE barista_break SET notify_back = ? WHERE id = 1",
            (1 if value else 0,),
        )
        await db.commit()


async def clear_barista_break_and_set_notify():
    """Снять перерыв и включить уведомление пользователям."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE barista_break SET break_until = NULL, notify_back = 1 WHERE id = 1"
        )
        await db.commit()


# ---------- Пользователи покупательского бота ----------

async def add_buyer_user(user_id: int):
    """Добавить пользователя в список (для уведомления о возврате баристы)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO buyer_users (user_id) VALUES (?)",
            (user_id,),
        )
        await db.commit()


async def get_all_buyer_user_ids():
    """Все user_id, которые пользовались ботом покупателя."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM buyer_users") as cur:
            rows = await cur.fetchall()
            return [r[0] for r in rows]


# ---------- Отзывы ----------

async def add_review(user_id: int, username: str | None, text: str):
    import time
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO reviews (user_id, username, text, created_at) VALUES (?, ?, ?, ?)",
            (user_id, username or "", text, time.time()),
        )
        await db.commit()


async def get_reviews(limit: int = 20):
    """Последние отзывы: (id, user_id, username, text, created_at)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT id, user_id, COALESCE(username, ''), text, created_at
               FROM reviews ORDER BY id DESC LIMIT ?""",
            (limit,),
        ) as cur:
            return await cur.fetchall()


async def consume_barista_notify_back() -> bool:
    """Прочитать и сбросить флаг notify_back. Возвращает True, если нужно уведомить пользователей."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO barista_break (id, break_until, notify_back) VALUES (1, NULL, 0)"
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT notify_back FROM barista_break WHERE id = 1"
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row and row[0]:
            await db.execute("UPDATE barista_break SET notify_back = 0 WHERE id = 1")
            await db.commit()
            return True
    return False