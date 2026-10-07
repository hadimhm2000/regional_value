# -*- coding: utf-8 -*-
"""
rates_admin.py — به‌روزرسانی جدول نرخ اعیانی (ayani_rates.json) از داخل ربات.

روال سالانه:
  ۱. ادمین /rates می‌زند → فایل اکسل نرخ‌های فعلی دریافت می‌کند.
  ۲. نرخ‌های سال جدید را در همان فایل وارد می‌کند (ساختار ستون‌ها تغییر نکند).
  ۳. فایل را برای ربات می‌فرستد → ربات اعتبارسنجی می‌کند و خلاصهٔ تغییرات را
     (تعداد خانه‌های تغییرکرده، بیشترین تغییرها، شهرستان‌های اضافه/حذف‌شده) نشان می‌دهد.
  ۴. با زدن «اعمال»، نسخهٔ قبلی پشتیبان‌گیری و نرخ‌های جدید بدون ری‌استارت فعال می‌شود.
     /rates_rollback آخرین نسخهٔ پشتیبان را برمی‌گرداند.

این ماژول وابستگی به aiogram ندارد (قابل تست مستقل).
"""
from __future__ import annotations

import datetime
import glob
import json
import os
import shutil

import ayani_calc

SHEET_TITLE = "نرخ اعیانی"

# ترتیب ستون‌های نرخ: (کلید کاربری، کلید سازه، عنوان ستون)
RATE_COLUMNS = []
_USE_SHORT = {
    "residential": "مسکونی", "commercial": "تجاری", "administrative": "اداری",
    "industrial": "صنعتی، خدماتی و ...", "agricultural": "کشاورزی، دامداری و ...",
}
_STRUCT_SHORT = {"concrete": "بتنی/فلزی/سوله", "other": "سایر سازه‌ها"}
for _u in ("residential", "commercial", "administrative", "industrial", "agricultural"):
    for _s in ("concrete", "other"):
        RATE_COLUMNS.append((_u, _s, f"{_USE_SHORT[_u]} — {_STRUCT_SHORT[_s]}"))

FIXED_HEADERS = ["استان", "شهرستان", "عرض جغرافیایی", "طول جغرافیایی"]
HEADERS = FIXED_HEADERS + [t for _, _, t in RATE_COLUMNS]

# حداکثر نسبت تغییر یک خانه که بدون هشدار پذیرفته می‌شود (مثلاً تایپ یک صفر اضافه)
SUSPICIOUS_RATIO = 3.0


def backup_dir() -> str:
    from config import DATA_DIR
    d = os.path.join(DATA_DIR, "rates_backups")
    os.makedirs(d, exist_ok=True)
    return d


# ══════════════════════════════════════════════════════════════════
# خروجی اکسل
# ══════════════════════════════════════════════════════════════════
def export_xlsx(path: str, rates: dict | None = None) -> int:
    """نوشتن نرخ‌های فعلی در اکسل؛ خروجی: تعداد ردیف (شهرستان)."""
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Font, PatternFill

    rates = rates or ayani_calc.load_rates()
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_TITLE
    ws.sheet_view.rightToLeft = True
    ws.append(HEADERS)
    head_fill = PatternFill("solid", fgColor="14284B")
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = head_fill
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 42
    filled_fill = PatternFill("solid", fgColor="FFF4CC")
    n = 0
    for prov, counties in rates["provinces"].items():
        for c in counties:
            row = [prov, c["county"], c.get("lat"), c.get("lng")]
            row += [c["rates"].get(u, {}).get(s) for u, s, _ in RATE_COLUMNS]
            ws.append(row)
            n += 1
            filled = c.get("filled") or {}
            for j, (u, s, _) in enumerate(RATE_COLUMNS):
                src = filled.get(f"{u}.{s}")
                if src:
                    cell = ws.cell(row=ws.max_row, column=len(FIXED_HEADERS) + 1 + j)
                    cell.fill = filled_fill
                    cell.comment = Comment(f"در جدول اصلی خالی بود؛ از شهرستان «{src}» پر شده است.", "ربات")
    for i in range(1, len(HEADERS) + 1):
        letter = ws.cell(row=1, column=i).column_letter
        ws.column_dimensions[letter].width = 16 if i > 4 else (18 if i <= 2 else 13)
    for row in ws.iter_rows(min_row=2, min_col=5):
        for cell in row:
            cell.number_format = "#,##0"
    ws.freeze_panes = "C2"

    info = wb.create_sheet("راهنما")
    info.sheet_view.rightToLeft = True
    for line in [
        "راهنمای به‌روزرسانی نرخ اعیانی",
        f"واحد: {rates.get('unit', 'ریال بر متر مربع')} — آخرین به‌روزرسانی: {rates.get('generated_at', '-')}",
        "۱. فقط مقادیر برگهٔ «نرخ اعیانی» را تغییر دهید؛ ترتیب و عنوان ستون‌ها را تغییر ندهید.",
        "۲. همهٔ نرخ‌ها باید عدد مثبت (ریال) باشند؛ خانهٔ خالی پذیرفته نمی‌شود.",
        "۳. برای افزودن شهرستان جدید، یک ردیف با نام استان (دقیقاً مثل ردیف‌های دیگر)، نام شهرستان و مختصات اضافه کنید.",
        "۴. حذف ردیف = حذف شهرستان؛ ربات پیش از اعمال، فهرست شهرستان‌های حذف‌شده را نشان می‌دهد.",
        "۵. خانه‌های زرد در جدول اصلی خالی بوده‌اند و از شهرستان همسایه پر شده‌اند.",
        "۶. پس از ویرایش، همین فایل را برای ربات بفرستید؛ تا «اعمال» را نزنید هیچ تغییری فعال نمی‌شود.",
    ]:
        info.append([line])
    info.column_dimensions["A"].width = 110
    wb.save(path)
    return n


# ══════════════════════════════════════════════════════════════════
# خواندن و اعتبارسنجی اکسل
# ══════════════════════════════════════════════════════════════════
def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    t = str(v).strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    t = t.replace(",", "").replace("٬", "").replace("٫", ".").replace(" ", "")
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return "bad"


def parse_xlsx(path: str, current: dict | None = None) -> dict:
    """
    خروجی: {"ok": bool, "errors": [...], "warnings": [...], "rates": dict|None, "summary": str}
    """
    from openpyxl import load_workbook

    current = current or ayani_calc.load_rates()
    errors, warnings = [], []
    try:
        wb = load_workbook(path, data_only=True, read_only=True)
    except Exception as e:
        return {"ok": False, "errors": [f"فایل اکسل قابل خواندن نیست: {e}"], "warnings": [], "rates": None,
                "summary": ""}
    ws = wb[SHEET_TITLE] if SHEET_TITLE in wb.sheetnames else wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return {"ok": False, "errors": ["فایل خالی است."], "warnings": [], "rates": None, "summary": ""}
    header = [str(h).strip() if h is not None else "" for h in rows[0]]
    if header[:len(HEADERS)] != HEADERS:
        return {"ok": False, "errors": ["عنوان ستون‌ها با قالب ربات یکی نیست. لطفاً فایل را از /rates دریافت و "
                                        "فقط مقادیر را ویرایش کنید."],
                "warnings": [], "rates": None, "summary": ""}

    cur_index = {}
    prov_norm = {}
    for prov, counties in current["provinces"].items():
        prov_norm[ayani_calc.normalize_name(prov)] = prov
        for c in counties:
            cur_index[(prov, ayani_calc.normalize_name(c["county"]))] = c

    new_provinces: dict = {}
    seen = {}   # (استان، نام نرمال شهرستان) → نام نمایشی
    for i, row in enumerate(rows[1:], start=2):
        row = list(row) + [None] * (len(HEADERS) - len(row))
        if all(v in (None, "") for v in row[:len(HEADERS)]):
            continue
        prov_raw, county = str(row[0] or "").strip(), str(row[1] or "").strip()
        if not prov_raw or not county:
            errors.append(f"ردیف {i}: نام استان یا شهرستان خالی است.")
            continue
        prov = prov_norm.get(ayani_calc.normalize_name(prov_raw), prov_raw)
        if prov not in current["provinces"]:
            warnings.append(f"ردیف {i}: استان جدید «{prov_raw}»")
        key = (prov, ayani_calc.normalize_name(county))
        if key in seen:
            errors.append(f"ردیف {i}: شهرستان «{county}» در استان «{prov}» تکراری است.")
            continue
        seen[key] = county
        old = cur_index.get(key)
        lat, lng = _num(row[2]), _num(row[3])
        if lat in (None, "bad") or lng in (None, "bad"):
            if old:
                lat, lng = old.get("lat"), old.get("lng")
            else:
                errors.append(f"ردیف {i}: مختصات شهرستان جدید «{county}» وارد نشده است.")
                continue
        elif not (24 <= float(lat) <= 40.5 and 44 <= float(lng) <= 63.5):
            errors.append(f"ردیف {i}: مختصات «{county}» خارج از محدودهٔ ایران است.")
            continue
        rates = {}
        filled = {}
        row_ok = True
        for j, (u, s, title) in enumerate(RATE_COLUMNS):
            v = _num(row[len(FIXED_HEADERS) + j])
            if v is None or v == "bad" or v <= 0:
                errors.append(f"ردیف {i} ({county})، ستون «{title}»: نرخ باید عدد مثبت باشد.")
                row_ok = False
                continue
            v = int(round(v))
            rates.setdefault(u, {})[s] = v
            if old:
                src = (old.get("filled") or {}).get(f"{u}.{s}")
                if src and old["rates"].get(u, {}).get(s) == v:
                    filled[f"{u}.{s}"] = src  # همچنان همان مقدار پرشده از همسایه
        if not row_ok:
            continue
        entry = {"county": old["county"] if old else county, "lat": float(lat), "lng": float(lng),
                 "rates": rates}
        if filled:
            entry["filled"] = filled
        new_provinces.setdefault(prov, []).append(entry)

    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings, "rates": None, "summary": ""}

    # ── مقایسه با نسخهٔ فعلی ──
    changed, suspicious, ratios = 0, [], []
    added = [f"{p} — {name}" for (p, nc), name in seen.items() if (p, nc) not in cur_index]
    removed = [f"{p} — {c['county']}" for (p, nc), c in cur_index.items() if (p, nc) not in seen]
    for prov, counties in new_provinces.items():
        for c in counties:
            old = cur_index.get((prov, ayani_calc.normalize_name(c["county"])))
            if not old:
                continue
            for u, s, title in RATE_COLUMNS:
                a, b = old["rates"].get(u, {}).get(s), c["rates"][u][s]
                if a != b:
                    changed += 1
                    if a:
                        r = b / a
                        ratios.append(r)
                        if r >= SUSPICIOUS_RATIO or r <= 1 / SUSPICIOUS_RATIO:
                            suspicious.append(f"{prov} — {c['county']} — {title}: {a:,} ← {b:,}")
    for s in suspicious[:15]:
        warnings.append(f"تغییر غیرعادی (بیش از {SUSPICIOUS_RATIO:g} برابر): {s}")
    if len(suspicious) > 15:
        warnings.append(f"... و {len(suspicious) - 15} تغییر غیرعادی دیگر")

    total_cells = sum(len(cs) for cs in new_provinces.values()) * len(RATE_COLUMNS)
    lines = [f"🏙 شهرستان‌ها: {sum(len(cs) for cs in new_provinces.values())} (در {len(new_provinces)} استان)",
             f"✏️ خانه‌های تغییرکرده: {changed} از {total_cells}"]
    if ratios:
        ratios.sort()
        med = ratios[len(ratios) // 2]
        lines.append(f"📈 میانهٔ تغییر نرخ‌ها: {(med - 1) * 100:+.1f}٪")
    if added:
        lines.append(f"➕ شهرستان جدید ({len(added)}): " + "، ".join(added[:10]) + (" ..." if len(added) > 10 else ""))
    if removed:
        lines.append(f"➖ شهرستان حذف‌شده ({len(removed)}): " + "، ".join(removed[:10])
                     + (" ..." if len(removed) > 10 else ""))
    new = {
        "version": int(current.get("version", 1)),
        "unit": current.get("unit", "ریال بر متر مربع"),
        "generated_at": datetime.date.today().isoformat(),
        "source": os.path.basename(path),
        "provinces": new_provinces,
    }
    return {"ok": True, "errors": [], "warnings": warnings, "rates": new, "summary": "\n".join(lines),
            "changed": changed, "added": len(added), "removed": len(removed)}


# ══════════════════════════════════════════════════════════════════
# اعمال / بازگردانی
# ══════════════════════════════════════════════════════════════════
def _write_atomic(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def apply_rates(new_rates: dict) -> str:
    """پشتیبان‌گیری از فایل فعلی، نوشتن نرخ‌های جدید و پاک کردن کش؛ خروجی: مسیر پشتیبان."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(backup_dir(), f"ayani_rates_{stamp}.json")
    shutil.copy2(ayani_calc.RATES_PATH, backup)
    _write_atomic(ayani_calc.RATES_PATH, new_rates)
    ayani_calc._RATES_CACHE = None
    ayani_calc.load_rates()  # بارگذاری فوری؛ اگر فایل خراب باشد همین‌جا خطا می‌دهد
    return backup


def list_backups() -> list:
    return sorted(glob.glob(os.path.join(backup_dir(), "ayani_rates_*.json")))


def rollback() -> str | None:
    """بازگرداندن آخرین نسخهٔ پشتیبان؛ خروجی: نام فایل پشتیبان یا None."""
    backups = list_backups()
    if not backups:
        return None
    last = backups[-1]
    with open(last, encoding="utf-8") as f:
        data = json.load(f)
    _write_atomic(ayani_calc.RATES_PATH, data)
    ayani_calc._RATES_CACHE = None
    ayani_calc.load_rates()
    os.replace(last, last + ".restored")
    return os.path.basename(last)


def describe_current() -> str:
    r = ayani_calc.load_rates()
    n = sum(len(cs) for cs in r["provinces"].values())
    return (f"📅 آخرین به‌روزرسانی: {r.get('generated_at', '-')}\n"
            f"📁 منبع: {r.get('source', '-')}\n"
            f"🏙 {n} شهرستان در {len(r['provinces'])} استان")
