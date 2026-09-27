# -*- coding: utf-8 -*-
"""
تست سرعت اتصال به سرور بله (بدون اجرای ربات).

اجرا:   python bale_speed_test.py
خروجی: صادرکنندهٔ گواهی، و زمان درخواست‌ها با گواهی سیستم / certifi (مثل aiogram) / بدون بررسی (فقط تست).
"""
import asyncio
import os
import socket
import ssl
import statistics
import tempfile
import time

import aiohttp
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), encoding="utf-8-sig")
TOKEN = os.environ.get("BOT_TOKEN")
BASE = os.environ.get("BALE_API_BASE", "https://tapi.bale.ai").rstrip("/")
HOST = BASE.split("//", 1)[-1].split("/", 1)[0]
N = 8


def dns_check():
    t = time.perf_counter()
    try:
        infos = socket.getaddrinfo(HOST, 443, proto=socket.IPPROTO_TCP)
    except Exception as e:
        print(f"DNS FAILED: {e}")
        return
    ms = (time.perf_counter() - t) * 1000
    v4 = sorted({i[4][0] for i in infos if i[0] == socket.AF_INET})
    v6 = sorted({i[4][0] for i in infos if i[0] == socket.AF_INET6})
    print(f"DNS {HOST}: {ms:.0f} ms | IPv4={v4} | IPv6={v6}")


def cert_issuer():
    """چه کسی گواهی tapi.bale.ai را امضا کرده؟ (اگر آنتی‌ویروس/VPN وسط باشد، نامش این‌جا دیده می‌شود)"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((HOST, 443), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=HOST) as ss:
                der = ss.getpeercert(binary_form=True)
        with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as f:
            f.write(ssl.DER_cert_to_PEM_cert(der))
            path = f.name
        info = ssl._ssl._test_decode_cert(path)
        os.remove(path)
        fmt = lambda t: ", ".join(f"{k}={v}" for rdn in t for k, v in rdn)
        print(f"CERT subject: {fmt(info.get('subject', ()))}")
        print(f"CERT issuer : {fmt(info.get('issuer', ()))}")
    except Exception as e:
        print(f"CERT check failed: {type(e).__name__}: {e}")


def _ssl_ctx(mode):
    if mode == "certifi":           # همان چیزی که aiogram استفاده می‌کند
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    if mode == "no-verify":         # فقط برای اندازه‌گیری سرعت در این تست — در ربات استفاده نمی‌شود
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context()  # windows: گواهی‌های سیستم


async def run(label, family, ssl_mode="system"):
    url = f"{BASE}/bot{TOKEN}/getMe"
    times, errors = [], 0
    connector = aiohttp.TCPConnector(family=family, ttl_dns_cache=3600, ssl=_ssl_ctx(ssl_mode))
    async with aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=30)) as s:
        for _ in range(N):
            t = time.perf_counter()
            try:
                async with s.get(url) as r:
                    await r.read()
                times.append((time.perf_counter() - t) * 1000)
            except Exception as e:
                errors += 1
                if errors == 1:
                    print(f"  [{label}] error: {type(e).__name__}: {str(e)[:160]}")
            await asyncio.sleep(0.3)
    if times:
        print(f"{label:<10} median={statistics.median(times):6.0f} ms  max={max(times):6.0f} ms  "
              f"errors={errors}/{N}  all={[round(x) for x in times]}")
    else:
        print(f"{label:<10} ALL FAILED ({errors}/{N})")


async def main():
    if not TOKEN:
        print("BOT_TOKEN در .env پیدا نشد.")
        return
    dns_check()
    cert_issuer()
    await run("system", 0, "system")
    await run("certifi", 0, "certifi")
    await run("no-verify", 0, "no-verify")


if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
