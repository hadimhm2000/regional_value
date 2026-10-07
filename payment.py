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

from config import BALE_WALLET_TOKEN, SUBSCRIPTION_PLANS, UTIL_SUBSCRIPTION_PLANS
from bale_file_sender import send_invoice

logger = logging.getLogger(__name__)

INVOICE_PAYLOAD_TYPE = "rv_sub"        # اشتراک استعلام ارزش منطقه‌ای
UTIL_PAYLOAD_TYPE = "util_sub"         # اشتراک خدمات کاربردی

PRODUCT_PLANS = {"rv": SUBSCRIPTION_PLANS, "util": UTIL_SUBSCRIPTION_PLANS}
PRODUCT_PAYLOAD = {"rv": INVOICE_PAYLOAD_TYPE, "util": UTIL_PAYLOAD_TYPE}
PAYLOAD_PRODUCT = {v: k for k, v in PRODUCT_PAYLOAD.items()}


def plan_price(product: str, plan: str, percent: int = 0) -> tuple:
    """(قیمت نهایی تومان، قیمت نهایی ریال، قیمت اصلی تومان) با اعمال درصد تخفیف."""
    p = PRODUCT_PLANS[product][plan]
    if percent:
        from discounts import discounted_toman
        toman = discounted_toman(p["toman"], percent)
    else:
        toman = p["toman"]
    return toman, toman * 10, p["toman"]


def expected_amount(payload: dict, user_id: int):
    """
    مبلغ درست (ریال) برای یک فاکتور بر اساس payload — برای تایید pre_checkout.
    None اگر payload نامعتبر باشد یا کد تخفیفش دیگر برای این کاربر معتبر نباشد.
    """
    product = PAYLOAD_PRODUCT.get(payload.get("type"))
    plan = payload.get("plan")
    if not product or plan not in PRODUCT_PLANS[product]:
        return None
    percent = 0
    if payload.get("dc"):
        from discounts import DiscountError, validate
        try:
            percent = validate(payload["dc"], user_id, product)["percent"]
        except DiscountError:
            return None
    return plan_price(product, plan, percent)[1]


async def send_subscription_invoice(bot, user_id: int, plan: str, product: str = "rv",
                                    discount: dict | None = None) -> bool:
    """discount: {"code", "percent"} — کد تخفیف معتبر (قبلاً با discounts.validate بررسی شده)."""
    if not BALE_WALLET_TOKEN:
        logger.error("[PAYMENT] BALE_WALLET_TOKEN تنظیم نشده — امکان ساخت فاکتور پرداخت نیست.")
        return False
    p = PRODUCT_PLANS[product].get(plan)
    if not p:
        logger.error(f"[PAYMENT] پلن نامعتبر: {plan}")
        return False

    percent = int((discount or {}).get("percent") or 0)
    toman, rial, list_toman = plan_price(product, plan, percent)
    data = {"type": PRODUCT_PAYLOAD[product], "plan": plan, "uid": user_id}
    if percent:
        data["dc"] = discount["code"]
    desc = f"{p['title']} ({p['days']} روز): {toman:,} تومان"
    if percent:
        desc += f" (قیمت اصلی {list_toman:,} تومان، {percent}٪ تخفیف با کد {discount['code']})"
    ok = await send_invoice(
        chat_id=user_id,
        title=p["title"] if product == "util" else f"{p['title']} سامانه استعلام ارزش منطقه‌ای",
        description=desc + "\nپس از پرداخت، اشتراک شما به‌صورت خودکار فعال می‌شود.",
        payload=json.dumps(data),
        provider_token=BALE_WALLET_TOKEN,
        prices=[{"label": p["title"], "amount": rial}],
    )
    if ok:
        logger.info(f"[PAYMENT] فاکتور {p['title']} ({toman:,} تومان) برای کاربر {user_id} ارسال شد")
    return ok


def parse_payload(raw: str) -> dict:
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {}
