# -*- coding: utf-8 -*-
"""تست‌های سخت‌سازی امنیتی: محدودیت نرخ، قفل کار سنگین، رزرو کد تخفیف، TLS، پشتیبان رمزدار، فایل حجیم."""
import asyncio
import os
import ssl
import time
import zipfile

import db
import discounts
import rate_limit as rl


# ── محدودیت نرخ ──
def test_sliding_window_limits_per_user_and_slides():
    t = [0.0]
    lim = rl.SlidingWindowLimiter(20, 60, clock=lambda: t[0])
    res = [lim.hit(1) for _ in range(25)]
    assert all(r[0] for r in res[:20]) and not any(r[0] for r in res[20:])
    assert res[20][1] is True and not any(r[1] for r in res[21:])   # فقط یک هشدار
    assert lim.hit(2)[0]                                              # کاربر دیگر آزاد است
    t[0] = 61
    assert lim.hit(1)[0]                                              # پنجره جلو رفت


def test_zero_limit_disables():
    lim = rl.SlidingWindowLimiter(0, 60)
    assert all(lim.hit(1)[0] for _ in range(500))


class _U:
    def __init__(self, i):
        self.id = i


class _Msg:
    def __init__(self, uid, pay=None):
        self.from_user, self.successful_payment, self.sent = _U(uid), pay, []

    async def answer(self, text, **kw):
        self.sent.append(text)


def test_middleware_blocks_spam_but_not_admin_or_payment():
    async def handler(event, data):
        return "ok"

    async def go():
        m = rl.RateLimitMiddleware(2, 60, admin_id=9)
        msg = _Msg(1)
        out = [await m(handler, msg, {}) for _ in range(5)]
        assert out == ["ok", "ok", None, None, None]
        assert len(msg.sent) == 1 and "صبر" in msg.sent[0]
        assert await m(handler, _Msg(1, pay=object()), {}) == "ok"      # پرداخت موفق هرگز رد نمی‌شود
        assert all([await m(handler, _Msg(9), {}) == "ok" for _ in range(10)])   # ادمین
    asyncio.run(go())


def test_busy_lock_one_job_per_user_and_kind():
    assert rl.try_acquire("rv_query", 77)
    assert not rl.try_acquire("rv_query", 77)
    assert rl.try_acquire("file_tools", 77) and rl.try_acquire("rv_query", 78)
    for k, u in (("rv_query", 77), ("file_tools", 77), ("rv_query", 78)):
        rl.release(k, u)
    assert not rl.is_busy("rv_query", 77)


# ── رزرو کد تخفیف ──
def test_discount_reserve_blocks_concurrent_use():
    db.init(":memory:")
    discounts.create("one", 20, max_uses=1, product="rv")
    assert discounts.reserve("ONE", 1, "rv")["percent"] == 20
    for uid, msg in ((1, "در حال انجام"), (2, "ظرفیت")):
        try:
            discounts.reserve("ONE", uid, "rv")
            raise AssertionError("رزرو دوم نباید پذیرفته شود")
        except discounts.DiscountError as e:
            assert msg in str(e)
    assert discounts.record_use("ONE", 1, 5)
    assert db.query_one("SELECT COUNT(*) c FROM discount_holds")["c"] == 0


def test_discount_hold_expires():
    db.init(":memory:")
    discounts.create("two", 10, max_uses=1)
    discounts.reserve("TWO", 3, "util")
    db.execute("UPDATE discount_holds SET expires_at=?", (time.time() - 1,))
    assert discounts.reserve("TWO", 4, "rv")


# ── TLS ──
def test_bale_sender_verifies_certificates():
    import bale_file_sender
    assert bale_file_sender._SSL.verify_mode == ssl.CERT_REQUIRED
    assert bale_file_sender._SSL.check_hostname is True
    src = open(bale_file_sender.__file__, encoding="utf-8").read()
    assert "ssl=False" not in src


# ── پشتیبان ──
def test_backup_plain_without_password(monkeypatch):
    import ops
    db.init(":memory:")
    monkeypatch.delenv("BACKUP_PASSWORD", raising=False)
    assert ops.backup_encryption() == "off"
    with zipfile.ZipFile(ops.make_backup(2)) as z:
        assert "bot.sqlite3" in z.namelist()


def test_backup_encrypted_with_password(monkeypatch):
    import pytest
    pyzipper = pytest.importorskip("pyzipper")
    import ops
    db.init(":memory:")
    monkeypatch.setenv("BACKUP_PASSWORD", "s3cret")
    assert ops.backup_encryption() == "aes"
    path = ops.make_backup(2)
    with zipfile.ZipFile(path) as z:            # بدون رمز خوانده نمی‌شود
        try:
            z.read("bot.sqlite3")
            raise AssertionError("پشتیبان باید رمزدار باشد")
        except (RuntimeError, NotImplementedError):
            pass
    with pyzipper.AESZipFile(path) as z:
        z.setpassword(b"s3cret")
        assert z.read("bot.sqlite3")


# ── فایل حجیم ──
def test_pdf_zoom_clamped_for_huge_pages():
    import utilities_handlers as uh
    assert uh._safe_zoom(595, 842, 2.0) == 2.0                     # A4 عادی دست نمی‌خورد
    z = uh._safe_zoom(14400, 14400, 2.0)                           # صفحهٔ ۵ متری
    assert (14400 * z) ** 2 <= uh.MAX_PAGE_PIXELS * 1.001


def test_upload_size_limit():
    import utilities_handlers as uh

    class F:
        def __init__(self, n):
            self.file_size = n
    assert not uh._too_big(F(5 * 1024 * 1024))
    assert uh._too_big(F((uh.MAX_UPLOAD_MB + 1) * 1024 * 1024))
    assert not uh._too_big(F(None))
