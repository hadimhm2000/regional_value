# -*- coding: utf-8 -*-
"""تست‌های محاسبهٔ سهم مالکانه — اجرا: python test_ownership_share.py  (یا pytest)"""

from fractions import Fraction as F

import ownership_share as o


def _parse(t):
    v, err = o.parse_amount(t)
    assert err is None, (t, err)
    return v


def test_parse_numbers():
    assert _parse("۳") == 3
    assert _parse("۱٫۵") == F(3, 2)
    assert _parse("1.5") == F(3, 2)
    assert _parse("۱۲٫۵ سهم") == F(25, 2)
    assert _parse("۳ دانگ مشاع") == 3
    assert _parse("۲۵٪") == 25
    assert _parse("۱٬۴۰۰") == 1400
    assert _parse("11,366,390") == 11366390
    assert _parse("۱۷۳٫۴۶") == F("173.46")          # دقیق، بدون خطای float


def test_parse_mixed_and_words():
    assert _parse("۶۷ و ۱/۳") == F(202, 3)
    assert _parse("67 1/3") == F(202, 3)
    assert _parse("۲ و نیم") == F(5, 2)
    assert _parse("نیم") == F(1, 2)
    assert _parse("یک‌سوم") == F(1, 3)
    assert _parse("ثمن") == F(1, 8)


def test_parse_rejects_ambiguous():
    assert o.parse_amount("۱/۵") == (None, o.ERR_SLASH)
    assert o.parse_amount("1,5") == (None, o.ERR_COMMA)
    assert o.parse_amount("")[1] == o.ERR_EMPTY
    assert o.parse_amount("سه")[1] == o.ERR_INVALID
    assert o.parse_amount("-2")[1] == o.ERR_INVALID
    assert o.parse_amount("67 4/3")[1] == o.ERR_INVALID   # بخش کسری ≥ ۱


def test_links_validation():
    assert o.link_fraction(o.make_link("all")) == 1
    assert o.link_fraction(o.make_link("dang", 3)) == F(1, 2)
    assert o.link_fraction(o.make_link("dang", F(3, 2))) == F(1, 4)
    assert o.link_fraction(o.make_link("percent", 25)) == F(1, 4)
    assert o.link_fraction(o.make_link("sahm", F(25, 2), 72)) == F(25, 144)
    for bad in [("dang", 7, None), ("dang", 0, None), ("percent", 101, None),
                ("sahm", 73, 72), ("sahm", 1, 0), ("sahm", 1, None)]:
        try:
            o.make_link(*bad)
        except ValueError:
            continue
        raise AssertionError(f"باید رد می‌شد: {bad}")


def test_chain_examples_from_deeds():
    # «نیم سهم مشاع از ۱۸۵ سهم سهام سه دانگ از شش دانگ»
    links = [o.make_link("sahm", F(1, 2), 185), o.make_link("dang", 3)]
    assert o.chain_fraction(links) == F(1, 2) / 185 * F(1, 2)
    # «دو سهم از ۴۸ سهم از ۶۷ و یک‌سوم شعیر از ۹۶ شعیر ششدانگ»
    links = [o.make_link("sahm", 2, 48), o.make_link("sahm", F(202, 3), 96)]
    assert o.chain_fraction(links) == F(2, 48) * F(202, 3) / 96
    # سند تک‌برگ: «۱۷۳٫۴۶ سهم از ۱۴۰۰ سهم عرصه و اعیان»
    assert o.chain_fraction([o.make_link("sahm", F("173.46"), 1400)]) == F(17346, 140000)


def test_apply_share_rounding():
    assert o.apply_share(1_000_000_000, F(1, 2)) == 500_000_000
    assert o.apply_share(1000, F(1, 3)) == 333
    assert o.apply_share(2000, F(1, 3)) == 667          # نیم به بالا
    assert o.apply_share(5, F(1, 2)) == 3
    assert o.apply_share(123_456_789, 1) == 123_456_789
    assert o.apply_share(0, F(1, 7)) == 0


def test_format():
    assert o.format_share(1) == "ششدانگ (۱۰۰٪)"
    assert o.format_share(F(1, 4)) == "۱٫۵ دانگ از ششدانگ (۲۵٪)"
    assert o.format_share(F(1, 3)) == "۲ دانگ از ششدانگ (≈ ۳۳٫۳۳۳۳٪)"
    assert o.describe_chain([o.make_link("all")]) == "ششدانگ"
    assert o.describe_chain([o.make_link("dang", 3)]) == "۳ دانگ از ششدانگ"
    assert o.describe_chain([o.make_link("sahm", F(1, 2), 185), o.make_link("dang", 3)]) == \
        "۰٫۵ سهم از ۱۸۵ سهم، از ۳ دانگ از ششدانگ"
    assert o.fmt_frac(F(202, 3)) == "۶۷ و ۱/۳"


def test_serialization_roundtrip():
    for f in [F(1), F(1, 3), F(17346, 140000), F(2776000, 11366390)]:
        assert o.frac_from_str(o.frac_to_str(f)) == f


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
