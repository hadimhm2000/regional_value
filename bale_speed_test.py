# -*- coding: utf-8 -*-
"""
تست سرعت اتصال به سرور بله (بدون اجرای ربات).

اجرا:   python bale_speed_test.py
خروجی: زمان DNS، زمان هر درخواست getMe، و مقایسهٔ اتصال عادی با اتصال فقط-IPv4.
"""
import asyncio
import os
import socket
import statistics
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


async def run(label, family):
    url = f"{BASE}/bot{TOKEN}/getMe"
    times, errors = [], 0
    connector = aiohttp.TCPConnector(family=family, ttl_dns_cache=3600)
    async with aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=30)) as s:
        for _ in range(N):
            t = time.perf_counter()
            try:
                async with s.get(url) as r:
                    await r.read()
                times.append((time.perf_counter() - t) * 1000)
            except Exception as e:
                errors += 1
                print(f"  [{label}] error: {type(e).__name__}: {e}")
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
    await run("default", 0)
    await run("ipv4-only", socket.AF_INET)


if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
