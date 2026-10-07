# -*- coding: utf-8 -*-
"""
ops.py — منطق خالص (بدون aiogram) برای نگه‌داری و فروش:

  • کاربران: ثبت همهٔ کاربرانی که با ربات کار کرده‌اند (برای پیام همگانی)
  • پشتیبان‌گیری: فایل zip شامل نسخهٔ سازگار پایگاه داده + فایل‌های دادهٔ قابل‌تغییر
  • پایش سامانهٔ مالیاتی: شمارش خطاهای پیاپی، وضعیت قطع/وصل، فهرست منتظران خبر وصل شدن
  • گروه‌های مخاطب پیام همگانی
  • پیگیری تست‌گیرندگانی که خرید نکرده‌اند
  • هشدار استفادهٔ غیرعادی (احتمال اشتراک مشترک بین چند دفتر)
"""
from __future__ import annotations

import datetime
import glob
import json
import os
import tempfile
import time
import zipfile

import db

# ══════════════════════════════════════════════════════════════════
# کاربران
# ══════════════════════════════════════════════════════════════════
def touch_user(user_id: int, full_name: str = "", username: str = "") -> None:
    now = time.time()
    db.execute("INSERT INTO users(user_id, full_name, username, first_seen, last_seen, blocked) VALUES (?,?,?,?,?,0) "
               "ON CONFLICT(user_id) DO UPDATE SET full_name=excluded.full_name, username=excluded.username, "
               "last_seen=excluded.last_seen, blocked=0",
               (int(user_id), full_name or "", username or "", now, now))


def mark_blocked(user_id: int) -> None:
    db.execute("UPDATE users SET blocked=1 WHERE user_id=?", (int(user_id),))


# ══════════════════════════════════════════════════════════════════
# پشتیبان‌گیری
# ══════════════════════════════════════════════════════════════════
def _tehran_now() -> datetime.datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("Asia/Tehran"))
    except Exception:
        return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3, minutes=30)))


def backup_files() -> list:
    """فایل‌های دادهٔ قابل‌تغییر (غیر از پایگاه داده) که باید در پشتیبان باشند."""
    from config import DATA_DIR
    here = os.path.dirname(os.path.abspath(__file__))
    files = [os.path.join(here, "ayani_rates.json")]          # با /rates تغییر می‌کند
    for name in ("cpi_index.json", "diyeh_rates.json", "subscriptions.xlsx"):
        files.append(os.path.join(DATA_DIR, name))
    files += glob.glob(os.path.join(DATA_DIR, "rates_backups", "*.json"))
    return [f for f in files if os.path.isfile(f)]


def make_backup(keep: int = 7) -> str:
    """ساخت DATA_DIR/backups/backup_<تاریخ>.zip؛ نسخه‌های قدیمی‌تر از keep حذف می‌شوند. خروجی: مسیر فایل."""
    from config import DATA_DIR
    out_dir = os.path.join(DATA_DIR, "backups")
    os.makedirs(out_dir, exist_ok=True)
    stamp = _tehran_now().strftime("%Y%m%d_%H%M%S")
    zip_path = os.path.join(out_dir, f"backup_{stamp}.zip")
    tmp_db = os.path.join(tempfile.mkdtemp(prefix="rvbk_"), "bot.sqlite3")
    try:
        db.backup_to(tmp_db)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(tmp_db, "bot.sqlite3")
            for f in backup_files():
                arc = os.path.relpath(f, DATA_DIR) if f.startswith(DATA_DIR) else os.path.basename(f)
                z.write(f, arc)
            z.writestr("README.txt", "پشتیبان ربات استعلام ارزش منطقه‌ای\n"
                                     "بازگردانی: ربات را متوقف کنید، bot.sqlite3 و بقیهٔ فایل‌ها را در پوشهٔ data "
                                     "(و ayani_rates.json را کنار main.py) قرار دهید و ربات را دوباره اجرا کنید.\n")
    finally:
        try:
            os.remove(tmp_db)
            os.rmdir(os.path.dirname(tmp_db))
        except OSError:
            pass
    old = sorted(glob.glob(os.path.join(out_dir, "backup_*.zip")))
    for f in old[:-keep] if keep > 0 else []:
        try:
            os.remove(f)
        except OSError:
            pass
    db.kv_set("last_backup_at", time.time())
    return zip_path


def backup_due(hour: int, now: datetime.datetime = None) -> bool:
    """امروز (وقت تهران) از ساعت hour گذشته و پشتیبان امروز هنوز ارسال نشده؟"""
    now = now or _tehran_now()
    if now.hour < hour:
        return False
    return db.kv_get("last_backup_day") != now.strftime("%Y-%m-%d")


def mark_backup_sent(now: datetime.datetime = None) -> None:
    db.kv_set("last_backup_day", (now or _tehran_now()).strftime("%Y-%m-%d"))


# ══════════════════════════════════════════════════════════════════
# پایش سامانهٔ مالیاتی
# ══════════════════════════════════════════════════════════════════
def tax_record(ok: bool, threshold: int) -> str | None:
    """
    ثبت نتیجهٔ یک استعلام از سامانه. خروجی:
      "down" — همین الان قطع تشخیص داده شد (برای هشدار به ادمین)
      "up"   — همین الان دوباره وصل شد
      None   — تغییری نکرد
    """
    down = db.kv_get("tax_down_since")
    if ok:
        db.kv_set("tax_fail_streak", 0)
        if down:
            db.kv_set("tax_down_since", None)
            db.kv_set("tax_last_downtime", time.time() - float(down))
            return "up"
        return None
    streak = int(db.kv_get("tax_fail_streak", 0) or 0) + 1
    db.kv_set("tax_fail_streak", streak)
    if not down and streak >= threshold:
        db.kv_set("tax_down_since", time.time())
        return "down"
    return None


def tax_is_down() -> bool:
    return bool(db.kv_get("tax_down_since"))


def tax_down_since():
    v = db.kv_get("tax_down_since")
    return float(v) if v else None


def tax_add_waiter(user_id: int) -> None:
    waiters = set(json.loads(db.kv_get("tax_waiters") or "[]"))
    waiters.add(int(user_id))
    db.kv_set("tax_waiters", json.dumps(sorted(waiters)))


def tax_pop_waiters() -> list:
    waiters = json.loads(db.kv_get("tax_waiters") or "[]")
    db.kv_set("tax_waiters", "[]")
    return waiters


# ══════════════════════════════════════════════════════════════════
# گروه‌های مخاطب پیام همگانی
# ══════════════════════════════════════════════════════════════════
SEGMENTS = {
    "all": "همهٔ کاربران",
    "rv_active": "مشترکان فعال ارزش منطقه‌ای",
    "util_active": "مشترکان فعال خدمات کاربردی",
    "expired": "اشتراک منقضی‌شده (بدون اشتراک فعال)",
    "trial_no_buy": "تست گرفته ولی هیچ خریدی نکرده",
    "never_paid": "هیچ‌وقت خرید نکرده (غیر از کاربران رایگان)",
}


def segment_users(segment: str, now: float = None) -> list:
    now = now or time.time()
    q = {
        "all": "SELECT user_id FROM users WHERE blocked=0",
        "rv_active": "SELECT user_id FROM subscriptions WHERE expires_at > :now",
        "util_active": "SELECT user_id FROM util_subscriptions WHERE expires_at > :now",
        "expired": ("SELECT user_id FROM (SELECT user_id FROM subscriptions UNION SELECT user_id FROM util_subscriptions) "
                    "WHERE user_id NOT IN (SELECT user_id FROM subscriptions WHERE expires_at > :now) "
                    "AND user_id NOT IN (SELECT user_id FROM util_subscriptions WHERE expires_at > :now) "
                    "AND user_id NOT IN (SELECT user_id FROM approved_users)"),
        "trial_no_buy": ("SELECT user_id FROM trial_requests WHERE status='approved' "
                         "AND user_id NOT IN (SELECT user_id FROM payments) "
                         "AND user_id NOT IN (SELECT user_id FROM approved_users)"),
        "never_paid": ("SELECT user_id FROM users WHERE blocked=0 AND user_id NOT IN (SELECT user_id FROM payments) "
                       "AND user_id NOT IN (SELECT user_id FROM approved_users) "
                       "AND user_id NOT IN (SELECT user_id FROM subscriptions WHERE expires_at > :now) "
                       "AND user_id NOT IN (SELECT user_id FROM util_subscriptions WHERE expires_at > :now)"),
    }[segment]
    blocked = {r["user_id"] for r in db.query("SELECT user_id FROM users WHERE blocked=1")}
    return [r["user_id"] for r in db.query(q, {"now": now}) if r["user_id"] not in blocked]


# ══════════════════════════════════════════════════════════════════
# پیگیری تست‌گیرندگان
# ══════════════════════════════════════════════════════════════════
def trial_followup_candidates(days: int, now: float = None) -> list:
    """
    تست تاییدشده، حداقل days روز از تایید گذشته، استعلام رایگانش تمام شده، هیچ اشتراک/پرداختی ندارد،
    کاربر رایگان ادمین نیست و پیام پیگیری قبلاً نگرفته.
    """
    now = now or time.time()
    rows = db.query(
        "SELECT user_id FROM trial_requests WHERE status='approved' AND decided_at IS NOT NULL AND decided_at <= ? "
        "AND user_id NOT IN (SELECT user_id FROM free_credits WHERE count > 0) "
        "AND user_id NOT IN (SELECT user_id FROM subscriptions) "
        "AND user_id NOT IN (SELECT user_id FROM payments) "
        "AND user_id NOT IN (SELECT user_id FROM approved_users) "
        "AND user_id NOT IN (SELECT user_id FROM followups WHERE kind='trial')",
        (now - days * 86400,))
    return [r["user_id"] for r in rows]


def mark_followup(user_id: int, kind: str) -> None:
    db.execute("INSERT OR IGNORE INTO followups(user_id, kind, sent_at) VALUES (?,?,?)",
               (int(user_id), kind, time.time()))


# ══════════════════════════════════════════════════════════════════
# استفادهٔ غیرعادی
# ══════════════════════════════════════════════════════════════════
def usage_alert(user_id: int, daily: int, provinces: int, now: float = None):
    """
    اگر کاربر در ۲۴ ساعت گذشته بیش از حد استعلام گرفته یا از استان‌های زیادی، و امروز هنوز هشدار
    داده نشده، خلاصهٔ آن را برمی‌گرداند (برای پیام ادمین)؛ وگرنه None.
    """
    now = now or time.time()
    r = db.query_one("SELECT COUNT(*) n, COUNT(DISTINCT province) p FROM queries WHERE user_id=? AND created_at>?",
                     (int(user_id), now - 86400))
    if r["n"] < daily and r["p"] < provinces:
        return None
    day = _tehran_now().strftime("%Y-%m-%d")
    if db.execute("INSERT OR IGNORE INTO usage_alerts(user_id, day) VALUES (?,?)", (int(user_id), day)) == 0:
        return None
    top = db.query("SELECT province, COUNT(*) n FROM queries WHERE user_id=? AND created_at>? GROUP BY province "
                   "ORDER BY n DESC LIMIT 6", (int(user_id), now - 86400))
    return {"count": r["n"], "provinces": r["p"], "top": [(x["province"], x["n"]) for x in top]}
