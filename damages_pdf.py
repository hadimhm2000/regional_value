# -*- coding: utf-8 -*-
"""
PDF رسمی نتیجهٔ «خسارت تأخیر تأدیه» و «مهریه به نرخ روز» — یک صفحه، طرح رسمی
(قاب دوخطی + نوار سرمه‌ای) هم‌خانوادهٔ گزارش ارزش منطقه‌ای (ayani_pdf.py).

اجزای مشترک (فونت Vazirmatn، پاراگراف راست‌به‌چپ، جدول برچسب/مقدار، عدد به
حروف) از ayani_pdf گرفته می‌شود تا ظاهر گزارش‌های ربات یکدست بماند.
ورودی‌ها خروجی damages_calc.calc_late_payment / calc_mahrieh هستند.
"""
import logging

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle

from ayani_pdf import (
    CONTENT_W, MARGIN, PAGE_H, PAGE_W, PAL_CLASSIC, _FONTS, _draw_ltr, _draw_rtl,
    _ensure_fonts, _footer_line, _kv, _money, _p, num_to_words,
)

logger = logging.getLogger(__name__)

_MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
           "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]

DISCLAIMER = ("این گزارش بر اساس شاخص بانک مرکزی و صرفاً جهت اطلاع تهیه شده است؛ "
              "ملاک نهایی، محاسبهٔ دادگاه یا واحد اجرای احکام است.")

ART_522 = ("مادهٔ ۵۲۲ قانون آیین دادرسی دادگاه‌های عمومی و انقلاب (در امور مدنی) مصوب ۱۳۷۹: "
           "در دعاویی که موضوع آن دین و از نوع وجه رایج بوده و با مطالبهٔ داین و تمکن مدیون، "
           "مدیون امتناع از پرداخت نموده، در صورت تغییر فاحش شاخص قیمت سالانه از زمان سررسید "
           "تا هنگام پرداخت و پس از مطالبهٔ طلبکار، دادگاه با رعایت تناسب تغییر شاخص سالانه که "
           "توسط بانک مرکزی جمهوری اسلامی ایران تعیین می‌گردد محاسبه و مورد حکم قرار خواهد داد، "
           "مگر اینکه طرفین به نحو دیگری مصالحه نمایند.")

ART_1082 = ("تبصرهٔ مادهٔ ۱۰۸۲ قانون مدنی: چنانچه مهریه وجه رایج باشد، متناسب با تغییر شاخص "
            "قیمت سالانهٔ زمان تأدیه نسبت به سال اجرای عقد که توسط بانک مرکزی جمهوری اسلامی "
            "ایران تعیین می‌گردد محاسبه و پرداخت خواهد شد، مگر اینکه زوجین در حین اجرای عقد به "
            "نحو دیگری تراضی کرده باشند.")


def month_label(key: str) -> str:
    """«1403/07» → «مهر ۱۴۰۳»"""
    y, m = key.split("/")
    return f"{_MONTHS[int(m) - 1]} {y}"


def jdate_label(t) -> str:
    return f"{t[0]:04d}/{t[1]:02d}/{t[2]:02d}"


def _idx(v) -> str:
    return f"{float(v):.3f}".rstrip("0").rstrip(".")


def _report_no(prefix: str) -> str:
    try:
        from regional_value_pdf import _gregorian_to_jalali, _now_tehran
        n = _now_tehran()
        jy, jm, jd = _gregorian_to_jalali(n.year, n.month, n.day)
        return f"{prefix}-{jy:04d}{jm:02d}{jd:02d}-{n.hour:02d}{n.minute:02d}{n.second:02d}"
    except Exception:
        import time
        return f"{prefix}-{int(time.time())}"


def _now_texts() -> tuple[str, str]:
    try:
        from regional_value_pdf import _get_persian_date, _get_persian_time
        return _get_persian_date(), _get_persian_time()
    except Exception:
        return "", ""


# ══════════════════════════════════════════════════════════════════
# چیدمان مشترک
# ══════════════════════════════════════════════════════════════════
def _build(output_path: str, ctx: dict) -> bool:
    """
    ctx: title, subtitle, report_no, inputs[(lab,val,full?)], indices[...],
         results[(label, amount)] (آخری = مبلغ اصلی), words, notes[str], law
    """
    _ensure_fonts()
    P = PAL_CLASSIC
    band_h = 30 * mm
    W = CONTENT_W - 4 * mm
    date_text, time_text = _now_texts()

    def page(c, doc):
        c.saveState()
        c.setStrokeColor(P["accent"]); c.setLineWidth(1.3)
        c.rect(8 * mm, 8 * mm, PAGE_W - 16 * mm, PAGE_H - 16 * mm)
        c.setStrokeColor(P["gold"]); c.setLineWidth(0.5)
        c.rect(9.6 * mm, 9.6 * mm, PAGE_W - 19.2 * mm, PAGE_H - 19.2 * mm)
        top = PAGE_H - 9.6 * mm
        c.setFillColor(P["accent"])
        c.rect(9.6 * mm, top - band_h, PAGE_W - 19.2 * mm, band_h, stroke=0, fill=1)
        c.setFillColor(P["gold"])
        c.rect(9.6 * mm, top - band_h - 1.1 * mm, PAGE_W - 19.2 * mm, 1.1 * mm, stroke=0, fill=1)
        rx, lx = PAGE_W - MARGIN - 2 * mm, MARGIN + 2 * mm
        _draw_rtl(c, rx, top - 13 * mm, ctx["title"], _FONTS["bold"], 17, colors.white)
        _draw_rtl(c, rx, top - 21 * mm, ctx["subtitle"], _FONTS["regular"], 9.5, colors.HexColor("#C8D3E6"))
        _draw_ltr(c, lx, top - 11 * mm, f"تاریخ: {date_text}", _FONTS["regular"], 8.5, colors.white)
        _draw_ltr(c, lx, top - 17 * mm, f"ساعت: {time_text}", _FONTS["regular"], 8.5, colors.white)
        _draw_ltr(c, lx, top - 23 * mm, f"شماره: {ctx['report_no']}", _FONTS["regular"], 8.5,
                  colors.HexColor("#E3C77E"))
        _footer_line(c, doc, P, y=13 * mm, total_pages=1, text=DISCLAIMER)
        c.restoreState()

    def section(title):
        t = Table([[_p(f"■  {title}", 10.5, P["accent"], "bold")]], colWidths=[W])
        t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.8, P["gold"]),
                               ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 1.0 * mm)]))
        return [t, Spacer(1, 1.6 * mm)]

    el = [Spacer(1, band_h - 2 * mm)]
    el += section("ورودی‌ها")
    el.append(_kv(ctx["inputs"], P, W, style="grid"))
    el.append(Spacer(1, 5 * mm))
    el += section("شاخص‌های اعمال‌شده")
    el.append(_kv(ctx["indices"], P, W, style="grid"))
    el.append(Spacer(1, 6 * mm))

    lab_w = W * 0.55
    rows = []
    for i, (label, amount) in enumerate(ctx["results"]):
        main = i == len(ctx["results"]) - 1
        rows.append([
            _p(f"{_money(amount)} ریال", 15 if main else 11, colors.white if main else P["ink"], "bold", "left"),
            _p(label, 12 if main else 10, colors.white if main else P["ink"], "bold" if main else "regular"),
        ])
    last = len(rows) - 1
    t = Table(rows, colWidths=[W - lab_w, lab_w])
    cmds = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOX", (0, 0), (-1, -1), 0.8, P["accent"]),
        ("BACKGROUND", (0, last), (-1, last), P["accent"]),
        ("TOPPADDING", (0, 0), (-1, -1), 2.6 * mm), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.6 * mm),
        ("LEFTPADDING", (0, 0), (-1, -1), 4 * mm), ("RIGHTPADDING", (0, 0), (-1, -1), 4 * mm),
    ]
    if last:
        cmds += [("BACKGROUND", (0, 0), (-1, last - 1), P["accent_soft"]),
                 ("LINEBELOW", (0, 0), (-1, last - 1), 0.5, P["line"]),
                 ("LINEABOVE", (0, last), (-1, last), 1.2, P["gold"])]
    t.setStyle(TableStyle(cmds))
    el.append(t)
    el.append(Spacer(1, 2 * mm))
    el.append(_p(f"به حروف: {ctx['words']} ریال", 8.6, P["muted"]))
    el.append(Spacer(1, 6 * mm))

    el += section("توضیحات")
    for ln in ctx["notes"]:
        el.append(_p(f"• {ln}", 8.6, P["ink"], leading=8.6 * 1.7))
    el.append(Spacer(1, 4 * mm))
    el += section("مستند قانونی")
    el.append(_p(ctx["law"], 8.3, P["muted"], leading=8.3 * 1.75))

    try:
        doc = SimpleDocTemplate(output_path, pagesize=A4, title=ctx["title"], author="",
                                leftMargin=MARGIN + 2 * mm, rightMargin=MARGIN + 2 * mm,
                                topMargin=12 * mm, bottomMargin=22 * mm)
        doc.build(el, onFirstPage=page, onLaterPages=page)
        return True
    except Exception as e:
        logger.error(f"[DAMAGES-PDF] خطا در ساخت PDF: {e}", exc_info=True)
        return False


# ══════════════════════════════════════════════════════════════════
# خسارت تأخیر تأدیه
# ══════════════════════════════════════════════════════════════════
def build_late_payment_pdf(output_path: str, r: dict, due: tuple, pay: tuple) -> bool:
    """r: خروجی damages_calc.calc_late_payment؛ due/pay: تاریخ‌های (سال، ماه، روز)."""
    notes = [
        "ارزش ریالی دین در زمان پرداخت برابر است با عدد شاخص زمان پرداخت تقسیم بر عدد شاخص "
        "زمان سررسید، ضربدر مبلغ دین؛ خسارت تأخیر تأدیه، تفاوت این مبلغ با اصل دین است.",
        f"فرمول: {_money(r['amount'])} × ({_idx(r['target_index'])} ÷ {_idx(r['base_index'])})",
        f"شاخص بهای کالاها و خدمات مصرفی بانک مرکزی با سال پایهٔ {r['cpi_base']} استفاده شده است.",
    ]
    if r["used_latest_available"]:
        notes.append(
            f"شاخص ماه پرداخت ({month_label(r['pay_key'])}) هنوز منتشر نشده و از نزدیک‌ترین شاخص موجود "
            f"({month_label(r['target_key'])}) استفاده شده است؛ پس از انتشار شاخص ماه پرداخت، "
            "برای نتیجهٔ دقیق‌تر محاسبه را تکرار کنید.")
    ctx = {
        "title": "گزارش محاسبهٔ خسارت تأخیر تأدیه",
        "subtitle": "بر اساس شاخص بهای کالاها و خدمات مصرفی بانک مرکزی",
        "report_no": _report_no("DT"),
        "inputs": [
            ("مبلغ دین", f"{_money(r['amount'])} ریال", True),
            ("زمان سررسید یا مطالبه", jdate_label(due)),
            ("زمان پرداخت", jdate_label(pay)),
        ],
        "indices": [
            ("شاخص زمان سررسید", f"{_idx(r['base_index'])}  ({month_label(r['due_key'])})"),
            ("شاخص زمان پرداخت", f"{_idx(r['target_index'])}  ({month_label(r['target_key'])})"),
        ],
        "results": [("خسارت به تنهایی", r["damages"]), ("اصل دین و خسارت", r["updated_amount"])],
        "words": num_to_words(r["updated_amount"]),
        "notes": notes,
        "law": ART_522,
    }
    return _build(output_path, ctx)


# ══════════════════════════════════════════════════════════════════
# مهریه به نرخ روز
# ══════════════════════════════════════════════════════════════════
def build_mahrieh_pdf(output_path: str, r: dict) -> bool:
    """r: خروجی damages_calc.calc_mahrieh"""
    ctx = {
        "title": "گزارش محاسبهٔ مهریه به نرخ روز",
        "subtitle": "بر اساس شاخص سالانهٔ بهای کالاها و خدمات مصرفی بانک مرکزی",
        "report_no": _report_no("MH"),
        "inputs": [
            ("مبلغ مهریه", f"{_money(r['amount'])} ریال", True),
            ("سال وقوع عقد", str(r["marriage_year"])),
            ("سال تأدیه", str(r["target_year"] + 1)),
        ],
        "indices": [
            ("شاخص سال عقد", f"{_idx(r['base_index'])}  (سال {r['marriage_year']})"),
            ("شاخص سال قبل از تأدیه", f"{_idx(r['target_index'])}  (سال {r['target_year']})"),
        ],
        "results": [("مهریه به نرخ روز", r["updated_amount"])],
        "words": num_to_words(r["updated_amount"]),
        "notes": [
            "مهریهٔ به نرخ روز برابر است با مبلغ مهریه ضربدر عدد شاخص سال قبل از سال تأدیه، "
            "تقسیم بر عدد شاخص سال وقوع عقد.",
            f"فرمول: {_money(r['amount'])} × ({_idx(r['target_index'])} ÷ {_idx(r['base_index'])})",
            f"شاخص سالانه = میانگین شاخص ماهانهٔ بانک مرکزی همان سال (سال پایهٔ {r['cpi_base']}).",
        ],
        "law": ART_1082,
    }
    return _build(output_path, ctx)
