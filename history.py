# -*- coding: utf-8 -*-
"""
history.py — سابقهٔ استعلام‌ها، کد رهگیری و گزارش تجمیعی چند ملک.

هر استعلام موفق با یک کد رهگیری یکتا (مثل RV-7K3M-X9PQ) ذخیره می‌شود؛ همهٔ
اطلاعات لازم برای ساخت دوبارهٔ همان PDF (پاسخ سامانهٔ مالیاتی + نتیجهٔ محاسبه)
و دادهٔ فرم (برای «محاسبهٔ مجدد با تغییر») نگه داشته می‌شود. بنابراین:
  • دریافت دوبارهٔ PDF نیازی به استعلام مجدد از سامانهٔ مالیاتی و مصرف اعتبار ندارد؛
  • هر کسی که کد رهگیری (یا QR روی PDF) را دارد، می‌تواند اصالت مبلغ را بررسی کند.

گزارش تجمیعی: چند استعلام با یک batch_code (مثل RB-Q2W3-E4R5) به هم وصل می‌شوند.
"""
from __future__ import annotations

import json
import time
from decimal import Decimal

import db

# کلیدهای موقت فرم که ذخیرهٔ آن‌ها در سابقه معنا ندارد
_TRANSIENT_KEYS = {"rv_edit_snapshot", "rv_share_wip", "rv_batch", "rv_plan", "rv_office_code",
                   "rv_addr_pending", "rv_bld_use_other_pending"}


# ── JSON با پشتیبانی Decimal (نتیجهٔ ayani_calc شامل Decimal است) ──
def _default(o):
    if isinstance(o, Decimal):
        return {"__dec__": str(o)}
    raise TypeError(f"{type(o).__name__} قابل ذخیره نیست")


def _hook(d):
    if len(d) == 1 and "__dec__" in d:
        return Decimal(d["__dec__"])
    return d


def dumps(o) -> str:
    return json.dumps(o, ensure_ascii=False, default=_default)


def loads(s):
    return json.loads(s, object_hook=_hook) if s else None


def form_inputs(data: dict) -> dict:
    return {k: v for k, v in (data or {}).items() if k.startswith("rv_") and k not in _TRANSIENT_KEYS}


# ══════════════════════════════════════════════════════════════════
def save_query(user_id: int, data: dict, *, province: str, county: str, address: str, calc: dict,
               tax_result: dict, plak: str | None, has_building: bool, batch_code: str | None = None,
               issuer: str | None = None, lat=None, lng=None) -> str:
    """ثبت استعلام موفق؛ خروجی: کد رهگیری. issuer: دفتر صادرکننده (همان که روی PDF چاپ شده)."""
    extra = {"plak": plak, "has_building": has_building, "issuer": issuer, "lat": lat, "lng": lng}
    for _ in range(5):
        code = db.new_code("RV")
        try:
            db.execute(
                "INSERT INTO queries(code, user_id, batch_code, created_at, province, county, address, total, "
                "land_value, bld_value, inputs, calc, tax_result, extra) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (code, int(user_id), batch_code, time.time(), province, county, address,
                 int(calc.get("total") or 0), int(calc.get("land_value") or 0),
                 int(calc.get("building_value") or 0), dumps(form_inputs(data)), dumps(calc),
                 dumps(tax_result or {}), dumps(extra)))
            return code
        except Exception as e:
            if "UNIQUE" not in str(e):
                raise
    raise RuntimeError("ساخت کد رهگیری یکتا ممکن نشد")


def _row_to_dict(r) -> dict:
    d = dict(r)
    for k in ("inputs", "calc", "tax_result", "extra"):
        d[k] = loads(d.get(k)) or {}
    d["plak"] = d["extra"].get("plak")
    d["has_building"] = d["extra"].get("has_building", True)
    d["issuer"] = d["extra"].get("issuer")
    lat, lng = d["extra"].get("lat"), d["extra"].get("lng")
    d["map_center"] = (lat, lng) if lat is not None and lng is not None else None
    return d


def get_query(code: str):
    r = db.query_one("SELECT * FROM queries WHERE code=?", (code,))
    return _row_to_dict(r) if r else None


def list_user_queries(user_id: int, limit: int = 10, offset: int = 0) -> list:
    return [dict(r) for r in db.query(
        "SELECT code, batch_code, created_at, province, county, address, total FROM queries "
        "WHERE user_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?", (int(user_id), limit, offset))]


def count_user_queries(user_id: int) -> int:
    return db.query_one("SELECT COUNT(*) AS n FROM queries WHERE user_id=?", (int(user_id),))["n"]


# ── گزارش تجمیعی ──
def start_batch(user_id: int, first_code: str) -> str:
    """batch جدید که اولین عضو آن استعلام first_code است (اگر از قبل در batch بود، همان برمی‌گردد)."""
    r = db.query_one("SELECT batch_code FROM queries WHERE code=? AND user_id=?", (first_code, int(user_id)))
    if r is None:
        raise KeyError(first_code)
    if r["batch_code"]:
        return r["batch_code"]
    for _ in range(5):
        b = db.new_code("RB")
        if db.query_one("SELECT 1 FROM queries WHERE batch_code=?", (b,)) is None:
            db.execute("UPDATE queries SET batch_code=? WHERE code=?", (b, first_code))
            return b
    raise RuntimeError("ساخت کد گزارش تجمیعی ممکن نشد")


def batch_items(batch_code: str) -> list:
    return [_row_to_dict(r) for r in db.query(
        "SELECT * FROM queries WHERE batch_code=? ORDER BY created_at", (batch_code,))]


def batch_count(batch_code: str) -> int:
    return db.query_one("SELECT COUNT(*) AS n FROM queries WHERE batch_code=?", (batch_code,))["n"]


def batch_summary(batch_code: str):
    r = db.query_one("SELECT COUNT(*) AS n, SUM(total) AS total, MIN(created_at) AS created_at, "
                     "MIN(user_id) AS user_id FROM queries WHERE batch_code=?", (batch_code,))
    return dict(r) if r and r["n"] else None
