# -*- coding: utf-8 -*-
"""
import_annual_index.py — ساخت assets/cpi_annual_1395.json (شاخص سالانهٔ مهریه، پایهٔ ۱۳۹۵=۱۰۰)
از جدول سالانهٔ رسمی بانک مرکزی (۱۳۱۵ به بعد) یا جدول هر ماشین‌حساب معتبر (مثل دادحساب).

چرا لازم است: سری ماهانهٔ ربات از فروردین ۱۳۷۵ شروع می‌شود، پس مهریهٔ عقدهای قبل از
۱۳۷۵ بدون جدول سالانه قابل محاسبه نیست. بانک مرکزی شاخص سالانه را از ۱۳۱۵ اعلام کرده
(پیوست نامهٔ سالانه به قوه قضاییه؛ مثلاً نامهٔ ۱۳۹۵/۲/۶ با پایهٔ ۱۳۹۰=۱۰۰، و نامهٔ
۱۴۰۱/۳/۱۷ که عدد ۱۴۰۰ را ۴۳۷٫۰۳۲ اعلام کرد).

ورودی: یک فایل .csv / .txt / .xlsx / .json با دو ستون «سال، شاخص» (هر سطر یک سال).
  • ارقام فارسی و ممیز «/» یا «٫» پذیرفته می‌شود (۴۳۷/۰۳۲ = 437.032)
  • سطرهای عنوان یا نامربوط خودبه‌خود رد می‌شوند
  • json: {"1315": 0.0123, ...} یا {"annual": {...}}

تبدیل پایه: اگر جدول با پایهٔ دیگری است (مثلاً ۱۳۹۰=۱۰۰ یا ۱۴۰۰=۱۰۰)، اسکریپت
ضریب تبدیل را از سال‌های مشترک با سری ماهانه (۱۳۷۵ به بعد) خودش پیدا می‌کند و همه را
به ۱۳۹۵=۱۰۰ می‌برد. با --no-rescale مقادیر بدون تغییر ذخیره می‌شوند.

اجرا (در پوشهٔ اصلی پروژه):
    python tools/import_annual_index.py annual.csv --source "نامهٔ بانک مرکزی ۱۴۰۵"
    python tools/import_annual_index.py annual.xlsx --dry-run      ← فقط گزارش، بدون ذخیره

گزارش خروجی برای هر سال مشترک، اختلاف جدول ورودی با میانگین ۱۲ ماه را نشان می‌دهد؛
اختلاف بیش از ۱٪ یعنی جدول ورودی یا پایه‌اش را دوباره بررسی کنید.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import damages_calc as dc  # noqa: E402

OUT_FILE = dc.ANNUAL_SEED_FILE
_NUM = re.compile(r"\d+(?:[./٫]\d+)?")


def _to_float(text: str) -> float:
    return float(dc.normalize_digits(str(text)).replace("٫", ".").replace("/", ".").replace(",", ""))


def parse_rows(lines) -> dict[int, float]:
    """هر سطر: اولین عدد ۴رقمی ۱۳xx/۱۴xx = سال، عدد بعدی = شاخص."""
    out: dict[int, float] = {}
    for line in lines:
        text = dc.normalize_digits(" ".join(str(c) for c in line if c is not None)
                                   if isinstance(line, (list, tuple)) else str(line))
        nums = _NUM.findall(text.replace("٬", ""))
        if len(nums) < 2:
            continue
        year = nums[0]
        if not (len(year) == 4 and year.isdigit() and 1300 <= int(year) <= 1500):
            continue
        out[int(year)] = _to_float(nums[1])
    return out


def read_input(path: str) -> dict[int, float]:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        raw = raw.get("annual", raw)
        return {int(dc.normalize_digits(str(k))): _to_float(v) for k, v in raw.items()}
    if ext in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        rows = []
        for ws in wb.worksheets:
            rows.extend(ws.iter_rows(values_only=True))
        return parse_rows(rows)
    with open(path, encoding="utf-8-sig") as f:
        return parse_rows(f.read().splitlines())


def monthly_averages() -> dict[int, float]:
    """میانگین ۱۲ ماه هر سال کامل از سری ماهانهٔ ۱۳۹۵=۱۰۰ (بدون جدول سالانه)."""
    series = dc.judicial_series()[0]
    out = {}
    for year in sorted({int(k[:4]) for k in series}):
        vals = [series.get(dc.month_key(year, m)) for m in range(1, 13)]
        if all(v is not None for v in vals):
            out[year] = sum(vals) / 12
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ساخت جدول شاخص سالانهٔ مهریه (۱۳۹۵=۱۰۰)")
    ap.add_argument("input", help="فایل csv/txt/xlsx/json با ستون‌های سال و شاخص")
    ap.add_argument("--source", default="", help="منبع جدول (برای ثبت در فایل)")
    ap.add_argument("--no-rescale", action="store_true", help="جدول از قبل پایهٔ ۱۳۹۵=۱۰۰ است")
    ap.add_argument("--dry-run", action="store_true", help="فقط گزارش؛ فایل نوشته نمی‌شود")
    ap.add_argument("--max-diff", type=float, default=1.0, help="حداکثر اختلاف مجاز (درصد) با میانگین ماهانه")
    args = ap.parse_args(argv)

    table = read_input(args.input)
    if not table:
        print("❌ هیچ سطر «سال، شاخص» در فایل پیدا نشد.")
        return 1
    print(f"خوانده شد: {len(table)} سال، از {min(table)} تا {max(table)}")

    avgs = monthly_averages()
    common = sorted(set(table) & set(avgs))
    factor = 1.0
    if not args.no_rescale:
        if not common:
            print("❌ هیچ سال مشترکی با سری ماهانه (۱۳۷۵ به بعد) نیست؛ ضریب پایه را نمی‌توان یافت. "
                  "اگر جدول پایهٔ ۱۳۹۵ است با --no-rescale اجرا کنید.")
            return 1
        factor = statistics.median(avgs[y] / table[y] for y in common)
        print(f"ضریب تبدیل به ۱۳۹۵=۱۰۰: {factor:.6g}  (از {len(common)} سال مشترک)")

    scaled = {y: v * factor for y, v in table.items()}
    bad = []
    if common:
        print("\nسال   جدول(۱۳۹۵)   میانگین‌ماهانه   اختلاف٪")
        for y in common:
            diff = (scaled[y] / avgs[y] - 1) * 100
            flag = "  ⚠️" if abs(diff) > args.max_diff else ""
            print(f"{y}  {scaled[y]:12.4f}  {avgs[y]:14.4f}  {diff:+7.3f}{flag}")
            if flag:
                bad.append(y)
    if bad:
        print(f"\n⚠️ اختلاف بیش از {args.max_diff}٪ در سال‌های {bad} — جدول یا پایهٔ آن را بررسی کنید.")

    if args.dry_run:
        print("\n(--dry-run: چیزی ذخیره نشد)")
        return 2 if bad else 0
    if bad:
        print("ذخیره انجام نشد. برای ذخیرهٔ اجباری --max-diff را بزرگ‌تر کنید.")
        return 2

    out = {
        "base": dc.CPI_BASE,
        "description": ("شاخص سالانهٔ بهای کالاها و خدمات مصرفی برای محاسبهٔ مهریه به نرخ روز "
                        "(تبصرهٔ مادهٔ ۱۰۸۲ ق.م.)، پایهٔ ۱۳۹۵=۱۰۰. اولویت: ثبت دستی مدیر "
                        "(/cpi_year) > این فایل > میانگین ۱۲ ماه سری ماهانه."),
        "source": args.source,
        "rescale_factor": factor,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "annual": {str(y): round(v, 6) for y, v in sorted(scaled.items())},
    }
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n✅ {len(scaled)} سال در {os.path.relpath(OUT_FILE, ROOT)} ذخیره شد.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
