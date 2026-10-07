# -*- coding: utf-8 -*-
"""
map_image.py — تصویر نقشهٔ ایستا (PNG) از سرویس نشان برای گزارش PDF ارزش منطقه‌ای.

  GET https://api.neshan.org/v5/static?key=<کلید>&type=day&zoom=..&width=..&height=..
      &latitude=..&longitude=..&marker=red
  (کلید در پارامتر key — نه در سرآیند Api-Key؛ خروجی PNG)

کلید سرویس نقشهٔ ایستا ممکن است جدا از کلید Geocoding باشد: NESHAN_STATIC_MAP_KEY
(اگر خالی باشد، همان NESHAN_API_KEY). اگر سرویس خطا بدهد، گزارش بدون نقشه ساخته می‌شود و
تا ۳۰ دقیقه دوباره تلاش نمی‌شود (تا ساخت PDF کند نشود).
"""
from __future__ import annotations

import logging
import os
import tempfile
import time

logger = logging.getLogger(__name__)

STATIC_URL = "https://api.neshan.org/v5/static"
_TIMEOUT = 8
_COOLDOWN = 30 * 60
_disabled_until = 0.0
last_error = ""


def _config():
    try:
        from config import NESHAN_STATIC_MAP, NESHAN_STATIC_MAP_KEY
        return NESHAN_STATIC_MAP, NESHAN_STATIC_MAP_KEY
    except Exception:
        return False, ""


def fetch_png(lat: float, lng: float, width: int = 800, height: int = 420, zoom: int = 16,
              force: bool = False) -> bytes | None:
    """PNG نقشه یا None (غیرفعال، بدون کلید، خطای سرویس)."""
    global _disabled_until, last_error
    enabled, key = _config()
    if not force and (not enabled or time.time() < _disabled_until):
        return None
    if not key:
        last_error = "کلید نشان تنظیم نشده است (NESHAN_API_KEY یا NESHAN_STATIC_MAP_KEY)."
        return None
    try:
        import requests
        r = requests.get(STATIC_URL, timeout=_TIMEOUT, params={
            "key": key, "type": "day", "zoom": zoom, "width": width, "height": height,
            "latitude": f"{lat:.6f}", "longitude": f"{lng:.6f}", "marker": "red"})
        ctype = r.headers.get("content-type", "")
        if r.status_code == 200 and (ctype.startswith("image/") or r.content[:4] == b"\x89PNG"):
            last_error = ""
            return r.content
        last_error = f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as e:
        last_error = f"{type(e).__name__}: {e}"
    logger.warning(f"[MAP] دریافت نقشهٔ ایستا ناموفق: {last_error}")
    _disabled_until = time.time() + _COOLDOWN
    return None


def fetch_to_file(lat, lng, **kw) -> str | None:
    """ذخیرهٔ نقشه در فایل موقت؛ مسیر یا None. فراخواننده فایل را پاک می‌کند."""
    if lat is None or lng is None:
        return None
    png = fetch_png(float(lat), float(lng), **kw)
    if not png:
        return None
    fd, path = tempfile.mkstemp(prefix="rvmap_", suffix=".png")
    with os.fdopen(fd, "wb") as f:
        f.write(png)
    return path
