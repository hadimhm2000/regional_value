# -*- coding: utf-8 -*-
"""
capture_calculator.py — ذخیرهٔ کامل یک صفحهٔ ماشین‌حساب وب (مثل دادحساب) برای بررسی.

این اسکریپت را روی کامپیوتر خودتان اجرا کنید (جایی که سایت باز می‌شود). خروجی یک
فایل zip است که برای Claude می‌فرستید تا ورودی‌ها، جدول‌ها و نحوهٔ محاسبه را بخواند.

چه چیزهایی ذخیره می‌شود (برای هر آدرس):
  • HTML نهایی صفحه (بعد از اجرای جاوااسکریپت) + اسکرین‌شات کامل
  • همهٔ فایل‌های جاوااسکریپت صفحه (فرمول‌ها و جدول‌ها معمولاً همین‌جا هستند)
  • همهٔ درخواست‌ها و پاسخ‌های شبکه (API/JSON) — اگر محاسبه سمت سرور باشد، همین‌جا دیده می‌شود
  • فهرست فرم‌ها: فیلدها، برچسب‌ها و گزینه‌های هر لیست کشویی
  • findings.txt: تکه‌هایی از کد که کلمات کلیدی دیه/ارث را دارند (با تبدیل \\uXXXX به فارسی)

هیچ اطلاعات شخصی، رمز یا کوکی‌ای ذخیره نمی‌شود (سرآیندهای Cookie/Authorization حذف می‌شوند).

────────────────────────── نصب (یک بار) ──────────────────────────
    pip install playwright
    python -m playwright install chromium
  اگر دستور دوم خطا داد (مثلاً دانلود مرورگر ممکن نبود)، مهم نیست: اسکریپت خودش
  Chrome یا Edge نصب‌شده روی ویندوز را پیدا و استفاده می‌کند.

────────────────────────── اجرا ──────────────────────────
    python capture_calculator.py

  یک پنجرهٔ مرورگر باز می‌شود. در هر صفحه:
    ۱. صبر کنید تا کامل بارگذاری شود؛
    ۲. اگر لازم است وارد حساب شوید؛
    ۳. یک یا دو نمونه محاسبه انجام دهید (مثلاً یک دیهٔ شکستگی + ماه حرام، یک ارث با همسر و فرزندان)
       و منوهای کشویی را یک بار باز کنید — هر چه بیشتر کلیک کنید، دادهٔ کامل‌تری ذخیره می‌شود؛
    ۴. به پنجرهٔ cmd برگردید و Enter بزنید.
  در پایان فایل dadhesab_capture_<تاریخ>.zip کنار همین اسکریپت ساخته می‌شود.

  آدرس‌های دیگر:   python capture_calculator.py https://example.com/page1 https://example.com/page2
  بدون پنجره و بدون انتظار (فقط بارگذاری خودکار):   python capture_calculator.py --auto
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import time
import zipfile
from urllib.parse import urlparse

DEFAULT_URLS = [
    "https://app.dadhesab.com/bloodmoney",
    "https://app.dadhesab.com/inheritance",
]

# کلمات کلیدی برای پیدا کردن جدول‌ها و فرمول‌ها در کد
KEYWORDS = [
    # دیه
    "دیه", "ارش", "حارصه", "دامیه", "متلاحمه", "سمحاق", "موضحه", "هاشمه", "منقله", "مأمومه", "مامومه",
    "دامغه", "جائفه", "شکستگی", "ماه حرام", "ماههای حرام", "تغلیظ", "انگشت", "استخوان", "دندان",
    "سقط جنین", "منافع", "بینایی", "شنوایی", "عقل", "نخاع", "شکاف", "کبودی", "سیاه شدن", "سرخ شدن",
    "bloodmoney", "diyeh", "diya", "dieh", "haram",
    # ارث
    "ارث", "وراث", "وارث", "ترکه", "سهم الارث", "سهم‌الارث", "طبقه", "فرض", "قرابت", "زوجه", "زوج",
    "همسر", "پسر", "دختر", "پدر", "مادر", "برادر", "خواهر", "جد", "جده", "عمو", "عمه", "دایی", "خاله",
    "نوه", "ابوینی", "ابی", "امی", "ثمن", "ربع", "نصف", "سدس", "ثلث", "ثلثان", "حجب", "رد",
    "inheritance", "heir", "estate", "spouse",
]

_SKIP_HEADERS = {"cookie", "set-cookie", "authorization", "proxy-authorization", "x-csrf-token", "x-xsrf-token"}
_TEXT_TYPES = ("javascript", "json", "html", "text", "css", "xml")
_MAX_BODY = 25 * 1024 * 1024


def _slug(url: str) -> str:
    p = urlparse(url)
    s = re.sub(r"[^A-Za-z0-9]+", "_", (p.netloc + p.path).strip("/"))[:80].strip("_")
    return s or "page"


def _safe_name(url: str, content_type: str) -> str:
    p = urlparse(url)
    base = os.path.basename(p.path) or "index"
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base)[:60]
    ext = os.path.splitext(base)[1]
    if not ext:
        ext = ".js" if "javascript" in content_type else ".json" if "json" in content_type else \
            ".html" if "html" in content_type else ".css" if "css" in content_type else ".bin"
        base += ext
    h = hashlib.sha1(url.encode()).hexdigest()[:8]
    return f"{h}_{base}"


def _decode_escapes(text: str) -> str:
    """\\u0627 و \\x.. → کاراکتر واقعی (فارسیِ داخل bundleها معمولاً این‌طور ذخیره می‌شود)."""
    def u(m):
        try:
            return chr(int(m.group(1), 16))
        except ValueError:
            return m.group(0)
    text = re.sub(r"\\u\{([0-9a-fA-F]{2,6})\}", u, text)
    return re.sub(r"\\u([0-9a-fA-F]{4})", u, text)


def _clean_headers(h: dict) -> dict:
    return {k: v for k, v in (h or {}).items() if k.lower() not in _SKIP_HEADERS}


# ══════════════════════════════════════════════════════════════════
# مرورگر
# ══════════════════════════════════════════════════════════════════
def _launch(p, headless: bool):
    """اول Chromium خود Playwright؛ اگر نصب نبود، Chrome و Edge نصب‌شده روی سیستم."""
    errors = []
    for kw in ({}, {"channel": "chrome"}, {"channel": "msedge"}):
        try:
            return p.chromium.launch(headless=headless, **kw)
        except Exception as e:
            errors.append(f"{kw or 'chromium'}: {str(e).splitlines()[0][:150]}")
    raise SystemExit("❌ هیچ مرورگری اجرا نشد. یکی از این‌ها را انجام دهید:\n"
                     "   python -m playwright install chromium\n"
                     "   یا Google Chrome / Microsoft Edge را نصب کنید.\n\n" + "\n".join(errors))


FORMS_JS = r"""
() => {
  const txt = el => (el && (el.innerText || el.textContent) || '').trim().replace(/\s+/g, ' ');
  const labelOf = el => {
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) return txt(l); }
    const p = el.closest('label'); if (p) return txt(p);
    return el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.name || '';
  };
  const fields = [...document.querySelectorAll('input, select, textarea, [role="combobox"], [role="radio"], [role="checkbox"], [role="switch"], [role="option"], [role="listbox"]')].map(el => ({
    tag: el.tagName.toLowerCase(), type: el.type || el.getAttribute('role') || '', name: el.name || '',
    id: el.id || '', label: labelOf(el), value: el.value ?? '', checked: !!el.checked,
    options: el.tagName === 'SELECT' ? [...el.options].map(o => ({value: o.value, text: txt(o)})) : undefined,
    text: ['option','radio','checkbox','switch'].includes(el.getAttribute('role')) ? txt(el) : undefined,
  }));
  const buttons = [...document.querySelectorAll('button, [role="button"], [role="tab"]')].map(txt).filter(Boolean);
  const tables = [...document.querySelectorAll('table')].map(t => [...t.rows].map(r => [...r.cells].map(txt)));
  const headings = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6,legend')].map(txt).filter(Boolean);
  return {title: document.title, url: location.href, headings, fields, buttons, tables, text: txt(document.body)};
}
"""


def capture(urls: list, out_dir: str, auto: bool, wait_seconds: int) -> list:
    from playwright.sync_api import sync_playwright

    summary = []
    with sync_playwright() as p:
        browser = _launch(p, headless=auto)
        context = browser.new_context(viewport={"width": 1366, "height": 900}, locale="fa-IR")
        for url in urls:
            page_dir = os.path.join(out_dir, _slug(url))
            files_dir = os.path.join(page_dir, "files")
            os.makedirs(files_dir, exist_ok=True)
            network, saved = [], {}

            def on_response(resp):
                try:
                    req = resp.request
                    ctype = (resp.headers or {}).get("content-type", "")
                    entry = {"t": round(time.time(), 3), "method": req.method, "url": resp.url,
                             "status": resp.status, "type": req.resource_type, "content_type": ctype,
                             "request_headers": _clean_headers(req.headers)}
                    if req.method != "GET" and req.post_data:
                        entry["request_body"] = req.post_data[:200_000]
                    if req.resource_type in ("script", "xhr", "fetch", "document", "stylesheet") \
                            or any(t in ctype for t in _TEXT_TYPES):
                        body = resp.body()
                        if len(body) <= _MAX_BODY:
                            name = saved.get(resp.url) or _safe_name(resp.url, ctype)
                            with open(os.path.join(files_dir, name), "wb") as f:
                                f.write(body)
                            saved[resp.url] = name
                            entry["saved_as"] = f"files/{name}"
                            if req.resource_type in ("xhr", "fetch") and len(body) < 200_000:
                                entry["response_preview"] = body.decode("utf-8", "replace")[:200_000]
                    network.append(entry)
                except Exception as e:  # پاسخ‌های redirect/بسته‌شده
                    network.append({"url": getattr(resp, "url", "?"), "error": str(e)[:200]})

            page = context.new_page()
            page.on("response", on_response)
            print(f"\n🌐 باز کردن {url}")
            try:
                page.goto(url, wait_until="networkidle", timeout=90_000)
            except Exception as e:
                print(f"   ⚠️ بارگذاری کامل نشد ({str(e).splitlines()[0][:120]}) — ادامه می‌دهیم")
            if auto:
                page.wait_for_timeout(wait_seconds * 1000)
            else:
                input("   👉 در مرورگر یکی دو نمونه محاسبه انجام دهید و منوها را باز کنید؛ "
                      "سپس اینجا Enter بزنید... ")
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                pass
            forms = {}
            try:
                forms = page.evaluate(FORMS_JS)
                with open(os.path.join(page_dir, "page.html"), "w", encoding="utf-8") as f:
                    f.write(page.content())
                page.screenshot(path=os.path.join(page_dir, "screenshot.png"), full_page=True)
            except Exception as e:
                print(f"   ⚠️ ذخیرهٔ HTML/اسکرین‌شات: {e}")
            with open(os.path.join(page_dir, "forms.json"), "w", encoding="utf-8") as f:
                json.dump(forms, f, ensure_ascii=False, indent=1)
            with open(os.path.join(page_dir, "network.json"), "w", encoding="utf-8") as f:
                json.dump(network, f, ensure_ascii=False, indent=1)
            _save_sourcemaps(context, page_dir, files_dir, saved)
            n_found = write_findings(page_dir, files_dir)
            print(f"   ✅ {len(saved)} فایل، {len(network)} درخواست شبکه، {n_found} یافتهٔ کلیدی")
            summary.append({"url": url, "files": len(saved), "requests": len(network), "findings": n_found,
                            "fields": len((forms or {}).get("fields", []))})
            page.close()
        browser.close()
    return summary


def _save_sourcemaps(context, page_dir, files_dir, saved):
    """اگر فایل‌های JS نقشهٔ منبع (.map) داشته باشند، کد اصلیِ خواناتر هم ذخیره می‌شود."""
    maps_dir = os.path.join(page_dir, "sourcemaps")
    for url, name in list(saved.items()):
        if not name.endswith(".js"):
            continue
        try:
            with open(os.path.join(files_dir, name), "rb") as f:
                tail = f.read()[-500:].decode("utf-8", "ignore")
            m = re.search(r"sourceMappingURL=([^\s'\"]+)", tail)
            if not m or m.group(1).startswith("data:"):
                continue
            from urllib.parse import urljoin
            r = context.request.get(urljoin(url, m.group(1)), timeout=30_000)
            if not r.ok:
                continue
            data = r.json()
            os.makedirs(maps_dir, exist_ok=True)
            for src, content in zip(data.get("sources", []), data.get("sourcesContent") or []):
                if not content or "node_modules" in src:
                    continue
                rel = re.sub(r"[^A-Za-z0-9._/-]+", "_", src.split("://")[-1]).lstrip("./")
                path = os.path.join(maps_dir, rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
        except Exception:
            continue


# ══════════════════════════════════════════════════════════════════
# جست‌وجوی کلمات کلیدی در کد
# ══════════════════════════════════════════════════════════════════
def write_findings(page_dir: str, files_dir: str, context_chars: int = 400) -> int:
    """تکه‌های کد اطراف کلمات کلیدی (با تبدیل \\uXXXX به فارسی) → findings.txt"""
    out, count = [], 0
    roots = [files_dir, os.path.join(page_dir, "sourcemaps")]
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _, names in os.walk(root):
            for name in sorted(names):
                path = os.path.join(dirpath, name)
                try:
                    with open(path, "rb") as f:
                        raw = f.read()
                    text = _decode_escapes(raw.decode("utf-8", "replace"))
                except Exception:
                    continue
                if "\\u" in raw.decode("utf-8", "ignore")[:2_000_000] and name.endswith(".js"):
                    with open(path + ".decoded.txt", "w", encoding="utf-8") as f:
                        f.write(text)
                hits = []
                for kw in KEYWORDS:
                    for m in re.finditer(re.escape(kw), text):
                        hits.append(m.start())
                if not hits:
                    continue
                hits.sort()
                merged = []
                for h in hits:
                    if merged and h - merged[-1][1] < context_chars:
                        merged[-1][1] = h
                    else:
                        merged.append([h, h])
                rel = os.path.relpath(path, page_dir)
                out.append(f"\n{'=' * 100}\n📄 {rel}  ({len(hits)} مورد)\n{'=' * 100}")
                for a, b in merged[:300]:
                    s, e = max(0, a - context_chars), min(len(text), b + context_chars)
                    out.append(f"\n--- [{s}:{e}] ---\n{text[s:e]}")
                    count += 1
    with open(os.path.join(page_dir, "findings.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(out) if out else "هیچ کلمهٔ کلیدی‌ای در کد پیدا نشد.")
    return count


# ══════════════════════════════════════════════════════════════════
def make_zip(out_dir: str) -> str:
    zip_path = out_dir.rstrip("/\\") + ".zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, _, names in os.walk(out_dir):
            for name in names:
                full = os.path.join(dirpath, name)
                z.write(full, os.path.relpath(full, os.path.dirname(out_dir)))
    return zip_path


def main(argv=None):
    ap = argparse.ArgumentParser(description="ذخیرهٔ کامل صفحه‌های ماشین‌حساب برای بررسی")
    ap.add_argument("urls", nargs="*", default=DEFAULT_URLS)
    ap.add_argument("--auto", action="store_true", help="بدون پنجره و بدون انتظار برای کاربر")
    ap.add_argument("--wait", type=int, default=8, help="ثانیه‌های انتظار در حالت --auto")
    ap.add_argument("--out", default=None, help="پوشهٔ خروجی")
    args = ap.parse_args(argv)

    try:
        import playwright  # noqa: F401
    except ImportError:
        raise SystemExit("❌ ابتدا نصب کنید:\n   pip install playwright\n   python -m playwright install chromium")
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = args.out or os.path.join(here, f"dadhesab_capture_{stamp}")
    os.makedirs(out_dir, exist_ok=True)
    summary = capture(args.urls, out_dir, auto=args.auto, wait_seconds=args.wait)
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump({"captured_at": stamp, "pages": summary}, f, ensure_ascii=False, indent=1)
    zip_path = make_zip(out_dir)
    shutil.rmtree(out_dir, ignore_errors=True)
    size = os.path.getsize(zip_path) / 1024 / 1024
    print(f"\n📦 فایل آماده شد: {zip_path}  ({size:.1f} مگابایت)")
    print("   این فایل را در گفت‌وگو با Claude بارگذاری کنید.")
    if not any(s["files"] for s in summary):
        print("⚠️ هیچ فایلی ذخیره نشد — احتمالاً صفحه باز نشد یا نیاز به ورود داشت.")


if __name__ == "__main__":
    main()
