# -*- coding: utf-8 -*-
"""
کیبوردها و توابع کمکی ناوبری (بازگشت / شروع مجدد) — مشترک بین main.py و هندلرها.
"""
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

MAIN_MENU_BUTTON = "🗺️ استعلام ارزش منطقه‌ای ملک"
HISTORY_BUTTON = "📂 سوابق استعلام"
VERIFY_BUTTON = "🔎 بررسی اصالت گزارش"
ACCOUNT_BUTTON = "👤 حساب کاربری و اشتراک"
UTIL_BUTTON = "🧰 خدمات کاربردی"
SUPPORT_BUTTON = "💬 پشتیبانی"
DISCOUNT_BUTTON = "🎟 کد تخفیف دارم"
BACK_BUTTON = "🔙 بازگشت"
RESTART_BUTTON = "🏠 شروع مجدد"

# پس از نتیجهٔ استعلام: افزودن ملک دیگر به گزارش تجمیعی / دریافت گزارش تجمیعی
BATCH_ADD_BUTTON = "➕ افزودن ملک دیگر (گزارش تجمیعی)"
BATCH_PDF_BUTTON = "📑 دریافت گزارش تجمیعی"


def _norm(text) -> str:
    """حذف علامت‌های نامرئی (variation selector) و فاصله‌های اضافه تا مقایسهٔ متن دکمه‌ها مطمئن باشد."""
    return (text or "").replace("️", "").replace("‏", "").replace("‎", "").strip()


def is_back(text) -> bool:
    t = _norm(text)
    return "بازگشت" in t and len(t) <= 15


def is_restart(text) -> bool:
    t = _norm(text)
    return "شروع مجدد" in t and len(t) <= 15


def is_main_menu_button(text) -> bool:
    t = _norm(text)
    return "استعلام ارزش" in t and len(t) <= 40


def is_button(text, button: str) -> bool:
    """مقایسهٔ متن پیام با یک دکمه (بی‌توجه به ایموجی و علامت‌های نامرئی)."""
    return _core(text) == _core(button)


def _core(text) -> str:
    """متن دکمه بدون ایموجی/علامت‌های ابتدای آن."""
    t = _norm(text)
    i = 0
    while i < len(t) and not t[i].isalpha():
        i += 1
    return t[i:]


def is_batch_pdf_button(text) -> bool:
    return _norm(text).startswith(_norm(BATCH_PDF_BUTTON))


def discount_percent(data: dict, product: str) -> int:
    """درصد کد تخفیفِ اعمال‌شده در وضعیت فرم برای این محصول (یا ۰)."""
    d = (data or {}).get("discount") or {}
    return int(d.get("percent") or 0) if d.get("product") in (product, "all") else 0


def nav_row():
    """ردیف ثابت پایین همهٔ مرحله‌ها: بازگشت به مرحلهٔ قبل + شروع مجدد (منوی اصلی)."""
    return [KeyboardButton(text=BACK_BUTTON), KeyboardButton(text=RESTART_BUTTON)]


def nav_only_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[nav_row()], resize_keyboard=True)


def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=MAIN_MENU_BUTTON)],
            [KeyboardButton(text=UTIL_BUTTON)],
            [KeyboardButton(text=HISTORY_BUTTON), KeyboardButton(text=VERIFY_BUTTON)],
            [KeyboardButton(text=ACCOUNT_BUTTON), KeyboardButton(text=SUPPORT_BUTTON)],
        ],
        resize_keyboard=True,
    )


def after_result_kb(batch_count: int = 0) -> ReplyKeyboardMarkup:
    """
    کیبورد پس از نتیجهٔ استعلام: منوی اصلی + «افزودن ملک دیگر»؛ اگر گزارش تجمیعی
    دو ملک یا بیشتر دارد، دکمهٔ دریافت آن هم نمایش داده می‌شود.
    """
    rows = [[KeyboardButton(text=BATCH_ADD_BUTTON)]]
    if batch_count >= 2:
        rows.append([KeyboardButton(text=f"{BATCH_PDF_BUTTON} ({_fa(batch_count)} ملک)")])
    rows += main_menu_kb().keyboard
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def _fa(n) -> str:
    return str(n).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
