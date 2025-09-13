# Function.py
import os
import json
import math
import sqlite3
import datetime as dt
from typing import List, Tuple, Optional, Dict, Any

import aiohttp

DEFAULT_BASE_CCY = os.getenv("BASE_CCY", "KZT")
DEFAULT_TRACKED = [c for c in os.getenv("SUPPORTED_CCY", "USD,RUB").split(",") if c]

# ---------- FS helpers ----------
def ensure_dirs(db_path: str):
    d = os.path.dirname(db_path)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)

# ---------- DB ----------
async def init_db(db_path: str):
    with sqlite3.connect(db_path) as con:
        cur = con.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            base_ccy TEXT,
            tracked_ccy TEXT,
            monthly_budget REAL DEFAULT 0,
            tz TEXT DEFAULT 'Asia/Almaty',
            anchor_msg_id INTEGER
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            type TEXT CHECK(type IN ('income','expense')),
            amount REAL,
            ccy TEXT,
            category TEXT,
            note TEXT,
            created_at TEXT,
            month_key TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS recurrents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            ccy TEXT,
            category TEXT,
            day_of_month INTEGER,
            note TEXT,
            active INTEGER DEFAULT 1
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS debts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            direction TEXT CHECK(direction IN ('to_me','from_me')),
            counterparty TEXT,
            amount REAL,
            ccy TEXT,
            note TEXT,
            status TEXT DEFAULT 'open',
            created_at TEXT,
            closed_at TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS fx_cache (
            ccy_base TEXT,
            ccy_quote TEXT,
            rate REAL,
            fetched_at TEXT,
            PRIMARY KEY (ccy_base, ccy_quote)
        )
        """)
        con.commit()

def with_con(db_path: str):
    return sqlite3.connect(db_path)

# ---------- Users ----------
def get_or_create_user(db_path: str, user_id: int):
    with with_con(db_path) as con:
        cur = con.cursor()
        cur.execute("SELECT user_id, base_ccy, tracked_ccy, monthly_budget, tz, anchor_msg_id FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        if row:
            return dict(zip(["user_id","base_ccy","tracked_ccy","monthly_budget","tz","anchor_msg_id"], row))
        cur.execute(
            "INSERT INTO users(user_id, base_ccy, tracked_ccy, monthly_budget, tz) VALUES(?,?,?,?,?)",
            (user_id, DEFAULT_BASE_CCY, json.dumps(DEFAULT_TRACKED), 0, 'Asia/Almaty')
        )
        con.commit()
        cur.execute("SELECT user_id, base_ccy, tracked_ccy, monthly_budget, tz, anchor_msg_id FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        return dict(zip(["user_id","base_ccy","tracked_ccy","monthly_budget","tz","anchor_msg_id"], row))

def update_user_settings(db_path: str, user_id: int, **kwargs):
    if not kwargs: return
    cols, vals = [], []
    for k,v in kwargs.items():
        if k == "tracked_ccy" and isinstance(v, list):
            v = json.dumps(v[:5])
        cols.append(f"{k}=?")
        vals.append(v)
    vals.append(user_id)
    with with_con(db_path) as con:
        con.execute(f"UPDATE users SET {', '.join(cols)} WHERE user_id=?", vals)
        con.commit()

# ---------- Anchor message ----------
def get_anchor(db_path: str, user_id: int) -> Optional[int]:
    with with_con(db_path) as con:
        cur = con.cursor()
        cur.execute("SELECT anchor_msg_id FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        return row[0] if row and row[0] else None

def set_anchor(db_path: str, user_id: int, msg_id: int):
    update_user_settings(db_path, user_id, anchor_msg_id=msg_id)

# ---------- Transactions ----------
def month_key_of(dt_str_iso: str) -> str:
    d = dt.datetime.fromisoformat(dt_str_iso.replace("Z",""))
    return d.strftime("%Y-%m")

def add_transaction(db_path: str, user_id: int, typ: str, amount: float, ccy: str, category: str, note: str = ""):
    now = dt.datetime.utcnow().isoformat(timespec='seconds') + "Z"
    mk = month_key_of(now)
    with with_con(db_path) as con:
        con.execute("""
        INSERT INTO transactions(user_id, type, amount, ccy, category, note, created_at, month_key)
        VALUES(?,?,?,?,?,?,?,?)
        """, (user_id, typ, amount, ccy, category, note, now, mk))
        con.commit()

def list_transactions(db_path: str, user_id: int, month_key: Optional[str]=None, page:int=1, per_page:int=10):
    offset = (page-1)*per_page
    q = "SELECT created_at, category, amount, ccy, type, note FROM transactions WHERE user_id=?"
    args = [user_id]
    if month_key:
        q += " AND month_key=?"
        args.append(month_key)
    q += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    args += [per_page, offset]
    with with_con(db_path) as con:
        cur = con.cursor()
        cur.execute(q, args)
        rows = cur.fetchall()
    return rows

def get_month_summary(db_path: str, user_id: int, month_key: Optional[str]=None):
    mk = month_key or dt.datetime.utcnow().strftime("%Y-%m")
    with with_con(db_path) as con:
        cur = con.cursor()
        cur.execute("""
        SELECT type, SUM(amount) FROM transactions
        WHERE user_id=? AND month_key=? GROUP BY type
        """, (user_id, mk))
        rows = cur.fetchall()
    data = dict(rows) if rows else {}
    income = float(data.get('income', 0) or 0)
    expense = float(data.get('expense', 0) or 0)
    free = income - expense
    return {"month_key": mk, "income": income, "expense": expense, "free": free}

# ---------- Debts ----------
def add_debt(db_path: str, user_id: int, direction: str, counterparty: str, amount: float, ccy: str, note: str = ""):
    now = dt.datetime.utcnow().isoformat(timespec='seconds') + "Z"
    with with_con(db_path) as con:
        con.execute("""
        INSERT INTO debts(user_id, direction, counterparty, amount, ccy, note, created_at, status)
        VALUES(?,?,?,?,?,?,?, 'open')
        """, (user_id, direction, counterparty, amount, ccy, note, now))
        con.commit()

def list_debts(db_path: str, user_id: int, status: str='open'):
    with with_con(db_path) as con:
        cur = con.cursor()
        cur.execute("""
        SELECT id, direction, counterparty, amount, ccy, note, created_at FROM debts
        WHERE user_id=? AND status=?
        ORDER BY created_at DESC
        """, (user_id, status))
        return cur.fetchall()

def close_debt(db_path: str, user_id: int, debt_id: int):
    now = dt.datetime.utcnow().isoformat(timespec='seconds') + "Z"
    with with_con(db_path) as con:
        con.execute("""
        UPDATE debts SET status='closed', closed_at=? WHERE user_id=? AND id=? AND status='open'
        """, (now, user_id, debt_id))
        con.commit()

# ---------- FX ----------
FX_TTL_SECONDS = 6 * 3600

async def get_rate(base: str, quote: str, db_path: str) -> Optional[float]:
    if base == quote:
        return 1.0
    with with_con(db_path) as con:
        cur = con.cursor()
        cur.execute("SELECT rate, fetched_at FROM fx_cache WHERE ccy_base=? AND ccy_quote=?", (base, quote))
        row = cur.fetchone()
    if row:
        rate, fetched_at = row
        try:
            fetched = dt.datetime.fromisoformat(fetched_at.replace("Z",""))
            if (dt.datetime.utcnow() - fetched).total_seconds() < FX_TTL_SECONDS:
                return float(rate)
        except Exception:
            pass

    url = f"https://api.exchangerate.host/latest?base={base}&symbols={quote}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=10) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            rate = data.get("rates", {}).get(quote)
            if rate:
                now = dt.datetime.utcnow().isoformat(timespec='seconds') + "Z"
                with with_con(db_path) as con:
                    con.execute("""
                    INSERT OR REPLACE INTO fx_cache(ccy_base, ccy_quote, rate, fetched_at)
                    VALUES(?,?,?,?)
                    """, (base, quote, float(rate), now))
                    con.commit()
                return float(rate)
    return None

async def get_rates_for_user(db_path: str, user_id: int) -> List[Tuple[str, float]]:
    user = get_or_create_user(db_path, user_id)
    base = user["base_ccy"] or DEFAULT_BASE_CCY
    tracked_raw = user["tracked_ccy"]
    tracked = json.loads(tracked_raw) if tracked_raw else DEFAULT_TRACKED
    tracked = [q for q in tracked if q]
    out = []
    for q in tracked[:5]:
        r = await get_rate(base, q, db_path)
        if r:
            out.append((q, r))
    return out

# ---------- Formatting ----------
def format_table(rows: List[Tuple], totals: Optional[Dict[str,float]]=None) -> str:
    header = ["Дата", "Категория", "Сумма", "Вал"]
    lines = []
    lines.append("{:<10} {:<12} {:>10} {:<4}".format(*header))
    for r in rows:
        date = (r[0] or "")[:10]
        cat = (r[1] or "")[:12]
        amt = f"{r[2]:,.0f}".replace(",", " ")
        ccy = r[3] or ""
        lines.append("{:<10} {:<12} {:>10} {:<4}".format(date, cat, amt, ccy))
    if totals:
        income = f"{totals.get('income',0):,.0f}".replace(",", " ")
        expense = f"{totals.get('expense',0):,.0f}".replace(",", " ")
        free = f"{totals.get('free',0):,.0f}".replace(",", " ")
        lines.append("")
        lines.append(f"Итого: доход {income} | расход {expense} | свободно {free}")
    return "\n".join(lines)

def monowrap(lines_text: str) -> str:
    """
    Возвращает текст из нескольких строк, где КАЖДАЯ строка обёрнута в inline-code (`...`),
    чтобы Telegram не показывал кнопку «Скопировать код».
    """
    out = []
    for line in lines_text.splitlines():
        safe = line.replace("`", "´")
        out.append(f"`{safe}`")
    return "\n".join(out)

# ---------- Simple advice ----------
def should_buy(amount: float, free_cash: float, days_left: int) -> Tuple[str, str]:
    if free_cash <= 0:
        return ("Не советую", "Свободных средств нет. Сначала пополни бюджет.")
    tighten = 0.8 if days_left <= 7 else 1.0
    a = amount / max(free_cash, 1)
    if a <= 0.05 * tighten:
        return ("Можно", "Риск низкий, трата мала относительно свободных.")
    if a <= 0.15 * tighten:
        return ("Осторожно", "Существенно для текущего баланса, подумай дважды.")
    return ("Не советую", "Слишком большой кусок от свободных, велик риск кассового разрыва.")
