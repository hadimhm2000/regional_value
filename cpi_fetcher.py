# -*- coding: utf-8 -*-
"""
دریافت خودکار شاخص ماهانهٔ بهای کالاها و خدمات مصرفی از سایت بانک مرکزی.

صفحهٔ مبدأ: https://cbi.ir/simplelist/1611.aspx
    فهرست سال‌ها → صفحهٔ هر سال → فایل PDF گزارش هر ماه.
هر PDF ماهانه (مثلاً «CPI 140506») جدول «شاخص کل بهای کالاها و خدمات مصرفی در
مناطق شهری ایران (1400=100)» را از فروردین ۱۴۰۰ تا ماه گزارش دارد؛ پس آخرین
PDF به‌تنهایی همهٔ ماه‌های ۱۴۰۰ به بعد را می‌دهد.

این ماژول وابستگی به aiogram ندارد؛ توابع شبکه‌ای همگام‌اند و باید با
asyncio.to_thread صدا زده شوند (damages_handlers.py).
"""
import logging
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

CBI_LIST_URL = "https://cbi.ir/simplelist/1611.aspx"
TABLE_BASE_YEAR = 1400          # جدول PDF از فروردین همین سال شروع می‌شود
_TIMEOUT = 40
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
           "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]


class CpiFetchError(Exception):
    pass


# ── پارس PDF ─────────────────────────────────────────────────────────────

_NUM_RE = re.compile(r"^-?\d+/\d+$")       # اعداد PDF بانک مرکزی: «83/3» = 83.3


def _num(token: str) -> float:
    return float(token.translate(_FA_DIGITS).replace("/", "."))


def parse_cpi_pdf(pdf_bytes: bytes) -> dict[str, float]:
    """
    جدول «شاخص کل (1400=100)» را از PDF ماهانهٔ بانک مرکزی می‌خواند.
    خروجی: {"1400/01": 83.3, ..., "1405/06": 800.0}

    فونت عناوین فارسی PDF قابل استخراج نیست، پس ردیف‌ها به ترتیب از بالا به
    پایین خوانده و از فروردین ۱۴۰۰ شماره‌گذاری می‌شوند. صحت ترتیب با ستون
    «درصد تغییر نسبت به ماه قبل» کنترل می‌شود؛ اگر نخواند، خطا می‌دهد.
    """
    import fitz  # PyMuPDF
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    rows: list[tuple[float, float | None]] = []      # (شاخص، درصد تغییر ماهانه)
    try:
        for page in doc:
            if "1400=100" not in page.get_text().translate(_FA_DIGITS).replace(" ", ""):
                continue
            cells_y = []
            for x0, y0, x1, y1, text, *_ in page.get_text("words"):
                t = text.translate(_FA_DIGITS)
                if _NUM_RE.match(t) or t in ("*", "❊", "٭"):
                    cells_y.append(((y0 + y1) / 2, x0, t))
            lines: list[list] = []                   # خانه‌هایی که مرکز عمودی‌شان نزدیک است = یک ردیف
            for yc, x0, t in sorted(cells_y):
                if lines and yc - lines[-1][0][0] <= 5:
                    lines[-1].append((yc, x0, t))
                else:
                    lines.append([(yc, x0, t)])
            for line in lines:
                cells = [(x0, t) for _, x0, t in sorted(line, key=lambda c: -c[1])]   # راست به چپ
                if len(cells) < 5 or not _NUM_RE.match(cells[0][1]):
                    continue
                change = _num(cells[1][1]) if _NUM_RE.match(cells[1][1]) else None
                rows.append((_num(cells[0][1]), change))
    finally:
        doc.close()

    if len(rows) < 13:
        raise CpiFetchError(f"جدول شاخص (1400=100) در PDF پیدا نشد ({len(rows)} ردیف).")
    checked = ok = 0
    for (prev, _), (cur, change) in zip(rows, rows[1:]):
        if change is None:
            continue
        checked += 1
        if abs((cur / prev - 1) * 100 - change) <= 0.35:
            ok += 1
    if checked < 12 or ok < checked * 0.9:
        raise CpiFetchError(f"ترتیب ردیف‌های جدول PDF قابل تأیید نیست ({ok}/{checked}).")

    out = {}
    for i, (value, _) in enumerate(rows):
        y, m = TABLE_BASE_YEAR + i // 12, i % 12 + 1
        out[f"{y:04d}/{m:02d}"] = value
    return out


# ── پیمایش سایت ──────────────────────────────────────────────────────────

class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None


def _links(html: str, base_url: str) -> list[tuple[str, str]]:
    p = _LinkParser()
    p.feed(html)
    return [(urljoin(base_url, h.strip()), t.translate(_FA_DIGITS)) for h, t in p.links if h]


def _year_of(text: str) -> int | None:
    years = [int(y) for y in re.findall(r"(?<!\d)(1[34]\d\d)(?!\d)", text)]
    years = [y for y in years if 1395 <= y <= 1500]
    return max(years) if years else None


def _month_of(url: str, text: str, year: int) -> int:
    """ماهِ یک لینک PDF از متن لینک (نام ماه) یا نام فایل (مثل CPI_140506)."""
    for i, name in enumerate(_MONTHS, 1):
        if name in text:
            return i
    m = re.search(rf"{year}[-_ ]?(0[1-9]|1[0-2])(?!\d)", url.translate(_FA_DIGITS))
    if m:
        return int(m.group(1))
    m = re.search(rf"{str(year)[2:]}(0[1-9]|1[0-2])(?!\d)", url)
    return int(m.group(1)) if m else 0


def _get(session, url: str):
    r = session.get(url, timeout=_TIMEOUT, headers=_HEADERS)
    r.raise_for_status()
    return r


def _is_pdf(url: str) -> bool:
    return ".pdf" in url.lower().split("?")[0]


def fetch_latest() -> tuple[dict[str, float], str]:
    """
    جدیدترین PDF ماهانه را از سایت بانک مرکزی پیدا و پارس می‌کند.
    خروجی: (شاخص‌های ماهانه با پایهٔ ۱۴۰۰، آدرس PDF)
    از هر سال حداکثر ۴ PDF (جدیدترها) بررسی و کامل‌ترین جدول برگردانده می‌شود.
    """
    import requests
    s = requests.Session()
    list_html = _get(s, CBI_LIST_URL).text
    year_links = {}
    for url, text in _links(list_html, CBI_LIST_URL):
        y = _year_of(text)
        if y and y >= TABLE_BASE_YEAR:
            year_links.setdefault(y, url)
    if not year_links:
        raise CpiFetchError("لینک سال‌ها در صفحهٔ بانک مرکزی پیدا نشد.")

    errors = []
    best: tuple[dict, str] | None = None
    for year in sorted(year_links, reverse=True)[:2]:     # سال جدید ممکن است هنوز PDF نداشته باشد
        url = year_links[year]
        if _is_pdf(url):
            pdfs = [(url, 12)]
        else:
            page_html = _get(s, url).text
            pdfs = [(u, _month_of(u, t, year)) for u, t in _links(page_html, url) if _is_pdf(u)]
            pdfs = list(dict.fromkeys(pdfs))
        pdfs.sort(key=lambda p: -p[1])
        for pdf_url, _month in pdfs[:4]:
            try:
                data = parse_cpi_pdf(_get(s, pdf_url).content)
            except Exception as e:
                errors.append(f"{pdf_url}: {e}")
                logger.warning(f"[CPI] PDF قابل استفاده نبود: {pdf_url} — {e}")
                continue
            if best is None or len(data) > len(best[0]):
                best = (data, pdf_url)
        if best:
            return best
    raise CpiFetchError("هیچ PDF قابل‌استفاده‌ای پیدا نشد. " + " | ".join(errors[-3:]))
