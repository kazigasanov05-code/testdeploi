"""
Бот для кофейни: бариста, покупатели и админка в одном файле.
"""
import asyncio
import html
import re
import time
from datetime import datetime

from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import KeyboardButton, InlineKeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder

import config
import database as db
import security

# ==================== БАРИСТА-БОТ ====================

barista_bot = Bot(
    token=config.BARISTA_BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML"),
)
barista_dp = Dispatcher(storage=MemoryStorage())


async def notify_client(user_id: int, order_id: int, status: str):
    try:
        await barista_bot.send_message(
            user_id,
            f"Ваш заказ №{order_id}: {html.escape(status)}",
        )
    except Exception:
        pass


def reply_only_main():
    """Единственная reply-кнопка: В главное меню."""
    return ReplyKeyboardBuilder().add(
        KeyboardButton(text="⬅ В главное меню")
    ).as_markup(resize_keyboard=True)


def main_menu_inline(on_break: bool = False):
    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text="📋 Показать заказы", callback_data="bar_orders"))
    if on_break:
        kb.add(InlineKeyboardButton(text="✅ Выйти с перерыва", callback_data="bar_break_end"))
    else:
        kb.add(InlineKeyboardButton(text="☕ Уйти на перерыв", callback_data="bar_break_start"))
    kb.add(InlineKeyboardButton(text="🚫 Стоп-лист", callback_data="bar_stoplist"))
    kb.add(InlineKeyboardButton(text="🗑 Очистить выданные заказы", callback_data="bar_clear_delivered"))
    kb.adjust(1)
    return kb.as_markup()


def order_inline_kb(order_id: int):
    """Под заказом: Принят | Готов | Выдан."""
    return InlineKeyboardBuilder().row(
        InlineKeyboardButton(text="Принят", callback_data=f"ord_{order_id}_accept"),
        InlineKeyboardButton(text="Готов", callback_data=f"ord_{order_id}_ready"),
        InlineKeyboardButton(text="Выдан", callback_data=f"ord_{order_id}_delivered"),
    ).as_markup()


@barista_dp.message(Command("start"))
async def barista_start(message: types.Message):
    break_until, _ = await db.get_barista_break()
    on_break = break_until is not None and break_until > time.time()
    await message.answer(
        "Привет, Бариста! 👨‍🍳\nВыберите команду:",
        reply_markup=reply_only_main(),
    )
    await message.answer(
        "Главное меню",
        reply_markup=main_menu_inline(on_break=on_break),
    )


@barista_dp.message(F.text == "⬅ В главное меню")
async def barista_back_to_main(message: types.Message):
    break_until, _ = await db.get_barista_break()
    on_break = break_until is not None and break_until > time.time()
    await message.answer(
        "Главное меню 👨‍🍳",
        reply_markup=main_menu_inline(on_break=on_break),
    )


@barista_dp.callback_query(F.data == "bar_orders")
async def show_orders(call: types.CallbackQuery):
    await call.answer()
    orders = await db.get_all_orders()
    if not orders:
        await call.message.edit_text(
            "Пока нет заказов 😔",
            reply_markup=InlineKeyboardBuilder().add(
                InlineKeyboardButton(text="⬅ В главное меню", callback_data="bar_main")
            ).as_markup(),
        )
        return

    await call.message.edit_text(
        "📋 Заказы (ниже — каждый заказ с кнопками статуса):",
        reply_markup=InlineKeyboardBuilder().add(
            InlineKeyboardButton(text="⬅ В главное меню", callback_data="bar_main")
        ).as_markup(),
    )
    for order_id, user_id, items, total, status in orders:
        block = (
            f"Заказ №{order_id} | {total}₽ | {html.escape(status)}\n"
            f"Товары: {html.escape(items)}"
        )
        if status in ("принят", "готовится"):
            block += f"\n  → Приготовить: {html.escape(items)}"
        await call.message.answer(
            block,
            reply_markup=order_inline_kb(order_id),
        )


@barista_dp.callback_query(F.data == "bar_main")
async def bar_main(call: types.CallbackQuery):
    await call.answer()
    break_until, _ = await db.get_barista_break()
    on_break = break_until is not None and break_until > time.time()
    await call.message.edit_text(
        "Главное меню 👨‍🍳",
        reply_markup=main_menu_inline(on_break=on_break),
    )


@barista_dp.callback_query(F.data == "bar_clear_delivered")
async def clear_delivered_orders(call: types.CallbackQuery):
    await call.answer()
    count = await db.clear_delivered_orders()
    text = f"🗑 Удалено выданных заказов: {count}" if count > 0 else "Нет выданных заказов для очистки."
    await call.message.edit_text(
        text,
        reply_markup=InlineKeyboardBuilder().add(
            InlineKeyboardButton(text="⬅ В главное меню", callback_data="bar_main")
        ).as_markup(),
    )


def _order_status_callback(callback_data: str):
    m = re.match(r"^ord_(\d+)_(accept|ready|delivered)$", callback_data)
    if not m:
        return None, None
    return int(m.group(1)), m.group(2)


@barista_dp.callback_query(F.data.regexp(r"^ord_\d+_accept$"))
async def order_accept(call: types.CallbackQuery):
    order_id, _ = _order_status_callback(call.data)
    if not order_id:
        await call.answer()
        return
    break_until, _ = await db.get_barista_break()
    if break_until is not None and break_until > time.time():
        await call.answer("Сейчас перерыв. Выйдите с перерыва.", show_alert=True)
        return
    orders = await db.get_all_orders()
    order_map = {o[0]: o for o in orders}
    if order_id not in order_map:
        await call.answer("Заказ не найден", show_alert=True)
        return
    await db.update_order_status(order_id, "принят")
    _, user_id, items, total, _ = order_map[order_id]
    if user_id:
        await notify_client(user_id, order_id, "принят")
    await call.answer("Заказ принят ✅")
    block = (
        f"Заказ №{order_id} | {total}₽ | принят\n"
        f"Товары: {html.escape(items)}\n"
        f"  → Приготовить: {html.escape(items)}"
    )
    try:
        await call.message.edit_text(block, reply_markup=order_inline_kb(order_id))
    except Exception:
        pass


@barista_dp.callback_query(F.data.regexp(r"^ord_\d+_ready$"))
async def order_ready(call: types.CallbackQuery):
    order_id, _ = _order_status_callback(call.data)
    if not order_id:
        await call.answer()
        return
    orders = await db.get_all_orders()
    order_map = {o[0]: o for o in orders}
    if order_id not in order_map:
        await call.answer("Заказ не найден", show_alert=True)
        return
    await db.update_order_status(order_id, "готов")
    _, user_id, items, total, _ = order_map[order_id]
    if user_id:
        await notify_client(user_id, order_id, "готов к выдаче")
    await call.answer("Готов к выдаче ✅")
    block = f"Заказ №{order_id} | {total}₽ | готов\nТовары: {html.escape(items)}"
    try:
        await call.message.edit_text(block, reply_markup=order_inline_kb(order_id))
    except Exception:
        pass


@barista_dp.callback_query(F.data.regexp(r"^ord_\d+_delivered$"))
async def order_delivered(call: types.CallbackQuery):
    order_id, _ = _order_status_callback(call.data)
    if not order_id:
        await call.answer()
        return
    orders = await db.get_all_orders()
    order_map = {o[0]: o for o in orders}
    if order_id not in order_map:
        await call.answer("Заказ не найден", show_alert=True)
        return
    await db.update_order_status(order_id, "выдан")
    _, user_id, items, total, _ = order_map[order_id]
    if user_id:
        await notify_client(user_id, order_id, "выдан")
    await call.answer("Выдан ✅")
    block = f"Заказ №{order_id} | {total}₽ | выдан\nТовары: {html.escape(items)}"
    try:
        await call.message.edit_text(block, reply_markup=order_inline_kb(order_id))
    except Exception:
        pass


@barista_dp.callback_query(F.data == "bar_break_start")
async def break_start(call: types.CallbackQuery):
    await call.answer()
    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text="15 мин", callback_data="break_15"))
    kb.add(InlineKeyboardButton(text="30 мин", callback_data="break_30"))
    kb.add(InlineKeyboardButton(text="60 мин", callback_data="break_60"))
    kb.add(InlineKeyboardButton(text="Отмена", callback_data="bar_main"))
    kb.adjust(3, 1)
    await call.message.edit_text(
        "Укажите длительность перерыва:",
        reply_markup=kb.as_markup(),
    )


@barista_dp.callback_query(F.data.regexp(r"^break_(15|30|60)$"))
async def break_set(call: types.CallbackQuery):
    m = re.match(r"^break_(15|30|60)$", call.data)
    minutes = int(m.group(1))
    break_until = time.time() + minutes * 60
    await db.set_barista_break_until(break_until)
    until_str = datetime.fromtimestamp(break_until).strftime("%H:%M")
    await call.answer(f"Перерыв до {until_str} ✅")
    await call.message.edit_text(
        f"✅ Перерыв до {until_str} ({minutes} мин). Пользователи увидят это время.",
        reply_markup=InlineKeyboardBuilder().add(
            InlineKeyboardButton(text="⬅ В главное меню", callback_data="bar_main")
        ).as_markup(),
    )


@barista_dp.callback_query(F.data == "bar_break_end")
async def break_end(call: types.CallbackQuery):
    await call.answer()
    await db.clear_barista_break_and_set_notify()
    await call.message.edit_text(
        "✅ Вы вышли с перерыва. Пользователям будет отправлено уведомление.",
        reply_markup=InlineKeyboardBuilder().add(
            InlineKeyboardButton(text="⬅ В главное меню", callback_data="bar_main")
        ).as_markup(),
    )


@barista_dp.callback_query(F.data == "bar_stoplist")
async def barista_stop_list_menu(call: types.CallbackQuery):
    await call.answer()
    pairs = await db.get_unique_drink_names()
    if not pairs:
        await call.message.edit_text(
            "Меню пусто — нечего добавлять в стоп-лист.",
            reply_markup=InlineKeyboardBuilder().add(
                InlineKeyboardButton(text="⬅ В главное меню", callback_data="bar_main")
            ).as_markup(),
        )
        return

    lines = ["🚫 Стоп-лист (по названию — все размеры):\n"]
    kb = InlineKeyboardBuilder()
    for i, (category, name) in enumerate(pairs):
        on_stop = await db.is_drink_name_on_stop_list(category, name)
        status = "✅ в стопе" if on_stop else "доступен"
        lines.append(f"• {html.escape(name)} ({html.escape(category)}) — {status}")
        if on_stop:
            kb.add(InlineKeyboardButton(text=f"✅ Снять: {name}", callback_data=f"stop_off_{i}"))
        else:
            kb.add(InlineKeyboardButton(text=f"🚫 В стоп: {name}", callback_data=f"stop_on_{i}"))
    kb.add(InlineKeyboardButton(text="⬅ В главное меню", callback_data="bar_main"))
    kb.adjust(1)
    await call.message.edit_text(
        "\n".join(lines) + "\n\nНажмите кнопку:",
        reply_markup=kb.as_markup(),
    )


@barista_dp.callback_query(F.data.regexp(r"^stop_on_(\d+)$"))
async def barista_stop_list_on(call: types.CallbackQuery):
    idx = int(re.match(r"^stop_on_(\d+)$", call.data).group(1))
    pairs = await db.get_unique_drink_names()
    if idx < 0 or idx >= len(pairs):
        await call.answer("Ошибка", show_alert=True)
        return
    category, name = pairs[idx]
    await db.set_stop_list_by_name(category, name, True)
    await call.answer("Добавлено в стоп-лист ✅")
    lines = ["🚫 <b>Стоп-лист</b> (по названию — все размеры):\n"]
    kb = InlineKeyboardBuilder()
    for i, (cat, n) in enumerate(pairs):
        on_stop = await db.is_drink_name_on_stop_list(cat, n)
        lines.append(f"• <b>{html.escape(n)}</b> ({html.escape(cat)}) — {'✅ в стопе' if on_stop else 'доступен'}")
        if on_stop:
            kb.add(InlineKeyboardButton(text=f"✅ Снять: {n}", callback_data=f"stop_off_{i}"))
        else:
            kb.add(InlineKeyboardButton(text=f"🚫 В стоп: {n}", callback_data=f"stop_on_{i}"))
    kb.add(InlineKeyboardButton(text="⬅ В главное меню", callback_data="bar_main"))
    kb.adjust(1)
    await call.message.edit_text(
        "\n".join(lines) + "\n\nНажмите кнопку:",
        reply_markup=kb.as_markup(),
    )


@barista_dp.callback_query(F.data.regexp(r"^stop_off_(\d+)$"))
async def barista_stop_list_off(call: types.CallbackQuery):
    idx = int(re.match(r"^stop_off_(\d+)$", call.data).group(1))
    pairs = await db.get_unique_drink_names()
    if idx < 0 or idx >= len(pairs):
        await call.answer("Ошибка", show_alert=True)
        return
    category, name = pairs[idx]
    await db.set_stop_list_by_name(category, name, False)
    await call.answer("Снято со стоп-листа ✅")
    lines = ["🚫 <b>Стоп-лист</b> (по названию — все размеры):\n"]
    kb = InlineKeyboardBuilder()
    for i, (cat, n) in enumerate(pairs):
        on_stop = await db.is_drink_name_on_stop_list(cat, n)
        lines.append(f"• <b>{html.escape(n)}</b> ({html.escape(cat)}) — {'✅ в стопе' if on_stop else 'доступен'}")
        if on_stop:
            kb.add(InlineKeyboardButton(text=f"✅ Снять: {n}", callback_data=f"stop_off_{i}"))
        else:
            kb.add(InlineKeyboardButton(text=f"🚫 В стоп: {n}", callback_data=f"stop_on_{i}"))
    kb.add(InlineKeyboardButton(text="⬅ В главное меню", callback_data="bar_main"))
    kb.adjust(1)
    await call.message.edit_text(
        "\n".join(lines) + "\n\nНажмите кнопку:",
        reply_markup=kb.as_markup(),
    )


# ==================== ПОКУПАТЕЛЬ-БОТ ====================

buyer_bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
buyer_dp = Dispatcher(storage=MemoryStorage())
buyer_last_ui_message_id: dict[int, int] = {}
buyer_last_invoice_message_id: dict[int, int] = {}


class AddCommentStates(StatesGroup):
    comment = State()


class ReviewStates(StatesGroup):
    text = State()


async def safe_edit_text(message: types.Message, text: str, reply_markup=None):
    """edit_text без ошибки 'message is not modified'."""
    try:
        await message.edit_text(text, reply_markup=reply_markup)
        buyer_last_ui_message_id[message.chat.id] = message.message_id
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


async def safe_answer_callback(call: types.CallbackQuery, text: str | None = None, show_alert: bool = False):
    """Безопасный answer callback, игнорирует устаревший query."""
    try:
        if text is None:
            await call.answer()
        else:
            await call.answer(text, show_alert=show_alert)
    except TelegramBadRequest as e:
        low = str(e).lower()
        if "query is too old" in low or "query id is invalid" in low:
            return
        raise


async def send_buyer_main_message(user_id: int, text: str):
    """Держит в чате один экран: редактирует его или создаёт при отсутствии."""
    prev_id = buyer_last_ui_message_id.get(user_id)
    if prev_id:
        try:
            await buyer_bot.edit_message_text(
                chat_id=user_id,
                message_id=prev_id,
                text=text,
                reply_markup=buyer_main_menu(),
            )
            return prev_id
        except TelegramBadRequest:
            pass
        except Exception:
            pass
    sent = await buyer_bot.send_message(user_id, text, reply_markup=buyer_main_menu())
    buyer_last_ui_message_id[user_id] = sent.message_id
    return sent.message_id


def buyer_main_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="☕ Меню", callback_data="menu")
    kb.button(text="🛒 Корзина", callback_data="cart")
    kb.button(text="📦 Статус заказа", callback_data="order_status")
    kb.button(text="🧾 Чеки", callback_data="receipts")
    kb.button(text="ℹ О нас", callback_data="about")
    kb.button(text="⭐ Отзывы", callback_data="reviews")
    kb.adjust(2, 2, 2)
    return kb.as_markup()


@buyer_dp.message(Command("start"))
async def buyer_start(msg: types.Message):
    await db.add_buyer_user(msg.from_user.id)
    prev_id = buyer_last_ui_message_id.pop(msg.from_user.id, None)
    if prev_id:
        try:
            await buyer_bot.delete_message(chat_id=msg.from_user.id, message_id=prev_id)
        except Exception:
            pass
    break_until, _ = await db.get_barista_break()
    on_break = break_until is not None and break_until > time.time()
    if on_break:
        until_str = datetime.fromtimestamp(break_until).strftime("%H:%M")
        text = f"Добро пожаловать в кофейню ☕\n\n⏸ <b>Бариста на перерыве до {until_str}</b>. После окончания перерыва вам придёт уведомление."
    else:
        text = "Добро пожаловать в кофейню ☕"
    await send_buyer_main_message(msg.from_user.id, text)


def _break_banner(break_until, now_ts):
    if break_until is None or break_until <= now_ts:
        return ""
    until_str = datetime.fromtimestamp(break_until).strftime("%H:%M")
    return f"\n\n⏸ Бариста на перерыве до {until_str}.\n"


@buyer_dp.callback_query()
async def buyer_callbacks(call: types.CallbackQuery, state: FSMContext):
    await safe_answer_callback(call)
    buyer_last_ui_message_id[call.from_user.id] = call.message.message_id
    menu = await db.get_menu()
    break_until, _ = await db.get_barista_break()
    now_ts = time.time()
    banner = _break_banner(break_until, now_ts)

    if call.data == "main":
        await state.clear()
        text = "Главное меню" + banner if banner else "Главное меню"
        await safe_edit_text(call.message, text, buyer_main_menu())
        return

    if call.data == "menu":
        kb = InlineKeyboardBuilder()
        for cat in menu:
            kb.button(text=cat, callback_data=f"cat:{cat}")
        kb.button(text="⬅ В главное меню", callback_data="main")
        kb.adjust(1)
        text = "Выберите категорию:" + banner if banner else "Выберите категорию:"
        await safe_edit_text(call.message, text, kb.as_markup())
        return

    if call.data.startswith("cat:"):
        cat = call.data.split(":", 1)[1]
        kb = InlineKeyboardBuilder()
        for item in menu.get(cat, []):
            text = f"{item['name']} {item['volume']} — {item['price']}"
            cb = f"add:{item['name']}|{item['volume']}|{item['price_int']}"
            kb.button(text=text, callback_data=cb)
        kb.button(text="⬅ Назад", callback_data="menu")
        kb.adjust(1)
        await safe_edit_text(call.message, html.escape(cat), kb.as_markup())
        return

    if call.data.startswith("add:"):
        _, data = call.data.split(":", 1)
        parts = data.split("|")
        if len(parts) != 3:
            await safe_answer_callback(call, "Ошибка")
            return
        name, volume, price = parts[0], parts[1], int(parts[2])
        item = f"{name} {volume}"
        await state.set_state(AddCommentStates.comment)
        await state.update_data(pending_item=item, pending_price=price)
        await call.message.edit_text(
            f"✏️ Комментарий для баристы\n\n"
            f"Напиток: {html.escape(item)}\n\n"
            f"Добавки, сиропы, пожелания? Напишите текст или нажмите «Без комментария».",
            reply_markup=InlineKeyboardBuilder().row(
                types.InlineKeyboardButton(text="Без комментария", callback_data=f"add_skip:{data}"),
            ).as_markup(),
        )
        return

    if call.data.startswith("add_skip:"):
        await state.clear()
        _, data = call.data.split(":", 1)
        parts = data.split("|")
        if len(parts) != 3:
            await safe_answer_callback(call, "Ошибка")
            return
        name, volume, price = parts[0], parts[1], int(parts[2])
        item = f"{name} {volume}"
        await db.add_to_cart(call.from_user.id, item, price, "")
        await safe_answer_callback(call, "Добавлено в корзину ✅")
        await safe_edit_text(call.message, "Добавлено в корзину ✅", buyer_main_menu())
        return

    if call.data == "cart":
        cart = await db.get_cart(call.from_user.id)
        if not cart:
            await safe_edit_text(call.message, "Корзина пуста", buyer_main_menu())
            return

        total = sum(p for _, p, _ in cart)
        lines = []
        for item, price, comment in cart:
            line = f"{html.escape(item)} — {price}₽"
            if comment:
                line += f"\n  <i>коммент: {html.escape(comment)}</i>"
            lines.append(line)
        items = "\n".join(lines)

        kb = InlineKeyboardBuilder()
        kb.button(text="💳 Оплатить", callback_data="pay")
        kb.button(text="🗑 Очистить корзину", callback_data="cart_clear")
        kb.button(text="⬅ В главное меню", callback_data="main")
        kb.adjust(1)

        await safe_edit_text(
            call.message,
            f"🛒 <b>Корзина</b>\n\n{items}\n\n💰 Итого: {total}₽",
            kb.as_markup(),
        )
        return

    if call.data == "cart_clear":
        await db.clear_cart(call.from_user.id)
        await safe_edit_text(call.message, "🗑 Корзина очищена", buyer_main_menu())
        await safe_answer_callback(call, "Корзина очищена")
        return

    if call.data == "order_status":
        order = await db.get_last_order(call.from_user.id)
        if not order:
            await safe_edit_text(call.message, "У вас нет заказов", buyer_main_menu())
            return

        oid, status = order
        await safe_edit_text(
            call.message,
            f"📦 <b>Заказ №{oid}</b>\nСтатус: <b>{html.escape(status)}</b>",
            buyer_main_menu(),
        )
        return

    if call.data == "receipts":
        orders = await db.get_orders_by_user(call.from_user.id, limit=15)
        if not orders:
            await safe_edit_text(
                call.message,
                "🧾 <b>Чеки</b>\n\nИстория покупок пока пустая.",
                buyer_main_menu(),
            )
            return

        lines = ["🧾 <b>Ваши чеки</b>\n"]
        for oid, items, total, status in orders:
            preview = html.escape((items or "").replace("\n", "; "))
            if len(preview) > 120:
                preview = preview[:117] + "..."
            lines.append(
                f"№{oid} • {total}₽ • {html.escape(status)}\n"
                f"<i>{preview}</i>\n"
            )
        await safe_edit_text(call.message, "\n".join(lines), buyer_main_menu())
        return

    if call.data == "about":
        kb = InlineKeyboardBuilder()
        kb.add(types.InlineKeyboardButton(text="📍 Открыть карту", url=config.MAP_URL))
        kb.add(types.InlineKeyboardButton(text="⬅ В главное меню", callback_data="main"))
        kb.adjust(1)
        await safe_edit_text(
            call.message,
            "ℹ <b>О нас</b>\n\nНаша кофейня ждёт вас! Нажмите кнопку ниже, чтобы открыть карту с местоположением.",
            kb.as_markup(),
        )
        return

    if call.data == "reviews":
        reviews = await db.get_reviews(15)
        if not reviews:
            text = "⭐ <b>Отзывы</b>\n\nПока нет отзывов. Станьте первым!"
        else:
            lines = ["⭐ <b>Отзывы</b>\n"]
            for rid, uid, username, rev_text, created_at in reviews:
                when = datetime.fromtimestamp(created_at).strftime("%d.%m.%Y %H:%M") if created_at else ""
                who = html.escape(username or f"ID{uid}")
                lines.append(f"• <b>{who}</b> ({when}):\n{html.escape(rev_text)}\n")
            text = "\n".join(lines)
        kb = InlineKeyboardBuilder()
        kb.add(types.InlineKeyboardButton(text="✏️ Написать отзыв", callback_data="review_write"))
        kb.add(types.InlineKeyboardButton(text="⬅ В главное меню", callback_data="main"))
        kb.adjust(1)
        await safe_edit_text(call.message, text, kb.as_markup())
        return

    if call.data == "review_write":
        await state.set_state(ReviewStates.text)
        await call.message.edit_text(
            "✏️ Напишите ваш отзыв (текстом):",
            reply_markup=InlineKeyboardBuilder().add(
                types.InlineKeyboardButton(text="Отмена", callback_data="main")
            ).as_markup(),
        )
        return

    if call.data == "pay":
        cart = await db.get_cart(call.from_user.id)
        total = sum(p for _, p, _ in cart)
        if not cart or total <= 0:
            await safe_edit_text(call.message, "Корзина пуста", buyer_main_menu())
            await safe_answer_callback(call, "Добавьте товары в корзину")
            return

        invoice_msg = await buyer_bot.send_invoice(
            chat_id=call.from_user.id,
            title="Оплата заказа",
            description="Кофейня",
            payload="coffee_order",
            provider_token=config.PAYMENT_TOKEN,
            currency="RUB",
            prices=[types.LabeledPrice(label="Заказ", amount=total * 100)],
        )
        buyer_last_invoice_message_id[call.from_user.id] = invoice_msg.message_id


@buyer_dp.pre_checkout_query()
async def pre_checkout(q: types.PreCheckoutQuery):
    await buyer_bot.answer_pre_checkout_query(q.id, ok=True)


@buyer_dp.message(AddCommentStates.comment, F.text)
async def add_comment_text(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    item = data.get("pending_item")
    price = data.get("pending_price")
    await state.clear()
    if item is None or price is None:
        try:
            await msg.delete()
        except Exception:
            pass
        await send_buyer_main_message(msg.from_user.id, "Выберите напиток в меню.")
        return
    comment = (msg.text or "").strip()[:500]
    await db.add_to_cart(msg.from_user.id, item, price, comment)
    try:
        await msg.delete()
    except Exception:
        pass
    await send_buyer_main_message(msg.from_user.id, "Добавлено в корзину ✅")


@buyer_dp.message(ReviewStates.text, F.text)
async def review_text(msg: types.Message, state: FSMContext):
    text = (msg.text or "").strip()[:1000]
    await state.clear()
    if not text:
        try:
            await msg.delete()
        except Exception:
            pass
        await send_buyer_main_message(msg.from_user.id, "Отзыв не может быть пустым.")
        return
    username = msg.from_user.username or msg.from_user.first_name or ""
    await db.add_review(msg.from_user.id, username, text)
    try:
        await msg.delete()
    except Exception:
        pass
    await send_buyer_main_message(msg.from_user.id, "Спасибо за отзыв! ✅")


@buyer_dp.message(F.successful_payment)
async def success_payment(msg: types.Message):
    invoice_msg_id = buyer_last_invoice_message_id.pop(msg.from_user.id, None)
    if invoice_msg_id:
        try:
            await buyer_bot.delete_message(chat_id=msg.from_user.id, message_id=invoice_msg_id)
        except Exception:
            pass
    cart = await db.get_cart(msg.from_user.id)
    total = sum(p for _, p, _ in cart)
    lines = []
    for item, price, comment in cart:
        line = f"{item} — {price}₽"
        if comment:
            line += f" (коммент: {comment})"
        lines.append(line)
    items = "\n".join(lines)
    order_id = await db.create_order(msg.from_user.id, items, total)
    await db.clear_cart(msg.from_user.id)

    try:
        await msg.delete()
    except Exception:
        pass
    receipt_message_id = await send_buyer_main_message(
        msg.from_user.id,
        f"🧾 <b>ЧЕК</b>\n"
        f"Заказ №{order_id}\n\n"
        f"{html.escape(items)}\n\n"
        f"💰 Итого: {total}₽\n\n"
        f"Спасибо за заказ!\n\n"
        f"Через 3 секунды вернём вас в главное меню.",
    )
    await asyncio.sleep(3)
    try:
        await buyer_bot.edit_message_text(
            chat_id=msg.from_user.id,
            message_id=receipt_message_id,
            text="Главное меню",
            reply_markup=buyer_main_menu(),
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise
    try:
        await barista_bot.send_message(
            config.BARISTA_ID,
            f"☕ <b>НОВЫЙ ЗАКАЗ №{order_id}</b>\n\n{html.escape(items)}\n\n💰 {total}₽",
        )
    except TelegramBadRequest as e:
        if "chat not found" not in str(e).lower():
            raise


# ==================== АДМИН-БОТ ====================

admin_bot = Bot(token=config.ADMIN_BOT_TOKEN)
admin_dp = Dispatcher(storage=MemoryStorage())


def is_admin(user_id: int) -> bool:
    if not config.ADMIN_IDS:
        return True
    return user_id in config.ADMIN_IDS


class AddItemStates(StatesGroup):
    category = State()
    name = State()
    volume = State()
    price = State()


class AddCategoryStates(StatesGroup):
    category_name = State()


class EditItemStates(StatesGroup):
    choose_id = State()
    category = State()
    name = State()
    volume = State()
    price = State()


class RenameCategoryStates(StatesGroup):
    new_name = State()


def admin_main_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.add(
        InlineKeyboardButton(text="📋 Показать меню", callback_data="admin_show_menu"),
        InlineKeyboardButton(text="📁 Категории", callback_data="admin_categories"),
    )
    kb.add(
        InlineKeyboardButton(text="☕️ Напитки по категории", callback_data="admin_drinks"),
        InlineKeyboardButton(text="🚫 Стоп-лист", callback_data="admin_stoplist"),
    )
    kb.adjust(2)
    return kb.as_markup()


def admin_back_kb():
    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text="🔙 В главное меню", callback_data="admin_main"))
    return kb.as_markup()


@admin_dp.message(Command("start"))
async def admin_start(msg: types.Message):
    if not is_admin(msg.from_user.id):
        await msg.answer("❌ У вас нет доступа к этому боту.")
        return
    await msg.answer(
        "👑 Панель администратора\n\n"
        "Управление меню кофейни: категории, напитки, стоп-лист.",
        reply_markup=admin_main_menu_kb(),
    )


@admin_dp.callback_query(F.data == "admin_main")
async def admin_main(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await call.message.edit_text(
        "👑 Главное меню админки",
        reply_markup=admin_main_menu_kb(),
    )
    await call.answer()


@admin_dp.callback_query(F.data == "admin_show_menu")
async def admin_show_menu(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return

    rows = await db.get_menu_with_ids()
    if not rows:
        await call.message.edit_text(
            "Меню пусто. Добавьте категории и напитки.",
            reply_markup=admin_back_kb(),
        )
        await call.answer()
        return

    by_cat = {}
    for mid, cat, name, volume, price, on_stop in rows:
        by_cat.setdefault(cat, []).append((mid, name, volume, price, on_stop))

    text = "📋 Текущее меню\n\n"
    for cat in sorted(by_cat):
        text += f"{cat}\n"
        for mid, name, volume, price, on_stop in by_cat[cat]:
            stop = " 🚫" if on_stop else ""
            text += f"  {name} {volume} — {price}{stop}\n"
        text += "\n"

    await call.message.edit_text(text, reply_markup=admin_back_kb())
    await call.answer()


@admin_dp.callback_query(F.data == "admin_categories")
async def admin_categories(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return

    categories = await db.get_categories()
    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text="➕ Добавить категорию", callback_data="admin_cat_add"))
    if categories:
        kb.add(InlineKeyboardButton(text="🗑 Удалить категорию", callback_data="admin_cat_del_list"))
        kb.add(InlineKeyboardButton(text="✏️ Переименовать категорию", callback_data="admin_cat_rename_list"))
    kb.add(InlineKeyboardButton(text="⬅ В главное меню", callback_data="admin_main"))
    kb.adjust(1)

    text = "📁 Категории\n\n"
    if categories:
        text += "Сейчас в меню: " + ", ".join(categories)
    else:
        text += "Пока нет категорий. Добавьте категорию."

    await call.message.edit_text(text, reply_markup=kb.as_markup())
    await call.answer()


@admin_dp.callback_query(F.data == "admin_cat_add")
async def admin_cat_add_start(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AddCategoryStates.category_name)
    await call.message.edit_text(
        "Введите <название новой категории>. После этого вы добавите первый напиток в эту категорию.",
        reply_markup=admin_back_kb(),
    )
    await call.answer()


@admin_dp.message(AddCategoryStates.category_name, F.text)
async def admin_cat_add_name(msg: types.Message, state: FSMContext):
    ok, err = security.validate_category(msg.text)
    if not ok:
        await msg.answer(f"❌ {err}")
        return
    cat = security.sanitize_text(msg.text, security.MAX_CATEGORY_LEN)
    await state.clear()
    await state.update_data(category=cat)
    await state.set_state(AddItemStates.name)
    await msg.answer(
        f"Категория «{cat}» будет создана при добавлении напитка.\nВведите <название> напитка:",
        reply_markup=admin_back_kb(),
    )


@admin_dp.callback_query(F.data == "admin_cat_del_list")
async def admin_cat_del_list(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    categories = await db.get_categories()
    if not categories:
        await call.answer("Нет категорий", show_alert=True)
        return
    kb = InlineKeyboardBuilder()
    for c in categories:
        kb.add(InlineKeyboardButton(text=f"🗑 {c}", callback_data=f"catdel_{c}"))
    kb.add(InlineKeyboardButton(text="⬅ Назад", callback_data="admin_categories"))
    kb.adjust(1)
    await call.message.edit_text(
        "Выберите категорию для <удаления> (удалятся все напитки в ней):",
        reply_markup=kb.as_markup(),
    )
    await call.answer()


@admin_dp.callback_query(F.data.startswith("catdel_"))
async def admin_cat_del_confirm(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    cat = call.data.replace("catdel_", "", 1)
    await db.delete_category(cat)
    await call.message.edit_text(
        f"✅ Категория «{cat}» и все напитки в ней удалены.",
        reply_markup=admin_back_kb(),
    )
    await call.answer("Удалено")


@admin_dp.callback_query(F.data == "admin_cat_rename_list")
async def admin_cat_rename_list(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    categories = await db.get_categories()
    if not categories:
        await call.answer("Нет категорий", show_alert=True)
        return
    kb = InlineKeyboardBuilder()
    for c in categories:
        kb.add(InlineKeyboardButton(text=c, callback_data=f"catren_{c}"))
    kb.add(InlineKeyboardButton(text="⬅ Назад", callback_data="admin_categories"))
    kb.adjust(1)
    await call.message.edit_text(
        "Выберите категорию для переименования:",
        reply_markup=kb.as_markup(),
    )
    await call.answer()


@admin_dp.callback_query(F.data.startswith("catren_"))
async def admin_cat_rename_ask(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    cat = call.data.replace("catren_", "", 1)
    await state.update_data(rename_old_cat=cat)
    await state.set_state(RenameCategoryStates.new_name)
    await call.message.edit_text(
        f"Введите новое название для категории «{cat}»:",
        reply_markup=admin_back_kb(),
    )
    await call.answer()


@admin_dp.message(RenameCategoryStates.new_name, F.text)
async def admin_cat_rename_do(msg: types.Message, state: FSMContext):
    ok, err = security.validate_category(msg.text)
    if not ok:
        await msg.answer(f"❌ {err}")
        return
    new_name = security.sanitize_text(msg.text, security.MAX_CATEGORY_LEN)
    data = await state.get_data()
    await state.clear()
    old_name = data["rename_old_cat"]
    await db.rename_category(old_name, new_name)
    await msg.answer(
        f"✅ Категория «{old_name}» переименована в «{new_name}».",
        reply_markup=InlineKeyboardBuilder().add(
            InlineKeyboardButton(text="⬅ В главное меню", callback_data="admin_main")
        ).as_markup(),
    )


@admin_dp.callback_query(F.data == "admin_drinks")
async def admin_drinks(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    categories = await db.get_categories()
    if not categories:
        await call.message.edit_text(
            "Сначала добавьте категорию (раздел Категории → Добавить категорию).",
            reply_markup=admin_back_kb(),
        )
        await call.answer()
        return
    kb = InlineKeyboardBuilder()
    for c in categories:
        kb.add(InlineKeyboardButton(text=c, callback_data=f"drinkcat_{c}"))
    kb.add(InlineKeyboardButton(text="⬅ В главное меню", callback_data="admin_main"))
    kb.adjust(1)
    await call.message.edit_text(
        "Выберите категорию для управления напитками:",
        reply_markup=kb.as_markup(),
    )
    await call.answer()


@admin_dp.callback_query(F.data.startswith("drinkcat_"))
async def admin_drinks_in_category(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    cat = call.data.replace("drinkcat_", "", 1)
    items = await db.get_menu_items_by_category(cat)
    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text="➕ Добавить напиток", callback_data=f"drinkadd_{cat}"))
    if items:
        kb.add(InlineKeyboardButton(text="🗑 Удалить напиток", callback_data=f"drinkdel_{cat}"))
        kb.add(InlineKeyboardButton(text="✏️ Редактировать напиток", callback_data=f"drinkedit_{cat}"))
    kb.add(InlineKeyboardButton(text="⬅ К выбору категории", callback_data="admin_drinks"))
    kb.adjust(1)

    text = f"🍷 Категория: {cat}\n\n"
    if items:
        for mid, name, volume, price, on_stop in items:
            stop = " 🚫" if on_stop else ""
            text += f"  {name} {volume} — {price}{stop}\n"
    else:
        text += "Пока нет напитков. Добавьте первый."

    await call.message.edit_text(text, reply_markup=kb.as_markup())
    await call.answer()


@admin_dp.callback_query(F.data.startswith("drinkadd_"))
async def admin_drink_add_start(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    cat = call.data.replace("drinkadd_", "", 1)
    await state.update_data(category=cat)
    await state.set_state(AddItemStates.name)
    await call.message.edit_text(
        f"Добавление напитка в категорию «{cat}».\nВведите <название> напитка:",
        reply_markup=admin_back_kb(),
    )
    await call.answer()


@admin_dp.message(AddItemStates.name, F.text)
async def admin_add_name(msg: types.Message, state: FSMContext):
    ok, err = security.validate_menu_name(msg.text)
    if not ok:
        await msg.answer(f"❌ {err}")
        return
    await state.update_data(name=security.sanitize_text(msg.text, security.MAX_NAME_LEN))
    await state.set_state(AddItemStates.volume)
    await msg.answer("Введите объём (например: 250мл или 0,4):")


@admin_dp.message(AddItemStates.volume, F.text)
async def admin_add_volume(msg: types.Message, state: FSMContext):
    ok, err = security.validate_volume(msg.text)
    if not ok:
        await msg.answer(f"❌ {err}")
        return
    await state.update_data(volume=security.sanitize_text(msg.text, security.MAX_VOLUME_LEN))
    await state.set_state(AddItemStates.price)
    await msg.answer("Введите цену (например: 200р или 200):")


@admin_dp.message(AddItemStates.price, F.text)
async def admin_add_price(msg: types.Message, state: FSMContext):
    ok, err = security.validate_price(msg.text)
    if not ok:
        await msg.answer(f"❌ {err}")
        return
    price = security.sanitize_text(msg.text, security.MAX_PRICE_LEN)
    data = await state.get_data()
    await state.clear()
    category = data["category"]
    name = data["name"]
    volume = data["volume"]
    await db.add_menu_item(category, name, volume, price)
    await msg.answer(
        f"✅ Добавлено: {category} — {name} {volume} — {price}",
        reply_markup=InlineKeyboardBuilder().add(
            InlineKeyboardButton(text="⬅ В главное меню", callback_data="admin_main")
        ).as_markup(),
    )


@admin_dp.callback_query(F.data.startswith("drinkdel_"))
async def admin_drink_del_list(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    cat = call.data.replace("drinkdel_", "", 1)
    items = await db.get_menu_items_by_category(cat)
    if not items:
        await call.answer("В этой категории нет напитков", show_alert=True)
        return
    kb = InlineKeyboardBuilder()
    for mid, name, volume, price, _ in items:
        kb.add(
            InlineKeyboardButton(
                text=f"🗑 {name} {volume} — {price}",
                callback_data=f"del_{mid}",
            )
        )
    kb.add(InlineKeyboardButton(text="⬅ Назад", callback_data=f"drinkcat_{cat}"))
    kb.adjust(1)
    await call.message.edit_text(
        f"Выберите напиток в «{cat}» для удаления:",
        reply_markup=kb.as_markup(),
    )
    await call.answer()


@admin_dp.callback_query(F.data.startswith("del_"))
async def admin_delete_confirm(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    item_id = int(call.data.split("_")[1])
    await db.delete_menu_item(item_id)
    await call.message.edit_text(
        "✅ Позиция удалена.",
        reply_markup=admin_back_kb(),
    )
    await call.answer("Удалено")


@admin_dp.callback_query(F.data.startswith("drinkedit_"))
async def admin_drink_edit_list(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    cat = call.data.replace("drinkedit_", "", 1)
    items = await db.get_menu_items_by_category(cat)
    if not items:
        await call.answer("В этой категории нет напитков", show_alert=True)
        return
    kb = InlineKeyboardBuilder()
    for mid, name, volume, price, _ in items:
        kb.add(
            InlineKeyboardButton(
                text=f"✏️ {name} {volume} — {price}",
                callback_data=f"edit_{mid}",
            )
        )
    kb.add(InlineKeyboardButton(text="⬅ Назад", callback_data=f"drinkcat_{cat}"))
    kb.adjust(1)
    await call.message.edit_text(
        f"Выберите напиток в «{cat}» для редактирования:",
        reply_markup=kb.as_markup(),
    )
    await call.answer()


@admin_dp.callback_query(F.data.startswith("edit_"))
async def admin_edit_start(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    item_id = int(call.data.split("_")[1])
    await state.update_data(edit_id=item_id)
    await state.set_state(EditItemStates.category)
    await call.message.edit_text(
        "Введите новую категорию (или оставьте текущую):",
        reply_markup=admin_back_kb(),
    )
    await call.answer()


@admin_dp.message(EditItemStates.category, F.text)
async def admin_edit_category(msg: types.Message, state: FSMContext):
    ok, err = security.validate_category(msg.text)
    if not ok:
        await msg.answer(f"❌ {err}")
        return
    await state.update_data(category=security.sanitize_text(msg.text, security.MAX_CATEGORY_LEN))
    await state.set_state(EditItemStates.name)
    await msg.answer("Введите новое <название>:")


@admin_dp.message(EditItemStates.name, F.text)
async def admin_edit_name(msg: types.Message, state: FSMContext):
    ok, err = security.validate_menu_name(msg.text)
    if not ok:
        await msg.answer(f"❌ {err}")
        return
    await state.update_data(name=security.sanitize_text(msg.text, security.MAX_NAME_LEN))
    await state.set_state(EditItemStates.volume)
    await msg.answer("Введите новый <объём>:")


@admin_dp.message(EditItemStates.volume, F.text)
async def admin_edit_volume(msg: types.Message, state: FSMContext):
    ok, err = security.validate_volume(msg.text)
    if not ok:
        await msg.answer(f"❌ {err}")
        return
    await state.update_data(volume=security.sanitize_text(msg.text, security.MAX_VOLUME_LEN))
    await state.set_state(EditItemStates.price)
    await msg.answer("Введите новую <цену>:")


@admin_dp.message(EditItemStates.price, F.text)
async def admin_edit_price(msg: types.Message, state: FSMContext):
    ok, err = security.validate_price(msg.text)
    if not ok:
        await msg.answer(f"❌ {err}")
        return
    price = security.sanitize_text(msg.text, security.MAX_PRICE_LEN)
    data = await state.get_data()
    await state.clear()
    item_id = data["edit_id"]
    category = data["category"]
    name = data["name"]
    volume = data["volume"]
    await db.update_menu_item(item_id, category, name, volume, price)
    await msg.answer(
        f"✅ Обновлено: {category} — {name} {volume} — {price}",
        reply_markup=InlineKeyboardBuilder().add(
            InlineKeyboardButton(text="⬅ В главное меню", callback_data="admin_main")
        ).as_markup(),
    )


@admin_dp.callback_query(F.data == "admin_stoplist")
async def admin_stoplist(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    pairs = await db.get_unique_drink_names()
    if not pairs:
        await call.message.edit_text(
            "Меню пусто. Нечего добавлять в стоп-лист.",
            reply_markup=admin_back_kb(),
        )
        await call.answer()
        return
    kb = InlineKeyboardBuilder()
    for i, (cat, name) in enumerate(pairs):
        on_stop = await db.is_drink_name_on_stop_list(cat, name)
        if on_stop:
            label = f"✅ Снять: {name} ({cat})"
            cb = f"stopname_off_{i}"
        else:
            label = f"🚫 В стоп: {name} ({cat})"
            cb = f"stopname_on_{i}"
        kb.add(InlineKeyboardButton(text=label, callback_data=cb))
    kb.add(InlineKeyboardButton(text="⬅ В главное меню", callback_data="admin_main"))
    kb.adjust(1)
    text = (
        "🚫 Стоп-лист (по названию напитка — все размеры)\n\n"
        "Позиции в стоп-листе не показываются покупателям. "
        "При добавлении напитка в стоп — все его размеры скрываются."
    )
    await call.message.edit_text(text, reply_markup=kb.as_markup())
    await call.answer()


@admin_dp.callback_query(F.data.regexp(r"^stopname_on_(\d+)$"))
async def admin_stop_name_on(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    m = re.match(r"^stopname_on_(\d+)$", call.data)
    idx = int(m.group(1))
    pairs = await db.get_unique_drink_names()
    if idx < 0 or idx >= len(pairs):
        await call.answer("Ошибка", show_alert=True)
        return
    category, name = pairs[idx]
    await db.set_stop_list_by_name(category, name, True)
    await call.answer("Все размеры добавлены в стоп-лист")
    kb = InlineKeyboardBuilder()
    for i, (cat, name) in enumerate(pairs):
        on_stop = await db.is_drink_name_on_stop_list(cat, name)
        if on_stop:
            label = f"✅ Снять: {name} ({cat})"
            cb = f"stopname_off_{i}"
        else:
            label = f"🚫 В стоп: {name} ({cat})"
            cb = f"stopname_on_{i}"
        kb.add(InlineKeyboardButton(text=label, callback_data=cb))
    kb.add(InlineKeyboardButton(text="⬅ В главное меню", callback_data="admin_main"))
    kb.adjust(1)
    await call.message.edit_text(
        "🚫 Стоп-лист\n\nВсе размеры напитка добавлены в стоп. Покупатели их не увидят.",
        reply_markup=kb.as_markup(),
    )


@admin_dp.callback_query(F.data.regexp(r"^stopname_off_(\d+)$"))
async def admin_stop_name_off(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    m = re.match(r"^stopname_off_(\d+)$", call.data)
    idx = int(m.group(1))
    pairs = await db.get_unique_drink_names()
    if idx < 0 or idx >= len(pairs):
        await call.answer("Ошибка", show_alert=True)
        return
    category, name = pairs[idx]
    await db.set_stop_list_by_name(category, name, False)
    await call.answer("Все размеры сняты со стоп-листа")
    kb = InlineKeyboardBuilder()
    for i, (cat, name) in enumerate(pairs):
        on_stop = await db.is_drink_name_on_stop_list(cat, name)
        if on_stop:
            label = f"✅ Снять: {name} ({cat})"
            cb = f"stopname_off_{i}"
        else:
            label = f"🚫 В стоп: {name} ({cat})"
            cb = f"stopname_on_{i}"
        kb.add(InlineKeyboardButton(text=label, callback_data=cb))
    kb.add(InlineKeyboardButton(text="⬅ В главное меню", callback_data="admin_main"))
    kb.adjust(1)
    await call.message.edit_text(
        "🚫 Стоп-лист\n\nВсе размеры напитка сняты со стоп-листа. Снова доступны покупателям.",
        reply_markup=kb.as_markup(),
    )


# ==================== ЗАПУСК ВСЕХ БОТОВ ====================

async def main():
    await db.init_db()
    print("Инициализация базы данных завершена")

    # Запускаем всех ботов параллельно
    await asyncio.gather(
        barista_dp.start_polling(barista_bot),
        buyer_dp.start_polling(buyer_bot),
        admin_dp.start_polling(admin_bot),
    )


if __name__ == "__main__":
    asyncio.run(main())