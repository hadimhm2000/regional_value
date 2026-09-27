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

# تعداد استعلام رایگانِ «تست» پس از تایید ادمین
TRIAL_CREDITS = int(os.environ.get("TRIAL_CREDITS", "2"))

# حداکثر تعداد استعلام هم‌زمان (جلوگیری از اورلود سایت مالیات/نشان و کرش ربات
# زیر بار سنگین کاربران زیاد)
MAX_CONCURRENT_QUERIES = int(os.environ.get("MAX_CONCURRENT_QUERIES", "10"))

# تعداد thread workerهای اجرای درخواست‌های شبکه‌ای بلاک‌کننده (requests)
EXECUTOR_MAX_WORKERS = int(os.environ.get("EXECUTOR_MAX_WORKERS", "20"))

# مسیر فایل‌های ذخیرهٔ داده (لیست تاییدشده‌ها و اعتبار استعلام رایگان)
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
os.makedirs(DATA_DIR, exist_ok=True)
APPROVED_USERS_FILE = os.path.join(DATA_DIR, "approved_users.json")
# اعتبار استعلام رایگان (استعلام‌های تست + جبران خطای سیستمی)
FREE_RETRY_FILE = os.path.join(DATA_DIR, "free_retries.json")
SUBSCRIPTIONS_FILE = os.path.join(DATA_DIR, "subscriptions.json")
TRIAL_REQUESTS_FILE = os.path.join(DATA_DIR, "trial_requests.json")
