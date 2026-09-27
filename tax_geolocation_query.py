# -*- coding: utf-8 -*-
"""
استعلام ارزش منطقه‌ای ملک از سایت tax.gov.ir بر اساس مختصات جغرافیایی (lat/lng)

معماری کشف‌شده:
- سایت ASP.NET WebForms با Postback سنتی است (بدون API جدا)
- GET به /action/do/GetAddressGeoLocation/{province_id} صفحه اولیه + __VIEWSTATE و __EVENTVALIDATION را می‌دهد
- POST با همان مقادیر + TextboxLatitude/TextboxLongitude + ButtonSearchGeolocation=استعلام
  نتیجه (بلوک، ردیف، ارزش معاملاتی مسکونی/اداری/تجاری) را در همان HTML برمی‌گرداند

نیازمندی‌ها:
    pip install requests beautifulsoup4 --break-system-packages
"""

import warnings
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://tax.gov.ir"

# جدول کامل ۳۱ استان با شناسه‌شون (استخراج‌شده از صفحه لیست سایت مالیاتی)
# نام‌ها با حروف فارسی استاندارد (ی/ک) نرمالایز شدن، نه عربی (ي/ك)
PROVINCE_IDS = {
    "مازندران": 12,
    "آذربایجان شرقی": 13,
    "آذربایجان غربی": 14,
    "کرمانشاه": 15,
    "خوزستان": 16,
    "فارس": 17,
    "کرمان": 18,
    "خراسان رضوی": 19,
    "اصفهان": 20,
    "خراسان شمالی": 21,
    "کردستان": 22,
    "هرمزگان": 23,
    "همدان": 24,
    "چهارمحال و بختیاری": 25,
    "لرستان": 26,
    "ایلام": 27,
    "کهگیلویه و بویراحمد": 28,
    "زنجان": 29,
    "تهران": 30,
    "خراسان جنوبی": 31,
    "مرکزی": 44,
    "البرز": 49,
    "گیلان": 55,
    "اردبیل": 61,
    "قم": 62,
    "قزوین": 63,
    "گلستان": 64,
    "سیستان و بلوچستان": 66,
    "یزد": 77,
    "بوشهر": 88,
    "سمنان": 99,
}

LAND_USE_MAP = {
    "مسکونی": "ارزش معاملاتی مسکونی",
    "تجاری": "ارزش معاملاتی تجاری",
    "اداری": "ارزش معاملاتی اداری",
}

# فیلدهای موردانتظار در هر استعلام — دقیقاً به ترتیبی که باید در PDF نمایش داده شوند
EXPECTED_FIELDS = [
    "جستجوی بلوک و ردیف و اداره مالیاتی مرتبط",
    "سال",
    "اطلاعات مکان انتخابی",
    "اداره کل امور مالیاتی",
    "واحد مرتبط مالیاتی سنیم املاک",
    "واحد مرتبط مالیاتی سنیم حقوقی",
    "واحد مرتبط مالیاتی سنیم حقیقی",
    "واحد مرتبط مالیاتی حقوقی",
    "واحد مرتبط مالیاتی حقیقی",
    "واحد مرتبط مالیاتی ارث",
    "شماره بلوک بر اساس دفترچه ارزش معاملاتی ملک",
    "شماره ردیف بر اساس دفترچه ارزش معاملاتی ملک",
    "ارزش معاملاتی مسکونی",
    "ارزش معاملاتی اداری",
    "ارزش معاملاتی تجاری",
]

# فیلدهای داده‌ای (نه عنوانی) — برای نمایش در جدول PDF
DATA_FIELDS = [
    "اداره کل امور مالیاتی",
    "واحد مرتبط مالیاتی سنیم املاک",
    "واحد مرتبط مالیاتی سنیم حقوقی",
    "واحد مرتبط مالیاتی سنیم حقیقی",
    "واحد مرتبط مالیاتی حقوقی",
    "واحد مرتبط مالیاتی حقیقی",
    "واحد مرتبط مالیاتی ارث",
    "شماره بلوک بر اساس دفترچه ارزش معاملاتی ملک",
    "شماره ردیف بر اساس دفترچه ارزش معاملاتی ملک",
    "ارزش معاملاتی مسکونی",
    "ارزش معاملاتی اداری",
    "ارزش معاملاتی تجاری",
]


def normalize_persian(text: str) -> str:
    """حروف عربی رایج (ي، ك) را به معادل فارسی (ی، ک) تبدیل می‌کند."""
    return (
        text.replace("ي", "ی")
        .replace("ك", "ک")
        .strip()
    )


def get_province_id(province_name: str) -> int:
    """نام استان (حتی با نویسه‌های عربی یا فاصله اضافه) را به شناسه عددی آن تبدیل می‌کند."""
    normalized = normalize_persian(province_name)
    if normalized in PROVINCE_IDS:
        return PROVINCE_IDS[normalized]
    raise ValueError(f"استان '{province_name}' در جدول پیدا نشد. لیست موجود: {list(PROVINCE_IDS.keys())}")


def get_province_list() -> list:
    """لیست نام استان‌ها را برمی‌گرداند (برای ساخت کیبورد)."""
    return list(PROVINCE_IDS.keys())


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}


def get_form_state(session: requests.Session, province_id: int):
    """صفحه اولیه استان را می‌گیرد و فیلدهای مخفی ASP.NET را استخراج می‌کند."""
    url = f"{BASE_URL}/action/do/GetAddressGeoLocation/{province_id}"
    resp = session.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    def get_val(field_id):
        el = soup.find("input", {"id": field_id})
        return el["value"] if el and el.has_attr("value") else ""

    return {
        "__VIEWSTATE": get_val("__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": get_val("__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION": get_val("__EVENTVALIDATION"),
        "url": url,
    }


def query_location_value(session: requests.Session, province_id: int, lat: float, lng: float, tax_year: str = "1405"):
    """
    با مختصات مشخص، استعلام ارزش منطقه‌ای را انجام می‌دهد.
    خروجی یک دیکشنری ساختاریافته شامل:
      - "سال": سال مالی استعلام (پیش‌فرض 1405)
      - "فیلدهای_ساختاریافته": دیکشنری با کلیدهای دقیق موردنیاز
      - "همه_فیلدهای_خام_صفحه": تمام جفت برچسب/مقدار خام یافت‌شده
      - "فیلدهای_پیدا_نشده": لیست فیلدهای موردانتظاری که پیدا نشدن
    """
    state = get_form_state(session, province_id)

    payload = {
        "__EVENTTARGET": "",
        "__EVENTARGUMENT": "",
        "__LASTFOCUS": "",
        "__VIEWSTATE": state["__VIEWSTATE"],
        "__VIEWSTATEGENERATOR": state["__VIEWSTATEGENERATOR"],
        "__EVENTVALIDATION": state["__EVENTVALIDATION"],
        "TextboxLatitude": f"{lat:.6f}",
        "TextboxLongitude": f"{lng:.6f}",
        "ButtonSearchGeolocation": "استعلام",
    }

    resp = session.post(
        state["url"],
        data=payload,
        headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
        timeout=20,
    )
    resp.raise_for_status()

    return build_full_result(resp.text, tax_year=tax_year)


def parse_result(html: str) -> dict:
    """از HTML نتیجه، همه‌ی جفت‌های برچسب/مقدار (.geohead / .geovalue) را استخراج می‌کند."""
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("div.gridView div.row")

    result = {}
    for row in rows:
        head = row.select_one(".geohead")
        value = row.select_one(".geovalue")
        if head and value:
            key = head.get_text(strip=True)
            val = value.get_text(strip=True)
            result[key] = val

    # فال‌بک: اگر div.row پیدا نشد
    if not result:
        all_heads = soup.select(".geohead")
        all_vals = soup.select(".geovalue")
        for h, v in zip(all_heads, all_vals):
            key = h.get_text(strip=True)
            val = v.get_text(strip=True)
            if key:
                result[key] = val

    return result


def build_full_result(html: str, tax_year: str = "1405") -> dict:
    """
    نتیجه کامل و ساختاریافته استعلام را می‌سازد.
    فیلدهای EXPECTED_FIELDS را (با تطبیق fuzzy/substring) از داده خام پیدا می‌کند.
    """
    raw = parse_result(html)
    normalized_map = {normalize_persian(k): (k, v) for k, v in raw.items()}

    structured = {}
    missing = []
    for field in EXPECTED_FIELDS:
        if field == "سال":
            structured[field] = tax_year
            continue
        norm_field = normalize_persian(field)
        found_value = None
        for nk, (orig_k, v) in normalized_map.items():
            if norm_field in nk or nk in norm_field:
                found_value = v
                break
        structured[field] = found_value
        if found_value is None:
            missing.append(field)

    if missing:
        warnings.warn(
            "فیلدهای زیر در پاسخ سایت مالیاتی پیدا نشدن: " + ", ".join(missing)
        )

    return {
        "سال": tax_year,
        "فیلدهای_ساختاریافته": structured,
        "همه_فیلدهای_خام_صفحه": raw,
        "فیلدهای_پیدا_نشده": missing,
    }


# ═══ توابع کمکی برای هندلر و PDF ═══

def find_land_use_value(tax_result: dict, land_use: str) -> int | None:
    """
    ارزش معاملاتی را بر اساس کاربری زمین از نتیجه ساختاریافته استخراج می‌کند.
    مقدار را به عدد صحیح (ریال) برمی‌گرداند یا None اگر پیدا نشد.
    """
    target_key = LAND_USE_MAP.get(land_use)
    if not target_key:
        return None

    structured = tax_result.get("فیلدهای_ساختاریافته", {})
    val = structured.get(target_key)
    if val:
        cleaned = str(val).replace(",", "").replace(" ریال", "").replace("،", "").strip()
        try:
            return int(cleaned)
        except (ValueError, TypeError):
            pass

    # فال‌بک: جستجو در فیلدهای خام
    raw = tax_result.get("همه_فیلدهای_خام_صفحه", {})
    for key, v in raw.items():
        if target_key in normalize_persian(key):
            cleaned = str(v).replace(",", "").replace(" ریال", "").replace("،", "").strip()
            try:
                return int(cleaned)
            except (ValueError, TypeError):
                return None
    return None


def extract_all_land_use_values(tax_result: dict) -> dict:
    """
    هر ۳ ارزش معاملاتی (مسکونی، تجاری، اداری) را استخراج می‌کند.
    خروجی: {"مسکونی": 12345, "تجاری": 67890, "اداری": 11111}  (None اگر پیدا نشد)
    """
    result = {}
    for lu_key in LAND_USE_MAP:
        result[lu_key] = find_land_use_value(tax_result, lu_key)
    return result
