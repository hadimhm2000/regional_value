# -*- coding: utf-8 -*-
"""
محاسبهٔ «سهم مالکانه» برای بخش ارزش منطقه‌ای (منطق خالص، بدون وابستگی به aiogram).

مبنا: کل ملک همیشه «ششدانگ» است (= ۱). هر مقداری که در سند آمده به یک کسر
دقیق (fractions.Fraction) از ششدانگ تبدیل می‌شود تا هیچ خطای گرد کردنی رخ ندهد؛
فقط مبلغ نهایی (ریال) گرد می‌شود.

انواع «حلقه» (link) — هر حلقه یک کسر بین ۰ و ۱ است:
  all      ششدانگ                                  → ۱
  dang     «۳ دانگ» / «۱٫۵ دانگ»                    → d ÷ ۶        (۰ < d ≤ ۶)
  sahm     «۱۲٫۵ سهم از ۷۲ سهم»                     → a ÷ b        (۰ < a ≤ b)
  percent  «۲۵ درصد»                                → p ÷ ۱۰۰      (۰ < p ≤ ۱۰۰)

زنجیره (سهمِ تودرتو): «۰٫۵ سهم از ۱۸۵ سهمِ ۳ دانگ از ششدانگ»
  → links = [sahm(0.5, 185), dang(3)] → (۰٫۵÷۱۸۵) × (۳÷۶)
  حلقهٔ اول سهمِ خودِ مالک است و هر حلقهٔ بعدی «پایه»ای است که حلقهٔ قبل از آن است.

ذخیره در FSM: هر حلقه یک dict ساده با رشته‌های کسری است (قابل JSON):
  {"k": "sahm", "n": "25/2", "d": "72"}
"""

from __future__ import annotations

import math
import re
from fractions import Fraction

MAX_LINKS = 6

KIND_ALL, KIND_DANG, KIND_SAHM, KIND_PERCENT = "all", "dang", "sahm", "percent"

# ══════════════════════════════════════════════════════════════════
# نرمال‌سازی و پارس عدد
# ══════════════════════════════════════════════════════════════════
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

# کسرهای لفظی رایج در اسناد
_FRACTION_WORDS = {
    "نیم": Fraction(1, 2), "نصف": Fraction(1, 2),
    "ثلث": Fraction(1, 3), "یکسوم": Fraction(1, 3), "دوسوم": Fraction(2, 3),
    "ربع": Fraction(1, 4), "یکچهارم": Fraction(1, 4), "سهچهارم": Fraction(3, 4),
    "خمس": Fraction(1, 5), "سدس": Fraction(1, 6), "ثمن": Fraction(1, 8),
}

# واحدهایی که کاربر ممکن است کنار عدد بنویسد (حذف می‌شوند)
_UNIT_WORDS = ("مشاع", "دانگ", "سهم", "درصد", "%", "٪")

ERR_EMPTY = "empty"
ERR_SLASH = "slash"          # «۱/۵» — مبهم (اعشار یا کسر؟)
ERR_COMMA = "comma"          # «۱,۵» — مبهم
ERR_INVALID = "invalid"


def normalize(text: str) -> str:
    t = (text or "").translate(_DIGITS)
    t = t.replace("ي", "ی").replace("ك", "ک")
    t = re.sub("[\u200c\u200d\u200e\u200f\u202a-\u202e\u2066-\u2069]", "", t)
    t = (t.replace("\u066b", ".").replace("\u066c", ",").replace("\u060c", ",")
         .replace("\u2044", "/").replace("\u2215", "/"))
    for w in _UNIT_WORDS:
        t = t.replace(w, " ")
    return re.sub(r"\s+", " ", t).strip()


def parse_amount(text: str):
    """
    ورودی عددیِ کاربر → (Fraction، None) یا (None، کد خطا).

    پذیرفته می‌شود:
      ۳ | ۱۲٫۵ | 12.5 | ۱٬۴۰۰ (جداکنندهٔ هزارگان) | ۶۷ و ۱/۳ | ۲ و نیم | نیم | ثلث
    رد می‌شود (مبهم): «۱/۵» تنها (معلوم نیست ۱٫۵ است یا یک‌پنجم) و «۱,۵».
    """
    t = normalize(text)
    if not t:
        return None, ERR_EMPTY

    compact = t.replace(" ", "")

    # کسر لفظی تنها: «نیم»، «ثلث»، «یک سوم»
    if compact in _FRACTION_WORDS:
        return _FRACTION_WORDS[compact], None

    # جداکنندهٔ هزارگان: فقط الگوی استاندارد ۱,۴۰۰ / ۱,۲۳۴,۵۶۷.۵
    if "," in compact:
        if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", compact):
            compact = compact.replace(",", "")
        else:
            return None, ERR_COMMA

    # عدد صحیح / اعشاری
    if re.fullmatch(r"\d+(\.\d+)?|\.\d+", compact):
        return Fraction(compact), None

    # عدد مخلوط: «۶۷ و ۱/۳» یا «۶۷ ۱/۳» (بخش کسری باید کوچک‌تر از ۱ باشد)
    m = re.fullmatch(r"(\d+)\s*(?:و\s*)?(\d+)\s*/\s*(\d+)", t)
    if m and " " in t:
        whole, n, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if d > 0 and 0 < n < d:
            return whole + Fraction(n, d), None
        return None, ERR_INVALID

    # عدد + کسر لفظی: «۲ و نیم»، «۱ و ربع»
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*و\s*(.+)", t)
    if m:
        word = m.group(2).replace(" ", "")
        if word in _FRACTION_WORDS:
            return Fraction(m.group(1)) + _FRACTION_WORDS[word], None

    if re.fullmatch(r"\d+(\.\d+)?\s*/\s*\d+(\.\d+)?", t):
        return None, ERR_SLASH

    return None, ERR_INVALID


ERROR_HINTS = {
    ERR_EMPTY: "لطفاً یک عدد وارد کنید.",
    ERR_SLASH: ("علامت «/» مبهم است (ممکن است منظور اعشار باشد یا کسر).\n"
                "• اگر عدد اعشاری است، با نقطه بنویسید؛ مثلاً ۱٫۵ یا 1.5\n"
                "• اگر کسر است (مثلاً یک‌سوم)، گزینهٔ «سهم از سهم» را انتخاب کنید و "
                "صورت و مخرج را جداگانه وارد کنید (۱ سهم از ۳ سهم)."),
    ERR_COMMA: "لطفاً عدد اعشاری را با نقطه بنویسید (مثلاً ۱٫۵ یا 1.5).",
    ERR_INVALID: "عدد نامعتبر است. فقط عدد وارد کنید (مثلاً ۳ یا ۱۲٫۵).",
}


# ══════════════════════════════════════════════════════════════════
# ذخیره/بازیابی کسر (رشتهٔ قابل JSON)
# ══════════════════════════════════════════════════════════════════
def frac_to_str(f: Fraction) -> str:
    f = Fraction(f)
    return str(f.numerator) if f.denominator == 1 else f"{f.numerator}/{f.denominator}"


def frac_from_str(s) -> Fraction:
    if isinstance(s, Fraction):
        return s
    return Fraction(str(s))


# ══════════════════════════════════════════════════════════════════
# حلقه‌ها و زنجیره
# ══════════════════════════════════════════════════════════════════
def make_link(kind: str, num: Fraction = None, den: Fraction = None) -> dict:
    """ساخت حلقه با اعتبارسنجی؛ در صورت نامعتبر بودن ValueError (پیام فارسی)."""
    if kind == KIND_ALL:
        return {"k": KIND_ALL}
    if num is None:
        raise ValueError("مقدار وارد نشده است.")
    num = Fraction(num)
    if num <= 0:
        raise ValueError("مقدار باید بزرگ‌تر از صفر باشد.")
    if kind == KIND_DANG:
        if num > 6:
            raise ValueError("مقدار دانگ نمی‌تواند از ۶ (ششدانگ) بیشتر باشد.")
        return {"k": KIND_DANG, "n": frac_to_str(num)}
    if kind == KIND_PERCENT:
        if num > 100:
            raise ValueError("درصد نمی‌تواند از ۱۰۰ بیشتر باشد.")
        return {"k": KIND_PERCENT, "n": frac_to_str(num)}
    if kind == KIND_SAHM:
        if den is None:
            raise ValueError("تعداد کل سهام (مخرج) وارد نشده است.")
        den = Fraction(den)
        if den <= 0:
            raise ValueError("تعداد کل سهام باید بزرگ‌تر از صفر باشد.")
        if num > den:
            raise ValueError("تعداد سهم مالک نمی‌تواند از تعداد کل سهام بیشتر باشد.")
        return {"k": KIND_SAHM, "n": frac_to_str(num), "d": frac_to_str(den)}
    raise ValueError(f"نوع سهم ناشناخته: {kind}")


def link_fraction(link: dict) -> Fraction:
    k = link["k"]
    if k == KIND_ALL:
        return Fraction(1)
    n = frac_from_str(link["n"])
    if k == KIND_DANG:
        return n / 6
    if k == KIND_PERCENT:
        return n / 100
    if k == KIND_SAHM:
        return n / frac_from_str(link["d"])
    raise ValueError(f"نوع سهم ناشناخته: {k}")


def chain_fraction(links: list) -> Fraction:
    """حاصل‌ضرب همهٔ حلقه‌ها = سهم نهایی از ششدانگ (۰ < سهم ≤ ۱)."""
    if not links:
        raise ValueError("سهمی وارد نشده است.")
    if len(links) > MAX_LINKS:
        raise ValueError("تعداد مراحل سهم بیش از حد مجاز است.")
    r = Fraction(1)
    for link in links:
        f = link_fraction(link)
        if not (0 < f <= 1):
            raise ValueError("مقدار سهم نامعتبر است.")
        r *= f
    return r


def apply_share(value, share) -> int:
    """مبلغ (ریال) × سهم → گرد کردن نیم به بالا، کاملاً دقیق (بدون float)."""
    q = Fraction(int(value)) * frac_from_str(share)
    if q < 0:
        return -apply_share(-int(value), share)
    return math.floor(q + Fraction(1, 2))


# ══════════════════════════════════════════════════════════════════
# نمایش
# ══════════════════════════════════════════════════════════════════
def to_fa(s) -> str:
    return str(s).translate(str.maketrans("0123456789.", "۰۱۲۳۴۵۶۷۸۹٫"))


def _decimal_places(f: Fraction):
    """تعداد ارقام اعشار دقیق یک کسر پایان‌پذیر؛ برای کسر متناوب None."""
    d, twos, fives = f.denominator, 0, 0
    while d % 2 == 0:
        d //= 2
        twos += 1
    while d % 5 == 0:
        d //= 5
        fives += 1
    return max(twos, fives) if d == 1 else None


def _decimal_str(f: Fraction, places: int) -> str:
    """نمایش اعشاری با گرد کردن نیم به بالا (بدون float)؛ جداکنندهٔ هزارگان «٬»."""
    neg = f < 0
    f = abs(f)
    scaled = math.floor(f * 10 ** places + Fraction(1, 2))
    whole, frac = divmod(scaled, 10 ** places)
    s = f"{whole:,}".replace(",", "\u066c")
    if places:
        s = (s + "." + str(frac).rjust(places, "0")).rstrip("0").rstrip(".")
    return ("-" if neg else "") + s


def fmt_frac(f, max_decimals: int = 6) -> str:
    """عدد کسری → متن فارسی: «۱۲٫۵»، «۶۷ و ۱/۳»، یا «≈ ۰٫۱۲۳۵»."""
    f = frac_from_str(f)
    places = _decimal_places(f)
    if places is not None and places <= max_decimals:
        return to_fa(_decimal_str(f, places))
    if f.denominator <= 1000:
        whole = f.numerator // f.denominator
        rest = f - whole
        txt = f"{rest.numerator}/{rest.denominator}"
        return to_fa(f"{whole:,}".replace(",", "\u066c") + " و " + txt if whole else txt)
    return "≈ " + to_fa(_decimal_str(f, 4))


def _approx(f: Fraction, places: int = 4) -> str:
    # سهم‌های خیلی کوچک (مثلاً ۰٫۰۰۸۱ دانگ) با دقت بیشتر نمایش داده می‌شوند
    if 0 < f < Fraction(1, 100):
        places = 6
    p = _decimal_places(f)
    if p is not None and p <= places:
        return to_fa(_decimal_str(f, p))
    return "≈ " + to_fa(_decimal_str(f, places))


def describe_link(link: dict) -> str:
    k = link["k"]
    if k == KIND_ALL:
        return "ششدانگ"
    n = fmt_frac(link["n"])
    if k == KIND_DANG:
        return f"{n} دانگ"
    if k == KIND_PERCENT:
        return f"{n} درصد"
    return f"{n} سهم از {fmt_frac(link['d'])} سهم"


def describe_chain(links: list) -> str:
    """«۰٫۵ سهم از ۱۸۵ سهم، از ۳ دانگ از ششدانگ»"""
    if len(links) == 1 and links[0]["k"] == KIND_ALL:
        return "ششدانگ"
    parts = [describe_link(l) for l in links]
    return "، از ".join(parts) + " از ششدانگ"


def format_share(share) -> str:
    """سهم نهایی → «۱٫۵ دانگ از ششدانگ (۲۵٪)»"""
    f = frac_from_str(share)
    if f == 1:
        return "ششدانگ (۱۰۰٪)"
    return f"{_approx(f * 6)} دانگ از ششدانگ ({_approx(f * 100)}٪)"


def format_fraction_plain(share) -> str:
    """کسر ساده برای فرمول: «۱/۴»"""
    f = frac_from_str(share)
    return to_fa(f"{f.numerator}/{f.denominator}") if f.denominator != 1 else to_fa(f.numerator)
