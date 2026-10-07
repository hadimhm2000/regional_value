# -*- coding: utf-8 -*-
"""
diyeh_calc.py — محاسبهٔ دیه طبق قانون مجازات اسلامی (کتاب دیات). منطق خالص، بدون aiogram.

داده: assets/diyeh_data.json
  • full_diyeh_by_year: مبلغ دیهٔ کامل هر سال (ریال) طبق بخشنامهٔ رئیس قوه قضاییه (مادهٔ ۵۴۹)
  • categories: دسته‌ها و موارد اعضا، منافع، جراحات و جنین:
      title          عنوان مورد
      rule           کسر دیهٔ کامل («1/2»، «2/100») یا متن حکم («موجب ارش است»، «یک پنجم دیه آن عضو»...)
      female_art560  مشمول مادهٔ ۵۶۰ (تنصیف دیهٔ زن از ثلث به بالا) است؟
      per_item       دیه «برای هر مورد» است (مثل هر دندان) — با تعداد ضرب می‌شود
      max_count      حداکثر تعداد مجاز
      law            متن مادهٔ قانونی
  مبلغ سال‌های جدید را ادمین با /diyeh_rate ثبت می‌کند (DATA_DIR/diyeh_rates.json) — بدون تغییر کد.

روش‌های محاسبه:
  ۱. درصدی       — درصدی از دیهٔ کامل (مثلاً درصد تعیین‌شدهٔ پزشکی قانونی برای ارش)
  ۲. کسری        — زنجیرهٔ کسرها («۱/۲ از ۱/۳ از دیهٔ کامل» = حاصل‌ضرب)
  ۳. دیهٔ قتل     — مرد: کامل؛ زن: نصف (۵۵۰)؛ خنثای مشکل: سه‌چهارم (۵۵۱)؛ ماه حرام/حرم مکه: +یک‌سوم (۵۵۵)
  ۴. اعضا و ...  — کسر جدول × تعداد × دیهٔ کامل؛ زن در موارد مشمول ۵۶۰ اگر کسر کل ≥ ثلث شود: نصف
     موارد با حکم متنی (ارش، «به همان نسبت»، ...) مبلغ ثابت ندارند و فقط حکم و ماده نمایش داده می‌شود.
"""
from __future__ import annotations

import json
import os
import re
from fractions import Fraction

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(_HERE, "assets", "diyeh_data.json")
_DATA_DIR = os.environ.get("DATA_DIR", os.path.join(_HERE, "data"))
RATES_FILE = os.path.join(_DATA_DIR, "diyeh_rates.json")

METHOD_PERCENT = "percent"
METHOD_FRACTION = "fraction"
METHOD_HOMICIDE = "homicide"
METHOD_ORGAN = "organ"
METHOD_TITLES = {
    METHOD_ORGAN: "اعضا، منافع، جراحات و جنین",
    METHOD_HOMICIDE: "دیهٔ قتل (نفس)",
    METHOD_PERCENT: "درصدی از دیهٔ کامل",
    METHOD_FRACTION: "کسری از دیهٔ کامل",
}
SEX_MALE, SEX_FEMALE, SEX_INTERSEX = "male", "female", "intersex"
SEX_TITLES = {SEX_MALE: "مرد", SEX_FEMALE: "زن", SEX_INTERSEX: "خنثای مشکل"}

ART_549 = ("ماده ۵۴۹ ـ موارد دیهٔ کامل همان است که در مقررات شرع تعیین شده است و میزان آن در ابتدای هر سال "
           "توسط رئیس قوه قضاییه به تفصیل بر اساس نظر مقام رهبری تعیین و اعلام می‌شود.")
ART_550 = "ماده ۵۵۰ ـ دیهٔ قتل زن، نصف دیهٔ مرد است."
ART_551 = ("ماده ۵۵۱ ـ دیهٔ خنثای ملحق به مرد، دیهٔ مرد و دیهٔ خنثای ملحق به زن، دیهٔ زن و دیهٔ خنثای مشکل، "
           "نصف دیهٔ مرد به علاوهٔ نصف دیهٔ زن است.")
ART_555 = ("ماده ۵۵۵ ـ هرگاه رفتار مرتکب و فوت مجنیٌ‌علیه هر دو در ماه‌های حرام «محرم، رجب، ذی‌القعده و ذی‌الحجه» "
           "یا در محدودهٔ حرم مکه واقع شود، خواه جنایت عمدی خواه غیرعمدی باشد، علاوه بر دیهٔ نفس، یک‌سوم دیه نیز "
           "افزوده می‌گردد. سایر مکان‌ها و زمان‌های مقدس و متبرک مشمول حکم تغلیظ دیه نیست.")
ART_560 = ("ماده ۵۶۰ ـ دیهٔ زن و مرد در اعضا و منافع تا کمتر از ثلث دیهٔ کامل مرد یکسان است و چنانچه ثلث یا بیشتر "
           "شود دیهٔ زن به نصف تقلیل می‌یابد.")

_FRACTION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*$")
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫", "01234567890123456789.")

_data_cache = None


def load_data() -> dict:
    global _data_cache
    if _data_cache is None:
        with open(DATA_FILE, encoding="utf-8") as f:
            _data_cache = json.load(f)
    return _data_cache


# ══════════════════════════════════════════════════════════════════
# مبلغ دیهٔ کامل هر سال
# ══════════════════════════════════════════════════════════════════
def _load_overrides() -> dict:
    if not os.path.exists(RATES_FILE):
        return {}
    try:
        with open(RATES_FILE, encoding="utf-8") as f:
            return {str(k): int(v) for k, v in json.load(f).items()}
    except Exception:
        return {}


def full_diyeh_table() -> dict:
    """{سال: مبلغ ریال} — دادهٔ پایه + مقادیر ثبت‌شدهٔ ادمین (مقدار ادمین مقدم است)."""
    t = {str(k): int(v) for k, v in load_data()["full_diyeh_by_year"].items()}
    t.update(_load_overrides())
    return dict(sorted(t.items()))


def full_diyeh(year: int) -> int | None:
    return full_diyeh_table().get(str(year))


def latest_year() -> int:
    return int(max(full_diyeh_table()))


def set_full_diyeh(year: int, amount_rial: int) -> None:
    """ثبت/اصلاح مبلغ دیهٔ کامل یک سال (ادمین)."""
    data = _load_overrides()
    data[str(int(year))] = int(amount_rial)
    os.makedirs(os.path.dirname(RATES_FILE), exist_ok=True)
    tmp = RATES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, RATES_FILE)


# ══════════════════════════════════════════════════════════════════
# جدول اعضا
# ══════════════════════════════════════════════════════════════════
def categories() -> list:
    return [c["category"] for c in load_data()["categories"]]


def items(category_index: int) -> list:
    return load_data()["categories"][category_index]["items"]


def get_item(category_index: int, item_index: int) -> dict:
    return items(category_index)[item_index]


def parse_fraction(text) -> Fraction | None:
    """«۱/۲»، «2/100»، «0.5/1» → Fraction؛ متن غیرکسری → None"""
    m = _FRACTION_RE.match(str(text or "").translate(_DIGITS))
    if not m:
        return None
    num, den = Fraction(m.group(1)), Fraction(m.group(2))
    if den == 0:
        return None
    return num / den


def item_fraction(item: dict) -> Fraction | None:
    return parse_fraction(item.get("rule"))


def female_halved(item: dict, total_fraction: Fraction) -> bool:
    """مادهٔ ۵۶۰: در موارد مشمول، دیهٔ زن از ثلث دیهٔ کامل به بالا نصف می‌شود."""
    return bool(item.get("female_art560")) and total_fraction >= Fraction(1, 3)


def needs_sex(item: dict, count: int = 1) -> bool:
    """جنسیت مصدوم در نتیجه اثر دارد؟ (فقط مواردی که با این تعداد به ثلث یا بیشتر می‌رسند)"""
    f = item_fraction(item)
    if f is None or not item.get("female_art560"):
        return False
    return f * (count if item.get("per_item") else 1) >= Fraction(1, 3)


def fraction_text(f: Fraction) -> str:
    return f"{f.numerator}/{f.denominator}"


# ══════════════════════════════════════════════════════════════════
# محاسبه
# ══════════════════════════════════════════════════════════════════
class DiyehError(ValueError):
    pass


def _round(x: Fraction) -> int:
    """گرد کردن به نزدیک‌ترین ریال (نیم به بالا)."""
    return int((x + Fraction(1, 2)).__floor__())


def calculate(*, year: int, method: str, percent=None, fractions=None, sex: str = SEX_MALE,
              holy_month: bool = False, category_index: int = None, item_index: int = None,
              count: int = 1) -> dict:
    """
    خروجی: {
      "year", "full", "method", "amount" (ریال یا None برای حکم متنی), "rule_text" (حکم متنی یا None),
      "fraction" (کسر نهایی دیهٔ کامل یا None), "inputs": [(برچسب، مقدار)], "steps": [متن],
      "laws": [متن مواد]
    }
    """
    full = full_diyeh(year)
    if not full:
        raise DiyehError(f"مبلغ دیهٔ کامل سال {year} ثبت نشده است.")
    inputs = [("سال", str(year)), ("روش محاسبه", METHOD_TITLES.get(method, method))]
    steps, laws = [], []
    fraction = None
    rule_text = None

    if method == METHOD_PERCENT:
        try:
            p = Fraction(str(percent).translate(_DIGITS).replace("%", "").strip())
        except (ValueError, ZeroDivisionError):
            raise DiyehError("درصد وارد شده معتبر نیست.")
        if p <= 0 or p > 1000:
            raise DiyehError("درصد باید بیشتر از صفر و حداکثر ۱۰۰۰ باشد.")
        fraction = p / 100
        inputs.append(("درصد از دیهٔ کامل", f"{_num(p)}٪"))
        steps.append(f"دیه = {_num(p)} درصد از دیهٔ کامل")

    elif method == METHOD_FRACTION:
        fr = [parse_fraction(x) for x in (fractions or [])]
        if not fr or any(f is None or f <= 0 for f in fr):
            raise DiyehError("کسرها معتبر نیست (نمونه: 1/2 یا 2/100).")
        fraction = Fraction(1)
        for f in fr:
            fraction *= f
        inputs.append(("کسرها", " از ".join(fraction_text(f) for f in fr) + " دیهٔ کامل"))
        steps.append("حاصل‌ضرب کسرها: " + " × ".join(fraction_text(f) for f in fr)
                     + f" = {fraction_text(fraction)} دیهٔ کامل")

    elif method == METHOD_HOMICIDE:
        if sex not in SEX_TITLES:
            raise DiyehError("جنسیت مقتول معتبر نیست.")
        inputs.append(("جنسیت مقتول", SEX_TITLES[sex]))
        fraction = {SEX_MALE: Fraction(1), SEX_FEMALE: Fraction(1, 2), SEX_INTERSEX: Fraction(3, 4)}[sex]
        if sex == SEX_FEMALE:
            laws.append(ART_550)
            steps.append("دیهٔ قتل زن: نصف دیهٔ کامل")
        elif sex == SEX_INTERSEX:
            laws.append(ART_551)
            steps.append("دیهٔ خنثای مشکل: سه‌چهارم دیهٔ کامل")
        inputs.append(("وقوع در ماه حرام یا حرم مکه", "بله" if holy_month else "خیر"))
        if holy_month:
            fraction *= Fraction(4, 3)
            laws.append(ART_555)
            steps.append("تغلیظ ماه حرام: یک‌سوم دیه افزوده می‌شود (× ۴/۳)")

    elif method == METHOD_ORGAN:
        try:
            cat = load_data()["categories"][category_index]
            item = cat["items"][item_index]
        except (IndexError, TypeError):
            raise DiyehError("مورد انتخاب‌شده معتبر نیست.")
        inputs += [("دسته", cat["category"]), ("نوع جنایت", item["title"])]
        if item.get("law"):
            laws.append(item["law"])
        base = item_fraction(item)
        n = 1
        if item.get("per_item"):
            if not isinstance(count, int) or count < 1:
                raise DiyehError("تعداد باید عدد صحیح مثبت باشد.")
            if item.get("max_count") and count > item["max_count"]:
                raise DiyehError(f"تعداد نمی‌تواند بیشتر از {item['max_count']} باشد.")
            n = count
            inputs.append(("تعداد", str(n)))
        if base is None:
            rule_text = item["rule"]
            steps.append(f"حکم قانونی این مورد: {rule_text}")
        else:
            fraction = base * n
            steps.append(f"کسر دیهٔ کامل طبق قانون: {fraction_text(base)}" + (f" برای هر مورد × {n} مورد" if n > 1 else ""))
            if item.get("female_art560") and fraction >= Fraction(1, 3):
                if sex not in (SEX_MALE, SEX_FEMALE):
                    raise DiyehError("جنسیت مصدوم معتبر نیست.")
                inputs.append(("جنسیت مصدوم", SEX_TITLES[sex]))
                laws.append(ART_560)
                if sex == SEX_FEMALE:
                    fraction /= 2
                    steps.append("مصدوم زن و دیه به ثلث دیهٔ کامل یا بیشتر رسیده است: نصف می‌شود (مادهٔ ۵۶۰)")
            elif item.get("female_art560"):
                laws.append(ART_560)
                steps.append("کمتر از ثلث دیهٔ کامل: دیهٔ زن و مرد یکسان است (مادهٔ ۵۶۰)")
    else:
        raise DiyehError("روش محاسبه معتبر نیست.")

    laws.append(ART_549)
    amount = _round(fraction * full) if fraction is not None else None
    return {"year": year, "full": full, "method": method, "amount": amount, "fraction": fraction,
            "rule_text": rule_text, "inputs": inputs, "steps": steps, "laws": laws}


def _num(f: Fraction) -> str:
    if f.denominator == 1:
        return str(f.numerator)
    return f"{float(f):.4f}".rstrip("0").rstrip(".")


def format_result_fa(r: dict) -> str:
    lines = ["⚖️ *نتیجهٔ محاسبهٔ دیه*", ""]
    lines += [f"• {k}: {v}" for k, v in r["inputs"]]
    lines.append("")
    if r["amount"] is not None:
        lines.append(f"📐 {fraction_text(r['fraction'])} دیهٔ کامل")
        lines.append(f"💰 *مبلغ دیه: {r['amount']:,} ریال* ({r['amount'] // 10:,} تومان)")
    else:
        lines.append(f"📜 *حکم: {r['rule_text']}*")
        lines.append("این مورد مبلغ ثابت ندارد؛ برای ارش یا «به همان نسبت»، درصد تعیین‌شده توسط پزشکی قانونی را "
                     "با روش «درصدی از دیهٔ کامل» محاسبه کنید.")
    lines.append(f"🔹 دیهٔ کامل سال {r['year']}: {r['full']:,} ریال")
    return "\n".join(lines)
