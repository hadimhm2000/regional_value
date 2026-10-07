# -*- coding: utf-8 -*-
"""تست محاسبهٔ هزینهٔ دادرسی و تمبر (منطق منتقل‌شده از online.judicial.services.ble) — اجرا: pytest"""
import pytest

import court_fee as cf
import stamp_duty as sd


def test_court_fee_first_instance_tiers():
    r = cf.calc_court_fee(500_000_000, cf.CAT_FIRST)
    # ۲۰۰ میلیون × ۲٫۵٪ + ۳۰۰ میلیون × ۳٫۵٪
    assert (r["base_fee"], r["excess_fee"], r["fee"]) == (5_000_000, 10_500_000, 15_500_000)
    assert cf.calc_court_fee(100_000_000, cf.CAT_FIRST)["fee"] == 2_500_000


def test_court_fee_appeal_and_retrial():
    assert cf.calc_court_fee(1_000_000_000, cf.CAT_APPEAL)["fee"] == 45_000_000
    assert cf.calc_court_fee(1_000_000_000, cf.CAT_RETRIAL)["fee"] == 55_000_000
    with pytest.raises(ValueError):
        cf.calc_court_fee(0, cf.CAT_FIRST)
    assert "15,500,000" in cf.format_result_fa(cf.calc_court_fee(500_000_000, cf.CAT_FIRST))


def test_stamp_duty_tiers():
    r = sd.calculate_stamp_duty(1_000_000_000)
    # حق‌الوکاله: ۵۰۰م × ۸٪ + ۵۰۰م × ۷٪ = ۷۵ میلیون؛ تمبر = ۵٪
    assert r["haghalvekaleh_kolli"] == 75_000_000
    assert r["haghalvekaleh_bedvi"] == 45_000_000 and r["haghalvekaleh_tajdidnazar"] == 30_000_000
    assert r["tamber_kolli"] == 3_750_000
    with pytest.raises(ValueError):
        sd.calculate_stamp_duty(40_000_000_000)


def test_stamp_from_lawyer_fee_sample():
    r = sd.calculate_stamp_from_lawyer_fee(1_000_000_000)
    assert r["total"] == {"fee": 1_000_000_000, "stamp": 50_000_000, "share": 37_500_000, "total": 87_500_000}
    assert r["first"]["total"] == 52_500_000 and r["appeal"]["total"] == 35_000_000
    text = sd.format_lawyer_fee_result_fa(1_000_000_000, r)
    assert "۸۷٬۵۰۰٬۰۰۰ ریال" in text and "ماده ۲۹" in text
