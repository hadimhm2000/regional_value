# -*- coding: utf-8 -*-
"""جستجوی نزدیک‌ترین بلوک دارای ارزش در همان شهر (بدون شبکه؛ سامانه و نقشه شبیه‌سازی شده)."""
import math

import geocode_and_query as gq

LAT, LNG = 35.70, 51.40


def _val(n):
    return {"فیلدهای_ساختاریافته": {"ارزش معاملاتی مسکونی": f"{n:,}",
                                    "شماره بلوک بر اساس دفترچه ارزش معاملاتی ملک": "12",
                                    "شماره ردیف بر اساس دفترچه ارزش معاملاتی ملک": "3"}}


EMPTY = {"فیلدهای_ساختاریافته": {"ارزش معاملاتی مسکونی": None}}


def _dist(lat, lng):
    return math.hypot((lat - LAT) * 111000, (lng - LNG) * 111000 * math.cos(math.radians(LAT)))


def test_has_tax_value():
    assert gq.has_tax_value(_val(5))
    assert not gq.has_tax_value(EMPTY)
    assert not gq.has_tax_value({})


def test_ring_points_are_at_radius():
    for p in gq._ring_points(LAT, LNG, 250, 0):
        assert abs(_dist(*p) - 250) < 3


def test_finds_nearest_ring_in_same_city():
    probed = []

    def probe(lat, lng):
        probed.append(_dist(lat, lng))
        return _val(9) if _dist(lat, lng) > 300 else None

    found = gq.find_nearest_valued(30, LAT, LNG, probe=probe, city_of=lambda a, b: "تهران")
    assert found and found["distance_m"] == 350 and found["city"] == "تهران"
    assert max(probed) < 400          # حلقه‌های دورتر استعلام نشدند


def test_skips_other_city_and_continues():
    # حلقهٔ ۱۰۰ متری فقط شرقِ نقطه ارزش دارد ولی آن طرف مرز شهر است
    def probe(lat, lng):
        return _val(1) if _dist(lat, lng) > 90 else None

    def city_of(lat, lng):
        if lat == LAT and lng == LNG:
            return "تهران"
        return "شهر ری" if (lng > LNG and _dist(lat, lng) < 150) else "تهران"

    found = gq.find_nearest_valued(30, LAT, LNG, probe=probe, city_of=city_of)
    assert found["distance_m"] == 100 and found["lng"] <= LNG + 1e-9   # نقطهٔ هم‌شهر همان حلقه


def test_city_names_normalized():
    assert gq._norm_city("شهر تهران") == "تهران"
    assert gq._norm_city("كرج") == "کرج"
    assert gq._norm_city(None) == ""


def test_unknown_candidate_city_only_close():
    def probe(lat, lng):
        return _val(1) if _dist(lat, lng) > 1100 else None

    def city_of(lat, lng):
        return "تهران" if (lat, lng) == (LAT, LNG) else None   # نشان برای نامزدها پاسخ نمی‌دهد

    assert gq.find_nearest_valued(30, LAT, LNG, probe=probe, city_of=city_of) is None


def test_gives_up_at_deadline():
    calls = []
    found = gq.find_nearest_valued(30, LAT, LNG, deadline=0.0,
                                   probe=lambda a, b: calls.append(1), city_of=lambda a, b: "تهران")
    assert found is None and not calls


def test_fallback_keeps_original_when_valued(monkeypatch):
    monkeypatch.setattr(gq, "find_nearest_valued", lambda *a, **k: 1 / 0)
    out = gq._with_nearest_fallback(30, LAT, LNG, _val(7))
    assert out["nearest"] is None and gq.has_tax_value(out["tax_info"])


def test_fallback_uses_nearest(monkeypatch):
    monkeypatch.setattr(gq, "find_nearest_valued", lambda *a, **k: {
        "tax_info": _val(8), "lat": 1, "lng": 2, "distance_m": 175, "city": "تهران"})
    out = gq._with_nearest_fallback(30, LAT, LNG, EMPTY)
    assert out["nearest"]["distance_m"] == 175 and gq.has_tax_value(out["tax_info"])
