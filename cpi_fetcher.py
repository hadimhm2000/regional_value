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

_NUM_RE = re.compile(r"^-?\d+/\d+-?$")      # اعداد PDF بانک مرکزی: «83/3» = 83.3 (علامت منفی ممکن است آخر بیاید)
# نویسه‌های نامرئی جهت‌دهی/قالب (RLE/LRE/PDF/RLM/LRM/ZWNJ/BOM…) که بسته به موتور استخراج داخل متن می‌آیند
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e"
                               "\u2066\u2067\u2068\u2069\ufeff\u00ad"), None)
_SEP_FIX = {ord("٫"): "/", ord("."): "/", ord(","): "/", ord("⁄"): "/", ord("∕"): "/", ord("−"): "-", ord("–"): "-"}


def _clean(token: str) -> str:
    """ارقام فارسی/عربی را لاتین و نویسه‌های نامرئی را حذف می‌کند؛ جداکنندهٔ اعشار را به «/» می‌برد."""
    t = token.translate(_FA_DIGITS).translate(_INVISIBLE).strip()
    return t.translate(_SEP_FIX)


# حالت‌های خواندن عدد (بسته به موتور استخراج):
#   0 = همان‌طور که هست («83/3»)
#   1 = ارقام کل عدد حرف‌به‌حرف برعکس («3/38» → 83.3)   ← خروجی PyMuPDF روی PDF بانک مرکزی
#   2 = فقط دو بخش قبل و بعد از «/» جابه‌جا («3/83» → 83.3)
_MODES = (0, 1, 2)


def _value(token: str, mode: int) -> float:
    """مقدار عددی یک خانه؛ علامت منفی چه اول چه آخر عدد بیاید پذیرفته می‌شود."""
    t = _clean(token)
    neg = t.startswith("-") or t.endswith("-")
    core = t.strip("-")
    if mode == 1:
        core = core[::-1]
    elif mode == 2:
        a, _, b = core.partition("/")
        core = f"{b}/{a}"
    v = float(core.replace("/", "."))
    return -v if neg else v


def _lines_from_words(words) -> list:
    """واژه‌های یک صفحه → ردیف‌های جدول: هر ردیف فهرست خانه‌های عددی (متن پاک‌شده) از راست به چپ."""
    cells_y = []
    for x0, y0, x1, y1, text, *_ in words:
        t = _clean(text)
        if _NUM_RE.match(t) or t in ("*", "❊", "٭"):
            cells_y.append(((y0 + y1) / 2, x0, t))
    lines: list[list] = []                       # خانه‌هایی که مرکز عمودی‌شان نزدیک است = یک ردیف
    for yc, x0, t in sorted(cells_y):
        if lines and yc - lines[-1][0][0] <= 5:
            lines[-1].append((yc, x0, t))
        else:
            lines.append([(yc, x0, t)])
    out = []
    for line in lines:
        cells = [t for _, x0, t in sorted(line, key=lambda c: -c[1])]
        if len(cells) >= 5:
            out.append(cells)
    return out


def _cell(cells, idx, mode):
    try:
        t = cells[idx]
    except IndexError:
        return None
    if not _NUM_RE.match(t):
        return None
    try:
        return _value(t, mode)
    except ValueError:                 # عدد نامعتبر نباید کل پارس را بشکند
        return None


def _longest_run(lines, k, j, mode):
    """بلندترین دنبالهٔ ردیف‌های پشت‌سرهم که ستون k (شاخص) با ستون j (درصد تغییر ماهانه) سازگارند.
    خروجی: (ردیف‌ها، میانگین خطا) — خطا برای تفکیک ترکیب‌های هم‌طول به کار می‌رود."""
    rows = [(_cell(c, k, mode), _cell(c, j, mode)) for c in lines]
    best, best_err, cur, cur_err = [], 0.0, [], 0.0
    for r in rows:
        if r[0] is None:
            cur, cur_err = [], 0.0
            continue
        err = None
        if cur and r[1] is not None and cur[-1][0]:
            e = abs((r[0] / cur[-1][0] - 1) * 100 - r[1])
            if e <= 0.35:
                err = e
        if err is not None:
            cur, cur_err = cur + [r], cur_err + err
        else:
            cur, cur_err = [r], 0.0
        if len(cur) > len(best):
            best, best_err = cur, cur_err
    if best and len({v for v, _ in best}) < 3:          # ستون ثابت = شاخص نیست
        return [], 0.0
    return best, (best_err / max(len(best) - 1, 1))


_COLS = [0, 1, 2, 3, 4, 5, -1, -2, -3, -4, -5, -6]     # شمارهٔ ستون از راست (۰..) یا از چپ (منفی)


def _find_table(lines) -> tuple[list, str]:
    """جای ستون «شاخص» و «درصد تغییر» را از روی خود داده پیدا می‌کند (به ترتیب ستون‌ها وابسته نیست)."""
    best, best_err, how = [], 0.0, ""
    for mode in _MODES:
        for k in _COLS:
            for j in _COLS:
                if k == j:
                    continue
                run, err = _longest_run(lines, k, j, mode)
                if len(run) > len(best) or (run and len(run) == len(best) and err < best_err - 1e-9):
                    best, best_err, how = run, err, f"ستون {k}/{j} حالت {mode}"
    return best, how


def parse_cpi_pdf(pdf_bytes: bytes) -> dict[str, float]:
    """
    جدول «شاخص کل (1400=100)» را از PDF ماهانهٔ بانک مرکزی می‌خواند.
    خروجی: {"1400/01": 83.3, ..., "1405/06": 800.0}

    فونت عناوین فارسی PDF قابل استخراج نیست، پس ردیف‌ها به ترتیب از بالا به
    پایین خوانده و از فروردین ۱۴۰۰ شماره‌گذاری می‌شوند. صحت ترتیب با ستون
    «درصد تغییر نسبت به ماه قبل» کنترل می‌شود؛ اگر نخواند، خطا می‌دهد.

    مقاوم در برابر تفاوت موتورهای استخراج: نویسه‌های نامرئی جهت‌دهی، ارقام عربی/فارسی،
    جداکنندهٔ اعشار «٫»، ترتیب معکوس اجزای عدد، و جای ستون‌ها (شاخص و درصد تغییر از روی خود
    داده پیدا می‌شوند). ردیف‌های جدول‌های دیگرِ PDF با «بلندترین دنبالهٔ سازگار» کنار گذاشته می‌شوند.
    """
    import fitz  # PyMuPDF
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page_lines, header_pages, n_words, sample = [], [], 0, []
    try:
        for i, page in enumerate(doc):
            words = page.get_text("words")
            plain = _clean(page.get_text()).replace(" ", "").replace("\n", "")
            if any(h in plain for h in ("1400=100", "100=1400", "001=0041", "0041=001")):
                header_pages.append(i)
            page_lines.append(_lines_from_words(words))
            n_words += len(words)
            if len(sample) < 3 and page_lines[-1]:
                sample.append(page_lines[-1][0])
    finally:
        doc.close()

    table, how = [], ""
    for pages in (header_pages, list(range(len(page_lines)))):    # اول صفحه‌های دارای سرعنوان، بعد همه
        lines = [c for i in pages for c in page_lines[i]]
        table, how = _find_table(lines)
        if len(table) >= 13:
            break

    if len(table) < 13:
        raise CpiFetchError(
            f"جدول شاخص (1400=100) در PDF پیدا نشد ({len(table)} ردیف سازگار). تشخیص: {len(page_lines)} صفحه، "
            f"{n_words} واژه، ردیف‌های عددی هر صفحه: {[len(l) for l in page_lines]}، "
            f"صفحه‌های دارای سرعنوان: {[i + 1 for i in header_pages]}، نمونه ردیف‌ها: {sample}")
    if table[0][0] >= 100:
        raise CpiFetchError(f"ردیف اول جدول فروردین ۱۴۰۰ نیست (مقدار {table[0][0]} — {how}).")

    logger.info(f"[CPI] جدول PDF خوانده شد: {len(table)} ماه ({how})")
    out = {}
    for i, (value, _) in enumerate(table):
        y, m = TABLE_BASE_YEAR + i // 12, i % 12 + 1
        out[f"{y:04d}/{m:02d}"] = value
    return out


# ── پیمایش سایت ──────────────────────────────────────────────────────────

class _LinkParser(HTMLParser):
    """لینک‌های <a> (با متن) و همچنین src/data عناصر iframe/embed/object را جمع می‌کند
    (گاهی PDF داخل صفحهٔ گزارش جاسازی شده است، نه لینک مستقیم)."""

    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a":
            self._href = a.get("href")
            self._text = []
        elif tag in ("iframe", "embed", "object", "source"):
            src = a.get("src") or a.get("data")
            if src:
                self.links.append((src, ""))

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
    out = []
    for h, t in p.links:
        h = (h or "").strip()
        if not h or h.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        out.append((urljoin(base_url, h), t.translate(_FA_DIGITS)))
    return out


def _year_of(text: str) -> int | None:
    years = [int(y) for y in re.findall(r"(?<!\d)(1[34]\d\d)(?!\d)", text)]
    years = [y for y in years if 1395 <= y <= 1500]
    return max(years) if years else None


_FA_LETTER = "؀-ۿﭐ-﷿ﹰ-﻿"


def _month_name_of(text: str) -> int:
    """شمارهٔ ماه از نام ماه در متن؛ فقط وقتی نام به‌صورت کلمهٔ مستقل بیاید
    (تا «دی» داخل «جدید» یا «تیر» داخل کلمه‌های دیگر ماه حساب نشود). «مهرماه» هم می‌پذیرد."""
    text = (text or "").replace("ي", "ی").replace("ك", "ک")
    for i, name in enumerate(_MONTHS, 1):
        if re.search(rf"(?<![{_FA_LETTER}]){name}(?:\s?ماه)?(?![{_FA_LETTER}])", text):
            return i
    return 0


def _month_of(url: str, text: str, year: int) -> int:
    """ماهِ یک لینک PDF از متن لینک (نام ماه) یا نام فایل (مثل CPI_140506)."""
    i = _month_name_of(text)
    if i:
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


# نشانه‌هایی که لینکِ بدون پسوند .pdf را هم «احتمالاً فایل» می‌کند (مثل Media/GetFile.ashx?id=…)
_FILE_HINTS = ("/media/", "download", "getfile", "getdoc", "/file", "attachment", "/upload", "/content/", ".ashx")
_REPORT_WORDS = ("CPI", "cpi", "شاخص", "تورم", "بهای کالا", "بهاي کالا")


def _host(url: str) -> str:
    from urllib.parse import urlparse
    h = urlparse(url).netloc.split(":")[0].lower()
    return h[4:] if h.startswith("www.") else h


_ASSET_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".css", ".js", ".woff", ".woff2", ".ttf",
              ".xls", ".xlsx", ".doc", ".docx", ".zip", ".rar", ".mp4", ".mp3")


def _maybe_file(url: str, text: str) -> bool:
    """لینکی که ممکن است فایل PDF باشد، هرچند آدرسش .pdf ندارد (صحتش بعد از دانلود با %PDF سنجیده می‌شود)."""
    u = url.lower().split("#")[0]
    if _is_pdf(u):
        return True
    if u.split("?")[0].endswith(_ASSET_EXT):
        return False
    return any(h in u for h in _FILE_HINTS)


def _maybe_item_page(base: str, url: str, text: str) -> bool:
    """صفحهٔ هر گزارش (که PDF داخل آن است): هم‌میزبان، غیر فایل، و متن/آدرس شبیه گزارش."""
    if _host(url) != _host(base):
        return False
    if _maybe_file(url, text):
        return False
    u = url.lower()
    if "simplelist" in u or "/about" in u or "/contact" in u:
        # فهرست‌های دیگر (نه خود گزارش)؛ فقط اگر متنش گزارش باشد
        return bool(_month_name_of(text)) or any(w in text for w in _REPORT_WORDS)
    return "showitem" in u or bool(_month_name_of(text)) or any(w in text for w in _REPORT_WORDS)


def _download_pdf(session, url: str) -> bytes:
    """فایل را می‌گیرد و مطمئن می‌شود واقعاً PDF است (نه صفحهٔ HTML خطا/لاگین/ضدربات)."""
    r = _get(session, url)
    body = r.content
    if b"%PDF" not in body[:1024]:
        ctype = (getattr(r, "headers", {}) or {}).get("Content-Type", "?")
        raise CpiFetchError(f"پاسخ PDF نیست (Content-Type={ctype}، {len(body)} بایت)")
    return body


def _pdf_candidates(session, page_url: str, year: int, diag: list, max_item_pages: int = 6):
    """PDFهای یک صفحهٔ سال. اگر در همان صفحه لینک PDF نبود، صفحه‌های گزارش‌ها را هم یک سطح می‌گردد."""
    html = _get(session, page_url).text
    links = _links(html, page_url)
    strict = [(u, _month_of(u, t, year)) for u, t in links if _is_pdf(u)]
    hinted = [(u, _month_of(u, t, year)) for u, t in links if _maybe_file(u, t) and not _is_pdf(u)]
    items = [(u, _month_of(u, t, year)) for u, t in links if _maybe_item_page(page_url, u, t)]
    diag.append(f"صفحهٔ {year}: {len(links)} لینک، {len(strict)} PDF، {len(hinted)} فایل‌مانند، "
                f"{len(items)} صفحهٔ گزارش‌مانند")
    cands = list(dict.fromkeys(strict))
    if not cands:
        # PDF مستقیم نیست ← صفحهٔ گزارش‌ها را یک سطح می‌گردیم؛ فایل‌مانندهای همین صفحه آخر می‌آیند
        items = list(dict.fromkeys(items))
        items.sort(key=lambda p: -p[1])
        for iu, im in items[:max_item_pages]:
            try:
                ihtml = _get(session, iu).text
            except Exception as e:
                diag.append(f"صفحهٔ گزارش {iu}: {type(e).__name__}")
                continue
            found = [(u, im or _month_of(u, t, year)) for u, t in _links(ihtml, iu) if _maybe_file(u, t)]
            diag.append(f"صفحهٔ گزارش {iu}: {len(found)} فایل‌مانند")
            found.sort(key=lambda p: not _is_pdf(p[0]))
            cands.extend(found)
        cands.extend(hinted)
        cands = list(dict.fromkeys(cands))
    if not cands:
        sample = [u for u, _ in links[:4]]
        diag.append("نمونه لینک‌ها: " + " ; ".join(sample))
    return cands


def _fetch_latest(s) -> tuple[dict[str, float], str]:
    diag: list[str] = []
    list_html = _get(s, CBI_LIST_URL).text
    year_links: dict[int, list[str]] = {}
    for url, text in _links(list_html, CBI_LIST_URL):
        y = _year_of(text)
        if y and y >= TABLE_BASE_YEAR:
            year_links.setdefault(y, [])
            if url not in year_links[y]:
                year_links[y].append(url)
    if not year_links:
        raise CpiFetchError("لینک سال‌ها در صفحهٔ بانک مرکزی پیدا نشد.")
    diag.append("سال‌ها: " + "، ".join(str(y) for y in sorted(year_links, reverse=True)))

    errors = []
    best: tuple[dict, str] | None = None
    for year in sorted(year_links, reverse=True)[:2]:     # سال جدید ممکن است هنوز PDF نداشته باشد
        pdfs: list[tuple[str, int]] = []
        for url in year_links[year][:3]:
            if _is_pdf(url):
                pdfs.append((url, 12))
                continue
            try:
                pdfs.extend(_pdf_candidates(s, url, year, diag))
            except Exception as e:
                diag.append(f"صفحهٔ {year} ({url}): {type(e).__name__}: {e}")
                logger.warning(f"[CPI] صفحهٔ سال {year} خوانده نشد: {url} — {e}")
        pdfs = list(dict.fromkeys(pdfs))
        pdfs.sort(key=lambda p: -p[1])
        # ماه ناشناخته (۰) ← ترتیب صفحه نامعلوم است؛ تعداد بیشتری امتحان می‌شود
        limit = 4 if (pdfs and pdfs[0][1]) else 8
        for pdf_url, _month in pdfs[:limit]:
            try:
                data = parse_cpi_pdf(_download_pdf(s, pdf_url))
            except Exception as e:
                errors.append(f"{pdf_url}: {e}")
                logger.warning(f"[CPI] PDF قابل استفاده نبود: {pdf_url} — {e}")
                continue
            if best is None or len(data) > len(best[0]):
                best = (data, pdf_url)
        if best:
            return best
    logger.error("[CPI] تشخیص: " + " | ".join(diag))
    raise CpiFetchError("هیچ PDF قابل‌استفاده‌ای پیدا نشد. "
                        + " | ".join(errors[-3:]) + ("" if errors else " [" + " | ".join(diag)[:400] + "]"))


def fetch_latest() -> tuple[dict[str, float], str]:
    """
    جدیدترین PDF ماهانه را از سایت بانک مرکزی پیدا و پارس می‌کند.
    خروجی: (شاخص‌های ماهانه با پایهٔ ۱۴۰۰، آدرس PDF)
    از هر سال حداکثر ۴ PDF (جدیدترها) بررسی و کامل‌ترین جدول برگردانده می‌شود.
    لینک PDF می‌تواند مستقیم در صفحهٔ سال یا (یک سطح پایین‌تر) در صفحهٔ هر گزارش باشد و لزومی ندارد
    پسوند .pdf داشته باشد؛ هر فایل دانلودشده باید با %PDF شروع شود. اگر چیزی پیدا نشد، پیام خطا
    خلاصهٔ تشخیص (تعداد لینک‌ها و نمونه آدرس‌ها) را هم می‌دهد.
    """
    import requests
    return _fetch_latest(requests.Session())
