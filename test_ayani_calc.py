# -*- coding: utf-8 -*-
"""تست‌های محاسبهٔ عرصه/اعیانی — اجرا: python test_ayani_calc.py  (یا pytest)"""

from decimal import Decimal

import ayani_calc as ac

TAX = {"مسکونی": 8_000_000, "تجاری": 20_000_000, "اداری": 8_000_000}


def test_rates_loaded_exactly_from_excel():
    tehran = ac.resolve_county("تهران", name_hints=["تهران"])
    assert tehran["rates"]["residential"]["concrete"] == 29_680_000
    assert tehran["rates"]["commercial"]["other"] == 32_640_000
    # مسکونی و اداری یکسان
    assert tehran["rates"]["residential"] == tehran["rates"]["administrative"]


def test_empty_cells_filled_from_neighbour():
    rudbar = ac.resolve_county("گیلان", name_hints=["رودبار"])
    assert rudbar["rates"]["industrial"]["other"] is not None
    azarshahr = ac.resolve_county("آذربایجان شرقی", name_hints=["آذرشهر"])
    assert all(v for u in azarshahr["rates"].values() for v in u.values())


def test_resolve_by_name_variants():
    assert ac.resolve_county("تهران", name_hints=["شهرستان شمیرانات"])["county"] == "شمیرانات"
    assert ac.resolve_county("خراسان شمالی", name_hints=["مه ولات", "بجنورد"])["county"] == "بجنورد"
    assert ac.resolve_county("خراسان رضوی", name_hints=["مه ولات"])["county"] == "مه‌ولات"


def test_resolve_never_fails():
    # نام ناشناخته + مختصات → نزدیک‌ترین شهرستان همان استان
    r = ac.resolve_county("اصفهان", 32.65, 51.67, ["جایی که در جدول نیست"])
    assert r["method"] == "nearest" and r["county"] == "اصفهان"
    # بدون هیچ اطلاعاتی → مرکز استان
    r = ac.resolve_county("آذربایجان شرقی")
    assert r["method"] == "default" and r["county"] == "تبریز"


def test_land_main_use():
    land = ac.compute_land_value(250, "تجاری", TAX)
    assert land["value"] == 250 * 20_000_000


def test_land_other_coefficients():
    # مثال کارفرما: ارزش ۲٬۰۰۰٬۰۰۰٬۰۰۰ × ۰٫۷ = ۱٬۴۰۰٬۰۰۰٬۰۰۰
    land = ac.compute_land_value(250, "سایر", TAX, other_index=0)
    assert land["base_total"] == 2_000_000_000 and land["value"] == 1_400_000_000
    expected = [0.7, 0.5, 0.4, 0.2, 0.1]
    for i, c in enumerate(expected):
        assert ac.compute_land_value(100, "سایر", TAX, i)["value"] == int(800_000_000 * c)


def test_building_incomplete_stage():
    rates = {"residential": {"concrete": 10_000_000, "other": 4_000_000}}
    b = ac.compute_building_value(rates, "residential", "concrete", 100, complete=False, stage_key="skeleton")
    assert b["full_value"] == 1_000_000_000 and b["value"] == 300_000_000
    # بندهای پارکینگ/طبقه/قدمت روی ساختمان ناتمام اعمال نمی‌شوند
    b2 = ac.compute_building_value(rates, "residential", "concrete", 100, complete=False,
                                   stage_key="finishing", parking_area=20, floor=9, age=10)
    assert b2["value"] == 800_000_000


def test_building_residential_high_floor_parking_age():
    rates = {"residential": {"concrete": 10_000_000, "other": 4_000_000}}
    b = ac.compute_building_value(rates, "residential", "concrete", 100, complete=True,
                                  parking_area=20, floor=8, age=5)
    # طبقه ۸ → ۳ طبقه بالاتر از پنجم → +۴٫۵٪
    assert b["floor_pct"] == Decimal("4.5") and b["adjusted_rate"] == 10_450_000
    assert b["main_value"] == 1_045_000_000
    assert b["parking_value"] == 20 * 5_000_000
    assert b["subtotal"] == 1_145_000_000
    assert b["age_pct"] == 10 and b["age_deduction"] == 114_500_000
    assert b["value"] == 1_030_500_000


def test_floor_rules():
    assert ac.floor_adjust_pct("residential", 5) == 0
    assert ac.floor_adjust_pct("administrative", 6) == Decimal("1.5")
    assert ac.floor_adjust_pct("commercial", 0) == 0
    assert ac.floor_adjust_pct("commercial", 2) == -20
    assert ac.floor_adjust_pct("commercial", -1) == -10
    assert ac.floor_adjust_pct("commercial", 5) == -30   # سقف ۳۰٪
    assert ac.floor_adjust_pct("industrial", 10) == 0


def test_age_cap_40_percent():
    rates = {"commercial": {"concrete": 10_000_000, "other": 5_000_000}}
    b = ac.compute_building_value(rates, "commercial", "other", 10, complete=True, floor=0, age=35)
    assert b["age_pct"] == 40 and b["value"] == 30_000_000


def test_total_and_steps():
    tehran = ac.resolve_county("تهران", name_hints=["تهران"])
    land = ac.compute_land_value(200, "سایر", TAX, other_index=1)
    b = ac.compute_building_value(tehran["rates"], "residential", "concrete", 120, True,
                                  parking_area=12, floor=7, age=3)
    res = ac.compute_all(land, b)
    assert res["total"] == land["value"] + b["value"]
    steps = ac.explain_steps(res)
    assert steps[-1][2] == res["total"]


if __name__ == "__main__":
    import sys
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"✅ {name}")
            except AssertionError as e:
                fails += 1
                print(f"❌ {name}: {e!r}")
    sys.exit(1 if fails else 0)
