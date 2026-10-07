# -*- coding: utf-8 -*-
"""
discounts.py — کدهای تخفیف اشتراک (منطق خالص، بدون aiogram).

  • کد عمومی: ادمین با /discount_new می‌سازد (درصد، سقف تعداد استفاده، مدت اعتبار، محصول)
  • کد شخصی: برای پیگیری تست‌گیرندگان خودکار ساخته می‌شود (فقط برای همان کاربر، یک بار)
  • هر کاربر از هر کد حداکثر یک بار استفاده می‌کند
  • محصول: rv (ارزش منطقه‌ای) | util (خدمات کاربردی) | all (هر دو)
"""
from __future__ import annotations

import re
import secrets
import time

import db

PRODUCTS = {"rv": "استعلام ارزش منطقه‌ای", "util": "خدمات کاربردی", "all": "همهٔ اشتراک‌ها"}
_CODE_RE = re.compile(r"^[A-Z0-9_-]{3,20}$")


class DiscountError(ValueError):
    pass


def normalize(code: str) -> str:
    t = (code or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    return t.strip().upper().replace(" ", "")


def create(code: str, percent: int, max_uses: int | None = None, days: int | None = None, product: str = "all",
           for_user: int | None = None, note: str = "") -> dict:
    code = normalize(code)
    if not _CODE_RE.match(code):
        raise DiscountError("کد باید ۳ تا ۲۰ کاراکتر از حروف انگلیسی، عدد، - یا _ باشد.")
    if not 1 <= int(percent) <= 100:
        raise DiscountError("درصد تخفیف باید بین ۱ تا ۱۰۰ باشد.")
    if product not in PRODUCTS:
        raise DiscountError("محصول باید rv، util یا all باشد.")
    if db.query_one("SELECT 1 FROM discount_codes WHERE code=?", (code,)):
        raise DiscountError("این کد قبلاً ساخته شده است.")
    expires = time.time() + days * 86400 if days else None
    db.execute("INSERT INTO discount_codes(code, percent, product, max_uses, used, expires_at, for_user, note, active, "
               "created_at) VALUES (?,?,?,?,0,?,?,?,1,?)",
               (code, int(percent), product, max_uses or None, expires, for_user, note or "", time.time()))
    return get(code)


def personal_code(user_id: int, percent: int, days: int, product: str = "rv", prefix: str = "VIP") -> dict:
    for _ in range(5):
        code = f"{prefix}-{secrets.token_hex(3).upper()}"
        try:
            return create(code, percent, max_uses=1, days=days, product=product, for_user=user_id,
                          note="پیگیری خودکار تست")
        except DiscountError:
            continue
    raise DiscountError("ساخت کد شخصی ممکن نشد.")


def get(code: str):
    r = db.query_one("SELECT * FROM discount_codes WHERE code=?", (normalize(code),))
    return dict(r) if r else None


def validate(code: str, user_id: int, product: str) -> dict:
    """کد معتبر برای این کاربر و محصول؟ خروجی: رکورد کد؛ وگرنه DiscountError با پیام مناسب."""
    d = get(code)
    if not d or not d["active"]:
        raise DiscountError("کد تخفیف معتبر نیست.")
    if d["expires_at"] and d["expires_at"] < time.time():
        raise DiscountError("مهلت استفاده از این کد تخفیف تمام شده است.")
    if d["product"] not in ("all", product):
        raise DiscountError(f"این کد فقط برای اشتراک «{PRODUCTS[d['product']]}» است.")
    if d["for_user"] and int(d["for_user"]) != int(user_id):
        raise DiscountError("این کد تخفیف مخصوص کاربر دیگری است.")
    if d["max_uses"] and d["used"] >= d["max_uses"]:
        raise DiscountError("ظرفیت استفاده از این کد تخفیف تکمیل شده است.")
    if db.query_one("SELECT 1 FROM discount_uses WHERE code=? AND user_id=?", (d["code"], int(user_id))):
        raise DiscountError("شما قبلاً از این کد تخفیف استفاده کرده‌اید.")
    return d


def discounted_toman(toman: int, percent: int) -> int:
    """قیمت پس از تخفیف (تومان، گرد به پایین تا ۱۰۰ تومان)."""
    return max(0, (toman * (100 - int(percent)) // 100) // 100 * 100)


def record_use(code: str, user_id: int, payment_id: int | None) -> bool:
    """ثبت استفاده پس از پرداخت موفق (اتمیک)؛ False اگر این کاربر قبلاً ثبت شده بود."""
    code = normalize(code)
    with db.tx() as c:
        n = c.execute("INSERT OR IGNORE INTO discount_uses(code, user_id, payment_id, used_at) VALUES (?,?,?,?)",
                      (code, int(user_id), payment_id, time.time())).rowcount
        if n:
            c.execute("UPDATE discount_codes SET used = used + 1 WHERE code=?", (code,))
    return n > 0


def deactivate(code: str) -> bool:
    return db.execute("UPDATE discount_codes SET active=0 WHERE code=?", (normalize(code),)) > 0


def list_codes(include_personal: bool = False) -> list:
    sql = "SELECT * FROM discount_codes" + ("" if include_personal else " WHERE for_user IS NULL") + \
          " ORDER BY created_at DESC LIMIT 50"
    return [dict(r) for r in db.query(sql)]
