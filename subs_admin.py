# -*- coding: utf-8 -*-
"""
subs_admin.py — مدیریت اشتراک‌ها با فایل اکسل (برای ادمین).

روال:
  ۱. /subs_excel → اکسل همهٔ مشترکان (خریداران، اشتراک‌های دستی، کاربران رایگان) با
     نام و کد دفتر خدمات قضایی، نوع اشتراک، تاریخ پایان (شمسی)، روزهای باقی‌مانده و ...
  ۲. ادمین در همان فایل:
       • ردیف جدید اضافه می‌کند (آیدی عددی + تاریخ پایان یا تعداد روز) ← اشتراک دستی؛
       • تاریخ پایان / روزهای باقی‌مانده / نوع اشتراک را تغییر می‌دهد؛
       • نام و کد دفتر و توضیحات هر آیدی را وارد/ویرایش می‌کند؛
       • «دسترسی رایگان دائمی» را بله/خیر می‌کند.
  ۳. فایل را برای ربات می‌فرستد → پیش‌نمایش تغییرات → «اعمال».
  ردیف حذف‌شده از فایل، اشتراکی را حذف نمی‌کند؛ برای لغو، تاریخ پایان را امروز یا
  گذشته بگذارید (یا روزهای باقی‌مانده = ۰).

هر خرید/تغییر اشتراک، فایل اکسل روی دیسک (config.SUBS_EXCEL_PATH) را هم به‌روز
می‌کند؛ پس هر خریدار جدید خودکار در اکسل می‌آید.

این ماژول وابستگی به aiogram ندارد (قابل تست مستقل).
"""
from __future__ import annotations

import datetime
import os
import time

import db

SHEET_TITLE = "اشتراک‌ها"

# (کلید، عنوان ستون، قابل ویرایش؟)
COLUMNS = [
    ("user_id", "آیدی عددی بله", True),
    ("office_name", "نام دفتر خدمات قضایی", True),
    ("office_code", "کد دفتر", True),
    ("plan", "نوع اشتراک", True),
    ("expires", "تاریخ پایان (شمسی)", True),
    ("days_left", "روزهای باقی‌مانده", True),
    ("approved", "دسترسی رایگان دائمی", True),
    ("status", "وضعیت", False),
    ("full_name", "نام در بله", False),
    ("username", "نام کاربری", False),
    ("national_id", "کدملی مدیر", False),
    ("last_payment", "آخرین پرداخت", False),
    ("paid_total", "مجموع پرداخت (تومان)", False),
    ("note", "توضیحات", True),
]
HEADERS = [t for _, t, _ in COLUMNS]
IDX = {k: i for i, (k, _, _) in enumerate(COLUMNS)}

PLAN_LABELS = {"monthly": "ماهانه", "yearly": "سالانه", "manual": "دستی"}
_LABEL_TO_PLAN = {"ماهانه": "monthly", "سالانه": "yearly", "سالیانه": "yearly", "دستی": "manual",
                  "monthly": "monthly", "yearly": "yearly", "manual": "manual"}
_YES = {"بله", "آری", "دارد", "yes", "y", "1", "true", "✓", "✅"}
_NO = {"خیر", "نه", "ندارد", "no", "n", "0", "false", "-"}


# ══════════════════════════════════════════════════════════════════
# تاریخ شمسی
# ══════════════════════════════════════════════════════════════════
def _tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Asia/Tehran")
    except Exception:
        return datetime.timezone(datetime.timedelta(hours=3, minutes=30))


def gregorian_to_jalali(gy, gm, gd):
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + (365 * gy) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100) + ((gy2 + 399) // 400) + gd + g_d_m[gm - 1]
    jy = -1595 + (33 * (days // 12053))
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm, jd = 1 + days // 31, 1 + days % 31
    else:
        jm, jd = 7 + (days - 186) // 30, 1 + (days - 186) % 30
    return jy, jm, jd


def jalali_to_gregorian(jy, jm, jd):
    jy += 1595
    days = -355668 + (365 * jy) + ((jy // 33) * 8) + (((jy % 33) + 3) // 4) + jd
    days += (jm - 1) * 31 if jm < 7 else ((jm - 7) * 30) + 186
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    leap = (gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0
    sal_a = [0, 31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 0
    while gm < 13 and gd > sal_a[gm]:
        gd -= sal_a[gm]
        gm += 1
    return gy, gm, gd


def ts_to_jalali_str(ts: float) -> str:
    d = datetime.datetime.fromtimestamp(ts, _tz())
    y, m, dd = gregorian_to_jalali(d.year, d.month, d.day)
    return f"{y:04d}/{m:02d}/{dd:02d}"


def _to_en(s) -> str:
    return str(s).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))


def parse_jalali(value):
    """«۱۴۰۵/۰۸/۱۵» / «1405-8-15» / تاریخ اکسل → (y, m, d) شمسی یا None (نامعتبر)."""
    if isinstance(value, (datetime.datetime, datetime.date)):
        # اکسل «1405/08/15» را تاریخ میلادی سال ۱۴۰۵ می‌فهمد؛ اجزای آن همان تاریخ شمسی است
        y, m, d = value.year, value.month, value.day
    else:
        t = _to_en(value).strip().replace("-", "/").replace(".", "/").replace("\\", "/")
        parts = [p for p in t.split("/") if p]
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            return None
        y, m, d = (int(p) for p in parts)
        if d > 1000 and y <= 31:   # «۱۵/۰۸/۱۴۰۵»
            y, d = d, y
    if not (1300 <= y <= 1500 and 1 <= m <= 12 and 1 <= d <= 31):
        return None
    if (m > 6 and d > 30) or (m == 12 and d > 30):
        return None
    return y, m, d


def jalali_end_of_day_ts(y, m, d) -> float:
    gy, gm, gd = jalali_to_gregorian(y, m, d)
    return datetime.datetime(gy, gm, gd, 23, 59, 59, tzinfo=_tz()).timestamp()


def end_of_day_after(days: int, now: float = None) -> float:
    """پایان روزِ «days روز بعد» به وقت تهران (days=0 → همین الان، یعنی لغو)."""
    now = now or time.time()
    if days <= 0:
        return now - 1
    d = datetime.datetime.fromtimestamp(now + days * 86400, _tz())
    return d.replace(hour=23, minute=59, second=59, microsecond=0).timestamp()


def _days_left(expires_at: float, now: float) -> int:
    return max(0, -int(-(expires_at - now) // 86400))


# ══════════════════════════════════════════════════════════════════
# جمع‌آوری ردیف‌ها
# ══════════════════════════════════════════════════════════════════
def collect_rows(now: float = None) -> list:
    """همهٔ کاربرانی که اشتراک (فعال/منقضی)، دسترسی رایگان یا مشخصات دفتر دارند."""
    now = now or time.time()
    ids = set()
    for sql in ("SELECT user_id FROM subscriptions", "SELECT user_id FROM approved_users",
                "SELECT user_id FROM subscriber_info"):
        ids |= {r["user_id"] for r in db.query(sql)}
    subs = {r["user_id"]: dict(r) for r in db.query("SELECT * FROM subscriptions")}
    approved = {r["user_id"] for r in db.query("SELECT user_id FROM approved_users")}
    info = {r["user_id"]: dict(r) for r in db.query("SELECT * FROM subscriber_info")}
    trials = {r["user_id"]: dict(r) for r in db.query("SELECT * FROM trial_requests")}
    pays = {r["user_id"]: dict(r) for r in db.query(
        "SELECT user_id, MAX(created_at) last, SUM(amount_rial) total FROM payments GROUP BY user_id")}

    rows = []
    for uid in ids:
        s, inf, tr, pay = subs.get(uid), info.get(uid, {}), trials.get(uid, {}), pays.get(uid)
        is_appr = uid in approved
        if is_appr:
            status = "رایگان دائمی"
        elif s and s["expires_at"] > now:
            status = "فعال"
        elif s:
            status = "منقضی"
        else:
            status = "بدون اشتراک"
        rows.append({
            "user_id": uid,
            "office_name": inf.get("office_name") or "",
            "office_code": inf.get("office_code") or tr.get("office_code") or "",
            "plan": PLAN_LABELS.get(s["plan"], s["plan"]) if s else "",
            "expires": ts_to_jalali_str(s["expires_at"]) if s else "",
            "days_left": _days_left(s["expires_at"], now) if s else "",
            "approved": "بله" if is_appr else "خیر",
            "status": status,
            "full_name": tr.get("full_name") or "",
            "username": ("@" + tr["username"]) if tr.get("username") else "",
            "national_id": tr.get("national_id") or "",
            "last_payment": ts_to_jalali_str(pay["last"]) if pay else "",
            "paid_total": (pay["total"] or 0) // 10 if pay else "",
            "note": inf.get("note") or "",
            "_expires_at": s["expires_at"] if s else None,
        })
    order = {"فعال": 0, "رایگان دائمی": 1, "منقضی": 2, "بدون اشتراک": 3}
    rows.sort(key=lambda r: (order[r["status"]], r["_expires_at"] or 0))
    return rows


# ══════════════════════════════════════════════════════════════════
# خروجی اکسل
# ══════════════════════════════════════════════════════════════════
def export_xlsx(path: str, now: float = None) -> int:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    now = now or time.time()
    rows = collect_rows(now)
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_TITLE
    ws.sheet_view.rightToLeft = True
    ws.append(HEADERS)
    edit_fill = PatternFill("solid", fgColor="14284B")
    info_fill = PatternFill("solid", fgColor="6B7280")
    for (key, _, editable), c in zip(COLUMNS, ws[1]):
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = edit_fill if editable else info_fill
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 36
    status_fill = {"فعال": "E3F2E1", "منقضی": "FBE3E1", "رایگان دائمی": "E6EBF3", "بدون اشتراک": "F3F4F6"}
    grey = PatternFill("solid", fgColor="F3F4F6")
    for r in rows:
        ws.append([r[k] for k, _, _ in COLUMNS])
        rr = ws.max_row
        ws.cell(rr, IDX["status"] + 1).fill = PatternFill("solid", fgColor=status_fill[r["status"]])
        for k, _, editable in COLUMNS:
            if not editable and k != "status":
                ws.cell(rr, IDX[k] + 1).fill = grey
    # ستون‌های متنی (تا اکسل «۱۴۰۵/۰۸/۱۵» یا کدها را عدد/تاریخ نکند)
    max_row = max(ws.max_row, 500)
    for key in ("office_code", "expires", "last_payment", "national_id"):
        col = IDX[key] + 1
        for rr in range(2, max_row + 1):
            ws.cell(rr, col).number_format = "@"
    for rr in range(2, max_row + 1):
        ws.cell(rr, IDX["paid_total"] + 1).number_format = "#,##0"
    widths = {"user_id": 14, "office_name": 30, "office_code": 11, "plan": 11, "expires": 15,
              "days_left": 12, "approved": 13, "status": 13, "full_name": 20, "username": 16,
              "national_id": 13, "last_payment": 14, "paid_total": 16, "note": 30}
    for k, w in widths.items():
        ws.column_dimensions[ws.cell(1, IDX[k] + 1).column_letter].width = w
    ws.freeze_panes = "B2"

    def col_range(key):
        letter = ws.cell(1, IDX[key] + 1).column_letter
        return f"{letter}2:{letter}{max_row}"
    dv_plan = DataValidation(type="list", formula1='"ماهانه,سالانه,دستی"', allow_blank=True)
    dv_yes = DataValidation(type="list", formula1='"بله,خیر"', allow_blank=True)
    ws.add_data_validation(dv_plan)
    ws.add_data_validation(dv_yes)
    dv_plan.add(col_range("plan"))
    dv_yes.add(col_range("approved"))

    info = wb.create_sheet("راهنما")
    info.sheet_view.rightToLeft = True
    for line in [
        "راهنمای اکسل اشتراک‌ها",
        f"تهیه‌شده در {ts_to_jalali_str(now or time.time())} — ستون‌های سرمه‌ای قابل ویرایش و ستون‌های خاکستری فقط برای اطلاع هستند.",
        "",
        "افزودن اشتراک دستی: یک ردیف جدید با «آیدی عددی بله» بسازید و «تاریخ پایان (شمسی)» (مثل 1405/08/15) "
        "یا «روزهای باقی‌مانده» (مثل 30) را وارد کنید. نوع اشتراک اگر خالی باشد «دستی» ثبت می‌شود.",
        "تمدید یا اصلاح: تاریخ پایان را عوض کنید، یا فقط عدد «روزهای باقی‌مانده» را تغییر دهید "
        "(از امروز حساب می‌شود). اگر هر دو را تغییر دهید، تاریخ پایان ملاک است.",
        "لغو اشتراک: تاریخ پایان را امروز یا گذشته بگذارید، یا روزهای باقی‌مانده را 0 کنید. "
        "حذف ردیف از فایل، اشتراکی را حذف نمی‌کند.",
        "نام و کد دفتر و توضیحات را برای هر آیدی آزادانه وارد/ویرایش کنید.",
        "«دسترسی رایگان دائمی»: بله = استفادهٔ رایگان بدون اشتراک؛ خیر = لغو دسترسی رایگان.",
        "پس از ویرایش، فایل را برای ربات بفرستید؛ تا «اعمال» را نزنید هیچ تغییری ثبت نمی‌شود.",
        "به کاربرانی که اشتراکشان جدید یا تمدید شود، پیام اطلاع‌رسانی ارسال می‌شود.",
    ]:
        info.append([line])
    info.column_dimensions["A"].width = 120
    # زمان تهیهٔ فایل — برای تشخیص این‌که «روزهای باقی‌مانده» واقعاً تغییر داده شده یا فقط
    # به‌خاطر گذشت زمان از تهیهٔ فایل با مقدار امروز فرق دارد
    meta = wb.create_sheet("_meta")
    meta.append(["exported_at", now])
    meta.sheet_state = "hidden"
    wb.save(path)
    return len(rows)


def refresh_file() -> str:
    """بازسازی فایل اکسل روی دیسک (config.SUBS_EXCEL_PATH)."""
    from config import SUBS_EXCEL_PATH
    tmp = SUBS_EXCEL_PATH + ".tmp.xlsx"
    export_xlsx(tmp)
    os.replace(tmp, SUBS_EXCEL_PATH)
    return SUBS_EXCEL_PATH


# ══════════════════════════════════════════════════════════════════
# خواندن اکسل ویرایش‌شده
# ══════════════════════════════════════════════════════════════════
def is_subs_workbook(path: str) -> bool:
    from openpyxl import load_workbook
    try:
        wb = load_workbook(path, read_only=True)
        return SHEET_TITLE in wb.sheetnames
    except Exception:
        return False


def _cell_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return _to_en(v).strip() if not isinstance(v, str) else v.strip()


def parse_xlsx(path: str, now: float = None) -> dict:
    """
    خروجی: {"ok", "errors", "actions", "summary", "changes": [متن هر تغییر]}
    actions: [{"user_id", "set_expiry": ts|None, "plan": str|None, "approve": True/False/None,
               "office_name", "office_code", "note" (None = بدون تغییر), "notify": bool}]
    """
    from openpyxl import load_workbook

    now = now or time.time()
    try:
        wb = load_workbook(path, data_only=True, read_only=True)
    except Exception as e:
        return {"ok": False, "errors": [f"فایل اکسل قابل خواندن نیست: {e}"], "actions": [], "changes": []}
    if SHEET_TITLE not in wb.sheetnames:
        return {"ok": False, "errors": [f"برگهٔ «{SHEET_TITLE}» در فایل نیست."], "actions": [], "changes": []}
    rows = list(wb[SHEET_TITLE].iter_rows(values_only=True))
    header = [str(h).strip() if h is not None else "" for h in (rows[0] if rows else [])]
    if header[:len(HEADERS)] != HEADERS:
        return {"ok": False, "errors": ["عنوان ستون‌ها با قالب ربات یکی نیست؛ فایل را از /subs_excel بگیرید و "
                                        "فقط مقادیر را ویرایش کنید."], "actions": [], "changes": []}
    current = {r["user_id"]: r for r in collect_rows(now)}
    exported_at = now
    if "_meta" in wb.sheetnames:
        for mrow in wb["_meta"].iter_rows(values_only=True):
            k, v = (list(mrow) + [None, None])[:2]
            if k == "exported_at" and isinstance(v, (int, float)):
                exported_at = float(v)

    errors, actions, changes, seen = [], [], [], set()
    for i, row in enumerate(rows[1:], start=2):
        row = list(row) + [None] * (len(HEADERS) - len(row))
        vals = {k: row[IDX[k]] for k, _, _ in COLUMNS}
        if all(_cell_str(vals[k]) == "" for k, _, ed in COLUMNS if ed):
            continue
        uid_s = _cell_str(vals["user_id"])
        if not uid_s.isdigit():
            errors.append(f"ردیف {i}: آیدی عددی نامعتبر است ({uid_s or 'خالی'}).")
            continue
        uid = int(uid_s)
        if uid in seen:
            errors.append(f"ردیف {i}: آیدی {uid} تکراری است.")
            continue
        seen.add(uid)
        cur = current.get(uid)
        act = {"user_id": uid, "set_expiry": None, "plan": None, "approve": None,
               "office_name": None, "office_code": None, "note": None, "notify": False}
        notes = []

        # ── مشخصات دفتر و توضیحات ──
        for key, title in (("office_name", "نام دفتر"), ("office_code", "کد دفتر"), ("note", "توضیحات")):
            new = _cell_str(vals[key])
            old = (cur or {}).get(key, "") or ""
            if new != old:
                act[key] = new
                notes.append(f"{title}: «{old or '—'}» ← «{new or '—'}»")

        # ── دسترسی رایگان ──
        appr = _cell_str(vals["approved"]).lower()
        if appr:
            if appr in _YES:
                want = True
            elif appr in _NO:
                want = False
            else:
                errors.append(f"ردیف {i}: «دسترسی رایگان دائمی» باید بله یا خیر باشد.")
                continue
            had = bool(cur and cur["approved"] == "بله")
            if want != had:
                act["approve"] = want
                notes.append("دسترسی رایگان دائمی: " + ("فعال" if want else "لغو"))

        # ── نوع اشتراک ──
        plan_raw = _cell_str(vals["plan"])
        plan = None
        if plan_raw:
            plan = _LABEL_TO_PLAN.get(plan_raw)
            if not plan:
                errors.append(f"ردیف {i}: نوع اشتراک باید ماهانه، سالانه یا دستی باشد.")
                continue

        # ── تاریخ پایان / روزهای باقی‌مانده ──
        exp_raw = vals["expires"]
        exp_s = _cell_str(exp_raw) if not isinstance(exp_raw, (datetime.date, datetime.datetime)) else "x"
        days_s = _cell_str(vals["days_left"])
        new_exp = None
        exp_changed = False
        if exp_s:
            ymd = parse_jalali(exp_raw)
            if not ymd:
                errors.append(f"ردیف {i}: تاریخ پایان نامعتبر است ({_cell_str(exp_raw)}) — قالب درست: 1405/08/15")
                continue
            exp_str = f"{ymd[0]:04d}/{ymd[1]:02d}/{ymd[2]:02d}"
            if not cur or exp_str != cur["expires"]:
                new_exp, exp_changed = jalali_end_of_day_ts(*ymd), True
        if not exp_changed and days_s != "":
            try:
                days = int(float(days_s))
            except ValueError:
                errors.append(f"ردیف {i}: روزهای باقی‌مانده باید عدد باشد.")
                continue
            if days < 0:
                errors.append(f"ردیف {i}: روزهای باقی‌مانده نمی‌تواند منفی باشد.")
                continue
            exported_days = (_days_left(cur["_expires_at"], exported_at)
                             if cur and cur["_expires_at"] is not None else None)
            if exported_days is None or days != exported_days:
                new_exp = end_of_day_after(days, now)
        has_sub = bool(cur and cur["_expires_at"] is not None)
        if new_exp is not None:
            act["set_expiry"] = new_exp
            act["plan"] = plan or (_LABEL_TO_PLAN.get(cur["plan"]) if has_sub else None) or "manual"
            old_exp = cur["_expires_at"] if has_sub else None
            if new_exp <= now:
                notes.append("اشتراک: لغو / پایان" + (f" (قبلاً تا {cur['expires']})" if has_sub else ""))
            else:
                notes.append(f"اشتراک ({PLAN_LABELS[act['plan']]}): "
                             + (f"{cur['expires']} ← " if has_sub else "جدید تا ")
                             + ts_to_jalali_str(new_exp))
                act["notify"] = old_exp is None or new_exp > max(old_exp, now)
        elif plan and has_sub and PLAN_LABELS.get(plan) != cur["plan"]:
            act["plan"] = plan
            notes.append(f"نوع اشتراک: {cur['plan'] or '—'} ← {PLAN_LABELS[plan]}")
        elif plan and not has_sub:
            errors.append(f"ردیف {i}: برای آیدی {uid} تاریخ پایان یا روزهای باقی‌مانده را وارد کنید.")
            continue

        if not cur and act["set_expiry"] is None and act["approve"] is None \
                and all(act[k] is None for k in ("office_name", "office_code", "note")):
            errors.append(f"ردیف {i}: برای آیدی جدید {uid} هیچ اطلاعاتی وارد نشده است.")
            continue
        if notes:
            actions.append(act)
            label = (cur or {}).get("office_name") or act.get("office_name") or ""
            changes.append(f"• {uid}{' — ' + label if label else ''}" + (" (جدید)" if not cur else "")
                           + ":\n   " + "\n   ".join(notes))

    if errors:
        return {"ok": False, "errors": errors, "actions": [], "changes": []}
    new_users = sum(1 for a in actions if a["user_id"] not in current)
    summary = f"✏️ {len(actions)} آیدی تغییر می‌کند" + (f" ({new_users} آیدی جدید)" if new_users else "")
    return {"ok": True, "errors": [], "actions": actions, "changes": changes, "summary": summary}


async def apply_actions(actions: list) -> list:
    """اعمال تغییرات؛ خروجی: [(user_id, expires_at)] کاربرانی که باید از تمدید/فعال‌سازی مطلع شوند."""
    import access_control
    notify = []
    for a in actions:
        uid = a["user_id"]
        if any(a[k] is not None for k in ("office_name", "office_code", "note")):
            access_control.set_office_info(uid, a["office_name"], a["office_code"], a["note"])
        if a["approve"] is True:
            await access_control.approve_user(uid)
        elif a["approve"] is False:
            await access_control.revoke_user(uid)
        if a["set_expiry"] is not None:
            await access_control.set_subscription_expiry(uid, a["plan"] or "manual", a["set_expiry"])
            if a["notify"]:
                notify.append((uid, a["set_expiry"], a["plan"] or "manual"))
        elif a["plan"]:
            db.execute("UPDATE subscriptions SET plan=?, updated_at=? WHERE user_id=?", (a["plan"], time.time(), uid))
    refresh_file()
    return notify
