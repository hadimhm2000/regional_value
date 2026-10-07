# -*- coding: utf-8 -*-
"""
billing.py — پایان پرداخت موفق (برای هر دو اشتراک) و رسید رسمی PDF.

complete_payment: فعال‌سازی اشتراک ← ثبت پرداخت با جزئیات (قیمت اصلی، تخفیف، دوره) ←
ثبت استفاده از کد تخفیف ← اطلاع به ادمین ← ارسال رسید PDF به کاربر.
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time

import access_control
import db

logger = logging.getLogger(__name__)

PRODUCT_TITLES = {"rv": "استعلام ارزش منطقه‌ای", "util": "خدمات کاربردی"}


def _fa(n) -> str:
    return str(n).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def receipt_no(payment_id: int) -> str:
    return f"PAY-{int(payment_id):06d}"


async def complete_payment(message, bot, product: str, plan_key: str, payload: dict) -> dict | None:
    """خروجی: اشتراک فعال‌شده {"plan","expires_at"} یا None اگر فعال‌سازی شکست خورد."""
    from config import ADMIN_ID
    from payment import PRODUCT_PLANS
    user_id = message.from_user.id
    sp = message.successful_payment
    plan = PRODUCT_PLANS[product][plan_key]
    amount = int(getattr(sp, "total_amount", 0) or plan["rial"])
    code = payload.get("dc")
    try:
        sub = await access_control.activate_subscription(user_id, plan_key, plan["days"], product=product)
    except Exception as e:
        logger.error(f"[BILLING] فعال‌سازی اشتراک {product} کاربر {user_id} ناموفق: {e}", exc_info=True)
        if ADMIN_ID:
            await _safe(bot.send_message(ADMIN_ID, f"🛑 پرداخت {plan['title']} کاربر {user_id} دریافت شد ولی "
                                                   f"فعال‌سازی اشتراک شکست خورد: {e}"))
        await _safe(message.answer("✅ پرداخت شما دریافت شد؛ فعال‌سازی اشتراک با خطا مواجه شد و به ادمین اطلاع داده شد."))
        return None

    payment_id = None
    try:
        payment_id = access_control.record_payment(
            user_id, plan_key if product == "rv" else f"util:{plan_key}", amount,
            getattr(sp, "provider_payment_charge_id", "") or "", product=product, plan_key=plan_key,
            days=plan["days"], expires_at=sub["expires_at"], list_price_rial=plan["rial"], discount_code=code)
        db.log_event("payment", user_id, f"{product}:{plan_key}" + (f":{code}" if code else ""))
        if code:
            import discounts
            discounts.record_use(code, user_id, payment_id)
    except Exception as e:
        logger.error(f"[BILLING] ثبت پرداخت {user_id} ناموفق: {e}", exc_info=True)

    if ADMIN_ID:
        office = access_control.get_office_info(user_id)
        text = (f"💳 خرید {plan['title']} — {getattr(message.from_user, 'full_name', '')} ({user_id})\n"
                f"💰 {amount // 10:,} تومان" + (f" (کد تخفیف {code})" if code else "") + "\n"
                f"🏢 دفتر: {office.get('office_name') or 'ثبت نشده'} — کد: {office.get('office_code') or 'ثبت نشده'}\n"
                "این خرید در اکسل اشتراک‌ها (/subs_excel) ثبت شد"
                + ("" if office.get("office_name") else f"؛ ثبت نام دفتر: /office {user_id} کد_دفتر نام_دفتر"))
        await _safe(bot.send_message(ADMIN_ID, text))
    if payment_id:
        await send_receipt(message.chat.id, payment_id)
    return sub


async def _safe(coro):
    try:
        return await coro
    except Exception as e:
        logger.warning(f"[BILLING] ارسال پیام ناموفق: {e}")


# ══════════════════════════════════════════════════════════════════
# رسید PDF
# ══════════════════════════════════════════════════════════════════
def get_payment(payment_id: int):
    r = db.query_one("SELECT * FROM payments WHERE id=?", (int(payment_id),))
    return dict(r) if r else None


def user_payments(user_id: int, limit: int = 10) -> list:
    return [dict(r) for r in db.query("SELECT * FROM payments WHERE user_id=? ORDER BY id DESC LIMIT ?",
                                      (int(user_id), limit))]


def _product_of(p: dict) -> str:
    if p.get("product"):
        return p["product"]
    return "util" if str(p.get("plan") or "").startswith("util:") else "rv"


def build_receipt_pdf(path: str, p: dict) -> bool:
    import legal_pdf
    from ayani_pdf import _money, num_to_words
    from menu_handlers import fa_date
    product = _product_of(p)
    plan_key = p.get("plan_key") or str(p.get("plan") or "").split(":")[-1]
    plan_title = access_control.PLAN_TITLES.get(plan_key, plan_key or "اشتراک")
    office = access_control.get_office_info(p["user_id"])
    user = db.query_one("SELECT full_name, username FROM users WHERE user_id=?", (p["user_id"],))
    tr = db.query_one("SELECT full_name FROM trial_requests WHERE user_id=?", (p["user_id"],))
    name = (user["full_name"] if user and user["full_name"] else "") or (tr["full_name"] if tr else "") or "—"
    rows = [("شمارهٔ رسید", receipt_no(p["id"])), ("تاریخ پرداخت", fa_date(p["created_at"], with_time=True)),
            ("نام پرداخت‌کننده", name), ("شناسهٔ کاربری بله", str(p["user_id"]))]
    if office.get("office_name"):
        rows.append(("دفتر خدمات قضایی", office["office_name"], True))
    if office.get("office_code"):
        rows.append(("کد دفتر", office["office_code"]))
    rows += [("سرویس", PRODUCT_TITLES[product]), ("نوع اشتراک", plan_title)]
    if p.get("starts_at") and p.get("expires_at"):
        rows.append(("دورهٔ اشتراک", f"از {fa_date(p['starts_at'])} تا {fa_date(p['expires_at'])}", True))
    elif p.get("days"):
        rows.append(("مدت اشتراک", f"{p['days']} روز"))
    if p.get("charge_id"):
        rows.append(("شناسهٔ تراکنش", p["charge_id"], True))
    amounts = []
    if p.get("list_price_rial") and p["list_price_rial"] != p["amount_rial"]:
        amounts.append(("قیمت اصلی", p["list_price_rial"]))
        amounts.append((f"تخفیف (کد {p.get('discount_code') or '—'})", p["list_price_rial"] - p["amount_rial"]))
    amounts.append(("مبلغ پرداخت‌شده", p["amount_rial"]))
    sections = [("kv", "مشخصات پرداخت", rows),
                ("amounts", amounts, num_to_words(p["amount_rial"])),
                ("bullets", "توضیحات", [f"معادل {_money(p['amount_rial'] // 10)} تومان.",
                                        "پرداخت از طریق درگاه کیف پول بله انجام شده است.",
                                        "صحت این رسید با شمارهٔ آن در ربات قابل پیگیری است."])]
    return legal_pdf.build_report(path, "رسید پرداخت اشتراک", "سامانهٔ استعلام ارزش منطقه‌ای و خدمات کاربردی",
                                  "RC", sections, "این رسید به‌صورت خودکار پس از پرداخت موفق صادر شده است.")


async def send_receipt(chat_id: int, payment_id: int) -> bool:
    from bale_file_sender import send_document_direct
    p = get_payment(payment_id)
    if not p:
        return False
    path = os.path.join(tempfile.gettempdir(), f"receipt_{payment_id}_{time.time_ns()}.pdf")
    try:
        ok = await asyncio.get_running_loop().run_in_executor(None, build_receipt_pdf, path, p)
        return bool(ok and await send_document_direct(
            chat_id, path, filename=f"رسید_{receipt_no(payment_id)}.pdf",
            caption=f"🧾 رسید پرداخت {receipt_no(payment_id)} — {p['amount_rial'] // 10:,} تومان"))
    except Exception as e:
        logger.error(f"[BILLING] ساخت/ارسال رسید {payment_id} ناموفق: {e}", exc_info=True)
        return False
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
