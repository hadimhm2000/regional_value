# -*- coding: utf-8 -*-
"""
bale_file_sender.py — ارسال مستقیم فایل به API بله با aiohttp (multipart/form-data).

FSInputFile در aiogram با API بله ناسازگار است و خطای
'failed to get HTTP URL content' می‌دهد. این ماژول به‌جای آن، فایل را
مستقیماً و به‌صورت multipart/form-data به endpoint بله می‌فرستد —
راه‌حلی که در پروژهٔ اصلی (online.judicial.services.ble) اثبات شده است.
"""
import json
import logging
import os

import aiohttp

from config import BOT_TOKEN, BALE_API_BASE

logger = logging.getLogger(__name__)


def _api_url(method: str) -> str:
    base = BALE_API_BASE.rstrip("/")
    return f"{base}/bot{BOT_TOKEN}/{method}"


def _serialize_reply_markup(reply_markup):
    if reply_markup is None:
        return None
    try:
        if hasattr(reply_markup, "model_dump"):
            return json.dumps(reply_markup.model_dump(exclude_none=True), ensure_ascii=False)
        return json.dumps(reply_markup, ensure_ascii=False, default=str)
    except Exception:
        return None


async def send_document_direct(
    chat_id: int,
    file_path: str,
    filename: str = None,
    caption: str = None,
    reply_markup=None,
) -> bool:
    """ارسال سند به کاربر با multipart/form-data مستقیم.

    یک تلاش مجدد خودکار در صورت شکست اول انجام می‌شود (شبکه/سرویس بله
    گاهی در تلاش اول با خطای موقت مواجه می‌شود).
    """
    for attempt in range(1, 3):
        result = await _send_document_once(chat_id, file_path, filename, caption, reply_markup)
        if result:
            return True
        if attempt == 1:
            import asyncio
            await asyncio.sleep(2)
            logger.info(f"[BALE-FILE] تلاش مجدد ارسال فایل: {filename or file_path} -> chat {chat_id}")
    return False


async def _send_document_once(
    chat_id: int,
    file_path: str,
    filename: str = None,
    caption: str = None,
    reply_markup=None,
) -> bool:
    if not os.path.exists(file_path):
        logger.error(f"[BALE-FILE] فایل وجود ندارد: {file_path}")
        return False

    if filename is None:
        filename = os.path.basename(file_path)

    url = _api_url("sendDocument")
    try:
        with open(file_path, "rb") as f:
            file_bytes = f.read()

        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field("chat_id", str(chat_id))
            if caption:
                data.add_field("caption", caption)
            rm_json = _serialize_reply_markup(reply_markup)
            if rm_json:
                data.add_field("reply_markup", rm_json)
            data.add_field(
                "document", file_bytes, filename=filename, content_type="application/octet-stream"
            )
            async with session.post(
                url, data=data, timeout=aiohttp.ClientTimeout(total=60), ssl=False
            ) as resp:
                result = await resp.json()
                if result.get("ok"):
                    logger.info(f"[BALE-FILE] فایل ارسال شد: {filename} -> chat {chat_id}")
                    return True
                logger.error(f"[BALE-FILE] خطای API: {result.get('description', 'unknown')}")
                return False
    except Exception as e:
        logger.error(f"[BALE-FILE] خطا در ارسال فایل {filename}: {e}")
        return False


async def send_invoice(
    chat_id: int,
    title: str,
    description: str,
    payload: str,
    provider_token: str,
    prices: list,
) -> bool:
    """ارسال فاکتور پرداخت کیف‌پول بله."""
    url = _api_url("sendInvoice")
    body = {
        "chat_id": chat_id,
        "title": title,
        "description": description,
        "payload": payload,
        "provider_token": provider_token,
        "currency": "IRR",
        "prices": prices,
    }
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            async with session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                result = await resp.json()
                if result.get("ok"):
                    logger.info(f"[BALE-FILE] فاکتور پرداخت ارسال شد برای chat {chat_id}")
                    return True
                logger.error(f"[BALE-FILE] خطای ارسال فاکتور: {result.get('description')}")
                return False
    except Exception as e:
        logger.error(f"[BALE-FILE] خطا در ارسال فاکتور: {e}")
        return False
