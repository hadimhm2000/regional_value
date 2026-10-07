# -*- coding: utf-8 -*-
"""
menu_handlers.py — بخش‌های منوی اصلی غیر از خود فرم استعلام:

  📂 سوابق استعلام      — فهرست استعلام‌های قبلی، دریافت دوبارهٔ PDF (بدون مصرف اعتبار)،
                          «محاسبهٔ مجدد با تغییر» (بارگذاری اطلاعات قبلی در فرم)
  🔎 بررسی اصالت گزارش  — وارد کردن کد رهگیری یا اسکن QR روی PDF (/start v_...)
  👤 حساب کاربری        — وضعیت اشتراک، خرید/تمدید، اشتراک دفتری (دعوت/حذف همکار، عضویت با کد)
  ➕ / 📑 گزارش تجمیعی  — افزودن ملک دیگر به گزارش و دریافت PDF تجمیعی

این روتر قبل از روتر فرم استعلام ثبت می‌شود تا دکمه‌های منو در هر مرحله‌ای کار کنند.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import os
import tempfile
import time

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import access_control
import db
import history
from bale_file_sender import send_document_direct
from config import (ADMIN_ID, BATCH_MAX_ITEMS, BOT_USERNAME, HISTORY_PAGE_SIZE, OFFICE_MAX_MEMBERS,
                    SUBSCRIPTION_PLANS)
from keyboards import (ACCOUNT_BUTTON, BATCH_ADD_BUTTON, HISTORY_BUTTON, MAIN_MENU_BUTTON, VERIFY_BUTTON,
                       is_back, is_batch_pdf_button, is_button, is_main_menu_button, is_restart, main_menu_kb,
                       nav_only_kb)

logger = logging.getLogger(__name__)

menu_router = Router()

# پس از این مدت، «افزودن ملک دیگر» یک استعلام مستقل شروع می‌کند (نه ادامهٔ گزارش تجمیعی قبلی)
_BATCH_WINDOW_SECONDS = 24 * 3600
_PDF_TIMEOUT_SECONDS = 90


class MenuForm(StatesGroup):
    waiting_verify_code = State()
    waiting_invite_code = State()


# ══════════════════════════════════════════════════════════════════
# کمکی‌ها
# ══════════════════════════════════════════════════════════════════
def _fa(n) -> str:
    return str(n).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def _money(n) -> str:
    return _fa(f"{int(n or 0):,}")


def _tehran(ts: float) -> datetime.datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.fromtimestamp(ts, ZoneInfo("Asia/Tehran"))
    except Exception:
        return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc) + datetime.timedelta(hours=3, minutes=30)


def fa_date(ts: float, with_time: bool = False) -> str:
    d = _tehran(ts)
    try:
        from regional_value_pdf import _gregorian_to_jalali
        y, m, dd = _gregorian_to_jalali(d.year, d.month, d.day)
    except Exception:
        y, m, dd = d.year, d.month, d.day
    s = f"{y}/{m:02d}/{dd:02d}"
    if with_time:
        s += f" ساعت {d.hour:02d}:{d.minute:02d}"
    return _fa(s)


def _pdf_date_time(ts: float):
    """تاریخ و ساعت به همان قالب PDF اصلی (برای ساخت دوبارهٔ گزارش با تاریخ صدور اصلی)."""
    d = _tehran(ts)
    try:
        from regional_value_pdf import _PERSIAN_MONTHS, _gregorian_to_jalali
        y, m, dd = _gregorian_to_jalali(d.year, d.month, d.day)
        return f"{_fa(dd)} {_PERSIAN_MONTHS[m - 1]} {_fa(y)}", _fa(f"{d.hour:02d}:{d.minute:02d}")
    except Exception:
        return fa_date(ts), _fa(f"{d.hour:02d}:{d.minute:02d}")


def _short(text: str, n: int = 45) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _days_left(expires_at: float) -> int:
    return max(0, -int(-(expires_at - time.time()) // 86400))


async def _go_main(message: Message, state: FSMContext, text: str = "🏠 منوی اصلی"):
    await state.clear()
    await message.answer(text, reply_markup=main_menu_kb())


def _owner_name(owner_id: int) -> str:
    tr = db.query_one("SELECT full_name FROM trial_requests WHERE user_id=?", (owner_id,))
    return (tr["full_name"] if tr and tr["full_name"] else "") or str(owner_id)


# ══════════════════════════════════════════════════════════════════
# /start (با پارامترهای v_ = تأیید اصالت، j_ = عضویت در دفتر) و منوی اصلی
# ══════════════════════════════════════════════════════════════════
WELCOME = (
    "👋 به ربات استعلام ارزش منطقه‌ای خوش آمدید.\n\n"
    f"• «{MAIN_MENU_BUTTON}»: استعلام و محاسبهٔ ارزش عرصه و اعیانی با خروجی PDF\n"
    f"• «{HISTORY_BUTTON}»: استعلام‌های قبلی، دریافت دوبارهٔ PDF و محاسبهٔ مجدد\n"
    f"• «{VERIFY_BUTTON}»: بررسی اصالت یک گزارش با کد رهگیری\n"
    f"• «{ACCOUNT_BUTTON}»: وضعیت اشتراک، تمدید و افزودن همکاران دفتر"
)


@menu_router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, command: CommandObject, bot: Bot):
    await state.clear()  # /start همیشه کاربر را از هر مرحله‌ای به منوی اصلی برمی‌گرداند
    arg = (command.args or "").strip()
    if arg.startswith("v_"):
        await _verify_and_reply(message, arg[2:])
        return
    if arg.startswith("j_"):
        await _join_with_code(message, state, bot, arg[2:])
        return
    await message.answer(WELCOME, reply_markup=main_menu_kb())


@menu_router.message(lambda m: bool(m.text) and is_main_menu_button(m.text))
async def start_regional_value(message: Message, state: FSMContext):
    from regional_value_handlers import regional_value_entry
    await regional_value_entry(message, state)


# ══════════════════════════════════════════════════════════════════
# 📂 سوابق استعلام
# ══════════════════════════════════════════════════════════════════
@menu_router.message(lambda m: bool(m.text) and is_button(m.text, HISTORY_BUTTON))
async def history_menu(message: Message, state: FSMContext):
    await state.clear()
    await _send_history_page(message, message.from_user.id, 0)


async def _send_history_page(message: Message, user_id: int, offset: int, edit: bool = False):
    total = history.count_user_queries(user_id)
    if total == 0:
        await message.answer("📂 هنوز استعلامی برای شما ثبت نشده است.", reply_markup=main_menu_kb())
        return
    items = history.list_user_queries(user_id, HISTORY_PAGE_SIZE, offset)
    lines = [f"📂 *سوابق استعلام* ({_fa(total)} مورد)\n"]
    buttons = []
    for i, q in enumerate(items, offset + 1):
        lines.append(
            f"{_fa(i)}. {q['code']} — {fa_date(q['created_at'])}\n"
            f"     📍 {q['province'] or '-'} — {q['county'] or '-'}  |  💰 {_money(q['total'])} ریال"
            + (f"\n     🗺 {_short(q['address'])}" if q.get("address") else "")
        )
        buttons.append(InlineKeyboardButton(text=f"{_fa(i)}. {q['code']}", callback_data=f"h:{q['code']}"))
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(text="◀️ قبلی", callback_data=f"hp:{max(0, offset - HISTORY_PAGE_SIZE)}"))
    if offset + HISTORY_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="بعدی ▶️", callback_data=f"hp:{offset + HISTORY_PAGE_SIZE}"))
    if nav:
        rows.append(nav)
    lines.append("\nبرای دریافت دوبارهٔ PDF یا محاسبهٔ مجدد، روی کد مورد نظر بزنید.")
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    if edit:
        try:
            await message.edit_text("\n".join(lines), reply_markup=kb)
            return
        except Exception:
            pass
    await message.answer("\n".join(lines), reply_markup=kb)


@menu_router.callback_query(F.data.startswith("hp:"))
async def cb_history_page(callback: CallbackQuery):
    try:
        offset = max(0, int(callback.data.split(":", 1)[1]))
    except ValueError:
        offset = 0
    await callback.answer()
    await _send_history_page(callback.message, callback.from_user.id, offset, edit=True)


def _own_query(code: str, user_id: int):
    q = history.get_query(code)
    return q if q and q["user_id"] == user_id else None


@menu_router.callback_query(F.data.startswith("h:"))
async def cb_history_item(callback: CallbackQuery):
    code = callback.data.split(":", 1)[1]
    q = _own_query(code, callback.from_user.id)
    if not q:
        await callback.answer("این استعلام یافت نشد.")
        return
    await callback.answer()
    from regional_value_handlers import _result_values_text
    text = (
        f"🔖 کد رهگیری: {q['code']}\n"
        f"📅 تاریخ: {fa_date(q['created_at'], with_time=True)}\n"
        f"📍 {q['province'] or '-'} — {q['county'] or '-'}\n"
        f"🗺 آدرس: {q['address'] or '-'}\n"
        + (f"🧾 پلاک ثبتی: {q['plak']}\n" if q.get("plak") else "")
        + f"\n{_result_values_text(q['calc'], bold=True)}"
    )
    rows = [[InlineKeyboardButton(text="📄 دریافت دوبارهٔ PDF", callback_data=f"hpdf:{code}")],
            [InlineKeyboardButton(text="✏️ محاسبهٔ مجدد با تغییر اطلاعات", callback_data=f"hedit:{code}")]]
    if q.get("batch_code"):
        n = history.batch_count(q["batch_code"])
        text += f"\n\n📑 عضو گزارش تجمیعی {q['batch_code']} ({_fa(n)} ملک)"
        if n >= 2:
            rows.append([InlineKeyboardButton(text="📑 دریافت گزارش تجمیعی",
                                              callback_data=f"bpdf:{q['batch_code']}")])
    await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@menu_router.callback_query(F.data.startswith("hpdf:"))
async def cb_history_pdf(callback: CallbackQuery):
    code = callback.data.split(":", 1)[1]
    q = _own_query(code, callback.from_user.id)
    if not q:
        await callback.answer("این استعلام یافت نشد.")
        return
    await callback.answer("در حال آماده‌سازی PDF...")
    await callback.message.answer("⏳ در حال آماده‌سازی فایل PDF...")
    ok = await send_query_pdf(callback.from_user.id, q)
    if not ok:
        await callback.message.answer("⚠️ آماده‌سازی فایل با خطا مواجه شد؛ لطفاً کمی بعد دوباره تلاش کنید.")
    else:
        db.log_event("pdf_resend", callback.from_user.id, code)


async def send_query_pdf(user_id: int, q: dict) -> bool:
    """ساخت دوبارهٔ PDF یک استعلام ثبت‌شده (با تاریخ و کد اصلی) و ارسال آن."""
    from ayani_pdf import build_ayani_pdf
    from regional_value_handlers import _result_values_text, verify_url

    date_text, time_text = _pdf_date_time(q["created_at"])
    path = os.path.join(tempfile.gettempdir(), f"rv_{q['code']}_{time.time_ns()}.pdf")
    loop = asyncio.get_running_loop()
    try:
        ok = await asyncio.wait_for(loop.run_in_executor(None, lambda: build_ayani_pdf(
            path, province=q["province"] or "", county=q["county"] or "", address=q["address"] or "",
            tax_result=q["tax_result"], result=q["calc"], plak=q.get("plak"), date_text=date_text,
            time_text=time_text, report_no=q["code"], verify_url=verify_url(q["code"]),
        )), timeout=_PDF_TIMEOUT_SECONDS)
        if not ok:
            return False
        return await send_document_direct(
            chat_id=user_id, file_path=path,
            filename=f"ارزش_منطقه_ای_{q['province']}_{q['code']}.pdf",
            caption=(f"📄 گزارش ارزش منطقه‌ای ملک (نسخهٔ مجدد)\n\n"
                     f"{_result_values_text(q['calc'], bold=False)}\n\n🔖 کد رهگیری: {q['code']}"),
        )
    except Exception as e:
        logger.error(f"[HISTORY] ساخت/ارسال دوبارهٔ PDF {q['code']} ناموفق: {e}", exc_info=True)
        return False
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


@menu_router.callback_query(F.data.startswith("hedit:"))
async def cb_history_edit(callback: CallbackQuery, state: FSMContext):
    code = callback.data.split(":", 1)[1]
    q = _own_query(code, callback.from_user.id)
    if not q:
        await callback.answer("این استعلام یافت نشد.")
        return
    await callback.answer()
    await callback.message.answer(
        f"✏️ اطلاعات استعلام {code} بارگذاری شد. در پیش‌نمایش، «ویرایش» را بزنید و هر مورد را "
        "تغییر دهید؛ با تایید، یک استعلام جدید (با کد رهگیری جدید) انجام می‌شود.")
    from regional_value_handlers import start_form_with_inputs
    await start_form_with_inputs(callback.message, state, q["inputs"], callback.from_user.id)


# ══════════════════════════════════════════════════════════════════
# ➕ / 📑 گزارش تجمیعی چند ملک
# ══════════════════════════════════════════════════════════════════
@menu_router.message(lambda m: bool(m.text) and is_button(m.text, BATCH_ADD_BUTTON))
async def batch_add(message: Message, state: FSMContext):
    from regional_value_handlers import regional_value_entry
    uid = message.from_user.id
    last = history.list_user_queries(uid, 1)
    if not last or time.time() - last[0]["created_at"] > _BATCH_WINDOW_SECONDS:
        await message.answer("ℹ️ استعلام اخیری برای افزودن به گزارش تجمیعی پیدا نشد؛ یک استعلام جدید شروع می‌شود.")
        await regional_value_entry(message, state)
        return
    batch = last[0]["batch_code"] or history.start_batch(uid, last[0]["code"])
    n = history.batch_count(batch)
    if n >= BATCH_MAX_ITEMS:
        await message.answer(
            f"⚠️ هر گزارش تجمیعی حداکثر {_fa(BATCH_MAX_ITEMS)} ملک دارد. گزارش فعلی را دریافت کنید؛ "
            "استعلام بعدی یک گزارش تجمیعی جدید شروع می‌کند.")
        return
    await message.answer(f"➕ ملک شمارهٔ {_fa(n + 1)} گزارش تجمیعی {batch}\nاطلاعات ملک بعدی را وارد کنید.")
    await regional_value_entry(message, state, batch_code=batch)


@menu_router.message(lambda m: bool(m.text) and is_batch_pdf_button(m.text))
async def batch_pdf_button(message: Message, state: FSMContext):
    uid = message.from_user.id
    last = history.list_user_queries(uid, 1)
    batch = last[0]["batch_code"] if last else None
    if not batch or history.batch_count(batch) < 2:
        await message.answer("ℹ️ گزارش تجمیعی‌ای با دو ملک یا بیشتر پیدا نشد.", reply_markup=main_menu_kb())
        return
    await state.clear()
    await _send_batch_pdf(message, uid, batch)


@menu_router.callback_query(F.data.startswith("bpdf:"))
async def cb_batch_pdf(callback: CallbackQuery):
    batch = callback.data.split(":", 1)[1]
    s = history.batch_summary(batch)
    if not s or s["user_id"] != callback.from_user.id:
        await callback.answer("این گزارش یافت نشد.")
        return
    await callback.answer("در حال آماده‌سازی...")
    await _send_batch_pdf(callback.message, callback.from_user.id, batch)


async def _send_batch_pdf(message: Message, user_id: int, batch: str):
    from ayani_pdf import build_batch_pdf
    from regional_value_handlers import verify_url

    items = [it for it in history.batch_items(batch) if it["user_id"] == user_id]
    await message.answer(f"⏳ در حال آماده‌سازی گزارش تجمیعی ({_fa(len(items))} ملک)...")
    for it in items:
        it["date_text"], it["time_text"] = _pdf_date_time(it["created_at"])
    path = os.path.join(tempfile.gettempdir(), f"rv_{batch}_{time.time_ns()}.pdf")
    loop = asyncio.get_running_loop()
    total = sum(int(it["total"] or 0) for it in items)
    try:
        ok = await asyncio.wait_for(loop.run_in_executor(
            None, lambda: build_batch_pdf(path, batch, items, verify_url_for=verify_url)),
            timeout=_PDF_TIMEOUT_SECONDS + 20 * len(items))
        sent = ok and await send_document_direct(
            chat_id=user_id, file_path=path, filename=f"گزارش_تجمیعی_{batch}.pdf",
            caption=(f"📑 گزارش تجمیعی ارزش منطقه‌ای — {_fa(len(items))} ملک\n\n"
                     f"💰 جمع ارزش منطقه‌ای: {_money(total)} ریال\n🔖 کد گزارش: {batch}"),
        )
    except Exception as e:
        logger.error(f"[BATCH] ساخت گزارش تجمیعی {batch} ناموفق: {e}", exc_info=True)
        sent = False
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    if sent:
        db.log_event("batch_pdf", user_id, batch)
        await message.answer("✅ گزارش تجمیعی ارسال شد.", reply_markup=main_menu_kb())
    else:
        await message.answer("⚠️ آماده‌سازی گزارش تجمیعی با خطا مواجه شد؛ لطفاً کمی بعد دوباره تلاش کنید.",
                             reply_markup=main_menu_kb())


# ══════════════════════════════════════════════════════════════════
# 🔎 بررسی اصالت گزارش
# ══════════════════════════════════════════════════════════════════
@menu_router.message(lambda m: bool(m.text) and is_button(m.text, VERIFY_BUTTON))
async def verify_menu(message: Message, state: FSMContext):
    await state.set_state(MenuForm.waiting_verify_code)
    await message.answer(
        "🔎 کد رهگیری درج‌شده روی گزارش را وارد کنید (مثلاً RV-7K3M-X9PQ).\n"
        "با اسکن QR روی گزارش هم مستقیم به همین بخش می‌رسید.",
        reply_markup=nav_only_kb(),
    )


@menu_router.message(Command("verify"))
async def cmd_verify(message: Message, command: CommandObject):
    await _verify_and_reply(message, command.args or "")


@menu_router.message(MenuForm.waiting_verify_code)
async def verify_code_input(message: Message, state: FSMContext):
    if not message.text:
        return
    if is_back(message.text) or is_restart(message.text):
        await _go_main(message, state)
        return
    if await _verify_and_reply(message, message.text, keep_waiting=True):
        await state.clear()


async def _verify_and_reply(message: Message, raw: str, keep_waiting: bool = False) -> bool:
    """خروجی: True اگر کد معتبر و پاسخ داده شد."""
    code = db.normalize_code(raw)
    if not code:
        await message.answer(
            "⚠️ این متن شبیه کد رهگیری نیست. کد ۸ کاراکتری روی گزارش را وارد کنید (مثلاً RV-7K3M-X9PQ).",
            reply_markup=nav_only_kb() if keep_waiting else main_menu_kb())
        return False
    db.log_event("verify", message.chat.id, code)
    if code.startswith("RB-"):
        s = history.batch_summary(code)
        if not s:
            await _verify_not_found(message, code, keep_waiting)
            return False
        items = history.batch_items(code)
        lines = "\n".join(f"{_fa(i)}. {it['code']} — {it['province'] or '-'} — {_money(it['total'])} ریال"
                          for i, it in enumerate(items, 1))
        await message.answer(
            "✅ *گزارش تجمیعی معتبر است*\n\n"
            f"🔖 کد: {code}\n📅 تاریخ: {fa_date(s['created_at'], with_time=True)}\n"
            f"🏠 تعداد املاک: {_fa(s['n'])}\n💰 جمع ارزش منطقه‌ای: *{_money(s['total'])} ریال*\n\n{lines}\n\n"
            "مبالغ بالا را با مبالغ روی گزارش مقایسه کنید؛ هر ردیف با کد رهگیری خودش هم قابل بررسی است.",
            reply_markup=main_menu_kb())
        return True
    q = history.get_query(code)
    if not q:
        await _verify_not_found(message, code, keep_waiting)
        return False
    from regional_value_handlers import _result_values_text
    await message.answer(
        "✅ *این گزارش معتبر است* و توسط سامانهٔ استعلام ارزش منطقه‌ای صادر شده است.\n\n"
        f"🔖 کد رهگیری: {code}\n"
        f"📅 تاریخ صدور: {fa_date(q['created_at'], with_time=True)}\n"
        f"📍 {q['province'] or '-'} — {q['county'] or '-'}\n"
        f"🗺 آدرس: {q['address'] or '-'}\n"
        + (f"🧾 پلاک ثبتی: {q['plak']}\n" if q.get("plak") else "")
        + f"\n{_result_values_text(q['calc'], bold=True)}\n\n"
        "مبالغ بالا را با مبالغ روی گزارش مقایسه کنید؛ هر اختلافی یعنی گزارش دست‌کاری شده است.",
        reply_markup=main_menu_kb())
    return True


async def _verify_not_found(message: Message, code: str, keep_waiting: bool):
    await message.answer(
        f"❌ گزارشی با کد {code} در سامانه ثبت نشده است.\n"
        "کد را دوباره با دقت بررسی کنید؛ اگر درست است، این گزارش توسط این سامانه صادر نشده است.",
        reply_markup=nav_only_kb() if keep_waiting else main_menu_kb())


# ══════════════════════════════════════════════════════════════════
# 👤 حساب کاربری و اشتراک دفتری
# ══════════════════════════════════════════════════════════════════
@menu_router.message(lambda m: bool(m.text) and is_button(m.text, ACCOUNT_BUTTON))
async def account_menu(message: Message, state: FSMContext):
    await state.clear()
    await _send_account(message, message.from_user.id)


async def _send_account(message: Message, uid: int):
    approved = await access_control.is_approved(uid)
    own = await access_control.get_own_subscription(uid)
    membership = await access_control.office_membership(uid)
    credits = await access_control.free_retry_count(uid)
    lines = ["👤 *حساب کاربری*\n", f"🆔 شناسهٔ شما: {uid}"]
    if approved:
        lines.append("✅ دسترسی: رایگان و دائمی (تایید مدیر سامانه)")
    elif own:
        title = SUBSCRIPTION_PLANS.get(own["plan"], {}).get("title", own["plan"])
        lines.append(f"💳 {title}: فعال تا {fa_date(own['expires_at'])} "
                     f"({_fa(_days_left(own['expires_at']))} روز باقی‌مانده)")
    if membership:
        owner = membership["owner_id"]
        osub = await access_control.get_own_subscription(owner)
        status = ("فعال" + (f" تا {fa_date(osub['expires_at'])}" if osub else "")
                  if membership["owner_active"] else "⛔️ غیرفعال (اشتراک مدیر دفتر به پایان رسیده)")
        lines.append(f"👥 عضو اشتراک دفتر «{_owner_name(owner)}»: {status}")
    if not approved and not own and not (membership and membership["owner_active"]):
        lines.append("⛔️ اشتراک فعالی ندارید.")
    if credits:
        lines.append(f"🎁 استعلام رایگان باقی‌مانده: {_fa(credits)}")
    lines.append(f"📊 استعلام‌های ثبت‌شده: {_fa(history.count_user_queries(uid))}")

    rows = []
    if not approved:
        rows.append([InlineKeyboardButton(text="💳 خرید / تمدید اشتراک", callback_data="acc:renew")])
    if await access_control.can_own_office(uid):
        n = len(await access_control.list_office_members(uid))
        rows.append([InlineKeyboardButton(text=f"👥 همکاران دفتر ({_fa(n)} از {_fa(OFFICE_MAX_MEMBERS)})",
                                          callback_data="acc:office")])
    if membership:
        rows.append([InlineKeyboardButton(text="🚪 خروج از اشتراک دفتر", callback_data="acc:leave")])
    else:
        rows.append([InlineKeyboardButton(text="🔗 عضویت در دفتر با کد دعوت", callback_data="acc:join")])
    await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@menu_router.callback_query(F.data == "acc:renew")
async def cb_renew(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    from regional_value_handlers import _ask_plan
    await state.clear()
    own = await access_control.get_own_subscription(callback.from_user.id)
    prefix = ""
    if own:
        prefix = (f"ℹ️ اشتراک فعلی شما تا {fa_date(own['expires_at'])} فعال است؛ مدت اشتراک جدید به انتهای "
                  "آن اضافه می‌شود.\n\n")
    await _ask_plan(callback.message, state, prefix)


def _invite_link(code: str) -> str:
    return f"https://ble.ir/{BOT_USERNAME}?start=j_{code.replace('-', '')}"


async def _office_panel(message: Message, uid: int, edit: bool = False):
    office = await access_control.get_or_create_office(uid)
    members = await access_control.list_office_members(uid)
    code = office["invite_code"]
    lines = [
        "👥 *اشتراک دفتری*\n",
        f"همکاران دفتر شما تا زمانی که اشتراک شما فعال است، بدون خرید اشتراک جداگانه از سامانه استفاده "
        f"می‌کنند (حداکثر {_fa(OFFICE_MAX_MEMBERS)} نفر).\n",
        f"🔑 کد دعوت: {code}",
        f"🔗 لینک دعوت: {_invite_link(code)}\n",
        "همکار شما لینک را باز می‌کند، یا در ربات «حساب کاربری ← عضویت در دفتر با کد دعوت» را می‌زند و "
        "کد بالا را وارد می‌کند.\n",
        f"اعضا ({_fa(len(members))} از {_fa(OFFICE_MAX_MEMBERS)}):",
    ]
    if members:
        lines += [f"{_fa(i)}. {m['full_name'] or '-'} ({m['user_id']}) — از {fa_date(m['added_at'])}"
                  for i, m in enumerate(members, 1)]
    else:
        lines.append("هنوز همکاری اضافه نشده است.")
    rows = [[InlineKeyboardButton(text=f"❌ حذف {_short(m['full_name'] or str(m['user_id']), 20)}",
                                  callback_data=f"om_rm:{m['user_id']}")] for m in members]
    rows.append([InlineKeyboardButton(text="🔄 تغییر کد دعوت", callback_data="om_reset")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    if edit:
        try:
            await message.edit_text("\n".join(lines), reply_markup=kb)
            return
        except Exception:
            pass
    await message.answer("\n".join(lines), reply_markup=kb)


@menu_router.callback_query(F.data == "acc:office")
async def cb_office(callback: CallbackQuery):
    uid = callback.from_user.id
    if not await access_control.can_own_office(uid):
        await callback.answer("برای مدیریت همکاران، ابتدا اشتراک تهیه کنید.")
        return
    await callback.answer()
    await _office_panel(callback.message, uid)


@menu_router.callback_query(F.data == "om_reset")
async def cb_office_reset(callback: CallbackQuery):
    uid = callback.from_user.id
    if not await access_control.can_own_office(uid):
        await callback.answer("اشتراک فعال ندارید.")
        return
    await access_control.reset_invite_code(uid)
    await callback.answer("کد دعوت جدید ساخته شد؛ کد قبلی دیگر کار نمی‌کند.")
    await _office_panel(callback.message, uid, edit=True)


@menu_router.callback_query(F.data.startswith("om_rm:"))
async def cb_office_remove(callback: CallbackQuery, bot: Bot):
    uid = callback.from_user.id
    try:
        member = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer()
        return
    if await access_control.remove_office_member(uid, member):
        await callback.answer("همکار حذف شد.")
        try:
            await bot.send_message(member, "ℹ️ دسترسی شما به اشتراک دفتر توسط مدیر دفتر لغو شد.",
                                   reply_markup=main_menu_kb())
        except Exception:
            pass
    else:
        await callback.answer("این همکار در فهرست نیست.")
    await _office_panel(callback.message, uid, edit=True)


@menu_router.callback_query(F.data == "acc:leave")
async def cb_office_leave(callback: CallbackQuery, bot: Bot):
    owner = await access_control.leave_office(callback.from_user.id)
    await callback.answer()
    if owner is None:
        await callback.message.answer("ℹ️ شما عضو اشتراک دفتری نیستید.", reply_markup=main_menu_kb())
        return
    await callback.message.answer("✅ از اشتراک دفتر خارج شدید.", reply_markup=main_menu_kb())
    try:
        name = getattr(callback.from_user, "full_name", "") or callback.from_user.id
        await bot.send_message(owner, f"ℹ️ همکار «{name}» از اشتراک دفتر شما خارج شد.")
    except Exception:
        pass


@menu_router.callback_query(F.data == "acc:join")
async def cb_office_join(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(MenuForm.waiting_invite_code)
    await callback.message.answer("🔗 کد دعوتی را که مدیر دفتر برای شما فرستاده وارد کنید:",
                                  reply_markup=nav_only_kb())


@menu_router.message(MenuForm.waiting_invite_code)
async def invite_code_input(message: Message, state: FSMContext, bot: Bot):
    if not message.text:
        return
    if is_back(message.text) or is_restart(message.text):
        await _go_main(message, state)
        return
    await _join_with_code(message, state, bot, message.text)


async def _join_with_code(message: Message, state: FSMContext, bot: Bot, code: str):
    user = message.from_user
    status, owner = await access_control.join_office(
        user.id, code, getattr(user, "full_name", "") or "", OFFICE_MAX_MEMBERS)
    msgs = {
        "ok": "✅ عضو اشتراک دفتر شدید؛ از این پس بدون اشتراک جداگانه می‌توانید استعلام بگیرید.",
        "owner_inactive": ("✅ عضو دفتر شدید، اما اشتراک مدیر دفتر در حال حاضر فعال نیست؛ پس از تمدید "
                           "اشتراک توسط مدیر، دسترسی شما خودکار فعال می‌شود."),
        "invalid": "⚠️ کد دعوت نامعتبر است. کد را از مدیر دفتر دوباره بگیرید.",
        "self": "ℹ️ این کد دعوتِ دفتر خود شماست؛ آن را برای همکارانتان بفرستید.",
        "already": "ℹ️ شما از قبل عضو همین دفتر هستید.",
        "other_office": "⚠️ شما عضو دفتر دیگری هستید. ابتدا از «حساب کاربری» از آن دفتر خارج شوید.",
        "full": "⚠️ ظرفیت همکاران این دفتر تکمیل است. با مدیر دفتر تماس بگیرید.",
    }
    if status == "invalid" and await state.get_state() == MenuForm.waiting_invite_code.state:
        await message.answer(msgs[status], reply_markup=nav_only_kb())
        return
    await state.clear()
    await message.answer(msgs[status], reply_markup=main_menu_kb())
    if status in ("ok", "owner_inactive"):
        db.log_event("office_join", user.id, str(owner))
        try:
            await bot.send_message(
                owner, f"👥 «{getattr(user, 'full_name', '') or user.id}» با کد دعوت به اشتراک دفتر شما پیوست.\n"
                       "مدیریت همکاران: «حساب کاربری ← همکاران دفتر».")
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════
# یادآوری تمدید اشتراک (وظیفهٔ پس‌زمینه — از main.py اجرا می‌شود)
# ══════════════════════════════════════════════════════════════════
def _renew_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💳 تمدید اشتراک", callback_data="acc:renew")]])


async def send_renew_reminders(bot: Bot, soon_days: int) -> int:
    """یک دور بررسی و ارسال یادآوری‌ها؛ خروجی: تعداد پیام‌های ارسال‌شده."""
    sent = 0
    for r in access_control.due_renew_reminders(soon_days):
        uid, exp = r["user_id"], r["expires_at"]
        if await access_control.is_approved(uid):
            access_control.mark_reminded(uid, r["kind"], exp)
            continue
        title = SUBSCRIPTION_PLANS.get(r["plan"], {}).get("title", "اشتراک")
        members = await access_control.list_office_members(uid)
        office_note = (f"\n👥 {_fa(len(members))} همکار دفتر شما هم از همین اشتراک استفاده می‌کنند."
                       if members else "")
        if r["kind"] == "soon":
            text = (f"⏰ {title} شما {_fa(_days_left(exp))} روز دیگر ({fa_date(exp)}) به پایان می‌رسد.\n"
                    "برای جلوگیری از وقفه در استعلام‌ها، همین حالا تمدید کنید؛ مدت جدید به انتهای اشتراک "
                    "فعلی اضافه می‌شود و روزی از دست نمی‌رود." + office_note)
        else:
            text = (f"🔔 {title} شما در تاریخ {fa_date(exp)} به پایان رسید.\n"
                    "برای ادامهٔ استفاده از سامانه، اشتراک را تمدید کنید." + office_note)
        try:
            await bot.send_message(uid, text, reply_markup=_renew_kb())
            sent += 1
            db.log_event(f"remind_{r['kind']}", uid)
        except Exception as e:
            logger.warning(f"[RENEW] ارسال یادآوری به {uid} ناموفق: {e}")
        # حتی اگر ارسال ناموفق بود (مثلاً ربات بلاک شده)، دوباره تلاش نمی‌شود
        access_control.mark_reminded(uid, r["kind"], exp)
    return sent


async def renew_reminder_loop(bot: Bot, soon_days: int, interval: int):
    await asyncio.sleep(30)  # کمی بعد از راه‌اندازی
    while True:
        try:
            n = await send_renew_reminders(bot, soon_days)
            if n:
                logger.info(f"[RENEW] {n} یادآوری تمدید ارسال شد")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[RENEW] خطا در بررسی یادآوری‌ها: {e}", exc_info=True)
            if ADMIN_ID:
                try:
                    await bot.send_message(ADMIN_ID, f"⚠️ خطا در یادآوری تمدید اشتراک: {e}")
                except Exception:
                    pass
        await asyncio.sleep(interval)
