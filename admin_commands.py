# -*- coding: utf-8 -*-
"""
admin_commands.py — دستورهای مدیریتی، فقط برای ADMIN_ID.

/approve <آیدی_عددی>   — افزودن کاربر به لیست تاییدشده (دسترسی رایگان)
/revoke  <آیدی_عددی>   — حذف کاربر از لیست تاییدشده
/approved               — نمایش لیست کاربران تاییدشده
/subs                   — نمایش مشترکان فعال
/addsub <آیدی> <monthly|yearly> — فعال‌سازی/تمدید دستی اشتراک
/credit <آیدی> <تعداد>  — افزودن استعلام رایگان
دکمه‌های «✅ تایید / ❌ رد» زیر پیام درخواست تست — تایید = ۲ استعلام رایگان
"""
import logging

from aiogram import Router
from aiogram.filters import Command
import time

from aiogram import F
from aiogram.types import CallbackQuery, Message

from config import ADMIN_ID, SUBSCRIPTION_PLANS, TRIAL_CREDITS
import access_control

logger = logging.getLogger(__name__)

admin_router = Router()


def _is_admin(message: Message) -> bool:
    return ADMIN_ID is not None and message.from_user.id == ADMIN_ID


@admin_router.message(Command("approve"))
async def cmd_approve(message: Message):
    if not _is_admin(message):
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("فرمت درست: /approve آیدی_عددی_کاربر")
        return
    user_id = int(parts[1])
    added = await access_control.approve_user(user_id)
    if added:
        await message.answer(f"✅ کاربر {user_id} تایید شد و از این پس رایگان استفاده می‌کند.")
        try:
            await message.bot.send_message(
                user_id, "✅ دسترسی شما توسط مدیر تایید شد. از این پس بدون نیاز به پرداخت می‌توانید استعلام بگیرید."
            )
        except Exception as e:
            logger.warning(f"[ADMIN] اطلاع‌رسانی تایید به کاربر {user_id} ناموفق بود: {e}")
    else:
        await message.answer(f"ℹ️ کاربر {user_id} از قبل در لیست تاییدشده‌ها بود.")


@admin_router.message(Command("revoke"))
async def cmd_revoke(message: Message):
    if not _is_admin(message):
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("فرمت درست: /revoke آیدی_عددی_کاربر")
        return
    user_id = int(parts[1])
    removed = await access_control.revoke_user(user_id)
    if removed:
        await message.answer(f"🗑 تایید کاربر {user_id} لغو شد.")
    else:
        await message.answer(f"ℹ️ کاربر {user_id} اصلاً در لیست تاییدشده‌ها نبود.")


@admin_router.message(Command("approved"))
async def cmd_list_approved(message: Message):
    if not _is_admin(message):
        return
    ids = await access_control.list_approved()
    if not ids:
        await message.answer("لیست کاربران تاییدشده خالی است.")
        return
    text = "\n".join(f"• {uid}" for uid in ids)
    await message.answer(f"👥 کاربران تاییدشده ({len(ids)} نفر):\n{text}")



# ══════════════════════════════════════════════════════════════════
# تایید / رد درخواست تست (دکمه‌های اینلاین زیر پیام ادمین)
# ══════════════════════════════════════════════════════════════════
@admin_router.callback_query(F.data.startswith("trial_"))
async def cb_trial_decision(callback: CallbackQuery):
    if ADMIN_ID is None or callback.from_user.id != ADMIN_ID:
        await callback.answer("دسترسی ندارید.")
        return
    try:
        action, uid_raw = callback.data.split(":", 1)
        user_id = int(uid_raw)
    except (ValueError, AttributeError):
        await callback.answer("دادهٔ نامعتبر.")
        return
    approve = action == "trial_ok"

    req = await access_control.decide_trial_request(user_id, approve)
    if req is None:
        await callback.answer("این درخواست قبلاً بررسی شده است.")
        return

    from regional_value_handlers import CONTINUE_BUTTON
    from keyboards import main_menu_kb, nav_row
    from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

    if approve:
        await access_control.grant_free_retry(user_id, TRIAL_CREDITS)
        user_text = (
            "✅ اطلاعات شما توسط ادمین تایید شد.\n\n"
            f"🎁 {TRIAL_CREDITS} استعلام کاملاً رایگان برای شما در نظر گرفته شد.\n"
            "برای ادامهٔ استعلام، دکمهٔ «ادامه استعلام» را بزنید."
        )
        kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=CONTINUE_BUTTON)], nav_row()], resize_keyboard=True)
        result_line = f"✅ تایید شد — {TRIAL_CREDITS} استعلام رایگان ثبت شد"
    else:
        user_text = (
            "❌ متاسفانه اطلاعات ارسالی شما توسط ادمین تایید نشد.\n"
            "در صورت نیاز، می‌توانید اطلاعات صحیح را دوباره ارسال کنید."
        )
        kb = main_menu_kb()
        result_line = "❌ رد شد"

    try:
        await callback.bot.send_message(user_id, user_text, reply_markup=kb)
    except Exception as e:
        logger.warning(f"[ADMIN] اطلاع‌رسانی نتیجهٔ تست به کاربر {user_id} ناموفق بود: {e}")
        result_line += " (ارسال پیام به کاربر ناموفق بود)"

    try:
        await callback.message.edit_text(f"{callback.message.text}\n\n{result_line}")
    except Exception:
        try:
            await callback.message.answer(f"{result_line} — کاربر {user_id}")
        except Exception:
            pass
    await callback.answer(result_line)


@admin_router.message(Command("subs"))
async def cmd_subs(message: Message):
    if not _is_admin(message):
        return
    subs = await access_control.list_subscriptions()
    now = time.time()
    active = [(uid, s) for uid, s in subs.items() if s.get("expires_at", 0) > now]
    if not active:
        await message.answer("هیچ اشتراک فعالی وجود ندارد.")
        return
    lines = [
        f"• {uid} — {SUBSCRIPTION_PLANS.get(s.get('plan'), {}).get('title', s.get('plan'))} — "
        f"{-int(-(s['expires_at'] - now) // 86400)} روز باقی‌مانده"
        for uid, s in sorted(active, key=lambda x: x[1]["expires_at"])
    ]
    await message.answer(f"💳 مشترکان فعال ({len(active)}):\n" + "\n".join(lines))


@admin_router.message(Command("addsub"))
async def cmd_addsub(message: Message):
    if not _is_admin(message):
        return
    parts = (message.text or "").split()
    if len(parts) != 3 or not parts[1].isdigit() or parts[2] not in SUBSCRIPTION_PLANS:
        await message.answer("فرمت درست: /addsub آیدی_عددی monthly یا yearly")
        return
    user_id, plan = int(parts[1]), parts[2]
    p = SUBSCRIPTION_PLANS[plan]
    sub = await access_control.activate_subscription(user_id, plan, p["days"])
    days_left = -int(-(sub["expires_at"] - time.time()) // 86400)
    await message.answer(f"✅ {p['title']} برای کاربر {user_id} فعال شد ({days_left} روز).")
    try:
        await message.bot.send_message(user_id, f"✅ {p['title']} شما توسط مدیر فعال شد.")
    except Exception as e:
        logger.warning(f"[ADMIN] اطلاع‌رسانی اشتراک به کاربر {user_id} ناموفق بود: {e}")


@admin_router.message(Command("credit"))
async def cmd_credit(message: Message):
    if not _is_admin(message):
        return
    parts = (message.text or "").split()
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await message.answer("فرمت درست: /credit آیدی_عددی تعداد")
        return
    user_id, n = int(parts[1]), int(parts[2])
    await access_control.grant_free_retry(user_id, n)
    await message.answer(f"✅ {n} استعلام رایگان برای کاربر {user_id} ثبت شد.")
