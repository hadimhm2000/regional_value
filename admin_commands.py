# -*- coding: utf-8 -*-
"""
admin_commands.py — دستورهای مدیریتی، فقط برای ADMIN_ID.

/approve <آیدی_عددی>   — افزودن کاربر به لیست تاییدشده (دسترسی رایگان)
/revoke  <آیدی_عددی>   — حذف کاربر از لیست تاییدشده
/approved               — نمایش لیست کاربران تاییدشده
/subs                   — نمایش مشترکان فعال
/addsub <آیدی> <monthly|yearly> — فعال‌سازی/تمدید دستی اشتراک
/credit <آیدی> <تعداد>  — افزودن استعلام رایگان
/stats                  — آمار استفاده، قیف تبدیل، فروش و خطاها
/rates                  — دریافت اکسل نرخ اعیانی فعلی؛ ارسال اکسل ویرایش‌شده = به‌روزرسانی (با تایید)
/rates_rollback         — بازگرداندن نسخهٔ قبلی نرخ‌ها
/subs_excel             — اکسل همهٔ مشترکان (با نام و کد دفتر)؛ ارسال اکسل ویرایش‌شده = افزودن/تغییر اشتراک‌ها (با تایید)
/diyeh_rate [سال مبلغ]  — نمایش/ثبت مبلغ دیهٔ کامل سال (ریال)
/office <آیدی> <کد دفتر> <نام دفتر> — ثبت نام و کد دفتر خدمات قضایی برای یک آیدی
/admin                  — فهرست همهٔ دستورهای مدیر
(پیام همگانی، کد تخفیف، بکاپ، وضعیت سامانهٔ مالیاتی و پشتیبانی: ops_handlers.py)
دکمه‌های «✅ تایید / ❌ رد» زیر پیام درخواست تست — تایید = ۲ استعلام رایگان
"""
import logging

from aiogram import Router
from aiogram.filters import Command
import time

from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import ADMIN_ID, SUBSCRIPTION_PLANS, TRIAL_CREDITS
import access_control

logger = logging.getLogger(__name__)

admin_router = Router()


def _is_admin(message: Message) -> bool:
    return ADMIN_ID is not None and message.from_user.id == ADMIN_ID


@admin_router.message(Command("approve"))
async def cmd_approve(message: Message):
    if not _is_admin(message):
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("فرمت درست: /approve آیدی_عددی_کاربر")
        return
    user_id = int(parts[1])
    added = await access_control.approve_user(user_id)
    access_control.refresh_subs_excel()
    if added:
        await message.answer(f"✅ کاربر {user_id} تایید شد و از این پس رایگان استفاده می‌کند.")
        try:
            await message.bot.send_message(
                user_id, "✅ دسترسی شما توسط مدیر تایید شد. از این پس بدون نیاز به پرداخت می‌توانید استعلام بگیرید."
            )
        except Exception as e:
            logger.warning(f"[ADMIN] اطلاع‌رسانی تایید به کاربر {user_id} ناموفق بود: {e}")
    else:
        await message.answer(f"ℹ️ کاربر {user_id} از قبل در لیست تاییدشده‌ها بود.")


@admin_router.message(Command("revoke"))
async def cmd_revoke(message: Message):
    if not _is_admin(message):
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("فرمت درست: /revoke آیدی_عددی_کاربر")
        return
    user_id = int(parts[1])
    removed = await access_control.revoke_user(user_id)
    access_control.refresh_subs_excel()
    if removed:
        await message.answer(f"🗑 تایید کاربر {user_id} لغو شد.")
    else:
        await message.answer(f"ℹ️ کاربر {user_id} اصلاً در لیست تاییدشده‌ها نبود.")


@admin_router.message(Command("approved"))
async def cmd_list_approved(message: Message):
    if not _is_admin(message):
        return
    ids = await access_control.list_approved()
    if not ids:
        await message.answer("لیست کاربران تاییدشده خالی است.")
        return
    text = "\n".join(f"• {uid}" for uid in ids)
    await message.answer(f"👥 کاربران تاییدشده ({len(ids)} نفر):\n{text}")



# ══════════════════════════════════════════════════════════════════
# تایید / رد درخواست تست (دکمه‌های اینلاین زیر پیام ادمین)
# ══════════════════════════════════════════════════════════════════
@admin_router.callback_query(F.data.startswith("trial_"))
async def cb_trial_decision(callback: CallbackQuery):
    if ADMIN_ID is None or callback.from_user.id != ADMIN_ID:
        await callback.answer("دسترسی ندارید.")
        return
    try:
        action, uid_raw = callback.data.split(":", 1)
        user_id = int(uid_raw)
    except (ValueError, AttributeError):
        await callback.answer("دادهٔ نامعتبر.")
        return
    approve = action == "trial_ok"

    req = await access_control.decide_trial_request(user_id, approve)
    if req is None:
        await callback.answer("این درخواست قبلاً بررسی شده است.")
        return

    from regional_value_handlers import CONTINUE_BUTTON
    from keyboards import main_menu_kb, nav_row
    from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

    if approve:
        await access_control.grant_free_retry(user_id, TRIAL_CREDITS)
        user_text = (
            "✅ اطلاعات شما توسط ادمین تایید شد.\n\n"
            f"🎁 {TRIAL_CREDITS} استعلام کاملاً رایگان برای شما در نظر گرفته شد.\n"
            "برای ادامهٔ استعلام، دکمهٔ «ادامه استعلام» را بزنید."
        )
        kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=CONTINUE_BUTTON)], nav_row()], resize_keyboard=True)
        result_line = f"✅ تایید شد — {TRIAL_CREDITS} استعلام رایگان ثبت شد"
    else:
        user_text = (
            "❌ متاسفانه اطلاعات ارسالی شما توسط ادمین تایید نشد.\n"
            "در صورت نیاز، می‌توانید اطلاعات صحیح را دوباره ارسال کنید."
        )
        kb = main_menu_kb()
        result_line = "❌ رد شد"

    try:
        await callback.bot.send_message(user_id, user_text, reply_markup=kb)
    except Exception as e:
        logger.warning(f"[ADMIN] اطلاع‌رسانی نتیجهٔ تست به کاربر {user_id} ناموفق بود: {e}")
        result_line += " (ارسال پیام به کاربر ناموفق بود)"

    try:
        await callback.message.edit_text(f"{callback.message.text}\n\n{result_line}")
    except Exception:
        try:
            await callback.message.answer(f"{result_line} — کاربر {user_id}")
        except Exception:
            pass
    await callback.answer(result_line)


@admin_router.message(Command("subs"))
async def cmd_subs(message: Message):
    if not _is_admin(message):
        return
    subs = await access_control.list_subscriptions()
    now = time.time()
    active = [(uid, s) for uid, s in subs.items() if s.get("expires_at", 0) > now]
    if not active:
        await message.answer("هیچ اشتراک فعالی وجود ندارد.")
        return
    lines = []
    for uid, s in sorted(active, key=lambda x: x[1]["expires_at"]):
        office = access_control.get_office_info(int(uid))
        label = " — ".join(x for x in (office.get("office_name"), office.get("office_code")) if x)
        lines.append(f"• {uid}{' (' + label + ')' if label else ''} — {access_control.plan_title(s.get('plan'))} — "
                     f"{-int(-(s['expires_at'] - now) // 86400)} روز باقی‌مانده")
    await message.answer(f"💳 مشترکان فعال ({len(active)}):\n" + "\n".join(lines)
                         + "\n\nفهرست کامل و قابل ویرایش: /subs_excel")


@admin_router.message(Command("addsub"))
async def cmd_addsub(message: Message):
    if not _is_admin(message):
        return
    parts = (message.text or "").split()
    if len(parts) != 3 or not parts[1].isdigit() or parts[2] not in SUBSCRIPTION_PLANS:
        await message.answer("فرمت درست: /addsub آیدی_عددی monthly یا yearly")
        return
    user_id, plan = int(parts[1]), parts[2]
    p = SUBSCRIPTION_PLANS[plan]
    sub = await access_control.activate_subscription(user_id, plan, p["days"])
    days_left = -int(-(sub["expires_at"] - time.time()) // 86400)
    await message.answer(f"✅ {p['title']} برای کاربر {user_id} فعال شد ({days_left} روز).")
    try:
        await message.bot.send_message(user_id, f"✅ {p['title']} شما توسط مدیر فعال شد.")
    except Exception as e:
        logger.warning(f"[ADMIN] اطلاع‌رسانی اشتراک به کاربر {user_id} ناموفق بود: {e}")


@admin_router.message(Command("credit"))
async def cmd_credit(message: Message):
    if not _is_admin(message):
        return
    parts = (message.text or "").split()
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await message.answer("فرمت درست: /credit آیدی_عددی تعداد")
        return
    user_id, n = int(parts[1]), int(parts[2])
    await access_control.grant_free_retry(user_id, n)
    await message.answer(f"✅ {n} استعلام رایگان برای کاربر {user_id} ثبت شد.")


# ══════════════════════════════════════════════════════════════════
# /stats — آمار استفاده، قیف تبدیل و درآمد
# ══════════════════════════════════════════════════════════════════
def _fa(n) -> str:
    return str(n).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def _tehran_midnight_ts() -> float:
    import datetime
    try:
        from zoneinfo import ZoneInfo
        now = datetime.datetime.now(ZoneInfo("Asia/Tehran"))
    except Exception:
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3, minutes=30)))
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def build_stats_text(now: float = None) -> str:
    import db
    now = now or time.time()
    periods = [("امروز", _tehran_midnight_ts()), ("۷ روز", now - 7 * 86400), ("۳۰ روز", now - 30 * 86400)]

    def ev(kind):
        return [db.query_one("SELECT COUNT(*) n FROM events WHERE kind=? AND created_at>=?", (kind, t))["n"]
                for _, t in periods]

    def ev_users(kind):
        return [db.query_one("SELECT COUNT(DISTINCT user_id) n FROM events WHERE kind=? AND created_at>=?",
                             (kind, t))["n"] for _, t in periods]

    def row(title, vals):
        return f"{title}: " + " | ".join(_fa(f"{v:,}") for v in vals)

    starts, confirms, ok = ev("form_start"), ev("preview_confirm"), ev("query_ok")
    no_res = [a + b + c for a, b, c in zip(ev("no_result"), ev("no_value"), ev("land_use_missing"))]
    pays = [db.query_one("SELECT COUNT(*) n, COALESCE(SUM(amount_rial),0) s FROM payments WHERE created_at>=?",
                         (t,)) for _, t in periods]
    trials = [db.query_one("SELECT COUNT(*) n FROM trial_requests WHERE created_at>=?", (t,))["n"]
              for _, t in periods]

    lines = ["📊 *آمار ربات*", "ستون‌ها: " + " | ".join(p for p, _ in periods), "",
             "— استفاده —",
             row("🟢 کاربران فعال (استعلام موفق)", ev_users("query_ok")),
             row("📝 شروع فرم", starts),
             row("👁 تایید پیش‌نمایش", confirms),
             row("✅ استعلام موفق", ok),
             row("⚠️ بدون نتیجه (آدرس/منطقه)", no_res),
             row("🛑 خطای سیستمی", ev("query_fail")),
             row("📄 دریافت دوبارهٔ PDF", ev("pdf_resend")),
             row("📑 گزارش تجمیعی", ev("batch_pdf")),
             row("🔎 بررسی اصالت", ev("verify")),
             "", "— خدمات کاربردی —",
             row("📈 خسارت و مهریه", ev("util_damages")),
             row("🧾 هزینه دادرسی", ev("util_court_fee")),
             row("💰 تمبر", ev("util_stamp")),
             row("🛠 ابزار فایل", ev("util_file_tools")),
             row("⚖️ دیه", ev("util_diyeh")),
             row("👪 سهم‌الارث", ev("util_inheritance")),
             row("🔒 رسیدن به سقف رایگان", ev_users("util_paywall")),
             "", "— قیف (۳۰ روز) —"]
    s30, c30, o30 = starts[2], confirms[2], ok[2]
    if s30:
        lines.append(f"شروع فرم ← تایید پیش‌نمایش: {_fa(round(100 * c30 / s30))}٪")
    if c30:
        lines.append(f"تایید پیش‌نمایش ← استعلام موفق: {_fa(round(100 * o30 / c30))}٪")
    lines += ["", "— فروش —",
              row("🧪 درخواست تست", trials),
              row("💳 پرداخت", [p["n"] for p in pays]),
              row("💰 درآمد (تومان)", [p["s"] // 10 for p in pays])]

    pending_trials = db.query_one("SELECT COUNT(*) n FROM trial_requests WHERE status='pending'")["n"]
    active_subs = db.query_one("SELECT COUNT(*) n FROM subscriptions WHERE expires_at>?", (now,))["n"]
    expiring = db.query_one("SELECT COUNT(*) n FROM subscriptions WHERE expires_at>? AND expires_at<=?",
                            (now, now + 7 * 86400))["n"]
    active_util = db.query_one("SELECT COUNT(*) n FROM util_subscriptions WHERE expires_at>?", (now,))["n"]
    lines += ["", "— وضعیت فعلی —",
              f"💳 مشترکان فعال ارزش منطقه‌ای: {_fa(active_subs)} (پایان در ۷ روز آینده: {_fa(expiring)})",
              f"🧰 مشترکان فعال خدمات کاربردی: {_fa(active_util)}",
              f"✅ کاربران رایگانِ ادمین: {_fa(db.query_one('SELECT COUNT(*) n FROM approved_users')['n'])}",
              f"⏳ درخواست تست در انتظار: {_fa(pending_trials)}",
              f"📂 کل استعلام‌های ثبت‌شده: {_fa(db.query_one('SELECT COUNT(*) n FROM queries')['n'])}"]

    top = db.query("SELECT province, COUNT(*) n FROM queries WHERE created_at>=? GROUP BY province "
                   "ORDER BY n DESC LIMIT 5", (now - 30 * 86400,))
    if top:
        lines += ["", "— پرکاربردترین استان‌ها (۳۰ روز) —"]
        lines += [f"{_fa(i)}. {r['province']}: {_fa(r['n'])}" for i, r in enumerate(top, 1)]
    fails = db.query("SELECT detail, created_at FROM events WHERE kind='query_fail' ORDER BY id DESC LIMIT 3")
    if fails:
        lines += ["", "— آخرین خطاهای سیستمی —"]
        lines += [f"• {(r['detail'] or '-').splitlines()[0][:120]}" for r in fails]
    return "\n".join(lines)


@admin_router.message(Command("stats"))
async def cmd_stats(message: Message):
    if not _is_admin(message):
        return
    try:
        await message.answer(build_stats_text())
    except Exception as e:
        logger.error(f"[ADMIN] ساخت آمار ناموفق: {e}", exc_info=True)
        await message.answer(f"⚠️ ساخت آمار ناموفق بود: {e}")


# ══════════════════════════════════════════════════════════════════
# /rates — به‌روزرسانی جدول نرخ اعیانی با اکسل (از داخل ربات)
# ══════════════════════════════════════════════════════════════════
def _pending_rates_path() -> str:
    import os
    from config import DATA_DIR
    return os.path.join(DATA_DIR, "rates_pending.json")


@admin_router.message(Command("rates"))
async def cmd_rates(message: Message):
    if not _is_admin(message):
        return
    import os
    import tempfile
    import rates_admin
    from bale_file_sender import send_document_direct

    path = os.path.join(tempfile.gettempdir(), f"ayani_rates_{time.time_ns()}.xlsx")
    try:
        rates_admin.export_xlsx(path)
        await send_document_direct(
            chat_id=message.chat.id, file_path=path, filename="نرخ_اعیانی.xlsx",
            caption=("📊 جدول نرخ اعیانی فعلی\n\n" + rates_admin.describe_current() + "\n\n"
                     "نرخ‌های جدید را در همین فایل وارد کنید (ساختار ستون‌ها تغییر نکند) و فایل را برای ربات "
                     "بفرستید. قبل از اعمال، خلاصهٔ تغییرات برای تایید نمایش داده می‌شود.\n"
                     "بازگرداندن نسخهٔ قبلی: /rates_rollback"),
        )
    except Exception as e:
        logger.error(f"[ADMIN] ساخت اکسل نرخ‌ها ناموفق: {e}", exc_info=True)
        await message.answer(f"⚠️ ساخت فایل اکسل ناموفق بود: {e}")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


@admin_router.message(F.document & F.document.file_name.lower().endswith(".xlsx")
                      & (F.from_user.id == (ADMIN_ID or -1))
                      & ~F.caption.startswith("/cpi"))   # اکسل شاخص تورم (/cpi_import) → خدمات کاربردی
async def admin_xlsx_upload(message: Message):
    """اکسل ارسالی ادمین: اگر برگهٔ «اشتراک‌ها» دارد → اکسل اشتراک‌ها؛ وگرنه → نرخ اعیانی."""
    import json
    import os
    import tempfile
    import rates_admin
    import subs_admin

    path = os.path.join(tempfile.gettempdir(), f"admin_upload_{time.time_ns()}.xlsx")
    try:
        await message.bot.download(message.document, destination=path)
        if subs_admin.is_subs_workbook(path):
            await _handle_subs_upload(message, path)
            return
        res = rates_admin.parse_xlsx(path)
    except Exception as e:
        logger.error(f"[ADMIN] دریافت/خواندن اکسل نرخ‌ها ناموفق: {e}", exc_info=True)
        await message.answer(f"⚠️ دریافت یا خواندن فایل ناموفق بود: {e}")
        return
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    if not res["ok"]:
        errs = res["errors"]
        await message.answer("❌ فایل پذیرفته نشد:\n" + "\n".join(f"• {e}" for e in errs[:20])
                             + (f"\n... و {len(errs) - 20} خطای دیگر" if len(errs) > 20 else ""))
        return
    if res["changed"] == 0 and not res["added"] and not res["removed"]:
        await message.answer("ℹ️ این فایل با نرخ‌های فعلی یکسان است؛ تغییری برای اعمال وجود ندارد.")
        return
    with open(_pending_rates_path(), "w", encoding="utf-8") as f:
        json.dump(res["rates"], f, ensure_ascii=False)
    text = "📋 *پیش‌نمایش به‌روزرسانی نرخ اعیانی*\n\n" + res["summary"]
    if res["warnings"]:
        text += "\n\n⚠️ هشدارها:\n" + "\n".join(f"• {w}" for w in res["warnings"])
    text += "\n\nبا «اعمال»، نسخهٔ فعلی پشتیبان‌گیری و نرخ‌های جدید بلافاصله فعال می‌شود."
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ اعمال", callback_data="rates_apply"),
        InlineKeyboardButton(text="❌ انصراف", callback_data="rates_cancel"),
    ]])
    await message.answer(text, reply_markup=kb)


@admin_router.callback_query(F.data.in_({"rates_apply", "rates_cancel"}))
async def cb_rates_decision(callback: CallbackQuery):
    if ADMIN_ID is None or callback.from_user.id != ADMIN_ID:
        await callback.answer("دسترسی ندارید.")
        return
    import json
    import os
    import rates_admin

    pending = _pending_rates_path()
    if not os.path.exists(pending):
        await callback.answer("درخواست فعالی وجود ندارد.")
        return
    if callback.data == "rates_cancel":
        os.remove(pending)
        await callback.answer("لغو شد.")
        await callback.message.answer("❌ به‌روزرسانی نرخ‌ها لغو شد.")
        return
    try:
        with open(pending, encoding="utf-8") as f:
            new_rates = json.load(f)
        backup = rates_admin.apply_rates(new_rates)
        os.remove(pending)
    except Exception as e:
        logger.error(f"[ADMIN] اعمال نرخ‌های جدید ناموفق: {e}", exc_info=True)
        await callback.answer("خطا")
        await callback.message.answer(f"🛑 اعمال نرخ‌ها ناموفق بود (نرخ‌های قبلی همچنان فعال است): {e}")
        return
    await callback.answer("اعمال شد.")
    await callback.message.answer(
        "✅ نرخ‌های جدید فعال شد.\n\n" + rates_admin.describe_current()
        + f"\n\n💾 نسخهٔ قبلی: {os.path.basename(backup)}\nبازگرداندن: /rates_rollback")


@admin_router.message(Command("rates_rollback"))
async def cmd_rates_rollback(message: Message):
    if not _is_admin(message):
        return
    import rates_admin
    try:
        name = rates_admin.rollback()
    except Exception as e:
        await message.answer(f"🛑 بازگرداندن ناموفق بود: {e}")
        return
    if not name:
        await message.answer("ℹ️ نسخهٔ پشتیبانی وجود ندارد.")
        return
    await message.answer(f"↩️ نسخهٔ {name} بازگردانده شد.\n\n" + rates_admin.describe_current())



# ══════════════════════════════════════════════════════════════════
# /subs_excel — مدیریت اشتراک‌ها با اکسل
# ══════════════════════════════════════════════════════════════════
def _pending_subs_path() -> str:
    import os
    from config import DATA_DIR
    return os.path.join(DATA_DIR, "subs_pending.json")


@admin_router.message(Command("subs_excel"))
async def cmd_subs_excel(message: Message):
    if not _is_admin(message):
        return
    import os
    import tempfile
    import subs_admin
    from bale_file_sender import send_document_direct

    path = os.path.join(tempfile.gettempdir(), f"subs_{time.time_ns()}.xlsx")
    try:
        n = subs_admin.export_xlsx(path)
        active = db_count_active()
        await send_document_direct(
            chat_id=message.chat.id, file_path=path,
            filename=f"اشتراک‌ها_{subs_admin.ts_to_jalali_str(time.time()).replace('/', '-')}.xlsx",
            caption=(f"📋 اکسل اشتراک‌ها — {n} آیدی ({active} اشتراک فعال)\n\n"
                     "• افزودن اشتراک دستی: ردیف جدید با آیدی عددی و تاریخ پایان (یا تعداد روز)\n"
                     "• تمدید/اصلاح: تغییر تاریخ پایان یا روزهای باقی‌مانده\n"
                     "• نام و کد دفتر هر آیدی را در ستون‌های مربوط وارد کنید\n\n"
                     "فایل ویرایش‌شده را برای ربات بفرستید؛ قبل از ثبت، تغییرات برای تایید نمایش داده می‌شود."),
        )
    except Exception as e:
        logger.error(f"[ADMIN] ساخت اکسل اشتراک‌ها ناموفق: {e}", exc_info=True)
        await message.answer(f"⚠️ ساخت اکسل اشتراک‌ها ناموفق بود: {e}")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def db_count_active() -> int:
    import db
    return db.query_one("SELECT COUNT(*) n FROM subscriptions WHERE expires_at>?", (time.time(),))["n"]


async def _handle_subs_upload(message: Message, path: str):
    import json
    import subs_admin

    res = subs_admin.parse_xlsx(path)
    if not res["ok"]:
        errs = res["errors"]
        await message.answer("❌ اکسل اشتراک‌ها پذیرفته نشد:\n" + "\n".join(f"• {e}" for e in errs[:20])
                             + (f"\n... و {len(errs) - 20} خطای دیگر" if len(errs) > 20 else ""))
        return
    if not res["actions"]:
        await message.answer("ℹ️ تغییری نسبت به اطلاعات فعلی در این فایل نیست.")
        return
    with open(_pending_subs_path(), "w", encoding="utf-8") as f:
        json.dump(res["actions"], f, ensure_ascii=False)
    changes = res["changes"]
    text = "📋 *پیش‌نمایش تغییرات اشتراک‌ها*\n\n" + res["summary"] + "\n\n" + "\n".join(changes[:25])
    if len(changes) > 25:
        text += f"\n... و {len(changes) - 25} مورد دیگر"
    text += "\n\nبا «اعمال»، تغییرات ثبت و به کاربرانی که اشتراکشان جدید/تمدید شده اطلاع داده می‌شود."
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ اعمال", callback_data="subs_apply"),
        InlineKeyboardButton(text="❌ انصراف", callback_data="subs_cancel"),
    ]])
    await message.answer(text, reply_markup=kb)


@admin_router.callback_query(F.data.in_({"subs_apply", "subs_cancel"}))
async def cb_subs_decision(callback: CallbackQuery):
    if ADMIN_ID is None or callback.from_user.id != ADMIN_ID:
        await callback.answer("دسترسی ندارید.")
        return
    import json
    import os
    import subs_admin
    from keyboards import main_menu_kb

    pending = _pending_subs_path()
    if not os.path.exists(pending):
        await callback.answer("درخواست فعالی وجود ندارد.")
        return
    if callback.data == "subs_cancel":
        os.remove(pending)
        await callback.answer("لغو شد.")
        await callback.message.answer("❌ تغییرات اشتراک‌ها ثبت نشد.")
        return
    try:
        with open(pending, encoding="utf-8") as f:
            actions = json.load(f)
        notify = await subs_admin.apply_actions(actions)
        os.remove(pending)
    except Exception as e:
        logger.error(f"[ADMIN] اعمال اکسل اشتراک‌ها ناموفق: {e}", exc_info=True)
        await callback.answer("خطا")
        await callback.message.answer(f"🛑 ثبت تغییرات ناموفق بود: {e}")
        return
    sent = 0
    for uid, exp, plan, product in notify:
        try:
            await callback.bot.send_message(
                uid, f"✅ {access_control.plan_title(plan, product)} شما توسط مدیر سامانه تا "
                     f"{_fa(subs_admin.ts_to_jalali_str(exp))} فعال شد.", reply_markup=main_menu_kb())
            sent += 1
        except Exception as e:
            logger.warning(f"[ADMIN] اطلاع‌رسانی اشتراک به {uid} ناموفق: {e}")
    await callback.answer("ثبت شد.")
    await callback.message.answer(
        f"✅ تغییرات {len(actions)} آیدی ثبت شد."
        + (f"\n📨 به {sent} کاربر اطلاع داده شد." if notify else "")
        + "\nاکسل به‌روز: /subs_excel")


@admin_router.message(Command("office"))
async def cmd_office(message: Message):
    if not _is_admin(message):
        return
    parts = (message.text or "").split(maxsplit=3)
    if len(parts) < 3 or not parts[1].isdigit():
        await message.answer("فرمت درست: /office آیدی_عددی کد_دفتر نام دفتر\n"
                             "مثال: /office 123456789 1234 دفتر خدمات الکترونیک قضایی شمارهٔ ۱۲ تهران")
        return
    uid, code = int(parts[1]), parts[2]
    name = parts[3] if len(parts) > 3 else None
    access_control.set_office_info(uid, office_name=name, office_code=code)
    access_control.refresh_subs_excel()
    info = access_control.get_office_info(uid)
    await message.answer(f"✅ ثبت شد — آیدی {uid}\n🏢 {info.get('office_name') or '—'}\n🔢 کد دفتر: {info.get('office_code')}")



# ══════════════════════════════════════════════════════════════════
# /diyeh_rate — مبلغ دیهٔ کامل هر سال (بخشنامهٔ سالانهٔ رئیس قوه قضاییه)
# ══════════════════════════════════════════════════════════════════
@admin_router.message(Command("diyeh_rate"))
async def cmd_diyeh_rate(message: Message):
    if not _is_admin(message):
        return
    import diyeh_calc as dyc
    nums = []
    for p in (message.text or "").split()[1:]:
        t = p.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")).replace(",", "").replace("٬", "")
        nums.append(int(t) if t.isdigit() else None)
    if len(nums) == 2 and all(nums) and 1370 <= nums[0] <= 1500 and nums[1] > 0:
        dyc.set_full_diyeh(nums[0], nums[1])
        await message.answer(f"✅ دیهٔ کامل سال {_fa(nums[0])} = {_fa(f'{nums[1]:,}')} ریال ثبت شد.")
        return
    last = list(dyc.full_diyeh_table().items())[-5:]
    await message.answer("⚖️ مبلغ دیهٔ کامل (ریال):\n" + "\n".join(f"• {_fa(y)}: {_fa(f'{v:,}')}" for y, v in last)
                         + "\n\nثبت یا اصلاح سال جدید:\n/diyeh_rate 1406 27000000000")


ADMIN_HELP = """🛠 دستورهای مدیر

👥 کاربران و اشتراک‌ها
/approve آیدی — دسترسی رایگان دائمی
/revoke آیدی — حذف دسترسی رایگان
/approved — فهرست تأییدشده‌ها
/subs — مشترکان فعال
/addsub آیدی monthly|yearly — فعال‌سازی دستی اشتراک
/credit آیدی تعداد — استعلام رایگان
/subs_excel — اکسل مشترکان (ارسال اکسل ویرایش‌شده = اعمال)
/office آیدی کد_دفتر نام دفتر — ثبت دفتر

📊 آمار و نرخ‌ها
/stats — آمار استفاده و فروش
/rates — اکسل نرخ اعیانی (ارسال اکسل = به‌روزرسانی)
/rates_rollback — برگشت نرخ‌ها
/diyeh_rate [سال مبلغ] — مبلغ دیهٔ کامل

📈 شاخص تورم
/cpi [1405/06 شاخص] — وضعیت/ثبت
/cpi_fetch — دریافت از بانک مرکزی
/cpi_year سال شاخص — شاخص سالانه
فایل با کپشن /cpi_pdf یا /cpi_import

📣 فروش و ارتباط
/broadcast — پیام همگانی بر اساس گروه کاربران
/discount_new کد درصد [سقف] [روز] [rv|util|all] — کد تخفیف
/discounts — فهرست کدها
/discount_off کد — غیرفعال‌سازی
پاسخ پشتیبانی: دکمهٔ «پاسخ» یا Reply روی پیام کاربر (/cancel انصراف)

⚙️ نگهداری
/backup — بکاپ فوری (خودکار هر شب)
/taxstatus — وضعیت سامانهٔ مالیاتی
/taxcheck — بررسی فوری سامانه
/maptest — آزمایش نقشهٔ نشان در PDF"""


@admin_router.message(Command("admin"))
async def cmd_admin_help(message: Message):
    if not _is_admin(message):
        return
    await message.answer(ADMIN_HELP)
