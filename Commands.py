# Commands.py
import os
import json
import math
import datetime as dt
from typing import Optional, List, Tuple

from aiogram import Dispatcher, Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.filters.state import StateFilter

from Function import (
    get_or_create_user, update_user_settings,
    add_transaction, list_transactions, get_month_summary,
    add_debt, list_debts, close_debt,
    get_rates_for_user,
    get_anchor, set_anchor,
    format_table, monowrap
)

# ---- Config / defaults ----
DEFAULT_CATEGORIES = ["Продукты","Транспорт","Коммуналка","Связь","Образование","Здоровье","Одежда","Развлечения","Прочее"]
INCOME_CATEGORIES = ["Работа","Фриланс","Подарок","Прочее"]

class TxStates(StatesGroup):
    waiting_amount = State()
    waiting_category = State()
    waiting_note = State()

class DebtStates(StatesGroup):
    waiting_direction = State()
    waiting_counterparty = State()
    waiting_amount = State()
    waiting_note = State()

# ---------- Keyboards ----------
def kb_main():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➖ Расход", callback_data="expense_add"),
         InlineKeyboardButton(text="➕ Доход", callback_data="income_add")],
        [InlineKeyboardButton(text="📊 Мои финансы", callback_data="summary"),
         InlineKeyboardButton(text="🗂 История", callback_data="history:1")],
        [InlineKeyboardButton(text="💱 Курс", callback_data="rates"),
         InlineKeyboardButton(text="🏦 Долги", callback_data="debts")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings"),
         InlineKeyboardButton(text="🧹 Очистить", callback_data="clear")]
    ])

def kb_cancel():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])

def kb_categories(for_income: bool):
    items = INCOME_CATEGORIES if for_income else DEFAULT_CATEGORIES
    rows, row = [], []
    for i, c in enumerate(items):
        row.append(InlineKeyboardButton(text=c, callback_data=f"cat:{c}"))
        if len(row) == 3:
            rows.append(row); row=[]
    if row: rows.append(row)
    rows.append([InlineKeyboardButton(text="Пропустить", callback_data="cat:__skip__")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_history(page: int, has_more: bool):
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"history:{page-1}"))
    if has_more:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"history:{page+1}"))
    if not nav:
        nav.append(InlineKeyboardButton(text="↻ Обновить", callback_data=f"history:{page}"))
    return InlineKeyboardMarkup(inline_keyboard=[nav, [InlineKeyboardButton(text="⬅️ Назад", callback_data="home")]])

def kb_debts(debts_open: List[tuple]):
    rows = []
    for d in debts_open[:10]:
        did, direction, cp, amount, ccy, note, created = d
        label = f"{'Мне' if direction=='to_me' else 'Я'}: {cp} • {amount:.0f} {ccy}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"debt_close:{did}")])
    rows.append([InlineKeyboardButton(text="➕ Добавить долг", callback_data="debt_add"),
                 InlineKeyboardButton(text="📜 Закрытые", callback_data="debts_closed")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_settings():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Базовая валюта", callback_data="set_base")],
        [InlineKeyboardButton(text="Отслеживаемые валюты (до 5)", callback_data="set_tracked")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="home")]
    ])

def kb_base_choices():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=x, callback_data=f"base:{x}") for x in ["KZT","USD","RUB","EUR","USDT"]],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])

# ---- Helpers: anchor message ----
async def reply_or_edit_anchor(message: Message, text: str, db_path: str, reply_markup: Optional[InlineKeyboardMarkup]=None):
    user_id = message.from_user.id
    anchor_id = get_anchor(db_path, user_id)
    if anchor_id:
        try:
            return await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=anchor_id,
                text=text,
                reply_markup=reply_markup
            )
        except Exception:
            pass
    m = await message.answer(text, reply_markup=reply_markup)
    set_anchor(db_path, user_id, m.message_id)
    return m

async def edit_anchor_from_cb(cb: CallbackQuery, text: str, db_path: str, reply_markup: Optional[InlineKeyboardMarkup]=None):
    user_id = cb.from_user.id
    anchor_id = get_anchor(db_path, user_id)
    if anchor_id:
        try:
            return await cb.message.bot.edit_message_text(
                chat_id=cb.message.chat.id, message_id=anchor_id,
                text=text, reply_markup=reply_markup
            )
        except Exception:
            pass
    set_anchor(db_path, user_id, cb.message.message_id)
    return await cb.message.edit_text(text, reply_markup=reply_markup)

# ---- Registration ----
def register_handlers(dp: Dispatcher, db_path: str):
    r = Router()
    dp.include_router(r)

    # /start
    @r.message(CommandStart())
    async def start_cmd(m: Message, state: FSMContext):
        get_or_create_user(db_path, m.from_user.id)
        await state.clear()
        await reply_or_edit_anchor(m, "Привет! Я *FinTrack*.\nВыбирай действие ниже.", db_path, kb_main())

    @r.callback_query(F.data == "home")
    async def home_cb(cb: CallbackQuery, state: FSMContext):
        await state.clear()
        await edit_anchor_from_cb(cb, "Главное меню:", db_path, kb_main())
        await cb.answer()

    # Универсальная отмена
    @r.callback_query(F.data == "cancel")
    async def cancel_cb(cb: CallbackQuery, state: FSMContext):
        await state.clear()
        await edit_anchor_from_cb(cb, "Действие отменено. Главное меню:", db_path, kb_main())
        await cb.answer("Отменено")

    # Чистка
    @r.callback_query(F.data == "clear")
    async def clear_cb(cb: CallbackQuery):
        for mid in range(cb.message.message_id, cb.message.message_id-20, -1):
            try:
                await cb.message.bot.delete_message(cb.message.chat.id, mid)
            except Exception:
                pass
        await cb.answer("Чат очищен (по возможности).")

    # Сводка
    @r.callback_query(F.data == "summary")
    async def summary_cb(cb: CallbackQuery):
        mk = dt.datetime.utcnow().strftime("%Y-%m")
        sums = get_month_summary(db_path, cb.from_user.id, mk)
        text = f"*Сводка {mk}*\nДоход: {sums['income']:.0f}\nРасход: {sums['expense']:.0f}\nСвободно: {sums['free']:.0f}"
        await edit_anchor_from_cb(cb, text, db_path, kb_main())
        await cb.answer()

    # Курсы
    @r.callback_query(F.data == "rates")
    async def rates_cb(cb: CallbackQuery):
        user = get_or_create_user(db_path, cb.from_user.id)
        base = user["base_ccy"]
        pairs = await get_rates_for_user(db_path, cb.from_user.id)
        if not pairs:
            await cb.answer("Не удалось получить курсы", show_alert=True); return
        lines = [f"*Курс к {base}:*"] + [f"{base} → {q}: {r:.4f}" for q, r in pairs]
        await edit_anchor_from_cb(cb, "\n".join(lines), db_path, kb_main())
        await cb.answer()

    # История
    @r.callback_query(F.data.startswith("history:"))
    async def history_cb(cb: CallbackQuery):
        _, page_s = cb.data.split(":")
        page = max(int(page_s), 1)
        per_page = 8
        mk = dt.datetime.utcnow().strftime("%Y-%m")
        rows = list_transactions(db_path, cb.from_user.id, mk, page, per_page+1)
        has_more = len(rows) > per_page
        rows = rows[:per_page]
        sums = get_month_summary(db_path, cb.from_user.id, mk)
        table = format_table(rows, sums)
        await edit_anchor_from_cb(cb, monowrap(table), db_path, kb_history(page, has_more))
        await cb.answer()

    # Добавить расход/доход
    @r.callback_query(F.data == "expense_add")
    async def expense_add(cb: CallbackQuery, state: FSMContext):
        await state.set_state(TxStates.waiting_amount)
        await state.update_data(tx_type="expense")
        await edit_anchor_from_cb(cb, "Введи сумму расхода (число):", db_path, kb_cancel())
        await cb.answer()

    @r.callback_query(F.data == "income_add")
    async def income_add(cb: CallbackQuery, state: FSMContext):
        await state.set_state(TxStates.waiting_amount)
        await state.update_data(tx_type="income")
        await edit_anchor_from_cb(cb, "Введи сумму дохода (число):", db_path, kb_cancel())
        await cb.answer()

    @r.message(TxStates.waiting_amount)
    async def tx_amount(m: Message, state: FSMContext):
        text_raw = (m.text or "").strip()
        try:
            amount = float(text_raw.replace(",", "."))
            if amount <= 0: raise ValueError()
        except Exception:
            try: await m.delete()
            except Exception: pass
            # !!! важное изменение: редактируем якорь, не создаём новое сообщение
            await reply_or_edit_anchor(m, "Некорректная сумма. Введи положительное число.", db_path, kb_cancel())
            return
        await state.update_data(amount=amount)
        try: await m.delete()
        except Exception: pass
        data = await state.get_data()
        for_income = data.get("tx_type") == "income"
        await state.set_state(TxStates.waiting_category)
        await reply_or_edit_anchor(m, "Выбери категорию:", db_path, kb_categories(for_income))

    @r.callback_query(TxStates.waiting_amount, F.data.startswith("cat:"))
    async def tx_category_wrong_cb(cb: CallbackQuery):
        await edit_anchor_from_cb(cb, "Сначала введи сумму или нажми Отмена.", db_path, kb_cancel())
        await cb.answer()

    @r.callback_query(TxStates.waiting_category, F.data.startswith("cat:"))
    async def tx_category(cb: CallbackQuery, state: FSMContext):
        _, cat = cb.data.split(":")
        if cat == "__skip__":
            cat = "Прочее"
        await state.update_data(category=cat)
        await state.set_state(TxStates.waiting_note)
        await edit_anchor_from_cb(cb, "Комментарий? (или напиши - для пропуска)", db_path, kb_cancel())
        await cb.answer()

    @r.message(TxStates.waiting_note)
    async def tx_note(m: Message, state: FSMContext):
        note = (m.text or "").strip()
        if note == "-":
            note = ""
        try: await m.delete()
        except Exception: pass
        data = await state.get_data()
        tx_type = data["tx_type"]
        amount = float(data["amount"])
        category = data["category"]
        u = get_or_create_user(db_path, m.from_user.id)
        add_transaction(db_path, m.from_user.id, tx_type, amount, u["base_ccy"], category, note)
        await state.clear()
        mk = dt.datetime.utcnow().strftime("%Y-%m")
        sums = get_month_summary(db_path, m.from_user.id, mk)
        rows = list_transactions(db_path, m.from_user.id, mk, 1, 8)
        table = format_table(rows, sums)
        await reply_or_edit_anchor(m, monowrap(table), db_path, kb_main())

    # ----- Debts -----
    @r.callback_query(F.data == "debts")
    async def debts_cb(cb: CallbackQuery):
        ds = list_debts(db_path, cb.from_user.id, 'open')
        await edit_anchor_from_cb(cb, "*Текущие долги:*", db_path, kb_debts(ds))
        await cb.answer()

    @r.callback_query(F.data == "debt_add")
    async def debt_add_start(cb: CallbackQuery, state: FSMContext):
        await state.set_state(DebtStates.waiting_direction)
        kbd = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Мне должны", callback_data="dir:to_me"),
            InlineKeyboardButton(text="Я должен", callback_data="dir:from_me")
        ], [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]])
        await edit_anchor_from_cb(cb, "Кто кому должен?", db_path, kbd)
        await cb.answer()

    @r.callback_query(DebtStates.waiting_direction, F.data.startswith("dir:"))
    async def debt_dir(cb: CallbackQuery, state: FSMContext):
        _, direction = cb.data.split(":")
        await state.update_data(direction=direction)
        await state.set_state(DebtStates.waiting_counterparty)
        await edit_anchor_from_cb(cb, "Имя контрагента?", db_path, kb_cancel())
        await cb.answer()

    @r.message(DebtStates.waiting_counterparty)
    async def debt_cp(m: Message, state: FSMContext):
        await state.update_data(counterparty=(m.text or "").strip())
        try: await m.delete()
        except: pass
        await state.set_state(DebtStates.waiting_amount)
        await reply_or_edit_anchor(m, "Сумма долга?", db_path, kb_cancel())

    @r.message(DebtStates.waiting_amount)
    async def debt_amount(m: Message, state: FSMContext):
        raw = (m.text or "").strip()
        try:
            amount = float(raw.replace(",", "."))
            if amount <= 0: raise ValueError()
        except Exception:
            try: await m.delete()
            except: pass
            await reply_or_edit_anchor(m, "Некорректная сумма. Введи положительное число.", db_path, kb_cancel())
            return
        await state.update_data(amount=amount)
        try: await m.delete()
        except: pass
        await state.set_state(DebtStates.waiting_note)
        await reply_or_edit_anchor(m, "Комментарий? (или - для пропуска)", db_path, kb_cancel())

    @r.message(DebtStates.waiting_note)
    async def debt_note(m: Message, state: FSMContext):
        note = "" if (m.text or "").strip() == "-" else (m.text or "").strip()
        try: await m.delete()
        except: pass
        data = await state.get_data()
        u = get_or_create_user(db_path, m.from_user.id)
        add_debt(db_path, m.from_user.id, data["direction"], data["counterparty"], float(data["amount"]), u["base_ccy"], note)
        await state.clear()
        ds = list_debts(db_path, m.from_user.id, 'open')
        await reply_or_edit_anchor(m, "*Текущие долги:*", db_path, kb_debts(ds))

    @r.callback_query(F.data.startswith("debt_close:"))
    async def debt_close_cb(cb: CallbackQuery):
        _, did = cb.data.split(":")
        try:
            close_debt(db_path, cb.from_user.id, int(did))
        except Exception:
            await cb.answer("Не удалось закрыть долг", show_alert=True); return
        ds = list_debts(db_path, cb.from_user.id, 'open')
        await edit_anchor_from_cb(cb, "*Текущие долги:*", db_path, kb_debts(ds))
        await cb.answer("Долг закрыт")

    # ----- Settings -----
    @r.callback_query(F.data == "settings")
    async def settings_cb(cb: CallbackQuery):
        u = get_or_create_user(db_path, cb.from_user.id)
        tracked = json.loads(u["tracked_ccy"]) if u["tracked_ccy"] else []
        text = (f"*Настройки*\nБазовая валюта: {u['base_ccy']}\n"
                f"Отслеживаемые: {', '.join(tracked) if tracked else '—'}")
        await edit_anchor_from_cb(cb, text, db_path, kb_settings())
        await cb.answer()

    @r.callback_query(F.data == "set_base")
    async def set_base_cb(cb: CallbackQuery):
        await edit_anchor_from_cb(cb, "Выбери базовую валюту:", db_path, kb_base_choices())
        await cb.answer()

    @r.callback_query(F.data.startswith("base:"))
    async def base_save(cb: CallbackQuery):
        _, val = cb.data.split(":")
        update_user_settings(db_path, cb.from_user.id, base_ccy=val)
        await settings_cb(cb)

    @r.callback_query(F.data == "set_tracked")
    async def set_tracked_cb(cb: CallbackQuery):
        all_ccy = ["USD","RUB","EUR","CNY","GBP","USDT","BTC"]
        u = get_or_create_user(db_path, cb.from_user.id)
        cur = set(json.loads(u["tracked_ccy"]) if u["tracked_ccy"] else [])
        rows, row = [], []
        for c in all_ccy:
            mark = "✅" if c in cur else "➕"
            row.append(InlineKeyboardButton(text=f"{mark} {c}", callback_data=f"track:{c}"))
            if len(row) == 3:
                rows.append(row); row=[]
        if row: rows.append(row)
        rows.append([InlineKeyboardButton(text="Сохранить", callback_data="track_save")])
        rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")])
        await edit_anchor_from_cb(cb, "Выбери до 5 валют для отображения курса:", db_path,
                                  InlineKeyboardMarkup(inline_keyboard=rows))
        await cb.answer()

    @r.callback_query(F.data.startswith("track:"))
    async def track_toggle(cb: CallbackQuery):
        _, c = cb.data.split(":")
        u = get_or_create_user(db_path, cb.from_user.id)
        cur = set(json.loads(u["tracked_ccy"]) if u["tracked_ccy"] else [])
        if c in cur:
            cur.remove(c)
        else:
            if len(cur) >= 5:
                await cb.answer("Не более 5 валют", show_alert=True); return
            cur.add(c)
        update_user_settings(db_path, cb.from_user.id, tracked_ccy=list(cur))
        await set_tracked_cb(cb)

    @r.callback_query(F.data == "track_save")
    async def track_save(cb: CallbackQuery):
        await settings_cb(cb)

    # ----- Fallbacks: всегда редактируем якорь, не создаём новые сообщения -----

    @r.message(StateFilter(TxStates.waiting_category))
    async def warn_choose_option_tx(m: Message):
        try: await m.delete()
        except: pass
        await reply_or_edit_anchor(m, "Пожалуйста, выберите одну из предложенных опций.", db_path, kb_cancel())

    @r.message(StateFilter(DebtStates.waiting_direction))
    async def warn_choose_option_debt_dir(m: Message):
        try: await m.delete()
        except: pass
        await reply_or_edit_anchor(m, "Пожалуйста, выберите одну из предложенных опций.", db_path, kb_cancel())

    # Во всех остальных случаях, когда бот ничего не ждёт — редактируем текущее меню
    @r.message()
    async def generic_warn(m: Message):
        try: await m.delete()
        except: pass
        await reply_or_edit_anchor(m, "Пожалуйста, выберите одну из предложенных опций.", db_path, kb_main())
