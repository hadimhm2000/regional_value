# -*- coding: utf-8 -*-
"""تست محاسبهٔ دیه و سهم‌الارث — اجرا: pytest"""
from fractions import Fraction as F

import pytest

import diyeh_calc as dc
import inheritance_calc as ic

FULL_1405 = 21_000_000_000


# ══════════════════════════════════════════════════════════════════
# دیه
# ══════════════════════════════════════════════════════════════════
def _find(title_part):
    for ci, cat in enumerate(dc.load_data()["categories"]):
        for ii, it in enumerate(cat["items"]):
            if title_part in it["title"]:
                return ci, ii
    raise KeyError(title_part)


def test_full_diyeh_table():
    assert dc.full_diyeh(1405) == FULL_1405
    assert dc.full_diyeh(1404) == 16_000_000_000 and dc.full_diyeh(1374) == 36_000_000
    assert dc.latest_year() >= 1405
    assert len(dc.categories()) == 25 and sum(len(dc.items(i)) for i in range(25)) == 234


def test_admin_override_new_year(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "RATES_FILE", str(tmp_path / "r.json"))
    assert dc.full_diyeh(1406) is None
    dc.set_full_diyeh(1406, 27_000_000_000)
    assert dc.full_diyeh(1406) == 27_000_000_000 and dc.latest_year() == 1406
    dc.set_full_diyeh(1405, 21_500_000_000)          # اصلاح سال موجود
    assert dc.full_diyeh(1405) == 21_500_000_000


@pytest.mark.parametrize("sex,holy,amount", [
    (dc.SEX_MALE, False, FULL_1405),
    (dc.SEX_FEMALE, False, FULL_1405 // 2),
    (dc.SEX_INTERSEX, False, FULL_1405 * 3 // 4),
    (dc.SEX_MALE, True, 28_000_000_000),
    (dc.SEX_FEMALE, True, 14_000_000_000),
])
def test_homicide(sex, holy, amount):
    r = dc.calculate(year=1405, method=dc.METHOD_HOMICIDE, sex=sex, holy_month=holy)
    assert r["amount"] == amount
    assert (dc.ART_555 in r["laws"]) == holy


def test_percent_and_fraction_chain():
    assert dc.calculate(year=1405, method=dc.METHOD_PERCENT, percent="۲٫۵")["amount"] == 525_000_000
    r = dc.calculate(year=1405, method=dc.METHOD_FRACTION, fractions=["1/2", "۱/۳"])
    assert r["fraction"] == F(1, 6) and r["amount"] == 3_500_000_000
    with pytest.raises(dc.DiyehError):
        dc.calculate(year=1405, method=dc.METHOD_PERCENT, percent="abc")
    with pytest.raises(dc.DiyehError):
        dc.calculate(year=1300, method=dc.METHOD_PERCENT, percent="1")


def test_organ_wounds_same_for_women():
    ci, ii = _find("حارصه")
    for sex in (dc.SEX_MALE, dc.SEX_FEMALE):
        r = dc.calculate(year=1405, method=dc.METHOD_ORGAN, category_index=ci, item_index=ii, sex=sex)
        assert r["amount"] == 210_000_000                    # ۱٪ دیهٔ کامل


def test_organ_article_560_threshold():
    ci, ii = _find("درآوردن یا از بین بردن یک چشم بینا")   # ۱/۲، مشمول ۵۶۰
    male = dc.calculate(year=1405, method=dc.METHOD_ORGAN, category_index=ci, item_index=ii, sex=dc.SEX_MALE)
    female = dc.calculate(year=1405, method=dc.METHOD_ORGAN, category_index=ci, item_index=ii, sex=dc.SEX_FEMALE)
    assert male["amount"] == FULL_1405 // 2 and female["amount"] == FULL_1405 // 4
    assert dc.needs_sex(dc.get_item(ci, ii))

    ci, ii = _find("دنده های محیط به قلب")                  # ۱/۴۰ برای هر دنده
    item = dc.get_item(ci, ii)
    assert not dc.needs_sex(item, 13) and dc.needs_sex(item, 14)   # ۱۳/۴۰ < ثلث ≤ ۱۴/۴۰
    f13 = dc.calculate(year=1405, method=dc.METHOD_ORGAN, category_index=ci, item_index=ii, count=13,
                       sex=dc.SEX_FEMALE)
    f14 = dc.calculate(year=1405, method=dc.METHOD_ORGAN, category_index=ci, item_index=ii, count=14,
                       sex=dc.SEX_FEMALE)
    assert f13["amount"] == FULL_1405 * 13 // 40 and f14["amount"] == FULL_1405 * 14 // 80
    with pytest.raises(dc.DiyehError):
        dc.calculate(year=1405, method=dc.METHOD_ORGAN, category_index=ci, item_index=ii, count=99)


def test_organ_textual_rule_has_no_amount():
    ci, ii = _find("شکستن استخوان هر عضو")
    r = dc.calculate(year=1405, method=dc.METHOD_ORGAN, category_index=ci, item_index=ii)
    assert r["amount"] is None and "یک پنجم" in r["rule_text"]
    assert "پزشکی قانونی" in dc.format_result_fa(r)


def test_teeth_count():
    ci, ii = _find("دندان های جلو")
    r = dc.calculate(year=1405, method=dc.METHOD_ORGAN, category_index=ci, item_index=ii, count=2,
                     sex=dc.SEX_FEMALE)
    assert r["amount"] == FULL_1405 // 10                     # ۲ × ۱/۲۰، کمتر از ثلث


# ══════════════════════════════════════════════════════════════════
# ارث — نمونه‌های شناخته‌شده (همه با منطق دادحساب هم یکسان‌اند)
# ══════════════════════════════════════════════════════════════════
def _shares(h):
    return {r["key"]: r["per_person"] for r in ic.calculate(h)["rows"]}


def H(marital=ic.SINGLE, wives=1, branches=(), **counts):
    return ic.Heirs(marital=marital, wives=wives, counts=counts, branches=list(branches))


@pytest.mark.parametrize("h,expected", [
    # ۹۰۷: فقط فرزندان، پسر دو برابر دختر
    (H(pesar=2, dokhtar=1), {"pesar": F(2, 5), "dokhtar": F(1, 5)}),
    # ۹۱۳ + ۹۰۷: زوجه ثمن، بقیه به فرزندان
    (H(ic.HAS_WIFE, pesar=1, dokhtar=1), {"zojeh": F(1, 8), "pesar": F(7, 12), "dokhtar": F(7, 24)}),
    # دو همسر در ثمن شریک‌اند
    (H(ic.HAS_WIFE, wives=2, pesar=1), {"zojeh": F(1, 16), "pesar": F(7, 8)}),
    # ۹۰۶: ابوین بدون اولاد — مادر ثلث، پدر بقیه
    (H(pedar=1, madar=1), {"madar": F(1, 3), "pedar": F(2, 3)}),
    # ۹۰۶ + حجب: دو برادر ← مادر سدس
    (H(pedar=1, madar=1, baradarAbaveyni=2), {"madar": F(1, 6), "pedar": F(5, 6)}),
    # شوهر + ابوین: شوهر نصف، مادر ثلث کل، پدر سدس
    (H(ic.HAS_HUSBAND, pedar=1, madar=1), {"zoj": F(1, 2), "madar": F(1, 3), "pedar": F(1, 6)}),
    # ۹۰۸: ابوین + یک دختر، رد مازاد به نسبت ۱:۱:۳
    (H(pedar=1, madar=1, dokhtar=1), {"pedar": F(1, 5), "madar": F(1, 5), "dokhtar": F(3, 5)}),
    # ۹۰۸ + حجب مادر: مادر از رد محروم
    # (مازاد ۱/۶ فقط بین پدر و دختر به نسبت ۱:۳)
    (H(pedar=1, madar=1, dokhtar=1, baradarAbaveyni=2), {"pedar": F(5, 24), "madar": F(1, 6), "dokhtar": F(5, 8)}),
    # ۹۱۴: شوهر + ابوین + یک دختر ← نقص بر دختر
    (H(ic.HAS_HUSBAND, pedar=1, madar=1, dokhtar=1), {"zoj": F(1, 4), "pedar": F(1, 6), "madar": F(1, 6),
                                                       "dokhtar": F(5, 12)}),
    # ۹۰۹: پدر + دو دختر، رد به نسبت ۱:۴
    (H(pedar=1, dokhtar=2), {"pedar": F(1, 5), "dokhtar": F(2, 5)}),
    # ابوین با پسر: هر کدام سدس
    (H(pedar=1, madar=1, pesar=1, dokhtar=1), {"pedar": F(1, 6), "madar": F(1, 6), "pesar": F(4, 9),
                                               "dokhtar": F(2, 9)}),
    # ۹۲۲: برادر تنی + یک برادر/خواهر امی ← امی سدس
    (H(baradarAbaveyni=1, siblingOmmi=1), {"siblingOmmi": F(1, 6), "baradarAbaveyni": F(5, 6)}),
    # ۹۱۸: اخوهٔ ابوینی، اخوهٔ ابی را محروم می‌کند
    (H(khaharAbaveyni=1, baradarAbi=2), {"khaharAbaveyni": F(1)}),
    # ۹۲۳: جد پدری و جد مادری
    (H(jaddAbi=1, jaddehAbi=1, jaddOmmi=1), {"jaddOmmi": F(1, 3), "jaddAbi": F(4, 9), "jaddehAbi": F(2, 9)}),
    # ۹۳۵: عمو و دایی
    (H(amooAbaveyni=1, daieAbaveyni=1), {"daieAbaveyni": F(1, 3), "amooAbaveyni": F(2, 3)}),
    # ۹۴۹: فقط زوجه ← بقیه به حاکم؛ فقط شوهر ← همهٔ ترکه
    (H(ic.HAS_WIFE), {"zojeh": F(1, 4), "hakem": F(3, 4)}),
    (H(ic.HAS_HUSBAND), {"zoj": F(1)}),
    # ۸۶۶: بدون وارث
    (H(), {"hakem": F(1)}),
])
def test_known_inheritance_cases(h, expected):
    assert _shares(h) == expected


def test_grandchildren_represent_parents():
    # پسر فوت‌شده با ۱ نوهٔ پسر و ۱ نوهٔ دختر + دختر فوت‌شده با ۱ نوهٔ پسر؛ بدون ابوین
    h = H(branches=[ic.Branch(True, 1, 1), ic.Branch(False, 1, 0)])
    rows = {r["label"]: r["per_person"] for r in ic.calculate(h)["rows"]}
    # شاخهٔ پسر ۲/۳ (بین نوه‌ها ۲:۱)، شاخهٔ دختر ۱/۳
    assert rows["نوهٔ پسری از پسر فوت‌شده (شاخهٔ 1)"] == F(4, 9)
    assert rows["نوهٔ دختری از پسر فوت‌شده (شاخهٔ 1)"] == F(2, 9)
    assert rows["نوهٔ پسری از دختر فوت‌شده (شاخهٔ 2)"] == F(1, 3)


def test_grandchildren_with_shortage_sum_to_one():
    h = H(ic.HAS_HUSBAND, pedar=1, madar=1, branches=[ic.Branch(False, 1, 2)])
    r = ic.calculate(h)
    assert sum(x["group"] for x in r["rows"]) == 1
    nav = {x["label"]: x["per_person"] for x in r["rows"] if x["key"] == "naveh"}
    assert sum(nav[k] * (1 if "پسری" in k else 2) for k in nav) == F(5, 12)


def test_estate_amounts_and_format():
    r = ic.calculate(H(ic.HAS_WIFE, pesar=1, dokhtar=1), estate=24_000_000_000)
    text = ic.format_result_fa(r)
    assert "3,000,000,000 ریال" in text and "14,000,000,000 ریال" in text and "7,000,000,000 ریال" in text
    assert "مخرج مشترک 24" in text


def test_validation():
    with pytest.raises(ic.InheritanceError):
        ic.calculate(H(pedar=2))
    with pytest.raises(ic.InheritanceError):
        ic.calculate(H(ic.HAS_WIFE, wives=5))
