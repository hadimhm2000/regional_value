# -*- coding: utf-8 -*-
"""
ops_handlers.py — هندلرهای نگه‌داری، فروش و پشتیبانی:

  کاربر:
    💬 پشتیبانی            — پیام (متن/عکس/فایل) به ادمین؛ پاسخ ادمین از طریق ربات می‌رسد
    🎟 کد تخفیف دارم       — در صفحهٔ انتخاب اشتراک (هر دو محصول)
    🧾 رسیدهای پرداخت      — در «حساب کاربری»
    هشدار قطعی سامانه     — پیش از شروع فرم استعلام، اگر سامانهٔ مالیاتی قطع باشد

  ادمین:
    /backup                       پشتیبان فوری (پشتیبان روزانه خودکار در ساعت BACKUP_HOUR)
    /broadcast                    پیام همگانی با انتخاب گروه مخاطب
    /discount_new کد درصد [سقف] [روز] [rv|util|all]   /discounts   /discount_off کد
    /taxstatus                    وضعیت سامانهٔ مالیاتی   /maptest   آزمایش سرویس نقشهٔ نشان
    پاسخ به پیام پشتیبانی: دکمهٔ «✍️ پاسخ» یا Reply روی همان پیام

  پس‌زمینه (ops_loop): پشتیبان روزانه، بررسی دوباره سامانهٔ قطع‌شده، پیگیری تست‌گیرندگان.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import os
import time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import access_control
import db
import discounts
import ops
from config import (ADMIN_ID, BACKUP_HOUR, BACKUP_KEEP, BROADCAST_DELAY, TAX_DOWN_THRESHOLD, TAX_PROBE_INTERVAL,
                    TRIAL_FOLLOWUP_CODE_DAYS, TRIAL_FOLLOWUP_DAYS, TRIAL_FOLLOWUP_DISCOUNT, USAGE_ALERT_DAILY,
                    USAGE_ALERT_PROVINCES)
from keyboards import SUPPORT_BUTTON, is_back, is_button, is_restart, main_menu_kb, nav_only_kb

logger = logging.getLogger(__name__)

ops_router = Router()


class OpsForm(StatesGroup):
    discount_code = State()
    support_user = State()
    support_admin_reply = State()
    broadcast_content = State()
    broadcast_confirm = State()


def _fa(n) -> str:
    return str(n).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def _is_admin_id(uid) -> bool:
    return bool(ADMIN_ID and uid == ADMIN_ID)


def _is_admin(message: Message) -> bool:
    return bool(message.from_user and _is_admin_id(message.from_user.id))


async def _notify_admin(bot: Bot, text: str, **kw):
    if not (ADMIN_ID and bot):
        return None
    try:
        return await bot.send_message(ADMIN_ID, text, **kw)
    except Exception as e:
        logger.warning(f"[OPS] پیام به ادمین ناموفق: {e}")
        return None


def _dur(seconds: float) -> str:
    m = int(seconds // 60)
    return f"{_fa(m // 60)} ساعت و {_fa(m % 60)} دقیقه" if m >= 60 else f"{_fa(max(1, m))} دقیقه"


# ══════════════════════════════════════════════════════════════════
# ثبت کاربران (middleware بیرونی روی همهٔ آپدیت‌ها)
# ══════════════════════════════════════════════════════════════════
async def track_users_middleware(handler, event, data):
    try:
        user = getattr(getattr(event, "message", None), "from_user", None) or \
            getattr(getattr(event, "callback_query", None), "from_user", None)
        if user and not user.is_bot:
            ops.touch_user(user.id, getattr(user, "full_name", "") or "", user.username or "")
    except Exception as e:
        logger.warning(f"[OPS] ثبت کاربر ناموفق: {e}")
    return await handler(event, data)


# ══════════════════════════════════════════════════════════════════
# پایش سامانهٔ مالیاتی
# ══════════════════════════════════════════════════════════════════
async def record_tax_result(bot: Bot, ok: bool, error=None):
    change = ops.tax_record(ok, TAX_DOWN_THRESHOLD)
    if change == "down":
        logger.error("[TAX] سامانهٔ مالیاتی قطع تشخیص داده شد")
        await _notify_admin(bot, f"🔴 سامانهٔ مالیاتی پس از {_fa(TAX_DOWN_THRESHOLD)} خطای پیاپی قطع تشخیص داده شد.\n"
                                 f"آخرین خطا: {str(error)[:200]}\n"
                                 "تا وصل شدن، به کاربران پیش از شروع فرم اطلاع داده می‌شود؛ هر "
                                 f"{_fa(TAX_PROBE_INTERVAL // 60)} دقیقه دوباره بررسی می‌شود.")
    elif change == "up":
        await _on_tax_up(bot)


async def _on_tax_up(bot: Bot):
    downtime = float(db.kv_get("tax_last_downtime") or 0)
    logger.info("[TAX] سامانهٔ مالیاتی دوباره در دسترس است")
    await _notify_admin(bot, f"🟢 سامانهٔ مالیاتی دوباره در دسترس است (مدت قطعی: {_dur(downtime)}).")
    for uid in ops.tax_pop_waiters():
        try:
            await bot.send_message(uid, "🟢 سامانهٔ مالیاتی دوباره در دسترس است؛ می‌توانید استعلام بگیرید.",
                                   reply_markup=main_menu_kb())
            await asyncio.sleep(BROADCAST_DELAY)
        except Exception:
            pass


async def tax_down_notice(message: Message, batch_code=None) -> bool:
    """اگر سامانه قطع است، پیام هشدار با دو گزینه نمایش داده می‌شود؛ True = فرم شروع نشود."""
    since = ops.tax_down_since()
    if not since:
        return False
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔔 وقتی وصل شد خبرم کن", callback_data="tax:notify")],
        [InlineKeyboardButton(text="▶️ ادامه به هر حال", callback_data=f"tax:go:{batch_code or ''}")],
    ])
    await message.answer(
        "⚠️ *سامانهٔ استعلام سازمان امور مالیاتی در حال حاضر پاسخ نمی‌دهد* "
        f"(از {_dur(time.time() - since)} پیش).\n\n"
        "می‌توانید به هر حال ادامه دهید (استعلام ممکن است ناموفق شود؛ در این صورت هزینه‌ای کسر نمی‌شود)، "
        "یا بخواهید وقتی سامانه وصل شد به شما خبر بدهیم.", reply_markup=kb)
    return True


@ops_router.callback_query(F.data == "tax:notify")
async def cb_tax_notify(callback: CallbackQuery):
    ops.tax_add_waiter(callback.from_user.id)
    await callback.answer("ثبت شد")
    await callback.message.answer("🔔 وقتی سامانه وصل شد به شما پیام می‌دهیم.", reply_markup=main_menu_kb())


@ops_router.callback_query(F.data.startswith("tax:go:"))
async def cb_tax_go(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    from regional_value_handlers import regional_value_entry
    batch = callback.data.split(":", 2)[2] or None
    # پیام callback متعلق به ربات است؛ from_user را کاربر واقعی قرار می‌دهیم
    msg = callback.message.model_copy(update={"from_user": callback.from_user})
    await regional_value_entry(msg.as_(callback.bot), state, batch, skip_tax_check=True)


async def tax_probe(bot: Bot) -> bool:
    """یک استعلام آزمایشی (مختصات میدان آزادی تهران)؛ True اگر سامانه پاسخ داد."""
    def _probe():
        from geocode_and_query import query_by_coordinates
        r = query_by_coordinates(35.6997, 51.3380, province_hint="تهران")
        return bool((r or {}).get("tax_info"))
    try:
        ok = await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(None, _probe), timeout=60)
    except Exception as e:
        logger.info(f"[TAX] بررسی دوباره ناموفق: {e}")
        ok = False
    if ok:
        await record_tax_result(bot, True)
    return ok


@ops_router.message(Command("taxstatus"))
async def cmd_taxstatus(message: Message):
    if not _is_admin(message):
        return
    since = ops.tax_down_since()
    streak = db.kv_get("tax_fail_streak", 0)
    if since:
        import json
        text = (f"🔴 سامانهٔ مالیاتی قطع است (از {_dur(time.time() - since)} پیش).\n"
                f"منتظر خبر وصل شدن: {_fa(len(json.loads(db.kv_get('tax_waiters') or '[]')))} نفر")
    else:
        text = f"🟢 سامانهٔ مالیاتی در دسترس است (خطاهای پیاپی اخیر: {_fa(streak)})."
    await message.answer(text + "\n\nبررسی فوری: /taxcheck")


@ops_router.message(Command("taxcheck"))
async def cmd_taxcheck(message: Message, bot: Bot):
    if not _is_admin(message):
        return
    await message.answer("⏳ در حال استعلام آزمایشی...")
    ok = await tax_probe(bot)
    await message.answer("🟢 سامانه پاسخ داد." if ok else "🔴 سامانه پاسخ نداد.")


# ══════════════════════════════════════════════════════════════════
# استفادهٔ غیرعادی
# ══════════════════════════════════════════════════════════════════
async def check_usage(bot: Bot, user_id: int):
    alert = ops.usage_alert(user_id, USAGE_ALERT_DAILY, USAGE_ALERT_PROVINCES)
    if not alert:
        return
    office = access_control.issuer_line(user_id) or "دفتر ثبت نشده"
    top = "، ".join(f"{p or '-'} ({_fa(n)})" for p, n in alert["top"])
    await _notify_admin(
        bot, f"⚠️ *استفادهٔ غیرعادی*\n\nکاربر {user_id} — {office}\n"
             f"در ۲۴ ساعت گذشته: {_fa(alert['count'])} استعلام از {_fa(alert['provinces'])} استان\n"
             f"استان‌ها: {top}\n\n"
             "ممکن است اشتراک بین چند دفتر استفاده شود. سوابق: /subs_excel")


# ══════════════════════════════════════════════════════════════════
# کد تخفیف — ورود کاربر
# ══════════════════════════════════════════════════════════════════
async def ask_discount_code(message: Message, state: FSMContext, product: str):
    await state.update_data(disc_return=product)
    await message.answer("🎟 کد تخفیف را وارد کنید:", reply_markup=nav_only_kb())
    await state.set_state(OpsForm.discount_code)


async def _back_to_plans(message: Message, state: FSMContext, prefix: str = ""):
    product = (await state.get_data()).get("disc_return", "rv")
    if product == "util":
        from utilities_handlers import offer_plans
        await offer_plans(message, state, prefix)
    else:
        from regional_value_handlers import _ask_plan
        await _ask_plan(message, state, prefix)


@ops_router.message(OpsForm.discount_code)
async def discount_code_input(message: Message, state: FSMContext):
    text = message.text or ""
    if is_restart(text):
        await state.clear()
        await message.answer("🏠 منوی اصلی", reply_markup=main_menu_kb())
        return
    if is_back(text):
        await _back_to_plans(message, state)
        return
    product = (await state.get_data()).get("disc_return", "rv")
    try:
        d = discounts.validate(text, message.from_user.id, product)
    except discounts.DiscountError as e:
        await message.answer(f"⚠️ {e}\nکد دیگری وارد کنید یا «بازگشت» را بزنید.")
        return
    await state.update_data(discount={"code": d["code"], "percent": d["percent"], "product": d["product"]})
    db.log_event("discount_applied", message.from_user.id, d["code"])
    await _back_to_plans(message, state)


# ══════════════════════════════════════════════════════════════════
# کد تخفیف — ادمین
# ══════════════════════════════════════════════════════════════════
@ops_router.message(Command("discount_new"))
async def cmd_discount_new(message: Message, command: CommandObject):
    if not _is_admin(message):
        return
    parts = (command.args or "").split()
    usage = ("فرمت: /discount_new کد درصد [سقف_تعداد] [مدت_روز] [rv|util|all]\n"
             "مثال: /discount_new KANOON20 20 50 30 all\n"
             "(سقف یا مدت ۰ = نامحدود؛ rv = ارزش منطقه‌ای، util = خدمات کاربردی)")
    if len(parts) < 2:
        await message.answer(usage)
        return
    try:
        nums = [int(x.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))) for x in parts[1:4] if x.isdigit()
                or x.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")).isdigit()]
        product = parts[-1] if parts[-1] in discounts.PRODUCTS else "all"
        percent = nums[0]
        max_uses = nums[1] if len(nums) > 1 and nums[1] > 0 else None
        days = nums[2] if len(nums) > 2 and nums[2] > 0 else None
        d = discounts.create(parts[0], percent, max_uses, days, product)
    except (IndexError, ValueError) as e:
        await message.answer(f"⚠️ {e if isinstance(e, discounts.DiscountError) else 'ورودی نامعتبر'}\n\n{usage}")
        return
    await message.answer(f"✅ کد تخفیف ساخته شد:\n{_describe_code(d)}")


def _describe_code(d: dict) -> str:
    from menu_handlers import fa_date
    parts = [f"🎟 {d['code']} — {_fa(d['percent'])}٪ — {discounts.PRODUCTS[d['product']]}",
             f"استفاده: {_fa(d['used'])}" + (f" از {_fa(d['max_uses'])}" if d["max_uses"] else " (بدون سقف)")]
    if d["expires_at"]:
        parts.append(f"اعتبار تا {fa_date(d['expires_at'])}")
    if not d["active"]:
        parts.append("⛔️ غیرفعال")
    return " | ".join(parts)


@ops_router.message(Command("discounts"))
async def cmd_discounts(message: Message):
    if not _is_admin(message):
        return
    codes = discounts.list_codes()
    personal = db.query_one("SELECT COUNT(*) n, SUM(used) u FROM discount_codes WHERE for_user IS NOT NULL")
    lines = [_describe_code(d) for d in codes] or ["هنوز کد عمومی ساخته نشده است."]
    lines.append(f"\nکدهای شخصی پیگیری تست: {_fa(personal['n'] or 0)} (استفاده‌شده: {_fa(personal['u'] or 0)})")
    lines.append("\nساخت: /discount_new کد درصد [سقف] [روز] [rv|util|all]\nغیرفعال: /discount_off کد")
    await message.answer("\n".join(lines))


@ops_router.message(Command("discount_off"))
async def cmd_discount_off(message: Message, command: CommandObject):
    if not _is_admin(message):
        return
    ok = discounts.deactivate(command.args or "")
    await message.answer("✅ کد غیرفعال شد." if ok else "⚠️ چنین کدی پیدا نشد.")


# ══════════════════════════════════════════════════════════════════
# 💬 پشتیبانی
# ══════════════════════════════════════════════════════════════════
SUPPORT_HOURLY_LIMIT = 15


@ops_router.message(lambda m: bool(m.text) and is_button(m.text, SUPPORT_BUTTON))
async def support_entry(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("💬 *پشتیبانی*\n\nسؤال یا مشکل خود را بنویسید (می‌توانید عکس یا فایل هم بفرستید). "
                         "پاسخ ادمین در همین ربات برای شما ارسال می‌شود.", reply_markup=nav_only_kb())
    await state.set_state(OpsForm.support_user)


def _reply_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✍️ پاسخ", callback_data=f"sup:r:{uid}")]])


async def _relay(bot: Bot, chat_id: int, message: Message, header: str, reply_markup=None):
    """ارسال محتوای پیام (متن/عکس/فایل) با یک سرتیتر؛ خروجی: پیام ارسال‌شده یا None."""
    body = message.text or message.caption or ""
    text = f"{header}\n\n{body}".strip()
    if message.photo:
        return await bot.send_photo(chat_id, message.photo[-1].file_id, caption=text[:1000], reply_markup=reply_markup)
    if message.document:
        return await bot.send_document(chat_id, message.document.file_id, caption=text[:1000],
                                       reply_markup=reply_markup)
    if message.text:
        return await bot.send_message(chat_id, text, reply_markup=reply_markup)
    return None


def _is_menu_text(message: Message) -> bool:
    from keyboards import ACCOUNT_BUTTON, HISTORY_BUTTON, MAIN_MENU_BUTTON, UTIL_BUTTON, VERIFY_BUTTON
    t = message.text or ""
    return bool(t) and (t.startswith("/") or any(is_button(t, b) for b in (
        MAIN_MENU_BUTTON, UTIL_BUTTON, HISTORY_BUTTON, VERIFY_BUTTON, ACCOUNT_BUTTON)))


@ops_router.message(OpsForm.support_user, lambda m: not _is_menu_text(m))
async def support_user_message(message: Message, state: FSMContext, bot: Bot):
    text = message.text or ""
    if is_back(text) or is_restart(text):
        await state.clear()
        await message.answer("🏠 منوی اصلی", reply_markup=main_menu_kb())
        return
    if not (message.text or message.photo or message.document):
        await message.answer("⚠️ فقط متن، عکس یا فایل قابل ارسال است.")
        return
    uid = message.from_user.id
    recent = db.query_one("SELECT COUNT(*) n FROM support_msgs WHERE user_id=? AND created_at>?",
                          (uid, time.time() - 3600))["n"]
    if recent >= SUPPORT_HOURLY_LIMIT:
        await message.answer("⏳ تعداد پیام‌های شما در یک ساعت گذشته زیاد بوده است؛ لطفاً کمی بعد پیام دهید.")
        return
    if not ADMIN_ID:
        await message.answer("⚠️ پشتیبانی در حال حاضر در دسترس نیست.")
        return
    u = message.from_user
    header = (f"💬 پیام پشتیبانی از {getattr(u, 'full_name', '') or '-'}"
              + (f" (@{u.username})" if u.username else "") + f"\n🆔 {uid}"
              + (f"\n🏢 {access_control.issuer_line(uid).replace('صادرکننده: ', '')}"
                 if access_control.issuer_line(uid) else ""))
    try:
        sent = await _relay(bot, ADMIN_ID, message, header, reply_markup=_reply_kb(uid))
    except Exception as e:
        logger.error(f"[SUPPORT] ارسال به ادمین ناموفق: {e}")
        sent = None
    if not sent:
        await message.answer("⚠️ ارسال پیام ناموفق بود؛ لطفاً دوباره تلاش کنید.")
        return
    db.execute("INSERT OR REPLACE INTO support_msgs(admin_msg_id, user_id, created_at) VALUES (?,?,?)",
               (sent.message_id, uid, time.time()))
    db.log_event("support_msg", uid)
    await message.answer("✅ پیام شما برای پشتیبانی ارسال شد. می‌توانید پیام دیگری هم بفرستید یا به منوی اصلی "
                         "بازگردید.", reply_markup=nav_only_kb())


@ops_router.callback_query(F.data.startswith("sup:r:"))
async def cb_support_reply(callback: CallbackQuery, state: FSMContext):
    if not _is_admin_id(callback.from_user.id):
        await callback.answer("دسترسی ندارید.")
        return
    uid = int(callback.data.split(":")[2])
    await state.set_state(OpsForm.support_admin_reply)
    await state.update_data(sup_target=uid)
    await callback.answer()
    await callback.message.answer(f"✍️ پاسخ خود به کاربر {uid} را بفرستید (متن، عکس یا فایل). انصراف: /cancel")


async def _deliver_reply(bot: Bot, admin_msg: Message, uid: int) -> bool:
    try:
        sent = await _relay(bot, uid, admin_msg, "📩 *پاسخ پشتیبانی:*")
        db.log_event("support_reply", uid)
        return bool(sent)
    except TelegramForbiddenError:
        ops.mark_blocked(uid)
        return False
    except Exception as e:
        logger.warning(f"[SUPPORT] ارسال پاسخ به {uid} ناموفق: {e}")
        return False


@ops_router.message(OpsForm.support_admin_reply)
async def support_admin_reply(message: Message, state: FSMContext, bot: Bot):
    if not _is_admin(message):
        return
    uid = (await state.get_data()).get("sup_target")
    await state.clear()
    if (message.text or "").startswith("/cancel") or not uid:
        await message.answer("انصراف داده شد.")
        return
    ok = await _deliver_reply(bot, message, uid)
    await message.answer("✅ پاسخ ارسال شد." if ok else "⚠️ ارسال پاسخ ناموفق بود (ممکن است ربات مسدود شده باشد).")


async def _is_support_reply(message: Message) -> bool:
    r = message.reply_to_message
    if not (r and _is_admin(message)):
        return False
    return db.query_one("SELECT 1 FROM support_msgs WHERE admin_msg_id=?", (r.message_id,)) is not None


@ops_router.message(_is_support_reply)
async def support_admin_reply_to(message: Message, bot: Bot):
    uid = db.query_one("SELECT user_id FROM support_msgs WHERE admin_msg_id=?",
                       (message.reply_to_message.message_id,))["user_id"]
    ok = await _deliver_reply(bot, message, uid)
    await message.answer("✅ پاسخ ارسال شد." if ok else "⚠️ ارسال پاسخ ناموفق بود.")


# ══════════════════════════════════════════════════════════════════
# 📢 پیام همگانی (ادمین)
# ══════════════════════════════════════════════════════════════════
@ops_router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext):
    if not _is_admin(message):
        return
    await state.clear()
    rows = []
    for key, title in ops.SEGMENTS.items():
        n = len(ops.segment_users(key))
        rows.append([InlineKeyboardButton(text=f"{title} ({_fa(n)})", callback_data=f"bc:seg:{key}")])
    await message.answer("📢 *پیام همگانی* — گروه مخاطب را انتخاب کنید:",
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@ops_router.callback_query(F.data.startswith("bc:seg:"))
async def cb_broadcast_segment(callback: CallbackQuery, state: FSMContext):
    if not _is_admin_id(callback.from_user.id):
        await callback.answer()
        return
    seg = callback.data.split(":")[2]
    await state.set_state(OpsForm.broadcast_content)
    await state.update_data(bc_segment=seg)
    await callback.answer()
    await callback.message.answer(f"گروه: {ops.SEGMENTS[seg]}\n\nپیام را بفرستید (متن، عکس با توضیح یا فایل). "
                                  "انصراف: /cancel")


@ops_router.message(OpsForm.broadcast_content)
async def broadcast_content(message: Message, state: FSMContext, bot: Bot):
    if not _is_admin(message):
        return
    if (message.text or "").startswith("/cancel"):
        await state.clear()
        await message.answer("انصراف داده شد.")
        return
    if not (message.text or message.photo or message.document):
        await message.answer("⚠️ فقط متن، عکس یا فایل.")
        return
    content = {"text": message.text or message.caption or "",
               "photo": message.photo[-1].file_id if message.photo else None,
               "document": message.document.file_id if message.document else None}
    seg = (await state.get_data())["bc_segment"]
    n = len(ops.segment_users(seg))
    await state.update_data(bc_content=content)
    await state.set_state(OpsForm.broadcast_confirm)
    await message.answer("👁 پیش‌نمایش:")
    await _send_content(bot, message.chat.id, content)
    await message.answer(
        f"این پیام برای {_fa(n)} نفر ({ops.SEGMENTS[seg]}) ارسال شود؟",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ ارسال", callback_data="bc:send"),
            InlineKeyboardButton(text="❌ انصراف", callback_data="bc:cancel")]]))


async def _send_content(bot: Bot, chat_id: int, c: dict):
    if c.get("photo"):
        return await bot.send_photo(chat_id, c["photo"], caption=c["text"][:1000] or None)
    if c.get("document"):
        return await bot.send_document(chat_id, c["document"], caption=c["text"][:1000] or None)
    return await bot.send_message(chat_id, c["text"])


_broadcast_tasks: set = set()


@ops_router.callback_query(OpsForm.broadcast_confirm, F.data.in_({"bc:send", "bc:cancel"}))
async def cb_broadcast_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if not _is_admin_id(callback.from_user.id):
        await callback.answer()
        return
    data = await state.get_data()
    await state.clear()
    await callback.answer()
    if callback.data == "bc:cancel":
        await callback.message.answer("❌ ارسال لغو شد.")
        return
    users = ops.segment_users(data["bc_segment"])
    await callback.message.answer(f"📤 ارسال برای {_fa(len(users))} نفر شروع شد؛ در پایان گزارش داده می‌شود.")
    task = asyncio.create_task(run_broadcast(bot, users, data["bc_content"], callback.from_user.id))
    _broadcast_tasks.add(task)
    task.add_done_callback(_broadcast_tasks.discard)


async def run_broadcast(bot: Bot, users: list, content: dict, report_to: int) -> dict:
    sent = failed = blocked = 0
    for uid in users:
        try:
            await _send_content(bot, uid, content)
            sent += 1
        except TelegramForbiddenError:
            ops.mark_blocked(uid)
            blocked += 1
        except Exception as e:
            failed += 1
            logger.warning(f"[BROADCAST] ارسال به {uid} ناموفق: {e}")
        await asyncio.sleep(BROADCAST_DELAY)
    db.log_event("broadcast", report_to, f"{sent}/{len(users)}")
    try:
        await bot.send_message(report_to, f"📢 پیام همگانی تمام شد.\n✅ ارسال‌شده: {_fa(sent)}\n"
                                          f"🚫 ربات را مسدود کرده‌اند: {_fa(blocked)}\n⚠️ ناموفق: {_fa(failed)}")
    except Exception:
        pass
    return {"sent": sent, "blocked": blocked, "failed": failed}


# ══════════════════════════════════════════════════════════════════
# 🧾 رسیدهای پرداخت (حساب کاربری)
# ══════════════════════════════════════════════════════════════════
@ops_router.callback_query(F.data == "acc:receipts")
async def cb_receipts(callback: CallbackQuery):
    import billing
    from menu_handlers import fa_date
    pays = billing.user_payments(callback.from_user.id)
    await callback.answer()
    if not pays:
        await callback.message.answer("🧾 هنوز پرداختی برای شما ثبت نشده است.")
        return
    rows = [[InlineKeyboardButton(
        text=f"{billing.receipt_no(p['id'])} — {fa_date(p['created_at'])} — {_fa(format(p['amount_rial'] // 10, ','))} تومان",
        callback_data=f"rc:{p['id']}")] for p in pays]
    await callback.message.answer("🧾 *رسیدهای پرداخت* — برای دریافت PDF روی هر مورد بزنید:",
                                  reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@ops_router.callback_query(F.data.startswith("rc:"))
async def cb_receipt(callback: CallbackQuery):
    import billing
    p = billing.get_payment(int(callback.data.split(":")[1]))
    if not p or (p["user_id"] != callback.from_user.id and not _is_admin_id(callback.from_user.id)):
        await callback.answer("رسید پیدا نشد.")
        return
    await callback.answer("در حال آماده‌سازی...")
    if not await billing.send_receipt(callback.message.chat.id, p["id"]):
        await callback.message.answer("⚠️ آماده‌سازی رسید ناموفق بود؛ کمی بعد دوباره تلاش کنید.")


# ══════════════════════════════════════════════════════════════════
# پشتیبان‌گیری و آزمایش نقشه (ادمین)
# ══════════════════════════════════════════════════════════════════
async def send_backup(bot: Bot, chat_id: int) -> bool:
    from bale_file_sender import send_document_direct
    try:
        path = await asyncio.get_running_loop().run_in_executor(None, ops.make_backup, BACKUP_KEEP)
    except Exception as e:
        logger.error(f"[BACKUP] ساخت پشتیبان ناموفق: {e}", exc_info=True)
        await _notify_admin(bot, f"🛑 ساخت پشتیبان ناموفق بود: {e}")
        return False
    size = os.path.getsize(path) / 1024 / 1024
    users = db.query_one("SELECT COUNT(*) n FROM users")["n"]
    active = db.query_one("SELECT COUNT(*) n FROM subscriptions WHERE expires_at>?", (time.time(),))["n"]
    ok = await send_document_direct(
        chat_id, path, filename=os.path.basename(path),
        caption=(f"💾 پشتیبان {os.path.basename(path)[7:-4]} — {size:.1f} مگابایت\n"
                 f"👥 کاربران: {_fa(users)} | 💳 مشترکان فعال: {_fa(active)}\n"
                 "این فایل را در جای امنی نگه دارید (شامل اطلاعات مشترکان است)."))
    if not ok:
        await _notify_admin(bot, f"⚠️ ارسال فایل پشتیبان ({size:.1f} مگابایت) ناموفق بود؛ فایل روی سرور ذخیره شد:\n{path}")
    return ok


@ops_router.message(Command("backup"))
async def cmd_backup(message: Message, bot: Bot):
    if not _is_admin(message):
        return
    await message.answer("⏳ در حال ساخت پشتیبان...")
    await send_backup(bot, message.chat.id)


@ops_router.message(Command("maptest"))
async def cmd_maptest(message: Message):
    if not _is_admin(message):
        return
    import map_image
    from bale_file_sender import send_photo_direct
    path = await asyncio.get_running_loop().run_in_executor(
        None, lambda: map_image.fetch_to_file(35.6997, 51.3380) if map_image.fetch_png(35.6997, 51.3380, force=True)
        else None)
    if not path:
        await message.answer(f"🔴 سرویس نقشهٔ ایستای نشان پاسخ نداد:\n{map_image.last_error}\n\n"
                             "اگر کلید فعلی به این سرویس دسترسی ندارد، در پنل نشان سرویس «نقشهٔ ایستا» را به کلید "
                             "اضافه کنید یا کلید جدا را در NESHAN_STATIC_MAP_KEY قرار دهید. تا آن زمان گزارش‌ها بدون "
                             "نقشه ساخته می‌شوند.")
        return
    try:
        await send_photo_direct(message.chat.id, path, caption="🟢 سرویس نقشهٔ نشان کار می‌کند؛ نقشه در گزارش‌ها چاپ می‌شود.")
    finally:
        os.remove(path)


# ══════════════════════════════════════════════════════════════════
# پیگیری تست‌گیرندگان
# ══════════════════════════════════════════════════════════════════
async def send_trial_followups(bot: Bot) -> int:
    sent = 0
    for uid in ops.trial_followup_candidates(TRIAL_FOLLOWUP_DAYS):
        code_line = ""
        if TRIAL_FOLLOWUP_DISCOUNT > 0:
            try:
                d = discounts.personal_code(uid, TRIAL_FOLLOWUP_DISCOUNT, TRIAL_FOLLOWUP_CODE_DAYS, product="rv")
                code_line = (f"\n\n🎁 کد تخفیف مخصوص شما: *{d['code']}* — {_fa(TRIAL_FOLLOWUP_DISCOUNT)}٪ تخفیف، "
                             f"تا {_fa(TRIAL_FOLLOWUP_CODE_DAYS)} روز. هنگام خرید «🎟 کد تخفیف دارم» را بزنید.")
            except discounts.DiscountError as e:
                logger.warning(f"[FOLLOWUP] ساخت کد برای {uid} ناموفق: {e}")
        text = ("👋 امیدواریم استعلام‌های رایگان سامانهٔ ارزش منطقه‌ای برایتان مفید بوده باشد.\n\n"
                "برای ادامهٔ استفاده بدون محدودیت (همراه با سوابق، گزارش تجمیعی و QR تأیید اصالت روی گزارش‌ها) "
                "می‌توانید اشتراک ماهانه یا سالانه تهیه کنید." + code_line)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💳 خرید اشتراک",
                                                                         callback_data="acc:renew")]])
        try:
            await bot.send_message(uid, text, reply_markup=kb)
            sent += 1
            db.log_event("followup_trial", uid)
        except TelegramForbiddenError:
            ops.mark_blocked(uid)
        except Exception as e:
            logger.warning(f"[FOLLOWUP] پیام به {uid} ناموفق: {e}")
        ops.mark_followup(uid, "trial")   # حتی در صورت خطا، دوباره ارسال نمی‌شود
        await asyncio.sleep(BROADCAST_DELAY)
    return sent


# ══════════════════════════════════════════════════════════════════
# حلقهٔ پس‌زمینه
# ══════════════════════════════════════════════════════════════════
async def ops_loop(bot: Bot, tick: int = 60):
    await asyncio.sleep(20)
    last_probe = last_followup = 0.0
    while True:
        try:
            now = time.time()
            if ADMIN_ID and ops.backup_due(BACKUP_HOUR):
                if await send_backup(bot, ADMIN_ID):
                    ops.mark_backup_sent()
            if ops.tax_is_down() and now - last_probe >= TAX_PROBE_INTERVAL:
                last_probe = now
                await tax_probe(bot)
            if ADMIN_ID and access_control.free_for_all_just_ended():
                await bot.send_message(ADMIN_ID, "⏰ دورهٔ رایگان همگانی به پایان رسید؛ از این لحظه "
                                                 "دسترسی کاربران مثل قبل (اشتراک/اعتبار) است.\n"
                                                 "برای شروع دوباره: /freeall تعداد_روز")
            if now - last_followup >= 3600:
                last_followup = now
                n = await send_trial_followups(bot)
                if n:
                    logger.info(f"[FOLLOWUP] {n} پیام پیگیری تست ارسال شد")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[OPS] خطا در حلقهٔ پس‌زمینه: {e}", exc_info=True)
        await asyncio.sleep(tick)
