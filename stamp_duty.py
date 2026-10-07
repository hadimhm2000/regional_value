"""
محاسبه تمبر مالیاتی و حق‌الوکاله برای دعاوی مالی
بر اساس آیین‌نامه تعرفه حق‌الوکاله (ماده ۹) و ماده ۱۰۳ ق.م.م.
"""
from decimal import Decimal, ROUND_HALF_UP


def calculate_stamp_duty(claim_amount: int) -> dict:
    """
    محاسبه تمبر دادگستری و حق‌الوکاله برای دعاوی مالی معمولی

    Args:
        claim_amount: بهای خواسته به ریال (عدد صحیح)

    Returns:
        dict شامل ۶ مقدار: تمبر کلی، تمبر بدوی، تمبر تجدیدنظر،
        حق‌الوکاله کلی، حق‌الوکاله بدوی، حق‌الوکاله تجدیدنظر
        (همه به ریال، به عدد صحیح گرد شده)
    """
    if claim_amount is None or claim_amount <= 0:
        raise ValueError("بهای خواسته باید عددی مثبت باشد")

    amount = Decimal(str(claim_amount))

    # پله‌های تعرفه حق‌الوکاله (ماده ۹ آیین‌نامه تعرفه حق‌الوکاله)
    TIERS = [
        (Decimal("500000000"), Decimal("0.08")),    # تا ۵۰۰ میلیون ریال: ۸٪
        (Decimal("2000000000"), Decimal("0.07")),   # مازاد تا ۲ میلیارد ریال: ۷٪
        (Decimal("10000000000"), Decimal("0.05")),  # مازاد تا ۱۰ میلیارد ریال: ۵٪
        (Decimal("30000000000"), Decimal("0.04")),  # مازاد تا ۳۰ میلیارد ریال: ۴٪
    ]

    haghalvekaleh_kolli = Decimal("0")
    remaining = amount
    lower_bound = Decimal("0")

    for upper_bound, rate in TIERS:
        if remaining <= 0:
            break
        tier_span = upper_bound - lower_bound
        tier_amount = min(remaining, tier_span)
        haghalvekaleh_kolli += tier_amount * rate
        remaining -= tier_amount
        lower_bound = upper_bound

    if remaining > 0:
        raise ValueError(
            "بهای خواسته بیش از سقف تعرفه (۳۰ میلیارد ریال) است؛ "
            "نیاز به بررسی جداگانه دارد"
        )

    # تقسیم بین مرحله بدوی (۶۰٪) و تجدیدنظر (۴۰٪)
    haghalvekaleh_bedvi = haghalvekaleh_kolli * Decimal("0.6")
    haghalvekaleh_tajdidnazar = haghalvekaleh_kolli * Decimal("0.4")

    # تمبر مالیاتی = ۵٪ حق‌الوکاله هر مرحله — ماده ۱۰۳ ق.م.م
    tamber_bedvi = haghalvekaleh_bedvi * Decimal("0.05")
    tamber_tajdidnazar = haghalvekaleh_tajdidnazar * Decimal("0.05")
    tamber_kolli = tamber_bedvi + tamber_tajdidnazar

    def r(value: Decimal) -> int:
        return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    return {
        "tamber_kolli": r(tamber_kolli),
        "tamber_bedvi": r(tamber_bedvi),
        "tamber_tajdidnazar": r(tamber_tajdidnazar),
        "haghalvekaleh_kolli": r(haghalvekaleh_kolli),
        "haghalvekaleh_bedvi": r(haghalvekaleh_bedvi),
        "haghalvekaleh_tajdidnazar": r(haghalvekaleh_tajdidnazar),
    }


def format_result_fa(claim_amount: int, result: dict) -> str:
    """قالب‌بندی خروجی به فارسی برای ارسال در ربات بله"""

    def fmt(n: int) -> str:
        return f"{n:,}"

    return (
        f"📄 محاسبه برای بهای خواسته: *{fmt(claim_amount)} ریال*\n\n"
        f"🔹 تمبر کلی: *{fmt(result['tamber_kolli'])} ریال*\n"
        f"🔹 تمبر بدوی: *{fmt(result['tamber_bedvi'])} ریال*\n"
        f"🔹 تمبر تجدیدنظر: *{fmt(result['tamber_tajdidnazar'])} ریال*\n\n"
        f"🔸 حق‌الوکاله کلی: *{fmt(result['haghalvekaleh_kolli'])} ریال*\n"
        f"🔸 حق‌الوکاله بدوی: *{fmt(result['haghalvekaleh_bedvi'])} ریال*\n"
        f"🔸 حق‌الوکاله تجدیدنظر: *{fmt(result['haghalvekaleh_tajdidnazar'])} ریال*"
    )


# ══════════════════════════════════════════════════════════════════════════════
# ⭐ محاسبه تمبر بر اساس حق‌الوکاله (۱۴۰۵/۰۷) — نوع پرونده: حقوقی
# آیین‌نامه تعرفه حق‌الوکاله، حق‌المشاوره و هزینه سفر وکلای دادگستری مصوب
# ۱۳۹۸/۱۲/۲۸ رئیس قوه قضائیه: ماده ۲۱ (۶۰٪ نخستین / ۴۰٪ تجدیدنظر) و ماده ۲۹
# (سهم صندوق = ۵۰٪ تمبر، سهم کانون = ۲۵٪ تمبر). تمبر مالیاتی = ۵٪ حق‌الوکاله.
# ══════════════════════════════════════════════════════════════════════════════
LAWYER_FEE_STAMP_RATE = Decimal("0.05")
LAWYER_FEE_SHARE_RATE = Decimal("0.75")  # ۵۰٪ صندوق + ۲۵٪ کانون

LAWYER_FEE_LEGAL_BASIS = (
    "از آیین نامه تعرفه حق الوكاله، حق المشاوره و هزینه سفر وكلای دادگستری "
    "مصوب ۱۳۹۸/۱۲/۲۸ رئیس قوه قضائیه :\n\n"
    "ماده ۲۱- شصت درصد حق الوکاله موضوع مواد ۹، ۱۳ و ۲۰ این آیین نامه به مرحله "
    "نخستین و چهل درصد به مرحله تجدیدنظر تعلق می گیرد.\n\n"
    "ماده ۲۹- وکلای دادگستری عضو کانون های وکلا مکلفند معادل پنجاه درصد آنچه "
    "بابت مالیات طبق قانون مالیات ها تمبر به وکالتنامه الصاق می کنند، برای صندوق "
    "و یک چهارم تمبر را بابت هزینه کانون و وکلای دادگستری عضو مرکز مکلفند پنج "
    "درصد حق الوکاله بابت هزینه مرکز به امور مالی دادگستری پرداخت کنند. "
    "دادگستری مکلف است سهم صندوق و سهم کانون و سهم مرکز را از تمام وکلای کانون "
    "و مرکز در هر مورد قبول و در آخر هر ماه حسب مورد به کانون وکلای دادگستری "
    "مربوط و یا مرکز پرداخت کند."
)


def _round_rial(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def calculate_stamp_from_lawyer_fee(lawyer_fee: int) -> dict:
    """تمبر مالیاتی و سهم صندوق و کانون بر اساس مبلغ حق‌الوکاله (ریال).

    کلیدهای tamber_kolli/tamber_bedvi/tamber_tajdidnazar هم‌نام خروجی
    calculate_stamp_duty هستند تا در انتخاب «نوع تمبر» اعلام وکالت قابل استفاده باشند.
    """
    if lawyer_fee is None or lawyer_fee <= 0:
        raise ValueError("مبلغ حق‌الوکاله باید عددی مثبت باشد")

    fee = Decimal(str(lawyer_fee))

    def _stage(stage_fee: Decimal) -> dict:
        stamp = stage_fee * LAWYER_FEE_STAMP_RATE
        share = stamp * LAWYER_FEE_SHARE_RATE
        return {"fee": _round_rial(stage_fee), "stamp": _round_rial(stamp),
                "share": _round_rial(share), "total": _round_rial(stamp + share)}

    total = _stage(fee)
    first = _stage(fee * Decimal("0.6"))
    appeal = _stage(fee * Decimal("0.4"))
    return {
        "case_type": "حقوقی",
        "total": total, "first": first, "appeal": appeal,
        "tamber_kolli": total["stamp"],
        "tamber_bedvi": first["stamp"],
        "tamber_tajdidnazar": appeal["stamp"],
    }


_EN_TO_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _fa_rial(n: int) -> str:
    return f"{n:,}".replace(",", "٬").translate(_EN_TO_FA_DIGITS) + " ریال"


def format_lawyer_fee_result_fa(lawyer_fee: int, result: dict) -> str:
    """قالب خروجی محاسبه تمبر بر اساس حق‌الوکاله (مطابق نمونهٔ کارفرما)."""
    t, f, a = result["total"], result["first"], result["appeal"]
    lines = [
        ("حق الوکاله", t["fee"]),
        ("تمبر مالیاتی", t["stamp"]),
        ("سهم صندوق و کانون", t["share"]),
        ("تمبر مالیاتی + سهم صندوق و کانون", t["total"]),
        ("حق الوکاله نخستین", f["fee"]),
        ("تمبر نخستین", f["stamp"]),
        ("سهم صندوق و کانون (نخستین)", f["share"]),
        ("تمبر + سهم صندوق و کانون (نخستین)", f["total"]),
        ("حق الوکاله تجدیدنظر", a["fee"]),
        ("تمبر تجدیدنظر", a["stamp"]),
        ("سهم صندوق و کانون (تجدیدنظر)", a["share"]),
        ("تمبر + سهم صندوق و کانون (تجدیدنظر)", a["total"]),
    ]
    body = "\n\n".join(f"{label} :\n*{_fa_rial(val)}*" for label, val in lines)
    return (
        "✅ *نتیجه محاسبه :*\n\n"
        f"{body}\n\n"
        "📚 *مستندات :*\n\n"
        f"{LAWYER_FEE_LEGAL_BASIS}\n\n"
        "📝 *ورودی ها :*\n\n"
        f"مبلغ حق الوکاله : {_fa_rial(lawyer_fee)}\n\n"
        f"نوع پرونده : {result.get('case_type', 'حقوقی')}"
    )
