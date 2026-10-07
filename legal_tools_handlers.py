# -*- coding: utf-8 -*-
"""
legal_tools_handlers.py — دو بخش «خدمات کاربردی»: ⚖️ محاسبه دیه و 👪 محاسبه سهم‌الارث.

هندلرها روی utilities_router ثبت می‌شوند و همان قواعد دسترسی را دارند (هر بخش یک بار رایگان،
سپس اشتراک خدمات کاربردی). یک «استفاده» = یک نتیجهٔ کامل.

دیه: سال → روش (اعضا و جراحات / قتل / درصدی / کسری) → جزئیات → نتیجه + PDF
ارث: وضعیت تأهل متوفی → صفحه‌های شمارنده (دکمه‌های ➖/➕) برای طبقهٔ اول، نوه‌ها،
     طبقهٔ دوم و سوم → ارزش ترکه (اختیاری) → نتیجه + PDF
"""
from __future__ import annotations

import asyncio
import logging
import os

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import access_control
import diyeh_calc as dyc
import inheritance_calc as ic
from utilities_handlers import (BACK_TEXT, ENTRY, MAIN_MENU_TEXT, UtilForm, _back_to_menu, _fa, _gate,
                                _is_main, _kb, _status_line, _tmp, _to_main, _used, back_kb, utilities_router)
from keyboards import is_back

logger = logging.getLogger(__name__)
router = utilities_router


async def _send_pdf(message: Message, build, filename: str, caption: str) -> bool:
    from bale_file_sender import send_document_direct
    path = _tmp(filename)
    try:
        ok = await asyncio.get_running_loop().run_in_executor(None, build, path)
        return bool(ok and await send_document_direct(message.chat.id, path, filename=filename, caption=caption))
    except Exception as e:
        logger.error(f"[LEGAL] خطا در ساخت/ارسال PDF: {e}", exc_info=True)
        return False
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _numbered(options) -> str:
    return "\n".join(f"{_fa(i)}. {o}" for i, o in enumerate(options, 1))


def _num_kb(count: int, per_row: int = 6):
    nums = [_fa(i) for i in range(1, count + 1)]
    return _kb([nums[i:i + per_row] for i in range(0, len(nums), per_row)] + [[BACK_TEXT, MAIN_MENU_TEXT]])


def _choice(text: str, count: int):
    t = (text or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")).strip()
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


def _to_int(text: str):
    t = (text or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    t = t.replace(",", "").replace("٬", "").replace(" ", "").strip()
    return int(t) if t.isdigit() else None


# ══════════════════════════════════════════════════════════════════
# ⚖️ محاسبه دیه
# ══════════════════════════════════════════════════════════════════
DY_METHODS = [dyc.METHOD_ORGAN, dyc.METHOD_HOMICIDE, dyc.METHOD_PERCENT, dyc.METHOD_FRACTION]
DY_METHOD_BUTTONS = {
    dyc.METHOD_ORGAN: "🦴 اعضا، منافع، جراحات و جنین",
    dyc.METHOD_HOMICIDE: "⚰️ دیهٔ قتل (نفس)",
    dyc.METHOD_PERCENT: "٪ درصدی از دیهٔ کامل (ارش)",
    dyc.METHOD_FRACTION: "➗ کسری از دیهٔ کامل",
}
SEX_BUTTONS = {"👨 مرد": dyc.SEX_MALE, "👩 زن": dyc.SEX_FEMALE, "⚧ خنثای مشکل": dyc.SEX_INTERSEX}
YES, NO = "✅ بله", "❌ خیر"


def _year_kb():
    years = sorted(dyc.full_diyeh_table(), reverse=True)[:4]
    return _kb([[_fa(y) for y in years], [BACK_TEXT, MAIN_MENU_TEXT]])


async def diyeh_entry(message: Message, state: FSMContext, prefix: str = ""):
    status = await _status_line(message.chat.id, "diyeh")
    latest = dyc.latest_year()
    await message.answer(
        prefix + f"⚖️ *محاسبه دیه*\n\n{status}"
        f"دیهٔ کامل سال {_fa(latest)}: {_fa(f'{dyc.full_diyeh(latest):,}')} ریال\n\n"
        "📅 سال پرداخت دیه را انتخاب کنید (ملاک، مبلغ دیهٔ سال پرداخت است) یا سال دیگری را تایپ کنید:",
        reply_markup=_year_kb())
    await state.set_state(UtilForm.dy_year)


ENTRY["diyeh"] = diyeh_entry


@router.message(UtilForm.dy_year)
async def dy_year(message: Message, state: FSMContext):
    text = message.text or ""
    if _is_main(text):
        await _to_main(message, state)
        return
    if is_back(text):
        await _back_to_menu(message, state)
        return
    y = _to_int(text)
    if not y or not dyc.full_diyeh(y):
        years = list(dyc.full_diyeh_table())
        await message.answer(f"⚠️ مبلغ دیهٔ کامل این سال ثبت نشده است. سال‌های موجود: {_fa(years[0])} تا "
                             f"{_fa(years[-1])}", reply_markup=_year_kb())
        return
    await state.update_data(dy={"year": y})
    await _ask_method(message, state)


async def _ask_method(message: Message, state: FSMContext):
    await message.answer("نوع محاسبه را انتخاب کنید:",
                         reply_markup=_kb([[DY_METHOD_BUTTONS[m]] for m in DY_METHODS] + [[BACK_TEXT, MAIN_MENU_TEXT]]))
    await state.set_state(UtilForm.dy_method)


@router.message(UtilForm.dy_method)
async def dy_method(message: Message, state: FSMContext):
    text = message.text or ""
    if _is_main(text):
        await _to_main(message, state)
        return
    if is_back(text):
        await diyeh_entry(message, state)
        return
    method = next((m for m, b in DY_METHOD_BUTTONS.items() if text == b), None)
    if not method:
        await _ask_method(message, state)
        return
    if not await _gate(message, state, "diyeh"):
        return
    dy = (await state.get_data()).get("dy") or {"year": dyc.latest_year()}
    dy["method"] = method
    await state.update_data(dy=dy)
    if method == dyc.METHOD_ORGAN:
        await _ask_category(message, state)
    elif method == dyc.METHOD_HOMICIDE:
        await message.answer("جنسیت مقتول را انتخاب کنید:",
                             reply_markup=_kb([list(SEX_BUTTONS)] + [[BACK_TEXT, MAIN_MENU_TEXT]]))
        await state.set_state(UtilForm.dy_sex)
    elif method == dyc.METHOD_PERCENT:
        await message.answer("درصد دیه را وارد کنید (مثلاً درصد ارش تعیین‌شده توسط پزشکی قانونی؛ نمونه: 2.5):",
                             reply_markup=back_kb)
        await state.set_state(UtilForm.dy_percent)
    else:
        await message.answer("کسرها را با فاصله وارد کنید؛ حاصل‌ضرب آن‌ها از دیهٔ کامل محاسبه می‌شود.\n"
                             "نمونه: «1/2 1/3» یعنی نصفِ یک‌سومِ دیهٔ کامل.", reply_markup=back_kb)
        await state.set_state(UtilForm.dy_fraction)


async def _ask_category(message: Message, state: FSMContext):
    cats = dyc.categories()
    await message.answer("📂 دسته را انتخاب کنید:\n\n" + _numbered(cats), reply_markup=_num_kb(len(cats)))
    await state.set_state(UtilForm.dy_cat)


@router.message(UtilForm.dy_cat)
async def dy_cat(message: Message, state: FSMContext):
    text = message.text or ""
    if _is_main(text):
        await _to_main(message, state)
        return
    if is_back(text):
        await _ask_method(message, state)
        return
    idx = _choice(text, len(dyc.categories()))
    if idx is None:
        await message.answer("⚠️ شمارهٔ یکی از دسته‌ها را انتخاب کنید.")
        return
    data = await state.get_data()
    dy = data["dy"]
    dy["cat"] = idx
    await state.update_data(dy=dy)
    await _ask_item(message, state, idx)


async def _ask_item(message: Message, state: FSMContext, cat: int):
    items = dyc.items(cat)
    lines = []
    for i, it in enumerate(items, 1):
        f = dyc.item_fraction(it)
        hint = (f" — {dyc.fraction_text(f)}" + (" برای هر مورد" if it.get("per_item") else "")) if f else ""
        lines.append(f"{_fa(i)}. {it['title']}{hint}")
    await message.answer(f"📋 *{dyc.categories()[cat]}* — نوع جنایت را انتخاب کنید:\n\n" + "\n".join(lines),
                         reply_markup=_num_kb(len(items)))
    await state.set_state(UtilForm.dy_item)


@router.message(UtilForm.dy_item)
async def dy_item(message: Message, state: FSMContext):
    text = message.text or ""
    if _is_main(text):
        await _to_main(message, state)
        return
    dy = (await state.get_data())["dy"]
    if is_back(text):
        await _ask_category(message, state)
        return
    idx = _choice(text, len(dyc.items(dy["cat"])))
    if idx is None:
        await message.answer("⚠️ شمارهٔ یکی از موارد را انتخاب کنید.")
        return
    dy["item"] = idx
    dy["count"] = 1
    await state.update_data(dy=dy)
    item = dyc.get_item(dy["cat"], idx)
    if item.get("per_item"):
        await message.answer(f"🔢 تعداد را وارد کنید (۱ تا {_fa(item.get('max_count') or 32)}):", reply_markup=back_kb)
        await state.set_state(UtilForm.dy_count)
        return
    await _after_count(message, state, dy)


@router.message(UtilForm.dy_count)
async def dy_count(message: Message, state: FSMContext):
    text = message.text or ""
    if _is_main(text):
        await _to_main(message, state)
        return
    dy = (await state.get_data())["dy"]
    if is_back(text):
        await _ask_item(message, state, dy["cat"])
        return
    n = _to_int(text)
    item = dyc.get_item(dy["cat"], dy["item"])
    mx = item.get("max_count") or 32
    if not n or not 1 <= n <= mx:
        await message.answer(f"⚠️ تعداد باید عددی بین ۱ تا {_fa(mx)} باشد.")
        return
    dy["count"] = n
    await state.update_data(dy=dy)
    await _after_count(message, state, dy)


async def _after_count(message: Message, state: FSMContext, dy: dict):
    item = dyc.get_item(dy["cat"], dy["item"])
    if dyc.needs_sex(item, dy.get("count", 1)):
        await message.answer("جنسیت مصدوم را انتخاب کنید (دیهٔ این مورد به ثلث دیهٔ کامل یا بیشتر می‌رسد؛ مادهٔ ۵۶۰):",
                             reply_markup=_kb([["👨 مرد", "👩 زن"], [BACK_TEXT, MAIN_MENU_TEXT]]))
        await state.set_state(UtilForm.dy_sex)
        return
    await _finish_diyeh(message, state, dy)


@router.message(UtilForm.dy_sex)
async def dy_sex(message: Message, state: FSMContext):
    text = message.text or ""
    if _is_main(text):
        await _to_main(message, state)
        return
    dy = (await state.get_data())["dy"]
    if is_back(text):
        if dy["method"] == dyc.METHOD_HOMICIDE:
            await _ask_method(message, state)
        else:
            await _ask_item(message, state, dy["cat"])
        return
    sex = SEX_BUTTONS.get(text)
    if not sex or (dy["method"] == dyc.METHOD_ORGAN and sex == dyc.SEX_INTERSEX):
        await message.answer("⚠️ یکی از گزینه‌ها را انتخاب کنید.")
        return
    dy["sex"] = sex
    await state.update_data(dy=dy)
    if dy["method"] == dyc.METHOD_HOMICIDE:
        await message.answer("آیا رفتار مرتکب و فوت هر دو در ماه‌های حرام (محرم، رجب، ذی‌القعده، ذی‌الحجه) "
                             "یا در حرم مکه واقع شده است؟ (مادهٔ ۵۵۵)",
                             reply_markup=_kb([[YES, NO], [BACK_TEXT, MAIN_MENU_TEXT]]))
        await state.set_state(UtilForm.dy_holy)
        return
    await _finish_diyeh(message, state, dy)


@router.message(UtilForm.dy_holy)
async def dy_holy(message: Message, state: FSMContext):
    text = message.text or ""
    if _is_main(text):
        await _to_main(message, state)
        return
    dy = (await state.get_data())["dy"]
    if is_back(text):
        await message.answer("جنسیت مقتول را انتخاب کنید:",
                             reply_markup=_kb([list(SEX_BUTTONS)] + [[BACK_TEXT, MAIN_MENU_TEXT]]))
        await state.set_state(UtilForm.dy_sex)
        return
    if text not in (YES, NO):
        await message.answer("⚠️ «بله» یا «خیر» را انتخاب کنید.")
        return
    dy["holy"] = text == YES
    await _finish_diyeh(message, state, dy)


@router.message(UtilForm.dy_percent)
@router.message(UtilForm.dy_fraction)
async def dy_value(message: Message, state: FSMContext):
    text = message.text or ""
    if _is_main(text):
        await _to_main(message, state)
        return
    if is_back(text):
        await _ask_method(message, state)
        return
    dy = (await state.get_data())["dy"]
    if dy["method"] == dyc.METHOD_PERCENT:
        dy["percent"] = text.strip()
    else:
        dy["fractions"] = [x for x in text.replace("از", " ").replace("،", " ").replace(",", " ").split() if x]
    await _finish_diyeh(message, state, dy, retry_on_error=True)


async def _finish_diyeh(message: Message, state: FSMContext, dy: dict, retry_on_error: bool = False):
    if not await _gate(message, state, "diyeh"):
        return
    try:
        r = dyc.calculate(year=dy["year"], method=dy["method"], percent=dy.get("percent"),
                          fractions=dy.get("fractions"), sex=dy.get("sex", dyc.SEX_MALE),
                          holy_month=bool(dy.get("holy")), category_index=dy.get("cat"),
                          item_index=dy.get("item"), count=int(dy.get("count") or 1))
    except dyc.DiyehError as e:
        await message.answer(f"⚠️ {e}")
        if not retry_on_error:
            await _ask_method(message, state)
        return
    _used(message.chat.id, "diyeh", dy["method"])
    await message.answer(dyc.format_result_fa(r))
    if r["amount"] is not None:
        import legal_pdf
        issuer = access_control.issuer_line(message.chat.id)
        await _send_pdf(message, lambda p: legal_pdf.build_diyeh_pdf(p, r, issuer=issuer), "محاسبه_دیه.pdf",
                        f"📄 گزارش محاسبهٔ دیه — {r['amount']:,} ریال")
    await state.update_data(dy={"year": dy["year"]})
    await _ask_method(message, state)


# ══════════════════════════════════════════════════════════════════
# 👪 محاسبه سهم‌الارث
# ══════════════════════════════════════════════════════════════════
MARITAL_BUTTONS = {
    "🚫 متوفی همسر نداشت": ic.SINGLE,
    "👩 متوفی زن بود و شوهر داشت": ic.HAS_HUSBAND,
    "👨 متوفی مرد بود و همسر داشت": ic.HAS_WIFE,
}
MAX_COUNT = 20
SKIP_ESTATE = "⏭ بدون مبلغ ترکه"

# صفحه‌ها: (عنوان، [(کلید، نوع)])  — نوع: "tog" (زنده/نیست) یا "cnt" (تعداد)
SCREENS = {
    "c1": ("طبقهٔ اول — پدر و مادر و فرزندان", [("pedar", "tog"), ("madar", "tog"), ("pesar", "cnt"),
                                               ("dokhtar", "cnt")]),
    "c2": ("طبقهٔ دوم — اجداد و برادر و خواهر", [("jaddAbi", "tog"), ("jaddehAbi", "tog"), ("jaddOmmi", "tog"),
                                                ("jaddehOmmi", "tog"), ("baradarAbaveyni", "cnt"),
                                                ("khaharAbaveyni", "cnt"), ("baradarAbi", "cnt"), ("khaharAbi", "cnt"),
                                                ("siblingOmmi", "cnt")]),
    "c3": ("طبقهٔ سوم — عمو، عمه، دایی و خاله", [(k, "cnt") for k in ic.CLASS3_KEYS]),
}
HAJB_ROWS = [("baradarAbaveyni", "برادر تنی یا پدری"), ("khaharAbaveyni", "خواهر تنی یا پدری")]


async def inheritance_entry(message: Message, state: FSMContext, prefix: str = ""):
    status = await _status_line(message.chat.id, "inheritance")
    await message.answer(prefix + f"👪 *محاسبه سهم‌الارث*\n\n{status}وضعیت تأهل متوفی در زمان فوت:",
                         reply_markup=_kb([[b] for b in MARITAL_BUTTONS] + [[BACK_TEXT, MAIN_MENU_TEXT]]))
    await state.set_state(UtilForm.inh_marital)


ENTRY["inheritance"] = inheritance_entry


@router.message(UtilForm.inh_marital)
async def inh_marital(message: Message, state: FSMContext):
    text = message.text or ""
    if _is_main(text):
        await _to_main(message, state)
        return
    if is_back(text):
        await _back_to_menu(message, state)
        return
    marital = MARITAL_BUTTONS.get(text)
    if not marital:
        await message.answer("⚠️ یکی از گزینه‌ها را انتخاب کنید.")
        return
    if not await _gate(message, state, "inheritance"):
        return
    await state.update_data(inh={"marital": marital, "wives": 1, "counts": {}, "branches": [], "screen": "c1"})
    if marital == ic.HAS_WIFE:
        await message.answer("متوفی چند همسر دائم داشت؟",
                             reply_markup=_kb([["۱", "۲", "۳", "۴"], [BACK_TEXT, MAIN_MENU_TEXT]]))
        await state.set_state(UtilForm.inh_wives)
        return
    await _show_board(message, state, new=True)


@router.message(UtilForm.inh_wives)
async def inh_wives(message: Message, state: FSMContext):
    text = message.text or ""
    if _is_main(text):
        await _to_main(message, state)
        return
    if is_back(text):
        await inheritance_entry(message, state)
        return
    n = _to_int(text)
    if not n or not 1 <= n <= 4:
        await message.answer("⚠️ عددی بین ۱ تا ۴ انتخاب کنید.")
        return
    inh = (await state.get_data())["inh"]
    inh["wives"] = n
    await state.update_data(inh=inh)
    await _show_board(message, state, new=True)


def _heirs(inh: dict) -> ic.Heirs:
    return ic.Heirs(marital=inh["marital"], wives=inh.get("wives", 1), counts=dict(inh["counts"]),
                    branches=[ic.Branch(bool(b[0]), int(b[1]), int(b[2])) for b in inh["branches"]])


def _cnt_row(key: str, label: str, n: int, kind: str) -> list:
    if kind == "tog":
        return [InlineKeyboardButton(text=("✅ " if n else "⬜️ ") + label + (" — زنده" if n else ""),
                                     callback_data=f"inh:tog:{key}")]
    return [InlineKeyboardButton(text="➖", callback_data=f"inh:dec:{key}"),
            InlineKeyboardButton(text=f"{label}: {_fa(n)}", callback_data="inh:noop"),
            InlineKeyboardButton(text="➕", callback_data=f"inh:inc:{key}")]


def _board(inh: dict):
    """متن و کیبورد صفحهٔ فعلی."""
    scr = inh["screen"]
    c = inh["counts"]
    rows = []
    if scr == "br":
        text = ("👶 *نوه‌ها (قائم‌مقامی — مادهٔ ۹۱۱)*\n\nبرای هر فرزندِ فوت‌شدهٔ متوفی که فرزند دارد یک «شاخه» "
                "بسازید؛ نوه‌ها سهم پدر یا مادر خود را می‌برند.")
        for i, (is_son, gs, gd) in enumerate(inh["branches"]):
            rows.append([InlineKeyboardButton(text=f"شاخهٔ {_fa(i + 1)}: فرزند فوت‌شده «{'پسر' if is_son else 'دختر'}» 🔁",
                                              callback_data=f"inh:bsex:{i}"),
                         InlineKeyboardButton(text="🗑", callback_data=f"inh:bdel:{i}")])
            rows.append(_cnt_row(f"b{i}gs", "نوهٔ پسر", gs, "cnt"))
            rows.append(_cnt_row(f"b{i}gd", "نوهٔ دختر", gd, "cnt"))
        if len(inh["branches"]) < 10:
            rows.append([InlineKeyboardButton(text="➕ افزودن شاخه", callback_data="inh:badd")])
        rows.append([InlineKeyboardButton(text="🔙 بازگشت به طبقهٔ اول", callback_data="inh:scr:c1")])
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    title, fields = SCREENS[scr]
    text = f"👪 *{title}*\n\nوارثانِ زنده در زمان فوت را مشخص کنید و سپس «ادامه» را بزنید."
    if scr == "c1":
        text += "\n(اگر طبقهٔ اول هیچ وارثی ندارد، «ادامه» شما را به طبقهٔ دوم می‌برد.)"
    elif scr == "c2":
        text += "\nتنی = ابوینی (هم‌پدر و هم‌مادر)، پدری = ابی، مادری = امی."
    for key, kind in fields:
        rows.append(_cnt_row(key, ic.LABELS[key], int(c.get(key) or 0), kind))
    if scr == "c1":
        if not (c.get("pesar") or c.get("dokhtar")):
            nb = sum(1 for b in inh["branches"] if b[1] + b[2] > 0)
            rows.append([InlineKeyboardButton(text=f"👶 نوه از فرزند فوت‌شده ({_fa(nb)} شاخه)",
                                              callback_data="inh:scr:br")])
        if c.get("pedar") and c.get("madar"):
            rows.append([InlineKeyboardButton(text="👇 برای حجب مادر (مادهٔ ۸۹۲):", callback_data="inh:noop")])
            for key, label in HAJB_ROWS:
                rows.append(_cnt_row(key, label, int(c.get(key) or 0), "cnt"))
    nav = []
    if scr != "c1":
        nav.append(InlineKeyboardButton(text="🔙 طبقهٔ قبل", callback_data="inh:prev"))
    nav.append(InlineKeyboardButton(text="✅ ادامه", callback_data="inh:next"))
    rows.append(nav)
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_board(message: Message, state: FSMContext, new: bool = False):
    inh = (await state.get_data())["inh"]
    text, kb = _board(inh)
    if new:
        await message.answer("👇 از دکمه‌های زیر استفاده کنید.", reply_markup=_kb([[BACK_TEXT, MAIN_MENU_TEXT]]))
        await message.answer(text, reply_markup=kb)
    else:
        try:
            await message.edit_text(text, reply_markup=kb)
        except Exception:
            await message.answer(text, reply_markup=kb)
    await state.set_state(UtilForm.inh_board)


def _class_has_heirs(inh: dict, scr: str) -> bool:
    c = inh["counts"]
    if scr == "c1":
        return any(c.get(k) for k in ("pedar", "madar", "pesar", "dokhtar")) or \
            any(b[1] + b[2] for b in inh["branches"])
    keys = ic.CLASS2_KEYS if scr == "c2" else ic.CLASS3_KEYS
    return any(c.get(k) for k in keys)


@router.callback_query(UtilForm.inh_board, F.data.startswith("inh:"))
async def inh_callback(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    inh = data.get("inh")
    if not inh:
        await callback.answer("این فرم منقضی شده است؛ دوباره شروع کنید.")
        return
    parts = callback.data.split(":")
    action = parts[1]
    c = inh["counts"]
    if action == "noop":
        await callback.answer()
        return
    if action in ("inc", "dec", "tog"):
        key = parts[2]
        if key.startswith("b") and key[1:-2].isdigit():           # شمارندهٔ نوه‌ها: b{i}gs / b{i}gd
            i, which = int(key[1:-2]), (1 if key.endswith("gs") else 2)
            if i < len(inh["branches"]):
                v = inh["branches"][i][which] + (1 if action == "inc" else -1)
                inh["branches"][i][which] = max(0, min(MAX_COUNT, v))
        elif key in ic.LABELS:
            if action == "tog":
                c[key] = 0 if c.get(key) else 1
            else:
                c[key] = max(0, min(MAX_COUNT, int(c.get(key) or 0) + (1 if action == "inc" else -1)))
    elif action == "scr":
        inh["screen"] = parts[2]
    elif action == "badd":
        inh["branches"].append([True, 0, 0])
    elif action == "bdel":
        i = int(parts[2])
        if i < len(inh["branches"]):
            inh["branches"].pop(i)
    elif action == "bsex":
        i = int(parts[2])
        if i < len(inh["branches"]):
            inh["branches"][i][0] = not inh["branches"][i][0]
    elif action == "prev":
        inh["screen"] = {"c2": "c1", "c3": "c2"}.get(inh["screen"], "c1")
    elif action == "next":
        scr = inh["screen"] if inh["screen"] in SCREENS else "c1"
        if _class_has_heirs(inh, scr) or scr == "c3":
            await state.update_data(inh=inh)
            await callback.answer()
            await _ask_estate(callback.message, state)
            return
        inh["screen"] = "c2" if scr == "c1" else "c3"
        await callback.answer("این طبقه وارثی ندارد؛ طبقهٔ بعد")
        await state.update_data(inh=inh)
        await _show_board(callback.message, state)
        return
    await state.update_data(inh=inh)
    await callback.answer()
    await _show_board(callback.message, state)


@router.message(UtilForm.inh_board)
async def inh_board_text(message: Message, state: FSMContext):
    text = message.text or ""
    if _is_main(text):
        await _to_main(message, state)
        return
    if is_back(text):
        await inheritance_entry(message, state)
        return
    await message.answer("👆 لطفاً از دکمه‌های پیام بالا (➖ / ➕ / ادامه) استفاده کنید.")


async def _ask_estate(message: Message, state: FSMContext):
    await message.answer("🏦 ارزش ترکه را به *ریال* وارد کنید تا سهم هر وارث به ریال هم محاسبه شود، "
                         f"یا «{SKIP_ESTATE}» را بزنید:",
                         reply_markup=_kb([[SKIP_ESTATE], [BACK_TEXT, MAIN_MENU_TEXT]]))
    await state.set_state(UtilForm.inh_estate)


@router.message(UtilForm.inh_estate)
async def inh_estate(message: Message, state: FSMContext):
    text = message.text or ""
    if _is_main(text):
        await _to_main(message, state)
        return
    if is_back(text):
        await _show_board(message, state, new=True)
        return
    estate = None
    if text != SKIP_ESTATE:
        estate = _to_int(text)
        if not estate:
            await message.answer(f"⚠️ مبلغ را فقط به‌صورت عدد (ریال) وارد کنید یا «{SKIP_ESTATE}» را بزنید.")
            return
    if not await _gate(message, state, "inheritance"):
        return
    inh = (await state.get_data())["inh"]
    try:
        r = ic.calculate(_heirs(inh), estate=estate)
    except ic.InheritanceError as e:
        await message.answer(f"⚠️ {e}")
        await _show_board(message, state, new=True)
        return
    _used(message.chat.id, "inheritance", inh["marital"])
    await message.answer(ic.format_result_fa(r))
    import legal_pdf
    issuer = access_control.issuer_line(message.chat.id)
    await _send_pdf(message, lambda p: legal_pdf.build_inheritance_pdf(p, r, issuer=issuer), "سهم_الارث.pdf",
                    "📄 گزارش تقسیم ترکه (سهم‌الارث)")
    await inheritance_entry(message, state, "برای محاسبهٔ دیگر:\n\n")

