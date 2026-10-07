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
        from aiogram import Router
        from sqlite_storage import SQLiteStorage

        self.rvh = rvh
        self.session = FakeSession()
        self.bot = Bot("123456:TEST-TOKEN", session=self.session)
        self.dp = Dispatcher(storage=SQLiteStorage())
        # روترها ماژول-سطح هستند؛ برای هر تست یک روتر والد تازه می‌سازیم
        for r in (admin_commands.admin_router, menu_handlers.menu_router, rvh.regional_value_router):
            r._parent_router = None
        parent = Router()
        parent.include_routers(admin_commands.admin_router, menu_handlers.menu_router,
                               rvh.regional_value_router)
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
            from pypdf import PdfReader
            reader = PdfReader(file_path)
            text = "".join(p.extract_text() or "" for p in reader.pages[:1])
            self.documents.append({"chat_id": chat_id, "filename": filename, "caption": caption,
                                   "pages": len(reader.pages), "text": text})
            return True
        monkeypatch.setattr(rvh, "send_document_direct", fake_send_document)
        monkeypatch.setattr(menu_handlers, "send_document_direct", fake_send_document)

    def _user(self, uid):
        return User(id=uid, is_bot=False, first_name=f"کاربر{uid}")

    async def send(self, text=None, uid=USER, location=None):
        msg = Message(message_id=next(self.ids), date=datetime.datetime.now(),
                      chat=Chat(id=uid, type="private"), from_user=self._user(uid), text=text,
                      location=location)
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
