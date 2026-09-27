# -*- coding: utf-8 -*-
"""
PDF گزارش ارزش منطقه‌ای (عرصه + اعیانی) — دو صفحه، با چند طرح گرافیکی.

  صفحهٔ ۱: مشخصات ملک و ورودی‌ها + سه مبلغ (عرصه، اعیانی، کل)
  صفحهٔ ۲: نحوهٔ محاسبه (گام‌به‌گام) + ضوابط اعمال‌شده

فونت: Vazirmatn (نسخهٔ ارقام فارسی، مجوز OFL) از پوشهٔ fonts/ پروژه؛
اگر نبود، به فونت‌های ویندوز (Tahoma) برمی‌گردد.

طرح‌ها (پارامتر design در build_ayani_pdf):
  classic  — رسمی/سندی: قاب دوخطی، نوار سرمه‌ای، جدول‌های کادردار
  cards    — کارت مدرن: کارت بزرگ مبلغ کل در بالا + کارت‌های عرصه و اعیانی
  minimal  — مینیمال تک‌رنگ: تایپوگرافی درشت، خطوط نازک، بدون پس‌زمینه
  sidebar  — ستون کناری: ستون رنگی راست با سه مبلغ + جزئیات در متن اصلی
"""

import logging
import os
import re

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

import ayani_calc as ac

logger = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
_FONT_DIR = os.path.join(_HERE, "fonts")

# ═══ پالت ═══
INK = colors.HexColor("#1F2937")
MUTED = colors.HexColor("#6B7280")
LINE = colors.HexColor("#E5E7EB")
SOFT = colors.HexColor("#F5F7F9")
ACCENT = colors.HexColor("#16324F")
ACCENT_SOFT = colors.HexColor("#E8EEF4")
NEG = colors.HexColor("#9B2C2C")

PAGE_W, PAGE_H = A4
MARGIN = 16 * mm
CONTENT_W = PAGE_W - 2 * MARGIN

_FONTS = {"regular": "Helvetica", "bold": "Helvetica-Bold"}
_fonts_ready = False


def _ensure_fonts():
    global _fonts_ready
    if _fonts_ready:
        return
    candidates = [
        ("AyaniRegular", os.path.join(_FONT_DIR, "Vazirmatn-FD-Regular.ttf"), "regular"),
        ("AyaniBold", os.path.join(_FONT_DIR, "Vazirmatn-FD-Bold.ttf"), "bold"),
        ("AyaniMedium", os.path.join(_FONT_DIR, "Vazirmatn-FD-Medium.ttf"), "medium"),
        ("AyaniLight", os.path.join(_FONT_DIR, "Vazirmatn-FD-Light.ttf"), "light"),
    ]
    fallbacks = {
        "regular": ["C:\\Windows\\Fonts\\tahoma.ttf"],
        "bold": ["C:\\Windows\\Fonts\\tahomabd.ttf", "C:\\Windows\\Fonts\\tahoma.ttf"],
        "medium": ["C:\\Windows\\Fonts\\tahoma.ttf"],
        "light": ["C:\\Windows\\Fonts\\tahoma.ttf"],
    }
    for name, path, role in candidates:
        paths = [path] + fallbacks[role]
        for p in paths:
            if os.path.exists(p):
                try:
                    pdfmetrics.registerFont(TTFont(name, p))
                    _FONTS[role] = name
                    break
                except Exception as e:
                    logger.warning(f"[AYANI-PDF] ثبت فونت {p} ناموفق: {e}")
    _FONTS.setdefault("medium", _FONTS["bold"])
    _FONTS.setdefault("light", _FONTS["regular"])
    _fonts_ready = True


def _bidi(text: str) -> str:
    if not text:
        return ""
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(str(text)))
    except ImportError:
        logger.warning("[AYANI-PDF] arabic_reshaper/python-bidi نصب نیست")
        return str(text)


_style_cache = {}


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class RTLParagraph(Paragraph):
    """
    پاراگراف فارسی با شکست خط صحیح: متن منطقی ابتدا بر اساس عرض ستون به
    خطوط شکسته می‌شود و سپس الگوریتم BiDi روی هر خط جداگانه اعمال می‌شود
    (اگر BiDi روی کل متن اعمال شود، ترتیب خطوطِ متن چندخطی برعکس می‌شود).
    """

    def __init__(self, text, style):
        self._logical = str(text)
        super().__init__(_esc(_bidi(self._logical)), style)

    def wrap(self, availWidth, availHeight):
        st = self.style
        try:
            import arabic_reshaper
            from bidi.algorithm import get_display
            words = arabic_reshaper.reshape(self._logical).split()
            space = pdfmetrics.stringWidth(" ", st.fontName, st.fontSize)
            lines, cur, cur_w = [], [], 0.0
            for w in words:
                ww = pdfmetrics.stringWidth(w, st.fontName, st.fontSize)
                if cur and cur_w + space + ww > availWidth - 1:
                    lines.append(" ".join(cur))
                    cur, cur_w = [w], ww
                else:
                    cur_w += (space if cur else 0) + ww
                    cur.append(w)
            if cur:
                lines.append(" ".join(cur))
            visual = "<br/>".join(_esc(get_display(ln, base_dir="R")) for ln in lines)
            self.__init_text(visual)
        except ImportError:
            pass
        return super().wrap(availWidth, availHeight)

    def __init_text(self, visual):
        Paragraph.__init__(self, visual, self.style)


def _p(text, size=9.5, color=INK, weight="regular", align="right", leading=None) -> Paragraph:
    key = (size, color.hexval(), weight, align, leading)
    st = _style_cache.get(key)
    if st is None:
        st = ParagraphStyle(
            name=f"ay{len(_style_cache)}",
            fontName=_FONTS.get(weight, _FONTS["regular"]),
            fontSize=size, leading=leading or size * 1.55, textColor=color,
            alignment={"right": 2, "center": 1, "left": 0}[align],
        )
        _style_cache[key] = st
    text = re.sub(r"(?<=\d),(?=\d)", "٬", str(text))
    text = re.sub(r"(?<=\d)\.(?=\d)", "٫", text)
    return RTLParagraph(text, st)


def _money(n: int) -> str:
    s = f"{abs(int(n)):,}".replace(",", "٬")
    return f"({s})" if n < 0 else s


def _floor_label(f) -> str:
    if f is None:
        return "—"
    f = int(f)
    if f == 0:
        return "همکف"
    return f"زیرزمین {_num(abs(f))}" if f < 0 else _num(f)


def _num(x) -> str:
    return ac.fmt(x).replace(",", "٬").replace(".", "٫")


# ═══ عدد به حروف ═══
_ONES = ["", "یک", "دو", "سه", "چهار", "پنج", "شش", "هفت", "هشت", "نه"]
_TENS = ["", "ده", "بیست", "سی", "چهل", "پنجاه", "شصت", "هفتاد", "هشتاد", "نود"]
_TEENS = ["ده", "یازده", "دوازده", "سیزده", "چهارده", "پانزده", "شانزده", "هفده", "هجده", "نوزده"]
_HUNDREDS = ["", "صد", "دویست", "سیصد", "چهارصد", "پانصد", "ششصد", "هفتصد", "هشتصد", "نهصد"]
_SCALES = ["", "هزار", "میلیون", "میلیارد", "هزار میلیارد", "میلیون میلیارد"]


def _three(n: int) -> str:
    parts = []
    h, r = divmod(n, 100)
    if h:
        parts.append(_HUNDREDS[h])
    if 10 <= r < 20:
        parts.append(_TEENS[r - 10])
    else:
        t, o = divmod(r, 10)
        if t:
            parts.append(_TENS[t])
        if o:
            parts.append(_ONES[o])
    return " و ".join(parts)


def num_to_words(n: int) -> str:
    n = int(n)
    if n == 0:
        return "صفر"
    groups, i = [], 0
    while n > 0:
        n, g = divmod(n, 1000)
        if g:
            groups.append((_three(g) + (" " + _SCALES[i] if _SCALES[i] else "")).strip())
        i += 1
    return " و ".join(reversed(groups))



# ══════════════════════════════════════════════════════════════════
# جمع‌آوری محتوا (مشترک بین همهٔ طرح‌ها)
# ══════════════════════════════════════════════════════════════════
def _collect(province, county, address, tax_result, result, date_text, time_text, plak=None) -> dict:
    import ownership_share as osh
    land, b = result["land"], result.get("building")
    land_share = osh.frac_from_str(result.get("land_share") or "1")
    bld_share = osh.frac_from_str(result.get("building_share") or "1")
    has_share = land_share != 1 or (b is not None and bld_share != 1)
    structured = (tax_result or {}).get("فیلدهای_ساختاریافته", {}) or {}
    year = (tax_result or {}).get("سال", "1405")

    def tax_field(k):
        v = structured.get(k)
        return str(v).strip() if v else "—"

    prop = [
        ("استان", province), ("شهرستان", county),
        ("آدرس", address or "—", True),
    ]
    if plak:
        prop.append(("پلاک ثبتی", plak, True))
    prop += [
        ("شماره بلوک", tax_field("شماره بلوک بر اساس دفترچه ارزش معاملاتی ملک")),
        ("شماره ردیف", tax_field("شماره ردیف بر اساس دفترچه ارزش معاملاتی ملک")),
        ("اداره کل امور مالیاتی", tax_field("اداره کل امور مالیاتی"), True),
    ]

    land_pairs = [("متراژ عرصه", f"{_num(land['area'])} متر مربع"),
                  ("کاربری", land["land_use"])]
    if land["land_use"] == "سایر":
        land_pairs.append(("نوع کاربری", land["land_use_title"], True))
        land_pairs.append(("ضریب تعدیل", _num(land["coef"])))
    land_pairs.append(("ارزش هر متر", f"{_money(land['unit_value'])} ریال"))
    land_pairs.append(("سهم مالکانه", osh.format_share(land_share), True))
    if result.get("land_share_desc") and land_share != 1:
        land_pairs.append(("مقدار در سند", result["land_share_desc"], True))

    if b is None:
        bld = [("اعیانی", "ملک فاقد اعیانی است", True)]
    else:
        bld = [("کاربری", b["use_title"] if b["use_key"] in ac.BUILDING_MAIN_KEYS else "سایر"),
               ("نوع سازه", b["structure_title"])]
        if b["use_key"] not in ac.BUILDING_MAIN_KEYS:
            bld.append(("نوع کاربری", b["use_title"], True))
        bld += [("متراژ اعیانی", f"{_num(b['area'])} متر مربع"),
                ("نرخ هر متر", f"{_money(b['rate'])} ریال")]
        if not b["complete"]:
            bld.append(("وضعیت ساختمان", f"ناتمام — مرحلهٔ {b['stage_title']}"))
        else:
            bld += [("وضعیت ساختمان", "تکمیل‌شده"),
                    ("پارکینگ و انباری", f"{_num(b['parking_area'])} متر مربع" if b["parking_area"] > 0 else "ندارد")]
            if b["use_key"] in ac.BUILDING_MAIN_KEYS:
                bld.append(("طبقه", _floor_label(b["floor"])))
            bld.append(("قدمت", f"{_num(b['age'])} سال"))
        bld.append(("سهم مالکانه", osh.format_share(bld_share), True))
        if result.get("building_share_desc") and bld_share != 1:
            bld.append(("مقدار در سند", result["building_share_desc"], True))

    rules = ["ارزش عرصه از سامانهٔ سازمان امور مالیاتی (ارزش معاملاتی هر متر مربع) × متراژ عرصه محاسبه شده است."]
    if land["land_use"] == "سایر":
        rules.append(f"برای کاربری‌های «سایر»، ارزش عرصه بر مبنای ارزش معاملاتی {land['base_use']} × ضریب تعدیل "
                     f"{_num(land['coef'])} محاسبه می‌شود (ضرایب: ۰٫۷، ۰٫۵، ۰٫۴، ۰٫۲، ۰٫۱).")
    if b is not None:
        rules.append("نرخ هر متر مربع اعیانی از جدول ارزش معاملاتی ساختمان به تفکیک شهرستان، کاربری و نوع سازه (به ریال) است.")
        if not b["complete"]:
            rules.append("برای ساختمان ناتمام، ارزش اعیانی به نسبت مرحلهٔ ساخت منظور می‌شود: "
                         "فونداسیون ۱۰٪، اسکلت ۳۰٪، سفت‌کاری ۵۰٪ و نازک‌کاری ۸۰٪.")
        else:
            if b["use_key"] in ac.BUILDING_MAIN_KEYS:
                rules += [
                    "مسکونی و اداری بیش از پنج طبقه (بدون احتساب زیرزمین و پیلوت): از طبقهٔ ششم به بالا به ازای هر طبقه ۱٫۵٪ به نرخ هر متر افزوده می‌شود.",
                    "تجاری: به ازای هر طبقه بالاتر یا پایین‌تر از همکف ۱۰٪ و حداکثر ۳۰٪ از نرخ هر متر کسر می‌شود.",
                ]
            rules += [
                "پارکینگ و انباری متعلق به واحد معادل ۵۰٪ نرخ هر متر مربع ساختمان محاسبه می‌شود.",
                "به ازای هر سال قدمت تا سقف ۲۰ سال، ۲٪ (حداکثر ۴۰٪) از کل ارزش اعیانی کسر می‌شود.",
            ]
    if has_share:
        rules.append("ارزش هر بخش ابتدا برای کل ملک (ششدانگ) محاسبه و سپس در سهم مالکانه (کسری از ششدانگ؛ "
                     "هر دانگ = یک‌ششم) ضرب شده است.")
    if b is None:
        rules.append("ملک فاقد اعیانی است؛ ارزش منطقه‌ای کل برابر ارزش عرصه است.")
    elif has_share:
        rules.append("ارزش منطقه‌ای کل = ارزش سهم عرصه + ارزش سهم اعیانی.")
    else:
        rules.append("ارزش منطقه‌ای کل = ارزش عرصه + ارزش اعیانی.")

    # «اطلاعات مکان انتخابی» و «ارزش معاملاتی» همان‌طور که سامانهٔ مالیاتی برگردانده
    try:
        from regional_value_pdf import SYSTEM_FIELDS, VALUE_FIELDS, _get_field_value
    except Exception:
        SYSTEM_FIELDS, VALUE_FIELDS = [], []
        _get_field_value = lambda tr, f: "—"

    def _clean(v):
        v = str(v or "").replace("ي", "ی").replace("ك", "ک").strip()
        return v if v and v != "-" else "—"

    location = [(f, _clean(_get_field_value(tax_result or {}, f))) for f in SYSTEM_FIELDS]
    tax_values = [(f, _clean(_get_field_value(tax_result or {}, f))) for f in VALUE_FIELDS]

    return {
        "location": location, "tax_values": tax_values,
        "province": province, "county": county, "year": year,
        "date": date_text, "time": time_text,
        "report_no": _report_no(),
        "prop": prop, "land": land_pairs, "bld": bld,
        "land_value": result.get("land_value", land["value"]),
        "bld_value": result.get("building_value", b["value"] if b else 0),
        "land_label": "ارزش سهم عرصه" if land_share != 1 else "ارزش عرصه",
        "bld_label": ("ارزش اعیانی (ندارد)" if b is None
                      else "ارزش سهم اعیانی" if bld_share != 1 else "ارزش اعیانی"),
        "scope_title": "عرصه و اعیانی" if b is not None else "عرصه (ملک فاقد اعیانی)",
        "total": result["total"],
        "words": num_to_words(result["total"]),
        "steps": ac.explain_steps(result), "rules": rules,
        "disclaimer": "این گزارش بر اساس استعلام از سامانهٔ سازمان امور مالیاتی و جدول ارزش معاملاتی "
                      "اعیانی تهیه شده و جنبهٔ اطلاع‌رسانی دارد.",
    }


def _report_no() -> str:
    """شمارهٔ گزارش: RV-سال‌ماه‌روز-ساعت‌دقیقه‌ثانیه (تقویم شمسی، وقت تهران)."""
    try:
        from regional_value_pdf import _now_tehran, _gregorian_to_jalali
        n = _now_tehran()
        jy, jm, jd = _gregorian_to_jalali(n.year, n.month, n.day)
        return f"RV-{jy:04d}{jm:02d}{jd:02d}-{n.hour:02d}{n.minute:02d}{n.second:02d}"
    except Exception:
        import time
        return f"RV-{int(time.time())}"


def _to_en_digits(s: str) -> str:
    return str(s).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))


# ══════════════════════════════════════════════════════════════════
# اجزای مشترک (پالت‌پذیر)
# ══════════════════════════════════════════════════════════════════
def _kv(pairs, P, width=CONTENT_W, cols=2, style="zebra", lab_size=8.3, val_size=9.5, pad=1.7, lab_ratio=0.34):
    """
    جدول برچسب/مقدار راست‌به‌چپ. pairs: [(label, value, full?)]
    style: zebra (نوار یک‌درمیان) | grid (کادر کامل) | lines (فقط خط زیر) | plain
    """
    rows, cmds = [], [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2.2 * mm), ("RIGHTPADDING", (0, 0), (-1, -1), 2.2 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), pad * mm), ("BOTTOMPADDING", (0, 0), (-1, -1), pad * mm),
    ]
    ncol = cols * 2
    lab_w = width * (lab_ratio / cols)
    val_w = width / cols - lab_w
    buf = []

    def cell_val(v):
        return _p(v, val_size, P["ink"], "medium")

    def cell_lab(l):
        return _p(l, lab_size, P["muted"])

    def flush():
        cells = []
        for lab, val in reversed(buf):
            cells += [cell_val(val), cell_lab(lab)]
        while len(cells) < ncol:
            cells = [_p("", 1), _p("", 1)] + cells
        rows.append(cells)
        buf.clear()

    for item in pairs:
        lab, val = item[0], item[1]
        full = len(item) > 2 and item[2]
        if full and cols > 1:
            if buf:
                flush()
            rows.append([cell_val(val)] + [""] * (ncol - 2) + [cell_lab(lab)])
            cmds.append(("SPAN", (0, len(rows) - 1), (ncol - 2, len(rows) - 1)))
            continue
        buf.append((lab, val))
        if len(buf) == cols:
            flush()
    if buf:
        flush()

    n = len(rows)
    if style == "zebra":
        for i in range(0, n, 2):
            cmds.append(("BACKGROUND", (0, i), (-1, i), P["soft"]))
        cmds.append(("LINEBELOW", (0, 0), (-1, -1), 0.35, P["line"]))
    elif style == "grid":
        cmds.append(("GRID", (0, 0), (-1, -1), 0.5, P["line"]))
        for c in range(1, ncol, 2):
            cmds.append(("BACKGROUND", (c, 0), (c, -1), P["soft"]))
        for cmd in [x for x in cmds if x[0] == "SPAN"]:
            r = cmd[1][1]
            cmds.append(("BACKGROUND", (0, r), (ncol - 2, r), colors.white))
    elif style == "lines":
        cmds.append(("LINEBELOW", (0, 0), (-1, -1), 0.3, P["line"]))
    t = Table(rows, colWidths=[val_w, lab_w] * cols)
    t.setStyle(TableStyle(cmds))
    return t


def _steps(steps, P, width=CONTENT_W, header_fill=None, header_text=None, zebra=False, total_fill=None,
           total_text=None, pad=2.1):
    ht = header_text or P["muted"]
    head = [_p("مبلغ (ریال)", 8.3, ht, "bold", "left"), _p("محاسبه", 8.3, ht, "bold"),
            _p("شرح", 8.3, ht, "bold"), _p("#", 8.3, ht, "bold", "center")]
    rows = [head]
    for i, (title, formula, amount) in enumerate(steps, 1):
        last = i == len(steps)
        amt = "" if amount is None else _money(amount)
        tc = total_text if (last and total_text) else P["ink"]
        color = P["neg"] if (amount is not None and amount < 0) else tc
        rows.append([
            _p(amt, 10.5 if last else 9.5, color, "bold" if last else "medium", "left"),
            _p(formula, 8.3, total_text if (last and total_text) else P["muted"]),
            _p(title, 10 if last else 9.3, tc, "bold" if last else "regular"),
            _p(_num(i), 8.3, total_text if (last and total_text) else P["muted"], align="center"),
        ])
    t = Table(rows, colWidths=[width * 0.22, width * 0.46, width * 0.26, width * 0.06], repeatRows=1)
    cmds = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), pad * mm), ("BOTTOMPADDING", (0, 0), (-1, -1), pad * mm),
        ("LEFTPADDING", (0, 0), (-1, -1), 2 * mm), ("RIGHTPADDING", (0, 0), (-1, -1), 2 * mm),
        ("LINEBELOW", (0, 1), (-1, -2), 0.35, P["line"]),
    ]
    if header_fill:
        cmds.append(("BACKGROUND", (0, 0), (-1, 0), header_fill))
    else:
        cmds.append(("LINEBELOW", (0, 0), (-1, 0), 0.9, P["accent"]))
    if zebra:
        for r in range(2, len(rows) - 1, 2):
            cmds.append(("BACKGROUND", (0, r), (-1, r), P["soft"]))
    cmds.append(("BACKGROUND", (0, -1), (-1, -1), total_fill or P["accent_soft"]))
    t.setStyle(TableStyle(cmds))
    return t


def _bullets(lines, P, size=8.4):
    return [_p(f"• {ln}", size, P["ink"], leading=size * 1.65) for ln in lines]


def _box(flowables, width, bg=None, border=None, radius=0, pad=4 * mm, border_w=0.6):
    """کادر/کارت دور یک یا چند flowable."""
    t = Table([[f] for f in flowables], colWidths=[width])
    cmds = [("LEFTPADDING", (0, 0), (-1, -1), pad), ("RIGHTPADDING", (0, 0), (-1, -1), pad),
            ("TOPPADDING", (0, 0), (-1, -1), 0.6 * mm), ("BOTTOMPADDING", (0, 0), (-1, -1), 0.6 * mm),
            ("TOPPADDING", (0, 0), (-1, 0), pad * 0.8), ("BOTTOMPADDING", (0, -1), (-1, -1), pad * 0.8)]
    if bg:
        cmds.append(("BACKGROUND", (0, 0), (-1, -1), bg))
    if border:
        cmds.append(("BOX", (0, 0), (-1, -1), border_w, border))
    if radius:
        cmds.append(("ROUNDEDCORNERS", [radius] * 4))
    t.setStyle(TableStyle(cmds))
    return t


def _row(cells, widths, valign="TOP"):
    """چینش افقی راست‌به‌چپ (اولین سلولِ لیست، سمت راست قرار می‌گیرد)."""
    cells, widths = list(reversed(cells)), list(reversed(widths))
    t = Table([cells], colWidths=widths)
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), valign),
                           ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                           ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    return t


def _draw_rtl(c, x_right, y, text, font, size, color):
    c.setFont(font, size)
    c.setFillColor(color)
    c.drawRightString(x_right, y, _bidi(text))


def _draw_ltr(c, x_left, y, text, font, size, color):
    c.setFont(font, size)
    c.setFillColor(color)
    c.drawString(x_left, y, _bidi(text))


def _footer_line(c, doc, P, left=MARGIN, right=PAGE_W - MARGIN, y=10 * mm, total_pages=2, text=None):
    c.setStrokeColor(P["line"])
    c.setLineWidth(0.5)
    c.line(left, y + 4 * mm, right, y + 4 * mm)
    _draw_rtl(c, right, y, text or "", _FONTS["regular"], 7.2, P["muted"])
    _draw_ltr(c, left, y, f"صفحهٔ {doc.page} از {total_pages}", _FONTS["regular"], 7.2, P["muted"])


# ══════════════════════════════════════════════════════════════════
# طرح ۱ — «رسمی» (classic)
# ══════════════════════════════════════════════════════════════════
PAL_CLASSIC = dict(ink=colors.HexColor("#1B2430"), muted=colors.HexColor("#5B6573"),
                   line=colors.HexColor("#C9D1DC"), soft=colors.HexColor("#EEF2F7"),
                   accent=colors.HexColor("#14284B"), accent_soft=colors.HexColor("#E6EBF3"),
                   gold=colors.HexColor("#B08D3C"), neg=colors.HexColor("#9B2C2C"))


def _render_classic(ctx):
    P = PAL_CLASSIC
    band_h = 30 * mm
    band2_h = 20 * mm   # صفحهٔ دوم: نوار باریک‌تر تا محتوا در یک صفحه جا شود
    W = CONTENT_W - 4 * mm

    def page(c, doc):
        c.saveState()
        bh = band_h if doc.page == 1 else band2_h
        # قاب دوخطی
        c.setStrokeColor(P["accent"]); c.setLineWidth(1.3)
        c.rect(8 * mm, 8 * mm, PAGE_W - 16 * mm, PAGE_H - 16 * mm)
        c.setStrokeColor(P["gold"]); c.setLineWidth(0.5)
        c.rect(9.6 * mm, 9.6 * mm, PAGE_W - 19.2 * mm, PAGE_H - 19.2 * mm)
        # نوار سرتیتر
        top = PAGE_H - 9.6 * mm
        c.setFillColor(P["accent"])
        c.rect(9.6 * mm, top - bh, PAGE_W - 19.2 * mm, bh, stroke=0, fill=1)
        c.setFillColor(P["gold"])
        c.rect(9.6 * mm, top - bh - 1.1 * mm, PAGE_W - 19.2 * mm, 1.1 * mm, stroke=0, fill=1)
        rx = PAGE_W - MARGIN - 2 * mm
        lx = MARGIN + 2 * mm
        if doc.page == 1:
            _draw_rtl(c, rx, top - 13 * mm, "گزارش ارزش منطقه‌ای ملک", _FONTS["bold"], 18, colors.white)
            _draw_rtl(c, rx, top - 21 * mm, f"{ctx['scope_title']} — سال {ctx['year']}", _FONTS["regular"], 9.5,
                      colors.HexColor("#C8D3E6"))
            _draw_ltr(c, lx, top - 11 * mm, f"تاریخ: {ctx['date']}", _FONTS["regular"], 8.5, colors.white)
            _draw_ltr(c, lx, top - 17 * mm, f"ساعت: {ctx['time']}", _FONTS["regular"], 8.5, colors.white)
            _draw_ltr(c, lx, top - 23 * mm, f"شماره: {ctx['report_no']}", _FONTS["regular"], 8.5,
                      colors.HexColor("#E3C77E"))
        else:
            _draw_rtl(c, rx, top - 12.5 * mm, "نحوهٔ محاسبه و اطلاعات مکان", _FONTS["bold"], 15, colors.white)
            _draw_ltr(c, lx, top - 9 * mm, f"{ctx['province']} — {ctx['county']}", _FONTS["regular"], 8.5,
                      colors.white)
            _draw_ltr(c, lx, top - 15 * mm, f"شماره: {ctx['report_no']}", _FONTS["regular"], 8.5,
                      colors.HexColor("#E3C77E"))
        _footer_line(c, doc, P, left=MARGIN, right=PAGE_W - MARGIN, y=13 * mm, text=ctx["disclaimer"])
        c.restoreState()

    def section(title):
        t = Table([[_p(f"■  {title}", 10.5, P["accent"], "bold")]], colWidths=[W])
        t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.8, P["gold"]),
                               ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 1.0 * mm)]))
        return [t, Spacer(1, 1.6 * mm)]

    el = [Spacer(1, band_h - 2 * mm)]
    el += section("مشخصات ملک")
    el.append(_kv(ctx["prop"], P, W, style="grid"))
    el.append(Spacer(1, 5 * mm))
    el += section("عرصه")
    el.append(_kv(ctx["land"], P, W, style="grid"))
    el.append(Spacer(1, 5 * mm))
    el += section("اعیانی")
    el.append(_kv(ctx["bld"], P, W, style="grid"))
    el.append(Spacer(1, 6 * mm))

    # جدول مبالغ
    lab_w = W * 0.55
    rows = [
        [_p(f"{_money(ctx['land_value'])} ریال", 11, P["ink"], "bold", "left"), _p(ctx["land_label"], 10, P["ink"])],
        [_p(f"{_money(ctx['bld_value'])} ریال", 11, P["ink"], "bold", "left"), _p(ctx["bld_label"], 10, P["ink"])],
        [_p(f"{_money(ctx['total'])} ریال", 15, colors.white, "bold", "left"),
         _p("ارزش منطقه‌ای کل", 12, colors.white, "bold")],
    ]
    t = Table(rows, colWidths=[W - lab_w, lab_w])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOX", (0, 0), (-1, -1), 0.8, P["accent"]),
        ("LINEBELOW", (0, 0), (-1, 1), 0.5, P["line"]),
        ("BACKGROUND", (0, 0), (-1, 1), P["accent_soft"]),
        ("BACKGROUND", (0, 2), (-1, 2), P["accent"]),
        ("LINEABOVE", (0, 2), (-1, 2), 1.2, P["gold"]),
        ("TOPPADDING", (0, 0), (-1, -1), 2.6 * mm), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.6 * mm),
        ("LEFTPADDING", (0, 0), (-1, -1), 4 * mm), ("RIGHTPADDING", (0, 0), (-1, -1), 4 * mm),
    ]))
    el.append(t)
    el.append(Spacer(1, 2 * mm))
    el.append(_p(f"به حروف: {ctx['words']} ریال", 8.6, P["muted"]))

    el.append(PageBreak())
    el.append(Spacer(1, band2_h - 3 * mm))
    if ctx["location"]:
        el += section("اطلاعات مکان انتخابی")
        el.append(_kv(ctx["location"], P, W, cols=1, style="grid", pad=0.9, lab_size=7.8, val_size=8.6,
                      lab_ratio=0.42))
        el.append(Spacer(1, 1.5 * mm))
        el.append(_kv(ctx["tax_values"], P, W, cols=3, style="grid", pad=0.9, lab_size=7.4, val_size=8.6,
                      lab_ratio=0.56))
        el.append(Spacer(1, 4 * mm))
    el += section("محاسبهٔ گام‌به‌گام")
    el.append(_steps(ctx["steps"], P, W, header_fill=P["accent"], header_text=colors.white, zebra=True,
                     total_fill=P["accent_soft"], pad=1.5))
    el.append(Spacer(1, 4 * mm))
    el += section("ضوابط اعمال‌شده")
    el += _bullets(ctx["rules"], P, size=7.9)
    return el, page, dict(leftMargin=MARGIN + 2 * mm, rightMargin=MARGIN + 2 * mm,
                          topMargin=12 * mm, bottomMargin=22 * mm)


# ══════════════════════════════════════════════════════════════════
# طرح ۲ — «کارت مدرن» (cards)
# ══════════════════════════════════════════════════════════════════
PAL_CARDS = dict(ink=colors.HexColor("#15232B"), muted=colors.HexColor("#6A7A83"),
                 line=colors.HexColor("#E3E9EC"), soft=colors.HexColor("#F4F7F8"),
                 accent=colors.HexColor("#0F5257"), accent_soft=colors.HexColor("#E3F0EF"),
                 amber=colors.HexColor("#E0A43B"), neg=colors.HexColor("#B4442F"),
                 page=colors.HexColor("#F2F5F6"))


def _render_cards(ctx):
    P = PAL_CARDS
    W = CONTENT_W

    def page(c, doc):
        c.saveState()
        c.setFillColor(P["page"])
        c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
        c.setFillColor(P["accent"])
        c.rect(0, PAGE_H - 4 * mm, PAGE_W, 4 * mm, stroke=0, fill=1)
        c.setFillColor(P["amber"])
        c.rect(PAGE_W - MARGIN - 28 * mm, PAGE_H - 4 * mm, 28 * mm, 4 * mm, stroke=0, fill=1)
        _footer_line(c, doc, P, y=9 * mm, text=ctx["disclaimer"])
        c.restoreState()

    def header(title, sub):
        return _row([
            [_p(title, 18, P["accent"], "bold"), _p(sub, 9, P["muted"])],
            [_p(f"{ctx['date']}  •  ساعت {ctx['time']}", 8.5, P["muted"], align="left"),
             _p(f"شماره گزارش: {ctx['report_no']}", 8, P["muted"], align="left")],
        ], [W * 0.62, W * 0.38], valign="BOTTOM")

    def card(title, body, width, bg=colors.white):
        return _box([_p(title, 10, P["accent"], "bold"), Spacer(1, 1.5 * mm)] + body, width, bg=bg, radius=6,
                    pad=4 * mm)

    el = [header("گزارش ارزش منطقه‌ای ملک", f"{ctx['scope_title']} — سال {ctx['year']}"), Spacer(1, 5 * mm)]

    # کارت بزرگ مبلغ کل
    hero = _box([
        _p("ارزش منطقه‌ای کل", 10, colors.HexColor("#BFDCDA")),
        _p(f"{_money(ctx['total'])}", 26, colors.white, "bold", leading=34),
        _p(f"ریال  —  {ctx['words']} ریال", 8.5, colors.HexColor("#BFDCDA")),
    ], W, bg=P["accent"], radius=8, pad=6 * mm)
    el += [hero, Spacer(1, 4 * mm)]

    gap = 4 * mm
    half = (W - gap) / 2

    def mini(label, value, width):
        return _box([_p(label, 9, P["muted"]),
                     _p(f"{_money(value)} ریال", 14, P["ink"], "bold", leading=20)],
                    width, bg=colors.white, radius=6, pad=4 * mm)

    el.append(_row([mini(ctx["land_label"], ctx["land_value"], half), Spacer(gap, 1),
                    mini(ctx["bld_label"], ctx["bld_value"], half)], [half, gap, half]))
    el.append(Spacer(1, 4 * mm))

    inner = W - 8 * mm
    el.append(card("مشخصات ملک", [_kv(ctx["prop"], P, inner, style="lines", pad=1.4)], W))
    el.append(Spacer(1, 4 * mm))
    inner_half = half - 8 * mm
    el.append(_row([
        card("عرصه", [_kv([(x[0], x[1]) for x in ctx["land"]], P, inner_half, cols=1, style="lines", pad=1.3)], half),
        Spacer(gap, 1),
        card("اعیانی", [_kv([(x[0], x[1]) for x in ctx["bld"]], P, inner_half, cols=1, style="lines", pad=1.3)], half),
    ], [half, gap, half]))

    el.append(PageBreak())
    el += [header("نحوهٔ محاسبه", f"{ctx['province']} — {ctx['county']}"), Spacer(1, 5 * mm)]
    el.append(card("محاسبهٔ گام‌به‌گام", [_steps(ctx["steps"], P, W - 8 * mm, total_fill=P["accent_soft"])], W))
    el.append(Spacer(1, 4 * mm))
    el.append(card("ضوابط اعمال‌شده", _bullets(ctx["rules"], P), W))
    return el, page, dict(leftMargin=MARGIN, rightMargin=MARGIN, topMargin=13 * mm, bottomMargin=20 * mm)


# ══════════════════════════════════════════════════════════════════
# طرح ۳ — «مینیمال» (minimal)
# ══════════════════════════════════════════════════════════════════
PAL_MIN = dict(ink=colors.HexColor("#111111"), muted=colors.HexColor("#7A7A7A"),
               line=colors.HexColor("#DADADA"), soft=colors.HexColor("#F6F6F6"),
               accent=colors.HexColor("#111111"), accent_soft=colors.HexColor("#F3F3F3"),
               neg=colors.HexColor("#8A1C1C"))


def _render_minimal(ctx):
    P = PAL_MIN
    W = CONTENT_W - 6 * mm

    def page(c, doc):
        c.saveState()
        c.setFillColor(P["ink"])
        c.rect(PAGE_W - 14 * mm, PAGE_H - 44 * mm, 1.6 * mm, 26 * mm, stroke=0, fill=1)
        _footer_line(c, doc, P, left=MARGIN + 3 * mm, right=PAGE_W - MARGIN - 3 * mm, y=11 * mm,
                     text=ctx["disclaimer"])
        c.restoreState()

    def header(title, sub):
        return [
            _p(sub, 8.5, P["muted"], "regular"),
            _p(title, 24, P["ink"], "bold", leading=32),
            _p(f"{ctx['date']}   |   ساعت {ctx['time']}   |   {ctx['report_no']}", 8, P["muted"]),
            Spacer(1, 7 * mm),
        ]

    def section(t):
        return [_p(t, 8.5, P["muted"], "medium"), Spacer(1, 0.5 * mm), _hr(W, P["ink"], 0.8), Spacer(1, 1 * mm)]

    el = header("گزارش ارزش منطقه‌ای ملک", f"{ctx['scope_title']} — سال {ctx['year']}")

    # اعداد اصلی
    third = W / 3

    def figure(label, value, big=False):
        return [_p(label, 8.5, P["muted"]),
                _p(_money(value), 20 if big else 14, P["ink"], "bold" if big else "medium",
                   leading=28 if big else 22),
                _p("ریال", 7.5, P["muted"])]

    fig = _row([figure("ارزش منطقه‌ای کل", ctx["total"], True), figure(ctx["land_label"], ctx["land_value"]),
                figure(ctx["bld_label"], ctx["bld_value"])], [third * 1.3, third * 0.85, third * 0.85], valign="BOTTOM")
    el += [_hr(W, P["ink"], 1.4), Spacer(1, 3 * mm), fig, Spacer(1, 2 * mm),
           _p(f"به حروف: {ctx['words']} ریال", 8.3, P["muted"]), Spacer(1, 3 * mm), _hr(W, P["line"], 0.5),
           Spacer(1, 7 * mm)]

    el += section("مشخصات ملک")
    el.append(_kv(ctx["prop"], P, W, style="lines", lab_size=8, pad=1.8))
    el.append(Spacer(1, 6 * mm))
    half = (W - 8 * mm) / 2
    el.append(_row([
        section_block("عرصه", [(x[0], x[1]) for x in ctx["land"]], P, half),
        Spacer(8 * mm, 1),
        section_block("اعیانی", [(x[0], x[1]) for x in ctx["bld"]], P, half),
    ], [half, 8 * mm, half]))

    el.append(PageBreak())
    el += header("نحوهٔ محاسبه", f"{ctx['province']} — {ctx['county']}")
    el.append(_steps(ctx["steps"], P, W, total_fill=P["soft"]))
    el.append(Spacer(1, 8 * mm))
    el += section("ضوابط اعمال‌شده")
    el += _bullets(ctx["rules"], P)
    return el, page, dict(leftMargin=MARGIN + 3 * mm, rightMargin=MARGIN + 3 * mm,
                          topMargin=18 * mm, bottomMargin=22 * mm)


def section_block(title, pairs, P, width):
    t = Table([[_p(title, 8.5, P["muted"], "medium")], [_hr(width, P["ink"], 0.8)],
               [_kv(pairs, P, width, cols=1, style="lines", lab_size=8, pad=1.6)]], colWidths=[width])
    t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                           ("TOPPADDING", (0, 0), (-1, -1), 0.5 * mm), ("BOTTOMPADDING", (0, 0), (-1, -1), 0.5 * mm)]))
    return t


def _hr(width, color, thickness):
    t = Table([[""]], colWidths=[width], rowHeights=[0.2])
    t.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, -1), thickness, color),
                           ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    return t


# ══════════════════════════════════════════════════════════════════
# طرح ۴ — «ستون کناری» (sidebar)
# ══════════════════════════════════════════════════════════════════
PAL_SIDE = dict(ink=colors.HexColor("#17231E"), muted=colors.HexColor("#66756E"),
                line=colors.HexColor("#DDE5E1"), soft=colors.HexColor("#F3F7F5"),
                accent=colors.HexColor("#123D30"), accent_soft=colors.HexColor("#E4EFEA"),
                light=colors.HexColor("#A9CDBE"), gold=colors.HexColor("#D2B46C"),
                neg=colors.HexColor("#9B2C2C"))

SIDE_W = 66 * mm


def _render_sidebar(ctx):
    from reportlab.platypus import BaseDocTemplate, Frame, NextPageTemplate, PageTemplate, FrameBreak
    P = PAL_SIDE
    gutter = 9 * mm
    main_w = PAGE_W - SIDE_W - 2 * gutter - 10 * mm

    def page1(c, doc):
        c.saveState()
        c.setFillColor(P["accent"])
        c.rect(PAGE_W - SIDE_W, 0, SIDE_W, PAGE_H, stroke=0, fill=1)
        c.setFillColor(P["gold"])
        c.rect(PAGE_W - SIDE_W, 0, 1.2 * mm, PAGE_H, stroke=0, fill=1)
        _draw_ltr(c, 10 * mm, 9 * mm, f"صفحهٔ {doc.page} از ۲", _FONTS["regular"], 7.2, P["muted"])
        c.restoreState()

    def page2(c, doc):
        c.saveState()
        c.setFillColor(P["accent"])
        c.rect(PAGE_W - 6 * mm, 0, 6 * mm, PAGE_H, stroke=0, fill=1)
        c.setFillColor(P["gold"])
        c.rect(PAGE_W - 7.2 * mm, 0, 1.2 * mm, PAGE_H, stroke=0, fill=1)
        _footer_line(c, doc, P, left=MARGIN, right=PAGE_W - MARGIN - 6 * mm, y=10 * mm, text=ctx["disclaimer"])
        c.restoreState()

    sw = SIDE_W - 2 * gutter
    side = [
        _p("گزارش", 10, P["light"]),
        _p("ارزش منطقه‌ای ملک", 17, colors.white, "bold", leading=25),
        _p(f"{ctx['scope_title']} — سال {ctx['year']}", 8.5, P["light"]),
        Spacer(1, 3 * mm), _hr(sw, P["gold"], 0.8), Spacer(1, 4 * mm),
        _p(f"تاریخ: {ctx['date']}", 8.5, colors.white),
        _p(f"ساعت: {ctx['time']}", 8.5, colors.white),
        _p(f"شماره: {ctx['report_no']}", 8.5, P["gold"]),
        Spacer(1, 14 * mm),
    ]
    for label, val in ((ctx["land_label"], ctx["land_value"]), (ctx["bld_label"], ctx["bld_value"])):
        side += [_p(label, 8.5, P["light"]), _p(_money(val), 15, colors.white, "bold", leading=22),
                 _p("ریال", 7.5, P["light"]), Spacer(1, 5 * mm)]
    side += [_hr(sw, P["gold"], 0.8), Spacer(1, 4 * mm),
             _p("ارزش منطقه‌ای کل", 10, P["gold"], "bold"),
             _p(_money(ctx["total"]), 20, colors.white, "bold", leading=28),
             _p("ریال", 8, P["light"]), Spacer(1, 3 * mm),
             _p(f"{ctx['words']} ریال", 8, P["light"], leading=13),
             Spacer(1, 18 * mm),
             _p(ctx["disclaimer"], 6.8, P["light"], leading=10.5)]

    def section(t):
        return [_p(t, 10.5, P["accent"], "bold"), Spacer(1, 0.6 * mm), _hr(main_w, P["gold"], 0.9),
                Spacer(1, 1.8 * mm)]

    main = []
    main += section("مشخصات ملک")
    main.append(_kv(ctx["prop"], P, main_w, cols=1, style="zebra", pad=1.6))
    main.append(Spacer(1, 6 * mm))
    main += section("عرصه")
    main.append(_kv([(x[0], x[1]) for x in ctx["land"]], P, main_w, cols=1, style="zebra", pad=1.6))
    main.append(Spacer(1, 6 * mm))
    main += section("اعیانی")
    main.append(_kv([(x[0], x[1]) for x in ctx["bld"]], P, main_w, cols=1, style="zebra", pad=1.6))

    p2w = PAGE_W - 2 * MARGIN - 6 * mm
    p2 = [_p("نحوهٔ محاسبه", 18, P["accent"], "bold", leading=26),
          _p(f"{ctx['province']} — {ctx['county']}  •  {ctx['date']}", 8.5, P["muted"]),
          Spacer(1, 3 * mm), _hr(p2w, P["gold"], 1.1), Spacer(1, 5 * mm),
          _steps(ctx["steps"], P, p2w, header_fill=P["accent"], header_text=colors.white, zebra=True),
          Spacer(1, 8 * mm), _p("ضوابط اعمال‌شده", 10.5, P["accent"], "bold"), Spacer(1, 0.6 * mm),
          _hr(p2w, P["gold"], 0.9), Spacer(1, 1.8 * mm)] + _bullets(ctx["rules"], P)

    frames1 = [
        Frame(PAGE_W - SIDE_W + gutter, 12 * mm, sw, PAGE_H - 26 * mm, id="side",
              leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0),
        Frame(10 * mm, 16 * mm, main_w, PAGE_H - 30 * mm, id="main",
              leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0),
    ]
    frame2 = [Frame(MARGIN, 20 * mm, p2w, PAGE_H - 36 * mm, id="p2",
                    leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)]
    templates = [PageTemplate(id="first", frames=frames1, onPage=page1),
                 PageTemplate(id="calc", frames=frame2, onPage=page2)]
    story = side + [FrameBreak()] + main + [NextPageTemplate("calc"), PageBreak()] + p2
    return story, templates


# ══════════════════════════════════════════════════════════════════
# ساخت PDF
# ══════════════════════════════════════════════════════════════════
DEFAULT_DESIGN = "classic"   # طرح انتخابی کارفرما
DESIGNS = {"classic": _render_classic, "cards": _render_cards, "minimal": _render_minimal,
           "sidebar": _render_sidebar}


def build_ayani_pdf(output_path: str, *, province: str, county: str, address: str,
                    tax_result: dict, result: dict, date_text: str = None, time_text: str = None,
                    design: str = None, plak: str = None) -> bool:
    """
    result: خروجی ayani_calc.compute_all
    tax_result: خروجی سامانهٔ مالیاتی (برای شماره بلوک/ردیف و اداره)
    design: یکی از DESIGNS (پیش‌فرض DEFAULT_DESIGN)
    """
    _ensure_fonts()
    try:
        from regional_value_pdf import _get_persian_date, _get_persian_time
        date_text = date_text or _get_persian_date()
        time_text = time_text or _get_persian_time()
    except Exception:
        date_text = date_text or ""
        time_text = time_text or ""

    ctx = _collect(province, county, address, tax_result, result, date_text, time_text, plak=plak)
    design = design if design in DESIGNS else DEFAULT_DESIGN
    try:
        if design == "sidebar":
            from reportlab.platypus import BaseDocTemplate
            story, templates = _render_sidebar(ctx)
            doc = BaseDocTemplate(output_path, pagesize=A4, title="گزارش ارزش منطقه‌ای ملک", author="")
            doc.addPageTemplates(templates)
            doc.build(story)
        else:
            story, on_page, margins = DESIGNS[design](ctx)
            doc = SimpleDocTemplate(output_path, pagesize=A4, title="گزارش ارزش منطقه‌ای ملک",
                                    author="", **margins)
            doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
        return True
    except Exception as e:
        logger.error(f"[AYANI-PDF] خطا در ساخت PDF: {e}", exc_info=True)
        return False
