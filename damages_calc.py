# -*- coding: utf-8 -*-
"""
محاسبهٔ «خسارت تأخیر تأدیه» و «مهریه به نرخ روز» بر اساس شاخص بهای کالاها و
خدمات مصرفی (تورم) بانک مرکزی.

این ماژول هیچ وابستگی به aiogram ندارد (منطق خالص) — مثل ayani_calc.py.

سری شاخص مورد استفاده: شاخص ماهانه با سال پایهٔ ۱۳۹۵=۱۰۰ (همان سری که در
محاسبات خسارت تأخیر تأدیه و سامانه‌هایی مثل دادحساب استفاده می‌شود). از سه
منبع ساخته می‌شود:
  ۱) assets/cpi_judicial_1395.json (در گیت): جدول دادحساب، فروردین ۱۳۶۱ تا آخرین ماه فایل
     (ماه‌های تا "approximate_until" = ۱۳۶۹/۱۲ فقط یک رقم اعشار دارند ← نتیجه با هشدار «تقریبی»)
  ۲) cpi_index.json ← "overrides": مقادیری که مدیر با /cpi یا اکسل ثبت/اصلاح کرده
  ۳) cpi_index.json ← "monthly_1400": جدول ۱۴۰۰=۱۰۰ که هر ماه خودکار از PDF
     بانک مرکزی (cbi.ir/simplelist/1611.aspx، cpi_fetcher.py) خوانده می‌شود.
     ماه‌های بعد از آخرین مقدار ۱۳۹۵ به‌صورت زنجیره‌ای ساخته می‌شوند:
       شاخص۱۳۹۵[ماه] = شاخص۱۳۹۵[ماه پیوند] × شاخص۱۴۰۰[ماه] ÷ شاخص۱۴۰۰[ماه پیوند]
     (ماه پیوند = آخرین ماهی که در هر دو سری موجود است؛ گرد به یک رقم اعشار)

cpi_index.json (در پوشهٔ دادهٔ ربات DATA_DIR، در گیت نیست):
    {
      "overrides":    {"1405/04": 3206.6, ...},   ← پایهٔ ۱۳۹۵
      "annual_1395":  {"1365": 0.85, ...},          ← شاخص سالانهٔ مهریه (اختیاری)
      "monthly_1400": {"1400/01": 83.3, ...},       ← جدول PDF بانک مرکزی
      "source_pdf": "...", "fetched_at": "...", "updated_at": "..."
    }
کلیدهای قدیمی "monthly"/"annual" (نسخهٔ قبل، با سال پایهٔ نامشخص) نادیده گرفته می‌شوند.

قواعد محاسبه:
  خسارت تأخیر تأدیه (مادهٔ ۵۲۲ قانون آیین دادرسی مدنی):
      مبلغ به‌روز = مبلغ × (شاخص ماه پرداخت ÷ شاخص ماه سررسید)
      خسارت = مبلغ به‌روز − مبلغ
      اگر شاخص ماه پرداخت هنوز منتشر نشده، نزدیک‌ترین شاخص موجود قبل از آن
      استفاده و در نتیجه اعلام می‌شود (مثل دادحساب: پرداخت مهر ۱۴۰۵ ← شهریور ۱۴۰۵).
  مهریه به نرخ روز (تبصرهٔ مادهٔ ۱۰۸۲ قانون مدنی):
      مهریهٔ به‌روز = مبلغ × (شاخص سال قبل از سال تأدیه ÷ شاخص سال وقوع عقد)
      شاخص سالانه، به ترتیب اولویت:
        ۱) ثبت دستی مدیر (cpi_index.json ← "annual_1395"، با /cpi_year)
        ۲) جدول سالانهٔ رسمی در گیت (assets/cpi_annual_1395.json ← "annual")
           — بانک مرکزی شاخص سالانه را از ۱۳۱۵ اعلام کرده (پیوست نامه‌های سالانه به
           قوه قضاییه)؛ این فایل با tools/import_annual_index.py ساخته می‌شود.
        ۳) میانگین ۱۲ ماه همان سال از سری ماهانه (فقط ۱۳۷۵ به بعد).
      صحت روش ۳: میانگین ماه‌های ۱۴۰۰ = ۴۳۷٫۰۴۲؛ عدد رسمی اعلام‌شده = ۴۳۷٫۰۳۲.
"""
import datetime
import json
import re
import os
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
# همان پوشهٔ دادهٔ config.py (بدون import کردن config تا این ماژول مستقل بماند)
_DATA_DIR = os.environ.get("DATA_DIR", os.path.join(_HERE, "data"))
CPI_FILE = os.path.join(_DATA_DIR, "cpi_index.json")
SEED_FILE = os.path.join(_HERE, "assets", "cpi_judicial_1395.json")
ANNUAL_SEED_FILE = os.path.join(_HERE, "assets", "cpi_annual_1395.json")

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def normalize_digits(text: str) -> str:
    """ارقام فارسی/عربی → انگلیسی؛ حذف فاصله و نیم‌فاصله."""
    return (text or "").translate(_DIGITS).replace(" ", "").replace("\u200c", "")
CPI_BASE = "1395=100"

_lock = threading.Lock()


# ── تاریخ شمسی ───────────────────────────────────────────────────────────

def today_jalali() -> tuple[int, int, int]:
    """تاریخ امروز (تهران) به شمسی."""
    tehran = datetime.timezone(datetime.timedelta(hours=3, minutes=30))
    now = datetime.datetime.now(tehran)
    try:
        import jdatetime
        j = jdatetime.date.fromgregorian(year=now.year, month=now.month, day=now.day)
        return j.year, j.month, j.day
    except ImportError:
        from regional_value_pdf import _gregorian_to_jalali
        return _gregorian_to_jalali(now.year, now.month, now.day)


def month_key(year: int, month: int) -> str:
    return f"{year:04d}/{month:02d}"


def prev_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def parse_jalali_date(text: str) -> tuple[int, int, int] | None:
    """«۱۴۰۳/۰۵/۱۲» یا «1403-5-12» → (1403, 5, 12)؛ ورودی نامعتبر → None"""
    raw = normalize_digits(text or "").replace("-", "/").replace(".", "/")
    parts = [p for p in raw.split("/") if p]
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    y, m, d = (int(p) for p in parts)
    if y < 100:
        y += 1400 if y < 50 else 1300
    if not (1300 <= y <= 1500 and 1 <= m <= 12 and 1 <= d <= 31):
        return None
    if m > 6 and d > 30:
        return None
    return y, m, d


# جداکننده‌های هزارگان که کاربران تایپ می‌کنند: 258.000.000 / 258,000,000 / ۲۵۸٬۰۰۰٬۰۰۰ / ۲۵۸ ۰۰۰ ۰۰۰
_THOUSANDS_SEPS = ".,،٬٫'’_-"
_AMOUNT_GROUPED = re.compile(r"\d{1,3}(?:[" + re.escape(_THOUSANDS_SEPS) + r"]\d{3})+")
_AMOUNT_UNITS = ("ریال", "ريال", "rial", "Rial", "RIAL")


def parse_amount(text: str) -> int | None:
    """
    مبلغ (ریال) با هر نوع جداکنندهٔ سه‌رقمی یا بدون آن:
      258000000 | 258.000.000 | 258,000,000 | ۲۵۸٬۰۰۰٬۰۰۰ | ۲۵۸ ۰۰۰ ۰۰۰ | 258.000.000 ریال
    عدد اعشاری یا گروه‌بندی نامعتبر (مثل 25.5 یا 2.58.000) پذیرفته نمی‌شود.
    """
    raw = normalize_digits(text or "")
    for unit in _AMOUNT_UNITS:
        raw = raw.replace(unit, "")
    raw = raw.strip()
    if raw.isdigit():
        value = int(raw)
    elif _AMOUNT_GROUPED.fullmatch(raw):
        value = int(re.sub(r"\D", "", raw))
    else:
        return None
    return value if value > 0 else None


def format_dotted(n: int) -> str:
    """۲۵۸۰۰۰۰۰۰ → «258.000.000» (همان شکلی که کاربران مبلغ را می‌نویسند)."""
    return f"{n:,}".replace(",", ".")


def amount_confirm_line(amount: int, back_text: str = None) -> str:
    """خط تأیید مبلغ واردشده به ریال و تومان؛ کاربر اگر اشتباه زده باشد فوراً می‌فهمد."""
    toman = format_dotted(amount // 10) + (f"٫{amount % 10}" if amount % 10 else "")
    line = f"✅ مبلغ واردشده: *{format_dotted(amount)} ریال* (معادل {toman} تومان)"
    if back_text:
        line += f"\n↩️ اگر اشتباه است، «{back_text}» را بزنید."
    return line


# ── دادهٔ شاخص ───────────────────────────────────────────────────────────

def _load_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_cpi() -> dict:
    """دادهٔ خام: seed (در گیت) + cpi_index.json (اصلاحات مدیر و جدول ۱۴۰۰ بانک مرکزی)."""
    with _lock:
        seed = _load_json(SEED_FILE)
        annual_seed = _load_json(ANNUAL_SEED_FILE)
        data = _load_json(CPI_FILE)
    data.setdefault("overrides", {})
    data.setdefault("annual_1395", {})
    data.setdefault("monthly_1400", {})
    data["seed"] = seed.get("monthly", {})
    data["approximate_until"] = seed.get("approximate_until", "")
    data["seed_annual"] = {str(k): float(v) for k, v in annual_seed.get("annual", {}).items()}
    return data


def save_cpi(data: dict):
    data = {k: v for k, v in data.items() if k not in ("seed", "seed_annual", "approximate_until")}
    data["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    tmp = CPI_FILE + ".tmp"
    os.makedirs(os.path.dirname(CPI_FILE), exist_ok=True)
    with _lock:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, CPI_FILE)


def judicial_series(data: dict | None = None) -> tuple[dict[str, float], set[str], str | None]:
    """
    سری ماهانهٔ ۱۳۹۵=۱۰۰.
    خروجی: (ماه ← شاخص، ماه‌هایی که زنجیره‌ای از جدول ۱۴۰۰ ساخته شده‌اند، ماه پیوند)
    """
    data = data or load_cpi()
    series = dict(data["seed"])
    series.update(data["overrides"])
    c1400 = data["monthly_1400"]
    common = [k for k in series if k in c1400]
    if not series or not common:
        return series, set(), None
    link = max(common)
    last = max(series)
    chained = set()
    for k in sorted(c1400):
        if k > last:
            series[k] = round(series[link] * c1400[k] / c1400[link], 1)
            chained.add(k)
    return series, chained, link


def set_monthly(year: int, month: int, value: float):
    """ثبت/اصلاح دستی یک ماه (پایهٔ ۱۳۹۵=۱۰۰)."""
    data = load_cpi()
    data["overrides"][month_key(year, month)] = float(value)
    save_cpi(data)


def set_monthly_1400(values: dict[str, float], source: str = "") -> list[str]:
    """ذخیرهٔ جدول ۱۴۰۰=۱۰۰ خوانده‌شده از PDF بانک مرکزی. خروجی: ماه‌های تازه‌اضافه‌شدهٔ سری."""
    data = load_cpi()
    before = set(judicial_series(data)[0])
    data["monthly_1400"].update({k: float(v) for k, v in values.items()})
    data["source_pdf"] = source
    data["fetched_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    save_cpi(data)
    return sorted(set(judicial_series(data)[0]) - before)


def import_rows(rows: list[tuple], replace: bool = False) -> tuple[int, int]:
    """ورود دسته‌ای (پایهٔ ۱۳۹۵): هر سطر (سال، ماه یا None، شاخص). خروجی: (تعداد ماهانه، تعداد سالانه)."""
    data = load_cpi()
    if replace:
        data["overrides"], data["annual_1395"] = {}, {}
    n_month = n_year = 0
    for year, month, value in rows:
        if month:
            data["overrides"][month_key(int(year), int(month))] = float(value)
            n_month += 1
        else:
            data["annual_1395"][str(int(year))] = float(value)
            n_year += 1
    save_cpi(data)
    return n_month, n_year


def latest_month(data: dict | None = None) -> tuple[int, int] | None:
    series = judicial_series(data)[0]
    if not series:
        return None
    y, m = max(series).split("/")
    return int(y), int(m)


def missing_required_month(today: tuple[int, int, int] | None = None) -> tuple[int, int] | None:
    """ماه قبل از ماه جاری اگر شاخصش هنوز موجود نیست (برای دریافت خودکار و یادآوری به مدیر)."""
    y, m, _ = today or today_jalali()
    py, pm = prev_month(y, m)
    if month_key(py, pm) in judicial_series()[0]:
        return None
    return py, pm


def first_annual_year(data: dict | None = None) -> int | None:
    """قدیمی‌ترین سالی که شاخص سالانه‌اش (ثبت‌شده یا قابل محاسبه از ماه‌ها) موجود است."""
    data = data or load_cpi()
    years = [int(y) for y in data["annual_1395"]] + [int(y) for y in data.get("seed_annual", {})]
    series = judicial_series(data)[0]
    if series:
        y0 = int(min(series)[:4])
        years.append(y0 if annual_index(y0, data) is not None else y0 + 1)
    return min(years) if years else None


def annual_index(year: int, data: dict | None = None) -> float | None:
    data = data or load_cpi()
    if str(year) in data["annual_1395"]:
        return data["annual_1395"][str(year)]
    if str(year) in data.get("seed_annual", {}):
        return data["seed_annual"][str(year)]
    series = judicial_series(data)[0]
    values = [series.get(month_key(year, m)) for m in range(1, 13)]
    if all(v is not None for v in values):
        return round(sum(values) / 12, 3)
    return None


# ── محاسبات ──────────────────────────────────────────────────────────────

class CpiMissing(Exception):
    """شاخص لازم برای محاسبه وارد نشده است."""


def calc_late_payment(amount: int, due: tuple[int, int, int],
                      calc_date: tuple[int, int, int] | None = None) -> dict:
    """calc_date = تاریخ پرداخت (یا امروز)."""
    data = load_cpi()
    series, chained, _ = judicial_series(data)
    calc_date = calc_date or today_jalali()
    due_key = month_key(due[0], due[1])
    if (due[0], due[1], due[2]) >= calc_date:
        raise ValueError("تاریخ سررسید باید قبل از تاریخ پرداخت/محاسبه باشد.")
    if due_key not in series:
        if series and due_key < min(series):
            raise ValueError(f"شاخص ماهانه از {min(series)} به بعد موجود است؛ "
                             "سررسید قدیمی‌تر قابل محاسبه نیست.")
        raise CpiMissing(f"شاخص ماه سررسید ({due_key}) وارد نشده است.")

    pay_key = month_key(calc_date[0], calc_date[1])
    target_key = pay_key
    used_latest = False
    if target_key not in series:
        available = [k for k in series if k <= pay_key]
        target_key, used_latest = max(available), True     # due_key موجود است پس خالی نیست

    base_idx = series[due_key]
    target_idx = series[target_key]
    updated = round(amount * target_idx / base_idx)
    return {
        "amount": amount,
        "due_key": due_key,
        "pay_key": pay_key,
        "target_key": target_key,
        "base_index": base_idx,
        "target_index": target_idx,
        "updated_amount": updated,
        "damages": updated - amount,
        "used_latest_available": used_latest,
        "chained": target_key in chained,
        "approximate": bool(data["approximate_until"]) and due_key <= data["approximate_until"],
        "cpi_base": CPI_BASE,
    }


MAHR_BASIS = ("payment", "death")


def calc_mahrieh(amount: int, marriage_year: int, payment_year: int | None = None,
                 basis: str = "payment") -> dict:
    """
    basis="payment": payment_year = سال تأدیه (پیش‌فرض: سال جاری) — مادهٔ ۲ آیین‌نامه
    basis="death":   payment_year = سال فوت زوج؛ مهریه از ترکه — مادهٔ ۳ آیین‌نامه
    در هر دو حالت شاخص «سال قبل» از سال مبنا اعمال می‌شود.
    """
    if basis not in MAHR_BASIS:
        raise ValueError(f"basis نامعتبر: {basis}")
    data = load_cpi()
    this_year = today_jalali()[0]
    payment_year = payment_year or this_year
    event = "فوت زوج" if basis == "death" else "پرداخت"
    if payment_year > this_year:
        raise ValueError(f"سال {event} نمی‌تواند بعد از سال جاری ({this_year}) باشد.")
    if payment_year <= marriage_year:
        raise ValueError(f"سال {event} باید بعد از سال وقوع عقد باشد.")
    target_year = payment_year - 1
    base_idx = annual_index(marriage_year, data)
    if base_idx is None:
        first = first_annual_year(data)
        hint = f" (شاخص سالانه از سال {first} به بعد موجود است)" if first else ""
        raise CpiMissing(f"شاخص سالانهٔ سال {marriage_year} وارد نشده است{hint}.")
    target_idx = annual_index(target_year, data)
    if target_idx is None:
        raise CpiMissing(f"شاخص سالانهٔ سال {target_year} وارد نشده است.")
    updated = round(amount * target_idx / base_idx)
    return {
        "amount": amount,
        "marriage_year": marriage_year,
        "payment_year": payment_year,
        "basis": basis,
        "target_year": target_year,
        "base_index": base_idx,
        "target_index": target_idx,
        "updated_amount": updated,
        "cpi_base": CPI_BASE,
    }
