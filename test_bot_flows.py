# -*- coding: utf-8 -*-
"""
تست سرتاسری ربات با یک Bot جعلی (بدون اینترنت): پیام‌ها از Dispatcher واقعی عبور
می‌کنند، سامانهٔ مالیاتی/نقشه و ارسال فایل شبیه‌سازی می‌شوند.

پوشش: فرم کامل استعلام با storage پایدار، ثبت در سوابق با کد رهگیری، QR/کد روی PDF،
دریافت دوبارهٔ PDF، بررسی اصالت (دکمه و لینک /start)، گزارش تجمیعی دو ملک،
محاسبهٔ مجدد از سوابق، یادآوری تمدید و اکسل اشتراک‌ها.
"""
import asyncio
import datetime
import itertools
import os
import time

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.types import (CallbackQuery, Chat, Location, Message, Update, User)

import access_control
import db
import history

ADMIN = 999
USER = 1001
COLLEAGUE = 1002

TAX = {
    "فیلدهای_ساختاریافته": {
        "اداره کل امور مالیاتی": "اداره کل امور مالیاتی شهر تهران",
        "شماره بلوک بر اساس دفترچه ارزش معاملاتی ملک": "54",
        "شماره ردیف بر اساس دفترچه ارزش معاملاتی ملک": "12",
        "ارزش معاملاتی مسکونی": "80,000,000",
        "ارزش معاملاتی اداری": "80,000,000",
        "ارزش معاملاتی تجاری": "200,000,000",
    },
    "سال": "1405",
}


# ══════════════════════════════════════════════════════════════════
# Bot جعلی
# ══════════════════════════════════════════════════════════════════
class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self._ids = itertools.count(1)

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        name = type(method).__name__
        if name in ("SendMessage", "EditMessageText", "SendInvoice", "SendLocation"):
            chat_id = getattr(method, "chat_id", None) or USER
            return Message(message_id=next(self._ids), date=datetime.datetime.now(),
                           chat=Chat(id=int(chat_id), type="private"), text=getattr(method, "text", None))
        return True

    async def close(self):
        pass

    async def stream_content(self, *a, **k):  # pragma: no cover
        yield b""

    def texts(self, chat_id=None):
        return [m.text for m in self.calls
                if type(m).__name__ in ("SendMessage", "EditMessageText")
                and (chat_id is None or int(getattr(m, "chat_id", 0) or 0) == chat_id or chat_id is None)]

    def last(self, kind="SendMessage"):
        return [m for m in self.calls if type(m).__name__ == kind][-1]


class Harness:
    def __init__(self, monkeypatch):
        import admin_commands
        import menu_handlers
        import regional_value_handlers as rvh
        import utilities_handlers
        from aiogram import Router
        from sqlite_storage import SQLiteStorage

        self.rvh = rvh
        self.session = FakeSession()
        self.bot = Bot("123456:TEST-TOKEN", session=self.session)
        self.dp = Dispatcher(storage=SQLiteStorage())
        # روترها ماژول-سطح هستند؛ برای هر تست یک روتر والد تازه می‌سازیم
        routers = (admin_commands.admin_router, menu_handlers.menu_router,
                   utilities_handlers.utilities_router, rvh.regional_value_router)
        for r in routers:
            r._parent_router = None
        parent = Router()
        parent.include_routers(*routers)
        self.dp.include_router(parent)
        self.ids = itertools.count(1)
        self.cb_ids = itertools.count(1)

        # ── شبیه‌سازی سرویس‌های بیرونی ──
        monkeypatch.setattr(rvh, "_do_query_sync",
                            lambda province, address, lat, lng: {"tax_info": TAX, "geocoded": {}})
        import ayani_calc
        monkeypatch.setattr(ayani_calc, "detect_location_names", lambda lat, lng: ["تهران"])
        import geocode_and_query
        monkeypatch.setattr(geocode_and_query, "reverse_geocode", lambda lat, lng: "تهران، خیابان آزمایشی")
        self.documents = []

        async def fake_send_document(chat_id, file_path, filename=None, caption=None, reply_markup=None):
            with open(file_path, "rb") as f:
                head = f.read(4)
            pages, text = 0, ""
            if head == b"%PDF":
                from pypdf import PdfReader
                reader = PdfReader(file_path)
                pages = len(reader.pages)
                text = "".join(p.extract_text() or "" for p in reader.pages[:1])
            self.documents.append({"chat_id": chat_id, "filename": filename, "caption": caption,
                                   "pages": pages, "text": text, "size": os.path.getsize(file_path)})
            return True
        monkeypatch.setattr(rvh, "send_document_direct", fake_send_document)
        monkeypatch.setattr(menu_handlers, "send_document_direct", fake_send_document)
        import bale_file_sender
        monkeypatch.setattr(bale_file_sender, "send_document_direct", fake_send_document)
        self.photos = []

        async def fake_send_photo(chat_id, file_path, caption=None, reply_markup=None):
            from PIL import Image
            self.photos.append(Image.open(file_path).size)
            return True
        monkeypatch.setattr(bale_file_sender, "send_photo_direct", fake_send_photo)

    def _user(self, uid):
        return User(id=uid, is_bot=False, first_name=f"کاربر{uid}")

    async def send(self, text=None, uid=USER, location=None, **extra):
        msg = Message(message_id=next(self.ids), date=datetime.datetime.now(),
                      chat=Chat(id=uid, type="private"), from_user=self._user(uid), text=text,
                      location=location, **extra)
        await self.dp.feed_update(self.bot, Update(update_id=next(self.ids), message=msg))

    async def click(self, data, uid=USER):
        msg = Message(message_id=next(self.ids), date=datetime.datetime.now(),
                      chat=Chat(id=uid, type="private"), from_user=self._user(0), text="-")
        cb = CallbackQuery(id=str(next(self.cb_ids)), from_user=self._user(uid), chat_instance="x",
                           message=msg, data=data)
        await self.dp.feed_update(self.bot, Update(update_id=next(self.ids), callback_query=cb))

    async def state(self, uid=USER):
        from aiogram.fsm.storage.base import StorageKey
        return await self.dp.storage.get_state(StorageKey(bot_id=self.bot.id, chat_id=uid, user_id=uid))

    def last_keyboard(self):
        for m in reversed(self.session.calls):
            rm = getattr(m, "reply_markup", None)
            if rm is not None and hasattr(rm, "keyboard"):
                return [b.text for row in rm.keyboard for b in row]
        return []

    def last_inline(self):
        for m in reversed(self.session.calls):
            rm = getattr(m, "reply_markup", None)
            if rm is not None and hasattr(rm, "inline_keyboard"):
                return [(b.text, b.callback_data) for row in rm.inline_keyboard for b in row]
        return []

    async def fill_form(self, uid=USER, area="200", building=True):
        """پاسخ به همهٔ مراحل فرم تا پیش‌نمایش (با گزینهٔ اول هر کیبورد، مگر عددی)."""
        numeric = {"waiting_area": area, "waiting_bld_area": "350", "waiting_bld_floor": "3",
                   "waiting_bld_age": "10", "waiting_bld_parking_area": "20"}
        for _ in range(40):
            st = (await self.state(uid) or "").split(":")[-1]
            if st == "waiting_preview":
                return
            if st == "waiting_province":
                await self.send("تهران", uid)
            elif st == "waiting_address":
                await self.send(None, uid, location=Location(latitude=35.70, longitude=51.40))
            elif st in numeric:
                await self.send(numeric[st], uid)
            elif st == "waiting_has_building":
                await self.send("✅ بله" if building else "❌ خیر", uid)
            elif st == "waiting_bld_parking":
                await self.send("❌ خیر", uid)
            else:
                kb = [t for t in self.last_keyboard() if "بازگشت" not in t and "شروع مجدد" not in t]
                assert kb, f"کیبوردی برای مرحلهٔ {st} نیست"
                await self.send(kb[0], uid)
        raise AssertionError("فرم به پیش‌نمایش نرسید")


@pytest.fixture
def h(monkeypatch):
    db.init(":memory:")
    return Harness(monkeypatch)


def run(coro):
    return asyncio.run(coro)


# ══════════════════════════════════════════════════════════════════
# سناریوها
# ══════════════════════════════════════════════════════════════════
def test_full_query_saves_history_and_pdf_has_tracking_code(h):
    async def go():
        await access_control.activate_subscription(USER, "monthly", 30)
        await h.send("/start")
        assert "سوابق استعلام" in " ".join(h.last_keyboard())
        await h.send("🗺️ استعلام ارزش منطقه‌ای ملک")
        await h.fill_form()
        await h.send("✅ تایید و ادامه")

        rows = history.list_user_queries(USER)
        assert len(rows) == 1
        code = rows[0]["code"]
        assert code.startswith("RV-") and rows[0]["total"] > 0
        # پیام نتیجه و کپشن PDF هر دو کد رهگیری دارند
        assert any(code in (t or "") for t in h.session.texts())
        doc = h.documents[-1]
        assert doc["pages"] == 2 and code in doc["caption"] and code in doc["filename"]
        # کیبورد پس از نتیجه: افزودن ملک دیگر
        assert any("افزودن ملک دیگر" in t for t in h.last_keyboard())
        assert await h.state() is None
        # ورودی‌های فرم برای «محاسبهٔ مجدد» ذخیره شده‌اند
        q = history.get_query(code)
        assert q["inputs"]["rv_province"] == "تهران" and q["inputs"]["rv_area"] == 200
        ev = {r["kind"] for r in db.query("SELECT kind FROM events")}
        assert {"form_start", "preview_confirm", "query_ok"} <= ev
    run(go())


def test_form_survives_restart_with_persistent_storage(h, monkeypatch):
    async def go():
        await access_control.activate_subscription(USER, "monthly", 30)
        await h.send("🗺️ استعلام ارزش منطقه‌ای ملک")
        await h.send("تهران")
        st = await h.state()
        # «ری‌استارت»: Dispatcher و storage جدید روی همان پایگاه داده
        h2 = Harness(monkeypatch)
        assert await h2.state() == st
        await h2.fill_form()
        await h2.send("✅ تایید و ادامه")
        assert history.count_user_queries(USER) == 1
    run(go())


def test_history_resend_pdf_and_verify(h):
    async def go():
        await access_control.activate_subscription(USER, "monthly", 30)
        await h.send("🗺️ استعلام ارزش منطقه‌ای ملک")
        await h.fill_form(building=False)
        await h.send("✅ تایید و ادامه")
        code = history.list_user_queries(USER)[0]["code"]
        credits_before = await access_control.free_retry_count(USER)

        await h.send("📂 سوابق استعلام")
        assert code in h.session.last().text
        assert ("۱. " + code, f"h:{code}") in h.last_inline()
        await h.click(f"h:{code}")
        assert ("📄 دریافت دوبارهٔ PDF", f"hpdf:{code}") in h.last_inline()
        n_docs = len(h.documents)
        await h.click(f"hpdf:{code}")
        assert len(h.documents) == n_docs + 1 and "نسخهٔ مجدد" in h.documents[-1]["caption"]
        assert await access_control.free_retry_count(USER) == credits_before
        # کاربر دیگر به این استعلام دسترسی ندارد
        n_docs = len(h.documents)
        await h.click(f"hpdf:{code}", uid=COLLEAGUE)
        assert len(h.documents) == n_docs

        # بررسی اصالت از منو (با فاصله و حروف کوچک) و از لینک QR
        await h.send("🔎 بررسی اصالت گزارش", uid=COLLEAGUE)
        await h.send(code.lower().replace("-", " "), uid=COLLEAGUE)
        assert "معتبر است" in h.session.last().text
        await h.send(f"/start v_{code.replace('-', '')}", uid=COLLEAGUE)
        assert "معتبر است" in h.session.last().text
        await h.send("/start v_RV2222AAAA", uid=COLLEAGUE)
        assert "ثبت نشده" in h.session.last().text
    run(go())


def test_batch_of_two_properties(h):
    async def go():
        await access_control.activate_subscription(USER, "monthly", 30)
        await h.send("🗺️ استعلام ارزش منطقه‌ای ملک")
        await h.fill_form(area="100", building=False)
        await h.send("✅ تایید و ادامه")
        await h.send("➕ افزودن ملک دیگر (گزارش تجمیعی)")
        await h.fill_form(area="300", building=True)
        await h.send("✅ تایید و ادامه")

        rows = history.list_user_queries(USER)
        assert len(rows) == 2 and rows[0]["batch_code"] == rows[1]["batch_code"]
        batch = rows[0]["batch_code"]
        pdf_btn = [t for t in h.last_keyboard() if "گزارش تجمیعی" in t and "دریافت" in t]
        assert pdf_btn and "۲" in pdf_btn[0]
        await h.send(pdf_btn[0])
        doc = h.documents[-1]
        assert doc["pages"] == 1 + 2 + 2 and batch in doc["caption"]
        total = sum(r["total"] for r in rows)
        assert f"{total:,}".translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")) in doc["caption"]
        # اصالت گزارش تجمیعی
        await h.send(f"/verify {batch}", uid=COLLEAGUE)
        assert "تجمیعی معتبر است" in h.session.last().text
    run(go())


def test_recalculate_from_history_goes_to_preview(h):
    async def go():
        await access_control.activate_subscription(USER, "monthly", 30)
        await h.send("🗺️ استعلام ارزش منطقه‌ای ملک")
        await h.fill_form(area="150")
        await h.send("✅ تایید و ادامه")
        code = history.list_user_queries(USER)[0]["code"]
        await h.click(f"hedit:{code}")
        assert (await h.state()).endswith("waiting_preview")
        assert "۱۵۰" in h.session.last().text or "150" in h.session.last().text
        await h.send("✅ تایید و ادامه")
        rows = history.list_user_queries(USER)
        assert len(rows) == 2 and rows[0]["code"] != code and rows[0]["total"] == rows[1]["total"]
    run(go())


def test_renew_reminder_and_renew_button(h):
    async def go():
        import menu_handlers
        now = time.time()
        db.execute("INSERT INTO subscriptions(user_id, plan, expires_at, updated_at) VALUES (?,?,?,?)",
                   (USER, "monthly", now + 2 * 86400, now))
        assert await menu_handlers.send_renew_reminders(h.bot, 3) == 1
        assert await menu_handlers.send_renew_reminders(h.bot, 3) == 0
        m = h.session.last()
        assert "به پایان می‌رسد" in m.text and m.reply_markup.inline_keyboard[0][0].callback_data == "acc:renew"
        await h.click("acc:renew")
        assert (await h.state()).endswith("waiting_plan")
        assert "اضافه می‌شود" in h.session.last().text
    run(go())


def test_payment_is_recorded_for_stats(h):
    async def go():
        from aiogram.types import SuccessfulPayment
        import admin_commands
        import json
        sp = SuccessfulPayment(currency="IRR", total_amount=12_000_000,
                               invoice_payload=json.dumps({"type": "rv_sub", "plan": "monthly", "uid": USER}),
                               telegram_payment_charge_id="t1", provider_payment_charge_id="p1")
        msg = Message(message_id=1, date=datetime.datetime.now(), chat=Chat(id=USER, type="private"),
                      from_user=User(id=USER, is_bot=False, first_name="x"), successful_payment=sp)
        await h.dp.feed_update(h.bot, Update(update_id=5000, message=msg))
        assert await access_control.get_subscription(USER) is not None
        assert db.query_one("SELECT COUNT(*) n FROM payments")["n"] == 1
        text = admin_commands.build_stats_text()
        assert "۱٬۲۰۰٬۰۰۰" in text or "۱,۲۰۰,۰۰۰" in text
        await h.send("/stats", uid=ADMIN)
        assert "آمار ربات" in h.session.last().text
    run(go())


def test_admin_subscription_excel_roundtrip(h, monkeypatch):
    async def go():
        import os
        import shutil
        import tempfile
        from aiogram.types import Document
        from openpyxl import load_workbook
        import admin_commands
        import subs_admin

        # خریدار قبلی + /office
        await access_control.activate_subscription(USER, "monthly", 30)
        await h.send(f"/office {USER} 4321 دفتر خدمات الکترونیک قضایی شمارهٔ ۷", uid=ADMIN)
        assert "شمارهٔ ۷" in h.session.last().text
        # غیرادمین نمی‌تواند
        await h.send(f"/office {USER} 1 x", uid=USER)
        assert access_control.get_office_info(USER)["office_code"] == "4321"

        # /subs_excel → فایل اکسل (ارسال مستقیم شبیه‌سازی می‌شود)
        sent = {}

        async def fake_send(chat_id, file_path, filename=None, caption=None, reply_markup=None):
            sent["path"] = shutil.copy(file_path, tempfile.mkdtemp())
            sent["caption"] = caption
            return True
        monkeypatch.setattr("bale_file_sender.send_document_direct", fake_send)
        await h.send("/subs_excel", uid=ADMIN)
        wb = load_workbook(sent["path"])
        ws = wb[subs_admin.SHEET_TITLE]
        assert ws.cell(2, subs_admin.IDX["office_name"] + 1).value == "دفتر خدمات الکترونیک قضایی شمارهٔ ۷"
        # ادمین یک اشتراک دستی جدید اضافه می‌کند
        ws.append([COLLEAGUE, "دفتر جدید", "9999", "دستی", "", 60])
        wb.save(sent["path"])

        async def fake_download(file, destination=None, **kw):
            shutil.copy(sent["path"], destination)
        monkeypatch.setattr(h.bot, "download", fake_download)
        doc = Document(file_id="f1", file_unique_id="u1", file_name="اشتراک‌ها.xlsx")
        msg = Message(message_id=900, date=datetime.datetime.now(), chat=Chat(id=ADMIN, type="private"),
                      from_user=User(id=ADMIN, is_bot=False, first_name="admin"), document=doc)
        await h.dp.feed_update(h.bot, Update(update_id=9000, message=msg))
        preview = h.session.last().text
        assert "پیش‌نمایش تغییرات اشتراک‌ها" in preview and str(COLLEAGUE) in preview
        assert await access_control.get_subscription(COLLEAGUE) is None  # هنوز اعمال نشده

        await h.click("subs_apply", uid=ADMIN)
        assert await access_control.get_subscription(COLLEAGUE) is not None
        assert access_control.get_office_info(COLLEAGUE)["office_name"] == "دفتر جدید"
        texts = h.session.texts()
        assert any("توسط مدیر سامانه" in (t or "") for t in texts)  # اطلاع به کاربر
        assert "ثبت شد" in texts[-1]
    run(go())



# ══════════════════════════════════════════════════════════════════
# 🧰 خدمات کاربردی
# ══════════════════════════════════════════════════════════════════
def _util_state(st):
    return (st or "").split(":")[-1]


def test_utilities_one_free_use_per_tool_then_paywall(h):
    async def go():
        import utilities_handlers as uh
        await h.send("🧰 خدمات کاربردی")
        assert _util_state(await h.state()) == "menu"
        assert "هر بخش ۱ بار رایگان" in h.session.last().text

        # هزینهٔ دادرسی: یک مبلغ = یک استفاده (چند مرحله برای همان مبلغ آزاد است)
        await h.send(uh.TOOLS["court_fee"])
        assert "۱ استفادهٔ رایگان" in h.session.last().text
        await h.send("500000000")
        await h.send(list(uh.CF_CATEGORIES)[0])
        assert "15,500,000" in h.session.last().text
        await h.send(list(uh.CF_CATEGORIES)[1])
        assert "22,500,000" in h.session.last().text
        assert access_control.util_used(USER, "court_fee") == 1
        await h.send(uh.CF_NEW_AMOUNT)
        assert _util_state(await h.state()) == "plan"
        assert "۸۰۰,۰۰۰ تومان" in h.session.last().text and "۸,۰۰۰,۰۰۰ تومان" in h.session.last().text

        # بخش دیگر (تمبر) هنوز رایگان است
        await h.send("🔙 بازگشت")
        await h.send(uh.TOOLS["stamp"])
        await h.send(uh.STAMP_BY_FEE)
        await h.send("1000000000")
        assert any("۸۷٬۵۰۰٬۰۰۰" in (t or "") for t in h.session.texts()[-3:])
        await h.send(uh.STAMP_BY_FEE)
        assert _util_state(await h.state()) == "plan"

        # خسارت تأخیر: یک محاسبه رایگان با خروجی PDF، دومی ← اشتراک
        await h.send("🔙 بازگشت")
        await h.send(uh.TOOLS["damages"])
        await h.send(uh.DMG_LATE_TEXT)
        await h.send("500000000")
        await h.send("1403/07/01")
        await h.send("1405/07/11")
        doc = h.documents[-1]
        assert doc["pages"] == 1 and "1,288,925,398" in doc["caption"]
        await h.send(uh.DMG_MAHR_TEXT)
        assert _util_state(await h.state()) == "plan"
        assert db.query_one("SELECT COUNT(*) n FROM events WHERE kind='util_paywall'")["n"] == 3
    run(go())


def test_utilities_purchase_unlocks_all_tools_but_not_regional_value(h):
    async def go():
        import json
        from aiogram.types import SuccessfulPayment
        import utilities_handlers as uh
        access_control.record_util_use(USER, "court_fee")       # رایگان مصرف شده
        await h.send("🧰 خدمات کاربردی")
        await h.send(uh.TOOLS["court_fee"])
        assert _util_state(await h.state()) == "plan"
        await h.send(uh._plan_button("monthly"))   # بدون توکن کیف‌پول فاکتور ساخته نمی‌شود
        assert "امکان ساخت فاکتور" in h.session.last().text

        sp = SuccessfulPayment(currency="IRR", total_amount=8_000_000,
                               invoice_payload=json.dumps({"type": "util_sub", "plan": "monthly", "uid": USER}),
                               telegram_payment_charge_id="t", provider_payment_charge_id="p")
        msg = Message(message_id=77, date=datetime.datetime.now(), chat=Chat(id=USER, type="private"),
                      from_user=User(id=USER, is_bot=False, first_name="x"), successful_payment=sp)
        await h.dp.feed_update(h.bot, Update(update_id=7700, message=msg))
        assert await access_control.get_subscription(USER, "util") is not None
        assert await access_control.get_subscription(USER) is None            # ارزش منطقه‌ای جداست
        assert db.query_one("SELECT plan FROM payments")["plan"] == "util:monthly"
        assert any("خدمات کاربردی" in (t or "") for t in h.session.texts(ADMIN))
        assert _util_state(await h.state()) == "menu"
        await h.send(uh.TOOLS["court_fee"])
        assert "اشتراک خدمات کاربردی فعال" in h.session.last().text
        await h.send("100000000")
        await h.send(list(uh.CF_CATEGORIES)[0])
        assert "2,500,000" in h.session.last().text
    run(go())


def test_pre_checkout_accepts_utilities_invoice(h):
    async def go():
        import json
        from aiogram.types import PreCheckoutQuery
        q = PreCheckoutQuery(id="q1", from_user=User(id=USER, is_bot=False, first_name="x"), currency="IRR",
                             total_amount=8_000_000,
                             invoice_payload=json.dumps({"type": "util_sub", "plan": "monthly", "uid": USER}))
        await h.dp.feed_update(h.bot, Update(update_id=8800, pre_checkout_query=q))
        assert h.session.last("AnswerPreCheckoutQuery").ok is True
        bad = q.model_copy(update={"id": "q2", "total_amount": 1})
        await h.dp.feed_update(h.bot, Update(update_id=8801, pre_checkout_query=bad))
        assert h.session.last("AnswerPreCheckoutQuery").ok is False
    run(go())


def test_approved_user_has_unlimited_utilities(h):
    async def go():
        import utilities_handlers as uh
        await access_control.approve_user(USER)
        await h.send("🧰 خدمات کاربردی")
        await h.send(uh.TOOLS["stamp"])
        for _ in range(3):
            await h.send(uh.STAMP_MALI)
            await h.send("1000000000")
        assert access_control.util_used(USER, "stamp") == 3
        assert _util_state(await h.state()) == "stamp_type"
    run(go())


def test_file_tools_compress_and_pdf_to_images(h, monkeypatch):
    async def go():
        import os
        import shutil
        import tempfile
        from aiogram.types import Document
        from PIL import Image
        from reportlab.pdfgen import canvas
        import utilities_handlers as uh

        d = tempfile.mkdtemp()
        img_path = os.path.join(d, "big.png")
        Image.effect_noise((1600, 1200), 80).convert("RGB").save(img_path)
        pdf_path = os.path.join(d, "doc.pdf")
        c = canvas.Canvas(pdf_path)
        for i in range(3):
            c.drawString(100, 700, f"page {i + 1}")
            c.showPage()
        c.save()
        sources = {"img": img_path, "pdf": pdf_path}

        async def fake_download(file, destination=None, **kw):
            shutil.copy(sources[file.file_id], destination)
        monkeypatch.setattr(h.bot, "download", fake_download)

        await h.send("🧰 خدمات کاربردی")
        await h.send(uh.TOOLS["file_tools"])
        await h.send(uh.FT_COMPRESS)
        await h.send(None, document=Document(file_id="img", file_unique_id="u1", file_name="big.png",
                                             mime_type="image/png"))
        assert "کاهش حجم انجام شد" in h.documents[-1]["caption"]
        assert h.documents[-1]["size"] <= 500 * 1024
        assert access_control.util_used(USER, "file_tools") == 1
        # استفادهٔ رایگان تمام شده → عکس بعدی پردازش نمی‌شود
        n_docs = len(h.documents)
        await h.send(None, document=Document(file_id="img", file_unique_id="u2", file_name="big.png",
                                             mime_type="image/png"))
        assert len(h.documents) == n_docs
        assert _util_state(await h.state()) == "plan"
        assert h.photos == []

        # با اشتراک: PDF سه‌صفحه‌ای → سه عکس
        await access_control.activate_subscription(USER, "monthly", 30, product="util")
        await h.send("🔙 بازگشت")
        await h.send(uh.TOOLS["file_tools"])
        await h.send(uh.FT_PDF2IMG)
        await h.send(None, document=Document(file_id="pdf", file_unique_id="u3", file_name="doc.pdf",
                                             mime_type="application/pdf"))
        assert len(h.photos) == 3
        assert any("۳ عکس جداگانه" in (t or "") for t in h.session.texts())
    run(go())


def test_account_and_reminder_for_utilities(h):
    async def go():
        import menu_handlers
        await h.send("👤 حساب کاربری و اشتراک")
        assert "خدمات کاربردی: اشتراک ندارد" in h.session.last().text
        assert ("🧰 خرید / تمدید اشتراک خدمات کاربردی", "acc:util") in h.last_inline()
        await h.click("acc:util")
        assert _util_state(await h.state()) == "plan"

        now = time.time()
        db.execute("INSERT INTO util_subscriptions(user_id, plan, expires_at, updated_at) VALUES (?,?,?,?)",
                   (USER, "monthly", now + 86400, now))
        assert await menu_handlers.send_renew_reminders(h.bot, 3) == 1
        m = h.session.last()
        assert "خدمات کاربردی" in m.text and m.reply_markup.inline_keyboard[0][0].callback_data == "acc:util"
    run(go())


# ══════════════════════════════════════════════════════════════════
# ⚖️ دیه و 👪 سهم‌الارث
# ══════════════════════════════════════════════════════════════════
def test_diyeh_flow_organ_female_and_paywall(h):
    async def go():
        import diyeh_calc as dyc
        import legal_tools_handlers as lt
        import utilities_handlers as uh
        await h.send("🧰 خدمات کاربردی")
        await h.send(uh.TOOLS["diyeh"])
        assert "21,000,000,000" in h.session.last().text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
        await h.send("۱۴۰۵")
        await h.send(lt.DY_METHOD_BUTTONS[dyc.METHOD_ORGAN])
        cat = dyc.categories().index("چشم")
        await h.send(str(cat + 1))
        item = next(i for i, it in enumerate(dyc.items(cat)) if it["title"] == "درآوردن یا از بین بردن یک چشم بینا")
        await h.send(str(item + 1))
        await h.send("1")                      # تعداد
        assert _util_state(await h.state()) == "dy_sex"
        await h.send("👩 زن")
        assert any("5,250,000,000 ریال" in (t or "") for t in h.session.texts()[-4:])
        assert h.documents[-1]["pages"] == 1 and "5,250,000,000" in h.documents[-1]["caption"]
        assert access_control.util_used(USER, "diyeh") == 1
        # استفادهٔ دوم ← پیشنهاد اشتراک
        await h.send(lt.DY_METHOD_BUTTONS[dyc.METHOD_HOMICIDE])
        assert _util_state(await h.state()) == "plan"
    run(go())


def test_diyeh_homicide_holy_month_and_textual_rule(h):
    async def go():
        import diyeh_calc as dyc
        import legal_tools_handlers as lt
        import utilities_handlers as uh
        await access_control.approve_user(USER)
        await h.send("🧰 خدمات کاربردی")
        await h.send(uh.TOOLS["diyeh"])
        await h.send("1405")
        await h.send(lt.DY_METHOD_BUTTONS[dyc.METHOD_HOMICIDE])
        await h.send("👨 مرد")
        await h.send(lt.YES)
        assert any("28,000,000,000 ریال" in (t or "") for t in h.session.texts()[-4:])
        # حکم متنی: بدون مبلغ و بدون PDF
        n_docs = len(h.documents)
        await h.send(lt.DY_METHOD_BUTTONS[dyc.METHOD_ORGAN])
        await h.send("1")                      # استخوان
        await h.send("1")                      # شکستن استخوان هر عضو
        assert any("یک پنجم دیه آن عضو" in (t or "") for t in h.session.texts()[-3:])
        assert len(h.documents) == n_docs
        # کسری
        await h.send(lt.DY_METHOD_BUTTONS[dyc.METHOD_FRACTION])
        await h.send("1/2 از ۱/۳")
        assert any("3,500,000,000 ریال" in (t or "") for t in h.session.texts()[-4:])
    run(go())


def test_inheritance_board_flow(h):
    async def go():
        import legal_tools_handlers as lt
        import utilities_handlers as uh
        await h.send("🧰 خدمات کاربردی")
        await h.send(uh.TOOLS["inheritance"])
        await h.send("👨 متوفی مرد بود و همسر داشت")
        await h.send("۱")                                   # یک همسر
        assert _util_state(await h.state()) == "inh_board"
        buttons = [d for _, d in h.last_inline()]
        assert "inh:tog:pedar" in buttons and "inh:inc:pesar" in buttons and "inh:scr:br" in buttons
        await h.click("inh:tog:pedar")
        await h.click("inh:tog:madar")
        assert "inh:inc:baradarAbaveyni" in [d for _, d in h.last_inline()]   # ردیف حجب مادر
        await h.click("inh:inc:pesar")
        for _ in range(2):
            await h.click("inh:inc:dokhtar")
        await h.click("inh:dec:dokhtar")
        await h.click("inh:inc:dokhtar")
        assert "inh:scr:br" not in [d for _, d in h.last_inline()]          # با فرزند زنده، نوه مطرح نیست
        await h.click("inh:next")
        assert _util_state(await h.state()) == "inh_estate"
        await h.send("36000000000")
        text = next(t for t in reversed(h.session.texts()) if t and "نتیجهٔ تقسیم ترکه" in t)
        assert "4,500,000,000 ریال" in text      # همسر ۱/۸
        assert "9,750,000,000 ریال" in text      # پسر ۱۳/۴۸
        assert "4,875,000,000 ریال" in text      # هر دختر ۱۳/۹۶
        assert h.documents[-1]["pages"] >= 1
        assert access_control.util_used(USER, "inheritance") == 1
    run(go())


def test_inheritance_empty_classes_move_to_next_and_grandchildren(h):
    async def go():
        import utilities_handlers as uh
        await access_control.approve_user(USER)
        await h.send("🧰 خدمات کاربردی")
        await h.send(uh.TOOLS["inheritance"])
        await h.send("🚫 متوفی همسر نداشت")
        # نوه‌ها: یک پسر فوت‌شده با ۱ نوهٔ پسر و ۱ نوهٔ دختر
        await h.click("inh:scr:br")
        await h.click("inh:badd")
        await h.click("inh:inc:b0gs")
        await h.click("inh:inc:b0gd")
        await h.click("inh:scr:c1")
        assert "۱ شاخه" in " ".join(t for t, _ in h.last_inline())
        await h.click("inh:next")
        await h.send("⏭ بدون مبلغ ترکه")
        text = next(t for t in reversed(h.session.texts()) if t and "نتیجهٔ تقسیم ترکه" in t)
        assert "2/3" in text and "1/3" in text

        # طبقهٔ اول خالی ← طبقهٔ دوم
        await h.send("🚫 متوفی همسر نداشت")
        await h.click("inh:next")
        assert "inh:inc:siblingOmmi" in [d for _, d in h.last_inline()]
        await h.click("inh:inc:baradarAbaveyni")
        await h.click("inh:inc:siblingOmmi")
        await h.click("inh:next")
        await h.send("⏭ بدون مبلغ ترکه")
        text = next(t for t in reversed(h.session.texts()) if t and "نتیجهٔ تقسیم ترکه" in t)
        assert "1/6" in text and "5/6" in text
    run(go())


def test_admin_diyeh_rate(h, monkeypatch, tmp_path):
    async def go():
        import diyeh_calc as dyc
        monkeypatch.setattr(dyc, "RATES_FILE", str(tmp_path / "rates.json"))
        await h.send("/diyeh_rate 1406 27000000000", uid=ADMIN)
        assert dyc.full_diyeh(1406) == 27_000_000_000
        await h.send("/diyeh_rate 1406 1", uid=USER)          # غیرادمین اثری ندارد
        assert dyc.full_diyeh(1406) == 27_000_000_000
    run(go())


def test_office_name_on_reports_and_verification(h, monkeypatch):
    async def go():
        import ayani_pdf
        import legal_pdf
        seen = []
        orig_rv, orig_dy = ayani_pdf.build_ayani_pdf, legal_pdf.build_diyeh_pdf

        def spy_rv(*a, **kw):
            seen.append(("rv", kw.get("issuer")))
            return orig_rv(*a, **kw)

        def spy_dy(path, r, issuer=None):
            seen.append(("dy", issuer))
            return orig_dy(path, r, issuer=issuer)
        monkeypatch.setattr(ayani_pdf, "build_ayani_pdf", spy_rv)
        monkeypatch.setattr(legal_pdf, "build_diyeh_pdf", spy_dy)

        access_control.set_office_info(USER, office_name="شمارهٔ ۷", office_code="4321")
        expected = "صادرکننده: دفتر خدمات الکترونیک قضایی شمارهٔ ۷ — کد 4321"
        await access_control.activate_subscription(USER, "monthly", 30)
        await h.send("🗺️ استعلام ارزش منطقه‌ای ملک")
        await h.fill_form(building=False)
        await h.send("✅ تایید و ادامه")
        assert ("rv", expected) in seen
        code = history.list_user_queries(USER)[0]["code"]
        assert history.get_query(code)["issuer"] == expected
        # دریافت دوباره: همان دفتر صادرکننده (حتی اگر بعداً نام دفتر عوض شود)
        access_control.set_office_info(USER, office_name="نام جدید")
        seen.clear()
        await h.click(f"hpdf:{code}")
        assert seen == [("rv", expected)]
        # بررسی اصالت دفتر صادرکننده را نشان می‌دهد
        await h.send(f"/verify {code}", uid=COLLEAGUE)
        assert expected in h.session.last().text

        # PDF دیه هم نام دفتر (فعلی) را دارد
        import diyeh_calc as dyc
        import legal_tools_handlers as lt
        import utilities_handlers as uh
        await h.send("🧰 خدمات کاربردی")
        await h.send(uh.TOOLS["diyeh"])
        await h.send("1405")
        await h.send(lt.DY_METHOD_BUTTONS[dyc.METHOD_HOMICIDE])
        await h.send("👨 مرد")
        await h.send(lt.NO)
        assert ("dy", "صادرکننده: دفتر خدمات الکترونیک قضایی نام جدید — کد 4321") in seen
    run(go())
