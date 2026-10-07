# -*- coding: utf-8 -*-
"""
کنترل دسترسی: کاربران تاییدشدهٔ ادمین، اعتبار استعلام رایگان، اشتراک
(شخصی و دفتری)، درخواست‌های تست — همه روی SQLite (db.py).

توابع async مانده‌اند تا امضای قبلی حفظ شود؛ خود عملیات SQLite کوتاه و
تراکنشی است.

اشتراک دفتری:
  مدیر دفتر (کاربری که خودش اشتراک فعال دارد یا تاییدشدهٔ ادمین است) یک
  «کد دعوت» دریافت می‌کند. همکاران با این کد عضو دفتر می‌شوند و تا وقتی
  دسترسی مدیر فعال است، بدون اشتراک جداگانه استعلام می‌گیرند.
  حداکثر تعداد اعضا: config.OFFICE_MAX_MEMBERS.
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
def _own_subscription(user_id: int):
    r = db.query_one("SELECT plan, expires_at, updated_at FROM subscriptions WHERE user_id=? AND expires_at > ?",
                     (int(user_id), time.time()))
    return dict(r) if r else None


async def get_own_subscription(user_id: int):
    """فقط اشتراکی که خود کاربر خریده (نه اشتراک دفتر)."""
    return _own_subscription(user_id)


async def get_subscription(user_id: int):
    """
    اشتراک فعالی که کاربر از آن استفاده می‌کند یا None:
    اشتراک خودش؛ وگرنه اشتراک مدیر دفتری که کاربر عضو آن است
    (در این حالت کلید office_owner هم برگردانده می‌شود).
    """
    own = _own_subscription(user_id)
    if own:
        return own
    owner = _office_owner_of(user_id)
    if owner is not None:
        if await is_approved(owner):
            return {"plan": "office", "expires_at": float("inf"), "office_owner": owner}
        osub = _own_subscription(owner)
        if osub:
            return {**osub, "office_owner": owner}
    return None


async def activate_subscription(user_id: int, plan: str, days: int) -> dict:
    """فعال‌سازی/تمدید؛ اگر هنوز فعال است، از تاریخ انقضای فعلی تمدید می‌شود."""
    now = time.time()
    with db.tx() as c:
        r = c.execute("SELECT expires_at FROM subscriptions WHERE user_id=?", (int(user_id),)).fetchone()
        start = max(now, r["expires_at"] if r else 0)
        expires = start + days * 86400
        c.execute("INSERT INTO subscriptions(user_id, plan, expires_at, updated_at) VALUES (?,?,?,?) "
                  "ON CONFLICT(user_id) DO UPDATE SET plan=excluded.plan, expires_at=excluded.expires_at, "
                  "updated_at=excluded.updated_at", (int(user_id), plan, expires, now))
    logger.info(f"[ACCESS] اشتراک {plan} ({days} روز) برای کاربر {user_id} فعال شد")
    return {"plan": plan, "expires_at": expires, "updated_at": now}


async def list_subscriptions() -> dict:
    return {str(r["user_id"]): dict(r) for r in
            db.query("SELECT user_id, plan, expires_at, updated_at FROM subscriptions")}


def record_payment(user_id: int, plan: str, amount_rial: int, charge_id: str = "") -> None:
    db.execute("INSERT INTO payments(user_id, plan, amount_rial, charge_id, created_at) VALUES (?,?,?,?,?)",
               (int(user_id), plan, int(amount_rial or 0), charge_id or "", time.time()))


# ── یادآوری تمدید ──
def due_renew_reminders(soon_days: int, now: float | None = None) -> list:
    """
    اشتراک‌هایی که باید برایشان یادآوری ارسال شود:
      kind="soon": کمتر از soon_days روز مانده و برای همین تاریخ انقضا هنوز یادآوری نشده
      kind="end" : منقضی شده (حداکثر ۷ روز پیش) و پیام پایان هنوز ارسال نشده
    """
    now = now or time.time()
    soon = db.query(
        "SELECT user_id, plan, expires_at FROM subscriptions WHERE expires_at > ? AND expires_at <= ? "
        "AND (reminded_soon IS NULL OR reminded_soon != expires_at)",
        (now, now + soon_days * 86400))
    ended = db.query(
        "SELECT user_id, plan, expires_at FROM subscriptions WHERE expires_at <= ? AND expires_at > ? "
        "AND (reminded_end IS NULL OR reminded_end != expires_at)",
        (now, now - 7 * 86400))
    return ([{**dict(r), "kind": "soon"} for r in soon] + [{**dict(r), "kind": "end"} for r in ended])


def mark_reminded(user_id: int, kind: str, expires_at: float) -> None:
    col = "reminded_soon" if kind == "soon" else "reminded_end"
    db.execute(f"UPDATE subscriptions SET {col}=? WHERE user_id=?", (expires_at, int(user_id)))


# ══════════════════════════════════════════════════════════════════
# اشتراک دفتری
# ══════════════════════════════════════════════════════════════════
def _office_owner_of(user_id: int):
    r = db.query_one("SELECT owner_id FROM office_members WHERE user_id=?", (int(user_id),))
    return r["owner_id"] if r else None


async def office_membership(user_id: int):
    """اگر کاربر عضو دفتری است: {"owner_id", "owner_active"}؛ وگرنه None."""
    owner = _office_owner_of(user_id)
    if owner is None:
        return None
    return {"owner_id": owner, "owner_active": await can_own_office(owner)}


async def can_own_office(user_id: int) -> bool:
    """مدیر دفتر می‌تواند باشد اگر خودش اشتراک فعال دارد یا تاییدشدهٔ ادمین است."""
    return await is_approved(user_id) or _own_subscription(user_id) is not None


async def get_or_create_office(owner_id: int) -> dict:
    r = db.query_one("SELECT * FROM offices WHERE owner_id=?", (int(owner_id),))
    if r:
        return dict(r)
    tr = db.query_one("SELECT office_code FROM trial_requests WHERE user_id=?", (int(owner_id),))
    for _ in range(5):
        code = db.new_code("OF", 6).replace("OF-", "")
        try:
            db.execute("INSERT INTO offices(owner_id, office_code, invite_code, created_at) VALUES (?,?,?,?)",
                       (int(owner_id), tr["office_code"] if tr else None, code, time.time()))
            break
        except Exception:
            continue
    return dict(db.query_one("SELECT * FROM offices WHERE owner_id=?", (int(owner_id),)))


async def reset_invite_code(owner_id: int) -> str:
    await get_or_create_office(owner_id)
    code = db.new_code("OF", 6).replace("OF-", "")
    db.execute("UPDATE offices SET invite_code=? WHERE owner_id=?", (code, int(owner_id)))
    return code


async def list_office_members(owner_id: int) -> list:
    return [dict(r) for r in db.query(
        "SELECT user_id, full_name, added_at FROM office_members WHERE owner_id=? ORDER BY added_at",
        (int(owner_id),))]


def normalize_invite(text: str) -> str:
    t = (text or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    return "".join(ch for ch in t.upper() if ch.isalnum()).removeprefix("OF")


async def join_office(user_id: int, invite_code: str, full_name: str, max_members: int) -> tuple:
    """
    عضویت با کد دعوت. خروجی (status, owner_id):
      ok | invalid | self | already | other_office | full | owner_inactive
    """
    code = normalize_invite(invite_code)
    if not code:
        return "invalid", None
    with db.tx() as c:
        off = c.execute("SELECT owner_id FROM offices WHERE replace(invite_code,'-','')=?", (code,)).fetchone()
        if not off:
            return "invalid", None
        owner = off["owner_id"]
        if owner == int(user_id):
            return "self", owner
        cur = c.execute("SELECT owner_id FROM office_members WHERE user_id=?", (int(user_id),)).fetchone()
        if cur and cur["owner_id"] == owner:
            return "already", owner
        if cur:
            return "other_office", cur["owner_id"]
        n = c.execute("SELECT COUNT(*) FROM office_members WHERE owner_id=?", (owner,)).fetchone()[0]
        if n >= max_members:
            return "full", owner
        c.execute("INSERT INTO office_members(user_id, owner_id, full_name, added_at) VALUES (?,?,?,?)",
                  (int(user_id), owner, full_name or "", time.time()))
    if not await can_own_office(owner):
        # عضویت ثبت می‌شود ولی تا تمدید اشتراک مدیر، دسترسی ندارد
        return "owner_inactive", owner
    logger.info(f"[OFFICE] کاربر {user_id} عضو دفتر {owner} شد")
    return "ok", owner


async def remove_office_member(owner_id: int, member_id: int) -> bool:
    return db.execute("DELETE FROM office_members WHERE owner_id=? AND user_id=?",
                      (int(owner_id), int(member_id))) > 0


async def leave_office(user_id: int):
    """خروج از دفتر؛ خروجی: owner_id قبلی یا None."""
    owner = _office_owner_of(user_id)
    if owner is not None:
        db.execute("DELETE FROM office_members WHERE user_id=?", (int(user_id),))
    return owner


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
