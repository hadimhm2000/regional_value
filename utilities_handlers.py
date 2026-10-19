# -*- coding: utf-8 -*-
"""
utilities_handlers.py — گزینهٔ «🧰 خدمات کاربردی» منوی اصلی.

بخش‌ها:
  📈 خسارت تأخیر و مهریه  — damages_calc.py + damages_pdf.py (شاخص تورم بانک مرکزی)
  🧾 محاسبه هزینه دادرسی  — court_fee.py
  💰 محاسبه تمبر          — stamp_duty.py (تمبر و حق‌الوکاله / تمبر بر اساس حق‌الوکاله)
  🛠 ابزار فایل           — کاهش حجم عکس، تبدیل PDF به عکس
  ⚖️ محاسبه دیه           — diyeh_calc.py (هندلرها در legal_tools_handlers.py)
  👪 محاسبه سهم‌الارث     — inheritance_calc.py (هندلرها در legal_tools_handlers.py)

دسترسی (access_control.util_access):
  • هر بخش config.UTIL_FREE_PER_TOOL بار (پیش‌فرض ۱) رایگان است؛
  • پس از آن «اشتراک خدمات کاربردی» لازم است (ماهانه/سالانه، جدا از اشتراک ارزش منطقه‌ای)؛
  • کاربران تاییدشدهٔ ادمین (دسترسی رایگان دائمی) محدودیتی ندارند.
  یک «استفاده» = یک نتیجهٔ کامل (یک محاسبه / یک فایل پردازش‌شده).

بخش مدیر شاخص تورم (/cpi، /cpi_fetch، /cpi_pdf، /cpi_year، /cpi_import) و دریافت
خودکار ماهانهٔ شاخص از سایت بانک مرکزی (cpi_reminder_loop) هم در همین فایل است.
"""
from __future__ import annotations

import asyncio
import datetime
import io
import logging
import os
import tempfile
import time

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

import access_control
import court_fee as cf
import rate_limit
import damages_calc as dc
import db
import stamp_duty as sd
from config import ADMIN_ID, UTIL_FREE_PER_TOOL, UTIL_SUBSCRIPTION_PLANS
from keyboards import DISCOUNT_BUTTON, UTIL_BUTTON, discount_percent, is_back, is_button, is_restart, main_menu_kb

logger = logging.getLogger(__name__)

utilities_router = Router()

TOOLS = {
    "damages": "📈 خسارت تأخیر و مهریه",
    "court_fee": "🧾 محاسبه هزینه دادرسی",
    "stamp": "💰 محاسبه تمبر",
    "file_tools": "🛠 ابزار فایل",
    "diyeh": "⚖️ محاسبه دیه",
    "inheritance": "👪 محاسبه سهم‌الارث",
}
MAIN_MENU_TEXT = "🏠 منوی اصلی"
BACK_TEXT = "🔙 بازگشت"


class UtilForm(StatesGroup):
    menu = State()
    plan = State()
    payment = State()
    dmg_type = State()
    dmg_amount = State()
    dmg_due = State()
    dmg_calc_date = State()
    mahr_amount = State()
    mahr_year = State()
    mahr_pay = State()
    mahr_death = State()
    cf_amount = State()
    cf_category = State()
    stamp_type = State()
    stamp_amount = State()
    stamp_fee = State()
    ft_menu = State()
    ft_image = State()
    ft_pdf = State()
    dy_year = State()
    dy_method = State()
    dy_cat = State()
    dy_item = State()
    dy_count = State()
    dy_sex = State()
    dy_holy = State()
    dy_percent = State()
    dy_fraction = State()
    inh_marital = State()
    inh_wives = State()
    inh_board = State()
    inh_estate = State()


# ══════════════════════════════════════════════════════════════════
# کیبوردها و کمکی‌ها
# ══════════════════════════════════════════════════════════════════
def _kb(rows) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t) for t in r] for r in rows], resize_keyboard=True)


menu_kb = _kb([[TOOLS["damages"], TOOLS["court_fee"]], [TOOLS["stamp"], TOOLS["file_tools"]],
               [TOOLS["diyeh"], TOOLS["inheritance"]], [MAIN_MENU_TEXT]])
back_kb = _kb([[BACK_TEXT, MAIN_MENU_TEXT]])

DMG_LATE_TEXT = "💸 خسارت تأخیر تأدیه (چک، سفته، مطالبه وجه)"
DMG_MAHR_TEXT = "💍 مهریه به نرخ روز"
DMG_TODAY_TEXT = "📅 محاسبه تا امروز"
dmg_type_kb = _kb([[DMG_LATE_TEXT], [DMG_MAHR_TEXT], [BACK_TEXT, MAIN_MENU_TEXT]])
dmg_calc_date_kb = _kb([[DMG_TODAY_TEXT], [BACK_TEXT, MAIN_MENU_TEXT]])
MAHR_THIS_YEAR_TEXT = "📅 پرداخت امسال"
MAHR_DEATH_TEXT = "⚰️ مهریه از ترکه (فوت زوج)"
mahr_pay_kb = _kb([[MAHR_THIS_YEAR_TEXT], [MAHR_DEATH_TEXT], [BACK_TEXT, MAIN_MENU_TEXT]])
MAHR_PAY_PROMPT = ("📅 سال پرداخت مهریه را به شمسی وارد کنید، یا «پرداخت امسال» را بزنید.\n"
                   "اگر مهریه از ترکهٔ زوج متوفی مطالبه می‌شود، «مهریه از ترکه» را بزنید "
                   "(مادهٔ ۳ آیین‌نامه: تاریخ فوت مبنای محاسبه است).")
MAHR_DEATH_PROMPT = "⚰️ سال فوت زوج را به شمسی وارد کنید (مثال: 1402):"

CF_NEW_AMOUNT = "🔢 مبلغ جدید"
CF_CATEGORIES = {
    f"1️⃣ {cf.CATEGORY_TITLES[cf.CAT_FIRST]}": cf.CAT_FIRST,
    f"2️⃣ {cf.CATEGORY_TITLES[cf.CAT_APPEAL]}": cf.CAT_APPEAL,
    f"3️⃣ {cf.CATEGORY_TITLES[cf.CAT_RETRIAL]}": cf.CAT_RETRIAL,
}
cf_category_kb = _kb([[t] for t in CF_CATEGORIES] + [[CF_NEW_AMOUNT], [BACK_TEXT, MAIN_MENU_TEXT]])

STAMP_MALI = "1️⃣ دعوی مالی است و مبلغ خواسته را می‌دانم"
STAMP_GHEIR_MALI = "2️⃣ دعوی غیر مالی است"
STAMP_BY_FEE = "3️⃣ محاسبه تمبر بر اساس حق‌الوکاله"
stamp_type_kb = _kb([[STAMP_MALI], [STAMP_GHEIR_MALI], [STAMP_BY_FEE], [BACK_TEXT, MAIN_MENU_TEXT]])

FT_COMPRESS = "🖼 کاهش حجم عکس"
FT_PDF2IMG = "📄➡️🖼 تبدیل PDF به عکس"
ft_menu_kb = _kb([[FT_COMPRESS], [FT_PDF2IMG], [BACK_TEXT, MAIN_MENU_TEXT]])

DISCLAIMER = ("ℹ️ این محاسبه بر اساس شاخص تورم بانک مرکزی و صرفاً جهت اطلاع است؛ "
              "ملاک نهایی، محاسبهٔ دادگاه یا واحد اجرای احکام می‌باشد.")


def _fa(n) -> str:
    return str(n).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def _fmt(n: int) -> str:
    return f"{n:,}"


AMOUNT_HINT = "(می‌توانید سه‌رقم‌سه‌رقم جدا کنید — مثال: 258.000.000)"
AMOUNT_ERROR = "⚠️ مبلغ را فقط به‌صورت عدد و به *ریال* وارد کنید؛ جدا کردن سه‌رقم‌سه‌رقم مجاز است (مثال: 258.000.000):"


def _amount_line(amount: int) -> str:
    """تأیید مبلغ به ریال و تومان همراه سؤال بعدی؛ اگر اشتباه است کاربر «بازگشت» می‌زند."""
    return dc.amount_confirm_line(amount, BACK_TEXT) + "\n\n"


def _jdate(t) -> str:
    return f"{t[0]:04d}/{t[1]:02d}/{t[2]:02d}"


def _is_main(text: str) -> bool:
    return bool(text) and (is_button(text, MAIN_MENU_TEXT) or is_restart(text))


def _tmp(name: str) -> str:
    return os.path.join(tempfile.gettempdir(), f"util_{time.time_ns()}_{name}")


async def _to_main(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🏠 منوی اصلی", reply_markup=main_menu_kb())


# ══════════════════════════════════════════════════════════════════
# دسترسی: اعتبار رایگان هر بخش ← اشتراک خدمات کاربردی
# ══════════════════════════════════════════════════════════════════
def _plan_button(key: str, percent: int = 0) -> str:
    p = UTIL_SUBSCRIPTION_PLANS[key]
    if percent:
        from payment import plan_price
        return f"🔹 {p['title']} — {_toman(plan_price('util', key, percent)[0])} تومان ({_fa(percent)}٪ تخفیف)"
    return f"🔹 {p['title']} — {_toman(p['toman'])} تومان"


def _toman(n: int) -> str:
    return _fa(f"{n:,}")


def _plan_kb(percent: int = 0) -> ReplyKeyboardMarkup:
    rows = [[_plan_button("monthly", percent)], [_plan_button("yearly", percent)]]
    if not percent:
        rows.append([DISCOUNT_BUTTON])
    return _kb(rows + [[BACK_TEXT, MAIN_MENU_TEXT]])


def _plans_text() -> str:
    m, y = UTIL_SUBSCRIPTION_PLANS["monthly"], UTIL_SUBSCRIPTION_PLANS["yearly"]
    return (f"🔹 اشتراک ماهانه: {_toman(m['toman'])} تومان\n"
            f"🔹 اشتراک سالانه: {_toman(y['toman'])} تومان")


async def _status_line(user_id: int, tool: str) -> str:
    access = await access_control.util_access(user_id, tool)
    if access == "free_period":
        from menu_handlers import fa_date
        return f"🎉 استفاده تا {fa_date(access_control.free_for_all_until())} برای همه رایگان است.\n\n"
    if access == "subscribed":
        sub = await access_control.get_own_subscription(user_id, "util")
        from menu_handlers import fa_date
        return f"✅ اشتراک خدمات کاربردی فعال تا {fa_date(sub['expires_at'])}\n\n"
    if access == "free_credit":
        left = access_control.util_free_left(user_id, tool)
        return (f"🎁 {_fa(left)} استفادهٔ رایگان از این بخش دارید؛ پس از آن برای ادامه، "
                "اشتراک خدمات کاربردی لازم است.\n\n")
    return ""


async def _gate(message: Message, state: FSMContext, tool: str) -> bool:
    """True = اجازهٔ استفاده؛ False = پیشنهاد خرید اشتراک نمایش داده شد."""
    user_id = message.chat.id
    if await access_control.util_access(user_id, tool) != "none":
        return True
    await state.update_data(util_tool=tool)
    await message.answer(
        f"🔔 از استفادهٔ رایگان بخش «{TOOLS[tool]}» استفاده کرده‌اید.\n\n"
        "برای ادامه، *اشتراک خدمات کاربردی* را تهیه کنید؛ یک اشتراک برای هر چهار بخش معتبر است:\n"
        + "\n".join(f"• {t}" for t in TOOLS.values()) + "\n\n" + _plans_text()
        + "\n\n(این اشتراک جدا از اشتراک استعلام ارزش منطقه‌ای است.)",
        reply_markup=_plan_kb(discount_percent(await state.get_data(), "util")))
    await state.set_state(UtilForm.plan)
    db.log_event("util_paywall", user_id, tool)
    return False


def _used(user_id: int, tool: str, detail: str = ""):
    """ثبت یک استفادهٔ کامل (مصرف اعتبار رایگان؛ برای مشترکان فقط آمار)."""
    access_control.record_util_use(user_id, tool)
    db.log_event(f"util_{tool}", user_id, detail or None)


# ══════════════════════════════════════════════════════════════════
# ورود و منوی خدمات کاربردی
# ══════════════════════════════════════════════════════════════════
@utilities_router.message(lambda m: bool(m.text) and is_button(m.text, UTIL_BUTTON))
async def utilities_entry(message: Message, state: FSMContext):
    await state.clear()
    await _show_menu(message, state)


async def _show_menu(message: Message, state: FSMContext, prefix: str = ""):
    uid = message.chat.id
    approved = await access_control.is_approved(uid)
    sub = await access_control.get_own_subscription(uid, "util")
    free_until = access_control.free_for_all_until()
    if approved:
        status = "✅ دسترسی رایگان دائمی"
    elif free_until:
        from menu_handlers import fa_date
        status = f"🎉 همهٔ بخش‌ها تا {fa_date(free_until)} برای همه رایگان است"
    elif sub:
        from menu_handlers import fa_date
        status = f"✅ اشتراک خدمات کاربردی فعال تا {fa_date(sub['expires_at'])}"
    else:
        free = [t for k, t in TOOLS.items() if access_control.util_free_left(uid, k) > 0]
        status = (f"🎁 هر بخش {_fa(UTIL_FREE_PER_TOOL)} بار رایگان است"
                  + (f" (باقی‌مانده: {'، '.join(free)})" if free else " — استفادهٔ رایگان همهٔ بخش‌ها انجام شده است"))
    await message.answer(
        prefix + "🧰 *خدمات کاربردی*\n\n" + status + "\n\n" + "یکی از بخش‌ها را انتخاب کنید:",
        reply_markup=menu_kb)
    await state.set_state(UtilForm.menu)


@utilities_router.message(UtilForm.menu)
async def utilities_menu(message: Message, state: FSMContext):
    text = message.text or ""
    if _is_main(text) or is_back(text):
        await _to_main(message, state)
        return
    tool = next((k for k, t in TOOLS.items() if is_button(text, t)), None)
    if not tool:
        await message.answer("لطفاً یکی از بخش‌ها را انتخاب کنید:", reply_markup=menu_kb)
        return
    if not await _gate(message, state, tool):
        return
    await ENTRY[tool](message, state)


async def _back_to_menu(message: Message, state: FSMContext):
    await _show_menu(message, state)


# ══════════════════════════════════════════════════════════════════
# خرید اشتراک خدمات کاربردی
# ══════════════════════════════════════════════════════════════════
async def offer_plans(message: Message, state: FSMContext, prefix: str = ""):
    """نمایش پلن‌ها (از «حساب کاربری» یا یادآوری تمدید)."""
    data = await state.get_data()
    percent = discount_percent(data, "util")
    note = f"🎟 کد تخفیف {data['discount']['code']} ({_fa(percent)}٪) اعمال شد.\n\n" if percent else ""
    await message.answer(prefix + note + "💳 *اشتراک خدمات کاربردی*\n\n" + _plans_text()
                         + "\n\nاشتراک مورد نظر را انتخاب کنید:", reply_markup=_plan_kb(percent))
    await state.set_state(UtilForm.plan)


@utilities_router.message(UtilForm.plan)
async def util_plan(message: Message, state: FSMContext, bot: Bot):
    text = message.text or ""
    if _is_main(text):
        await _to_main(message, state)
        return
    if is_back(text):
        await _show_menu(message, state)
        return
    data = await state.get_data()
    percent = discount_percent(data, "util")
    if text == DISCOUNT_BUTTON:
        from ops_handlers import ask_discount_code
        await ask_discount_code(message, state, "util")
        return
    plan = next((k for k in UTIL_SUBSCRIPTION_PLANS if text.strip() == _plan_button(k, percent)), None)
    if not plan:
        plan = "monthly" if "ماهانه" in text else "yearly" if ("سالانه" in text or "سالیانه" in text) else None
    if not plan:
        await message.answer("⚠️ لطفاً یکی از گزینه‌های ماهانه یا سالانه را انتخاب کنید.", reply_markup=_plan_kb(percent))
        return
    from payment import send_subscription_invoice
    if await send_subscription_invoice(bot, message.from_user.id, plan, product="util",
                                       discount=data.get("discount") if percent else None):
        await state.set_state(UtilForm.payment)
        await message.answer(
            f"💳 فاکتور {UTIL_SUBSCRIPTION_PLANS[plan]['title']} ارسال شد؛ لطفاً آن را پرداخت کنید.\n"
            "پس از پرداخت، اشتراک خودکار فعال می‌شود.", reply_markup=back_kb)
    else:
        if ADMIN_ID:
            try:
                await bot.send_message(ADMIN_ID, f"⚠️ ارسال فاکتور خدمات کاربردی برای {message.from_user.id} شکست خورد.")
            except Exception:
                pass
        await message.answer("⚠️ در حال حاضر امکان ساخت فاکتور پرداخت نیست. لطفاً کمی بعد تلاش کنید.",
                             reply_markup=_plan_kb(percent))


@utilities_router.message(UtilForm.payment)
async def util_payment_wait(message: Message, state: FSMContext):
    text = message.text or ""
    if _is_main(text):
        await _to_main(message, state)
        return
    if is_back(text):
        await offer_plans(message, state)
        return
    await message.answer("⏳ منتظر پرداخت فاکتور هستیم.", reply_markup=back_kb)


async def on_util_payment(message: Message, state: FSMContext, bot: Bot, payload: dict):
    """پرداخت موفق اشتراک خدمات کاربردی (از هندلر successful_payment صدا زده می‌شود)."""
    user_id = message.from_user.id
    plan_key = payload.get("plan")
    plan = UTIL_SUBSCRIPTION_PLANS.get(plan_key)
    if not plan:
        logger.error(f"[UTIL-PAYMENT] پلن نامعتبر در پرداخت کاربر {user_id}: {payload}")
        if ADMIN_ID:
            await bot.send_message(ADMIN_ID, f"⚠️ پرداخت خدمات کاربردی با پلن ناشناخته از {user_id}: {payload}")
        await message.answer("✅ پرداخت دریافت شد؛ فعال‌سازی با خطا مواجه شد و به ادمین اطلاع داده شد.")
        return
    import billing
    sub = await billing.complete_payment(message, bot, "util", plan_key, payload)
    if not sub:
        return
    from menu_handlers import fa_date
    await state.clear()
    await _show_menu(message, state, f"✅ پرداخت تایید شد و {plan['title']} تا {fa_date(sub['expires_at'])} فعال است.\n\n")


# ══════════════════════════════════════════════════════════════════
# 📈 خسارت تأخیر تأدیه و مهریه به نرخ روز
# ══════════════════════════════════════════════════════════════════
async def damages_entry(message: Message, state: FSMContext, prefix: str = ""):
    status = await _status_line(message.chat.id, "damages")
    await message.answer(prefix + "📈 *محاسبهٔ خسارت تأخیر تأدیه و مهریه به نرخ روز*\n\n"
                         f"{status}نوع محاسبه را انتخاب کنید:", reply_markup=dmg_type_kb)
    await state.set_state(UtilForm.dmg_type)


@utilities_router.message(UtilForm.dmg_type)
async def damages_type(message: Message, state: FSMContext):
    text = message.text or ""
    if _is_main(text):
        await _to_main(message, state)
        return
    if is_back(text):
        await _back_to_menu(message, state)
        return
    if text not in (DMG_LATE_TEXT, DMG_MAHR_TEXT):
        await message.answer("لطفاً یکی از گزینه‌های زیر را انتخاب کنید:", reply_markup=dmg_type_kb)
        return
    if not await _gate(message, state, "damages"):
        return
    if text == DMG_LATE_TEXT:
        await message.answer("💸 مبلغ اصل خواسته (مبلغ چک/سفته/بدهی) را به *ریال* وارد کنید:\n" + AMOUNT_HINT,
                             reply_markup=back_kb)
        await state.set_state(UtilForm.dmg_amount)
    else:
        await message.answer("💍 مبلغ مهریهٔ مندرج در عقدنامه را به *ریال* وارد کنید:\n" + AMOUNT_HINT,
                             reply_markup=back_kb)
        await state.set_state(UtilForm.mahr_amount)


@utilities_router.message(UtilForm.dmg_amount)
async def dmg_amount(message: Message, state: FSMContext):
    text = message.text or ""
    if _is_main(text):
        await _to_main(message, state)
        return
    if is_back(text):
        await damages_entry(message, state)
        return
    amount = dc.parse_amount(text)
    if not amount:
        await message.answer(AMOUNT_ERROR)
        return
    await state.update_data(dmg_amount=amount)
    await message.answer(_amount_line(amount) + "📅 تاریخ سررسید را به شمسی وارد کنید (مثال: 1402/08/15):",
                         reply_markup=back_kb)
    await state.set_state(UtilForm.dmg_due)


@utilities_router.message(UtilForm.dmg_due)
async def dmg_due(message: Message, state: FSMContext):
    text = message.text or ""
    if _is_main(text):
        await _to_main(message, state)
        return
    if is_back(text):
        await message.answer("💸 مبلغ اصل خواسته را به *ریال* وارد کنید:\n" + AMOUNT_HINT, reply_markup=back_kb)
        await state.set_state(UtilForm.dmg_amount)
        return
    due = dc.parse_jalali_date(text)
    if not due:
        await message.answer("⚠️ تاریخ نامعتبر است. به شکل سال/ماه/روز وارد کنید (مثال: 1402/08/15):")
        return
    await state.update_data(dmg_due=list(due))
    await message.answer("📅 تاریخ پرداخت (خسارت تا چه تاریخی محاسبه شود؟)\n"
                         "تاریخ را به شمسی وارد کنید یا «تا امروز» را بزنید:", reply_markup=dmg_calc_date_kb)
    await state.set_state(UtilForm.dmg_calc_date)


async def _send_pdf_result(message: Message, build, filename: str, caption: str, fallback_text: str):
    """نتیجه به‌صورت PDF؛ اگر ساخت/ارسال نشد، متن نتیجه ارسال می‌شود."""
    from bale_file_sender import send_document_direct
    path = _tmp("result.pdf")
    sent = False
    try:
        ok = await asyncio.get_running_loop().run_in_executor(None, build, path)
        if ok and os.path.exists(path):
            sent = bool(await send_document_direct(message.chat.id, path, filename=filename, caption=caption))
    except Exception as e:
        logger.error(f"[DAMAGES] خطا در ساخت/ارسال PDF: {e}", exc_info=True)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    if not sent:
        await message.answer(fallback_text)


@utilities_router.message(UtilForm.dmg_calc_date)
async def dmg_calc_date(message: Message, state: FSMContext):
    text = message.text or ""
    if _is_main(text):
        await _to_main(message, state)
        return
    if is_back(text):
        await message.answer("📅 تاریخ سررسید را به شمسی وارد کنید:", reply_markup=back_kb)
        await state.set_state(UtilForm.dmg_due)
        return
    if text == DMG_TODAY_TEXT:
        calc_date = dc.today_jalali()
    else:
        calc_date = dc.parse_jalali_date(text)
        if not calc_date:
            await message.answer("⚠️ تاریخ نامعتبر است. به شکل سال/ماه/روز وارد کنید یا «تا امروز» را بزنید:")
            return
    data = await state.get_data()
    due, pay = tuple(data["dmg_due"]), tuple(calc_date)
    try:
        r = dc.calc_late_payment(data["dmg_amount"], due, pay)
    except dc.CpiMissing as e:
        logger.warning(f"[DAMAGES] شاخص ناقص: {e}")
        await message.answer("⚠️ شاخص تورم لازم برای این محاسبه هنوز در ربات ثبت نشده است.\n"
                             "مدیر مطلع شد؛ لطفاً کمی بعد دوباره تلاش کنید.")
        await _notify_admin_missing(message.bot, str(e))
        await damages_entry(message, state)
        return
    except ValueError as e:
        await message.answer(f"⚠️ {e}")
        return

    _used(message.chat.id, "damages", "late")
    import damages_pdf
    note = ""
    approx = ("⚠️ شاخص ماه سررسید (۱۳۶۱ تا ۱۳۶۹) فقط با یک رقم اعشار منتشر شده؛ نتیجه تقریبی است.\n"
              if r.get("approximate") else "")
    if r["used_latest_available"]:
        note = (f"\n🔸 شاخص ماه پرداخت ({r['pay_key']}) هنوز منتشر نشده؛ از نزدیک‌ترین "
                f"شاخص موجود ({r['target_key']}) استفاده شد.\n")
    caption = ("📄 گزارش محاسبهٔ خسارت تأخیر تأدیه\n\n"
               f"💵 مبلغ دین: {_fmt(r['amount'])} ریال\n"
               f"📈 اصل دین و خسارت: {_fmt(r['updated_amount'])} ریال\n"
               f"💸 خسارت به تنهایی: {_fmt(r['damages'])} ریال\n"
               + (f"🔸 شاخص ماه پرداخت هنوز منتشر نشده؛ شاخص {r['target_key']} استفاده شد.\n"
                  if r["used_latest_available"] else "") + approx)
    await _send_pdf_result(
        message, lambda path: damages_pdf.build_late_payment_pdf(
            path, r, due, pay, issuer=access_control.issuer_line(message.chat.id)),
        "خسارت_تاخیر_تادیه.pdf", caption,
        "📊 *نتیجهٔ محاسبهٔ خسارت تأخیر تأدیه*\n\n"
        f"💵 مبلغ دین: {_fmt(r['amount'])} ریال\n"
        f"📅 سررسید: {_jdate(due)} (شاخص {r['due_key']}: {r['base_index']:g})\n"
        f"📅 پرداخت: {_jdate(pay)} (شاخص {r['target_key']}: {r['target_index']:g})\n\n"
        f"📈 اصل دین و خسارت: *{_fmt(r['updated_amount'])} ریال*\n"
        f"💸 خسارت به تنهایی: *{_fmt(r['damages'])} ریال*\n{note}\n{approx}"
        f"فرمول: مبلغ × (شاخص {r['target_key']} ÷ شاخص {r['due_key']})\n\n{DISCLAIMER}")
    await damages_entry(message, state, "برای محاسبهٔ دیگر:\n\n")


@utilities_router.message(UtilForm.mahr_amount)
async def mahr_amount(message: Message, state: FSMContext):
    text = message.text or ""
    if _is_main(text):
        await _to_main(message, state)
        return
    if is_back(text):
        await damages_entry(message, state)
        return
    amount = dc.parse_amount(text)
    if not amount:
        await message.answer(AMOUNT_ERROR)
        return
    await state.update_data(mahr_amount=amount)
    await message.answer(_amount_line(amount) + "📅 سال وقوع عقد را به شمسی وارد کنید (مثال: 1385):",
                         reply_markup=back_kb)
    await state.set_state(UtilForm.mahr_year)


@utilities_router.message(UtilForm.mahr_year)
async def mahr_year(message: Message, state: FSMContext):
    text = message.text or ""
    if _is_main(text):
        await _to_main(message, state)
        return
    if is_back(text):
        await message.answer("💍 مبلغ مهریه را به *ریال* وارد کنید:\n" + AMOUNT_HINT, reply_markup=back_kb)
        await state.set_state(UtilForm.mahr_amount)
        return
    raw = dc.normalize_digits(text)
    if not raw.isdigit() or not (1300 <= int(raw) <= 1500):
        await message.answer("⚠️ سال را به‌صورت چهار رقمی شمسی وارد کنید (مثال: 1385):")
        return
    await state.update_data(mahr_year=int(raw))
    await message.answer(MAHR_PAY_PROMPT, reply_markup=mahr_pay_kb)
    await state.set_state(UtilForm.mahr_pay)


def _parse_year(text: str) -> int | None:
    raw = dc.normalize_digits(text)
    return int(raw) if raw.isdigit() and 1300 <= int(raw) <= 1500 else None


async def _mahr_back_to_year(message: Message, state: FSMContext):
    await message.answer("📅 سال وقوع عقد را به شمسی وارد کنید (مثال: 1385):", reply_markup=back_kb)
    await state.set_state(UtilForm.mahr_year)


@utilities_router.message(UtilForm.mahr_pay)
async def mahr_pay(message: Message, state: FSMContext):
    text = message.text or ""
    if _is_main(text):
        await _to_main(message, state)
        return
    if is_back(text):
        await _mahr_back_to_year(message, state)
        return
    if is_button(text, MAHR_DEATH_TEXT):
        await message.answer(MAHR_DEATH_PROMPT, reply_markup=back_kb)
        await state.set_state(UtilForm.mahr_death)
        return
    if is_button(text, MAHR_THIS_YEAR_TEXT):
        year = dc.today_jalali()[0]
    else:
        year = _parse_year(text)
        if not year:
            await message.answer("⚠️ سال را به‌صورت چهار رقمی شمسی وارد کنید (مثال: 1404) "
                                 "یا یکی از دکمه‌ها را بزنید.", reply_markup=mahr_pay_kb)
            return
    await _mahr_finish(message, state, year, "payment")


@utilities_router.message(UtilForm.mahr_death)
async def mahr_death(message: Message, state: FSMContext):
    text = message.text or ""
    if _is_main(text):
        await _to_main(message, state)
        return
    if is_back(text):
        await message.answer(MAHR_PAY_PROMPT, reply_markup=mahr_pay_kb)
        await state.set_state(UtilForm.mahr_pay)
        return
    year = _parse_year(text)
    if not year:
        await message.answer("⚠️ " + MAHR_DEATH_PROMPT.replace("⚰️ ", ""))
        return
    await _mahr_finish(message, state, year, "death")


async def _mahr_finish(message: Message, state: FSMContext, pay_year: int, basis: str):
    data = await state.get_data()
    marriage_year = data["mahr_year"]
    try:
        r = dc.calc_mahrieh(data["mahr_amount"], marriage_year, payment_year=pay_year, basis=basis)
    except dc.CpiMissing as e:
        logger.warning(f"[MAHRIEH] شاخص ناقص: {e}")
        first = dc.first_annual_year()
        if first and marriage_year < first:
            await message.answer(f"⚠️ شاخص سالانهٔ سال‌های قبل از {first} هنوز در ربات ثبت نشده است "
                                 f"(فعلاً سال عقد {first} به بعد قابل محاسبه است).\n"
                                 "مدیر مطلع شد.")
        else:
            await message.answer("⚠️ شاخص تورم لازم برای این سال هنوز در ربات ثبت نشده است.\n"
                                 "مدیر مطلع شد؛ لطفاً کمی بعد دوباره تلاش کنید.")
        await _notify_admin_missing(message.bot, str(e))
        await damages_entry(message, state)
        return
    except ValueError as e:
        # خطای سال پرداخت/فوت: همان مرحله دوباره پرسیده می‌شود
        if basis == "death":
            await message.answer(f"⚠️ {e}\n\n{MAHR_DEATH_PROMPT}", reply_markup=back_kb)
        else:
            await message.answer(f"⚠️ {e}\n\n{MAHR_PAY_PROMPT}", reply_markup=mahr_pay_kb)
        return

    _used(message.chat.id, "damages", "mahrieh")
    import damages_pdf
    death = basis == "death"
    event_line = (f"⚰️ سال فوت زوج: {r['payment_year']} (مادهٔ ۳ آیین‌نامه)" if death
                  else f"📅 سال پرداخت: {r['payment_year']}")
    caption = ("📄 گزارش محاسبهٔ مهریه به نرخ روز\n\n"
               f"💵 مبلغ مهریه: {_fmt(r['amount'])} ریال (سال عقد {r['marriage_year']})\n"
               f"{event_line}\n"
               f"💍 مهریه به نرخ روز: {_fmt(r['updated_amount'])} ریال\n")
    await _send_pdf_result(
        message, lambda path: damages_pdf.build_mahrieh_pdf(path, r, issuer=access_control.issuer_line(message.chat.id)),
        "مهریه_به_نرخ_روز.pdf", caption,
        "📊 *نتیجهٔ محاسبهٔ مهریه به نرخ روز*\n\n"
        f"💵 مبلغ مهریه: {_fmt(r['amount'])} ریال\n"
        f"📅 سال عقد: {r['marriage_year']} (شاخص سالانه: {r['base_index']:g})\n"
        f"{event_line}\n"
        f"📅 سال مبنا (سال قبل از {'فوت' if death else 'پرداخت'}): {r['target_year']} "
        f"(شاخص سالانه: {r['target_index']:g})\n\n"
        f"💍 مهریه به نرخ روز: *{_fmt(r['updated_amount'])} ریال*\n\n{DISCLAIMER}")
    await damages_entry(message, state, "برای محاسبهٔ دیگر:\n\n")


# ══════════════════════════════════════════════════════════════════
# 🧾 محاسبه هزینه دادرسی
# ══════════════════════════════════════════════════════════════════
CF_PROMPT = ("🧾 *محاسبه هزینه دادرسی*\n\n"
             "💵 بهای خواسته یا محکوم‌به را به *ریال* وارد کنید:\n"
             "(فقط عدد؛ می‌توانید سه‌رقم‌سه‌رقم جدا کنید — مثال: 258.000.000)")


async def court_fee_entry(message: Message, state: FSMContext, prefix: str = ""):
    status = await _status_line(message.chat.id, "court_fee")
    await message.answer(prefix + CF_PROMPT.replace("\n\n", "\n\n" + status, 1), reply_markup=back_kb)
    await state.set_state(UtilForm.cf_amount)


@utilities_router.message(UtilForm.cf_amount)
async def cf_amount(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if _is_main(text):
        await _to_main(message, state)
        return
    if is_back(text):
        await _back_to_menu(message, state)
        return
    amount = dc.parse_amount(text)
    if not amount:
        await message.answer(AMOUNT_ERROR)
        return
    if not await _gate(message, state, "court_fee"):
        return
    # یک «استفاده» = یک مبلغ؛ دیدن مراحل مختلف دادرسی برای همان مبلغ، استفادهٔ جدید نیست
    await state.update_data(cf_amount=amount, cf_counted=False)
    await message.answer(_amount_line(amount) + "مرحلهٔ دادرسی را انتخاب کنید:",
                         reply_markup=cf_category_kb)
    await state.set_state(UtilForm.cf_category)


@utilities_router.message(UtilForm.cf_category)
async def cf_category(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if _is_main(text):
        await _to_main(message, state)
        return
    if is_back(text):
        await _back_to_menu(message, state)
        return
    if text == CF_NEW_AMOUNT:
        if not await _gate(message, state, "court_fee"):
            return
        await court_fee_entry(message, state)
        return
    category = CF_CATEGORIES.get(text)
    data = await state.get_data()
    if not category or not data.get("cf_amount"):
        await message.answer("لطفاً یکی از گزینه‌های زیر را انتخاب کنید:", reply_markup=cf_category_kb)
        return
    result = cf.calc_court_fee(int(data["cf_amount"]), category)
    if not data.get("cf_counted"):
        _used(message.chat.id, "court_fee", category)
        await state.update_data(cf_counted=True)
    await message.answer(cf.format_result_fa(result) + "\n\nبرای مرحلهٔ دیگر، گزینهٔ آن را بزنید:",
                         reply_markup=cf_category_kb)


# ══════════════════════════════════════════════════════════════════
# 💰 محاسبه تمبر
# ══════════════════════════════════════════════════════════════════
async def stamp_entry(message: Message, state: FSMContext, prefix: str = ""):
    status = await _status_line(message.chat.id, "stamp")
    await message.answer(prefix + f"💰 *محاسبه تمبر*\n\n{status}لطفاً یکی از گزینه‌های زیر را انتخاب کنید:",
                         reply_markup=stamp_type_kb)
    await state.set_state(UtilForm.stamp_type)


@utilities_router.message(UtilForm.stamp_type)
async def stamp_type(message: Message, state: FSMContext):
    text = message.text or ""
    if _is_main(text):
        await _to_main(message, state)
        return
    if is_back(text):
        await _back_to_menu(message, state)
        return
    if text == STAMP_GHEIR_MALI:
        # اطلاع ثابت — استفاده محسوب نمی‌شود
        await message.answer("📋 *تمبر دعوی غیر مالی:*\n\nمبلغ *200,000 ریال* به ازای هر خواسته می‌باشد.",
                             reply_markup=stamp_type_kb)
        return
    if text not in (STAMP_MALI, STAMP_BY_FEE):
        await message.answer("⚠️ لطفاً یکی از گزینه‌های موجود را انتخاب کنید:", reply_markup=stamp_type_kb)
        return
    if not await _gate(message, state, "stamp"):
        return
    if text == STAMP_BY_FEE:
        await message.answer("💵 لطفاً *مبلغ حق‌الوکاله* را به *ریال* وارد فرمایید:\n" + AMOUNT_HINT,
                             reply_markup=back_kb)
        await state.set_state(UtilForm.stamp_fee)
    else:
        await message.answer("💵 لطفاً *مبلغ خواسته* را به *ریال* وارد فرمایید:\n" + AMOUNT_HINT,
                             reply_markup=back_kb)
        await state.set_state(UtilForm.stamp_amount)


@utilities_router.message(UtilForm.stamp_amount)
async def stamp_amount(message: Message, state: FSMContext):
    text = message.text or ""
    if _is_main(text):
        await _to_main(message, state)
        return
    if is_back(text):
        await stamp_entry(message, state)
        return
    amount = dc.parse_amount(text)
    if not amount:
        await message.answer(AMOUNT_ERROR)
        return
    try:
        result = sd.calculate_stamp_duty(amount)
    except ValueError as e:
        await message.answer(f"⚠️ خطا در محاسبه: {e}")
        return
    _used(message.chat.id, "stamp", "claim")
    await message.answer(dc.amount_confirm_line(amount) + "\n\n"
                         f"✅ *نتیجه محاسبه تمبر مالیاتی:*\n\n{sd.format_result_fa(amount, result)}")
    await stamp_entry(message, state, "برای محاسبهٔ دیگر:\n\n")


@utilities_router.message(UtilForm.stamp_fee)
async def stamp_fee(message: Message, state: FSMContext):
    text = message.text or ""
    if _is_main(text):
        await _to_main(message, state)
        return
    if is_back(text):
        await stamp_entry(message, state)
        return
    fee = dc.parse_amount(text)
    if not fee:
        await message.answer(AMOUNT_ERROR.replace("مبلغ را", "مبلغ حق‌الوکاله را"))
        return
    result = sd.calculate_stamp_from_lawyer_fee(fee)
    _used(message.chat.id, "stamp", "lawyer_fee")
    await message.answer(dc.amount_confirm_line(fee) + "\n\n" + sd.format_lawyer_fee_result_fa(fee, result))
    await stamp_entry(message, state, "برای محاسبهٔ دیگر:\n\n")


# ══════════════════════════════════════════════════════════════════
# 🛠 ابزار فایل: کاهش حجم عکس / تبدیل PDF به عکس
# ══════════════════════════════════════════════════════════════════
MAX_PDF_PAGES = 40
TARGET_IMAGE_KB = 500
PAGE_SEND_DELAY = 0.35
TALL_PAGE_ASPECT_THRESHOLD = 1.6
BLANK_ROW_BRIGHTNESS = 250
MIN_GAP_PX = 25
MIN_SEGMENT_PX = 150
# محدودیت‌های ضد فایل مخرب (پرکردن حافظه/CPU سرور)
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "20"))          # حجم فایل ارسالی
MAX_IMAGE_PIXELS = 50_000_000                                         # عکس ورودی (~۷۰۷۰×۷۰۷۰)
MAX_PAGE_PIXELS = 12_000_000                                          # هر صفحهٔ رندرشدهٔ PDF
MAX_TOTAL_RENDER_PIXELS = 160_000_000                                 # جمع پیکسل همهٔ صفحات PDF


def _too_big(file_obj) -> bool:
    """آیا فایل ارسالی (طبق file_size اعلام‌شدهٔ بله) از سقف مجاز بزرگ‌تر است؟"""
    size = getattr(file_obj, "file_size", None) or 0
    return size > MAX_UPLOAD_MB * 1024 * 1024


def _too_big_text() -> str:
    return f"⚠️ حجم فایل بیشتر از حد مجاز ({_fa(MAX_UPLOAD_MB)} مگابایت) است؛ لطفاً فایل کوچک‌تری ارسال فرمایید."


async def file_tools_entry(message: Message, state: FSMContext, prefix: str = ""):
    status = await _status_line(message.chat.id, "file_tools")
    await message.answer(
        prefix + f"🛠 *ابزار فایل*\n\n{status}"
        "🖼 *کاهش حجم عکس* — عکس را ارسال کنید تا حجم آن کاهش یابد.\n"
        "📄➡️🖼 *تبدیل PDF به عکس* — هر صفحهٔ PDF به‌صورت یک عکس جداگانه ارسال می‌شود.",
        reply_markup=ft_menu_kb)
    await state.set_state(UtilForm.ft_menu)


@utilities_router.message(UtilForm.ft_menu)
async def ft_menu(message: Message, state: FSMContext):
    text = message.text or ""
    if _is_main(text):
        await _to_main(message, state)
        return
    if is_back(text):
        await _back_to_menu(message, state)
        return
    if text == FT_COMPRESS:
        await message.answer("🖼 لطفاً عکس مورد نظر را ارسال فرمایید (به صورت عکس یا فایل تصویری):",
                             reply_markup=back_kb)
        await state.set_state(UtilForm.ft_image)
        return
    if text == FT_PDF2IMG:
        await message.answer("📄 لطفاً فایل PDF مورد نظر را ارسال فرمایید:", reply_markup=back_kb)
        await state.set_state(UtilForm.ft_pdf)
        return
    await message.answer("⚠️ لطفاً یکی از گزینه‌های منو را انتخاب فرمایید:", reply_markup=ft_menu_kb)


def compress_image(src_path: str, dst_path: str, target_kb: int = TARGET_IMAGE_KB) -> int:
    """کاهش تدریجی کیفیت/ابعاد تا حدود target_kb؛ خروجی: حجم نهایی (بایت)."""
    from PIL import Image
    img = Image.open(src_path)
    if img.width * img.height > MAX_IMAGE_PIXELS:   # قبل از decode کامل بررسی می‌شود (ضد decompression bomb)
        raise ValueError("ابعاد این عکس بیش از حد بزرگ است؛ لطفاً عکس کوچک‌تری ارسال فرمایید.")
    if img.mode in ("RGBA", "P", "LA", "L"):
        img = img.convert("RGB")
    quality, scale, target = 90, 1.0, target_kb * 1024
    while True:
        w, h = img.size
        resized = img if scale >= 1.0 else img.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                                                      Image.LANCZOS)
        buf = io.BytesIO()
        resized.save(buf, format="JPEG", quality=quality, optimize=True)
        size = buf.tell()
        if size <= target or (quality <= 30 and scale <= 0.3):
            with open(dst_path, "wb") as f:
                f.write(buf.getvalue())
            return size
        if quality > 30:
            quality -= 10
        else:
            scale -= 0.15


def split_tall_page(img) -> list:
    """صفحهٔ PDF غیرعادی بلند (چند «کارت صفحه» چسبیده) از شکاف‌های سفید افقی برش می‌خورد."""
    import numpy as np
    if img.height / img.width < TALL_PAGE_ASPECT_THRESHOLD:
        return [img]
    row_means = np.array(img.convert("L")).mean(axis=1)
    gaps, start = [], None
    for y, blank in enumerate(row_means >= BLANK_ROW_BRIGHTNESS):
        if blank and start is None:
            start = y
        elif not blank and start is not None:
            if y - start >= MIN_GAP_PX:
                gaps.append((start, y))
            start = None
    if start is not None and len(row_means) - start >= MIN_GAP_PX:
        gaps.append((start, len(row_means)))
    if not gaps:
        return [img]
    cuts = [0] + [(a + b) // 2 for a, b in gaps] + [img.height]
    segs = [img.crop((0, t, img.width, b)) for t, b in zip(cuts, cuts[1:]) if b - t >= MIN_SEGMENT_PX]
    return segs or [img]


def render_pdf_pages(pdf_path: str, max_pages: int = MAX_PDF_PAGES) -> list:
    """
    صفحات PDF → لیست تصاویر PIL (صفحات بلند برش می‌خورند). با PyMuPDF؛ اگر نصب نبود
    با pypdfium2. ValueError برای PDF خالی یا پرصفحه.
    """
    from PIL import Image
    pages = []
    try:
        import fitz  # PyMuPDF
        pdf = fitz.open(pdf_path)
        try:
            n = pdf.page_count
            _check_pages(n, max_pages)
            zoom = 2.0 if n <= 10 else (1.5 if n <= 20 else 1.0)
            budget = MAX_TOTAL_RENDER_PIXELS
            for i in range(n):
                page = pdf.load_page(i)
                z = _safe_zoom(page.rect.width, page.rect.height, zoom)
                budget -= int(page.rect.width * z) * int(page.rect.height * z)
                if budget < 0:
                    raise ValueError("حجم تصویری صفحات این PDF بیش از حد مجاز است؛ "
                                     "لطفاً فایل را به بخش‌های کوچک‌تر تقسیم و دوباره ارسال کنید.")
                pix = page.get_pixmap(matrix=fitz.Matrix(z, z))
                mode = "RGB" if pix.n < 4 else "RGBA"
                pages.append(Image.frombytes(mode, (pix.width, pix.height), pix.samples).convert("RGB"))
        finally:
            pdf.close()
    except ImportError:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(pdf_path)
        try:
            n = len(pdf)
            _check_pages(n, max_pages)
            zoom = 2.0 if n <= 10 else (1.5 if n <= 20 else 1.0)
            budget = MAX_TOTAL_RENDER_PIXELS
            for i in range(n):
                w, h = pdf[i].get_size()
                z = _safe_zoom(w, h, zoom)
                budget -= int(w * z) * int(h * z)
                if budget < 0:
                    raise ValueError("حجم تصویری صفحات این PDF بیش از حد مجاز است؛ "
                                     "لطفاً فایل را به بخش‌های کوچک‌تر تقسیم و دوباره ارسال کنید.")
                pages.append(pdf[i].render(scale=z).to_pil().convert("RGB"))
        finally:
            pdf.close()
    out = []
    for p in pages:
        out += split_tall_page(p)
    return out


def _safe_zoom(width: float, height: float, zoom: float) -> float:
    """کاهش بزرگ‌نمایی برای صفحه‌های غیرعادی بزرگ تا تصویر هر صفحه از MAX_PAGE_PIXELS بیشتر نشود."""
    area = max(1.0, float(width) * float(height))
    if area * zoom * zoom <= MAX_PAGE_PIXELS:
        return zoom
    return max(0.01, (MAX_PAGE_PIXELS / area) ** 0.5)


def _check_pages(n: int, max_pages: int):
    if n == 0:
        raise ValueError("فایل PDF ارسالی خالی یا نامعتبر است.")
    if n > max_pages:
        raise ValueError(f"این فایل {n} صفحه دارد و بیشتر از حد مجاز ({max_pages} صفحه) است. "
                         "لطفاً فایل را به بخش‌های کوچک‌تر تقسیم و دوباره ارسال کنید.")


@utilities_router.message(UtilForm.ft_image, F.photo | F.document)
async def ft_image(message: Message, state: FSMContext, bot: Bot):
    is_photo = bool(message.photo)
    if not is_photo and not (message.document and (message.document.mime_type or "").startswith("image/")):
        await message.answer("⚠️ لطفاً یک فایل تصویری (عکس) ارسال فرمایید.")
        return
    if _too_big(message.photo[-1] if is_photo else message.document):
        await message.answer(_too_big_text())
        return
    # قفل قبل از _gate: تا پردازش این فایل تمام و استفاده ثبت نشده، فایل دیگری پذیرفته نمی‌شود
    # (جلوگیری از استفادهٔ چندبارهٔ یک اعتبار رایگان با ارسال هم‌زمان چند فایل)
    if not rate_limit.try_acquire("file_tools", message.chat.id):
        await message.answer(rate_limit.BUSY_TEXTS["file_tools"])
        return
    try:
        await _ft_image_work(message, state, bot, is_photo)
    finally:
        rate_limit.release("file_tools", message.chat.id)


async def _ft_image_work(message: Message, state: FSMContext, bot: Bot, is_photo: bool):
    if not await _gate(message, state, "file_tools"):
        return
    from bale_file_sender import send_document_direct
    await message.answer("⏳ در حال دریافت و فشرده‌سازی عکس...")
    src, dst = _tmp("src.img"), _tmp("compressed.jpg")
    try:
        await bot.download(message.photo[-1] if is_photo else message.document, destination=src)
        original = os.path.getsize(src)
        final = await asyncio.get_running_loop().run_in_executor(None, compress_image, src, dst)
        if final >= original and is_photo:
            os.replace(src, dst)
            final = os.path.getsize(dst)
        ok = await send_document_direct(
            message.chat.id, dst, filename="compressed.jpg",
            caption=(f"✅ کاهش حجم انجام شد.\n\nحجم اولیه: {original / 1024:.0f} کیلوبایت\n"
                     f"حجم نهایی: {final / 1024:.0f} کیلوبایت"))
        if ok:
            _used(message.chat.id, "file_tools", "compress")
        else:
            await message.answer("❌ ارسال فایل با خطا مواجه شد. لطفاً دوباره تلاش کنید.")
    except ValueError as e:
        await message.answer(f"⚠️ {e}")
    except Exception as e:
        logger.error(f"[FILE-TOOLS] خطا در فشرده‌سازی عکس: {e}", exc_info=True)
        await message.answer("❌ خطایی در پردازش عکس رخ داد. لطفاً دوباره تلاش کنید.")
    finally:
        for p in (src, dst):
            try:
                os.remove(p)
            except OSError:
                pass
    if await state.get_state() == UtilForm.ft_image.state:
        await message.answer("می‌توانید عکس دیگری ارسال کنید یا بازگردید:", reply_markup=back_kb)


@utilities_router.message(UtilForm.ft_pdf, F.document)
async def ft_pdf(message: Message, state: FSMContext, bot: Bot):
    mime, fname = message.document.mime_type or "", message.document.file_name or ""
    if mime != "application/pdf" and not fname.lower().endswith(".pdf"):
        await message.answer("⚠️ لطفاً فقط فایل با فرمت PDF ارسال فرمایید.")
        return
    if _too_big(message.document):
        await message.answer(_too_big_text())
        return
    if not rate_limit.try_acquire("file_tools", message.chat.id):
        await message.answer(rate_limit.BUSY_TEXTS["file_tools"])
        return
    try:
        await _ft_pdf_work(message, state, bot)
    finally:
        rate_limit.release("file_tools", message.chat.id)


async def _ft_pdf_work(message: Message, state: FSMContext, bot: Bot):
    if not await _gate(message, state, "file_tools"):
        return
    from bale_file_sender import send_photo_direct
    await message.answer("⏳ در حال تبدیل فایل PDF به عکس...\nهر صفحه به‌صورت یک عکس جداگانه ارسال می‌شود.")
    pdf_path = _tmp("src.pdf")
    page_paths = []
    try:
        await bot.download(message.document, destination=pdf_path)
        try:
            images = await asyncio.get_running_loop().run_in_executor(None, render_pdf_pages, pdf_path)
        except ValueError as e:
            await message.answer(f"⚠️ {e}")
            return
        for i, img in enumerate(images, 1):
            path = _tmp(f"page{i:03d}.jpg")
            img.save(path, format="JPEG", quality=85, optimize=True)
            if os.path.getsize(path) > 9 * 1024 * 1024:
                compress_image(path, path, target_kb=8000)
            page_paths.append(path)
        n = len(page_paths)
        sent = 0
        for i, path in enumerate(page_paths, 1):
            await message.answer(f"📄 صفحه {_fa(i)} از {_fa(n)}")
            if await send_photo_direct(message.chat.id, path):
                sent += 1
            else:
                await message.answer(f"⚠️ ارسال صفحه {_fa(i)} با خطا مواجه شد.")
            if n > 1:
                await asyncio.sleep(PAGE_SEND_DELAY)
        if sent:
            _used(message.chat.id, "file_tools", f"pdf2img:{n}")
        await message.answer(f"✅ تبدیل انجام شد. ({_fa(sent)} عکس جداگانه ارسال شد)")
    except Exception as e:
        logger.error(f"[FILE-TOOLS] خطا در تبدیل PDF: {e}", exc_info=True)
        await message.answer("❌ خطایی در تبدیل فایل PDF رخ داد. لطفاً دوباره تلاش کنید.")
    finally:
        for p in [pdf_path] + page_paths:
            try:
                os.remove(p)
            except OSError:
                pass
    if await state.get_state() == UtilForm.ft_pdf.state:
        await message.answer("می‌توانید فایل PDF دیگری ارسال کنید یا بازگردید:", reply_markup=back_kb)


@utilities_router.message(UtilForm.ft_image)
@utilities_router.message(UtilForm.ft_pdf)
async def ft_other(message: Message, state: FSMContext):
    text = message.text or ""
    if _is_main(text):
        await _to_main(message, state)
        return
    if is_back(text):
        await file_tools_entry(message, state)
        return
    if await state.get_state() == UtilForm.ft_pdf.state:
        await message.answer("⚠️ لطفاً فایل PDF را به صورت فایل (Document) ارسال فرمایید.")
    else:
        await message.answer("⚠️ لطفاً یک فایل تصویری (عکس) ارسال فرمایید یا «بازگشت» را بزنید.")


ENTRY = {"damages": damages_entry, "court_fee": court_fee_entry, "stamp": stamp_entry,
         "file_tools": file_tools_entry}   # دیه و ارث در legal_tools_handlers اضافه می‌شوند


# ══════════════════════════════════════════════════════════════════
# بخش مدیر — شاخص تورم بانک مرکزی (پایهٔ ۱۳۹۵=۱۰۰)
# ══════════════════════════════════════════════════════════════════
_last_missing_alert: dict = {}


def _is_admin(message: Message) -> bool:
    return bool(ADMIN_ID and message.from_user and message.from_user.id == ADMIN_ID)


async def _notify_admin_missing(bot: Bot, detail: str):
    """هشدار کمبود شاخص به مدیر — هر مورد حداکثر یک‌بار در ۶ ساعت."""
    now = datetime.datetime.now()
    last = _last_missing_alert.get(detail)
    if not ADMIN_ID or (last and (now - last).total_seconds() < 6 * 3600):
        return
    _last_missing_alert[detail] = now
    try:
        await bot.send_message(ADMIN_ID, f"📈 کاربری محاسبهٔ خسارت/مهریه انجام داد ولی {detail}\n"
                                         "دریافت از بانک مرکزی: /cpi_fetch\n"
                                         "ثبت دستی (پایهٔ ۱۳۹۵): /cpi سال/ماه مقدار   یا   /cpi_year سال مقدار")
    except Exception:
        pass


def cpi_status() -> str:
    data = dc.load_cpi()
    series, chained, link = dc.judicial_series(data)
    latest = dc.latest_month(data)
    missing = dc.missing_required_month()
    c1400 = data["monthly_1400"]
    lines = [
        "📈 *وضعیت شاخص تورم (بانک مرکزی)*",
        f"سال پایه: {dc.CPI_BASE}",
        f"ماه‌های موجود: {len(series)} ({min(series) if series else '—'} تا "
        f"{dc.month_key(*latest) if latest else '—'})",
        f"اصلاحات دستی: {len(data['overrides'])} ماه، {len(data['annual_1395'])} سال",
        f"جدول سالانهٔ مهریه (گیت): {len(data['seed_annual'])} سال"
        + (f" ({min(data['seed_annual'], key=int)} تا {max(data['seed_annual'], key=int)})"
           if data['seed_annual'] else "") + f"؛ مهریه از سال {dc.first_annual_year(data) or '—'}",
        f"جدول ۱۴۰۰=۱۰۰ بانک مرکزی: تا {max(c1400) if c1400 else '—'}"
        + (f" (ماه پیوند {link}، {len(chained)} ماه زنجیره‌ای)" if link else ""),
        f"آخرین دریافت: {data.get('fetched_at') or '—'}",
    ]
    if missing:
        lines.append(f"⚠️ شاخص ماه {dc.month_key(*missing)} هنوز موجود نیست.")
    lines += ["", "دریافت فوری از بانک مرکزی: /cpi_fetch",
              "یا ارسال PDF ماهانهٔ بانک مرکزی با کپشن /cpi_pdf",
              "ثبت دستی یک ماه (پایهٔ ۱۳۹۵): /cpi 1405/06 3453.5",
              "ثبت سالانه (مهریه): /cpi_year 1365 0.85",
              "ورود دسته‌ای: فایل اکسل (سال | ماه | شاخص) با کپشن /cpi_import"]
    return "\n".join(lines)


@utilities_router.message(Command("cpi"), _is_admin)
async def cpi_cmd(message: Message):
    parts = [dc.normalize_digits(p) for p in (message.text or "").split()]
    if len(parts) == 1:
        await message.answer(cpi_status())
        return
    try:
        ym = parts[1].replace("-", "/").split("/")
        year, month, value = int(ym[0]), int(ym[1]), float(parts[2])
        assert len(parts) == 3 and 1300 <= year <= 1500 and 1 <= month <= 12 and value > 0
    except Exception:
        await message.answer("⚠️ ورودی نامعتبر. فرمت: /cpi 1405/06 3453.5")
        return
    dc.set_monthly(year, month, value)
    await message.answer(f"✅ شاخص ماه {dc.month_key(year, month)} = {value:g} (پایهٔ ۱۳۹۵) ثبت شد.")


async def _fetch_from_cbi() -> list:
    import cpi_fetcher
    values, url = await asyncio.to_thread(cpi_fetcher.fetch_latest)
    added = dc.set_monthly_1400(values, url)
    logger.info(f"[CPI] دریافت از بانک مرکزی: {url} — ماه‌های جدید: {added}")
    return added


def _added_text(added: list) -> str:
    if not added:
        return "ماه جدیدی اضافه نشد (آخرین PDF بانک مرکزی قبلاً دریافت شده بود)."
    series = dc.judicial_series()[0]
    return "ماه‌های جدید: " + "، ".join(f"{k}={series[k]:g}" for k in added)


@utilities_router.message(Command("cpi_fetch"), _is_admin)
async def cpi_fetch_cmd(message: Message):
    await message.answer("⏳ در حال دریافت از سایت بانک مرکزی...")
    try:
        added = await _fetch_from_cbi()
    except Exception as e:
        logger.error(f"[CPI] خطا در دریافت از بانک مرکزی: {e}", exc_info=True)
        await message.answer(f"❌ دریافت از بانک مرکزی ناموفق بود: {str(e)[:300]}\n"
                             "می‌توانید PDF ماهانه را از cbi.ir/simplelist/1611.aspx دانلود و با کپشن /cpi_pdf بفرستید.")
        return
    await message.answer(f"✅ {_added_text(added)}\n\n{cpi_status()}")


@utilities_router.message(F.document, F.caption.startswith("/cpi_pdf"), _is_admin)
async def cpi_pdf_cmd(message: Message, bot: Bot):
    import cpi_fetcher
    path = _tmp("cpi.pdf")
    try:
        await bot.download(message.document, destination=path)
        with open(path, "rb") as f:
            values = cpi_fetcher.parse_cpi_pdf(f.read())
        added = dc.set_monthly_1400(values, f"PDF ارسالی مدیر ({message.document.file_name or ''})")
        await message.answer(f"✅ {_added_text(added)}\n\n{cpi_status()}")
    except Exception as e:
        logger.error(f"[CPI] خطا در خواندن PDF: {e}", exc_info=True)
        await message.answer(f"❌ خطا در خواندن PDF: {str(e)[:300]}")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


@utilities_router.message(Command("cpi_year"), _is_admin)
async def cpi_year_cmd(message: Message):
    parts = [dc.normalize_digits(p) for p in (message.text or "").split()]
    try:
        year, value = int(parts[1]), float(parts[2])
        assert 1300 <= year <= 1500 and value > 0
    except Exception:
        await message.answer("⚠️ فرمت: /cpi_year 1365 0.85")
        return
    dc.import_rows([(year, None, value)])
    await message.answer(f"✅ شاخص سالانهٔ {year} = {value:g} ثبت شد.")


def read_cpi_excel(path: str) -> list:
    """سطرهای (سال، ماه یا None، شاخص)؛ سرتیتر و سطرهای نامعتبر نادیده گرفته می‌شوند."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows = []
    for row in wb.active.iter_rows(values_only=True):
        cells = list(row[:3])
        if len(cells) < 3:
            continue
        try:
            year = int(float(dc.normalize_digits(str(cells[0]))))
            month_raw = dc.normalize_digits(str(cells[1] or "")).strip()
            month = int(float(month_raw)) if month_raw not in ("", "None") else None
            value = float(dc.normalize_digits(str(cells[2])).replace(",", ""))
        except (TypeError, ValueError):
            continue
        if not (1300 <= year <= 1500) or value <= 0 or (month is not None and not (1 <= month <= 12)):
            continue
        rows.append((year, month, value))
    wb.close()
    return rows


@utilities_router.message(F.document, F.caption.startswith("/cpi_import"), _is_admin)
async def cpi_import_cmd(message: Message, bot: Bot):
    path = _tmp("cpi_import.xlsx")
    try:
        await bot.download(message.document, destination=path)
        rows = read_cpi_excel(path)
        if not rows:
            await message.answer("⚠️ هیچ سطر معتبری پیدا نشد. ستون‌ها: سال | ماه | شاخص")
            return
        replace = "replace" in (message.caption or "")
        n_month, n_year = dc.import_rows(rows, replace=replace)
        await message.answer(f"✅ ورود شاخص انجام شد: {n_month} ماه و {n_year} سال"
                             + (" (دادهٔ قبلی جایگزین شد)" if replace else "") + "\n\n" + cpi_status())
    except Exception as e:
        logger.error(f"[CPI] خطا در ورود اکسل: {e}", exc_info=True)
        await message.answer(f"❌ خطا در خواندن فایل: {str(e)[:200]}")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


async def cpi_reminder_loop(bot: Bot, check_every_seconds: int = 3600):
    """
    از روز اول هر ماه شمسی تا موجود شدن شاخص ماه قبل، روزی یک‌بار (ساعت ۱۰ به بعد)
    آخرین PDF را از سایت بانک مرکزی دریافت می‌کند؛ اگر نشد، به مدیر یادآوری می‌کند.
    """
    last_checked_day = None
    tehran = datetime.timezone(datetime.timedelta(hours=3, minutes=30))
    while True:
        try:
            await asyncio.sleep(check_every_seconds)
            now = datetime.datetime.now(tehran)
            if now.hour < 10 or last_checked_day == now.date():
                continue
            missing = dc.missing_required_month()
            if not missing:
                continue
            last_checked_day = now.date()
            key = dc.month_key(*missing)
            error = ""
            try:
                added = await _fetch_from_cbi()
                if key in added or not dc.missing_required_month():
                    if ADMIN_ID:
                        await bot.send_message(ADMIN_ID, f"📈 شاخص تورم از سایت بانک مرکزی دریافت شد. {_added_text(added)}")
                    continue
            except Exception as e:
                error = f"\nخطای دریافت خودکار: {str(e)[:200]}"
                logger.error(f"[CPI] خطا در دریافت خودکار: {e}")
            if ADMIN_ID:
                await bot.send_message(
                    ADMIN_ID,
                    f"📈 یادآوری ماهانه: شاخص تورم ماه {key} هنوز در ربات موجود نیست "
                    f"(احتمالاً بانک مرکزی هنوز منتشر نکرده).{error}\n"
                    "فردا دوباره خودکار بررسی می‌شود. دریافت فوری: /cpi_fetch\n"
                    "یا PDF را از cbi.ir/simplelist/1611.aspx با کپشن /cpi_pdf بفرستید.")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[CPI] خطا در دریافت/یادآوری ماهانه: {e}")


# دیه و سهم‌الارث — هندلرها روی همین روتر ثبت و به ENTRY اضافه می‌شوند
import legal_tools_handlers  # noqa: E402,F401
