# -*- coding: utf-8 -*-
"""
rate_limit.py — محدودیت نرخ درخواست برای هر کاربر.

دو لایه:
  ۱) RateLimitMiddleware: حداکثر N پیام/کلیک در هر پنجرهٔ زمانی برای هر کاربر (ضد اسپم و اسکریپت).
     پیام‌های اضافه بی‌صدا رد می‌شوند و فقط یک بار در هر پنجره هشدار داده می‌شود.
  ۲) قفل «کار سنگین»: هر کاربر در هر لحظه فقط یک کار سنگین از هر نوع (استعلام، پردازش فایل)
     دارد؛ تا تمام نشده، درخواست هم‌نوع بعدی پذیرفته نمی‌شود. این قفل مانع می‌شود یک نفر همهٔ
     ظرفیت هم‌زمان (MAX_CONCURRENT_QUERIES) را بگیرد یا با ارسال هم‌زمان چند فایل از یک
     اعتبار رایگان چند بار استفاده کند.

ادمین و پیام‌های پرداخت موفق هرگز محدود نمی‌شوند. شمارنده‌ها در حافظه‌اند و با ری‌استارت صفر می‌شوند
(هدف فقط جلوگیری از هجوم لحظه‌ای است).
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque

from aiogram import BaseMiddleware

logger = logging.getLogger(__name__)

RATE_LIMITED_TEXT = "⏳ تعداد درخواست‌های شما در یک دقیقهٔ اخیر زیاد است؛ لطفاً کمی صبر کنید و دوباره تلاش کنید."


class SlidingWindowLimiter:
    """شمارندهٔ پنجرهٔ لغزان برای هر کلید (بدون وابستگی به aiogram؛ قابل تست مستقیم)."""

    def __init__(self, limit: int, window: float, clock=time.monotonic):
        self.limit = int(limit)
        self.window = float(window)
        self.clock = clock
        self._hits: dict = defaultdict(deque)
        self._warned: dict = {}
        self._last_sweep = clock()

    def hit(self, key) -> tuple[bool, bool]:
        """ثبت یک درخواست. خروجی: (مجاز است؟، باید هشدار داده شود؟)."""
        now = self.clock()
        self._sweep(now)
        q = self._hits[key]
        while q and now - q[0] >= self.window:
            q.popleft()
        if self.limit > 0 and len(q) >= self.limit:
            warn = now - self._warned.get(key, -1e18) >= self.window
            if warn:
                self._warned[key] = now
            return False, warn
        q.append(now)
        return True, False

    def _sweep(self, now: float) -> None:
        """پاک‌سازی دوره‌ای کلیدهای بی‌استفاده تا حافظه بی‌نهایت رشد نکند."""
        if now - self._last_sweep < max(self.window, 60):
            return
        self._last_sweep = now
        for k in [k for k, q in self._hits.items() if not q or now - q[-1] >= self.window]:
            del self._hits[k]
        for k in [k for k, t in self._warned.items() if now - t >= self.window]:
            del self._warned[k]


class RateLimitMiddleware(BaseMiddleware):
    """middleware بیرونی برای message یا callback_query."""

    def __init__(self, limit: int, window: float, admin_id: int | None = None):
        self.limiter = SlidingWindowLimiter(limit, window)
        self.admin_id = admin_id

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user") or getattr(event, "from_user", None)
        if user is None or (self.admin_id and user.id == self.admin_id):
            return await handler(event, data)
        # پرداخت موفق هرگز نباید گم شود (پول کاربر کم شده است)
        if getattr(event, "successful_payment", None):
            return await handler(event, data)
        allowed, warn = self.limiter.hit(user.id)
        if allowed:
            return await handler(event, data)
        if warn:
            logger.warning(f"[RATE-LIMIT] کاربر {user.id} از سقف {self.limiter.limit} درخواست در "
                           f"{self.limiter.window:.0f} ثانیه عبور کرد")
            try:
                if hasattr(event, "message") and hasattr(event, "data"):   # CallbackQuery
                    await event.answer(RATE_LIMITED_TEXT, show_alert=True)
                else:
                    await event.answer(RATE_LIMITED_TEXT)
            except Exception as e:
                logger.debug(f"[RATE-LIMIT] ارسال هشدار ناموفق: {e}")
        elif hasattr(event, "message") and hasattr(event, "data"):
            # کلیک رد شده: پاسخ خالی تا دکمه در حالت «در حال بارگذاری» نماند
            try:
                await event.answer()
            except Exception:
                pass
        return None


# ══════════════════════════════════════════════════════════════════
# قفل کار سنگین (یکی در هر لحظه برای هر کاربر و هر نوع کار)
# ══════════════════════════════════════════════════════════════════
_busy: set = set()

BUSY_TEXTS = {
    "rv_query": "⏳ استعلام قبلی شما هنوز در حال انجام است؛ لطفاً تا پایان آن صبر کنید و سپس دوباره تلاش کنید.",
    "file_tools": "⏳ فایل قبلی شما هنوز در حال پردازش است؛ لطفاً پس از دریافت نتیجه، فایل بعدی را ارسال کنید.",
}


def is_busy(kind: str, user_id: int) -> bool:
    return (kind, int(user_id)) in _busy


def try_acquire(kind: str, user_id: int) -> bool:
    """گرفتن قفل؛ False اگر کار هم‌نوعی برای این کاربر در جریان است.
    (بین بررسی و افزودن هیچ await نیست، پس در asyncio اتمیک است.)"""
    key = (kind, int(user_id))
    if key in _busy:
        return False
    _busy.add(key)
    return True


def release(kind: str, user_id: int) -> None:
    _busy.discard((kind, int(user_id)))
