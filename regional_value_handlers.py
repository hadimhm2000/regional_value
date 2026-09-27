# -*- coding: utf-8 -*-
"""
هندلر بخش «استعلام ارزش منطقه‌ای ملک» (عرصه + اعیانی) — نسخهٔ تولید.

فلو (همگام با پروژهٔ اصلی online.judicial.services.ble):
  ۱. انتخاب استان
  ۲. آدرس: «ارسال لوکیشن» یا «وارد کردن آدرس دقیق» (با نام شهر در ابتدا) →
     یافتن روی نقشه (نشان) → ارسال لوکیشن برای تایید کاربر → خیر: انتخاب دستی روی نقشه
  ۳. ورود متراژ عرصه
  ۴. انتخاب کاربری زمین (مسکونی/تجاری/اداری/سایر)
     ↳ سایر: ۵ زیرگزینه با ضریب تعدیل (۰٫۷/۰٫۵/۰٫۴/۰٫۲/۰٫۱)
  ۵. سهم مالکانه عرصه (ownership_share): ششدانگ / دانگ / سهم از سهم / درصد
     ↳ «از کل ششدانگ است؟» خیر → حلقهٔ بعدی زنجیره (مثل «۲ سهم از ۴۸ سهم از ۳ دانگ»)
  ۶. ملک اعیانی دارد؟ (بله/خیر)
     ↳ اگر اعیانی دارد: «سهم اعیانی همان سهم عرصه است؟» خیر → سهم اعیانی جداگانه
  ۷. اعیانی (فقط اگر دارد): کاربری (مسکونی/تجاری/اداری/سایر ← صنعتی/کشاورزی) →
     نوع سازه → متراژ → تکمیل شده؟
       خیر → مرحلهٔ ساخت (فونداسیون/اسکلت/سفت‌کاری/نازک‌کاری)
       بله → پارکینگ و انباری (متراژ) → طبقه (فقط مسکونی/تجاری/اداری) → قدمت
     (متراژ اعیانی می‌تواند از متراژ عرصه بیشتر باشد — ساختمان چندطبقه)
  ۸. پلاک ثبتی (اختیاری — قابل رد شدن)
  ۹. پیش‌نمایش (تایید / ویرایش هر مورد)
  ۰. همان لحظهٔ زدن «استعلام ارزش منطقه‌ای»: کاربری که دسترسی ندارد (ثبت‌نشده /
     در انتظار تایید / پایان اعتبار) فوراً پیام معرفی / انتظار / خرید اشتراک را می‌بیند
  ۱۰. دسترسی (پس از تایید پیش‌نمایش):
       کاربر رایگانِ ادمین / مشترک فعال ← استعلام
       اعتبار رایگان (تست) ← مصرف یک اعتبار و استعلام
       بقیه ← متن معرفی سامانه + گزینهٔ «تست»:
         کد دفتر خدمات قضایی + کدملی مدیرعامل → ارسال برای ادمین →
         تایید ادمین = ۲ استعلام رایگان → پس از اتمام، انتخاب اشتراک
         ماهانه/سالانه → فاکتور بله → فعال‌شدن اشتراک
  ۱۱. استعلام عرصه از سامانهٔ مالیاتی + تعیین شهرستان از روی نقشه + محاسبهٔ
     اعیانی (ayani_calc) × سهم مالکانه → PDF دو صفحه‌ای (ayani_pdf) → ارسال

ویژگی‌های پایداری این نسخهٔ مستقل (حفظ‌شده):
  • هر استعلام حداکثر دو بار تلاش می‌شود (تلاش اول + یک تلاش مجدد خودکار).
  • تعداد استعلام‌های هم‌زمان با Semaphore محدود است.
  • هر مرحلهٔ شبکه‌ای/سنگین timeout دارد.
  • اگر استعلامی که با اعتبار رایگان انجام شده به‌خاطر خطای سیستمی ناتمام
    بماند، اعتبار مصرف‌شده بازگردانده می‌شود.
  • خروجی PDF با ارسال مستقیم multipart (bale_file_sender) — نه FSInputFile.
  • به ازای استعلام موفق پیامی به ادمین ارسال *نمی‌شود*؛ فقط خطاها.
  • «بازگشت» و «شروع مجدد» در همهٔ مرحله‌ها.
"""

import asyncio
import copy
import logging
import os
import re
import tempfile
import time

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, PreCheckoutQuery, ReplyKeyboardRemove, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

import ayani_calc
import ownership_share as osh
from config import ADMIN_ID, MAX_CONCURRENT_QUERIES, SUBSCRIPTION_PLANS, TRIAL_CREDITS
from tax_geolocation_query import get_province_list, find_land_use_value, extract_all_land_use_values
import access_control
from payment import send_subscription_invoice, parse_payload, INVOICE_PAYLOAD_TYPE
from bale_file_sender import send_document_direct
from keyboards import is_back, is_restart, main_menu_kb, nav_only_kb, nav_row

logger = logging.getLogger(__name__)

regional_value_router = Router()

LAND_USES = ["مسکونی", "تجاری", "اداری"]

# حداکثر تعداد استعلام‌هایی که هم‌زمان می‌توانند درگیر شبکه/CPU باشند
_query_semaphore = asyncio.Semaphore(MAX_CONCURRENT_QUERIES)

# سقف زمانی هر مرحلهٔ شبکه‌ای/سنگین
_STEP_TIMEOUT_SECONDS = 45


class RVForm(StatesGroup):
    waiting_province = State()
    waiting_address = State()           # انتخاب روش: ارسال لوکیشن / وارد کردن آدرس دقیق
    waiting_address_text = State()      # تایپ آدرس دقیق (با نام شهر در ابتدا)
    waiting_address_confirm = State()   # «آیا این لوکیشن با آدرس شما یکی است؟»
    waiting_address_location = State()  # انتخاب دستی لوکیشن روی نقشه
    waiting_area = State()
    waiting_land_use = State()
    waiting_land_other = State()        # زیرگزینهٔ «سایر» کاربری عرصه (ضریب تعدیل)
    waiting_has_building = State()      # ملک اعیانی دارد؟
    waiting_share_type = State()        # سهم مالکانه: نوع (ششدانگ/دانگ/سهم از سهم/درصد)
    waiting_share_amount = State()      # سهم مالکانه: مقدار (دانگ/درصد/صورت سهم)
    waiting_share_den = State()         # سهم مالکانه: تعداد کل سهام (مخرج)
    waiting_share_scope = State()       # سهم مالکانه: از کل ششدانگ است یا از بخشی از آن؟
    waiting_share_same = State()        # سهم اعیانی همان سهم عرصه است؟
    waiting_bld_use = State()           # کاربری اعیانی
    waiting_bld_use_other = State()     # زیرگزینهٔ «سایر» کاربری اعیانی
    waiting_bld_structure = State()     # نوع سازه
    waiting_bld_area = State()          # متراژ اعیانی
    waiting_bld_complete = State()      # ساختمان تکمیل شده؟
    waiting_bld_stage = State()         # مرحلهٔ ساخت (ناتمام)
    waiting_bld_parking = State()       # پارکینگ و انباری دارد؟
    waiting_bld_parking_area = State()  # متراژ پارکینگ و انباری
    waiting_bld_floor = State()         # طبقه
    waiting_bld_age = State()           # قدمت
    waiting_plak = State()              # مشخصات پلاک ثبتی (اختیاری)
    waiting_preview = State()           # پیش‌نمایش (تایید / ویرایش)
    waiting_edit_choice = State()       # انتخاب مورد ویرایش
    waiting_intro = State()             # متن معرفی سامانه + گزینهٔ «تست»
    waiting_office_code = State()       # کد دفتر خدمات قضایی
    waiting_national_id = State()       # کدملی مدیرعامل
    waiting_trial_approval = State()    # انتظار تایید ادمین
    waiting_plan = State()              # انتخاب اشتراک ماهانه/سالانه
    waiting_payment = State()


# ══════════════════════════════════════════════════════════════════
# کیبوردها و توابع کمکی
# ══════════════════════════════════════════════════════════════════
_YES, _NO = "✅ بله", "❌ خیر"
_CONFIRM, _EDIT = "✅ تایید و ادامه", "✏️ ویرایش"


def get_main_menu_kb(user_id: int = 0):
    return main_menu_kb()


_ADDR_LOCATION = "📍 ارسال موقعیت روی نقشه"
_ADDR_MANUAL = "✍️ وارد کردن آدرس دقیق"
_ADDR_OK = "✅ بله، درست است"
_ADDR_WRONG = "❌ خیر، درست نیست"

# انتخاب روش ورود آدرس
address_or_location_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=_ADDR_LOCATION, request_location=True)],
        [KeyboardButton(text=_ADDR_MANUAL)],
        nav_row(),
    ],
    resize_keyboard=True,
)

# فقط ارسال لوکیشن (بعد از «خیر» یا وقتی آدرس روی نقشه پیدا نشد)
location_only_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=_ADDR_LOCATION, request_location=True)], nav_row()],
    resize_keyboard=True,
)

address_confirm_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=_ADDR_OK), KeyboardButton(text=_ADDR_WRONG)], nav_row()],
    resize_keyboard=True,
)


def _to_fa(n) -> str:
    return str(n).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def _to_en(text: str) -> str:
    return (text or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))


def _num_kb(count: int, per_row: int = 5) -> ReplyKeyboardMarkup:
    """کیبورد شماره‌ای ۱..count + بازگشت/شروع مجدد."""
    nums = [KeyboardButton(text=_to_fa(i)) for i in range(1, count + 1)]
    rows = [nums[i:i + per_row] for i in range(0, len(nums), per_row)]
    rows.append(nav_row())
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


_yes_no_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=_YES), KeyboardButton(text=_NO)], nav_row()],
    resize_keyboard=True,
)

_preview_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=_CONFIRM), KeyboardButton(text=_EDIT)], nav_row()],
    resize_keyboard=True,
)


def _parse_choice(text: str, count: int):
    """«۲» / «2» / «۲. ...» → ایندکس صفر-مبنا، یا None."""
    t = _to_en(text).strip()
    digits = ""
    for ch in t:
        if ch.isdigit():
            digits += ch
        else:
            break
    if not digits:
        return None
    n = int(digits)
    return n - 1 if 1 <= n <= count else None


def _parse_number(text: str, *, integer=False, allow_negative=False, min_value=None, max_value=None):
    t = _to_en(text).strip().replace(",", "").replace("٬", "").replace("٫", ".").replace(" ", "")
    t = t.replace("−", "-").replace("–", "-").replace("‎", "").replace("‏", "")
    # «۱-» (منفی نوشته‌شده در سمت راست) هم پذیرفته می‌شود
    if t.endswith("-") and t.count("-") == 1:
        t = "-" + t[:-1]
    try:
        v = int(t) if integer else float(t)
    except (ValueError, TypeError):
        return None
    if not allow_negative and v < 0:
        return None
    if min_value is not None and v < min_value:
        return None
    if max_value is not None and v > max_value:
        return None
    return v


def _numbered(options: list) -> str:
    return "\n".join(f"{_to_fa(i)}. {o}" for i, o in enumerate(options, 1))


def _has_bld(d: dict) -> bool:
    """ملک اعیانی دارد؟ (فقط وقتی کاربر صریحاً «بله» زده باشد)"""
    return d.get("rv_has_building") is True


def _floor_applies(d: dict) -> bool:
    """سؤال طبقه فقط برای مسکونی/تجاری/اداریِ تکمیل‌شده (نه صنعتی/کشاورزی)."""
    return d.get("rv_bld_complete") is True and d.get("rv_bld_use") in ayani_calc.BUILDING_MAIN_KEYS


async def go_main_menu(message: Message, state: FSMContext, text: str = "🏠 به منوی اصلی بازگشتید."):
    """پاک‌کردن کامل وضعیت و نمایش منوی اصلی — مقصد «شروع مجدد» در همهٔ مرحله‌ها."""
    await state.clear()
    await message.answer(text, reply_markup=main_menu_kb())


async def _notify_admin(bot: Bot, text: str):
    if not ADMIN_ID:
        return
    try:
        await bot.send_message(ADMIN_ID, text)
    except Exception as e:
        logger.error(f"[RV] خطا در اطلاع‌رسانی به ادمین: {e}")


# ══════════════════════════════════════════════════════════════════
# موتور مراحل سناریو (جدول‌محور)
#
#  - هر مرحله: نام، کلیدهای داده، شرط لازم‌بودن.
#  - _advance: اولین مرحلهٔ لازمِ بی‌پاسخ را می‌پرسد؛ اگر همه پر بودند
#    پیش‌نمایش نمایش داده می‌شود.
#  - بازگشت: دادهٔ مرحلهٔ قبلی (و همهٔ مراحل بعد از آن) پاک و همان مرحله
#    دوباره پرسیده می‌شود.
#  - ویرایش از پیش‌نمایش: از داده‌ها عکس (snapshot) گرفته می‌شود، فیلد
#    انتخابی (و وابسته‌هایش) پاک و دوباره پرسیده می‌شود؛ بعد از پاسخ،
#    اگر مرحلهٔ تازه‌ای لازم شده باشد پرسیده و سپس به پیش‌نمایش برمی‌گردد.
#    «بازگشت» در حالت ویرایش = انصراف از ویرایش و برگشت به پیش‌نمایش.
# ══════════════════════════════════════════════════════════════════
_STEPS = [
    ("province", ["rv_province"], lambda d: True),
    ("address", ["rv_address", "rv_lat", "rv_lng", "rv_addr_pending"], lambda d: True),
    ("area", ["rv_area"], lambda d: True),
    ("land_use", ["rv_land_use"], lambda d: True),
    ("land_other", ["rv_land_other_idx"], lambda d: d.get("rv_land_use") == "سایر"),
    # سهم مالکانه عرصه — rv_share_wip: وضعیت موقت زیرفلوی ورود سهم (با پاک شدن مرحله پاک می‌شود)
    ("share_arse", ["rv_share_arse", "rv_share_arse_links", "rv_share_wip"], lambda d: True),
    ("has_building", ["rv_has_building"], lambda d: True),
    ("share_same", ["rv_share_same"], _has_bld),
    ("share_aayan", ["rv_share_aayan", "rv_share_aayan_links", "rv_share_wip"],
     lambda d: _has_bld(d) and d.get("rv_share_same") is False),
    ("bld_use", ["rv_bld_use", "rv_bld_use_other_pending"], _has_bld),
    ("bld_structure", ["rv_bld_structure"], _has_bld),
    ("bld_area", ["rv_bld_area"], _has_bld),
    ("bld_complete", ["rv_bld_complete"], _has_bld),
    ("bld_stage", ["rv_bld_stage"], lambda d: d.get("rv_bld_complete") is False),
    ("bld_parking", ["rv_bld_has_parking"], lambda d: d.get("rv_bld_complete") is True),
    ("bld_parking_area", ["rv_bld_parking_area"], lambda d: d.get("rv_bld_has_parking") is True),
    ("bld_floor", ["rv_bld_floor"], _floor_applies),
    ("bld_age", ["rv_bld_age"], lambda d: d.get("rv_bld_complete") is True),
    # آخرین مرحله: مشخصات پلاک ثبتی (اختیاری — «رد شدن» = رشتهٔ خالی)
    ("plak", ["rv_plak_text"], lambda d: True),
]
_STEP_INDEX = {name: i for i, (name, _, _) in enumerate(_STEPS)}
_ALL_KEYS = list(dict.fromkeys(k for _, keys, _ in _STEPS for k in keys))
# مراحل اعیانی (با «ندارد» همه پاک می‌شوند)
_BLD_STEPS = ["share_same", "share_aayan", "bld_use", "bld_structure", "bld_area", "bld_complete",
              "bld_stage", "bld_parking", "bld_parking_area", "bld_floor", "bld_age"]

# فیلدهای قابل ویرایش در پیش‌نمایش: (عنوان، مراحلی که پاک و دوباره پرسیده می‌شوند، شرط نمایش)
_EDIT_FIELDS = [
    ("استان", ["province", "address"], lambda d: True),
    ("آدرس / موقعیت روی نقشه", ["address"], lambda d: True),
    ("متراژ عرصه", ["area"], lambda d: True),
    ("کاربری زمین", ["land_use", "land_other"], lambda d: True),
    ("سهم مالکانه", ["share_arse", "share_same", "share_aayan"], lambda d: True),
    ("وجود اعیانی", ["has_building"], lambda d: True),
    ("کاربری اعیانی", ["bld_use"], _has_bld),
    ("نوع سازه", ["bld_structure"], _has_bld),
    ("متراژ اعیانی", ["bld_area"], _has_bld),
    ("وضعیت تکمیل ساختمان", ["bld_complete", "bld_stage", "bld_parking", "bld_parking_area",
                              "bld_floor", "bld_age"], _has_bld),
    ("مرحلهٔ ساخت", ["bld_stage"], lambda d: d.get("rv_bld_complete") is False),
    ("پارکینگ و انباری", ["bld_parking", "bld_parking_area"], lambda d: d.get("rv_bld_complete") is True),
    ("طبقه", ["bld_floor"], _floor_applies),
    ("قدمت ساختمان", ["bld_age"], lambda d: d.get("rv_bld_complete") is True),
    ("پلاک ثبتی", ["plak"], lambda d: True),
]


def _step_keys(names) -> dict:
    out = {}
    for n in names:
        for k in _STEPS[_STEP_INDEX[n]][1]:
            out[k] = None
    return out


def _first_missing(d: dict):
    for name, keys, required in _STEPS:
        if required(d) and d.get(keys[0]) is None:
            return name
    return None


async def _advance(message: Message, state: FSMContext):
    """اولین مرحلهٔ لازمِ بی‌پاسخ را بپرس؛ اگر همه کامل است → پیش‌نمایش."""
    d = await state.get_data()
    step = _first_missing(d)
    if step is None:
        await state.update_data(rv_edit_snapshot=None)
        await _show_preview(message, state)
        return
    await _ASK[step](message, state)


async def _go_back(message: Message, state: FSMContext, current: str):
    """
    بازگشت از مرحلهٔ فعلی به مرحلهٔ لازمِ قبلی؛ دادهٔ آن مرحله و همهٔ
    مراحل بعد از آن پاک می‌شود. در حالت ویرایش = انصراف و برگشت به پیش‌نمایش.
    قبل از مرحلهٔ استان = منوی اصلی.
    """
    d = await state.get_data()
    snapshot = d.get("rv_edit_snapshot")
    if snapshot:
        await state.update_data(**snapshot, rv_edit_snapshot=None)
        await _show_preview(message, state)
        return

    idx = _STEP_INDEX[current]
    prev = None
    for i in range(idx - 1, -1, -1):
        _, _, required = _STEPS[i]
        if required(d):
            prev = i
            break
    if prev is None:
        await go_main_menu(message, state)
        return
    await state.update_data(**_step_keys([n for n, _, _ in _STEPS[prev:]]))
    await _advance(message, state)


# ══════════════════════════════════════════════════════════════════
# نقطه ورود
# ══════════════════════════════════════════════════════════════════
async def regional_value_entry(message: Message, state: FSMContext):
    """شروع فرآیند استعلام (همهٔ داده‌های قبلی پاک می‌شود)."""
    await state.clear()
    await state.update_data(**{k: None for k in _ALL_KEYS}, rv_edit_snapshot=None)
    if await _entry_access_gate(message, state):
        return
    await _advance(message, state)


async def _entry_access_gate(message: Message, state: FSMContext) -> bool:
    """
    همان لحظهٔ زدن «استعلام ارزش منطقه‌ای»: اگر کاربر دسترسی ندارد (نه تاییدشدهٔ
    ادمین، نه اشتراک فعال، نه اعتبار رایگان)، به‌جای شروع فرم، پیام مناسب نمایش
    داده می‌شود. True = پیام نمایش داده شد و فرم شروع نشود.
    """
    user_id = message.from_user.id
    try:
        if await _has_free_access(user_id) or await access_control.has_free_retry(user_id):
            return False
        status = ((await access_control.get_trial_request(user_id)) or {}).get("status")
    except Exception as e:
        # خطای خواندن دسترسی نباید مانع استفاده شود — بررسی نهایی بعد از پیش‌نمایش انجام می‌شود
        logger.error(f"[RV] خطا در بررسی دسترسی ورودی کاربر {user_id}: {e}", exc_info=True)
        return False

    if status == "pending":
        await state.set_state(RVForm.waiting_trial_approval)
        await message.answer(
            "⏳ درخواست تست شما ثبت شده و در انتظار تایید ادمین است؛ نتیجه در اسرع وقت اعلام می‌گردد.",
            reply_markup=nav_only_kb(),
        )
    elif status == "approved":
        # تست استفاده شده → خرید اشتراک
        await _ask_plan(message, state, "🔔 استعلام‌های رایگان شما به پایان رسیده است.\n\n")
    else:
        # کاربر ثبت‌نشده (یا درخواست ردشده) → معرفی سامانه + گزینهٔ «تست»
        await _show_intro(message, state)
    return True


async def _back_from_access(message: Message, state: FSMContext):
    """«بازگشت» از مراحل دسترسی: اگر فرم پر شده → پیش‌نمایش، وگرنه → منوی اصلی."""
    if _form_complete(await state.get_data()):
        await _show_preview(message, state)
    else:
        await go_main_menu(message, state)


# ══════════════════════════════════════════════════════════════════
# «شروع مجدد» — در همهٔ مرحله‌ها (و حتی بدون state) کاربر را مستقیم به
# منوی اصلی برمی‌گرداند. باید اولین هندلر روتر باشد.
# ══════════════════════════════════════════════════════════════════
@regional_value_router.message(lambda m: bool(m.text) and is_restart(m.text))
async def rv_restart(message: Message, state: FSMContext):
    await go_main_menu(message, state)


# «ادامه استعلام» (پس از تایید تست / فعال‌شدن اشتراک) — در هر مرحله‌ای
@regional_value_router.message(lambda m: bool(m.text) and m.text.strip() == CONTINUE_BUTTON)
async def rv_continue(message: Message, state: FSMContext, bot: Bot):
    await process_continue(message, state, bot)


# ══════════════════════════════════════════════════════════════════
# مرحله ۱: انتخاب استان
# ══════════════════════════════════════════════════════════════════
async def _ask_province(message: Message, state: FSMContext):
    provinces = get_province_list()
    rows = [[KeyboardButton(text=p) for p in provinces[i:i + 3]] for i in range(0, len(provinces), 3)]
    rows.append(nav_row())
    await message.answer(
        "🗺️ *استعلام ارزش منطقه‌ای ملک*\n\nلطفاً استان مربوطه را انتخاب کنید:",
        reply_markup=ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True),
    )
    await state.set_state(RVForm.waiting_province)


@regional_value_router.message(RVForm.waiting_province)
async def process_province(message: Message, state: FSMContext):
    if not message.text:
        return
    if is_back(message.text):
        await _go_back(message, state, "province")
        return
    selected = message.text.strip()
    if selected not in get_province_list():
        await message.answer("⚠️ لطفاً یکی از استان‌های لیست را انتخاب کنید.")
        return
    await state.update_data(rv_province=selected)
    await _advance(message, state)


# ══════════════════════════════════════════════════════════════════
# مرحله ۲: ورود آدرس (متنی یا موقعیت مکانی روی نقشه)
# ══════════════════════════════════════════════════════════════════
async def _ask_address(message: Message, state: FSMContext):
    d = await state.get_data()
    await state.update_data(rv_addr_pending=None)
    await message.answer(
        f"✅ استان: *{d.get('rv_province', '')}*\n\n"
        f"📍 موقعیت ملک را چگونه اعلام می‌کنید؟\n\n"
        f"• «{_ADDR_LOCATION}»: نقطهٔ ملک را روی نقشه انتخاب و ارسال کنید\n"
        f"• «{_ADDR_MANUAL}»: آدرس را تایپ کنید؛ ربات آن را روی نقشه پیدا می‌کند و برای تایید شما می‌فرستد",
        reply_markup=address_or_location_kb,
    )
    await state.set_state(RVForm.waiting_address)


async def _ask_address_text(message: Message, state: FSMContext):
    d = await state.get_data()
    province = d.get("rv_province", "")
    example_city = ayani_calc.PROVINCE_CAPITALS.get(province, "تهران")
    await message.answer(
        "✍️ لطفاً آدرس دقیق ملک را تایپ کنید.\n\n"
        "⚠️ آدرس حتماً باید با *نام شهر* شروع شود.\n"
        f"مثال: {example_city}، خیابان ...، کوچه ...، پلاک ...",
        reply_markup=nav_only_kb(),
    )
    await state.set_state(RVForm.waiting_address_text)


async def _ask_manual_location(message: Message, state: FSMContext, reason: str = ""):
    await message.answer(
        (reason + "\n\n" if reason else "")
        + f"📍 لطفاً خودتان موقعیت دقیق ملک را روی نقشه انتخاب کنید و با دکمهٔ «{_ADDR_LOCATION}» ارسال نمایید.",
        reply_markup=location_only_kb,
    )
    await state.set_state(RVForm.waiting_address_location)


def _norm_txt(s: str) -> str:
    s = str(s or "").replace("ي", "ی").replace("ك", "ک").replace("‌", " ")
    s = s.replace("‏", "").replace("‎", "")
    return re.sub(r"\s+", " ", s).strip()


def _strip_city_prefix(s: str) -> str:
    return re.sub(r"^(استان|شهرستان|شهر)\s+", "", s)


def _starts_with_name(address: str, name: str) -> bool:
    """آیا آدرس با این نام شروع می‌شود؟ (فاصله/نیم‌فاصله درون نام اختیاری است: اسلامشهر = اسلام‌شهر)"""
    a = _strip_city_prefix(_norm_txt(address))
    n = _strip_city_prefix(_norm_txt(name)).replace(" ", "")
    if not n:
        return False
    pattern = r"\s?".join(map(re.escape, n)) + r"(?=$|[\s،,\-–:.()؛;/])"
    return re.match(pattern, a) is not None


_STREET_START = re.compile(
    r"^(خیابان|خ\.|کوچه|ک\.|بلوار|بزرگراه|اتوبان|میدان|م\.|چهارراه|سه ?راه|کوی|محله|شهرک|پلاک|بن ?بست|جاده|روستا)(?=$|[\s،,.])"
)


def _city_at_start(address: str, province: str):
    """نام شهر ابتدای آدرس را از بین شهرستان‌های همان استان پیدا می‌کند (یا None)."""
    names = []
    try:
        names = [c["county"] for c in ayani_calc._province_counties(province)]
    except Exception:
        pass
    names += [ayani_calc.PROVINCE_CAPITALS.get(province, ""), province]
    for name in sorted({n for n in names if n}, key=len, reverse=True):
        if _starts_with_name(address, name):
            return name
    return None


def _same_province(geo_province, province) -> bool:
    if not geo_province:
        return True  # نامشخص → رد نکن
    return ayani_calc.normalize_name(_strip_city_prefix(_norm_txt(geo_province))) == \
        ayani_calc.normalize_name(province)


@regional_value_router.message(RVForm.waiting_address, F.content_type == "text")
async def process_address(message: Message, state: FSMContext):
    if not message.text:
        return
    if is_back(message.text):
        await _go_back(message, state, "address")
        return
    if message.text.strip() == _ADDR_MANUAL:
        await _ask_address_text(message, state)
        return
    if message.text.strip() == _ADDR_LOCATION:
        await message.answer("📍 لطفاً روی دکمهٔ «ارسال موقعیت» بزنید و نقطهٔ ملک را روی نقشه انتخاب کنید.",
                             reply_markup=address_or_location_kb)
        return
    # آدرسی که مستقیماً (بدون زدن دکمه) تایپ شده هم همان مسیر «آدرس دقیق» را طی می‌کند
    await process_address_text(message, state)


@regional_value_router.message(RVForm.waiting_address_text, F.content_type == "text")
async def process_address_text(message: Message, state: FSMContext):
    if not message.text:
        return
    if is_back(message.text):
        await _ask_address(message, state)
        return
    address = _norm_txt(message.text)
    if len(address) < 8:
        await message.answer("⚠️ آدرس بسیار کوتاه است. لطفاً آدرس دقیق را با نام شهر در ابتدا وارد کنید.")
        await state.set_state(RVForm.waiting_address_text)
        return

    province = (await state.get_data()).get("rv_province", "")
    city = _city_at_start(address, province)
    example_city = ayani_calc.PROVINCE_CAPITALS.get(province, "تهران")
    no_city_msg = ("⚠️ ابتدای آدرس باید *نام شهر* باشد.\n"
                   f"لطفاً آدرس را دوباره وارد کنید؛ مثال: {example_city}، خیابان ...، کوچه ...")

    # آدرسی که با خیابان/کوچه/... شروع شده قطعاً نام شهر ندارد → بدون معطلی و بدون استعلام نقشه
    if city is None and _STREET_START.match(address):
        await message.answer(no_city_msg, reply_markup=nav_only_kb())
        await state.set_state(RVForm.waiting_address_text)
        return

    await message.answer("🔎 در حال یافتن آدرس روی نقشه...")
    geo = None
    try:
        from geocode_and_query import geocode_address
        loop = asyncio.get_running_loop()
        geo = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: geocode_address(address, city=city, province=province)),
            timeout=20,
        )
    except Exception as e:
        logger.warning(f"[RV] geocode آدرس ناموفق ({address}): {e}")

    # شهرهای کوچک‌تر (غیر مرکز شهرستان) از روی نتیجهٔ نقشه تایید می‌شوند
    if city is None and geo and geo.get("city") and _starts_with_name(address, geo["city"]):
        city = geo["city"]

    if city is None:
        await message.answer(no_city_msg, reply_markup=nav_only_kb())
        await state.set_state(RVForm.waiting_address_text)
        return

    if not geo:
        await state.update_data(rv_addr_pending={"address": address})
        await _ask_manual_location(message, state, "⚠️ آدرس واردشده روی نقشه پیدا نشد.")
        return

    if not _same_province(geo.get("province"), province):
        await message.answer(
            f"⚠️ این آدرس روی نقشه در استان «{geo.get('province')}» پیدا شد، نه «{province}».\n"
            f"لطفاً آدرس را اصلاح کنید یا با «بازگشت» موقعیت را روی نقشه ارسال کنید.",
            reply_markup=nav_only_kb(),
        )
        await state.set_state(RVForm.waiting_address_text)
        return

    await state.update_data(rv_addr_pending={"address": address, "lat": geo["lat"], "lng": geo["lng"]})
    try:
        await message.answer_location(latitude=geo["lat"], longitude=geo["lng"])
    except Exception as e:
        logger.warning(f"[RV] ارسال لوکیشن به کاربر ناموفق: {e}")
    await message.answer(
        f"🗺 موقعیت بالا از روی آدرس شما روی نقشه پیدا شد:\n📍 {address}\n\n"
        f"آیا این لوکیشن با آدرس ملک شما یکی است؟",
        reply_markup=address_confirm_kb,
    )
    await state.set_state(RVForm.waiting_address_confirm)


@regional_value_router.message(RVForm.waiting_address_confirm, F.content_type == "text")
async def process_address_confirm(message: Message, state: FSMContext):
    if not message.text:
        return
    text = message.text.strip()
    if is_back(text):
        await _ask_address_text(message, state)
        return
    pending = (await state.get_data()).get("rv_addr_pending") or {}
    if text in (_ADDR_OK, _YES, "بله") and pending.get("lat") is not None:
        await state.update_data(rv_address=pending["address"], rv_lat=pending["lat"],
                                rv_lng=pending["lng"], rv_addr_pending=None)
        await message.answer("✅ آدرس و موقعیت ملک ثبت شد.")
        await _advance(message, state)
        return
    if text in (_ADDR_WRONG, _NO, "خیر"):
        await _ask_manual_location(message, state)
        return
    await message.answer("⚠️ لطفاً یکی از گزینه‌های «بله» یا «خیر» را انتخاب کنید.", reply_markup=address_confirm_kb)


@regional_value_router.message(RVForm.waiting_address_location, F.content_type == "text")
async def process_address_location_text(message: Message, state: FSMContext):
    if not message.text:
        return
    if is_back(message.text):
        await _ask_address(message, state)
        return
    await message.answer(f"📍 لطفاً موقعیت ملک را با دکمهٔ «{_ADDR_LOCATION}» روی نقشه انتخاب و ارسال کنید.",
                         reply_markup=location_only_kb)


@regional_value_router.message(RVForm.waiting_address, F.content_type == "location")
@regional_value_router.message(RVForm.waiting_address_text, F.content_type == "location")
@regional_value_router.message(RVForm.waiting_address_confirm, F.content_type == "location")
@regional_value_router.message(RVForm.waiting_address_location, F.content_type == "location")
async def process_address_location(message: Message, state: FSMContext):
    """کاربر نقطه‌ای را روی نقشه انتخاب و ارسال کرده است (مستقیم یا بعد از «خیر»)."""
    lat, lng = message.location.latitude, message.location.longitude
    pending = (await state.get_data()).get("rv_addr_pending") or {}
    display_address = pending.get("address")  # آدرس تایپ‌شدهٔ کاربر (اگر قبلاً وارد کرده)
    if not display_address:
        # آدرس‌خوانی معکوس فقط برای نمایش — استعلام مستقیماً روی مختصات انجام می‌شود.
        try:
            from geocode_and_query import reverse_geocode
            loop = asyncio.get_running_loop()
            display_address = await asyncio.wait_for(
                loop.run_in_executor(None, reverse_geocode, lat, lng), timeout=15,
            )
        except Exception as e:
            logger.warning(f"[RV] reverse_geocode ناموفق: {e}")
            display_address = None
    if not display_address:
        display_address = f"مختصات انتخاب‌شده روی نقشه ({lat:.6f}, {lng:.6f})"

    await state.update_data(rv_address=display_address, rv_lat=lat, rv_lng=lng, rv_addr_pending=None)
    await message.answer(f"✅ موقعیت مکانی دریافت شد.\n📍 {display_address}")
    await _advance(message, state)


# ══════════════════════════════════════════════════════════════════
# مرحله ۳: متراژ عرصه
# ══════════════════════════════════════════════════════════════════
async def _ask_area(message: Message, state: FSMContext):
    await message.answer(
        "📐 لطفاً متراژ دقیق عرصه را به متر مربع وارد کنید:\n(مثال: 250)",
        reply_markup=nav_only_kb(),
    )
    await state.set_state(RVForm.waiting_area)


@regional_value_router.message(RVForm.waiting_area)
async def process_area(message: Message, state: FSMContext):
    if not message.text:
        return
    if is_back(message.text):
        await _go_back(message, state, "area")
        return
    area = _parse_number(message.text, min_value=0.01, max_value=1_000_000)
    if area is None:
        await message.answer("⚠️ متراژ نامعتبر است. لطفاً یک عدد مثبت (متر مربع) وارد کنید.")
        return
    # متراژ اعیانی (زیربنا) می‌تواند از عرصه بیشتر باشد؛ محدودیتی بین این دو نیست.
    await state.update_data(rv_area=area)
    await _advance(message, state)


# ══════════════════════════════════════════════════════════════════
# مرحله ۴: کاربری زمین (عرصه) + زیرگزینه‌های «سایر» (ضریب تعدیل)
# ══════════════════════════════════════════════════════════════════
LAND_USE_KEY_MAP = {
    "۱. مسکونی": "مسکونی", "مسکونی": "مسکونی",
    "۲. تجاری": "تجاری", "تجاری": "تجاری",
    "۳. اداری": "اداری", "اداری": "اداری",
    "۴. سایر": "سایر", "سایر": "سایر",
}


async def _ask_land_use(message: Message, state: FSMContext):
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="۱. مسکونی"), KeyboardButton(text="۲. تجاری")],
        [KeyboardButton(text="۳. اداری"), KeyboardButton(text="۴. سایر")],
        nav_row(),
    ], resize_keyboard=True)
    await message.answer("🏢 لطفاً کاربری زمین را انتخاب کنید:", reply_markup=kb)
    await state.set_state(RVForm.waiting_land_use)


@regional_value_router.message(RVForm.waiting_land_use)
async def process_land_use(message: Message, state: FSMContext):
    if not message.text:
        return
    if is_back(message.text):
        await _go_back(message, state, "land_use")
        return
    land_use = LAND_USE_KEY_MAP.get(message.text.strip())
    if not land_use:
        idx = _parse_choice(message.text, 4)
        land_use = ["مسکونی", "تجاری", "اداری", "سایر"][idx] if idx is not None else None
    if not land_use:
        await message.answer("⚠️ لطفاً یکی از گزینه‌های کاربری را انتخاب کنید.")
        return
    await state.update_data(rv_land_use=land_use, rv_land_other_idx=None)
    await _advance(message, state)


async def _ask_land_other(message: Message, state: FSMContext):
    await message.answer(
        "📋 نوع کاربری زمین را انتخاب کنید:\n\n"
        f"{_numbered([o['title'] for o in ayani_calc.LAND_OTHER_OPTIONS])}\n\n"
        "👇 شمارهٔ گزینهٔ مورد نظر را از دکمه‌های زیر انتخاب کنید:",
        reply_markup=_num_kb(len(ayani_calc.LAND_OTHER_OPTIONS)),
    )
    await state.set_state(RVForm.waiting_land_other)


@regional_value_router.message(RVForm.waiting_land_other)
async def process_land_other(message: Message, state: FSMContext):
    if not message.text:
        return
    if is_back(message.text):
        await _go_back(message, state, "land_other")
        return
    idx = _parse_choice(message.text, len(ayani_calc.LAND_OTHER_OPTIONS))
    if idx is None:
        await message.answer("⚠️ لطفاً فقط یکی از شماره‌های ۱ تا ۵ را انتخاب کنید.")
        return
    await state.update_data(rv_land_other_idx=idx)
    await _advance(message, state)


# ══════════════════════════════════════════════════════════════════
# مرحلهٔ ۵-ب: ملک اعیانی دارد؟ (بعد از سهم مالکانه عرصه)
# ══════════════════════════════════════════════════════════════════
async def _ask_has_building(message: Message, state: FSMContext):
    await message.answer(
        "🏗 آیا ملک دارای *اعیانی* (ساختمان / بنا) است؟\n\n"
        "• بله: سهم اعیانی و مشخصات ساختمان پرسیده می‌شود\n"
        "• خیر: مستقیماً به محاسبه می‌رود (فقط ارزش عرصه)",
        reply_markup=_yes_no_kb,
    )
    await state.set_state(RVForm.waiting_has_building)


@regional_value_router.message(RVForm.waiting_has_building)
async def process_has_building(message: Message, state: FSMContext):
    if not message.text:
        return
    if is_back(message.text):
        await _go_back(message, state, "has_building")
        return
    if message.text in (_YES, "بله"):
        # اگر قبلاً (در حالت ویرایش) اطلاعات اعیانی وارد شده، دست نخورده می‌ماند
        await state.update_data(rv_has_building=True)
    elif message.text in (_NO, "خیر"):
        # همهٔ داده‌های اعیانی و سهم اعیانی پاک می‌شود
        await state.update_data(rv_has_building=False, **_step_keys(_BLD_STEPS))
    else:
        await message.answer("⚠️ لطفاً «بله» یا «خیر» را انتخاب کنید.")
        return
    await _advance(message, state)


# ══════════════════════════════════════════════════════════════════
# مرحلهٔ ۵-الف و ۵-ج: سهم مالکانه (زیرفلوی چندمرحله‌ای — ownership_share)
#
#  rv_share_wip = {"target": "arse"|"aayan", "links": [...], "stage": ..., "kind": ..., "num": ...}
#    stage: type   → انتخاب نوع (ششدانگ / دانگ / سهم از سهم / درصد)
#           amount → مقدار دانگ / درصد / تعداد سهم مالک
#           den    → تعداد کل سهام (فقط «سهم از سهم»)
#           scope  → «این مقدار از کل ششدانگ است؟» خیر → حلقهٔ بعدی زنجیره
#  با تکمیل: rv_share_<target> = کسر نهایی («a/b») و rv_share_<target>_links = حلقه‌ها
# ══════════════════════════════════════════════════════════════════
_SHARE_TITLES = {"arse": "عرصه", "aayan": "اعیانی"}
_SHARE_STEP = {"arse": "share_arse", "aayan": "share_aayan"}

# (برچسب دکمه، نوع) — ششدانگ فقط برای حلقهٔ اول
_SHARE_KINDS_FIRST = [
    ("ششدانگ (مالکیت کامل)", osh.KIND_ALL),
    ("دانگ (مثلاً ۳ دانگ یا ۱٫۵ دانگ)", osh.KIND_DANG),
    ("سهم از سهم (مثلاً ۱۲٫۵ سهم از ۷۲ سهم)", osh.KIND_SAHM),
    ("درصد (مثلاً ۲۵٪)", osh.KIND_PERCENT),
]
_SHARE_KINDS_NEXT = _SHARE_KINDS_FIRST[1:]

_SCOPE_WHOLE = "✅ بله، از کل ششدانگ"
_SCOPE_PART = "↪️ خیر، از بخشی از ملک"
_scope_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=_SCOPE_WHOLE)], [KeyboardButton(text=_SCOPE_PART)],
              nav_row()],
    resize_keyboard=True,
)


def _share_kinds(wip: dict) -> list:
    return _SHARE_KINDS_FIRST if not wip["links"] else _SHARE_KINDS_NEXT


def _new_wip(target: str) -> dict:
    return {"target": target, "links": [], "stage": "type", "kind": None, "num": None}


async def _get_wip(state: FSMContext):
    """کپی مستقل از وضعیت زیرفلو (تغییر درجا روی دادهٔ FSM انجام نشود)."""
    return copy.deepcopy((await state.get_data()).get("rv_share_wip"))


async def _ask_share(message: Message, state: FSMContext, target: str):
    wip = await _get_wip(state)
    if not wip or wip.get("target") != target:
        wip = _new_wip(target)
        await state.update_data(rv_share_wip=wip)
    title = _SHARE_TITLES[target]
    stage = wip["stage"]

    if stage == "type":
        kinds = _share_kinds(wip)
        labels = [f"{_to_fa(i)}. {t}" for i, (t, _) in enumerate(kinds, 1)]
        kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=l)] for l in labels]
                                 + [nav_row()], resize_keyboard=True)
        if not wip["links"]:
            text = (
                f"⚖️ *سهم مالکانه از {title}*\n\n"
                f"مقدار مالکیت در سند (بخش «مقدار مالکیت» / «میزان سهم») به چه صورت نوشته شده است؟\n\n"
                f"{_numbered([t for t, _ in kinds])}\n\n"
                f"💡 اگر سند نوشته «ششدانگ … به‌استثنای ۱۲ سهم از ۷۲ سهم»، باقیمانده را وارد کنید "
                f"(۶۰ سهم از ۷۲ سهم)."
            )
        else:
            text = (
                f"📎 تا اینجا: «{osh.describe_chain(wip['links']).replace(' از ششدانگ', '')}»\n\n"
                f"این مقدار از *چه بخشی* از ملک است؟ نوع آن را انتخاب کنید:\n"
                f"(مثلاً در «۲ سهم از ۴۸ سهم از ۳ دانگ»، بخش دوم «۳ دانگ» است)\n\n"
                f"{_numbered([t for t, _ in kinds])}"
            )
        await message.answer(text, reply_markup=kb)
        await state.set_state(RVForm.waiting_share_type)
        return

    if stage == "amount":
        kind = wip["kind"]
        if kind == osh.KIND_DANG:
            text = ("🔢 مقدار *دانگ* را وارد کنید (بیشتر از ۰ و حداکثر ۶):\n"
                    "مثال: ۳ — ۱٫۵ — ۲ و نیم")
        elif kind == osh.KIND_PERCENT:
            text = "🔢 *درصد* مالکیت را وارد کنید (بیشتر از ۰ و حداکثر ۱۰۰):\nمثال: ۲۵ — ۱۲٫۵"
        else:
            text = ("🔢 *تعداد سهم مالک* را وارد کنید (عدد اول):\n"
                    "مثال: در «۱۲٫۵ سهم مشاع از ۷۲ سهم» عدد *۱۲٫۵*")
        await message.answer(text, reply_markup=nav_only_kb())
        await state.set_state(RVForm.waiting_share_amount)
        return

    if stage == "den":
        await message.answer(
            f"🔢 *تعداد کل سهام* را وارد کنید (عدد دوم):\n"
            f"مثال: در «۱۲٫۵ سهم مشاع از ۷۲ سهم» عدد *۷۲*\n\n"
            f"(سهم مالک: {osh.fmt_frac(wip['num'])} سهم)",
            reply_markup=nav_only_kb(),
        )
        await state.set_state(RVForm.waiting_share_den)
        return

    # stage == "scope"
    last = wip["links"][-1]
    await message.answer(
        f"✅ ثبت شد: «{osh.describe_link(last)}»\n\n"
        f"آیا این مقدار از *کل ششدانگ* ملک است؟\n\n"
        f"• بله: مثل «۳ دانگ مشاع از ششدانگ» یا «۱۲ سهم از ۷۲ سهم ششدانگ»\n"
        f"• خیر: مثل «۲ سهم از ۴۸ سهم *از ۳ دانگ*» — یعنی سهم از بخشی از ملک",
        reply_markup=_scope_kb,
    )
    await state.set_state(RVForm.waiting_share_scope)


async def _finish_share(message: Message, state: FSMContext, wip: dict):
    target = wip["target"]
    share = osh.chain_fraction(wip["links"])
    await state.update_data(**{
        f"rv_share_{target}": osh.frac_to_str(share),
        f"rv_share_{target}_links": wip["links"],
        "rv_share_wip": None,
    })
    await message.answer(
        f"✅ سهم مالکانه از {_SHARE_TITLES[target]}: *{osh.format_share(share)}*"
        + (f"\n↳ طبق سند: {osh.describe_chain(wip['links'])}" if _share_needs_detail(wip["links"]) else "")
    )
    await _advance(message, state)


def _share_needs_detail(links) -> bool:
    """شرح «طبق سند» فقط وقتی مفید است که با خود سهم نهایی تکراری نباشد."""
    return bool(links) and (len(links) > 1 or links[0]["k"] in (osh.KIND_SAHM, osh.KIND_PERCENT))


@regional_value_router.message(RVForm.waiting_share_type)
@regional_value_router.message(RVForm.waiting_share_amount)
@regional_value_router.message(RVForm.waiting_share_den)
@regional_value_router.message(RVForm.waiting_share_scope)
async def process_share(message: Message, state: FSMContext):
    if not message.text:
        return
    d = await state.get_data()
    wip = copy.deepcopy(d.get("rv_share_wip"))
    if not wip:
        # وضعیت زیرفلو از دست رفته (مثلاً ری‌استارت) → پرسیدن دوباره از اول همین مرحله
        await _advance(message, state)
        return
    target, stage = wip["target"], wip["stage"]
    text = message.text.strip()

    # ── بازگشت: یک گام به عقب در همین زیرفلو
    if is_back(text):
        if stage == "type":
            if not wip["links"]:
                await _go_back(message, state, _SHARE_STEP[target])
                return
            wip["stage"] = "scope"
        elif stage == "amount":
            wip.update(stage="type", kind=None, num=None)
        elif stage == "den":
            wip.update(stage="amount", num=None)
        else:  # scope → حذف آخرین حلقه و ورود دوبارهٔ مقدار آن
            last = wip["links"].pop()
            wip.update(stage="amount", kind=last["k"], num=None)
        await state.update_data(rv_share_wip=wip)
        await _advance(message, state)
        return

    # ── انتخاب نوع
    if stage == "type":
        kinds = _share_kinds(wip)
        # فقط دکمه یا شمارهٔ خالی پذیرفته می‌شود (مثلاً «۳ دانگ» تایپ‌شده نباید «گزینهٔ ۳» تعبیر شود)
        labels = {f"{_to_fa(i)}. {t}": i - 1 for i, (t, _) in enumerate(kinds, 1)}
        labels.update({t: i for i, (t, _) in enumerate(kinds)})
        idx = labels.get(text)
        if idx is None and _to_en(text).isdigit():
            idx = _parse_choice(text, len(kinds))
        if idx is None:
            await message.answer("⚠️ لطفاً یکی از گزینه‌ها را از دکمه‌های زیر انتخاب کنید.")
            return
        kind = kinds[idx][1]
        if kind == osh.KIND_ALL:
            wip["links"] = [osh.make_link(osh.KIND_ALL)]
            await _finish_share(message, state, wip)
            return
        wip.update(stage="amount", kind=kind, num=None)
        await state.update_data(rv_share_wip=wip)
        await _advance(message, state)
        return

    # ── ورود عدد (مقدار / صورت / مخرج)
    if stage in ("amount", "den"):
        value, err = osh.parse_amount(text)
        if err:
            await message.answer(f"⚠️ {osh.ERROR_HINTS[err]}")
            return
        try:
            if stage == "amount" and wip["kind"] == osh.KIND_SAHM:
                if value <= 0:
                    raise ValueError("تعداد سهم باید بزرگ‌تر از صفر باشد.")
                wip.update(stage="den", num=osh.frac_to_str(value))
                await state.update_data(rv_share_wip=wip)
                await _advance(message, state)
                return
            if stage == "den":
                link = osh.make_link(osh.KIND_SAHM, osh.frac_from_str(wip["num"]), value)
            else:
                link = osh.make_link(wip["kind"], value)
        except ValueError as e:
            await message.answer(f"⚠️ {e}\nلطفاً دوباره وارد کنید.")
            return
        wip["links"].append(link)
        wip.update(stage="scope", kind=None, num=None)
        await state.update_data(rv_share_wip=wip)
        await _advance(message, state)
        return

    # ── از کل ششدانگ است؟
    if text in (_SCOPE_WHOLE, _YES, "بله"):
        await _finish_share(message, state, wip)
        return
    if text in (_SCOPE_PART, _NO, "خیر"):
        if len(wip["links"]) >= osh.MAX_LINKS:
            await message.answer(
                f"⚠️ حداکثر {_to_fa(osh.MAX_LINKS)} مرحله برای سهم قابل ثبت است. "
                f"اگر مقدار آخر از کل ششدانگ است «{_SCOPE_WHOLE}» را بزنید، "
                f"در غیر این صورت با «بازگشت» مقادیر را اصلاح کنید.")
            return
        wip.update(stage="type", kind=None, num=None)
        await state.update_data(rv_share_wip=wip)
        await _advance(message, state)
        return
    await message.answer("⚠️ لطفاً یکی از گزینه‌های زیر را انتخاب کنید.")


async def _ask_share_same(message: Message, state: FSMContext):
    d = await state.get_data()
    await message.answer(
        f"🏗 آیا سهم شما از *اعیانی* هم همان «{osh.format_share(d.get('rv_share_arse') or '1')}» است؟\n\n"
        f"(در بیشتر اسناد، مقدار مالکیت برای «عرصه و اعیان» یکسان نوشته می‌شود. "
        f"اگر مثلاً عرصه وقفی است یا سهم اعیانی جداگانه ذکر شده، «خیر» را بزنید.)",
        reply_markup=_yes_no_kb,
    )
    await state.set_state(RVForm.waiting_share_same)


@regional_value_router.message(RVForm.waiting_share_same)
async def process_share_same(message: Message, state: FSMContext):
    if not message.text:
        return
    if is_back(message.text):
        await _go_back(message, state, "share_same")
        return
    if message.text in (_YES, "بله"):
        await state.update_data(rv_share_same=True, **_step_keys(["share_aayan"]))
    elif message.text in (_NO, "خیر"):
        await state.update_data(rv_share_same=False)
    else:
        await message.answer("⚠️ لطفاً «بله» یا «خیر» را انتخاب کنید.")
        return
    await _advance(message, state)


def _effective_shares(d: dict):
    """
    (سهم عرصه، سهم اعیانی) به‌صورت رشتهٔ کسری.
    پرونده‌های قدیمی (قبل از این فیچر) سهم ندارند → ششدانگ.
    """
    land = d.get("rv_share_arse") or "1"
    if d.get("rv_share_same") is False and d.get("rv_share_aayan"):
        return land, d["rv_share_aayan"]
    return land, land


def _share_summary_lines(d: dict) -> list:
    if not d.get("rv_share_arse"):
        return []
    land, bld = _effective_shares(d)

    def line(label, share, links):
        s = f"⚖️ {label}: {osh.format_share(share)}"
        if links and _share_needs_detail(links):
            s += f"\n     ↳ طبق سند: {osh.describe_chain(links)}"
        return s

    if not _has_bld(d):
        return [line("سهم مالکانه", land, d.get("rv_share_arse_links"))]
    if d.get("rv_share_same") is False:
        return [line("سهم مالکانه عرصه", land, d.get("rv_share_arse_links")),
                line("سهم مالکانه اعیانی", bld, d.get("rv_share_aayan_links"))]
    return [line("سهم مالکانه (عرصه و اعیان)", land, d.get("rv_share_arse_links"))]


# ══════════════════════════════════════════════════════════════════
# مرحله ۵: اعیانی — کاربری، نوع سازه، متراژ، وضعیت ساخت، پارکینگ، طبقه، قدمت
# ══════════════════════════════════════════════════════════════════
_BLD_MAIN = [("۱. مسکونی", "residential"), ("۲. تجاری", "commercial"),
             ("۳. اداری", "administrative"), ("۴. سایر", None)]


async def _ask_bld_use(message: Message, state: FSMContext):
    d = await state.get_data()
    if d.get("rv_bld_use_other_pending"):
        titles = [ayani_calc.BUILDING_USES[k] for k in ayani_calc.BUILDING_OTHER_KEYS]
        await message.answer(
            "📋 کاربری ساختمان را انتخاب کنید:\n\n"
            f"{_numbered(titles)}\n\n"
            "👇 شمارهٔ گزینهٔ مورد نظر را از دکمه‌های زیر انتخاب کنید:",
            reply_markup=_num_kb(len(titles)),
        )
        await state.set_state(RVForm.waiting_bld_use_other)
        return
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=_BLD_MAIN[0][0]), KeyboardButton(text=_BLD_MAIN[1][0])],
        [KeyboardButton(text=_BLD_MAIN[2][0]), KeyboardButton(text=_BLD_MAIN[3][0])],
        nav_row(),
    ], resize_keyboard=True)
    await message.answer("🏗 *اعیانی*\n\nکاربری ساختمان (اعیانی) را انتخاب کنید:", reply_markup=kb)
    await state.set_state(RVForm.waiting_bld_use)


@regional_value_router.message(RVForm.waiting_bld_use)
async def process_bld_use(message: Message, state: FSMContext):
    if not message.text:
        return
    if is_back(message.text):
        await _go_back(message, state, "bld_use")
        return
    idx = _parse_choice(message.text, len(_BLD_MAIN))
    if idx is None:
        by_title = {t.split(". ", 1)[1]: i for i, (t, _) in enumerate(_BLD_MAIN)}
        idx = by_title.get(message.text.strip())
    if idx is None:
        await message.answer("⚠️ لطفاً یکی از گزینه‌های کاربری را انتخاب کنید.")
        return
    use_key = _BLD_MAIN[idx][1]
    if use_key is None:
        await state.update_data(rv_bld_use=None, rv_bld_use_other_pending=True)
    else:
        await state.update_data(rv_bld_use=use_key, rv_bld_use_other_pending=None)
    await _advance(message, state)


@regional_value_router.message(RVForm.waiting_bld_use_other)
async def process_bld_use_other(message: Message, state: FSMContext):
    if not message.text:
        return
    if is_back(message.text):
        if (await state.get_data()).get("rv_edit_snapshot"):
            await _go_back(message, state, "bld_use")
            return
        # بازگشت به منوی اصلی کاربری اعیانی (انتخاب «سایر» پاک می‌شود)
        await state.update_data(rv_bld_use=None, rv_bld_use_other_pending=None)
        await _advance(message, state)
        return
    idx = _parse_choice(message.text, len(ayani_calc.BUILDING_OTHER_KEYS))
    if idx is None:
        await message.answer("⚠️ لطفاً فقط شمارهٔ ۱ یا ۲ را انتخاب کنید.")
        return
    await state.update_data(rv_bld_use=ayani_calc.BUILDING_OTHER_KEYS[idx], rv_bld_use_other_pending=None)
    await _advance(message, state)


async def _ask_structure(message: Message, state: FSMContext):
    await message.answer(
        "🧱 نوع سازه را انتخاب کنید:\n\n"
        f"{_numbered([ayani_calc.STRUCTURES['concrete'], ayani_calc.STRUCTURES['other']])}",
        reply_markup=_num_kb(2),
    )
    await state.set_state(RVForm.waiting_bld_structure)


@regional_value_router.message(RVForm.waiting_bld_structure)
async def process_bld_structure(message: Message, state: FSMContext):
    if not message.text:
        return
    if is_back(message.text):
        await _go_back(message, state, "bld_structure")
        return
    idx = _parse_choice(message.text, 2)
    if idx is None:
        await message.answer("⚠️ لطفاً فقط شمارهٔ ۱ یا ۲ را انتخاب کنید.")
        return
    await state.update_data(rv_bld_structure=["concrete", "other"][idx])
    await _advance(message, state)


async def _ask_bld_area(message: Message, state: FSMContext):
    await message.answer(
        "📐 لطفاً متراژ اعیانی (زیربنا) را به متر مربع وارد کنید:\n"
        "(مجموع زیربنای مندرج در سند — مثال: 120)",
        reply_markup=nav_only_kb(),
    )
    await state.set_state(RVForm.waiting_bld_area)


@regional_value_router.message(RVForm.waiting_bld_area)
async def process_bld_area(message: Message, state: FSMContext):
    if not message.text:
        return
    if is_back(message.text):
        await _go_back(message, state, "bld_area")
        return
    area = _parse_number(message.text, min_value=0.01, max_value=1_000_000)
    if area is None:
        await message.answer("⚠️ متراژ نامعتبر است. لطفاً یک عدد مثبت (متر مربع) وارد کنید.")
        return
    await state.update_data(rv_bld_area=area)
    await _advance(message, state)


async def _ask_complete(message: Message, state: FSMContext):
    await message.answer("🏠 آیا ساختمان تکمیل شده است؟", reply_markup=_yes_no_kb)
    await state.set_state(RVForm.waiting_bld_complete)


@regional_value_router.message(RVForm.waiting_bld_complete)
async def process_bld_complete(message: Message, state: FSMContext):
    if not message.text:
        return
    if is_back(message.text):
        await _go_back(message, state, "bld_complete")
        return
    if message.text in (_YES, "بله"):
        await state.update_data(rv_bld_complete=True, rv_bld_stage=None)
    elif message.text in (_NO, "خیر"):
        await state.update_data(rv_bld_complete=False, rv_bld_has_parking=None,
                                rv_bld_parking_area=None, rv_bld_floor=None, rv_bld_age=None)
    else:
        await message.answer("⚠️ لطفاً «بله» یا «خیر» را انتخاب کنید.")
        return
    await _advance(message, state)


async def _ask_stage(message: Message, state: FSMContext):
    titles = [s["title"] for s in ayani_calc.CONSTRUCTION_STAGES]
    await message.answer(
        "🚧 ساختمان در کدام مرحله از ساخت قرار دارد؟\n\n" f"{_numbered(titles)}",
        reply_markup=_num_kb(len(titles), per_row=4),
    )
    await state.set_state(RVForm.waiting_bld_stage)


@regional_value_router.message(RVForm.waiting_bld_stage)
async def process_bld_stage(message: Message, state: FSMContext):
    if not message.text:
        return
    if is_back(message.text):
        await _go_back(message, state, "bld_stage")
        return
    idx = _parse_choice(message.text, len(ayani_calc.CONSTRUCTION_STAGES))
    if idx is None:
        await message.answer("⚠️ لطفاً فقط یکی از شماره‌های ۱ تا ۴ را انتخاب کنید.")
        return
    await state.update_data(rv_bld_stage=ayani_calc.CONSTRUCTION_STAGES[idx]["key"])
    await _advance(message, state)


async def _ask_parking(message: Message, state: FSMContext):
    await message.answer(
        "🚗 آیا پارکینگ و انباری متعلق به هر واحد ساختمانی نسبت به ملک موجود می‌باشد؟",
        reply_markup=_yes_no_kb,
    )
    await state.set_state(RVForm.waiting_bld_parking)


@regional_value_router.message(RVForm.waiting_bld_parking)
async def process_bld_parking(message: Message, state: FSMContext):
    if not message.text:
        return
    if is_back(message.text):
        await _go_back(message, state, "bld_parking")
        return
    if message.text in (_YES, "بله"):
        await state.update_data(rv_bld_has_parking=True, rv_bld_parking_area=None)
    elif message.text in (_NO, "خیر"):
        await state.update_data(rv_bld_has_parking=False, rv_bld_parking_area=None)
    else:
        await message.answer("⚠️ لطفاً «بله» یا «خیر» را انتخاب کنید.")
        return
    await _advance(message, state)


async def _ask_parking_area(message: Message, state: FSMContext):
    await message.answer(
        "📐 متراژ پارکینگ و انباری را به متر مربع وارد کنید:\n(مجموع هر دو — مثال: 18)",
        reply_markup=nav_only_kb(),
    )
    await state.set_state(RVForm.waiting_bld_parking_area)


@regional_value_router.message(RVForm.waiting_bld_parking_area)
async def process_bld_parking_area(message: Message, state: FSMContext):
    if not message.text:
        return
    if is_back(message.text):
        await _go_back(message, state, "bld_parking_area")
        return
    p_area = _parse_number(message.text, min_value=0.01, max_value=1_000_000)
    if p_area is None:
        await message.answer("⚠️ متراژ نامعتبر است. لطفاً یک عدد مثبت (متر مربع) وارد کنید.")
        return
    await state.update_data(rv_bld_parking_area=p_area)
    await _advance(message, state)


async def _ask_floor(message: Message, state: FSMContext):
    await message.answer(
        "🏢 طبقهٔ واحد را وارد کنید (فقط عدد):\n\n"
        "• همکف = ۰\n"
        "• اگر طبقه زیر همکف می‌باشد، نماد منفی (-) را کنار عدد قرار دهید؛ مثلاً ‎-1\n"
        "(طبقات بدون احتساب زیرزمین و پیلوت شمرده می‌شوند)",
        reply_markup=nav_only_kb(),
    )
    await state.set_state(RVForm.waiting_bld_floor)


@regional_value_router.message(RVForm.waiting_bld_floor)
async def process_bld_floor(message: Message, state: FSMContext):
    if not message.text:
        return
    if is_back(message.text):
        await _go_back(message, state, "bld_floor")
        return
    floor = _parse_number(message.text, integer=True, allow_negative=True, min_value=-10, max_value=200)
    if floor is None:
        await message.answer("⚠️ لطفاً فقط یک عدد صحیح وارد کنید (مثلاً ۳، ۰ یا ‎-1).")
        return
    await state.update_data(rv_bld_floor=floor)
    await _advance(message, state)


async def _ask_age(message: Message, state: FSMContext):
    await message.answer("📅 قدمت ساختمان چند سال است؟ (فقط عدد — مثلاً ۵)", reply_markup=nav_only_kb())
    await state.set_state(RVForm.waiting_bld_age)


@regional_value_router.message(RVForm.waiting_bld_age)
async def process_bld_age(message: Message, state: FSMContext):
    if not message.text:
        return
    if is_back(message.text):
        await _go_back(message, state, "bld_age")
        return
    age = _parse_number(message.text, integer=True, min_value=0, max_value=300)
    if age is None:
        await message.answer("⚠️ لطفاً فقط یک عدد صحیح وارد کنید (مثلاً ۰ برای نوساز).")
        return
    await state.update_data(rv_bld_age=age)
    await _advance(message, state)


# ══════════════════════════════════════════════════════════════════
# مرحلهٔ آخر: مشخصات پلاک ثبتی (اختیاری)
# ══════════════════════════════════════════════════════════════════
_SKIP = "⏭ رد شدن"
PLAK_TEMPLATE = (
    "پلاک [عدد فرعی] فرعی از [عدد اصلی] اصلی، مفروز و مجزی‌شده از پلاک [فرعی مبدأ] فرعی از اصلی مذکور،\n"
    "واقع در بخش [شماره بخش] ثبتی [نام شهرستان/استان]"
)
PLAK_MAX_LEN = 600
_plak_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=_SKIP)], nav_row()],
    resize_keyboard=True,
)


async def _ask_plak(message: Message, state: FSMContext):
    await message.answer(
        "🧾 *مشخصات پلاک ثبتی ملک*\n\n"
        "لطفاً متن زیر را طبق سند مالکیت تکمیل کنید (موارد داخل [ ] را با اطلاعات سند جایگزین کنید) "
        "و ارسال نمایید:\n\n"
        f"{PLAK_TEMPLATE}\n\n"
        "مثال:\n"
        "پلاک ۱۲۳۴ فرعی از ۵۶ اصلی، مفروز و مجزی‌شده از پلاک ۱۲۰ فرعی از اصلی مذکور، "
        "واقع در بخش ۱۱ ثبتی تهران\n\n"
        f"در صورت تمایل نداشتن، دکمهٔ «{_SKIP}» را بزنید.",
        reply_markup=_plak_kb,
    )
    await state.set_state(RVForm.waiting_plak)


@regional_value_router.message(RVForm.waiting_plak)
async def process_plak(message: Message, state: FSMContext):
    if not message.text:
        await message.answer(f"⚠️ لطفاً مشخصات پلاک را به‌صورت متن ارسال کنید یا «{_SKIP}» را بزنید.")
        return
    text = message.text.strip()
    if is_back(text):
        await _go_back(message, state, "plak")
        return
    if text in (_SKIP, "رد شدن", "رد"):
        await state.update_data(rv_plak_text="")          # رشتهٔ خالی = رد شده (مرحله پاسخ داده شده)
        await _advance(message, state)
        return
    if "[" in text or "]" in text:
        await message.answer("⚠️ به نظر می‌رسد بخش‌های داخل [ ] هنوز تکمیل نشده‌اند. "
                             "لطفاً آن‌ها را با اطلاعات سند جایگزین کنید.")
        return
    if len(text) < 5 or not any(ch.isdigit() for ch in text):
        await message.answer("⚠️ متن واردشده کامل نیست؛ لطفاً شمارهٔ پلاک فرعی/اصلی و بخش ثبتی را "
                             f"طبق نمونه وارد کنید یا «{_SKIP}» را بزنید.")
        return
    if len(text) > PLAK_MAX_LEN:
        await message.answer(f"⚠️ متن بیش از حد طولانی است (حداکثر {_to_fa(PLAK_MAX_LEN)} نویسه).")
        return
    await state.update_data(rv_plak_text=text)
    await _advance(message, state)


_ASK = {
    "province": _ask_province, "address": _ask_address, "area": _ask_area,
    "land_use": _ask_land_use, "land_other": _ask_land_other,
    "has_building": _ask_has_building,
    "share_arse": lambda m, s: _ask_share(m, s, "arse"),
    "share_same": _ask_share_same,
    "share_aayan": lambda m, s: _ask_share(m, s, "aayan"),
    "bld_use": _ask_bld_use, "bld_structure": _ask_structure, "bld_area": _ask_bld_area,
    "bld_complete": _ask_complete, "bld_stage": _ask_stage, "bld_parking": _ask_parking,
    "bld_parking_area": _ask_parking_area, "bld_floor": _ask_floor, "bld_age": _ask_age,
    "plak": _ask_plak,
}


# ══════════════════════════════════════════════════════════════════
# مرحله ۶: پیش‌نمایش (تایید / ویرایش)
# ══════════════════════════════════════════════════════════════════
def _floor_text(f) -> str:
    if f is None:
        return "-"
    return "همکف" if f == 0 else (f"زیرزمین {abs(f)} (‎{f})" if f < 0 else str(f))


def _plak_summary_lines(data: dict) -> list:
    plak = data.get("rv_plak_text")
    if plak is None:            # پرونده‌های قدیمی
        return []
    return [f"🧾 پلاک ثبتی: {plak or 'وارد نشده'}"]


def _area_text(v) -> str:
    """متراژ با اعشار واقعی (۱۲۰٫۵ نباید ۱۲۱ نمایش داده شود)."""
    v = v or 0
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def _inputs_summary(data: dict) -> str:
    """خلاصهٔ ورودی‌های کاربر (پیش‌نمایش و پیام خطای ادمین)."""
    land_use = data.get("rv_land_use") or "-"
    if land_use == "سایر" and data.get("rv_land_other_idx") is not None:
        land_use = f"سایر — {ayani_calc.LAND_OTHER_OPTIONS[data['rv_land_other_idx']]['title']}"
    lines = [
        f"📍 استان: {data.get('rv_province') or '-'}",
        f"🗺 آدرس: {data.get('rv_address') or '-'}",
        f"📐 متراژ عرصه: {_area_text(data.get('rv_area'))} متر مربع",
        f"🏢 کاربری زمین: {land_use}",
    ]
    if data.get("rv_has_building") is False:
        lines.append("🏗 اعیانی: ندارد")
        lines += _share_summary_lines(data)
        lines += _plak_summary_lines(data)
        return "\n".join(lines)
    lines += _share_summary_lines(data)
    lines += [
        f"🏗 کاربری اعیانی: {ayani_calc.BUILDING_USES.get(data.get('rv_bld_use'), '-')}",
        f"🧱 نوع سازه: {ayani_calc.STRUCTURES.get(data.get('rv_bld_structure'), '-')}",
        f"📐 متراژ اعیانی: {_area_text(data.get('rv_bld_area'))} متر مربع",
    ]
    if data.get("rv_bld_complete"):
        p = data.get("rv_bld_parking_area") or 0
        lines.append("✅ وضعیت ساختمان: تکمیل‌شده")
        lines.append(f"🚗 پارکینگ و انباری: {p:,.0f} متر مربع" if p else "🚗 پارکینگ و انباری: ندارد")
        if _floor_applies(data):
            lines.append(f"🏢 طبقه: {_floor_text(data.get('rv_bld_floor'))}")
        lines.append(f"📅 قدمت: {data.get('rv_bld_age') or 0} سال")
    else:
        stage = next((s["title"] for s in ayani_calc.CONSTRUCTION_STAGES
                      if s["key"] == data.get("rv_bld_stage")), "-")
        lines.append(f"🚧 وضعیت ساختمان: ناتمام — مرحلهٔ {stage}")
    lines += _plak_summary_lines(data)
    return "\n".join(lines)


async def _show_preview(message: Message, state: FSMContext):
    d = await state.get_data()
    await message.answer(
        "📝 *پیش‌نمایش اطلاعات ملک*\n\n"
        f"{_inputs_summary(d)}\n\n"
        f"در صورت صحت اطلاعات «{_CONFIRM}» و در غیر این صورت «{_EDIT}» را بزنید.",
        reply_markup=_preview_kb,
    )
    await state.set_state(RVForm.waiting_preview)


@regional_value_router.message(RVForm.waiting_preview)
async def process_preview(message: Message, state: FSMContext, bot: Bot):
    if not message.text:
        return
    if is_back(message.text):
        # بازگشت از پیش‌نمایش = بازگشت به آخرین مرحله (دادهٔ آن پاک می‌شود)
        d = await state.get_data()
        last = [n for n, _, req in _STEPS if req(d)][-1]
        await state.update_data(**_step_keys([last]))
        await _advance(message, state)
        return
    if message.text == _CONFIRM:
        await _start_access_flow(message, state, bot)
        return
    if message.text == _EDIT:
        await _ask_edit_choice(message, state)
        return
    await message.answer(f"⚠️ لطفاً یکی از گزینه‌های «{_CONFIRM}» یا «{_EDIT}» را انتخاب کنید.")


def _edit_options(d: dict) -> list:
    return [(title, steps) for title, steps, cond in _EDIT_FIELDS if cond(d)]


async def _ask_edit_choice(message: Message, state: FSMContext):
    opts = _edit_options(await state.get_data())
    await message.answer(
        "✏️ کدام مورد را می‌خواهید ویرایش کنید؟\n\n" f"{_numbered([t for t, _ in opts])}",
        reply_markup=_num_kb(len(opts), per_row=4),
    )
    await state.set_state(RVForm.waiting_edit_choice)


@regional_value_router.message(RVForm.waiting_edit_choice)
async def process_edit_choice(message: Message, state: FSMContext):
    if not message.text:
        return
    if is_back(message.text):
        await _show_preview(message, state)
        return
    d = await state.get_data()
    opts = _edit_options(d)
    idx = _parse_choice(message.text, len(opts))
    if idx is None:
        await message.answer("⚠️ لطفاً شمارهٔ یکی از موارد را انتخاب کنید.")
        return
    snapshot = {k: d.get(k) for k in _ALL_KEYS}
    await state.update_data(**_step_keys(opts[idx][1]), rv_edit_snapshot=snapshot)
    await _advance(message, state)


# ══════════════════════════════════════════════════════════════════
# مرحله ۷: مسیر دسترسی
#   رایگانِ ادمین / مشترک فعال ← استعلام
#   اعتبار رایگان (تست) ← مصرف و استعلام
#   تست تاییدشده و اعتبار تمام‌شده ← انتخاب اشتراک
#   درخواست تست در انتظار ← پیام انتظار
#   بقیه ← متن معرفی سامانه + گزینهٔ «تست»
# ══════════════════════════════════════════════════════════════════
INTRO_TEXT = (
    "⚖️ سامانه تخصصی استعلام ارزش منطقه‌ای\n\n"
    "بادرود\n"
    "این سامانه به‌صورت تخصصی برای دفاتر خدمات الکترونیک قضایی طراحی شده و امکان دسترسی سریع و آسان "
    "به اطلاعات ارزش منطقه‌ای را فراهم می‌نماید.\n\n"
    "🟢 ویژه مدیران محترم دفاتر خدمات الکترونیک قضایی\n\n"
    "چنانچه مدیریت یک دفتر خدمات الکترونیک قضایی را بر عهده دارید، می‌توانید با تهیه اشتراک، "
    "به‌صورت مستقیم از امکانات سامانه استفاده نمایید.\n\n"
    "🔸️ دو استعلام کاملاً رایگان پیش از پرداخت\n\n"
    "پیش از تهیه اشتراک، امکان انجام ۲ استعلام به‌صورت کاملاً رایگان برای شما فراهم است تا بتوانید "
    "عملکرد و دقت سامانه را بررسی کرده و پس از اطمینان، نسبت به تهیه اشتراک اقدام فرمایید.\n\n"
    "تعرفه اشتراک\n\n"
    "🔹 اشتراک ماهانه: {monthly} تومان\n"
    "🔹 اشتراک سالانه: {yearly} تومان\n\n"
    "اشتراک سالانه با هدف فراهم‌سازی دسترسی مستمر و مقرون‌به‌صرفه برای دفاتر ارائه شده است.\n\n"
    "📌 جهت استفاده از دو استعلام رایگان و همچنین دریافت اطلاعات بیشتر گزینه تست را انتخاب بفرمائید.\n\n"
    "با احترام\n"
    "سامانه استعلام ارزش منطقه‌ای\n"
    "ویژه دفاتر خدمات الکترونیک قضایی"
)

_TEST_BUTTON = "🧪 تست"
CONTINUE_BUTTON = "▶️ ادامه استعلام"


def _fa_money(n: int) -> str:
    return _to_fa(f"{n:,}")


def _plan_button(key: str) -> str:
    p = SUBSCRIPTION_PLANS[key]
    return f"🔹 {p['title']} — {_fa_money(p['toman'])} تومان"


def _plan_from_text(text: str):
    t = (text or "").strip()
    for key in SUBSCRIPTION_PLANS:
        if t == _plan_button(key):
            return key
    if "ماهانه" in t:
        return "monthly"
    if "سالانه" in t or "سالیانه" in t:
        return "yearly"
    return None


def _intro_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=_TEST_BUTTON)], nav_row()], resize_keyboard=True)


def _plan_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=_plan_button("monthly"))],
                  [KeyboardButton(text=_plan_button("yearly"))],
                  nav_row()],
        resize_keyboard=True,
    )


def _continue_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=CONTINUE_BUTTON)], nav_row()], resize_keyboard=True)


def _form_complete(data: dict) -> bool:
    return bool(data) and bool(data.get("rv_province")) and _first_missing(data) is None


async def _has_free_access(user_id: int) -> bool:
    """کاربر رایگانِ ادمین یا مشترک فعال."""
    try:
        if await access_control.is_approved(user_id):
            return True
        return await access_control.get_subscription(user_id) is not None
    except Exception as e:
        logger.error(f"[RV] خطا در بررسی دسترسی کاربر {user_id}: {e}", exc_info=True)
        return False


async def _start_access_flow(message: Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id

    if await _has_free_access(user_id):
        await message.answer("⏳ در حال استعلام ارزش منطقه‌ای... لطفاً چند لحظه صبر کنید.",
                             reply_markup=ReplyKeyboardRemove())
        await run_regional_value_query(message, state, bot, refund_credit=False)
        return

    # ── استعلام رایگان (تست / جبران خطا) ──
    if await access_control.has_free_retry(user_id) and await access_control.consume_free_retry(user_id):
        await message.answer(
            "🎁 این استعلام با اعتبار رایگان شما انجام می‌شود.\n⏳ در حال استعلام... لطفاً چند لحظه صبر کنید.",
            reply_markup=ReplyKeyboardRemove(),
        )
        ok = await run_regional_value_query(message, state, bot, refund_credit=True)
        if ok:
            left = await access_control.free_retry_count(user_id)
            if left > 0:
                await message.answer(f"🎁 استعلام رایگان باقی‌مانده: {_to_fa(left)}", reply_markup=main_menu_kb())
            else:
                await _ask_plan(message, state, "🔔 استعلام‌های رایگان شما به پایان رسید.\n\n")
        return

    req = await access_control.get_trial_request(user_id)
    status = (req or {}).get("status")
    if status == "approved":
        # تست استفاده شده → خرید اشتراک
        await _ask_plan(message, state)
        return
    if status == "pending":
        await state.set_state(RVForm.waiting_trial_approval)
        await message.answer(
            "⏳ درخواست تست شما ثبت شده و در انتظار تایید ادمین است؛ نتیجه در اسرع وقت اعلام می‌گردد.",
            reply_markup=nav_only_kb(),
        )
        return

    # کاربر جدید (یا درخواست ردشده) → معرفی سامانه + گزینهٔ تست
    await _show_intro(message, state)


async def _show_intro(message: Message, state: FSMContext):
    await message.answer(
        INTRO_TEXT.format(monthly=_fa_money(SUBSCRIPTION_PLANS["monthly"]["toman"]),
                          yearly=_fa_money(SUBSCRIPTION_PLANS["yearly"]["toman"])),
        reply_markup=_intro_kb(),
    )
    await state.set_state(RVForm.waiting_intro)


@regional_value_router.message(RVForm.waiting_intro)
async def process_intro(message: Message, state: FSMContext):
    if not message.text:
        return
    if is_back(message.text):
        await _back_from_access(message, state)
        return
    if "تست" in message.text:
        await _ask_office_code(message, state)
        return
    await message.answer("⚠️ لطفاً گزینهٔ «تست» را انتخاب کنید.", reply_markup=_intro_kb())


# ── درخواست تست: کد دفتر + کدملی مدیرعامل ──
def _valid_national_id(code: str) -> bool:
    if len(code) != 10 or not code.isdigit() or len(set(code)) == 1:
        return False
    check = int(code[9])
    s = sum(int(code[i]) * (10 - i) for i in range(9)) % 11
    return check == s if s < 2 else check == 11 - s


async def _ask_office_code(message: Message, state: FSMContext):
    await message.answer("🏢 لطفاً کد دفتر خدمات الکترونیک قضایی را وارد کنید:", reply_markup=nav_only_kb())
    await state.set_state(RVForm.waiting_office_code)


@regional_value_router.message(RVForm.waiting_office_code)
async def process_office_code(message: Message, state: FSMContext):
    if not message.text:
        return
    if is_back(message.text):
        await _show_intro(message, state)
        return
    code = _to_en(message.text).strip().replace(" ", "")
    if not code.isdigit() or not (3 <= len(code) <= 12):
        await message.answer("⚠️ کد دفتر نامعتبر است. لطفاً فقط عدد وارد کنید.")
        return
    await state.update_data(rv_office_code=code)
    await message.answer("🪪 لطفاً کدملی مدیرعامل (مدیر دفتر) را وارد کنید:", reply_markup=nav_only_kb())
    await state.set_state(RVForm.waiting_national_id)


@regional_value_router.message(RVForm.waiting_national_id)
async def process_national_id(message: Message, state: FSMContext, bot: Bot):
    if not message.text:
        return
    if is_back(message.text):
        await _ask_office_code(message, state)
        return
    nid = _to_en(message.text).strip().replace(" ", "").replace("-", "")
    if not _valid_national_id(nid):
        await message.answer("⚠️ کدملی نامعتبر است. لطفاً کدملی ۱۰ رقمی صحیح را وارد کنید.")
        return

    user = message.from_user
    office_code = (await state.get_data()).get("rv_office_code", "")
    dupes = await access_control.save_trial_request(
        user.id, office_code, nid,
        full_name=getattr(user, "full_name", "") or "", username=getattr(user, "username", "") or "",
    )

    await state.set_state(RVForm.waiting_trial_approval)
    await message.answer(
        "✅ اطلاعات شما ثبت شد.\n\n"
        "⏳ لطفاً منتظر باشید تا اطلاعات شما توسط ادمین تایید گردد؛ نتیجه در اسرع وقت اعلام می‌گردد.",
        reply_markup=nav_only_kb(),
    )

    if not ADMIN_ID:
        logger.error("[RV] ADMIN_ID تنظیم نشده — درخواست تست قابل ارسال برای ادمین نیست.")
        return
    text = (
        "🧪 درخواست تست (۲ استعلام رایگان)\n\n"
        f"👤 کاربر: {getattr(user, 'full_name', '')}"
        + (f" (@{user.username})" if getattr(user, "username", None) else "") + "\n"
        f"🆔 آیدی عددی: {user.id}\n"
        f"🏢 کد دفتر خدمات قضایی: {office_code}\n"
        f"🪪 کدملی مدیرعامل: {nid}"
    )
    if dupes:
        text += "\n\n⚠️ هشدار تکراری:\n" + "\n".join(f"• {d}" for d in dupes)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ تایید", callback_data=f"trial_ok:{user.id}"),
        InlineKeyboardButton(text="❌ رد", callback_data=f"trial_no:{user.id}"),
    ]])
    try:
        await bot.send_message(ADMIN_ID, text, reply_markup=kb)
    except Exception as e:
        logger.error(f"[RV] ارسال درخواست تست به ادمین ناموفق بود: {e}")


@regional_value_router.message(RVForm.waiting_trial_approval)
async def process_trial_waiting(message: Message, state: FSMContext, bot: Bot):
    if message.text and is_back(message.text):
        await _back_from_access(message, state)
        return
    if message.text and message.text.strip() == CONTINUE_BUTTON:
        await _start_access_flow(message, state, bot)
        return
    await message.answer(
        "⏳ درخواست شما در انتظار تایید ادمین است؛ نتیجه در اسرع وقت اعلام می‌گردد.",
        reply_markup=nav_only_kb(),
    )


async def process_continue(message: Message, state: FSMContext, bot: Bot):
    """دکمهٔ «ادامه استعلام» (پس از تایید تست یا فعال‌شدن اشتراک) — در هر وضعیتی."""
    if _form_complete(await state.get_data()):
        await _start_access_flow(message, state, bot)
    else:
        await regional_value_entry(message, state)


# ── انتخاب اشتراک ──
async def _ask_plan(message: Message, state: FSMContext, prefix: str = ""):
    await message.answer(prefix + "💳 لطفاً اشتراک مدنظر خود را تعیین بفرمائید:", reply_markup=_plan_kb())
    await state.set_state(RVForm.waiting_plan)


@regional_value_router.message(RVForm.waiting_plan)
async def process_plan(message: Message, state: FSMContext, bot: Bot):
    if not message.text:
        return
    if is_back(message.text):
        if _form_complete(await state.get_data()):
            await _show_preview(message, state)
        else:
            await go_main_menu(message, state)
        return
    plan = _plan_from_text(message.text)
    if not plan:
        await message.answer("⚠️ لطفاً یکی از گزینه‌های ماهانه یا سالانه را انتخاب کنید.", reply_markup=_plan_kb())
        return
    user_id = message.from_user.id
    if await send_subscription_invoice(bot, user_id, plan):
        await state.update_data(rv_plan=plan)
        await state.set_state(RVForm.waiting_payment)
        await message.answer(
            f"💳 فاکتور {SUBSCRIPTION_PLANS[plan]['title']} ارسال شد؛ لطفاً آن را پرداخت کنید.\n"
            "پس از پرداخت، اشتراک شما خودکار فعال می‌شود"
            + (" و استعلام شروع می‌شود." if _form_complete(await state.get_data()) else "."),
            reply_markup=nav_only_kb(),
        )
    else:
        await _notify_admin(bot, f"⚠️ ارسال فاکتور اشتراک برای کاربر {user_id} شکست خورد (بررسی BALE_WALLET_TOKEN).")
        await message.answer("⚠️ در حال حاضر امکان ساخت فاکتور پرداخت نیست. لطفاً کمی بعد دوباره تلاش کنید.",
                             reply_markup=_plan_kb())


# ══════════════════════════════════════════════════════════════════
# پرداخت: تایید pre_checkout + فعال‌سازی اشتراک پس از پرداخت موفق
# ══════════════════════════════════════════════════════════════════
@regional_value_router.pre_checkout_query()
async def rv_pre_checkout(pre_checkout_query: PreCheckoutQuery, bot: Bot):
    payload = parse_payload(getattr(pre_checkout_query, "invoice_payload", ""))
    plan = SUBSCRIPTION_PLANS.get(payload.get("plan"))
    ok = payload.get("type") == INVOICE_PAYLOAD_TYPE and plan is not None \
        and getattr(pre_checkout_query, "total_amount", plan["rial"] if plan else 0) == (plan or {}).get("rial")
    try:
        if ok:
            await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)
        else:
            await bot.answer_pre_checkout_query(
                pre_checkout_query.id, ok=False, error_message="فاکتور نامعتبر است؛ لطفاً دوباره اشتراک را انتخاب کنید.")
    except Exception as e:
        logger.error(f"[RV-PAYMENT] خطا در answer_pre_checkout_query: {e}")


@regional_value_router.message(F.successful_payment)
async def rv_successful_payment(message: Message, state: FSMContext, bot: Bot):
    """
    پرداخت موفق در هر وضعیتی پردازش می‌شود (کاربر ممکن است پس از دریافت
    فاکتور «بازگشت»/«شروع مجدد» زده باشد). اشتراک فعال می‌شود و اگر اطلاعات
    فرم کامل باشد، استعلام بلافاصله اجرا می‌شود.
    """
    user_id = message.from_user.id
    sp = message.successful_payment
    payload = parse_payload(getattr(sp, "invoice_payload", ""))
    plan_key = payload.get("plan") if payload.get("type") == INVOICE_PAYLOAD_TYPE else None
    plan_key = plan_key or (await state.get_data()).get("rv_plan")
    logger.info(f"[RV-PAYMENT] پرداخت موفق از کاربر {user_id} — پلن={plan_key}")

    plan = SUBSCRIPTION_PLANS.get(plan_key)
    if not plan:
        # پرداختی با payload ناشناخته (مثلاً فاکتور قدیمی تک‌استعلام) → یک اعتبار رایگان
        await access_control.grant_free_retry(user_id)
        await _notify_admin(bot, f"⚠️ پرداخت با payload ناشناخته از کاربر {user_id}: {payload} — یک اعتبار رایگان ثبت شد.")
        await message.answer("✅ پرداخت شما تایید شد و یک استعلام رایگان برایتان ثبت شد.", reply_markup=_continue_kb())
        return

    try:
        sub = await access_control.activate_subscription(user_id, plan_key, plan["days"])
    except Exception as e:
        logger.error(f"[RV] خطا در فعال‌سازی اشتراک {user_id}: {e}", exc_info=True)
        await _notify_admin(bot, f"🛑 پرداخت {plan['title']} کاربر {user_id} دریافت شد ولی فعال‌سازی اشتراک شکست خورد: {e}")
        await message.answer("✅ پرداخت شما دریافت شد؛ فعال‌سازی اشتراک با خطا مواجه شد و به ادمین اطلاع داده شد.")
        return

    await _notify_admin(bot, f"💳 خرید {plan['title']} — کاربر {getattr(message.from_user, 'full_name', '')} ({user_id})")
    expiry = _jalali_date(sub["expires_at"])
    head = f"✅ پرداخت تایید شد و {plan['title']} شما تا {expiry} فعال است."
    if _form_complete(await state.get_data()):
        await message.answer(head + "\n⏳ در حال استعلام... لطفاً چند لحظه صبر کنید.", reply_markup=ReplyKeyboardRemove())
        await run_regional_value_query(message, state, bot, refund_credit=False)
    else:
        await go_main_menu(message, state, head + "\nبرای استعلام، دکمهٔ «استعلام ارزش منطقه‌ای» را بزنید.")


def _jalali_date(ts: float) -> str:
    import datetime
    d = datetime.datetime.fromtimestamp(ts)
    try:
        import jdatetime
        return _to_fa(jdatetime.date.fromgregorian(date=d.date()).strftime("%Y/%m/%d"))
    except Exception:
        try:
            from regional_value_pdf import _gregorian_to_jalali
            y, m, dd = _gregorian_to_jalali(d.year, d.month, d.day)
            return _to_fa(f"{y}/{m:02d}/{dd:02d}")
        except Exception:
            return d.strftime("%Y-%m-%d")


@regional_value_router.message(RVForm.waiting_payment)
async def rv_payment_step_nav(message: Message, state: FSMContext):
    """مرحلهٔ انتظار برای پرداخت: «بازگشت» = برگشت به انتخاب اشتراک."""
    if message.text and is_back(message.text):
        await _ask_plan(message, state)
        return
    if message.text:
        await message.answer(
            "⏳ منتظر پرداخت فاکتور هستیم. برای تغییر اشتراک «بازگشت» و برای منوی اصلی «شروع مجدد» را بزنید.",
            reply_markup=nav_only_kb(),
        )


# ══════════════════════════════════════════════════════════════════
# اجرای استعلام: محدودیت هم‌زمانی + تلاش مجدد خودکار + مدیریت خطا
# ══════════════════════════════════════════════════════════════════
_REFUND_NOTE = "\n💚 اعتبار استعلام رایگان شما بازگردانده شد."


async def _system_failure(message, state, bot, user_id, refund_credit, admin_text, user_text):
    """خطای سیستمی: اطلاع به ادمین + بازگرداندن اعتبار مصرف‌شده + پیام به کاربر."""
    await _notify_admin(bot, admin_text)
    if refund_credit:
        try:
            await access_control.grant_free_retry(user_id)
        except Exception as e:
            logger.error(f"[RV] خطا در بازگرداندن اعتبار رایگان برای {user_id}: {e}")
    await message.answer(user_text + (_REFUND_NOTE if refund_credit else ""),
                         reply_markup=get_main_menu_kb(user_id))
    await state.clear()


def _result_values_text(calc: dict, bold: bool = True) -> str:
    """متن نتیجه برای کاربر؛ با سهم مالکانه، ارزش کامل (ششدانگ) هم برای شفافیت ذکر می‌شود."""
    def money(v):
        txt = f"{v:,} ریال"
        return f"*{txt}*" if bold else txt

    land, bld = calc["land"], calc["building"]
    land_share = osh.frac_from_str(calc.get("land_share") or "1")
    bld_share = osh.frac_from_str(calc.get("building_share") or "1")
    lines = []
    if land_share != 1:
        lines.append(f"1️⃣ ارزش سهم عرصه: {money(calc['land_value'])}")
        lines.append(f"     ↳ سهم مالکانه: {osh.format_share(land_share)}")
        lines.append(f"     ↳ ارزش ششدانگ عرصه: {land['value']:,} ریال")
    else:
        lines.append(f"1️⃣ ارزش عرصه: {money(calc['land_value'])}")
    if bld is None:
        lines.append("2️⃣ ارزش اعیانی: ملک فاقد اعیانی است")
    elif bld_share != 1:
        lines.append(f"2️⃣ ارزش سهم اعیانی: {money(calc['building_value'])}")
        lines.append(f"     ↳ سهم مالکانه: {osh.format_share(bld_share)}")
        lines.append(f"     ↳ ارزش ششدانگ اعیانی: {bld['value']:,} ریال")
    else:
        lines.append(f"2️⃣ ارزش اعیانی: {money(calc['building_value'])}")
    lines.append(f"3️⃣ ارزش منطقه‌ای کل: {money(calc['total'])}")
    return "\n".join(lines)


async def run_regional_value_query(message: Message, state: FSMContext, bot: Bot, refund_credit: bool) -> bool:
    """اجرای استعلام؛ True فقط وقتی گزارش PDF با موفقیت به کاربر رسیده باشد."""
    user_id = message.from_user.id
    data = await state.get_data()

    province = data.get("rv_province", "")
    address = data.get("rv_address", "")
    rv_lat = data.get("rv_lat")
    rv_lng = data.get("rv_lng")
    area = data.get("rv_area", 0)
    land_use = data.get("rv_land_use", "مسکونی")

    async with _query_semaphore:
        loop = asyncio.get_running_loop()
        result = None
        last_error = None

        # ── تلاش اول + یک تلاش مجدد خودکار در صورت خطای سیستمی ──
        for attempt in (1, 2):
            try:
                result = await asyncio.wait_for(
                    loop.run_in_executor(None, _do_query_sync, province, address, rv_lat, rv_lng),
                    timeout=_STEP_TIMEOUT_SECONDS,
                )
                last_error = None
                break
            except Exception as e:
                last_error = e
                logger.error(f"[RV] خطا در تلاش {attempt} استعلام برای کاربر {user_id}: {e}", exc_info=True)
                if attempt == 1:
                    await asyncio.sleep(2)

        if last_error is not None:
            await _system_failure(
                message, state, bot, user_id, refund_credit,
                f"🛑 استعلام ارزش منطقه‌ای برای کاربر {user_id} پس از ۲ تلاش شکست خورد.\n"
                f"استان: {province} | آدرس: {address}\nخطا: {last_error}",
                "⚠️ در حال حاضر سامانهٔ استعلام با مشکل مواجه است.\nلطفاً *۳۰ دقیقه دیگر* دوباره تلاش کنید.",
            )
            return

        try:
            tax_result = result.get("tax_info", {}) if result else {}

            if not tax_result.get("فیلدهای_ساختاریافته"):
                await message.answer(
                    "⚠️ متاسفانه نتیجه‌ای از سامانه مالیاتی دریافت نشد.\n"
                    "ممکن است آدرس دقیق نباشد یا مختصات خارج از محدوده تعریف‌شده باشد.",
                    reply_markup=get_main_menu_kb(user_id),
                )
                return

            all_lu_values = extract_all_land_use_values(tax_result)
            if not any(v is not None for v in all_lu_values.values()):
                await message.answer(
                    "🛑 منطقهٔ مورد نظر شما در سایت اداره امور مالیاتی، ارزش منطقه‌ای ثبت نشده است.",
                    reply_markup=get_main_menu_kb(user_id),
                )
                return

            # ── عرصه: ارزش واحد از سامانه (برای «سایر» مبنای مسکونی × ضریب تعدیل) ──
            land = ayani_calc.compute_land_value(
                area, land_use, all_lu_values, other_index=data.get("rv_land_other_idx"),
            )
            if not land["ok"]:
                available = [
                    f"{lu}: {v:,} ریال" for lu in LAND_USES
                    if (v := find_land_use_value(tax_result, lu)) is not None
                ]
                avail_text = "\n".join(available) if available else "هیچ مقداری یافت نشد"
                await message.answer(
                    f"⚠️ کاربری *{land_use}* برای این موقعیت تعریف نشده است.\n\nارزش‌های موجود:\n{avail_text}",
                    reply_markup=get_main_menu_kb(user_id),
                )
                return

            # ── وجود اعیانی و سهم مالکانه ──
            # پرونده‌های قدیمی (قبل از سؤال «اعیانی دارد؟») همیشه اعیانی داشتند → True
            has_building = data.get("rv_has_building") is not False
            land_share, bld_share = _effective_shares(data)

            # ── اعیانی: تعیین شهرستان از روی نقشه (هرگز «یافت نشد» نمی‌دهد) ──
            geo = result.get("geocoded") or {}
            g_lat = rv_lat if rv_lat is not None else geo.get("lat")
            g_lng = rv_lng if rv_lng is not None else geo.get("lng")

            def _resolve():
                hints = []
                if g_lat is not None and g_lng is not None:
                    try:
                        hints = ayani_calc.detect_location_names(g_lat, g_lng)
                    except Exception as e:
                        logger.warning(f"[RV] detect_location_names ناموفق: {e}")
                if geo.get("city"):
                    hints.append(geo["city"])
                return ayani_calc.resolve_county(province, g_lat, g_lng, hints)

            async def _resolve_county():
                try:
                    return await asyncio.wait_for(loop.run_in_executor(None, _resolve),
                                                  timeout=_STEP_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    # بدون نام‌یابی نقشه — فقط با مختصات/مرکز استان
                    return await loop.run_in_executor(
                        None, lambda: ayani_calc.resolve_county(province, g_lat, g_lng, []))

            if has_building:
                county_info = await _resolve_county()
            else:
                # بدون اعیانی، شهرستان فقط برای نمایش است — شکست آن نباید مانع نتیجه شود
                try:
                    county_info = await _resolve_county()
                except Exception as county_err:
                    logger.warning(f"[RV] تعیین شهرستان (بدون اعیانی) ناموفق: {county_err}")
                    county_info = {"county": geo.get("city") or "-", "rates": None,
                                   "method": "none", "hint": geo.get("city")}
            logger.info(f"[RV] شهرستان اعیانی: {county_info['county']} "
                        f"(روش={county_info['method']}, نقشه={county_info.get('hint')})")

            building = None
            if has_building:
                building = ayani_calc.compute_building_value(
                    county_info["rates"],
                    use_key=data.get("rv_bld_use") or "residential",
                    structure=data.get("rv_bld_structure") or "concrete",
                    area=data.get("rv_bld_area") or 0,
                    complete=bool(data.get("rv_bld_complete")),
                    stage_key=data.get("rv_bld_stage") or "foundation",
                    parking_area=data.get("rv_bld_parking_area") or 0,
                    floor=data.get("rv_bld_floor"),
                    age=data.get("rv_bld_age") or 0,
                )
            calc = ayani_calc.compute_all(land, building, land_share=land_share, building_share=bld_share)
            if _share_needs_detail(data.get("rv_share_arse_links")):
                calc["land_share_desc"] = osh.describe_chain(data["rv_share_arse_links"])
            bld_links = (data.get("rv_share_aayan_links") if data.get("rv_share_same") is False
                         else data.get("rv_share_arse_links"))
            if has_building and _share_needs_detail(bld_links):
                calc["building_share_desc"] = osh.describe_chain(bld_links)
            # به کاربر نام شهرستانِ واقعی نقطه (از نقشه) نمایش داده می‌شود؛ اگر نرخ از
            # شهرستان همسایه گرفته شده باشد، فقط در لاگ ثبت می‌شود.
            county_label = county_info["county"]
            if county_info["method"] != "name" and county_info.get("hint"):
                county_label = county_info["hint"].replace("شهرستان ", "").strip()

            await message.answer(
                f"📊 *نتیجهٔ محاسبهٔ ارزش منطقه‌ای*\n\n"
                f"📍 {province} — {county_label}\n\n"
                f"{_result_values_text(calc, bold=True)}",
            )

            # ── ساخت PDF دو صفحه‌ای (صفحهٔ ۱ خلاصه، صفحهٔ ۲ نحوهٔ محاسبه) با تلاش مجدد ──
            pdf_path = os.path.join(tempfile.gettempdir(), f"regional_value_{user_id}_{int(time.time())}.pdf")
            pdf_ok = False
            for attempt in (1, 2):
                try:
                    pdf_ok = await asyncio.wait_for(
                        loop.run_in_executor(
                            None,
                            lambda: _build_pdf_sync(pdf_path, province, county_label, address, tax_result, calc,
                                            plak=data.get("rv_plak_text") or None),
                        ),
                        timeout=_STEP_TIMEOUT_SECONDS,
                    )
                    if pdf_ok:
                        break
                except Exception as e:
                    logger.error(f"[RV] خطا در تلاش {attempt} ساخت PDF برای کاربر {user_id}: {e}", exc_info=True)
                if attempt == 1:
                    await asyncio.sleep(1)

            if not pdf_ok or not os.path.exists(pdf_path):
                # فال‌بک متنی — نحوهٔ محاسبه به‌صورت متن، تا کاربر دست‌خالی نماند
                steps = ayani_calc.explain_steps(calc)
                steps_text = "\n".join(
                    f"• {t}: {f'{a:,} ریال' if a is not None else ''}" + (f"\n   {f}" if f else "")
                    for t, f, a in steps
                )
                await message.answer(f"🧮 *نحوهٔ محاسبه*\n\n{steps_text}")
                await _system_failure(
                    message, state, bot, user_id, refund_credit,
                    f"🛑 ساخت PDF برای کاربر {user_id} شکست خورد.\n{_inputs_summary(data)}",
                    "⚠️ خطا در ساخت فایل گزارش PDF (نتیجه به‌صورت متنی ارسال شد). "
                    "برای دریافت فایل، لطفاً *۳۰ دقیقه دیگر* دوباره تلاش کنید.",
                )
                return

            # ── ارسال مستقیم PDF (نه FSInputFile) ──
            sent = await send_document_direct(
                chat_id=user_id,
                file_path=pdf_path,
                filename=f"ارزش_منطقه_ای_{province}.pdf",
                caption=(
                    f"📄 گزارش ارزش منطقه‌ای ملک ({'عرصه و اعیانی' if has_building else 'عرصه'})\n\n"
                    f"{_result_values_text(calc, bold=False)}"
                ),
            )
            try:
                os.remove(pdf_path)
            except Exception:
                pass

            if sent:
                await message.answer("بازگشت به منوی اصلی.", reply_markup=get_main_menu_kb(user_id))
                return True
            else:
                await _system_failure(
                    message, state, bot, user_id, refund_credit,
                    f"🛑 ارسال فایل PDF برای کاربر {user_id} شکست خورد (بعد از تلاش مجدد داخلی).",
                    "⚠️ گزارش ساخته شد ولی ارسال فایل ناموفق بود. لطفاً *۳۰ دقیقه دیگر* دوباره تلاش کنید.",
                )

        except Exception as e:
            # سپر نهایی: هیچ خطای پیش‌بینی‌نشده‌ای نباید کل ربات را کرش کند
            logger.error(f"[RV] خطای غیرمنتظره در پردازش کاربر {user_id}: {e}", exc_info=True)
            try:
                await _system_failure(
                    message, state, bot, user_id, refund_credit,
                    f"🛑 خطای غیرمنتظره برای کاربر {user_id}: {e}",
                    "⚠️ خطایی در پردازش استعلام رخ داد. لطفاً *۳۰ دقیقه دیگر* دوباره تلاش کنید.",
                )
            except Exception:
                pass
        finally:
            await state.clear()


def _do_query_sync(province: str, address: str, lat, lng):
    """نسخهٔ synchronous برای اجرا در executor (بدون event loop تودرتو)."""
    if lat is not None and lng is not None:
        from geocode_and_query import query_by_coordinates
        return query_by_coordinates(lat, lng, province_hint=province)
    from geocode_and_query import full_pipeline
    return full_pipeline(address=address, province_hint=province)


def _build_pdf_sync(pdf_path, province, county, address, tax_result, calc, plak=None):
    from ayani_pdf import build_ayani_pdf
    return build_ayani_pdf(
        pdf_path, province=province, county=county, address=address,
        tax_result=tax_result, result=calc, plak=plak,
    )
