# -*- coding: utf-8 -*-
"""
تولید PDF گزارش استعلام ارزش منطقه‌ای با ReportLab (خالص پایتون، بدون وابستگی GTK).

هدر: عکس واقعی اسکرین‌شات سامانه مالیاتی
بدنه: تمام اطلاعات ساختاریافته از tax.gov.ir
پایان: استان، آدرس، متراژ + محاسبه ارزش کل برای هر ۳ کاربری
"""

import datetime
import logging
import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image,
    KeepTogether,
)
from reportlab.lib.styles import ParagraphStyle

logger = logging.getLogger(__name__)

# ═══ مسیر هدر ═══
DEFAULT_HEADER_IMAGE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "tax_header.jpg"
)

_PERSIAN_MONTHS = [
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"
]
_PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"

# ═══ فیلدهای بخش اطلاعات مکان (۹ فیلد اول) ═══
SYSTEM_FIELDS = [
    "اداره کل امور مالیاتی",
    "واحد مرتبط مالیاتی سنیم املاک",
    "واحد مرتبط مالیاتی سنیم حقوقی",
    "واحد مرتبط مالیاتی سنیم حقیقی",
    "واحد مرتبط مالیاتی حقوقی",
    "واحد مرتبط مالیاتی حقیقی",
    "واحد مرتبط مالیاتی ارث",
    "شماره بلوک بر اساس دفترچه ارزش معاملاتی ملک",
    "شماره ردیف بر اساس دفترچه ارزش معاملاتی ملک",
]

# ═══ فیلدهای ارزش معاملاتی (۳ فیلد) ═══
VALUE_FIELDS = [
    "ارزش معاملاتی مسکونی",
    "ارزش معاملاتی اداری",
    "ارزش معاملاتی تجاری",
]

# ═══ رنگ‌ها ═══
CLR_DARK_BLUE = colors.HexColor("#1a3a5c")
CLR_BLUE = colors.HexColor("#2c5282")
CLR_LIGHT_BLUE_BG = colors.HexColor("#f5f9fc")
CLR_BLUE_BORDER = colors.HexColor("#8faabe")
CLR_SEC_HEADER_BG = colors.HexColor("#d6e4f0")
CLR_SEC_BLUE_BG = colors.HexColor("#c5d9ed")
CLR_GREEN_BORDER = colors.HexColor("#8aaa8a")
CLR_GREEN_HEADER_BG = colors.HexColor("#d4edda")
CLR_GREEN_LABEL_BG = colors.HexColor("#f0f7f0")
CLR_GREEN_TEXT = colors.HexColor("#155724")
CLR_SELECTED_BG = colors.HexColor("#fff3cd")
CLR_FOOTER_TEXT = colors.HexColor("#aaaaaa")
CLR_TOTAL_BG = colors.HexColor("#1a3a5c")
CLR_DATE_BG = colors.HexColor("#f0f0f0")
CLR_DARK_BG = colors.HexColor("#1a1a2e")
CLR_LIGHT_BLUE_TEXT = colors.HexColor("#a8c8f0")

# ═══ ثبات فونت ═══
_font_registered = False
_FONT_NAME = "Vazirmatn"

_FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")


def _ensure_font():
    """ثبت فونت فارسی (یک‌بار).

    اولویت اول: Vazirmatn که همراه این پکیج در پوشهٔ fonts/ قرار داره —
    مستقل از سیستم‌عامل (ویندوز/لینوکس) و همیشه در دسترسه. اگر به هر
    دلیلی پیدا نشد، به فونت‌های سیستمی ویندوز/لینوکس فال‌بک می‌کنه.
    """
    global _font_registered, _FONT_NAME
    if _font_registered:
        return

    _font_candidates = [
        (os.path.join(_FONTS_DIR, "Vazirmatn-Regular.ttf"), "Vazirmatn"),
        (os.path.join(_FONTS_DIR, "Vazirmatn-Bold.ttf"), "Vazirmatn-Bold"),
        # فال‌بک ویندوز
        ("C:\\Windows\\Fonts\\tahoma.ttf", "Tahoma"),
        ("C:\\Windows\\Fonts\\tahomabd.ttf", "TahomaBold"),
        ("C:\\Windows\\Fonts\\BNAZANIN.TTF", "BNazanin"),
        # فال‌بک لینوکس (در صورت نصب بودن Vazirmatn یا Noto در سیستم)
        ("/usr/share/fonts/truetype/vazirmatn/Vazirmatn-Regular.ttf", "Vazirmatn"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVuSans"),
    ]

    registered_any = False
    for path, name in _font_candidates:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont(name, path))
                registered_any = True
                if name in ("Vazirmatn", "Tahoma"):
                    _FONT_NAME = name
                logger.info(f"[RV-PDF] فونت {name} از {path} ثبت شد")
            except Exception as e:
                logger.warning(f"[RV-PDF] خطا در ثبت فونت {path}: {e}")
            continue

    if not registered_any:
        logger.warning(
            "[RV-PDF] هیچ فونت فارسی‌ای ثبت نشد — متن فارسی ممکنه درست چاپ نشه. "
            "پوشهٔ fonts/ را کنار این فایل قرار دهید."
        )
        _FONT_NAME = "Helvetica"

    _font_registered = True


def _to_pd(n) -> str:
    """تبدیل عدد به رقم فارسی."""
    return str(n).translate(str.maketrans("0123456789", _PERSIAN_DIGITS))


def _gregorian_to_jalali(gy, gm, gd):
    """تبدیل تاریخ میلادی به شمسی.

    ⭐ اصلاحیه: نسخهٔ قبلی fallback (برای وقتی jdatetime نصب نبود) دو باگ
    داشت: (۱) کبیسهٔ میلادی را در شمارش روزها لحاظ نمی‌کرد و (۲) حلقهٔ
    تبدیل روز-به-ماه شمسی برای روزهای بعد از نیمهٔ سال (مثلاً دی/بهمن/اسفند)
    مقدار غلط برمی‌گرداند — دقیقاً همین باعث شده بود گزارش‌های ارزش
    منطقه‌ای تاریخ نادرست (مثلاً سال ۱۴۰۷ به‌جای ۱۴۰۵) نشان بدهند، چون
    روی VPS پکیج jdatetime نصب نبوده و همیشه از همین مسیر معیوب استفاده
    می‌شده. الگوریتم fallback پایین با چند تاریخ مرجع (از جمله مرزهای
    کبیسه مثل ۱۴۰۲/۱۲/۲۹ و ۱۴۰۳/۱۲/۳۰) تست و تأیید شده است.
    برای دقت و سرعت بیشتر، نصب jdatetime روی VPS همچنان توصیه می‌شود:
        pip install jdatetime --break-system-packages
    """
    try:
        import jdatetime
        j = jdatetime.date.fromgregorian(year=gy, month=gm, day=gd)
        return j.year, j.month, j.day
    except ImportError:
        logger.warning(
            "[RV-PDF] پکیج jdatetime نصب نیست؛ از الگوریتم fallback داخلی "
            "برای تبدیل تاریخ استفاده می‌شود. برای دقت کامل: "
            "pip install jdatetime --break-system-packages"
        )
        g_days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
        j_days_in_month = [31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29]

        gy2, gm2, gd2 = gy - 1600, gm - 1, gd - 1
        g_day_no = (365 * gy2 + (gy2 + 3) // 4
                    - (gy2 + 99) // 100 + (gy2 + 399) // 400)
        for i in range(gm2):
            g_day_no += g_days_in_month[i]
        if gm2 > 1 and ((gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0):
            g_day_no += 1  # کبیسهٔ میلادی — نسخهٔ قبلی این خط را نداشت
        g_day_no += gd2

        j_day_no = g_day_no - 79
        j_np = j_day_no // 12053
        j_day_no %= 12053

        jy = 979 + 33 * j_np + 4 * (j_day_no // 1461)
        j_day_no %= 1461
        if j_day_no >= 366:
            jy += (j_day_no - 1) // 365
            j_day_no = (j_day_no - 1) % 365

        for i in range(11):
            if j_day_no < j_days_in_month[i]:
                jm = i + 1
                jd = j_day_no + 1
                break
            j_day_no -= j_days_in_month[i]
        else:
            jm, jd = 12, j_day_no + 1

        return jy, jm, jd


def _now_tehran() -> datetime.datetime:
    """زمان فعلی به وقت تهران — مستقل از تنظیم time zone سرور/VPS.

    ⭐ اصلاحیه: نسخهٔ قبلی از datetime.datetime.now() (بدون tz) استفاده
    می‌کرد که همان ساعت سیستم‌عامل سرور را برمی‌گرداند؛ اگر ساعت VPS با
    وقت ایران (UTC+3:30) تنظیم نشده باشد، ساعتِ چاپ‌شده در گزارش با ساعت
    واقعی درخواست کاربر نمی‌خواند.
    """
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("Asia/Tehran"))
    except Exception:
        # فال‌بک: افست ثابت +۳:۳۰ (بدون در نظر گرفتن DST، که ایران دیگر ندارد)
        return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=3, minutes=30)


def _get_persian_date() -> str:
    now = _now_tehran()
    try:
        jy, jm, jd = _gregorian_to_jalali(now.year, now.month, now.day)
    except Exception:
        jy, jm, jd = now.year, now.month, now.day
    return f"{_to_pd(jd)} {_PERSIAN_MONTHS[jm - 1]} {_to_pd(jy)}"


def _get_persian_time() -> str:
    now = _now_tehran()
    # ⭐ اصلاحیه: f"{_to_pd(n):02s}" باگ داشت — چون _to_pd خروجی رشته‌ای
    # می‌دهد، فرمت "02s" پدینگ صفر را روی رشتهٔ تک‌کاراکتری با چپ‌چین
    # پیش‌فرض به سمت راست اضافه می‌کرد و "0" لاتین با رقم فارسی قاطی
    # می‌شد (مثلاً ساعت ۲ به‌جای "۰۲" به‌صورت "۲0" چاپ می‌شد). با صفرچین
    # کردن عدد پیش از تبدیل به رقم فارسی، این مشکل کامل رفع می‌شود.
    return f"{_to_pd(f'{now.hour:02d}')}:{_to_pd(f'{now.minute:02d}')}"


def _fmt(n: int) -> str:
    """فرمت عدد با جداکننده هزارگان فارسی."""
    return f"{n:,}".translate(str.maketrans("0123456789", _PERSIAN_DIGITS))


def _bidi(text: str) -> str:
    """اعمال الگوریتم BiDi و تغییر شکل حروف عربی/فارسی."""
    if not text:
        return text
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        reshaped = arabic_reshaper.reshape(text)
        return get_display(reshaped)
    except ImportError:
        logger.warning("[RV-PDF] arabic_reshaper یا python-bidi نصب نیست; متن بدون BiDi استفاده می‌شود")
        return text


def _get_style(font_size=10, bold=False, color=None, align="right") -> ParagraphStyle:
    """ساخت یا بازیابی ParagraphStyle برای متن فارسی RTL."""
    clr = color or colors.black
    return ParagraphStyle(
        name=f"rv_{font_size}_{bold}_{align}_{clr.hexval() if hasattr(clr, 'hexval') else clr}",
        fontName=_FONT_NAME,
        fontSize=font_size,
        leading=font_size * 1.5,
        alignment={'right': 2, 'center': 1, 'left': 0}.get(align, 2),
        textColor=clr,
        wordWrap='RTL',
    )


def _p(text: str, font_size=10, bold=False, color=None, align="right") -> Paragraph:
    """ساخت پاراگراف فارسی RTL."""
    bidi_text = _bidi(str(text))
    style = _get_style(font_size=font_size, bold=bold, color=color, align=align)
    return Paragraph(bidi_text, style=style)


def _get_field_value(tax_result: dict, field_name: str) -> str:
    """
    مقدار یک فیلد را از نتیجه ساختاریافته استخراج می‌کند.
    اول از فیلدهای_ساختاریافته می‌گرده، بعد از فیلدهای_خام.
    اگر پیدا نشد "-" برمی‌گرداند.
    """
    structured = tax_result.get("فیلدهای_ساختاریافته", {})
    val = structured.get(field_name)
    if val and str(val).strip():
        return str(val).strip()

    # فال‌بک: جستجو در فیلدهای خام
    def norm(t):
        return t.replace("ي", "ی").replace("ك", "ک").strip()

    raw = tax_result.get("همه_فیلدهای_خام_صفحه", {})
    norm_field = norm(field_name)
    for k, v in raw.items():
        norm_key = norm(k)
        if norm_field in norm_key or norm_key in norm_field:
            if v and str(v).strip():
                return str(v).strip()
    return "-"


def _make_section_header(title: str, bg_color, border_color, text_color):
    """ساخت هدر یک بخش (نوار رنگی با عنوان)."""
    t = Table(
        [[_p(title, font_size=10, bold=True, color=text_color)]],
        colWidths=[A4[0] - 20 * mm],
        rowHeights=[8 * mm],
    )
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg_color),
        ("BOX", (0, 0), (-1, -1), 0.5, border_color),
        ("TOPPADDING", (0, 0), (-1, -1), 2 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2 * mm),
        ("LEFTPADDING", (0, 0), (-1, -1), 4 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4 * mm),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


def _make_data_table(rows_data: list, label_bg, label_color, border_color):
    """
    rows_data: لیستی از تاپل‌ها [(label, value), ...]
    خروجی: Table با ستون برچسب و مقدار
    """
    col_w = (A4[0] - 20 * mm) / 2
    table_data = []
    for label, value in rows_data:
        table_data.append([
            _p(label, font_size=9.5, bold=True, color=label_color),
            _p(str(value) if value else "-", font_size=9.5, color=colors.black),
        ])

    t = Table(table_data, colWidths=[col_w, col_w])
    style_cmds = [
        ("GRID", (0, 0), (-1, -1), 0.5, border_color),
        ("TOPPADDING", (0, 0), (-1, -1), 2 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2 * mm),
        ("LEFTPADDING", (0, 0), (-1, -1), 3 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    # پس‌زمینه ستون برچسب
    for i in range(len(table_data)):
        style_cmds.append(("BACKGROUND", (0, i), (0, i), label_bg))

    t.setStyle(TableStyle(style_cmds))
    return t


def build_regional_value_pdf(
    tax_result: dict,
    province: str,
    address: str,
    area: float,
    land_use: str,
    total_value: int,
    output_path: str,
    header_image_path: str = None,
    all_land_use_values: dict = None,
) -> bool:
    """
    ساخت PDF گزارش ارزش منطقه‌ای با ReportLab.

    Args:
        tax_result: دیکشنری ساختاریافته خروجی build_full_result()
            {"سال": "1405", "فیلدهای_ساختاریافته": {...}, ...}
        province: نام استان
        address: آدرس کامل
        area: متراژ عرصه (متر مربع)
        land_use: کاربری زمین (مسکونی/تجاری/اداری)
        total_value: مبلغ کل (ریال)
        output_path: مسیر خروجی PDF
        header_image_path: مسیر عکس هدر (اختیاری)
        all_land_use_values: دیکشنری هر ۳ ارزش (مسکونی/تجاری/اداری → عدد ریال)

    Returns:
        True در صورت موفقیت
    """
    _ensure_font()

    date_str = _get_persian_date()
    time_str = _get_persian_time()
    tax_year = tax_result.get("سال", "1405")
    unit_value = int(total_value // area) if area > 0 else 0
    all_values = all_land_use_values or {}

    elements = []
    pw = A4[0]  # عرض صفحه
    usable_w = pw - 20 * mm  # عرض قابل استفاده

    # ═══ ۱. هدر (عکس یا متنی) ═══
    img_path = header_image_path or DEFAULT_HEADER_IMAGE
    if os.path.exists(img_path):
        try:
            img = Image(img_path, width=usable_w, height=30 * mm)
            img.hAlign = "CENTER"
            elements.append(img)
        except Exception as e:
            logger.warning(f"[RV-PDF] خطا در بارگذاری عکس هدر: {e}")
            elements.append(_make_text_header(usable_w))
    else:
        elements.append(_make_text_header(usable_w))

    # ═══ ۲. نوار عنوان ═══
    title_text = f"جستجوی بلوک و ردیف و اداره مالیاتی مرتبط — سال {tax_year}"
    title_tbl = Table(
        [[_p(title_text, font_size=11, bold=True, color=colors.white, align="center")]],
        colWidths=[usable_w],
        rowHeights=[9 * mm],
    )
    title_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CLR_BLUE),
        ("TOPPADDING", (0, 0), (-1, -1), 2 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2 * mm),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements.append(title_tbl)

    # ═══ ۳. نوار تاریخ ═══
    time_text = f"ساعت: {time_str}"
    date_tbl = Table(
        [[_p(date_str, font_size=8, color=colors.HexColor("#333333")),
          _p(time_text, font_size=8, color=colors.HexColor("#333333"), align="left")]],
        colWidths=[usable_w / 2, usable_w / 2],
        rowHeights=[6 * mm],
    )
    date_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CLR_DATE_BG),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5 * mm),
        ("LEFTPADDING", (0, 0), (-1, -1), 3 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements.append(date_tbl)

    elements.append(Spacer(1, 3 * mm))

    # ═══ ۴. بخش اول: اطلاعات مکان انتخابی ═══
    elements.append(_make_section_header(
        "اطلاعات مکان انتخابی", CLR_SEC_HEADER_BG, CLR_BLUE_BORDER, CLR_DARK_BLUE
    ))
    system_rows = [(f, _get_field_value(tax_result, f)) for f in SYSTEM_FIELDS]
    elements.append(_make_data_table(system_rows, CLR_LIGHT_BLUE_BG, CLR_DARK_BLUE, CLR_BLUE_BORDER))

    elements.append(Spacer(1, 3 * mm))

    # ═══ ۵. بخش دوم: ارزش معاملاتی ═══
    elements.append(_make_section_header(
        "ارزش معاملاتی", CLR_SEC_BLUE_BG, CLR_BLUE_BORDER, CLR_DARK_BLUE
    ))
    value_rows = [(f, _get_field_value(tax_result, f)) for f in VALUE_FIELDS]
    elements.append(_make_data_table(value_rows, CLR_LIGHT_BLUE_BG, CLR_DARK_BLUE, CLR_BLUE_BORDER))

    elements.append(Spacer(1, 3 * mm))

    # ═══ ۶. بخش سوم: مشخصات درخواست ═══
    elements.append(_make_section_header(
        "مشخصات درخواست", CLR_GREEN_HEADER_BG, CLR_GREEN_BORDER, CLR_GREEN_TEXT
    ))

    request_data = [
        ("استان", province),
        ("آدرس اعلام‌شده", address),
        ("متراژ عرصه", f"{_to_pd(int(area))} متر مربع"),
        ("کاربری انتخاب‌شده", f"{land_use} ★"),
    ]

    # ردیف‌های ارزش کل هر کاربری
    for lu_name, lu_val in all_values.items():
        if lu_val is not None:
            lu_total = int(lu_val * area)
            star = " ★" if lu_name == land_use else ""
            request_data.append((f"ارزش کل {lu_name}{star}", f"{_fmt(lu_total)} ریال"))

    request_table = _make_data_table(
        request_data, CLR_GREEN_LABEL_BG, CLR_GREEN_TEXT, CLR_GREEN_BORDER
    )

    # هایلایت ردیف کاربری انتخاب‌شده (ردیف شماره ۴ — ایندکس ۳)
    sel_idx = None
    for i, (lbl, _) in enumerate(request_data):
        if "★" in lbl:
            sel_idx = i
            break
    if sel_idx is not None:
        extra = TableStyle([
            ("BACKGROUND", (0, sel_idx), (-1, sel_idx), CLR_SELECTED_BG),
        ])
        request_table.setStyle(extra)

    elements.append(request_table)

    elements.append(Spacer(1, 6 * mm))

    # ═══ ۷. ارزش کل ═══
    total_calc = f"{_to_pd(int(area))} متر مربع × {_fmt(unit_value)} ریال = {_fmt(total_value)} ریال"
    total_tbl = Table(
        [
            [_p(f"مبلغ ارزش منطقه‌ای کل ({land_use})", font_size=10, bold=True, color=colors.white, align="center")],
            [_p(f"{_fmt(total_value)} ریال", font_size=16, bold=True, color=colors.white, align="center")],
            [_p(total_calc, font_size=8, color=colors.HexColor("#b0c4de"), align="center")],
        ],
        colWidths=[usable_w],
        rowHeights=[7 * mm, 12 * mm, 6 * mm],
    )
    total_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CLR_TOTAL_BG),
        ("TOPPADDING", (0, 0), (-1, -1), 2 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2 * mm),
        ("LEFTPADDING", (0, 0), (-1, -1), 4 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4 * mm),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROUNDEDCORNERS", [3, 3, 3, 3]),
    ]))
    elements.append(total_tbl)

    elements.append(Spacer(1, 8 * mm))

    # ═══ ۸. فوتر ═══
    footer_tbl = Table(
        [[_p(
            "این گزارش صرفاً بر اساس استعلام از سامانه سازمان امور مالیاتی کشور تولید شده و جنبه اطلاع‌رسانی دارد.",
            font_size=7, color=CLR_FOOTER_TEXT, align="center"
        )]],
        colWidths=[usable_w],
    )
    footer_tbl.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (-1, 0), 0.5, colors.HexColor("#e0e0e0")),
        ("TOPPADDING", (0, 0), (-1, -1), 2 * mm),
    ]))
    elements.append(footer_tbl)

    # ═══ ساخت PDF ═══
    try:
        doc = SimpleDocTemplate(
            output_path,
            pagesize=A4,
            leftMargin=10 * mm,
            rightMargin=10 * mm,
            topMargin=5 * mm,
            bottomMargin=5 * mm,
        )
        doc.build(elements)
        logger.info(f"[RV-PDF] PDF ساخته شد: {output_path}")
        return True
    except Exception as e:
        logger.error(f"[RV-PDF] خطا در ساخت PDF: {e}", exc_info=True)
        return False


def _make_text_header(usable_w) -> Table:
    """هدر متنی فال‌بک (وقتی عکس وجود نداشته باشد)."""
    header_tbl = Table(
        [[
            _p("وزارت امور اقتصادی و دارایی", font_size=11, bold=True, color=colors.white),
            _p("", font_size=1),  # spacer
        ],
        [
            _p("سازمان امور مالیاتی کشور", font_size=14, bold=True, color=CLR_LIGHT_BLUE_TEXT),
            _p("", font_size=1),
        ]],
        colWidths=[usable_w * 0.7, usable_w * 0.3],
        rowHeights=[6 * mm, 8 * mm],
    )
    header_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CLR_DARK_BG),
        ("LINEBELOW", (0, 0), (-1, -1), 2, colors.HexColor("#4a90d9")),
        ("TOPPADDING", (0, 0), (-1, -1), 2 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2 * mm),
        ("LEFTPADDING", (0, 0), (-1, -1), 5 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5 * mm),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return header_tbl
