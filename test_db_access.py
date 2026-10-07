# -*- coding: utf-8 -*-
"""تست‌های پایگاه داده، کنترل دسترسی، یادآوری تمدید و سوابق — اجرا: pytest"""
import asyncio
import json
import os
import tempfile
import time
from decimal import Decimal

import pytest

import access_control as ac
import db
import history


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def fresh_db():
    db.init(":memory:")
    yield


# ══════════════════════════════════════════════════════════════════
# انتقال JSONهای قدیمی
# ══════════════════════════════════════════════════════════════════
def test_migrates_legacy_json_files():
    d = tempfile.mkdtemp()
    exp = time.time() + 5 * 86400
    files = {
        "approved_users.json": ["111", "222"],
        "free_retries.json": {"333": 2},
        "subscriptions.json": {"444": {"plan": "yearly", "expires_at": exp, "updated_at": 1}},
        "trial_requests.json": {"555": {"office_code": "1234", "national_id": "0012345678",
                                        "status": "approved", "created_at": 1}},
    }
    for name, data in files.items():
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            json.dump(data, f)
    db.init(os.path.join(d, "bot.sqlite3"), migrate_from=d)
    assert run(ac.is_approved(111)) and run(ac.is_approved(222))
    assert run(ac.free_retry_count(333)) == 2
    assert run(ac.get_subscription(444))["plan"] == "yearly"
    assert run(ac.get_trial_request(555))["status"] == "approved"
    # فایل‌ها کنار گذاشته شده‌اند تا دوباره منتقل نشوند
    assert all(os.path.exists(os.path.join(d, n + ".migrated")) for n in files)
    assert not any(os.path.exists(os.path.join(d, n)) for n in files)


# ══════════════════════════════════════════════════════════════════
# اعتبار و اشتراک
# ══════════════════════════════════════════════════════════════════
def test_free_credit_cannot_go_negative():
    run(ac.grant_free_retry(1, 2))
    assert run(ac.consume_free_retry(1))
    assert run(ac.consume_free_retry(1))
    assert not run(ac.consume_free_retry(1))
    assert run(ac.free_retry_count(1)) == 0


def test_subscription_renewal_extends_from_current_expiry():
    first = run(ac.activate_subscription(7, "monthly", 30))
    second = run(ac.activate_subscription(7, "monthly", 30))
    assert second["expires_at"] == pytest.approx(first["expires_at"] + 30 * 86400, abs=2)


def test_expired_subscription_is_not_active():
    db.execute("INSERT INTO subscriptions(user_id, plan, expires_at, updated_at) VALUES (8,'monthly',?,?)",
               (time.time() - 10, time.time()))
    assert run(ac.get_subscription(8)) is None


# ══════════════════════════════════════════════════════════════════
# یادآوری تمدید
# ══════════════════════════════════════════════════════════════════
def test_renew_reminders_sent_once_per_expiry():
    now = time.time()
    db.execute("INSERT INTO subscriptions(user_id, plan, expires_at, updated_at) VALUES (1,'monthly',?,?)",
               (now + 2 * 86400, now))
    db.execute("INSERT INTO subscriptions(user_id, plan, expires_at, updated_at) VALUES (2,'monthly',?,?)",
               (now + 20 * 86400, now))
    db.execute("INSERT INTO subscriptions(user_id, plan, expires_at, updated_at) VALUES (3,'monthly',?,?)",
               (now - 3600, now))
    due = {(r["user_id"], r["kind"]) for r in ac.due_renew_reminders(3)}
    assert due == {(1, "soon"), (3, "end")}
    for r in ac.due_renew_reminders(3):
        ac.mark_reminded(r["user_id"], r["kind"], r["expires_at"])
    assert ac.due_renew_reminders(3) == []
    # تمدید → تاریخ انقضای جدید → دورهٔ بعد دوباره یادآوری می‌شود
    db.execute("UPDATE subscriptions SET expires_at=? WHERE user_id=1", (now + 2.5 * 86400,))
    assert [(r["user_id"], r["kind"]) for r in ac.due_renew_reminders(3)] == [(1, "soon")]


# ══════════════════════════════════════════════════════════════════
# سوابق و کد رهگیری
# ══════════════════════════════════════════════════════════════════
CALC = {"land": {"area": Decimal("120.5"), "value": 100, "coef": Decimal("0.5")}, "building": None,
        "land_value": 100, "building_value": 0, "total": 100, "land_share": "1/2"}


def test_query_roundtrip_preserves_decimals_and_drops_transient_keys():
    data = {"rv_province": "تهران", "rv_area": 120.5, "rv_edit_snapshot": {"x": 1}, "rv_batch": None,
            "other": 1}
    code = history.save_query(5, data, province="تهران", county="تهران", address="آدرس", calc=CALC,
                              tax_result={"سال": "1405"}, plak="پلاک ۱", has_building=False)
    assert db.normalize_code(code) == code
    q = history.get_query(code)
    assert q["calc"]["land"]["area"] == Decimal("120.5") and isinstance(q["calc"]["land"]["coef"], Decimal)
    assert q["inputs"] == {"rv_province": "تهران", "rv_area": 120.5}
    assert q["plak"] == "پلاک ۱" and q["has_building"] is False
    assert history.list_user_queries(5)[0]["code"] == code
    assert history.list_user_queries(6) == []


def test_batch_collects_queries():
    kw = dict(province="تهران", county="تهران", address="a", calc=CALC, tax_result={}, plak=None,
              has_building=False)
    c1 = history.save_query(5, {}, **kw)
    batch = history.start_batch(5, c1)
    assert batch.startswith("RB-") and history.start_batch(5, c1) == batch
    history.save_query(5, {}, batch_code=batch, **kw)
    assert history.batch_count(batch) == 2
    assert history.batch_summary(batch)["total"] == 200
    with pytest.raises(KeyError):
        history.start_batch(6, c1)  # استعلام کاربر دیگر


@pytest.mark.parametrize("raw,expected", [
    ("RV-7K3M-X9PQ", "RV-7K3M-X9PQ"),
    ("rv 7k3m x9pq", "RV-7K3M-X9PQ"),
    ("۷K۳MX۹PQ", "RV-7K3M-X9PQ"),
    ("RB-Q2W3-E4R5", "RB-Q2W3-E4R5"),
    ("RV7K3MX9PQ", "RV-7K3M-X9PQ"),
    ("سلام", ""),
    ("RV-0000-0000", ""),   # ۰ و ۱ در الفبای کد نیستند
])
def test_normalize_code(raw, expected):
    assert db.normalize_code(raw) == expected


# ══════════════════════════════════════════════════════════════════
# ذخیرهٔ پایدار وضعیت فرم
# ══════════════════════════════════════════════════════════════════
def test_sqlite_fsm_storage_roundtrip():
    from aiogram.fsm.storage.base import StorageKey
    from sqlite_storage import SQLiteStorage

    async def go():
        s = SQLiteStorage()
        k = StorageKey(bot_id=1, chat_id=2, user_id=2)
        assert await s.get_state(k) is None and await s.get_data(k) == {}
        await s.set_state(k, "RVForm:waiting_area")
        await s.set_data(k, {"rv_province": "تهران", "rv_lat": 35.7, "nested": {"a": [1, None]}})
        assert await s.get_state(k) == "RVForm:waiting_area"
        assert (await s.get_data(k))["nested"] == {"a": [1, None]}
        await s.update_data(k, {"rv_area": 200})
        assert (await s.get_data(k))["rv_area"] == 200
        # یک نمونهٔ جدید storage (مثل ری‌استارت ربات) همان داده را می‌بیند
        s2 = SQLiteStorage()
        assert await s2.get_state(k) == "RVForm:waiting_area"
        await s2.set_state(k, None)
        await s2.set_data(k, {})
        assert db.query_one("SELECT COUNT(*) n FROM fsm")["n"] == 0
    run(go())
