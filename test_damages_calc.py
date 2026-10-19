"""تست محاسبهٔ خسارت تأخیر تأدیه و مهریه (منتقل‌شده از online.judicial.services.ble) — اجرا: pytest"""
import os

import pytest

import cpi_fetcher
import damages_calc as dc

SAMPLE_PDF = os.environ.get("CPI_SAMPLE_PDF", "")   # مثلاً «CPI 140506 site.pdf» بانک مرکزی


@pytest.fixture(autouse=True)
def _tmp_cpi(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "CPI_FILE", str(tmp_path / "cpi_index.json"))


# جدول ۱۴۰۰=۱۰۰ بانک مرکزی (PDF شهریور ۱۴۰۵) + یک ماه فرضی بعدی برای تست زنجیره
CBI_1400 = {"1405/02": 667.5, "1405/03": 716.6, "1405/04": 742.8,
            "1405/05": 770.3, "1405/06": 800.0, "1405/07": 830.0}


def test_seed_series_matches_published_values():
    series, chained, link = dc.judicial_series()
    assert series["1403/07"] == 1339.1          # مهر ۱۴۰۳ (جدول دادحساب)
    assert series["1405/06"] == 3452.0
    assert series["1395/07"] == 100.0 and min(series) == "1361/01"
    assert not chained and link is None


def test_chain_from_cbi_1400_table():
    added = dc.set_monthly_1400(CBI_1400, "test")
    assert added == ["1405/07"]
    series, chained, link = dc.judicial_series()
    assert link == "1405/06"
    assert series["1405/07"] == round(3452 * 830.0 / 800.0, 1)
    assert chained == {"1405/07"}
    # همان زنجیره‌ای که دادحساب برای ماه‌های برآوردی‌اش استفاده کرده:
    assert round(3323.9 * 800.0 / 770.3, 1) == 3452.1


def test_sample_calculation_dadhesab():
    """نمونهٔ دادحساب: ۵۰۰ میلیون، سررسید مهر ۱۴۰۳، پرداخت مهر ۱۴۰۵ ← شاخص شهریور ۱۴۰۵."""
    r = dc.calc_late_payment(500_000_000, (1403, 7, 1), (1405, 7, 11))
    assert r["due_key"] == "1403/07" and r["base_index"] == 1339.1
    assert r["pay_key"] == "1405/07" and r["target_key"] == "1405/06"
    assert r["target_index"] == 3452 and r["used_latest_available"]
    assert r["updated_amount"] == 1_288_925_398
    assert r["damages"] == 788_925_398


def test_payment_month_index_is_used_when_available():
    r = dc.calc_late_payment(1_000_000, (1402, 3, 10), (1404, 7, 15))
    assert r["target_key"] == "1404/07" and not r["used_latest_available"]
    assert r["updated_amount"] == round(1_000_000 * 1967.6 / 885.8)
    assert r["damages"] == r["updated_amount"] - 1_000_000


def test_manual_override_wins_over_seed():
    dc.set_monthly(1403, 7, 1340.0)
    assert dc.judicial_series()[0]["1403/07"] == 1340.0


def test_due_before_series_start():
    with pytest.raises(ValueError):
        dc.calc_late_payment(1_000_000, (1360, 12, 1), (1402, 5, 1))


def test_mahrieh_uses_year_before_payment_and_averages(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "ANNUAL_SEED_FILE", str(tmp_path / "none.json"))   # فقط میانگین ماهانه
    r = dc.calc_mahrieh(10_000_000, 1400, payment_year=1405)
    assert r["target_year"] == 1404
    assert r["base_index"] == 437.042
    assert r["target_index"] == pytest.approx(1961.683, abs=0.001)
    assert r["updated_amount"] == round(10_000_000 * r["target_index"] / r["base_index"])


def test_mahrieh_explicit_annual_index():
    dc.import_rows([(1365, None, 0.85)])
    r = dc.calc_mahrieh(1_000, 1365, payment_year=1405)
    assert r["base_index"] == 0.85
    with pytest.raises(dc.CpiMissing):
        dc.calc_mahrieh(1_000, 1314, payment_year=1405)


def test_annual_seed_from_dadhesab_cbi_table():
    # جدول سالانهٔ دادحساب (API cpis، منبع بانک مرکزی، پایهٔ ۱۳۹۵)
    assert dc.first_annual_year() == 1315
    assert dc.annual_index(1315) == 0.004 and dc.annual_index(1350) == 0.056
    assert dc.annual_index(1375) == 4.52 and dc.annual_index(1400) == 437.042
    assert dc.annual_index(1404) == 1961.6
    r = dc.calc_mahrieh(1_000_000, 1350, payment_year=1405)
    assert r["target_year"] == 1404 and r["updated_amount"] == 35_028_571_429


def test_missing_required_month():
    assert dc.missing_required_month((1405, 7, 3)) is None
    assert dc.missing_required_month((1405, 8, 3)) == (1405, 7)
    dc.set_monthly_1400(CBI_1400, "test")
    assert dc.missing_required_month((1405, 8, 3)) is None


def test_parse_inputs():
    assert dc.parse_jalali_date("۱۴۰۲/۰۸/۱۵") == (1402, 8, 15)
    assert dc.parse_jalali_date("1402/08/31") is None
    assert dc.parse_amount("۵۰۰,۰۰۰") == 500000


def test_parse_amount_thousand_separators():
    for text in ("258.000.000", "258,000,000", "۲۵۸٬۰۰۰٬۰۰۰", "۲۵۸.۰۰۰.۰۰۰", "۲۵۸ ۰۰۰ ۰۰۰",
                 "258000000", "258.000.000 ریال", "٢٥٨،٠٠٠،٠٠٠"):
        assert dc.parse_amount(text) == 258_000_000, text
    for text in ("25.5", "2.58.000", "12,5", "0", "", "abc", "-5", "1.0000"):
        assert dc.parse_amount(text) is None, text


def test_amount_confirm_line():
    line = dc.amount_confirm_line(258_000_000, "🔙 بازگشت")
    assert "258.000.000 ریال" in line and "25.800.000 تومان" in line and "بازگشت" in line
    assert "123٫4 تومان" in dc.amount_confirm_line(1234)


def test_fetcher_link_helpers():
    html = '<a href="/simplelist/2000.aspx">سال ۱۴۰۵</a><a href="/about.aspx">درباره</a>'
    links = cpi_fetcher._links(html, cpi_fetcher.CBI_LIST_URL)
    assert links[0] == ("https://cbi.ir/simplelist/2000.aspx", "سال 1405")
    assert cpi_fetcher._year_of(links[0][1]) == 1405 and cpi_fetcher._year_of("درباره") is None
    assert cpi_fetcher._month_of("https://cbi.ir/x/CPI_140506_site.pdf", "", 1405) == 6
    assert cpi_fetcher._month_of("https://cbi.ir/x/a.pdf", "گزارش مهر ماه", 1405) == 7


@pytest.mark.skipif(not os.path.exists(SAMPLE_PDF), reason="CPI_SAMPLE_PDF تنظیم نشده")
def test_parse_cbi_pdf_sample():
    with open(SAMPLE_PDF, "rb") as f:
        values = cpi_fetcher.parse_cpi_pdf(f.read())
    assert values["1400/01"] == 83.3 and values["1403/07"] == 310.1
    assert max(values) == "1405/06" and values["1405/06"] == 800.0


def test_result_pdfs_are_built(tmp_path):
    import damages_pdf
    r = dc.calc_late_payment(500_000_000, (1403, 7, 1), (1405, 7, 11))
    late = tmp_path / "late.pdf"
    assert damages_pdf.build_late_payment_pdf(str(late), r, (1403, 7, 1), (1405, 7, 11))
    m = dc.calc_mahrieh(10_000_000, 1385, payment_year=1405)
    mahr = tmp_path / "mahr.pdf"
    assert damages_pdf.build_mahrieh_pdf(str(mahr), m)
    assert late.read_bytes()[:4] == b"%PDF" and mahr.read_bytes()[:4] == b"%PDF"
    assert damages_pdf.month_label("1403/07") == "مهر 1403"


# ── جدول سالانهٔ مهریه (قبل از ۱۳۷۵) ──────────────────────────────────────

def test_official_1400_annual_matches_monthly_average(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "ANNUAL_SEED_FILE", str(tmp_path / "none.json"))
    # عدد رسمی بانک مرکزی برای ۱۴۰۰ (نامهٔ ۱۴۰۱/۳/۱۷) = ۴۳۷٫۰۳۲
    assert abs(dc.annual_index(1400) - 437.032) < 0.05


def test_mahrieh_before_1375_needs_annual_table(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "ANNUAL_SEED_FILE", str(tmp_path / "none.json"))
    with pytest.raises(dc.CpiMissing, match="1361"):
        dc.calc_mahrieh(1_000_000, 1360, payment_year=1405)


def test_import_annual_table_rescales_and_feeds_mahrieh(tmp_path, monkeypatch):
    import tools.import_annual_index as imp
    out = tmp_path / "cpi_annual_1395.json"
    monkeypatch.setattr(dc, "ANNUAL_SEED_FILE", str(out))
    monkeypatch.setattr(imp, "OUT_FILE", str(out))
    avgs = imp.monthly_averages()
    k = 100 / avgs[1390]                     # جدول ورودی با پایهٔ ۱۳۹۰=۱۰۰ و ارقام فارسی
    fa = str.maketrans("0123456789.", "۰۱۲۳۴۵۶۷۸۹/")
    lines = ["سال,شاخص", "1360," + f"{0.5 * k:.6f}".translate(fa)]
    lines += [f"{y},{(v * k):.6f}".translate(fa) for y, v in avgs.items()]
    src = tmp_path / "annual.csv"
    src.write_text("\n".join(lines), encoding="utf-8")
    assert imp.main([str(src), "--source", "test"]) == 0
    assert abs(dc.annual_index(1360) - 0.5) < 1e-4
    assert dc.first_annual_year() == 1360
    r = dc.calc_mahrieh(1_000_000, 1360, payment_year=1405)
    assert r["updated_amount"] == round(1_000_000 * dc.annual_index(1404) / dc.annual_index(1360))
    dc.import_rows([(1360, None, 0.6)])      # ثبت دستی مدیر بر جدول گیت اولویت دارد
    assert dc.annual_index(1360) == 0.6


def test_import_rejects_wrong_table(tmp_path, monkeypatch):
    import tools.import_annual_index as imp
    monkeypatch.setattr(imp, "OUT_FILE", str(tmp_path / "out.json"))
    avgs = imp.monthly_averages()
    rows = [f"{y},{v * (1.5 if y == 1390 else 1):.4f}" for y, v in avgs.items()]
    src = tmp_path / "bad.csv"
    src.write_text("\n".join(rows), encoding="utf-8")
    assert imp.main([str(src)]) == 2
    assert not (tmp_path / "out.json").exists()


def test_mahrieh_rule_year_before_payment_not_payment_year():
    # مادهٔ ۲ آیین‌نامهٔ تبصرهٔ مادهٔ ۱۰۸۲: شاخص «سال قبل» از سال تأدیه/فوت — نه خودِ آن سال (برخلاف دادحساب)
    r = dc.calc_mahrieh(1_000_000, 1390, payment_year=1402)
    assert r["target_year"] == 1401 and r["target_index"] == 640.225


def test_mahrieh_death_basis_article_3(tmp_path):
    # مادهٔ ۳ آیین‌نامه: مهریه از ترکه ← سال فوت مبناست؛ شاخص سال قبل از فوت
    r = dc.calc_mahrieh(5_000_000, 1370, payment_year=1402, basis="death")
    assert r["basis"] == "death" and r["payment_year"] == 1402 and r["target_year"] == 1401
    assert r["updated_amount"] == round(5_000_000 * 640.225 / 1.189)
    import damages_pdf
    out = tmp_path / "death.pdf"
    assert damages_pdf.build_mahrieh_pdf(str(out), r)
    assert out.read_bytes()[:4] == b"%PDF"


def test_mahrieh_payment_year_validation():
    this_year = dc.today_jalali()[0]
    with pytest.raises(ValueError, match="بعد از سال جاری"):
        dc.calc_mahrieh(1_000, 1390, payment_year=this_year + 1)
    with pytest.raises(ValueError, match="بعد از سال وقوع عقد"):
        dc.calc_mahrieh(1_000, 1400, payment_year=1400, basis="death")
    with pytest.raises(ValueError):
        dc.calc_mahrieh(1_000, 1390, payment_year=1400, basis="other")
    assert dc.calc_mahrieh(1_000, 1400)["payment_year"] == this_year


def test_late_payment_from_1361_with_approximate_flag(tmp_path):
    # ۱۳۶۱ تا ۱۳۷۴ از جدول دادحساب (بانک مرکزی)؛ تا ۱۳۶۹/۱۲ فقط یک رقم اعشار ← تقریبی
    r = dc.calc_late_payment(1_000_000, (1361, 1, 10), (1405, 6, 1))
    assert r["base_index"] == 0.3 and r["approximate"]
    assert r["updated_amount"] == round(1_000_000 * 3452.0 / 0.3)
    r2 = dc.calc_late_payment(1_000_000, (1370, 1, 10), (1405, 6, 1))
    assert r2["base_index"] == 1.086 and not r2["approximate"]
    assert not dc.calc_late_payment(1_000_000, (1400, 1, 1), (1405, 6, 1))["approximate"]
    import damages_pdf
    out = tmp_path / "approx.pdf"
    assert damages_pdf.build_late_payment_pdf(str(out), r, (1361, 1, 10), (1405, 6, 1))
