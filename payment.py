# -*- coding: utf-8 -*-
"""
payment.py — فاکتور کیف‌پول بله برای خرید اشتراک (ماهانه/سالانه).

کاربران رایگانِ ادمین و مشترکان فعال این مرحله را نمی‌بینند. بقیه پس از
اتمام استعلام‌های تست، یکی از پلن‌های config.SUBSCRIPTION_PLANS را انتخاب
و از طریق فاکتور خودِ بله پرداخت می‌کنند؛ پس از successful_payment اشتراک
فعال می‌شود.
"""
import json
import logging

from config import BALE_WALLET_TOKEN, SUBSCRIPTION_PLANS
from bale_file_sender import send_invoice

logger = logging.getLogger(__name__)

INVOICE_PAYLOAD_TYPE = "rv_sub"


async def send_subscription_invoice(bot, user_id: int, plan: str) -> bool:
    if not BALE_WALLET_TOKEN:
        logger.error("[PAYMENT] BALE_WALLET_TOKEN تنظیم نشده — امکان ساخت فاکتور پرداخت نیست.")
        return False
    p = SUBSCRIPTION_PLANS.get(plan)
    if not p:
        logger.error(f"[PAYMENT] پلن نامعتبر: {plan}")
        return False

    payload = json.dumps({"type": INVOICE_PAYLOAD_TYPE, "plan": plan, "uid": user_id})
    ok = await send_invoice(
        chat_id=user_id,
        title=f"{p['title']} سامانه استعلام ارزش منطقه‌ای",
        description=(
            f"{p['title']} ({p['days']} روز): {p['toman']:,} تومان\n"
            f"پس از پرداخت، اشتراک شما به‌صورت خودکار فعال می‌شود."
        ),
        payload=payload,
        provider_token=BALE_WALLET_TOKEN,
        prices=[{"label": p["title"], "amount": p["rial"]}],
    )
    if ok:
        logger.info(f"[PAYMENT] فاکتور {p['title']} ({p['toman']:,} تومان) برای کاربر {user_id} ارسال شد")
    return ok


def parse_payload(raw: str) -> dict:
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {}
