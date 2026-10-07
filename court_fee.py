"""
محاسبهٔ هزینهٔ دادرسی محاکم دادگستری (۱۴۰۵/۰۷) — بر اساس بهای خواسته یا محکوم‌به (ریال).

  ۱. بدوی و صلح: تا ۲۰۰,۰۰۰,۰۰۰ ریال ۲٫۵٪ و نسبت به مازاد آن ۳٫۵٪
  ۲. تجدیدنظر و واخواهی: ۴٫۵٪
  ۳. اعاده دادرسی، فرجام‌خواهی و اعتراض ثالث: ۵٫۵٪
"""
from decimal import Decimal, ROUND_HALF_UP

FIRST_INSTANCE_THRESHOLD = 200_000_000  # ریال

CAT_FIRST = "first"
CAT_APPEAL = "appeal"
CAT_RETRIAL = "retrial"

CATEGORY_TITLES = {
    CAT_FIRST: "بدوی و صلح",
    CAT_APPEAL: "تجدیدنظر و واخواهی",
    CAT_RETRIAL: "اعاده دادرسی، فرجام‌خواهی و اعتراض ثالث",
}

CATEGORY_RULES = {
    CAT_FIRST: "تا ۲۰۰ میلیون ریال ۲٫۵ درصد و نسبت به مازاد آن ۳٫۵ درصد بهای خواسته",
    CAT_APPEAL: "۴٫۵ درصد بهای خواسته یا محکوم‌به",
    CAT_RETRIAL: "۵٫۵ درصد بهای خواسته یا محکوم‌به",
}


def _pct(amount: int, percent: str) -> Decimal:
    return Decimal(amount) * Decimal(percent) / Decimal(100)


def _round(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def calc_court_fee(amount_rial: int, category: str) -> dict:
    """هزینهٔ دادرسی (ریال) + جزئیات. ValueError برای مبلغ/دستهٔ نامعتبر."""
    if not isinstance(amount_rial, int) or amount_rial <= 0:
        raise ValueError("مبلغ باید عددی بزرگ‌تر از صفر باشد.")
    if category == CAT_FIRST:
        base = min(amount_rial, FIRST_INSTANCE_THRESHOLD)
        excess = max(0, amount_rial - FIRST_INSTANCE_THRESHOLD)
        part1 = _pct(base, "2.5")
        part2 = _pct(excess, "3.5")
        return {"category": category, "amount": amount_rial, "fee": _round(part1 + part2),
                "base": base, "base_fee": _round(part1), "excess": excess, "excess_fee": _round(part2)}
    if category == CAT_APPEAL:
        return {"category": category, "amount": amount_rial, "fee": _round(_pct(amount_rial, "4.5"))}
    if category == CAT_RETRIAL:
        return {"category": category, "amount": amount_rial, "fee": _round(_pct(amount_rial, "5.5"))}
    raise ValueError("دستهٔ نامعتبر")


def format_result_fa(r: dict) -> str:
    cat = r["category"]
    lines = [
        f"⚖️ *هزینهٔ دادرسی — مرحلهٔ {CATEGORY_TITLES[cat]}*",
        "",
        f"💵 بهای خواسته / محکوم‌به: {r['amount']:,} ریال ({r['amount'] // 10:,} تومان)",
        f"📐 قاعده: {CATEGORY_RULES[cat]}",
    ]
    if cat == CAT_FIRST and r["excess"]:
        lines += [
            f"  • تا {FIRST_INSTANCE_THRESHOLD:,} ریال × ۲٫۵٪ = {r['base_fee']:,} ریال",
            f"  • مازاد {r['excess']:,} ریال × ۳٫۵٪ = {r['excess_fee']:,} ریال",
        ]
    lines += [
        "",
        f"💰 *هزینهٔ دادرسی: {r['fee']:,} ریال* ({r['fee'] // 10:,} تومان)",
    ]
    return "\n".join(lines)
