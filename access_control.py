# -*- coding: utf-8 -*-
"""
کنترل دسترسی: لیست کاربران تاییدشده توسط ادمین + اعتبار استعلام رایگان.

کاربران تاییدشده (approved) بدون پرداخت هزینه استفاده می‌کنند.
کاربرانی که تاییدشده نیستند باید هزینه را پرداخت کنند؛ اگر بعد از پرداخت،
استعلام به هر دلیلی (خطای سرور مالیات/نشان و ...) کامل نشد، یک اعتبار
«استعلام رایگان» برایشان ثبت می‌شود تا بار بعد بدون پرداخت مجدد تلاش کنند.

ذخیره‌سازی ساده روی فایل JSON با قفل asyncio (کافی برای مقیاس یک ربات
تک‌پردازه‌ای؛ اگر بعداً چند-پردازه/چند-سرور شدید، این‌جا را به دیتابیس
واقعی مثل SQLite/Postgres تغییر بدهید).
"""
import asyncio
import json
import logging
import os

import time

from config import APPROVED_USERS_FILE, FREE_RETRY_FILE, SUBSCRIPTIONS_FILE, TRIAL_REQUESTS_FILE

logger = logging.getLogger(__name__)

_lock = asyncio.Lock()


def _read_json_set(path: str) -> set:
    if not os.path.exists(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception as e:
        logger.error(f"[ACCESS] خطا در خواندن {path}: {e}")
        return set()


def _write_json_set(path: str, data: set) -> None:
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(sorted(data), f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)  # نوشتن اتمیک — از خراب شدن فایل زیر بار همزمان جلوگیری می‌کند


def _read_json_dict(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"[ACCESS] خطا در خواندن {path}: {e}")
        return {}


def _write_json_dict(path: str, data: dict) -> None:
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


# ══════════════════════════════════════════════════════════════════
# لیست کاربران تاییدشده (دسترسی رایگان و همیشگی)
# ══════════════════════════════════════════════════════════════════
async def is_approved(user_id: int) -> bool:
    async with _lock:
        ids = _read_json_set(APPROVED_USERS_FILE)
    return str(user_id) in ids


async def approve_user(user_id: int) -> bool:
    """افزودن کاربر به لیست تاییدشده‌ها. True اگر تازه اضافه شد، False اگر از قبل بود."""
    async with _lock:
        ids = _read_json_set(APPROVED_USERS_FILE)
        if str(user_id) in ids:
            return False
        ids.add(str(user_id))
        _write_json_set(APPROVED_USERS_FILE, ids)
        logger.info(f"[ACCESS] کاربر {user_id} تایید شد")
        return True


async def revoke_user(user_id: int) -> bool:
    """حذف کاربر از لیست تاییدشده‌ها. True اگر حذف شد، False اگر اصلاً نبود."""
    async with _lock:
        ids = _read_json_set(APPROVED_USERS_FILE)
        if str(user_id) not in ids:
            return False
        ids.discard(str(user_id))
        _write_json_set(APPROVED_USERS_FILE, ids)
        logger.info(f"[ACCESS] تایید کاربر {user_id} لغو شد")
        return True


async def list_approved() -> list:
    async with _lock:
        ids = _read_json_set(APPROVED_USERS_FILE)
    return sorted(ids)


# ══════════════════════════════════════════════════════════════════
# اعتبار استعلام رایگان (برای کاربرانی که پرداخت کردند ولی استعلام‌شان
# به‌خاطر خطای سیستمی ناتمام ماند)
# ══════════════════════════════════════════════════════════════════
async def grant_free_retry(user_id: int, count: int = 1) -> None:
    async with _lock:
        data = _read_json_dict(FREE_RETRY_FILE)
        data[str(user_id)] = data.get(str(user_id), 0) + count
        _write_json_dict(FREE_RETRY_FILE, data)
        logger.info(f"[ACCESS] {count} اعتبار استعلام رایگان برای کاربر {user_id} ثبت شد")


async def free_retry_count(user_id: int) -> int:
    async with _lock:
        data = _read_json_dict(FREE_RETRY_FILE)
    return int(data.get(str(user_id), 0))


async def has_free_retry(user_id: int) -> bool:
    async with _lock:
        data = _read_json_dict(FREE_RETRY_FILE)
    return data.get(str(user_id), 0) > 0


async def consume_free_retry(user_id: int) -> bool:
    """مصرف یک اعتبار رایگان (در صورت وجود). True اگر مصرف شد."""
    async with _lock:
        data = _read_json_dict(FREE_RETRY_FILE)
        count = data.get(str(user_id), 0)
        if count <= 0:
            return False
        if count == 1:
            data.pop(str(user_id), None)
        else:
            data[str(user_id)] = count - 1
        _write_json_dict(FREE_RETRY_FILE, data)
        logger.info(f"[ACCESS] یک اعتبار استعلام رایگان کاربر {user_id} مصرف شد")
        return True


# ══════════════════════════════════════════════════════════════════
# اشتراک (ماهانه/سالانه) — {user_id: {"plan": ..., "expires_at": unix_ts}}
# ══════════════════════════════════════════════════════════════════
async def get_subscription(user_id: int):
    """اشتراک فعال کاربر یا None."""
    async with _lock:
        data = _read_json_dict(SUBSCRIPTIONS_FILE)
    sub = data.get(str(user_id))
    if sub and sub.get("expires_at", 0) > time.time():
        return sub
    return None


async def activate_subscription(user_id: int, plan: str, days: int) -> dict:
    """فعال‌سازی/تمدید اشتراک؛ اگر هنوز فعال است، از تاریخ انقضای فعلی تمدید می‌شود."""
    async with _lock:
        data = _read_json_dict(SUBSCRIPTIONS_FILE)
        now = time.time()
        cur = data.get(str(user_id)) or {}
        start = max(now, cur.get("expires_at", 0))
        sub = {"plan": plan, "expires_at": start + days * 86400, "updated_at": now}
        data[str(user_id)] = sub
        _write_json_dict(SUBSCRIPTIONS_FILE, data)
    logger.info(f"[ACCESS] اشتراک {plan} ({days} روز) برای کاربر {user_id} فعال شد")
    return sub


async def list_subscriptions() -> dict:
    async with _lock:
        return _read_json_dict(SUBSCRIPTIONS_FILE)


# ══════════════════════════════════════════════════════════════════
# درخواست‌های «تست» (دو استعلام رایگان پس از تایید ادمین)
# {user_id: {"office_code", "national_id", "full_name", "username", "status", "created_at"}}
# status: pending | approved | rejected
# ══════════════════════════════════════════════════════════════════
async def get_trial_request(user_id: int):
    async with _lock:
        return _read_json_dict(TRIAL_REQUESTS_FILE).get(str(user_id))


async def save_trial_request(user_id: int, office_code: str, national_id: str,
                             full_name: str = "", username: str = "") -> list:
    """
    ثبت درخواست تست (وضعیت pending). خروجی: لیست هشدارهای تکراری‌بودن
    (کاربران دیگری که با همین کد دفتر یا کدملی قبلاً درخواست داده‌اند).
    """
    async with _lock:
        data = _read_json_dict(TRIAL_REQUESTS_FILE)
        dupes = []
        for uid, r in data.items():
            if uid == str(user_id):
                continue
            if r.get("office_code") == office_code:
                dupes.append(f"کد دفتر قبلاً برای کاربر {uid} ({r.get('status')}) ثبت شده")
            if r.get("national_id") == national_id:
                dupes.append(f"کدملی قبلاً برای کاربر {uid} ({r.get('status')}) ثبت شده")
        data[str(user_id)] = {
            "office_code": office_code, "national_id": national_id,
            "full_name": full_name, "username": username,
            "status": "pending", "created_at": time.time(),
        }
        _write_json_dict(TRIAL_REQUESTS_FILE, data)
    return dupes


async def decide_trial_request(user_id: int, approve: bool):
    """
    تایید/رد درخواست تست. خروجی: رکورد به‌روزشده، یا None اگر درخواستی
    نبود یا قبلاً بررسی شده بود (جلوگیری از دوبار دادن اعتبار).
    """
    async with _lock:
        data = _read_json_dict(TRIAL_REQUESTS_FILE)
        r = data.get(str(user_id))
        if not r or r.get("status") != "pending":
            return None
        r["status"] = "approved" if approve else "rejected"
        r["decided_at"] = time.time()
        _write_json_dict(TRIAL_REQUESTS_FILE, data)
    return r
