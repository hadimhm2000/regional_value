# -*- coding: utf-8 -*-
"""
کیبوردها و توابع کمکی ناوبری (بازگشت / شروع مجدد) — مشترک بین main.py و هندلرها.
"""
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

MAIN_MENU_BUTTON = "🗺️ استعلام ارزش منطقه‌ای ملک"
BACK_BUTTON = "🔙 بازگشت"
RESTART_BUTTON = "🏠 شروع مجدد"


def _norm(text) -> str:
    """حذف علامت‌های نامرئی (variation selector) و فاصله‌های اضافه تا مقایسهٔ متن دکمه‌ها مطمئن باشد."""
    return (text or "").replace("\ufe0f", "").replace("\u200f", "").replace("\u200e", "").strip()


def is_back(text) -> bool:
    t = _norm(text)
    return "بازگشت" in t and len(t) <= 15


def is_restart(text) -> bool:
    t = _norm(text)
    return "شروع مجدد" in t and len(t) <= 15


def is_main_menu_button(text) -> bool:
    t = _norm(text)
    return "استعلام ارزش" in t and len(t) <= 40


def nav_row():
    """ردیف ثابت پایین همهٔ مرحله‌ها: بازگشت به مرحلهٔ قبل + شروع مجدد (منوی اصلی)."""
    return [KeyboardButton(text=BACK_BUTTON), KeyboardButton(text=RESTART_BUTTON)]


def nav_only_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[nav_row()], resize_keyboard=True)


def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=MAIN_MENU_BUTTON)]],
        resize_keyboard=True,
    )
