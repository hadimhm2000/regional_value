# -*- coding: utf-8 -*-
"""
تنظیمات مرکزی ربات — همه از متغیرهای محیطی خوانده می‌شوند.
"""
import os
import sys

# خواندن خودکار فایل .env (کنار همین فایل) — نیازی به set/export دستی نیست
from dotenv import load_dotenv
load_dotenv(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
    encoding="utf-8-sig",
)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    sys.exit(
        "❌ متغیر محیطی BOT_TOKEN تنظیم نشده است.\n"
        "لینوکس/مک:   export BOT_TOKEN=توکن_ربات_بله_شما\n"
        "ویندوز (cmd): set BOT_TOKEN=توکن_ربات_بله_شما"
    )

BALE_API_BASE = os.environ.get("BALE_API_BASE", "https://tapi.bale.ai")

NESHAN_API_KEY = os.environ.get("NESHAN_API_KEY", "")

# آیدی عددی ادمین در بله — دستورهای مدیریتی (/approve, /revoke, ...) فقط
# برای همین آیدی فعال است و پیام‌های خطا هم فقط برای همین آیدی ارسال می‌شود
_admin_id_raw = os.environ.get("ADMIN_ID", "")
try:
    ADMIN_ID = int(_admin_id_raw) if _admin_id_raw else None
except ValueError:
    ADMIN_ID = None

# توکن درگاه کیف‌پول بله — از پنل توسعه‌دهندگان بله (business.bale.ai) دریافت می‌شود
BALE_WALLET_TOKEN = os.environ.get("BALE_WALLET_TOKEN", "")

# ═══ اشتراک (برای کاربرانی که در لیست رایگانِ ادمین نیستند) ═══
# مبالغ به تومان؛ فاکتور بله به ریال صادر می‌شود (× ۱۰)
SUB_MONTHLY_TOMAN = int(os.environ.get("SUB_MONTHLY_TOMAN", "1200000"))    # ۱,۲۰۰,۰۰۰ تومان
SUB_YEARLY_TOMAN = int(os.environ.get("SUB_YEARLY_TOMAN", "10000000"))     # ۱۰,۰۰۰,۰۰۰ تومان
SUB_MONTHLY_DAYS = int(os.environ.get("SUB_MONTHLY_DAYS", "30"))
SUB_YEARLY_DAYS = int(os.environ.get("SUB_YEARLY_DAYS", "365"))

SUBSCRIPTION_PLANS = {
    "monthly": {"title": "اشتراک ماهانه", "toman": SUB_MONTHLY_TOMAN, "days": SUB_MONTHLY_DAYS},
    "yearly": {"title": "اشتراک سالانه", "toman": SUB_YEARLY_TOMAN, "days": SUB_YEARLY_DAYS},
}
for _p in SUBSCRIPTION_PLANS.values():
    _p["rial"] = _p["toman"] * 10

# ═══ اشتراک «خدمات کاربردی» (خسارت و مهریه، هزینه دادرسی، تمبر، ابزار فایل) ═══
# جدا از اشتراک استعلام ارزش منطقه‌ای؛ هر بخش UTIL_FREE_PER_TOOL بار رایگان است
UTIL_SUB_MONTHLY_TOMAN = int(os.environ.get("UTIL_SUB_MONTHLY_TOMAN", "800000"))    # ۸۰۰,۰۰۰ تومان
UTIL_SUB_YEARLY_TOMAN = int(os.environ.get("UTIL_SUB_YEARLY_TOMAN", "8000000"))     # ۸,۰۰۰,۰۰۰ تومان
UTIL_FREE_PER_TOOL = int(os.environ.get("UTIL_FREE_PER_TOOL", "1"))

UTIL_SUBSCRIPTION_PLANS = {
    "monthly": {"title": "اشتراک ماهانهٔ خدمات کاربردی", "toman": UTIL_SUB_MONTHLY_TOMAN, "days": SUB_MONTHLY_DAYS},
    "yearly": {"title": "اشتراک سالانهٔ خدمات کاربردی", "toman": UTIL_SUB_YEARLY_TOMAN, "days": SUB_YEARLY_DAYS},
}
for _p in UTIL_SUBSCRIPTION_PLANS.values():
    _p["rial"] = _p["toman"] * 10

# تعداد استعلام رایگانِ «تست» پس از تایید ادمین
TRIAL_CREDITS = int(os.environ.get("TRIAL_CREDITS", "2"))

# حداکثر تعداد استعلام هم‌زمان (جلوگیری از اورلود سایت مالیات/نشان و کرش ربات
# زیر بار سنگین کاربران زیاد)
MAX_CONCURRENT_QUERIES = int(os.environ.get("MAX_CONCURRENT_QUERIES", "10"))

# ═══ محدودیت نرخ درخواست هر کاربر (rate_limit.py) ═══
# حداکثر پیام / کلیک دکمه در هر RATE_LIMIT_WINDOW ثانیه؛ ۰ = بدون محدودیت. ادمین معاف است.
RATE_LIMIT_MESSAGES = int(os.environ.get("RATE_LIMIT_MESSAGES", "20"))
RATE_LIMIT_CALLBACKS = int(os.environ.get("RATE_LIMIT_CALLBACKS", "30"))
RATE_LIMIT_WINDOW = int(os.environ.get("RATE_LIMIT_WINDOW", "60"))

# تعداد thread workerهای اجرای درخواست‌های شبکه‌ای بلاک‌کننده (requests)
EXECUTOR_MAX_WORKERS = int(os.environ.get("EXECUTOR_MAX_WORKERS", "20"))

# مسیر فایل‌های ذخیرهٔ داده (لیست تاییدشده‌ها و اعتبار استعلام رایگان)
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
os.makedirs(DATA_DIR, exist_ok=True)
# پایگاه دادهٔ SQLite (کاربران، اشتراک‌ها، سابقهٔ استعلام، دفترها، آمار، وضعیت فرم‌ها)
DB_PATH = os.environ.get("DB_PATH", os.path.join(DATA_DIR, "bot.sqlite3"))

# فایل‌های JSON نسخه‌های قبلی — فقط برای انتقال خودکار به SQLite در اولین اجرا
APPROVED_USERS_FILE = os.path.join(DATA_DIR, "approved_users.json")
# اعتبار استعلام رایگان (استعلام‌های تست + جبران خطای سیستمی)
FREE_RETRY_FILE = os.path.join(DATA_DIR, "free_retries.json")
SUBSCRIPTIONS_FILE = os.path.join(DATA_DIR, "subscriptions.json")
TRIAL_REQUESTS_FILE = os.path.join(DATA_DIR, "trial_requests.json")

# نام کاربری ربات در بله (بدون @) — برای لینک QR تأیید اصالت
BOT_USERNAME = os.environ.get("BOT_USERNAME", "Regionalvalue_bot").lstrip("@")

# ═══ یادآوری تمدید ═══
# چند روز قبل از پایان اشتراک یادآوری ارسال شود
RENEW_REMIND_DAYS = int(os.environ.get("RENEW_REMIND_DAYS", "3"))
# فاصلهٔ بررسی اشتراک‌های رو به پایان (ثانیه)
RENEW_CHECK_INTERVAL = int(os.environ.get("RENEW_CHECK_INTERVAL", "3600"))

# ═══ سابقهٔ استعلام ═══
# تعداد استعلام‌های اخیر که در «سوابق استعلام» نمایش داده می‌شود
HISTORY_PAGE_SIZE = int(os.environ.get("HISTORY_PAGE_SIZE", "10"))
# حداکثر تعداد ملک در یک گزارش تجمیعی
BATCH_MAX_ITEMS = int(os.environ.get("BATCH_MAX_ITEMS", "15"))

# فایل اکسل اشتراک‌ها که پس از هر خرید/تغییر اشتراک خودکار به‌روز می‌شود (همان خروجی /subs_excel)
SUBS_EXCEL_PATH = os.environ.get("SUBS_EXCEL_PATH", os.path.join(DATA_DIR, "subscriptions.xlsx"))

# ═══ پشتیبان‌گیری خودکار ═══
BACKUP_HOUR = int(os.environ.get("BACKUP_HOUR", "3"))          # ساعت ارسال پشتیبان روزانه (وقت تهران)
BACKUP_KEEP = int(os.environ.get("BACKUP_KEEP", "7"))          # تعداد نسخه‌های نگه‌داشته‌شده روی سرور

# ═══ پایش سامانهٔ مالیاتی ═══
TAX_DOWN_THRESHOLD = int(os.environ.get("TAX_DOWN_THRESHOLD", "3"))   # چند خطای پیاپی = قطع
TAX_PROBE_INTERVAL = int(os.environ.get("TAX_PROBE_INTERVAL", "300"))  # بررسی دوباره هنگام قطعی (ثانیه)

# ═══ پیگیری خودکار تست‌گیرندگان ═══
TRIAL_FOLLOWUP_DAYS = int(os.environ.get("TRIAL_FOLLOWUP_DAYS", "3"))
TRIAL_FOLLOWUP_DISCOUNT = int(os.environ.get("TRIAL_FOLLOWUP_DISCOUNT", "10"))   # ۰ = بدون کد تخفیف
TRIAL_FOLLOWUP_CODE_DAYS = int(os.environ.get("TRIAL_FOLLOWUP_CODE_DAYS", "7"))

# ═══ هشدار استفادهٔ غیرعادی (احتمال اشتراک مشترک بین چند دفتر) ═══
USAGE_ALERT_DAILY = int(os.environ.get("USAGE_ALERT_DAILY", "50"))         # استعلام در ۲۴ ساعت
USAGE_ALERT_PROVINCES = int(os.environ.get("USAGE_ALERT_PROVINCES", "5"))  # تعداد استان مختلف در ۲۴ ساعت

# ═══ پیام همگانی ═══
BROADCAST_DELAY = float(os.environ.get("BROADCAST_DELAY", "0.07"))        # فاصلهٔ ارسال هر پیام (ثانیه)

# ═══ تصویر نقشه در PDF ارزش منطقه‌ای (سرویس نقشهٔ ایستای نشان، با همان NESHAN_API_KEY) ═══
NESHAN_STATIC_MAP = os.environ.get("NESHAN_STATIC_MAP", "1") not in ("0", "false", "no", "")
NESHAN_STATIC_MAP_KEY = os.environ.get("NESHAN_STATIC_MAP_KEY", "") or NESHAN_API_KEY
