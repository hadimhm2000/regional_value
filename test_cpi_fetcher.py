"""دریافت خودکار CPI از cbi.ir: لینک PDF مستقیم، بدون پسوند .pdf، داخل صفحهٔ گزارش/iframe،
و پیام خطای تشخیصی (بدون شبکهٔ واقعی)."""
import pytest

import cpi_fetcher as c

_REAL_PARSE = c.parse_cpi_pdf     # فیکسچر پایین آن را برای آزمون‌های پیمایش سایت جعلی می‌کند

B = "https://cbi.ir"
YEAR_URL = B + "/simplelist/1611/1405.aspx"


class _Resp:
    def __init__(self, body, ctype="text/html"):
        self.content = body if isinstance(body, bytes) else body.encode()
        self.text = self.content.decode("utf-8", "ignore")
        self.headers = {"Content-Type": ctype}

    def raise_for_status(self):
        pass


class _Session:
    def __init__(self, pages):
        self.pages = pages

    def get(self, url, **kw):
        if url not in self.pages:
            raise RuntimeError("404 " + url)
        return self.pages[url]


PDF = _Resp(b"%PDF-1.4 fake", "application/pdf")


def _index():
    return _Resp('<a href="/simplelist/1611/1405.aspx">گزارش‌های سال ۱۴۰۵</a>'
                 '<a href="/simplelist/1611/1404.aspx">سال ۱۴۰۴</a>')


@pytest.fixture(autouse=True)
def _fake_parse(monkeypatch):
    monkeypatch.setattr(c, "parse_cpi_pdf", lambda b: {"1400/01": 83.3, "1405/07": 836.8})


def _run(pages):
    return c._fetch_latest(_Session({c.CBI_LIST_URL: _index(), **pages}))


def test_direct_pdf_links():
    data, url = _run({
        YEAR_URL: _Resp('<a href="/Media/CPI140507.pdf">مهرماه ۱۴۰۵</a>'),
        B + "/Media/CPI140507.pdf": PDF})
    assert url.endswith("CPI140507.pdf") and data["1405/07"] == 836.8


def test_file_link_without_pdf_extension():
    _, url = _run({
        YEAR_URL: _Resp('<a href="/Media/GetFile.ashx?id=77">مهرماه ۱۴۰۵</a>'),
        B + "/Media/GetFile.ashx?id=77": PDF})
    assert url.endswith("id=77")


def test_report_page_with_embedded_iframe():
    _, url = _run({
        YEAR_URL: _Resp('<a href="/showitem/9911.aspx">شاخص بهای کالاها مهرماه ۱۴۰۵</a>'),
        B + "/showitem/9911.aspx": _Resp('<iframe src="/Media/GetFile.ashx?id=5"></iframe>'),
        B + "/Media/GetFile.ashx?id=5": PDF})
    assert url.endswith("id=5")


def test_html_instead_of_pdf_gives_clear_error():
    with pytest.raises(c.CpiFetchError) as e:
        _run({YEAR_URL: _Resp('<a href="/Media/CPI140507.pdf">مهر</a>'),
              B + "/Media/CPI140507.pdf": _Resp("<html>blocked</html>")})
    assert "پاسخ PDF نیست" in str(e.value)


def test_empty_year_page_error_includes_diagnosis():
    with pytest.raises(c.CpiFetchError) as e:
        _run({YEAR_URL: _Resp("<html></html>"),
              B + "/simplelist/1611/1404.aspx": _Resp("<html></html>")})
    assert "هیچ PDF قابل" in str(e.value) and "0 لینک" in str(e.value)


@pytest.mark.parametrize("text,month", [
    ("مهرماه ۱۴۰۵", 7), ("دی ماه", 10), ("خلاصه نتایج تیر", 4), ("گزارش جدید", 0)])
def test_month_name_is_whole_word(text, month):
    assert c._month_name_of(text) == month


# ── پارس PDF با موتور استخراج ساختگی (PyMuPDF لازم نیست) ─────────────────────

def _fake_fitz(monkeypatch, wrap=lambda t: t, order=(0, 1, 2, 3, 4), junk=False):
    """یک جدول ۶۰ ماهه (فروردین ۱۴۰۰ تا اسفند ۱۴۰۴) با ستون‌های راست‌به‌چپ: شاخص، درصد تغییر، ۳ ستون دیگر."""
    import sys
    import types

    values = [83.3]
    for i in range(1, 60):
        values.append(round(values[-1] * (1 + (1.5 + (i % 5) * 0.4) / 100), 1))
    words = []
    for i, v in enumerate(values):
        y = 100 + i * 12
        change = 0.0 if i == 0 else round((v / values[i - 1] - 1) * 100, 1)
        toks = [f"{v:.1f}".replace(".", "/"), f"{change:.1f}".replace(".", "/"), "1/0", "2/0", "3/0"]
        for tok, col in zip(toks, order):          # order[i] = شمارهٔ ستون (از راست) برای i-امین خانه
            x = 500 - col * 60
            words.append((x, y, x + 40, y + 8, wrap(tok), 0, 0, 0))
    if junk:                                       # جدول دیگری زیر جدول اصلی (مثلاً سایر شاخص‌ها)
        for i in range(22):
            y = 100 + (60 + i) * 12
            for col in range(6):
                words.append((500 - col * 60, y, 540 - col * 60, y + 8, wrap(f"{(i * 7 + col * 13) % 97 + 3}/{(i + col) % 10}"), 0, 0, 0))

    class Page:
        def get_text(self, kind="text"):
            return words if kind == "words" else wrap("(1400=100)")

    class Doc(list):
        def close(self):
            pass

    mod = types.SimpleNamespace(open=lambda stream, filetype: Doc([Page()]))
    monkeypatch.setitem(sys.modules, "fitz", mod)
    return values


def test_parse_plain(monkeypatch):
    values = _fake_fitz(monkeypatch)
    out = _REAL_PARSE(b"x")
    assert len(out) == 60 and out["1400/01"] == 83.3 and out["1404/12"] == values[-1]


def test_parse_with_bidi_marks_and_arabic_digits(monkeypatch):
    AR = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
    values = _fake_fitz(monkeypatch, wrap=lambda t: "‫" + t.translate(AR).replace("/", "٫") + "‬")
    assert _REAL_PARSE(b"x")["1404/12"] == values[-1]


def test_parse_reversed_number_parts(monkeypatch):
    def flip(t):
        a, _, b = t.partition("/")
        return f"{b}/{a}" if b else t
    values = _fake_fitz(monkeypatch, wrap=flip)
    assert _REAL_PARSE(b"x")["1404/12"] == values[-1]


def test_parse_no_table_error_has_diagnosis(monkeypatch):
    import sys
    import types

    class Page:
        def get_text(self, kind="text"):
            return [] if kind == "words" else "سلام"

    class Doc(list):
        def close(self):
            pass

    monkeypatch.setitem(sys.modules, "fitz", types.SimpleNamespace(open=lambda stream, filetype: Doc([Page()])))
    with pytest.raises(c.CpiFetchError) as e:
        _REAL_PARSE(b"x")
    assert "0 ردیف" in str(e.value) and "1 صفحه" in str(e.value)


def test_parse_columns_swapped(monkeypatch):
    values = _fake_fitz(monkeypatch, order=(1, 0, 2, 3, 4))      # ستون درصد تغییر سمت راستِ شاخص
    assert _REAL_PARSE(b"x")["1404/12"] == values[-1]


def test_parse_index_in_middle_column(monkeypatch):
    values = _fake_fitz(monkeypatch, order=(2, 4, 0, 1, 3))
    assert _REAL_PARSE(b"x")["1404/12"] == values[-1]


def test_parse_ignores_other_tables_on_page(monkeypatch):
    values = _fake_fitz(monkeypatch, junk=True)
    out = _REAL_PARSE(b"x")
    assert len(out) == 60 and out["1400/01"] == 83.3 and out["1404/12"] == values[-1]


def test_value_modes_and_signs():
    assert c._value("83/3", 0) == 83.3
    assert c._value("3/38", 1) == 83.3                 # حرف‌به‌حرف برعکس (خروجی PyMuPDF روی PDF بانک مرکزی)
    assert c._value("00/001", 1) == 100.0
    assert c._value("3/83", 2) == 83.3                 # جابه‌جایی دو بخش
    assert c._value("-0/5", 0) == -0.5 and c._value("5/0-", 1) == -0.5
    assert c._cell(["abc"], 0, 1) is None


def test_parse_char_reversed_numbers_like_real_server(monkeypatch):
    def rev(t):                                        # «83/3» → «3/38»؛ منفی آخر عدد می‌آید
        neg = t.startswith("-")
        r = t.lstrip("-")[::-1]
        return r + "-" if neg else r
    values = _fake_fitz(monkeypatch, wrap=rev, junk=True)
    out = _REAL_PARSE(b"x")
    assert len(out) == 60 and out["1400/01"] == 83.3 and out["1404/12"] == values[-1]
