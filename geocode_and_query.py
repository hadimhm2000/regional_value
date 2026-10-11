# -*- coding: utf-8 -*-
"""
پایپ‌لاین کامل: آدرس متنی فارسی -> Geocoding با نشان -> استعلام ارزش منطقه‌ای از tax.gov.ir

نیازمندی‌ها:
    pip install requests beautifulsoup4 --break-system-packages

نکته امنیتی: کلید API نشان از متغیر محیطی NESHAN_API_KEY خوانده می‌شود.
"""

import json
import math
import os
import logging
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from tax_geolocation_query import (query_location_value, get_province_id,
                                   extract_all_land_use_values, normalize_persian)

logger = logging.getLogger(__name__)

NESHAN_API_KEY = os.environ.get("NESHAN_API_KEY", "")
NESHAN_GEOCODE_URL = "https://api.neshan.org/geocoding/v1/plus"
NESHAN_REVERSE_URL = "https://api.neshan.org/v5/reverse"


def geocode_address(address: str, city: str = None, province: str = None) -> dict:
    """
    آدرس متنی فارسی را با سرویس نشان به (lat, lng, province, city) تبدیل می‌کند.
    """
    if not NESHAN_API_KEY:
        raise RuntimeError("NESHAN_API_KEY تنظیم نشده است. لطفاً در فایل .env مقداردهی کنید.")

    payload = {"address": address}
    if city:
        payload["city"] = city
    if province:
        payload["province"] = province

    resp = requests.get(
        NESHAN_GEOCODE_URL,
        params={"json": json.dumps(payload, ensure_ascii=False)},
        headers={"Api-Key": NESHAN_API_KEY},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    items = data.get("items", [])
    if not items:
        raise ValueError(f"Geocoding نتیجه‌ای برنگرداند. آدرس را بررسی کنید.")

    best = items[0]
    loc = best.get("location", {})
    if "latitude" not in loc or "longitude" not in loc:
        raise ValueError(f"فرمت location ناشناخته: {loc}")

    return {
        "lat": loc["latitude"],
        "lng": loc["longitude"],
        "province": best.get("province") or province,
        "city": best.get("city") or city,
        "neighbourhood": best.get("neighbourhood"),
    }


def reverse_geocode(lat: float, lng: float) -> str:
    """
    مختصات را به آدرس دقیق (نشان) تبدیل می‌کند — همین مقدار در فیلد
    «آدرس اعلام‌شده» در PDF درج می‌شود، پس هرچه این تابع موفق‌تر باشد،
    آدرس گزارش دقیق‌تر است.

    ⭐ اصلاحیه: قبلاً فقط کلید formatted_address چک می‌شد و اگر پاسخ نشان
    ساختار دیگری داشت (یا کلید نبود)، بی‌سروصدا None برمی‌گشت و در PDF
    به‌جای آدرس واقعی فقط مختصات خام چاپ می‌شد. حالا چند کلید محتمل را
    امتحان می‌کند، در نبود همه‌شان از قطعات آدرس (استان/شهر/محله/خیابان)
    یک آدرس قابل‌خواندن می‌سازد، و در صورت شکست کامل، کل پاسخ خام را در
    لاگ ثبت می‌کند تا اگر نشان ساختار پاسخش را عوض کرد، سریع قابل تشخیص باشد.
    """
    if not NESHAN_API_KEY:
        logger.warning("[reverse_geocode] NESHAN_API_KEY تنظیم نشده — آدرس‌خوانی معکوس ممکن نیست.")
        return None
    try:
        resp = requests.get(
            NESHAN_REVERSE_URL,
            params={"lat": lat, "lng": lng},
            headers={"Api-Key": NESHAN_API_KEY},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        # ۱) رایج‌ترین کلید مستقیم آدرس فرمت‌شده
        for key in ("formatted_address", "formattedAddress", "address"):
            val = data.get(key)
            if val:
                return val

        # ۲) اگر پاسخ داخل یک ساختار تودرتو باشد (مثلاً data["address"] خودش دیکشنری)
        nested = data.get("address")
        if isinstance(nested, dict):
            for key in ("formatted_address", "formattedAddress"):
                val = nested.get(key)
                if val:
                    return val
            parts = [nested.get(k) for k in ("route_name", "neighbourhood", "city", "state") if nested.get(k)]
            if parts:
                return "، ".join(parts)

        # ۳) ساخت آدرس تقریبی از قطعات پراکنده در سطح ریشهٔ پاسخ
        parts = [data.get(k) for k in ("route_name", "neighbourhood", "district", "city", "state") if data.get(k)]
        if parts:
            return "، ".join(parts)

        # هیچ‌کدام جواب نداد — پاسخ خام را لاگ کن تا قابل بررسی باشد
        logger.warning(f"[reverse_geocode] ساختار پاسخ نشان ناشناخته بود: {data}")
        return None
    except Exception as e:
        logger.warning(f"[reverse_geocode] خطا در آدرس‌خوانی معکوس ({lat}, {lng}): {e}")
        return None


# ═══ جستجوی نزدیک‌ترین بلوک دارای ارزش (وقتی نقطهٔ انتخابی در سامانه ارزش ندارد) ═══
# شعاع حلقه‌ها (متر) از نزدیک به دور؛ نقاط هر حلقه هم‌زمان استعلام می‌شوند
NEAREST_RINGS_M = [50, 100, 175, 250, 350, 500, 700, 900, 1200, 1600, 2100, 2800, 3600, 4500]
NEAREST_TIME_BUDGET = float(os.environ.get("NEAREST_SEARCH_SECONDS", "100"))
NEAREST_WORKERS = int(os.environ.get("NEAREST_SEARCH_WORKERS", "4"))
# اگر شهر نقطهٔ نامزد از نقشه قابل تشخیص نبود، فقط تا این فاصله پذیرفته می‌شود
NEAREST_UNKNOWN_CITY_MAX_M = 1000


def has_tax_value(tax_result: dict) -> bool:
    """آیا سامانه برای این نقطه دست‌کم یک ارزش معاملاتی (مسکونی/تجاری/اداری) داده است؟"""
    try:
        return any(v is not None for v in extract_all_land_use_values(tax_result or {}).values())
    except Exception:
        return False


def _offset_point(lat: float, lng: float, dist_m: float, bearing_deg: float):
    r = 6371000.0
    b = math.radians(bearing_deg)
    dlat = dist_m * math.cos(b) / r
    dlng = dist_m * math.sin(b) / (r * math.cos(math.radians(lat)))
    return lat + math.degrees(dlat), lng + math.degrees(dlng)


def _ring_points(lat: float, lng: float, radius_m: float, ring_idx: int):
    n = 8 if radius_m <= 500 else (12 if radius_m <= 1600 else 16)
    start = (ring_idx % 2) * (180.0 / n)   # حلقه‌های پشت‌سرهم نیم‌گام می‌چرخند تا پوشش بهتر شود
    return [_offset_point(lat, lng, radius_m, start + i * 360.0 / n) for i in range(n)]


def _norm_city(name) -> str:
    if not isinstance(name, str):
        return ""
    n = normalize_persian(name).replace("‌", " ")
    for prefix in ("شهرستان ", "شهر "):
        if n.startswith(prefix):
            n = n[len(prefix):]
    return " ".join(n.split())


def reverse_city(lat: float, lng: float):
    """نام شهر نقطه از نشان: «» برای خارج از شهر، None اگر سرویس در دسترس نبود."""
    if not NESHAN_API_KEY:
        return None
    try:
        resp = requests.get(NESHAN_REVERSE_URL, params={"lat": lat, "lng": lng},
                            headers={"Api-Key": NESHAN_API_KEY}, timeout=10)
        resp.raise_for_status()
        data = resp.json() or {}
        if isinstance(data.get("address"), dict):
            data = {**data, **data["address"]}
        return _norm_city(data.get("city"))
    except Exception as e:
        logger.warning(f"[NEAREST] تشخیص شهر ({lat}, {lng}) ناموفق: {e}")
        return None


def _probe(province_id: int, lat: float, lng: float):
    try:
        with requests.Session() as s:
            res = query_location_value(s, province_id, lat, lng)
        return res if has_tax_value(res) else None
    except Exception as e:
        logger.info(f"[NEAREST] استعلام نقطهٔ ({lat:.6f}, {lng:.6f}) ناموفق: {e}")
        return None


def find_nearest_valued(province_id: int, lat: float, lng: float, deadline: float = None,
                        probe=None, city_of=None):
    """
    نزدیک‌ترین نقطهٔ دارای ارزش منطقه‌ای در همان شهرِ (lat, lng).
    حلقه‌به‌حلقه از نزدیک به دور جستجو می‌کند؛ اگر نقطهٔ دارای ارزش در شهر دیگری
    بود، سراغ نقطهٔ بعدی می‌رود. خروجی: {"tax_info", "lat", "lng", "distance_m", "city"}
    یا None.
    """
    probe = probe or (lambda a, b: _probe(province_id, a, b))
    city_of = city_of or reverse_city
    if deadline is None:
        deadline = time.monotonic() + NEAREST_TIME_BUDGET
    origin_city = city_of(lat, lng)
    logger.info(f"[NEAREST] نقطهٔ ({lat}, {lng}) ارزش ندارد؛ جستجوی نزدیک‌ترین بلوک در شهر «{origin_city}»")

    def city_ok(cand_city, dist):
        if not origin_city:              # شهر مبدأ نامعلوم یا نقطهٔ خارج از شهر ← نزدیک‌ترین
            return True
        if cand_city is None:            # تشخیص شهر نامزد ناموفق ← فقط فاصلهٔ کم
            return dist <= NEAREST_UNKNOWN_CITY_MAX_M
        return cand_city == origin_city

    probed = 0
    with ThreadPoolExecutor(max_workers=NEAREST_WORKERS) as pool:
        for idx, radius in enumerate(NEAREST_RINGS_M):
            if time.monotonic() > deadline:
                logger.warning(f"[NEAREST] پایان مهلت جستجو پیش از شعاع {radius} متر ({probed} نقطه)")
                break
            points = _ring_points(lat, lng, radius, idx)
            results = list(pool.map(lambda p: probe(p[0], p[1]), points))
            probed += len(points)
            for (plat, plng), res in zip(points, results):
                if not res:
                    continue
                cand_city = city_of(plat, plng) if origin_city else None
                if city_ok(cand_city, radius):
                    logger.info(f"[NEAREST] یافت شد: {radius} متر، شهر «{cand_city}»، پس از {probed} نقطه")
                    return {"tax_info": res, "lat": plat, "lng": plng, "distance_m": radius,
                            "city": cand_city or origin_city or ""}
                logger.info(f"[NEAREST] نقطهٔ {radius} متری ارزش دارد ولی در شهر «{cand_city}» است؛ ادامه")
    logger.warning(f"[NEAREST] نقطهٔ دارای ارزش در همان شهر یافت نشد ({probed} نقطه)")
    return None


def _with_nearest_fallback(province_id: int, lat: float, lng: float, tax_result: dict) -> dict:
    """اگر نقطه ارزش نداشت، tax_info نزدیک‌ترین بلوکِ هم‌شهر و مشخصات آن را برمی‌گرداند."""
    if has_tax_value(tax_result):
        return {"tax_info": tax_result, "nearest": None}
    found = find_nearest_valued(province_id, lat, lng)
    if not found:
        return {"tax_info": tax_result, "nearest": None, "nearest_searched": True}
    return {
        "tax_info": found["tax_info"],
        "nearest": {"lat": found["lat"], "lng": found["lng"],
                    "distance_m": found["distance_m"], "city": found["city"]},
    }


def query_by_coordinates(lat: float, lng: float, province_hint: str) -> dict:
    """
    مختصات مستقیم (مثلاً از موقعیت مکانی ارسال‌شده روی نقشه در بله) -> ارزش
    منطقه‌ای (tax.gov.ir). برخلاف full_pipeline، مرحلهٔ Geocoding نشان را
    کامل حذف می‌کند چون مختصات از قبل مشخص است و فقط استان (که از قبل توسط
    کاربر انتخاب شده) لازم است تا شناسهٔ صفحهٔ مالیاتی درست ساخته شود.
    """
    if not province_hint:
        raise ValueError(
            "استان مشخص نشده است. بدون استان نمی‌شه شناسه صحیح صفحه مالیاتی رو ساخت."
        )

    province_id = get_province_id(province_hint)

    with requests.Session() as session:
        tax_result = query_location_value(session, province_id, lat, lng)
    fallback = _with_nearest_fallback(province_id, lat, lng, tax_result)

    return {
        "geocoded": {"lat": lat, "lng": lng, "province": province_hint, "city": None},
        "province_id": province_id,
        **fallback,   # tax_info (+ nearest اگر از بلوک مجاور گرفته شد)
    }


def full_pipeline(address: str, city: str = None, province_hint: str = None) -> dict:
    """
    آدرس متنی -> مختصات (نشان) -> ارزش منطقه‌ای (tax.gov.ir)
    خروجی: دیکشنری نتیجه یا خطا در صورت عدم موفقیت هر مرحله
    """
    geo = geocode_address(address, city=city, province=province_hint)

    province_name = geo["province"] or province_hint
    if not province_name:
        raise ValueError(
            "استان از Geocoding مشخص نشد و province_hint هم داده نشده. "
            "بدون استان نمی‌شه شناسه صحیح صفحه مالیاتی رو ساخت."
        )

    province_id = get_province_id(province_name)

    with requests.Session() as session:
        tax_result = query_location_value(session, province_id, geo["lat"], geo["lng"])
    fallback = _with_nearest_fallback(province_id, geo["lat"], geo["lng"], tax_result)

    return {
        "geocoded": geo,
        "province_id": province_id,
        **fallback,   # tax_info (+ nearest اگر از بلوک مجاور گرفته شد)
    }
