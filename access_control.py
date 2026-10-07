# -*- coding: utf-8 -*-
"""
کنترل دسترسی: کاربران تاییدشدهٔ ادمین، اعتبار استعلام رایگان، اشتراک،
مشخصات دفتر هر مشترک (نام و کد دفتر خدمات قضایی) و درخواست‌های تست —
همه روی SQLite (db.py).

توابع async مانده‌اند تا امضای قبلی حفظ شود؛ خود عملیات SQLite کوتاه و
تراکنشی است.
"""
from __future__ import annotations

import logging
import time

import db

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# لیست کاربران تاییدشده (دسترسی رایگان و همیشگی)
# ══════════════════════════════════════════════════════════════════
async def is_approved(user_id: int) -> bool:
    return db.query_one("SELECT 1 FROM approved_users WHERE user_id=?", (int(user_id),)) is not None


async def approve_user(user_id: int) -> bool:
    """افزودن کاربر به لیست تاییدشده‌ها. True اگر تازه اضافه شد."""
    added = db.execute("INSERT OR IGNORE INTO approved_users VALUES (?,?)", (int(user_id), time.time())) > 0
    if added:
        logger.info(f"[ACCESS] کاربر {user_id} تایید شد")
    return added


async def revoke_user(user_id: int) -> bool:
    removed = db.execute("DELETE FROM approved_users WHERE user_id=?", (int(user_id),)) > 0
    if removed:
        logger.info(f"[ACCESS] تایید کاربر {user_id} لغو شد")
    return removed


async def list_approved() -> list:
    return [str(r["user_id"]) for r in db.query("SELECT user_id FROM approved_users ORDER BY user_id")]


# ══════════════════════════════════════════════════════════════════
# اعتبار استعلام رایگان (تست + جبران خطای سیستمی)
# ══════════════════════════════════════════════════════════════════
async def grant_free_retry(user_id: int, count: int = 1) -> None:
    db.execute("INSERT INTO free_credits VALUES (?,?) ON CONFLICT(user_id) "
               "DO UPDATE SET count = count + excluded.count", (int(user_id), int(count)))
    logger.info(f"[ACCESS] {count} اعتبار استعلام رایگان برای کاربر {user_id} ثبت شد")


async def free_retry_count(user_id: int) -> int:
    r = db.query_one("SELECT count FROM free_credits WHERE user_id=?", (int(user_id),))
    return int(r["count"]) if r else 0


async def has_free_retry(user_id: int) -> bool:
    return await free_retry_count(user_id) > 0


async def consume_free_retry(user_id: int) -> bool:
    """مصرف اتمیک یک اعتبار رایگان. True اگر مصرف شد."""
    with db.tx() as c:
        n = c.execute("UPDATE free_credits SET count = count - 1 WHERE user_id=? AND count > 0",
                      (int(user_id),)).rowcount
        c.execute("DELETE FROM free_credits WHERE user_id=? AND count <= 0", (int(user_id),))
    if n:
        logger.info(f"[ACCESS] یک اعتبار استعلام رایگان کاربر {user_id} مصرف شد")
    return n > 0


# ══════════════════════════════════════════════════════════════════
# اشتراک شخصی (ماهانه/سالانه)
# ══════════════════════════════════════════════════════════════════
# دو محصول جدا با اشتراک جدا:
#   "rv"   — استعلام ارزش منطقه‌ای (جدول subscriptions)
#   "util" — خدمات کاربردی: خسارت/مهریه، هزینه دادرسی، تمبر، ابزار فایل (جدول util_subscriptions)
PRODUCT_TABLES = {"rv": "subscriptions", "util": "util_subscriptions"}
PRODUCT_TITLES = {"rv": "استعلام ارزش منطقه‌ای", "util": "خدمات کاربردی"}


def _table(product: str) -> str:
    return PRODUCT_TABLES[product]


def _own_subscription(user_id: int, product: str = "rv"):
    r = db.query_one(f"SELECT plan, expires_at, updated_at FROM {_table(product)} WHERE user_id=? AND expires_at > ?",
                     (int(user_id), time.time()))
    return dict(r) if r else None


async def get_own_subscription(user_id: int, product: str = "rv"):
    return _own_subscription(user_id, product)


async def get_subscription(user_id: int, product: str = "rv"):
    """اشتراک فعال کاربر برای یک محصول یا None."""
    return _own_subscription(user_id, product)


async def activate_subscription(user_id: int, plan: str, days: int, product: str = "rv") -> dict:
    """فعال‌سازی/تمدید؛ اگر هنوز فعال است، از تاریخ انقضای فعلی تمدید می‌شود."""
    now = time.time()
    t = _table(product)
    with db.tx() as c:
        r = c.execute(f"SELECT expires_at FROM {t} WHERE user_id=?", (int(user_id),)).fetchone()
        start = max(now, r["expires_at"] if r else 0)
        expires = start + days * 86400
        c.execute(f"INSERT INTO {t}(user_id, plan, expires_at, updated_at) VALUES (?,?,?,?) "
                  "ON CONFLICT(user_id) DO UPDATE SET plan=excluded.plan, expires_at=excluded.expires_at, "
                  "updated_at=excluded.updated_at", (int(user_id), plan, expires, now))
    logger.info(f"[ACCESS] اشتراک {product}/{plan} ({days} روز) برای کاربر {user_id} فعال شد")
    refresh_subs_excel()
    return {"plan": plan, "expires_at": expires, "updated_at": now}


def refresh_subs_excel() -> None:
    """به‌روزرسانی فایل اکسل اشتراک‌ها روی دیسک (هر خریدار جدید خودکار در اکسل می‌آید)."""
    try:
        import subs_admin
        subs_admin.refresh_file()
    except Exception as e:  # اکسل نباید فعال‌سازی اشتراک را بشکند
        logger.warning(f"[ACCESS] به‌روزرسانی فایل اکسل اشتراک‌ها ناموفق: {e}")


async def list_subscriptions(product: str = "rv") -> dict:
    return {str(r["user_id"]): dict(r) for r in
            db.query(f"SELECT user_id, plan, expires_at, updated_at FROM {_table(product)}")}


async def set_subscription_expiry(user_id: int, plan: str, expires_at: float, product: str = "rv") -> None:
    """تنظیم مستقیم تاریخ پایان اشتراک (ثبت/ویرایش دستی ادمین از اکسل)."""
    db.execute(f"INSERT INTO {_table(product)}(user_id, plan, expires_at, updated_at) VALUES (?,?,?,?) "
               "ON CONFLICT(user_id) DO UPDATE SET plan=excluded.plan, expires_at=excluded.expires_at, "
               "updated_at=excluded.updated_at", (int(user_id), plan, float(expires_at), time.time()))
    logger.info(f"[ACCESS] تاریخ پایان اشتراک {product} کاربر {user_id} دستی تنظیم شد ({plan})")


PLAN_TITLES = {"monthly": "اشتراک ماهانه", "yearly": "اشتراک سالانه", "manual": "اشتراک دستی (ادمین)"}


def plan_title(plan: str, product: str = "rv") -> str:
    title = PLAN_TITLES.get(plan or "", plan or "اشتراک")
    return f"{title} {PRODUCT_TITLES[product]}"


# ══════════════════════════════════════════════════════════════════
# خدمات کاربردی: اعتبار رایگان هر بخش (config.UTIL_FREE_PER_TOOL) و سپس اشتراک
# ══════════════════════════════════════════════════════════════════
def util_used(user_id: int, tool: str) -> int:
    r = db.query_one("SELECT count FROM util_usage WHERE user_id=? AND tool=?", (int(user_id), tool))
    return int(r["count"]) if r else 0


def util_free_left(user_id: int, tool: str) -> int:
    from config import UTIL_FREE_PER_TOOL
    return max(0, UTIL_FREE_PER_TOOL - util_used(user_id, tool))


async def util_access(user_id: int, tool: str) -> str:
    """
    وضعیت دسترسی به یک بخش خدمات کاربردی:
      "free_user" (تاییدشدهٔ ادمین) | "subscribed" | "free_credit" | "none"
    """
    if await is_approved(user_id):
        return "free_user"
    if _own_subscription(user_id, "util"):
        return "subscribed"
    if util_free_left(user_id, tool) > 0:
        return "free_credit"
    return "none"


def record_util_use(user_id: int, tool: str) -> None:
    """ثبت یک استفادهٔ کامل (برای مشترکان هم ثبت می‌شود؛ برای آمار)."""
    db.execute("INSERT INTO util_usage(user_id, tool, count, last_at) VALUES (?,?,1,?) "
               "ON CONFLICT(user_id, tool) DO UPDATE SET count=count+1, last_at=excluded.last_at",
               (int(user_id), tool, time.time()))


# ══════════════════════════════════════════════════════════════════
# مشخصات دفتر هر مشترک (نام و کد دفتر خدمات قضایی — ثبت ادمین)
# ══════════════════════════════════════════════════════════════════
def get_office_info(user_id: int) -> dict:
    """{"office_name", "office_code", "note"}؛ اگر ادمین کد دفتر را ثبت نکرده، کد درخواست تست."""
    r = db.query_one("SELECT office_name, office_code, note FROM subscriber_info WHERE user_id=?", (int(user_id),))
    info = dict(r) if r else {"office_name": None, "office_code": None, "note": None}
    if not info.get("office_code"):
        tr = db.query_one("SELECT office_code FROM trial_requests WHERE user_id=?", (int(user_id),))
        if tr and tr["office_code"]:
            info["office_code"] = tr["office_code"]
    return info


def issuer_line(user_id: int):
    """
    متن «صادرکننده» برای سربرگ PDFها: نام و کد دفتر خدمات قضایی کاربر (ثبت ادمین یا کد درخواست تست).
    اگر هیچ‌کدام ثبت نشده باشد None.
    """
    try:
        info = get_office_info(user_id)
    except Exception:
        return None
    name = (info.get("office_name") or "").strip()
    code = (info.get("office_code") or "").strip()
    if not name and not code:
        return None
    if name and not name.startswith("دفتر"):
        name = f"دفتر خدمات الکترونیک قضایی {name}"
    if not name:
        name = "دفتر خدمات الکترونیک قضایی"
    return f"صادرکننده: {name}" + (f" — کد {code}" if code else "")


def set_office_info(user_id: int, office_name=None, office_code=None, note=None) -> None:
    """ثبت/ویرایش نام و کد دفتر؛ مقدار None یعنی آن فیلد تغییر نکند."""
    cur = db.query_one("SELECT office_name, office_code, note FROM subscriber_info WHERE user_id=?",
                       (int(user_id),))
    cur = dict(cur) if cur else {"office_name": None, "office_code": None, "note": None}
    vals = {"office_name": office_name, "office_code": office_code, "note": note}
    merged = {k: (cur[k] if v is None else (v.strip() or None)) for k, v in vals.items()}
    db.execute("INSERT INTO subscriber_info(user_id, office_name, office_code, note, updated_at) VALUES (?,?,?,?,?) "
               "ON CONFLICT(user_id) DO UPDATE SET office_name=excluded.office_name, "
               "office_code=excluded.office_code, note=excluded.note, updated_at=excluded.updated_at",
               (int(user_id), merged["office_name"], merged["office_code"], merged["note"], time.time()))


def record_payment(user_id: int, plan: str, amount_rial: int, charge_id: str = "") -> None:
    db.execute("INSERT INTO payments(user_id, plan, amount_rial, charge_id, created_at) VALUES (?,?,?,?,?)",
               (int(user_id), plan, int(amount_rial or 0), charge_id or "", time.time()))


# ── یادآوری تمدید ──
def due_renew_reminders(soon_days: int, now: float | None = None, product: str = "rv") -> list:
    """
    اشتراک‌هایی که باید برایشان یادآوری ارسال شود:
      kind="soon": کمتر از soon_days روز مانده و برای همین تاریخ انقضا هنوز یادآوری نشده
      kind="end" : منقضی شده (حداکثر ۷ روز پیش) و پیام پایان هنوز ارسال نشده
    """
    now = now or time.time()
    soon = db.query(
        f"SELECT user_id, plan, expires_at FROM {_table(product)} WHERE expires_at > ? AND expires_at <= ? "
        "AND (reminded_soon IS NULL OR reminded_soon != expires_at)",
        (now, now + soon_days * 86400))
    ended = db.query(
        f"SELECT user_id, plan, expires_at FROM {_table(product)} WHERE expires_at <= ? AND expires_at > ? "
        "AND (reminded_end IS NULL OR reminded_end != expires_at)",
        (now, now - 7 * 86400))
    return ([{**dict(r), "kind": "soon", "product": product} for r in soon]
            + [{**dict(r), "kind": "end", "product": product} for r in ended])


def mark_reminded(user_id: int, kind: str, expires_at: float, product: str = "rv") -> None:
    col = "reminded_soon" if kind == "soon" else "reminded_end"
    db.execute(f"UPDATE {_table(product)} SET {col}=? WHERE user_id=?", (expires_at, int(user_id)))


# ══════════════════════════════════════════════════════════════════
# درخواست‌های «تست»
# ══════════════════════════════════════════════════════════════════
async def get_trial_request(user_id: int):
    r = db.query_one("SELECT * FROM trial_requests WHERE user_id=?", (int(user_id),))
    return dict(r) if r else None


async def save_trial_request(user_id: int, office_code: str, national_id: str,
                             full_name: str = "", username: str = "") -> list:
    """ثبت درخواست (pending). خروجی: هشدارهای تکراری‌بودن کد دفتر/کدملی."""
    dupes = []
    for r in db.query("SELECT user_id, office_code, national_id, status FROM trial_requests "
                      "WHERE user_id != ? AND (office_code=? OR national_id=?)",
                      (int(user_id), office_code, national_id)):
        if r["office_code"] == office_code:
            dupes.append(f"کد دفتر قبلاً برای کاربر {r['user_id']} ({r['status']}) ثبت شده")
        if r["national_id"] == national_id:
            dupes.append(f"کدملی قبلاً برای کاربر {r['user_id']} ({r['status']}) ثبت شده")
    db.execute("INSERT OR REPLACE INTO trial_requests(user_id, office_code, national_id, full_name, username, "
               "status, created_at, decided_at) VALUES (?,?,?,?,?,'pending',?,NULL)",
               (int(user_id), office_code, national_id, full_name, username, time.time()))
    return dupes


async def decide_trial_request(user_id: int, approve: bool):
    """تایید/رد درخواست؛ None اگر درخواستی نبود یا قبلاً بررسی شده بود."""
    with db.tx() as c:
        n = c.execute("UPDATE trial_requests SET status=?, decided_at=? WHERE user_id=? AND status='pending'",
                      ("approved" if approve else "rejected", time.time(), int(user_id))).rowcount
        if not n:
            return None
        r = c.execute("SELECT * FROM trial_requests WHERE user_id=?", (int(user_id),)).fetchone()
    return dict(r)
