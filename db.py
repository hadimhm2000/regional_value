# -*- coding: utf-8 -*-
"""
db.py — پایگاه دادهٔ SQLite مرکزی ربات.

جایگزین فایل‌های JSON قبلی (approved_users / free_retries / subscriptions /
trial_requests) و محل ذخیرهٔ:
  • سابقهٔ استعلام‌ها (با کد رهگیری یکتا، برای دریافت دوبارهٔ PDF و تأیید اصالت)
  • دفترها و اعضای دفتر (اشتراک دفتری)
  • پرداخت‌ها و رویدادها (برای /stats)
  • وضعیت FSM کاربران (ری‌استارت ربات فرم نیمه‌کارهٔ کسی را پاک نمی‌کند)

نکات:
  • یک اتصال مشترک با WAL و یک قفل threading؛ همهٔ عملیات کوتاه و تک‌تراکنشی
    هستند، پس اجرای مستقیم در event loop (بدون executor) مشکلی ایجاد نمی‌کند.
  • هر تغییر چندمرحله‌ای (مثل مصرف اعتبار) داخل یک تراکنش انجام می‌شود تا
    دو درخواست هم‌زمان نتوانند یک اعتبار را دو بار مصرف کنند.
  • اولین اجرا، داده‌های فایل‌های JSON قدیمی را خودکار منتقل می‌کند
    (فایل‌ها با پسوند ‎.migrated‎ نگه داشته می‌شوند).
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS approved_users (
    user_id     INTEGER PRIMARY KEY,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS free_credits (
    user_id     INTEGER PRIMARY KEY,
    count       INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS subscriptions (
    user_id         INTEGER PRIMARY KEY,
    plan            TEXT NOT NULL,
    expires_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    reminded_soon   REAL,           -- expires_at ای که یادآوری «نزدیک پایان» برایش ارسال شد
    reminded_end    REAL            -- expires_at ای که پیام «پایان اشتراک» برایش ارسال شد
);
CREATE TABLE IF NOT EXISTS trial_requests (
    user_id     INTEGER PRIMARY KEY,
    office_code TEXT,
    national_id TEXT,
    full_name   TEXT,
    username    TEXT,
    status      TEXT NOT NULL,      -- pending | approved | rejected
    created_at  REAL NOT NULL,
    decided_at  REAL
);
CREATE TABLE IF NOT EXISTS offices (
    owner_id    INTEGER PRIMARY KEY,   -- مدیر دفتر = صاحب اشتراک
    office_code TEXT,
    invite_code TEXT UNIQUE NOT NULL,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS office_members (
    user_id     INTEGER PRIMARY KEY,   -- هر کاربر حداکثر عضو یک دفتر
    owner_id    INTEGER NOT NULL,
    full_name   TEXT,
    added_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_members_owner ON office_members(owner_id);
CREATE TABLE IF NOT EXISTS queries (
    code        TEXT PRIMARY KEY,      -- کد رهگیری
    user_id     INTEGER NOT NULL,
    batch_code  TEXT,                  -- گزارش تجمیعی چند ملک
    created_at  REAL NOT NULL,
    province    TEXT,
    county      TEXT,
    address     TEXT,
    total       INTEGER,
    land_value  INTEGER,
    bld_value   INTEGER,
    inputs      TEXT,                  -- JSON دادهٔ فرم (برای «محاسبهٔ مجدد با تغییر»)
    calc        TEXT,                  -- JSON نتیجهٔ ayani_calc.compute_all
    tax_result  TEXT,                  -- JSON پاسخ سامانهٔ مالیاتی
    extra       TEXT                   -- JSON سایر اطلاعات گزارش (پلاک، has_building، ...)
);
CREATE INDEX IF NOT EXISTS ix_queries_user ON queries(user_id, created_at);
CREATE INDEX IF NOT EXISTS ix_queries_batch ON queries(batch_code);
CREATE TABLE IF NOT EXISTS payments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    plan        TEXT,
    amount_rial INTEGER,
    charge_id   TEXT,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER,
    kind        TEXT NOT NULL,
    detail      TEXT,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_kind ON events(kind, created_at);
CREATE TABLE IF NOT EXISTS fsm (
    key         TEXT PRIMARY KEY,
    state       TEXT,
    data        TEXT
);
"""

_conn: sqlite3.Connection | None = None
_lock = threading.RLock()
_path: str | None = None


def init(path: str | None = None, migrate_from: str | None = None) -> None:
    """باز کردن/ساختن پایگاه داده. path=":memory:" برای تست."""
    global _conn, _path
    if path is None:
        from config import DB_PATH, DATA_DIR
        path, migrate_from = DB_PATH, (migrate_from or DATA_DIR)
    with _lock:
        if _conn is not None and _path == path and path != ":memory:":
            return
        if _conn is not None:
            _conn.close()
        _conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=30)
        _conn.row_factory = sqlite3.Row
        if path != ":memory:":
            _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.executescript(_SCHEMA)
        _path = path
    if migrate_from:
        _migrate_json(migrate_from)


def conn() -> sqlite3.Connection:
    if _conn is None:
        init()
    return _conn


@contextmanager
def tx():
    """تراکنش اتمیک (BEGIN IMMEDIATE) با قفل سراسری."""
    c = conn()
    with _lock:
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise


def query(sql: str, params=()) -> list:
    with _lock:
        return conn().execute(sql, params).fetchall()


def query_one(sql: str, params=()):
    with _lock:
        return conn().execute(sql, params).fetchone()


def execute(sql: str, params=()) -> int:
    """اجرای یک دستور تکی؛ خروجی: تعداد ردیف‌های تغییرکرده."""
    with _lock:
        return conn().execute(sql, params).rowcount


# ══════════════════════════════════════════════════════════════════
# کد رهگیری
# ══════════════════════════════════════════════════════════════════
# بدون حروف/ارقام مشابه (0/O، 1/I/L) تا هنگام تایپ از روی کاغذ اشتباه نشود
_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def new_code(prefix: str = "RV", length: int = 8) -> str:
    body = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(length))
    return f"{prefix}-{body[:4]}-{body[4:]}"


def normalize_code(text: str) -> str:
    """
    «rv 7k3m-x9pq» / ارقام فارسی / بدون خط تیره → «RV-7K3M-X9PQ».
    کد گزارش تجمیعی با پیشوند RB حفظ می‌شود؛ بدون پیشوند، RV فرض می‌شود.
    خروجی خالی یعنی متن شبیه کد رهگیری نیست.
    """
    t = (text or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    t = "".join(ch for ch in t.upper() if ch.isascii() and ch.isalnum())
    prefix = "RV"
    if t[:2] in ("RV", "RB") and len(t) == 10:
        prefix, t = t[:2], t[2:]
    if len(t) != 8 or any(ch not in _CODE_ALPHABET for ch in t):
        return ""
    return f"{prefix}-{t[:4]}-{t[4:]}"


# ══════════════════════════════════════════════════════════════════
# رویدادها (آمار)
# ══════════════════════════════════════════════════════════════════
def log_event(kind: str, user_id: int | None = None, detail: str | None = None) -> None:
    try:
        execute("INSERT INTO events(user_id, kind, detail, created_at) VALUES (?,?,?,?)",
                (user_id, kind, (detail or "")[:500] or None, time.time()))
    except Exception as e:  # آمار هرگز نباید جریان اصلی را بشکند
        logger.warning(f"[DB] ثبت رویداد {kind} ناموفق: {e}")


# ══════════════════════════════════════════════════════════════════
# انتقال خودکار داده‌های JSON قدیمی
# ══════════════════════════════════════════════════════════════════
def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"[DB] خواندن {path} برای انتقال ناموفق: {e}")
        return None


def _migrate_json(data_dir: str) -> None:
    files = {
        "approved": os.path.join(data_dir, "approved_users.json"),
        "credits": os.path.join(data_dir, "free_retries.json"),
        "subs": os.path.join(data_dir, "subscriptions.json"),
        "trials": os.path.join(data_dir, "trial_requests.json"),
    }
    present = {k: p for k, p in files.items() if os.path.exists(p)}
    if not present:
        return
    now = time.time()
    with tx() as c:
        if "approved" in present:
            for uid in _load_json(present["approved"]) or []:
                c.execute("INSERT OR IGNORE INTO approved_users VALUES (?,?)", (int(uid), now))
        if "credits" in present:
            for uid, n in (_load_json(present["credits"]) or {}).items():
                c.execute("INSERT INTO free_credits VALUES (?,?) ON CONFLICT(user_id) "
                          "DO UPDATE SET count = count + excluded.count", (int(uid), int(n)))
        if "subs" in present:
            for uid, s in (_load_json(present["subs"]) or {}).items():
                c.execute("INSERT OR REPLACE INTO subscriptions(user_id, plan, expires_at, updated_at) "
                          "VALUES (?,?,?,?)",
                          (int(uid), s.get("plan", "monthly"), float(s.get("expires_at", 0)),
                           float(s.get("updated_at", now))))
        if "trials" in present:
            for uid, r in (_load_json(present["trials"]) or {}).items():
                c.execute("INSERT OR REPLACE INTO trial_requests VALUES (?,?,?,?,?,?,?,?)",
                          (int(uid), r.get("office_code"), r.get("national_id"), r.get("full_name"),
                           r.get("username"), r.get("status", "pending"), float(r.get("created_at", now)),
                           r.get("decided_at")))
    for p in present.values():
        try:
            os.replace(p, p + ".migrated")
        except OSError as e:
            logger.error(f"[DB] تغییر نام {p} ناموفق: {e}")
    logger.info(f"[DB] داده‌های JSON قدیمی به SQLite منتقل شد: {', '.join(present)}")
