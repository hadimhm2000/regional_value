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


def _check(d, user_id: int, product: str, now: float) -> None:
    if not d or not d["active"]:
        raise DiscountError("کد تخفیف معتبر نیست.")
    if d["expires_at"] and d["expires_at"] < now:
        raise DiscountError("مهلت استفاده از این کد تخفیف تمام شده است.")
    if d["product"] not in ("all", product):
        raise DiscountError(f"این کد فقط برای اشتراک «{PRODUCTS[d['product']]}» است.")
    if d["for_user"] and int(d["for_user"]) != int(user_id):
        raise DiscountError("این کد تخفیف مخصوص کاربر دیگری است.")
    if d["max_uses"] and d["used"] >= d["max_uses"]:
        raise DiscountError("ظرفیت استفاده از این کد تخفیف تکمیل شده است.")


def validate(code: str, user_id: int, product: str) -> dict:
    """کد معتبر برای این کاربر و محصول؟ خروجی: رکورد کد؛ وگرنه DiscountError با پیام مناسب."""
    d = get(code)
    _check(d, user_id, product, time.time())
    if db.query_one("SELECT 1 FROM discount_uses WHERE code=? AND user_id=?", (d["code"], int(user_id))):
        raise DiscountError("شما قبلاً از این کد تخفیف استفاده کرده‌اید.")
    return d


# مدت رزرو کد بین تایید pre_checkout و رسیدن پرداخت موفق (ثانیه)
HOLD_SECONDS = 180


def reserve(code: str, user_id: int, product: str) -> dict:
    """
    رزرو اتمیک کد تخفیف در لحظهٔ pre_checkout (قبل از کسر پول).

    بدون رزرو، دو فاکتور هم‌زمان (از یک کاربر یا چند کاربر روی کدی با ظرفیت محدود) هر دو از
    validate عبور می‌کردند چون استفاده تازه پس از پرداخت ثبت می‌شود. حالا:
      • تا HOLD_SECONDS ثانیه هیچ پرداخت دیگری از همین کاربر با همین کد پذیرفته نمی‌شود؛
      • رزروهای فعال دیگران هم جزو ظرفیت (max_uses) حساب می‌شوند.
    رزرو با record_use (پرداخت موفق) آزاد می‌شود و در غیر این صورت خودش منقضی می‌شود.
    """
    code = normalize(code)
    now = time.time()
    with db.tx() as c:
        c.execute("DELETE FROM discount_holds WHERE expires_at <= ?", (now,))
        r = c.execute("SELECT * FROM discount_codes WHERE code=?", (code,)).fetchone()
        d = dict(r) if r else None
        _check(d, user_id, product, now)
        if c.execute("SELECT 1 FROM discount_uses WHERE code=? AND user_id=?", (code, int(user_id))).fetchone():
            raise DiscountError("شما قبلاً از این کد تخفیف استفاده کرده‌اید.")
        if c.execute("SELECT 1 FROM discount_holds WHERE code=? AND user_id=?", (code, int(user_id))).fetchone():
            raise DiscountError("پرداخت دیگری با این کد تخفیف در حال انجام است؛ چند دقیقهٔ دیگر دوباره تلاش کنید.")
        if d["max_uses"]:
            held = c.execute("SELECT COUNT(*) FROM discount_holds WHERE code=?", (code,)).fetchone()[0]
            if d["used"] + held >= d["max_uses"]:
                raise DiscountError("ظرفیت استفاده از این کد تخفیف تکمیل شده است.")
        c.execute("INSERT INTO discount_holds(code, user_id, expires_at) VALUES (?,?,?)",
                  (code, int(user_id), now + HOLD_SECONDS))
    return d


def release_hold(code: str, user_id: int) -> None:
    db.execute("DELETE FROM discount_holds WHERE code=? AND user_id=?", (normalize(code), int(user_id)))


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
        c.execute("DELETE FROM discount_holds WHERE code=? AND user_id=?", (code, int(user_id)))
    return n > 0


def deactivate(code: str) -> bool:
    return db.execute("UPDATE discount_codes SET active=0 WHERE code=?", (normalize(code),)) > 0


def list_codes(include_personal: bool = False) -> list:
    sql = "SELECT * FROM discount_codes" + ("" if include_personal else " WHERE for_user IS NULL") + \
          " ORDER BY created_at DESC LIMIT 50"
    return [dict(r) for r in db.query(sql)]
