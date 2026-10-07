# -*- coding: utf-8 -*-
"""تست‌های اکسل مدیریت اشتراک‌ها (subs_admin) — اجرا: pytest"""
import asyncio
import datetime
import os
import tempfile
import time

import pytest
from openpyxl import load_workbook

import access_control as ac
import db
import subs_admin as sa


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def fresh_db():
    db.init(":memory:")
    yield


def _export():
    path = os.path.join(tempfile.mkdtemp(), "subs.xlsx")
    sa.export_xlsx(path)
    return path


def _rows(path):
    ws = load_workbook(path)[sa.SHEET_TITLE]
    return ws, {ws.cell(r, 1).value: r for r in range(2, ws.max_row + 1) if ws.cell(r, 1).value}


def _col(key):
    return sa.IDX[key] + 1


@pytest.mark.parametrize("j", [(1405, 1, 1), (1405, 6, 31), (1405, 7, 30), (1403, 12, 30), (1399, 12, 30)])
def test_jalali_roundtrip(j):
    g = sa.jalali_to_gregorian(*j)
    assert sa.gregorian_to_jalali(*g) == j


def test_known_jalali_dates():
    assert sa.jalali_to_gregorian(1405, 1, 1) == (2026, 3, 21)
    assert sa.gregorian_to_jalali(2026, 10, 7) == (1405, 7, 15)


@pytest.mark.parametrize("raw,expected", [
    ("1405/08/15", (1405, 8, 15)), ("۱۴۰۵/۸/۱۵", (1405, 8, 15)), ("1405-08-15", (1405, 8, 15)),
    ("15/08/1405", (1405, 8, 15)), (datetime.datetime(1405, 8, 15), (1405, 8, 15)),
    ("1405/07/31", None), ("1405/13/01", None), ("فردا", None), ("2026/10/07", None),
])
def test_parse_jalali(raw, expected):
    assert sa.parse_jalali(raw) == expected


def test_export_contains_buyers_trial_info_and_payments():
    run(ac.activate_subscription(11, "monthly", 30))
    run(ac.save_trial_request(11, "4321", "0012345678", full_name="علی", username="ali"))
    ac.record_payment(11, "monthly", 12_000_000)
    run(ac.approve_user(12))
    ws, rows = _rows(_export())
    r = rows[11]
    assert ws.cell(r, _col("office_code")).value == "4321"
    assert ws.cell(r, _col("plan")).value == "ماهانه"
    assert ws.cell(r, _col("status")).value == "فعال"
    assert ws.cell(r, _col("paid_total")).value == 1_200_000
    assert ws.cell(r, _col("username")).value == "@ali"
    assert ws.cell(rows[12], _col("approved")).value == "بله"


def test_unchanged_file_has_no_actions():
    run(ac.activate_subscription(11, "yearly", 365))
    ac.set_office_info(11, "دفتر ۱", "100")
    res = sa.parse_xlsx(_export())
    assert res["ok"] and res["actions"] == []


def test_add_new_manual_subscription_and_office_name():
    path = _export()
    wb = load_workbook(path)
    ws = wb[sa.SHEET_TITLE]
    ws.append([555, "دفتر خدمات قضایی شمارهٔ ۵", "5555", "", "1405/12/29", None, "", "", "", "", "", "", "", "یادداشت"])
    wb.save(path)
    res = sa.parse_xlsx(path)
    assert res["ok"], res["errors"]
    a = res["actions"][0]
    assert a["user_id"] == 555 and a["plan"] == "manual" and a["notify"]
    notify = run(sa.apply_actions(res["actions"]))
    assert notify and notify[0][0] == 555
    sub = run(ac.get_subscription(555))
    assert sub["plan"] == "manual" and sa.ts_to_jalali_str(sub["expires_at"]) == "1405/12/29"
    info = ac.get_office_info(555)
    assert info == {"office_name": "دفتر خدمات قضایی شمارهٔ ۵", "office_code": "5555", "note": "یادداشت"}


def test_new_id_by_days_and_errors():
    path = _export()
    wb = load_workbook(path)
    ws = wb[sa.SHEET_TITLE]
    ws.append([601, "", "", "ماهانه", "", 30])
    wb.save(path)
    res = sa.parse_xlsx(path)
    assert res["ok"]
    run(sa.apply_actions(res["actions"]))
    assert 29 <= (run(ac.get_subscription(601))["expires_at"] - time.time()) / 86400 <= 31

    for bad, msg in [([602, "", "", "", "1405/07/31"], "تاریخ پایان نامعتبر"),
                     (["abc", "x"], "آیدی عددی نامعتبر"),
                     ([603, "", "", "سه‌ماهه", "1405/12/01"], "نوع اشتراک"),
                     ([604, "", "", "ماهانه"], "تاریخ پایان یا روزهای باقی‌مانده")]:
        p2 = _export()
        wb = load_workbook(p2)
        wb[sa.SHEET_TITLE].append(bad)
        wb.save(p2)
        res = sa.parse_xlsx(p2)
        assert not res["ok"] and msg in res["errors"][0], res["errors"]


def test_change_expiry_days_cancel_and_approve():
    for uid in (21, 22, 23):
        run(ac.activate_subscription(uid, "monthly", 30))
    path = _export()
    wb = load_workbook(path)
    ws, rows = wb[sa.SHEET_TITLE], None
    rows = {ws.cell(r, 1).value: r for r in range(2, ws.max_row + 1)}
    ws.cell(rows[21], _col("expires")).value = "1406/01/15"          # تغییر تاریخ
    ws.cell(rows[22], _col("days_left")).value = 0                    # لغو
    ws.cell(rows[23], _col("approved")).value = "بله"                 # رایگان دائمی
    ws.cell(rows[23], _col("office_name")).value = "دفتر ۲۳"
    wb.save(path)
    res = sa.parse_xlsx(path)
    assert res["ok"] and len(res["actions"]) == 3
    run(sa.apply_actions(res["actions"]))
    assert sa.ts_to_jalali_str(run(ac.get_subscription(21))["expires_at"]) == "1406/01/15"
    assert run(ac.get_subscription(22)) is None
    assert run(ac.is_approved(23)) and ac.get_office_info(23)["office_name"] == "دفتر ۲۳"


def test_days_column_drift_after_export_is_not_a_change():
    """فایلی که دیروز گرفته شده: «روزهای باقی‌مانده» امروز یکی کمتر است ولی کاربر آن را تغییر نداده."""
    run(ac.activate_subscription(31, "monthly", 30))
    path = os.path.join(tempfile.mkdtemp(), "old.xlsx")
    sa.export_xlsx(path, now=time.time() - 86400)
    res = sa.parse_xlsx(path)
    assert res["ok"] and res["actions"] == []


def test_refresh_file_after_purchase():
    from config import SUBS_EXCEL_PATH
    if os.path.exists(SUBS_EXCEL_PATH):
        os.remove(SUBS_EXCEL_PATH)
    run(ac.activate_subscription(77, "yearly", 365))
    _, rows = _rows(SUBS_EXCEL_PATH)
    assert 77 in rows
