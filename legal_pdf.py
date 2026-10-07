# -*- coding: utf-8 -*-
"""
legal_pdf.py — گزارش PDF رسمیِ یک‌صفحه‌ای برای محاسبه‌های دیه و سهم‌الارث، هم‌خانوادهٔ
گزارش ارزش منطقه‌ای و خسارت تأخیر (قاب دوخطی + نوار سرمه‌ای، فونت Vazirmatn).

build_report(output_path, title, subtitle, prefix, sections, disclaimer)
  sections: لیستی از
    ("kv", عنوان، [(برچسب، مقدار) یا (برچسب، مقدار، تمام‌عرض)])
    ("table", عنوان، [سرستون‌ها]، [[سلول‌ها]]، [نسبت عرض ستون‌ها])   ← ستون اول سمت راست
    ("amounts", [(برچسب، مبلغ ریال)], متن به حروف یا None)        ← آخرین ردیف = مبلغ اصلی
    ("bullets", عنوان، [متن])
    ("text", عنوان، متن)
"""
from __future__ import annotations

import logging

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle

from ayani_pdf import (CONTENT_W, MARGIN, PAGE_H, PAGE_W, PAL_CLASSIC, _FONTS, _draw_ltr, _draw_rtl,
                       _ensure_fonts, _kv, _money, _p)

logger = logging.getLogger(__name__)


def _report_no(prefix: str) -> str:
    try:
        from regional_value_pdf import _gregorian_to_jalali, _now_tehran
        n = _now_tehran()
        jy, jm, jd = _gregorian_to_jalali(n.year, n.month, n.day)
        return f"{prefix}-{jy:04d}{jm:02d}{jd:02d}-{n.hour:02d}{n.minute:02d}{n.second:02d}"
    except Exception:
        import time
        return f"{prefix}-{int(time.time())}"


def _now_texts():
    try:
        from regional_value_pdf import _get_persian_date, _get_persian_time
        return _get_persian_date(), _get_persian_time()
    except Exception:
        return "", ""


def build_report(output_path: str, title: str, subtitle: str, prefix: str, sections: list,
                 disclaimer: str) -> bool:
    _ensure_fonts()
    P = PAL_CLASSIC
    band_h = 30 * mm
    W = CONTENT_W - 4 * mm
    date_text, time_text = _now_texts()
    report_no = _report_no(prefix)

    def page(c, doc):
        c.saveState()
        c.setStrokeColor(P["accent"]); c.setLineWidth(1.3)
        c.rect(8 * mm, 8 * mm, PAGE_W - 16 * mm, PAGE_H - 16 * mm)
        c.setStrokeColor(P["gold"]); c.setLineWidth(0.5)
        c.rect(9.6 * mm, 9.6 * mm, PAGE_W - 19.2 * mm, PAGE_H - 19.2 * mm)
        top = PAGE_H - 9.6 * mm
        if doc.page == 1:
            c.setFillColor(P["accent"])
            c.rect(9.6 * mm, top - band_h, PAGE_W - 19.2 * mm, band_h, stroke=0, fill=1)
            c.setFillColor(P["gold"])
            c.rect(9.6 * mm, top - band_h - 1.1 * mm, PAGE_W - 19.2 * mm, 1.1 * mm, stroke=0, fill=1)
            rx, lx = PAGE_W - MARGIN - 2 * mm, MARGIN + 2 * mm
            _draw_rtl(c, rx, top - 13 * mm, title, _FONTS["bold"], 17, colors.white)
            _draw_rtl(c, rx, top - 21 * mm, subtitle, _FONTS["regular"], 9.5, colors.HexColor("#C8D3E6"))
            _draw_ltr(c, lx, top - 11 * mm, f"تاریخ: {date_text}", _FONTS["regular"], 8.5, colors.white)
            _draw_ltr(c, lx, top - 17 * mm, f"ساعت: {time_text}", _FONTS["regular"], 8.5, colors.white)
            _draw_ltr(c, lx, top - 23 * mm, f"شماره: {report_no}", _FONTS["regular"], 8.5,
                      colors.HexColor("#E3C77E"))
        y = 13 * mm
        c.setStrokeColor(P["line"]); c.setLineWidth(0.5)
        c.line(MARGIN, y + 4 * mm, PAGE_W - MARGIN, y + 4 * mm)
        _draw_rtl(c, PAGE_W - MARGIN, y, disclaimer, _FONTS["regular"], 7.2, P["muted"])
        _draw_ltr(c, MARGIN, y, f"صفحهٔ {doc.page}", _FONTS["regular"], 7.2, P["muted"])
        c.restoreState()

    def section(t):
        tb = Table([[_p(f"■  {t}", 10.5, P["accent"], "bold")]], colWidths=[W])
        tb.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.8, P["gold"]),
                                ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.0 * mm)]))
        return [tb, Spacer(1, 1.6 * mm)]

    el = [Spacer(1, band_h - 2 * mm)]
    for sec in sections:
        kind = sec[0]
        if kind == "kv":
            el += section(sec[1])
            el.append(_kv(sec[2], P, W, style="grid"))
        elif kind == "table":
            _, t, headers, rows, ratios = sec
            el += section(t)
            widths = [W * r for r in ratios]
            data = [list(reversed([_p(h, 8, colors.white, "bold", "center") for h in headers]))]
            for row in rows:
                cells = []
                for j, v in enumerate(row):
                    cells.append(_p(str(v), 8.6, P["ink"], "bold" if j == len(row) - 1 else "regular",
                                    "right" if j == 0 else "center"))
                data.append(list(reversed(cells)))
            tb = Table(data, colWidths=list(reversed(widths)), repeatRows=1)
            cmds = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("BACKGROUND", (0, 0), (-1, 0), P["accent"]),
                    ("BOX", (0, 0), (-1, -1), 0.6, P["line"]), ("INNERGRID", (0, 1), (-1, -1), 0.35, P["line"]),
                    ("TOPPADDING", (0, 0), (-1, -1), 1.6 * mm), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.6 * mm),
                    ("LEFTPADDING", (0, 0), (-1, -1), 1.6 * mm), ("RIGHTPADDING", (0, 0), (-1, -1), 1.6 * mm)]
            for r in range(2, len(data), 2):
                cmds.append(("BACKGROUND", (0, r), (-1, r), P["soft"]))
            tb.setStyle(TableStyle(cmds))
            el.append(tb)
        elif kind == "amounts":
            _, rows_, words = sec
            lab_w = W * 0.55
            rows = []
            for i, (label, amount) in enumerate(rows_):
                main = i == len(rows_) - 1
                rows.append([_p(f"{_money(amount)} ریال", 15 if main else 11, colors.white if main else P["ink"],
                                "bold", "left"),
                             _p(label, 12 if main else 10, colors.white if main else P["ink"],
                                "bold" if main else "regular")])
            last = len(rows) - 1
            tb = Table(rows, colWidths=[W - lab_w, lab_w])
            cmds = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("BOX", (0, 0), (-1, -1), 0.8, P["accent"]),
                    ("BACKGROUND", (0, last), (-1, last), P["accent"]),
                    ("TOPPADDING", (0, 0), (-1, -1), 2.6 * mm), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.6 * mm),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4 * mm), ("RIGHTPADDING", (0, 0), (-1, -1), 4 * mm)]
            if last:
                cmds += [("BACKGROUND", (0, 0), (-1, last - 1), P["accent_soft"]),
                         ("LINEBELOW", (0, 0), (-1, last - 1), 0.5, P["line"]),
                         ("LINEABOVE", (0, last), (-1, last), 1.2, P["gold"])]
            tb.setStyle(TableStyle(cmds))
            el.append(tb)
            if words:
                el.append(Spacer(1, 2 * mm))
                el.append(_p(f"به حروف: {words} ریال", 8.6, P["muted"]))
        elif kind == "bullets":
            el += section(sec[1])
            for ln in sec[2]:
                el.append(_p(f"• {ln}", 8.6, P["ink"], leading=8.6 * 1.7))
        elif kind == "text":
            el += section(sec[1])
            el.append(_p(sec[2], 8.3, P["muted"], leading=8.3 * 1.75))
        el.append(Spacer(1, 5 * mm))
    try:
        doc = SimpleDocTemplate(output_path, pagesize=A4, title=title, author="",
                                leftMargin=MARGIN + 2 * mm, rightMargin=MARGIN + 2 * mm,
                                topMargin=12 * mm, bottomMargin=22 * mm)
        doc.build(el, onFirstPage=page, onLaterPages=page)
        return True
    except Exception as e:
        logger.error(f"[LEGAL-PDF] خطا در ساخت PDF: {e}", exc_info=True)
        return False


# ══════════════════════════════════════════════════════════════════
# دیه
# ══════════════════════════════════════════════════════════════════
def build_diyeh_pdf(output_path: str, r: dict) -> bool:
    from ayani_pdf import num_to_words
    import diyeh_calc as dc
    sections = [("kv", "ورودی‌ها", [(k, v, len(v) > 40) for k, v in r["inputs"]]),
                ("kv", "مبنای محاسبه", [("دیهٔ کامل سال " + str(r["year"]), f"{_money(r['full'])} ریال"),
                                        ("کسر دیهٔ کامل", dc.fraction_text(r["fraction"]))]),
                ("amounts", [("مبلغ دیه", r["amount"])], num_to_words(r["amount"])),
                ("bullets", "نحوهٔ محاسبه", r["steps"]),
                ("bullets", "مستند قانونی (قانون مجازات اسلامی)", r["laws"])]
    return build_report(output_path, "گزارش محاسبهٔ دیه", "بر اساس قانون مجازات اسلامی (کتاب دیات)", "DY",
                        sections, "این گزارش صرفاً جهت اطلاع است؛ ملاک نهایی رأی دادگاه و نظر پزشکی قانونی است.")


# ══════════════════════════════════════════════════════════════════
# سهم‌الارث
# ══════════════════════════════════════════════════════════════════
def build_inheritance_pdf(output_path: str, r: dict) -> bool:
    import inheritance_calc as ic
    rows, estate = r["rows"], r["estate"]
    lcd = ic.common_denominator(rows)
    headers = ["وارث", "تعداد", "سهم هر نفر", f"با مخرج {lcd}", "درصد"] + (["مبلغ هر نفر (ریال)"] if estate else [])
    ratios = [0.34, 0.08, 0.14, 0.14, 0.1] + ([0.2] if estate else [])
    total = sum(ratios)
    ratios = [x / total for x in ratios]
    table = []
    for row in rows:
        pct = float(row["per_person"]) * 100
        line = [row["label"], str(row["count"]), ic.share_text(row["per_person"]),
                ic.share_text(row["per_person"], lcd), f"{pct:.2f}".rstrip("0").rstrip(".") + "٪"]
        if estate:
            line.append(_money(round(row["per_person"] * estate)))
        table.append(line)
    sections = [("kv", "ورودی‌ها", [(k, v, len(v) > 40) for k, v in r["inputs"]]
                 + ([("ارزش ترکه", f"{_money(estate)} ریال")] if estate else [])),
                ("table", "سهم هر وارث", headers, table, ratios)]
    notes = list(r["notes"]) + ["سهام بدون در نظر گرفتن دیون، وصیت و مالیات بر ارث و صرفاً بر اساس قواعد ارث "
                                "قانون مدنی است؛ «سهم هر نفر» کسری از کل ترکه است."]
    sections += [("bullets", "توضیحات", notes), ("bullets", "مستند قانونی (قانون مدنی)", r["laws"])]
    return build_report(output_path, "گزارش تقسیم ترکه (سهم‌الارث)", "بر اساس قانون مدنی", "IN", sections,
                        "این گزارش صرفاً جهت اطلاع است؛ ملاک نهایی گواهی انحصار وراثت و رأی دادگاه است.")
