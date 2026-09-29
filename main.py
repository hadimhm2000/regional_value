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
"""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent, Message

from config import BOT_TOKEN, BALE_API_BASE, ADMIN_ID, NESHAN_API_KEY, EXECUTOR_MAX_WORKERS
from regional_value_handlers import regional_value_router, regional_value_entry
from admin_commands import admin_router
from keyboards import main_menu_kb, is_main_menu_button
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

dp = Dispatcher(storage=MemoryStorage())

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

dp.include_router(admin_router)
dp.include_router(regional_value_router)


@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()  # /start همیشه کاربر را از هر مرحله‌ای به منوی اصلی برمی‌گرداند
    await message.answer(
        "👋 به ربات استعلام ارزش منطقه‌ای خوش آمدید.\n\nبرای شروع، دکمهٔ زیر را بزنید:",
        reply_markup=main_menu_kb(),
    )


@dp.message(lambda m: bool(m.text) and is_main_menu_button(m.text))
async def start_regional_value(message: Message, state: FSMContext):
    await regional_value_entry(message, state)


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
    while True:
        try:
            await dp.start_polling(bot)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.error(f"[MAIN] polling با خطا متوقف شد، ۵ ثانیه دیگر دوباره تلاش می‌شود: {e}", exc_info=True)
            await asyncio.sleep(5)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("ربات متوقف شد.")
