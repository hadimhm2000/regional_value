# -*- coding: utf-8 -*-
"""
تست امکانات نگه‌داری و فروش: کد تخفیف (تا فاکتور و رسید)، رسید PDF، پشتیبانی دوطرفه بدون
افشای آیدی ادمین، پیام همگانی بر اساس گروه، هشدار قطعی سامانهٔ مالیاتی، هشدار استفادهٔ غیرعادی،
پیگیری تست، پشتیبان روزانه و نقشه در PDF.
"""
import datetime
import json
import os
import time
import zipfile

from aiogram.types import Chat, Message, PreCheckoutQuery, SuccessfulPayment, Update, User

import access_control
import db
import discounts
import ops
from test_bot_flows import ADMIN, COLLEAGUE, USER, h, run  # noqa: F401  (fixture)


def _capture_invoices(monkeypatch) -> list:
    import payment
    monkeypatch.setattr(payment, "BALE_WALLET_TOKEN", "WALLET")
    invoices = []

    async def fake_invoice(**kw):
        invoices.append(kw)
        return True
    monkeypatch.setattr(payment, "send_invoice", fake_invoice)
    return invoices


def _pay_msg(payload: dict, amount: int, uid=USER, mid=1):
    sp = SuccessfulPayment(currency="IRR", total_amount=amount, invoice_payload=json.dumps(payload),
                           telegram_payment_charge_id="t", provider_payment_charge_id=f"p{mid}")
    return Message(message_id=mid, date=datetime.datetime.now(), chat=Chat(id=uid, type="private"),
                   from_user=User(id=uid, is_bot=False, first_name="x"), successful_payment=sp)


# ══════════════════════════════════════════════════════════════════
# کد تخفیف
# ══════════════════════════════════════════════════════════════════
def test_discount_logic():
    db.init(":memory:")
    d = discounts.create("off20", 20, max_uses=1, days=5, product="rv")
    assert d["code"] == "OFF20"
    assert discounts.discounted_toman(1_200_000, 20) == 960_000
    assert discounts.validate("off20", USER, "rv")["percent"] == 20
    try:
        discounts.validate("OFF20", USER, "util")
        raise AssertionError
    except discounts.DiscountError as e:
        assert "فقط برای" in str(e)
    assert discounts.record_use("OFF20", USER, 1) is True
    for uid in (USER, COLLEAGUE):   # تکراری / ظرفیت تمام
        try:
            discounts.validate("OFF20", uid, "rv")
            raise AssertionError
        except discounts.DiscountError:
            pass
    p = discounts.personal_code(USER, 10, 7)
    assert p["for_user"] == USER and p["code"].startswith("VIP-")
    try:
        discounts.validate(p["code"], COLLEAGUE, "rv")
        raise AssertionError
    except discounts.DiscountError as e:
        assert "کاربر دیگری" in str(e)


def test_discount_flow_invoice_precheckout_payment_and_receipt(h, monkeypatch):
    async def go():
        invoices = _capture_invoices(monkeypatch)
        await h.send("/discount_new NOROOZ 25 10 30 rv", uid=ADMIN)
        assert "ساخته شد" in h.session.last().text
        await h.send("/discount_new X", uid=USER)            # کاربر عادی نمی‌تواند
        assert discounts.get("X") is None

        await h.click("acc:renew")
        assert "🎟 کد تخفیف دارم" in h.last_keyboard()
        await h.send("🎟 کد تخفیف دارم")
        await h.send("WRONG")
        assert "معتبر نیست" in h.session.last().text
        await h.send("noroz")
        await h.send("norooz")
        assert "اعمال شد" in h.session.last().text
        kb = h.last_keyboard()
        assert any("۹۰۰,۰۰۰" in t and "۲۵٪" in t for t in kb), kb
        assert "🎟 کد تخفیف دارم" not in kb
        await h.send([t for t in kb if "ماهانه" in t][0])
        inv = invoices[-1]
        payload = json.loads(inv["payload"])
        assert payload["dc"] == "NOROOZ" and inv["prices"][0]["amount"] == 9_000_000

        # pre_checkout: مبلغ تخفیف‌خورده درست است، مبلغ کامل نه
        q = PreCheckoutQuery(id="q1", from_user=User(id=USER, is_bot=False, first_name="x"), currency="IRR",
                             total_amount=9_000_000, invoice_payload=inv["payload"])
        await h.dp.feed_update(h.bot, Update(update_id=9100, pre_checkout_query=q))
        assert h.session.last("AnswerPreCheckoutQuery").ok is True
        await h.dp.feed_update(h.bot, Update(update_id=9101, pre_checkout_query=q.model_copy(
            update={"id": "q2", "total_amount": 12_000_000})))
        assert h.session.last("AnswerPreCheckoutQuery").ok is False

        await h.dp.feed_update(h.bot, Update(update_id=9102, message=_pay_msg(payload, 9_000_000)))
        assert await access_control.get_subscription(USER) is not None
        p = db.query_one("SELECT * FROM payments")
        assert p["discount_code"] == "NOROOZ" and p["list_price_rial"] == 12_000_000 and p["product"] == "rv"
        assert discounts.get("NOROOZ")["used"] == 1
        receipt = h.documents[-1]
        assert receipt["chat_id"] == USER and "PAY-000001" in receipt["filename"]
        assert "PAY-000001" in receipt["text"] and receipt["pages"] == 1
        assert any("NOROOZ" in (t or "") for t in h.session.texts(ADMIN))
        # کد دوباره برای همین کاربر قابل استفاده نیست
        q3 = q.model_copy(update={"id": "q3"})
        await h.dp.feed_update(h.bot, Update(update_id=9103, pre_checkout_query=q3))
        assert h.session.last("AnswerPreCheckoutQuery").ok is False

        # /discounts
        await h.send("/discounts", uid=ADMIN)
        assert "NOROOZ" in h.session.last().text and "۱ از ۱۰" in h.session.last().text
        await h.send("/discount_off norooz", uid=ADMIN)
        assert discounts.get("NOROOZ")["active"] == 0
    run(go())


def test_utilities_discount_and_receipts_in_account(h, monkeypatch):
    async def go():
        import utilities_handlers as uh
        invoices = _capture_invoices(monkeypatch)
        discounts.create("UTIL50", 50, product="util")
        access_control.record_util_use(USER, "court_fee")
        await h.send("🧰 خدمات کاربردی")
        await h.send(uh.TOOLS["court_fee"])
        await h.send("🎟 کد تخفیف دارم")
        await h.send("UTIL50")
        kb = h.last_keyboard()
        assert any("۴۰۰,۰۰۰" in t for t in kb), kb
        await h.send([t for t in kb if "ماهانه" in t][0])
        payload = json.loads(invoices[-1]["payload"])
        assert payload["type"] == "util_sub" and payload["dc"] == "UTIL50"
        await h.dp.feed_update(h.bot, Update(update_id=9200, message=_pay_msg(payload, 4_000_000)))
        assert await access_control.get_subscription(USER, "util") is not None
        n_docs = len(h.documents)

        # حساب کاربری ← رسیدها ← PDF
        await h.send("👤 حساب کاربری و اشتراک")
        assert ("🧾 رسیدهای پرداخت", "acc:receipts") in h.last_inline()
        await h.click("acc:receipts")
        rc = [c for t, c in h.last_inline() if c.startswith("rc:")]
        assert len(rc) == 1
        await h.click(rc[0])
        assert len(h.documents) == n_docs + 1 and "400,000" in h.documents[-1]["caption"]
        await h.click(rc[0], uid=COLLEAGUE)                # رسید دیگران قابل دریافت نیست
        assert len(h.documents) == n_docs + 1
    run(go())


# ══════════════════════════════════════════════════════════════════
# پشتیبانی
# ══════════════════════════════════════════════════════════════════
def test_support_relay_and_admin_reply(h):
    async def go():
        await h.send("💬 پشتیبانی")
        assert (await h.state()).endswith("support_user")
        await h.send("سلام، گزارش من باز نمی‌شود")
        to_admin = [m for m in h.session.calls if type(m).__name__ == "SendMessage" and int(m.chat_id) == ADMIN][-1]
        assert int(to_admin.chat_id) == ADMIN and "گزارش من باز نمی‌شود" in to_admin.text and str(USER) in to_admin.text
        assert to_admin.reply_markup.inline_keyboard[0][0].callback_data == f"sup:r:{USER}"
        assert "ارسال شد" in h.session.texts()[-1]
        # هیچ پیامی به کاربر آیدی ادمین را نشان نمی‌دهد
        assert all(str(ADMIN) not in (m.text or "") for m in h.session.calls
                   if type(m).__name__ == "SendMessage" and int(m.chat_id) == USER)

        # پاسخ با دکمه
        await h.click(f"sup:r:{USER}", uid=ADMIN)
        await h.send("فایل را دوباره دریافت کنید", uid=ADMIN)
        reply = [m for m in h.session.calls if type(m).__name__ == "SendMessage" and int(m.chat_id) == USER][-1]
        assert "پاسخ پشتیبانی" in reply.text and "دوباره دریافت" in reply.text
        assert "پاسخ ارسال شد" in h.session.last().text

        # پاسخ با Reply روی پیام کاربر
        admin_msg_id = db.query_one("SELECT admin_msg_id FROM support_msgs")["admin_msg_id"]
        replied = Message(message_id=admin_msg_id, date=datetime.datetime.now(), chat=Chat(id=ADMIN, type="private"),
                          text="x")
        msg = Message(message_id=5555, date=datetime.datetime.now(), chat=Chat(id=ADMIN, type="private"),
                      from_user=User(id=ADMIN, is_bot=False, first_name="a"), text="پاسخ دوم", reply_to_message=replied)
        await h.dp.feed_update(h.bot, Update(update_id=9300, message=msg))
        reply = [m for m in h.session.calls if type(m).__name__ == "SendMessage" and int(m.chat_id) == USER][-1]
        assert "پاسخ دوم" in reply.text

        # دکمهٔ منو از حالت پشتیبانی خارج می‌کند (به ادمین ارسال نمی‌شود)
        before = db.query_one("SELECT COUNT(*) n FROM support_msgs")["n"]
        await h.send("💬 پشتیبانی")
        await h.send("📂 سوابق استعلام")
        assert db.query_one("SELECT COUNT(*) n FROM support_msgs")["n"] == before
        # کاربر عادی نمی‌تواند دکمهٔ پاسخ را بزند
        await h.click(f"sup:r:{COLLEAGUE}", uid=USER)
        assert not (await h.state() or "").endswith("support_admin_reply")
    run(go())


# ══════════════════════════════════════════════════════════════════
# پیام همگانی
# ══════════════════════════════════════════════════════════════════
def test_broadcast_by_segment(h):
    async def go():
        import ops_handlers
        await h.send("/start", uid=USER)
        await h.send("/start", uid=COLLEAGUE)
        await h.send("/start", uid=1003)
        await access_control.activate_subscription(USER, "monthly", 30)
        assert set(ops.segment_users("all")) >= {USER, COLLEAGUE, 1003}
        assert ops.segment_users("rv_active") == [USER]
        assert USER not in ops.segment_users("never_paid")

        await h.send("/broadcast", uid=ADMIN)
        segs = [c for t, c in h.last_inline()]
        assert "bc:seg:rv_active" in segs
        await h.click("bc:seg:rv_active", uid=ADMIN)
        await h.send("📣 نرخ‌های جدید ۱۴۰۵ اضافه شد", uid=ADMIN)
        assert "۱ نفر" in h.session.last().text
        n_before = len([m for m in h.session.calls if type(m).__name__ == "SendMessage" and int(m.chat_id) == USER])
        await h.click("bc:send", uid=ADMIN)
        for t in list(ops_handlers._broadcast_tasks):
            await t
        to_user = [m for m in h.session.calls if type(m).__name__ == "SendMessage" and int(m.chat_id) == USER]
        assert len(to_user) == n_before + 1 and "نرخ‌های جدید" in to_user[-1].text
        assert not any("نرخ‌های جدید" in (m.text or "") for m in h.session.calls
                       if type(m).__name__ == "SendMessage" and int(m.chat_id) == COLLEAGUE)
        assert "پیام همگانی تمام شد" in h.session.last().text
    run(go())


def test_broadcast_marks_blocked_users(h, monkeypatch):
    async def go():
        import ops_handlers
        from aiogram.exceptions import TelegramForbiddenError
        ops.touch_user(USER)
        ops.touch_user(COLLEAGUE)
        real = ops_handlers._send_content

        async def fake(bot, chat_id, c):
            if chat_id == COLLEAGUE:
                raise TelegramForbiddenError(method=None, message="blocked")
            return await real(bot, chat_id, c)
        monkeypatch.setattr(ops_handlers, "_send_content", fake)
        r = await ops_handlers.run_broadcast(h.bot, [USER, COLLEAGUE], {"text": "سلام"}, ADMIN)
        assert r == {"sent": 1, "blocked": 1, "failed": 0}
        assert COLLEAGUE not in ops.segment_users("all")
    run(go())


# ══════════════════════════════════════════════════════════════════
# قطعی سامانهٔ مالیاتی
# ══════════════════════════════════════════════════════════════════
def test_tax_outage_alert_notice_and_recovery(h, monkeypatch):
    async def go():
        import ops_handlers
        rvh = h.rvh
        monkeypatch.setattr(ops_handlers, "TAX_DOWN_THRESHOLD", 2)
        for _ in range(2):
            await ops_handlers.record_tax_result(h.bot, False, "timeout")
        assert ops.tax_is_down()
        assert sum("سامانهٔ مالیاتی" in (t or "") and "قطع" in (t or "") for t in h.session.texts(ADMIN)) == 1
        await ops_handlers.record_tax_result(h.bot, False, "timeout")   # هشدار تکراری نه
        assert sum("قطع تشخیص" in (t or "") for t in h.session.texts(ADMIN)) == 1

        await h.send("🗺️ استعلام ارزش منطقه‌ای ملک")
        assert "پاسخ نمی‌دهد" in h.session.last().text
        cbs = [c for t, c in h.last_inline()]
        assert "tax:notify" in cbs
        await h.click("tax:notify")
        await h.send("/taxstatus", uid=ADMIN)
        assert "۱ نفر" in h.session.last().text

        # ادامه به هر حال ← فرم شروع می‌شود
        await h.click([c for c in cbs if c.startswith("tax:go:")][0])
        assert (await h.state() or "").startswith("RVForm")

        # بررسی دوباره موفق ← اطلاع به ادمین و منتظران
        import geocode_and_query
        monkeypatch.setattr(geocode_and_query, "query_by_coordinates",
                            lambda *a, **k: {"tax_info": {"x": 1}}, raising=False)
        assert await ops_handlers.tax_probe(h.bot) is True
        assert not ops.tax_is_down()
        assert any("دوباره در دسترس" in (t or "") for t in h.session.texts(ADMIN))
        to_user = [m.text for m in h.session.calls if type(m).__name__ == "SendMessage" and int(m.chat_id) == USER]
        assert "دوباره در دسترس" in to_user[-1]
        assert ops.tax_pop_waiters() == []
    run(go())


def test_successful_query_resets_tax_streak(h):
    async def go():
        import ops_handlers
        await access_control.activate_subscription(USER, "monthly", 30)
        await ops_handlers.record_tax_result(h.bot, False, "x")
        await h.send("🗺️ استعلام ارزش منطقه‌ای ملک")
        await h.fill_form()
        await h.send("✅ تایید و ادامه")
        assert int(db.kv_get("tax_fail_streak", 0)) == 0
    run(go())


# ══════════════════════════════════════════════════════════════════
# استفادهٔ غیرعادی، پیگیری تست، پشتیبان
# ══════════════════════════════════════════════════════════════════
def test_usage_alert_once_per_day(h, monkeypatch):
    async def go():
        import ops_handlers
        monkeypatch.setattr(ops_handlers, "USAGE_ALERT_DAILY", 3)
        now = time.time()
        for i, prov in enumerate(["تهران", "فارس", "گیلان"]):
            db.execute("INSERT INTO queries(code, user_id, created_at, province) VALUES (?,?,?,?)",
                       (f"C{i}", USER, now - 60 * i, prov))
        await ops_handlers.check_usage(h.bot, USER)
        await ops_handlers.check_usage(h.bot, USER)
        alerts = [t for t in h.session.texts(ADMIN) if "استفادهٔ غیرعادی" in (t or "")]
        assert len(alerts) == 1 and "۳ استعلام" in alerts[0] and "۳ استان" in alerts[0]
    run(go())


def test_trial_followup_with_personal_code(h):
    async def go():
        import ops_handlers
        old = time.time() - 4 * 86400
        db.execute("INSERT INTO trial_requests(user_id, full_name, status, created_at, decided_at) "
                   "VALUES (?,?,?,?,?)", (USER, "x", "approved", old, old))
        db.execute("INSERT INTO trial_requests(user_id, full_name, status, created_at, decided_at) "
                   "VALUES (?,?,?,?,?)", (COLLEAGUE, "y", "approved", old, old))
        await access_control.activate_subscription(COLLEAGUE, "monthly", 30)   # خریده ← پیگیری نه
        assert await ops_handlers.send_trial_followups(h.bot) == 1
        assert await ops_handlers.send_trial_followups(h.bot) == 0
        m = h.session.last()
        assert int(m.chat_id) == USER and "VIP-" in m.text
        code = m.text.split("*")[1]
        assert discounts.validate(code, USER, "rv")["percent"] == 10
    run(go())


def test_backup_zip_and_schedule(h, monkeypatch):
    async def go():
        import ops_handlers
        await h.send("/backup", uid=ADMIN)
        doc = h.documents[-1]
        assert doc["chat_id"] == ADMIN and doc["filename"].startswith("backup_") and "پشتیبان" in doc["caption"]
        from config import DATA_DIR
        zips = sorted(os.listdir(os.path.join(DATA_DIR, "backups")))
        with zipfile.ZipFile(os.path.join(DATA_DIR, "backups", zips[-1])) as z:
            names = z.namelist()
        assert "bot.sqlite3" in names and "ayani_rates.json" in names
        t = datetime.datetime(2026, 10, 7, 2, 0)
        assert ops.backup_due(3, t) is False
        t = t.replace(hour=4)
        assert ops.backup_due(3, t) is True
        ops.mark_backup_sent(t)
        assert ops.backup_due(3, t) is False
        await h.send("/backup", uid=USER)                  # کاربر عادی
        assert h.documents[-1]["chat_id"] == ADMIN and len(h.documents) == 1
    run(go())


def test_admin_help_lists_commands(h):
    async def go():
        await h.send("/admin", uid=ADMIN)
        text = h.session.last().text
        for cmd in ("/broadcast", "/discount_new", "/backup", "/taxstatus", "/subs_excel", "/office", "/maptest"):
            assert cmd in text
        n = len(h.session.calls)
        await h.send("/admin", uid=USER)
        assert not any("دستورهای مدیر" in (getattr(m, "text", "") or "") for m in h.session.calls[n:])
    run(go())


# ══════════════════════════════════════════════════════════════════
# نقشه در PDF
# ══════════════════════════════════════════════════════════════════
def test_pdf_includes_map_when_service_available(h, monkeypatch):
    async def go():
        import io
        import map_image
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (800, 420), (200, 220, 240)).save(buf, "PNG")
        calls = []

        def fake_png(lat, lng, **kw):
            calls.append((lat, lng))
            return buf.getvalue()
        monkeypatch.setattr(map_image, "fetch_png", fake_png)
        await access_control.activate_subscription(USER, "monthly", 30)
        await h.send("🗺️ استعلام ارزش منطقه‌ای ملک")
        await h.fill_form()
        await h.send("✅ تایید و ادامه")
        doc = [d for d in h.documents if d["pages"]][-1]
        assert calls and abs(calls[0][0] - 35.70) < 1e-6
        from pypdf import PdfReader  # noqa: F401
        assert doc["size"] > 0
        q = db.query_one("SELECT extra FROM queries ORDER BY created_at DESC LIMIT 1")
        assert "35.7" in (q["extra"] or "")
    run(go())


def test_map_fetch_failure_has_cooldown(monkeypatch):
    import map_image
    import requests
    monkeypatch.setattr(map_image, "_disabled_until", 0.0)
    monkeypatch.setattr(map_image, "_config", lambda: (True, "KEY"))
    n = []

    def boom(*a, **k):
        n.append(1)
        raise requests.ConnectionError("no net")
    monkeypatch.setattr(requests, "get", boom)
    assert map_image.fetch_png(35.7, 51.4) is None
    assert map_image.fetch_png(35.7, 51.4) is None
    assert len(n) == 1 and "no net" in map_image.last_error
    monkeypatch.setattr(map_image, "_disabled_until", 0.0)
