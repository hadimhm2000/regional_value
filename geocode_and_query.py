# -*- coding: utf-8 -*-
"""
پایپ‌لاین کامل: آدرس متنی فارسی -> Geocoding با نشان -> استعلام ارزش منطقه‌ای از tax.gov.ir

نیازمندی‌ها:
    pip install requests beautifulsoup4 --break-system-packages

نکته امنیتی: کلید API نشان از متغیر محیطی NESHAN_API_KEY خوانده می‌شود.
"""

import json
import os
import logging

import requests

from tax_geolocation_query import query_location_value, get_province_id

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

    return {
        "geocoded": {"lat": lat, "lng": lng, "province": province_hint, "city": None},
        "province_id": province_id,
        "tax_info": tax_result,
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

    return {
        "geocoded": geo,
        "province_id": province_id,
        "tax_info": tax_result,  # حالا دیکشنری ساختاریافته است
    }
