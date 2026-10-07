# -*- coding: utf-8 -*-
"""
نقطه ورود ربات بله — بخش «استعلام ارزش منطقه‌ای ملک».

نکات پایداری زیر بار کاربران زیاد:
  • یک ThreadPoolExecutor با تعداد worker بیشتر برای درخواست‌های شبکه‌ای
    بلاک‌کننده (requests به tax.gov.ir/نشان) ست می‌شود.
  • یک هندلر خطای سراسری (dp.errors) هر استثنای گرفته‌نشده در هر هندلری
    را لاگ و به ادمین گزارش می‌کند — بدون این‌که کل ربات متوقف شود.
  • حلقهٔ polling در یک try/except بیرونی قرار دارد و در صورت قطع شدن
    غیرمنتظره (قطعی شبکه و ...) خودش را دوباره راه‌اندازی می‌کند.
  • وضعیت فرم کاربران در SQLite نگه داشته می‌شود (sqlite_storage)؛ ری‌استارت ربات
    فرم نیمه‌کارهٔ کسی را پاک نمی‌کند.
  • یک وظیفهٔ پس‌زمینه، یادآوری تمدید اشتراک را ارسال می‌کند.
"""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.types import ErrorEvent

import db
from config import (BOT_TOKEN, BALE_API_BASE, ADMIN_ID, NESHAN_API_KEY, EXECUTOR_MAX_WORKERS,
                    RENEW_CHECK_INTERVAL, RENEW_REMIND_DAYS)
from regional_value_handlers import regional_value_router
from menu_handlers import menu_router, renew_reminder_loop
from utilities_handlers import utilities_router, cpi_reminder_loop
from admin_commands import admin_router
from ops_handlers import ops_router, ops_loop, track_users_middleware
from sqlite_storage import SQLiteStorage
from bale_retry import BaleRetryMiddleware, is_transient_error

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

if not NESHAN_API_KEY:
    logger.warning(
        "⚠️ NESHAN_API_KEY تنظیم نشده — مرحلهٔ Geocoding آدرس متنی کار نخواهد کرد "
        "(ارسال موقعیت مستقیم روی نقشه همچنان کار می‌کند)."
    )

# ═══ Bot با آدرس اختصاصی بله (نه تلگرام) ═══
custom_api_server = TelegramAPIServer.from_base(BALE_API_BASE)
session = AiohttpSession(api=custom_api_server)
# خطاهای موقت بله (5xx / Connection refused) خودکار چند بار تکرار می‌شوند
session.middleware(BaleRetryMiddleware())
bot = Bot(token=BOT_TOKEN, session=session)

# پایگاه داده (و انتقال خودکار فایل‌های JSON نسخهٔ قبلی در اولین اجرا)
db.init()
dp = Dispatcher(storage=SQLiteStorage())

# ═══ لاگ کندی: هر آپدیتی که پردازشش بیش از SLOW_UPDATE_SECONDS طول بکشد ثبت می‌شود ═══
SLOW_UPDATE_SECONDS = 2.0


@dp.update.outer_middleware()
async def _timing_middleware(handler, event, data):
    started = time.perf_counter()
    try:
        return await handler(event, data)
    finally:
        elapsed = time.perf_counter() - started
        if elapsed >= SLOW_UPDATE_SECONDS:
            msg = getattr(event, "message", None)
            user = getattr(getattr(msg, "from_user", None), "id", "-")
            text = (getattr(msg, "text", None) or "")[:30]
            state = data.get("raw_state")
            logger.warning(f"[SLOW] {elapsed:.1f}s | user={user} | state={state} | text={text!r}")

# ثبت کاربران (برای پیام همگانی / پیگیری تست)
dp.update.outer_middleware()(track_users_middleware)

dp.include_router(admin_router)
# پشتیبانی، پیام همگانی، کد تخفیف، رسیدها، بکاپ، وضعیت سامانهٔ مالیاتی
dp.include_router(ops_router)
# منوی اصلی (/start، سوابق، اصالت، حساب کاربری، گزارش تجمیعی) قبل از فرم استعلام —
# تا دکمه‌های منو در هر مرحله‌ای کار کنند
dp.include_router(menu_router)
dp.include_router(utilities_router)   # 🧰 خدمات کاربردی (خسارت/مهریه، هزینه دادرسی، تمبر، ابزار فایل)
dp.include_router(regional_value_router)


# ═══ هندلر خطای سراسری — هیچ استثنایی نباید کل ربات را متوقف کند ═══
@dp.errors()
async def global_error_handler(event: ErrorEvent):
    exc = event.exception
    if is_transient_error(exc):
        # قطعی/خطای لحظه‌ای سرور بله حتی پس از تلاش‌های مجدد — باگ ربات نیست؛
        # فقط لاگ می‌شود و پیام «خطای سراسری» برای ادمین ارسال نمی‌شود.
        logger.warning(f"[GLOBAL] خطای موقت بله پس از تلاش مجدد: {type(exc).__name__}: {exc}")
        return True
    logger.error(
        f"[GLOBAL] خطای گرفته‌نشده در هندلینگ آپدیت: {exc}",
        exc_info=exc,
    )
    if ADMIN_ID:
        try:
            await bot.send_message(
                ADMIN_ID,
                f"🛑 خطای گرفته‌نشدهٔ سراسری:\n{type(event.exception).__name__}: {event.exception}",
            )
        except Exception:
            pass
    return True  # آپدیت را handled علامت بزن تا aiogram دوباره throw نکند


async def main():
    # اجرای درخواست‌های شبکه‌ای بلاک‌کننده (requests) روی یک pool بزرگ‌تر —
    # جلوگیری از صف طولانی زیر بار کاربران زیاد هم‌زمان
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=EXECUTOR_MAX_WORKERS))

    logger.info("در حال راه‌اندازی ربات...")
    reminder_task = asyncio.create_task(renew_reminder_loop(bot, RENEW_REMIND_DAYS, RENEW_CHECK_INTERVAL))
    cpi_task = asyncio.create_task(cpi_reminder_loop(bot))   # دریافت ماهانهٔ شاخص تورم بانک مرکزی
    ops_task = asyncio.create_task(ops_loop(bot))   # بکاپ روزانه، پایش سامانهٔ مالیاتی، پیگیری تست
    try:
        while True:
            try:
                await dp.start_polling(bot)
                break  # توقف عادی (Ctrl+C / SIGTERM) — دوباره راه‌اندازی نشود
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.error(f"[MAIN] polling با خطا متوقف شد، ۵ ثانیه دیگر دوباره تلاش می‌شود: {e}", exc_info=True)
                await asyncio.sleep(5)
    finally:
        reminder_task.cancel()
        cpi_task.cancel()
        ops_task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("ربات متوقف شد.")
