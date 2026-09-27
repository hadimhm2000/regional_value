# -*- coding: utf-8 -*-
"""
محاسبهٔ ارزش عرصه + اعیانی برای بخش «ارزش منطقه‌ای».

این ماژول هیچ وابستگی به aiogram ندارد (منطق خالص) تا هم در هندلر ربات و
هم در تست/اسکریپت ادمین قابل استفاده باشد.

داده: ayani_rates.json (خروجی scripts/build_ayani_rates.py از اکسل کارفرما)
      ← نرخ هر متر مربع اعیانی به ریال، به تفکیک استان/شهرستان/کاربری/نوع سازه.

خلاصهٔ قواعد (طبق دستور کارفرما):
  عرصه:
    - مسکونی/تجاری/اداری → ارزش واحد همان کاربری از سامانه مالیاتی × متراژ
    - «سایر» → ارزش عرصه (مبنای مسکونی) × ضریب تعدیل (۰٫۷ / ۰٫۵ / ۰٫۴ / ۰٫۲ / ۰٫۱)
  اعیانی:
    نرخ = اکسل[شهرستان][کاربری][نوع سازه]
    - ساختمان ناتمام: متراژ × نرخ × درصد مرحلهٔ ساخت (۱۰/۳۰/۵۰/۸۰٪)؛ بقیهٔ بندها اعمال نمی‌شود.
    - ساختمان تکمیل:
        ۱) طبقه: مسکونی/اداری از طبقهٔ ششم به بالا هر طبقه +۱٫۵٪
                 تجاری هر طبقه بالاتر/پایین‌تر از همکف −۱۰٪ (حداکثر −۳۰٪)
        ۲) پارکینگ و انباری: متراژ × ۵۰٪ نرخ اکسل
        ۳) قدمت: هر سال ۲٪ (حداکثر ۲۰ سال = ۴۰٪) از جمع بندهای فوق کسر می‌شود.
  سهم مالکانه (ownership_share): ارزش عرصه × سهم عرصه + ارزش اعیانی × سهم اعیانی
  ارزش منطقه‌ای کل = ارزش (سهم) عرصه + ارزش (سهم) اعیانی — ملک فاقد اعیانی: فقط عرصه
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from decimal import Decimal, ROUND_HALF_UP

import ownership_share

logger = logging.getLogger(__name__)

RATES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ayani_rates.json")

# ══════════════════════════════════════════════════════════════════
# گزینه‌ها (برچسب‌ها دقیقاً طبق متن کارفرما)
# ══════════════════════════════════════════════════════════════════

# کاربری‌های اصلی عرصه → کلید ارزش در سامانه مالیاتی
LAND_MAIN_USES = ["مسکونی", "تجاری", "اداری"]

# زیرگزینه‌های «سایر» عرصه — ضریب تعدیل نسبت به ارزش عرصه
LAND_OTHER_OPTIONS = [
    {"title": "خدماتی، آموزشی، فرهنگی، بهداشتی درمانی، تفریحی ورزشی، گردشگری، هتلداری", "coef": Decimal("0.7")},
    {"title": "صنعتی کارگاهی، حمل و نقل، انبار و توقفگاه", "coef": Decimal("0.5")},
    {"title": "باغات، اراضی مزروعی، آبی، دامداری، دامپروری، پرورش طیور و آبزیان، پرورش گل و گیاه", "coef": Decimal("0.4")},
    {"title": "اراضی مزروعی و دیمی", "coef": Decimal("0.2")},
    {"title": "سایر", "coef": Decimal("0.1")},
]

# مبنای ارزش عرصه برای گزینه‌های «سایر» (به ترتیب اولویت)
LAND_OTHER_BASE_ORDER = ["مسکونی", "اداری", "تجاری"]

# کاربری اعیانی → کلید ستون در ayani_rates.json
BUILDING_USES = {
    "residential": "مسکونی",
    "commercial": "تجاری",
    "administrative": "اداری",
    "industrial": "صنعتی -کارگاهی، خدماتی، آموزشی، بهداشتی- درمانی، تفریحی ورزشی ،فرهنگی، هتلداری گردشگری حمل و نقل، انبار، پارکینگ عمومی (توقفگاه)",
    "agricultural": "کشاورزی دامداری دامپروری پرورش طیور و آبزیان پرورش گل و گیاه",
}
BUILDING_MAIN_KEYS = ["residential", "commercial", "administrative"]
BUILDING_OTHER_KEYS = ["industrial", "agricultural"]

STRUCTURES = {
    "concrete": "تمام بتون، اسکلت بتونی و فلزی، سوله",
    "other": "سایر",
}

CONSTRUCTION_STAGES = [
    {"key": "foundation", "title": "فونداسیون", "pct": Decimal("10")},
    {"key": "skeleton", "title": "اسکلت", "pct": Decimal("30")},
    {"key": "rough", "title": "سفت‌کاری", "pct": Decimal("50")},
    {"key": "finishing", "title": "نازک‌کاری", "pct": Decimal("80")},
]

PARKING_RATE_PCT = Decimal("50")
FLOOR_RES_THRESHOLD = 5            # از طبقهٔ ششم به بالا
FLOOR_RES_STEP_PCT = Decimal("1.5")
FLOOR_COM_STEP_PCT = Decimal("10")
FLOOR_COM_MAX_PCT = Decimal("30")
AGE_STEP_PCT = Decimal("2")
AGE_MAX_YEARS = 20                 # سقف ۲۰ سال = ۴۰٪


def _rial(x) -> int:
    return int(Decimal(x).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _pct_str(p: Decimal) -> str:
    p = p.normalize()
    return f"{p:f}".rstrip("0").rstrip(".") if "." in f"{p:f}" else f"{p:f}"


# ══════════════════════════════════════════════════════════════════
# دادهٔ نرخ‌ها و تعیین شهرستان
# ══════════════════════════════════════════════════════════════════
_RATES_CACHE = None


def load_rates() -> dict:
    global _RATES_CACHE
    if _RATES_CACHE is None:
        with open(RATES_PATH, encoding="utf-8") as f:
            _RATES_CACHE = json.load(f)
    return _RATES_CACHE


def normalize_name(s: str) -> str:
    """یکسان‌سازی نام برای تطبیق: ی/ک عربی، نیم‌فاصله، فاصله، پیشوندهای «شهرستان/شهر»."""
    s = str(s or "")
    s = s.replace("ي", "ی").replace("ك", "ک").replace("ۀ", "ه").replace("ة", "ه")
    s = s.replace("أ", "ا").replace("إ", "ا")
    s = re.sub(r"^\s*(شهرستان|شهر|بخش)\s+", "", s)
    s = s.replace("‌", "").replace("‏", "").replace("‎", "")
    return re.sub(r"\s+", "", s).strip()


def _province_counties(province: str) -> list:
    provinces = load_rates()["provinces"]
    if province in provinces:
        return provinces[province]
    n = normalize_name(province)
    for p, counties in provinces.items():
        if normalize_name(p) == n:
            return counties
    raise KeyError(f"استان «{province}» در جدول نرخ اعیانی نیست")


def _distance(lat1, lng1, lat2, lng2) -> float:
    dlat = lat1 - lat2
    dlng = (lng1 - lng2) * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dlat, dlng)


def resolve_county(province: str, lat: float = None, lng: float = None,
                   name_hints: list = None) -> dict:
    """
    شهرستان مبنای نرخ اعیانی را برمی‌گرداند — همیشه یک نتیجه (هرگز «یافت نشد»).

    ترتیب:
      ۱) تطبیق نام (شهرستان/شهر حاصل از نقشه) با شهرستان‌های همان استان
      ۲) نزدیک‌ترین مرکز شهرستانِ همان استان به نقطهٔ انتخاب‌شده (= همسایه)
      ۳) مرکز استان (اولین ردیفی که نامش با نام مرکز استان یکی است) یا اولین ردیف

    خروجی: {"county": نام شهرستان در جدول, "rates": {...}, "method": "name|nearest|default",
             "hint": نام تشخیص‌داده‌شده از نقشه (در صورت وجود)}
    """
    counties = _province_counties(province)
    by_norm = {normalize_name(c["county"]): c for c in counties}

    hints = [h for h in (name_hints or []) if h]
    for h in hints:
        c = by_norm.get(normalize_name(h))
        if c:
            return {"county": c["county"], "rates": c["rates"], "method": "name", "hint": h}

    if lat is not None and lng is not None:
        with_coords = [c for c in counties if c.get("lat") is not None]
        if with_coords:
            c = min(with_coords, key=lambda c: _distance(lat, lng, c["lat"], c["lng"]))
            return {"county": c["county"], "rates": c["rates"], "method": "nearest",
                    "hint": hints[0] if hints else None}

    c = by_norm.get(normalize_name(PROVINCE_CAPITALS.get(province, ""))) or counties[0]
    return {"county": c["county"], "rates": c["rates"], "method": "default",
            "hint": hints[0] if hints else None}


PROVINCE_CAPITALS = {
    "آذربایجان شرقی": "تبریز", "آذربایجان غربی": "ارومیه", "اردبیل": "اردبیل",
    "اصفهان": "اصفهان", "البرز": "کرج", "ایلام": "ایلام", "بوشهر": "بوشهر",
    "تهران": "تهران", "چهارمحال و بختیاری": "شهرکرد", "خراسان جنوبی": "بیرجند",
    "خراسان رضوی": "مشهد", "خراسان شمالی": "بجنورد", "خوزستان": "اهواز",
    "زنجان": "زنجان", "سمنان": "سمنان", "سیستان و بلوچستان": "زاهدان",
    "فارس": "شیراز", "قزوین": "قزوین", "قم": "قم", "کردستان": "سنندج",
    "کرمان": "کرمان", "کرمانشاه": "کرمانشاه", "کهگیلویه و بویراحمد": "یاسوج",
    "گلستان": "گرگان", "گیلان": "رشت", "لرستان": "خرم‌آباد", "مازندران": "ساری",
    "مرکزی": "اراک", "هرمزگان": "بندرعباس", "همدان": "همدان", "یزد": "یزد",
}


def detect_location_names(lat: float, lng: float) -> list:
    """
    نام شهرستان/شهر نقطهٔ انتخاب‌شده را از سرویس آدرس‌خوانی معکوس نشان
    می‌گیرد (بدون خطا — در صورت شکست لیست خالی برمی‌گرداند).
    """
    try:
        import requests
        from geocode_and_query import NESHAN_API_KEY, NESHAN_REVERSE_URL
        if not NESHAN_API_KEY:
            return []
        resp = requests.get(
            NESHAN_REVERSE_URL, params={"lat": lat, "lng": lng},
            headers={"Api-Key": NESHAN_API_KEY}, timeout=10,
        )
        resp.raise_for_status()
        data = resp.json() or {}
        if isinstance(data.get("address"), dict):
            data = {**data, **data["address"]}
        names = [data.get(k) for k in ("county", "city", "district")]
        return [n for n in names if isinstance(n, str) and n.strip()]
    except Exception as e:
        logger.warning(f"[AYANI] تشخیص شهرستان از نقشه ناموفق بود ({lat}, {lng}): {e}")
        return []


# ══════════════════════════════════════════════════════════════════
# محاسبات
# ══════════════════════════════════════════════════════════════════
def compute_land_value(area, land_use: str, tax_values: dict, other_index: int = None) -> dict:
    """
    ارزش عرصه.
      land_use: «مسکونی»/«تجاری»/«اداری» یا «سایر»
      tax_values: {"مسکونی": int|None, "تجاری": ..., "اداری": ...} از سامانه مالیاتی
      other_index: شمارهٔ زیرگزینهٔ «سایر» (۰ تا ۴)
    """
    area = Decimal(str(area))
    if land_use != "سایر":
        unit = tax_values.get(land_use)
        if unit is None:
            return {"ok": False, "land_use": land_use}
        value = _rial(area * unit)
        return {
            "ok": True, "land_use": land_use, "land_use_title": land_use,
            "area": area, "unit_value": int(unit), "base_use": land_use,
            "coef": Decimal("1"), "base_total": value, "value": value,
        }

    opt = LAND_OTHER_OPTIONS[other_index]
    base_use = next((u for u in LAND_OTHER_BASE_ORDER if tax_values.get(u) is not None), None)
    if base_use is None:
        return {"ok": False, "land_use": land_use}
    unit = int(tax_values[base_use])
    base_total = _rial(area * unit)
    value = _rial(Decimal(base_total) * opt["coef"])
    return {
        "ok": True, "land_use": "سایر", "land_use_title": opt["title"],
        "area": area, "unit_value": unit, "base_use": base_use,
        "coef": opt["coef"], "base_total": base_total, "value": value,
    }


def floor_adjust_pct(use_key: str, floor: int) -> Decimal:
    """درصد تعدیل نرخ بر اساس طبقه (مثبت = افزایش، منفی = کسر)."""
    if floor is None:
        return Decimal("0")
    if use_key in ("residential", "administrative"):
        extra = max(0, int(floor) - FLOOR_RES_THRESHOLD)
        return FLOOR_RES_STEP_PCT * extra
    if use_key == "commercial":
        return -min(FLOOR_COM_MAX_PCT, FLOOR_COM_STEP_PCT * abs(int(floor)))
    return Decimal("0")


def compute_building_value(rates: dict, use_key: str, structure: str, area,
                           complete: bool, stage_key: str = None,
                           parking_area=None, floor: int = None, age: int = None) -> dict:
    """ارزش اعیانی با تمام بندها؛ خروجی شامل جزئیات هر مرحله برای PDF."""
    rate = int(rates[use_key][structure])
    area = Decimal(str(area))
    res = {
        "use_key": use_key, "use_title": BUILDING_USES[use_key],
        "structure": structure, "structure_title": STRUCTURES[structure],
        "rate": rate, "area": area, "complete": complete,
    }

    if not complete:
        stage = next(s for s in CONSTRUCTION_STAGES if s["key"] == stage_key)
        full = _rial(area * rate)
        value = _rial(Decimal(full) * stage["pct"] / 100)
        res.update({"stage_title": stage["title"], "stage_pct": stage["pct"],
                    "full_value": full, "value": value})
        return res

    floor_pct = floor_adjust_pct(use_key, floor)
    adj_rate = Decimal(rate) * (100 + floor_pct) / 100
    main_value = _rial(area * adj_rate)

    p_area = Decimal(str(parking_area or 0))
    parking_rate = Decimal(rate) * PARKING_RATE_PCT / 100
    parking_value = _rial(p_area * parking_rate)

    subtotal = main_value + parking_value
    age = max(0, int(age or 0))
    age_pct = AGE_STEP_PCT * min(age, AGE_MAX_YEARS)
    age_deduction = _rial(Decimal(subtotal) * age_pct / 100)
    value = subtotal - age_deduction

    res.update({
        "floor": floor, "floor_pct": floor_pct, "adjusted_rate": _rial(adj_rate),
        "main_value": main_value,
        "parking_area": p_area, "parking_rate": _rial(parking_rate), "parking_value": parking_value,
        "subtotal": subtotal, "age": age, "age_pct": age_pct, "age_deduction": age_deduction,
        "value": value,
    })
    return res


def compute_all(land: dict, building: dict = None, land_share=1, building_share=1) -> dict:
    """
    جمع نهایی با اعمال «سهم مالکانه» (ownership_share).
      building=None       → ملک فاقد اعیانی است (ارزش اعیانی = ۰)
      land_share          → سهم مالک از عرصه (کسر ۰ < s ≤ ۱؛ Fraction یا رشتهٔ «a/b»)
      building_share      → سهم مالک از اعیانی
    land["value"] / building["value"] همچنان ارزش کامل (ششدانگ) باقی می‌مانند؛
    ارزش سهم مالک در land_value / building_value و جمع آن در total است.
    """
    ls = ownership_share.frac_from_str(land_share)
    bs = ownership_share.frac_from_str(building_share)
    for s in (ls, bs):
        if not (0 < s <= 1):
            raise ValueError("سهم مالکانه باید بزرگ‌تر از صفر و حداکثر ششدانگ باشد")
    land_value = ownership_share.apply_share(land["value"], ls)
    building_full = building["value"] if building else 0
    building_value = ownership_share.apply_share(building_full, bs) if building else 0
    return {
        "land": land, "building": building,
        "land_share": ownership_share.frac_to_str(ls),
        "building_share": ownership_share.frac_to_str(bs) if building else None,
        "land_value": land_value, "building_value": building_value,
        "full_total": land["value"] + building_full,
        "total": land_value + building_value,
    }


def _share_step(title: str, full_value: int, share: str, owned_value: int):
    f = ownership_share.frac_from_str(share)
    return (title,
            f"{fmt(full_value)} × {f.numerator}/{f.denominator} (سهم مالکانه)",
            owned_value)


# ══════════════════════════════════════════════════════════════════
# متن توضیح محاسبه (برای PDF صفحهٔ دوم و پیام متنی)
# ══════════════════════════════════════════════════════════════════
def fmt(n) -> str:
    """۱۲٬۳۴۵ — جداکنندهٔ هزارگان."""
    if isinstance(n, Decimal):
        n = n.normalize()
        if n == n.to_integral():
            n = int(n)
    return f"{n:,}" if isinstance(n, int) else f"{n}"


def explain_steps(result: dict) -> list:
    """
    لیستی از (عنوان، شرح/فرمول، مبلغ به ریال یا None) برای نمایش نحوهٔ محاسبه.
    """
    land, b = result["land"], result.get("building")
    land_share = result.get("land_share") or "1"
    bld_share = result.get("building_share") or "1"
    land_owned = result.get("land_value", land["value"])
    bld_owned = result.get("building_value", b["value"] if b else 0)
    steps = []

    # ── عرصه
    if land["land_use"] == "سایر":
        steps.append(("ارزش پایهٔ عرصه",
                      f"{fmt(land['area'])} متر مربع × {fmt(land['unit_value'])} ریال (ارزش معاملاتی {land['base_use']})",
                      land["base_total"]))
        steps.append(("ضریب تعدیل کاربری عرصه",
                      f"{fmt(land['base_total'])} × {fmt(land['coef'])}  ({land['land_use_title']})",
                      land["value"]))
    else:
        steps.append(("ارزش عرصه",
                      f"{fmt(land['area'])} متر مربع × {fmt(land['unit_value'])} ریال (ارزش معاملاتی {land['land_use']})",
                      land["value"]))
    if ownership_share.frac_from_str(land_share) != 1:
        steps.append(_share_step("سهم مالکانه از عرصه", land["value"], land_share, land_owned))

    # ── اعیانی
    if b is None:
        steps.append(("ارزش منطقه‌ای کل", "ارزش عرصه (ملک فاقد اعیانی است)", result["total"]))
        return steps

    if not b["complete"]:
        steps.append(("ارزش کامل اعیانی",
                      f"{fmt(b['area'])} متر مربع × {fmt(b['rate'])} ریال",
                      b["full_value"]))
        steps.append((f"مرحلهٔ ساخت: {b['stage_title']}",
                      f"{fmt(b['full_value'])} × {_pct_str(b['stage_pct'])}٪",
                      b["value"]))
    else:
        if b["floor_pct"] != 0:
            sign = "+" if b["floor_pct"] > 0 else "−"
            fl = b["floor"]
            fl_txt = f"زیرزمین {abs(fl)}" if fl < 0 else f"طبقهٔ {fl}"
            steps.append((f"تعدیل طبقه ({fl_txt})",
                          f"{fmt(b['rate'])} × (۱۰۰٪ {sign} {_pct_str(abs(b['floor_pct']))}٪) = {fmt(b['adjusted_rate'])} ریال",
                          None))
        steps.append(("ارزش اعیانی واحد",
                      f"{fmt(b['area'])} متر مربع × {fmt(b['adjusted_rate'])} ریال",
                      b["main_value"]))
        if b["parking_area"] > 0:
            steps.append(("پارکینگ و انباری",
                          f"{fmt(b['parking_area'])} متر مربع × {fmt(b['parking_rate'])} ریال (۵۰٪ نرخ)",
                          b["parking_value"]))
        if b["age_deduction"] > 0:
            steps.append((f"کسر قدمت ({b['age']} سال)",
                          f"{fmt(b['subtotal'])} × {_pct_str(b['age_pct'])}٪",
                          -b["age_deduction"]))
        steps.append(("ارزش اعیانی", "", b["value"]))
    if ownership_share.frac_from_str(bld_share) != 1:
        steps.append(_share_step("سهم مالکانه از اعیانی", b["value"], bld_share, bld_owned))

    shared = ownership_share.frac_from_str(land_share) != 1 or ownership_share.frac_from_str(bld_share) != 1
    steps.append(("ارزش منطقه‌ای کل",
                  "سهم عرصه + سهم اعیانی" if shared else "ارزش عرصه + ارزش اعیانی",
                  result["total"]))
    return steps
