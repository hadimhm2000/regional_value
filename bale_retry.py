# -*- coding: utf-8 -*-
"""
bale_retry.py — تلاش مجدد خودکار برای خطاهای موقت API بله.

سرور بله گاهی برای چند ثانیه «Internal Server Error» (5xx) برمی‌گرداند یا
اتصال را رد می‌کند (Connection refused). بدون این middleware، همان یک خطای
لحظه‌ای کل هندلر را می‌شکند، کاربر در میانهٔ فرم بی‌پاسخ می‌ماند و به ادمین
پیام «🛑 خطای گرفته‌نشدهٔ سراسری» می‌رسد.

این middleware روی session ربات نصب می‌شود و هر درخواست (sendMessage و ...)
را در صورت خطای موقت چند بار با فاصلهٔ افزایشی تکرار می‌کند.
"""
import asyncio
import logging

import aiohttp
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter, TelegramServerError
from aiogram.methods import GetUpdates

logger = logging.getLogger(__name__)

# فاصلهٔ بین تلاش‌ها (ثانیه) — جمعاً حدود ۱۰ ثانیه صبر، سپس خطا بالا می‌رود
RETRY_DELAYS = (1, 3, 6)


def is_transient_error(exc: BaseException) -> bool:
    """خطاهای موقت سمت بله/شبکه که با تکرار درخواست برطرف می‌شوند."""
    if isinstance(exc, (TelegramServerError, TelegramRetryAfter)):
        return True
    if isinstance(exc, TelegramNetworkError):
        return True
    return isinstance(exc, (aiohttp.ClientConnectionError, asyncio.TimeoutError))


def _is_safe_to_repeat(exc: BaseException) -> bool:
    """آیا تکرار درخواست خطر ارسال پیام تکراری ندارد؟

    • 5xx و RetryAfter و «اتصال برقرار نشد» → درخواست به بله نرسیده یا پردازش
      نشده؛ تکرار امن است.
    • Timeout → ممکن است پیام ارسال شده ولی پاسخ نرسیده باشد؛ برای جلوگیری از
      پیام تکراری، تکرار نمی‌کنیم.
    """
    if isinstance(exc, (TelegramServerError, TelegramRetryAfter)):
        return True
    if isinstance(exc, TelegramNetworkError):
        cause = exc.__cause__
        if isinstance(cause, aiohttp.ClientConnectorError):
            return True
        return "timeout" not in str(exc).lower()
    return False


class BaleRetryMiddleware(BaseRequestMiddleware):
    async def __call__(self, make_request, bot, method):
        # getUpdates خودش در aiogram backoff دارد؛ دوباره‌کاری نکنیم
        if isinstance(method, GetUpdates):
            return await make_request(bot, method)

        name = type(method).__name__
        for attempt, delay in enumerate((*RETRY_DELAYS, None), start=1):
            try:
                return await make_request(bot, method)
            except Exception as e:
                if delay is None or not _is_safe_to_repeat(e):
                    raise
                if isinstance(e, TelegramRetryAfter):
                    delay = max(delay, e.retry_after)
                logger.warning(
                    f"[BALE-RETRY] {name} تلاش {attempt} ناموفق ({type(e).__name__}: {e}) — "
                    f"تلاش مجدد پس از {delay} ثانیه"
                )
                await asyncio.sleep(delay)
