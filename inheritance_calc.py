# -*- coding: utf-8 -*-
"""
inheritance_calc.py — تقسیم ترکه (سهم‌الارث) طبق قانون مدنی ایران. منطق خالص، بدون aiogram.

ورودی (Heirs): وضعیت تأهل متوفی، طبقهٔ اول (پدر، مادر، پسران، دختران، نوه‌ها به قائم‌مقامی)،
طبقهٔ دوم (اجداد چهارگانه، برادر و خواهر ابوینی/ابی، کلالهٔ امی)، طبقهٔ سوم (عمو، عمه، دایی، خاله —
هر کدام ابوینی/ابی/امی). هر طبقه فقط وقتی ارث می‌برد که طبقهٔ قبل وارثی نداشته باشد (مادهٔ ۸۶۲).

همهٔ کسرها از «کل ترکه» است (نه از باقی‌مانده)؛ سهم همسر ابتدا جدا می‌شود (۹۱۳) و صاحبان فرض بعدی
فرض خود را از کل ترکه می‌برند و باقی‌مانده به قرابت می‌رسد. مواد اصلی:
  ۸۶۶ نبود وارث ← حاکم   ۸۹۲ حجب مادر   ۹۰۶–۹۰۹ ابوین و اولاد   ۹۱۱–۹۱۲ نوه‌ها   ۹۱۳ همسر
  ۹۱۴ نقص بر دختر/دختران و رد مازاد   ۹۱۷–۹۲۴ طبقهٔ دوم   ۹۲۹–۹۳۵ طبقهٔ سوم   ۹۴۹ همسر تنها
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction as F

SINGLE, HAS_HUSBAND, HAS_WIFE = "single", "husband", "wife"
MARITAL_TITLES = {SINGLE: "بدون همسر", HAS_HUSBAND: "دارای شوهر (متوفی زن است)",
                  HAS_WIFE: "دارای همسر (متوفی مرد است)"}

LABELS = {
    "zoj": "شوهر", "zojeh": "همسر (زوجه)", "pedar": "پدر", "madar": "مادر", "pesar": "پسر", "dokhtar": "دختر",
    "jaddAbi": "جد پدری", "jaddehAbi": "جده پدری", "jaddOmmi": "جد مادری", "jaddehOmmi": "جده مادری",
    "baradarAbaveyni": "برادر ابوینی (تنی)", "khaharAbaveyni": "خواهر ابوینی (تنی)",
    "baradarAbi": "برادر ابی (پدری)", "khaharAbi": "خواهر ابی (پدری)", "siblingOmmi": "برادر/خواهر امی (مادری)",
    "amooAbaveyni": "عموی ابوینی", "ammehAbaveyni": "عمهٔ ابوینی", "daieAbaveyni": "دایی ابوینی",
    "khalehAbaveyni": "خالهٔ ابوینی", "amooAbi": "عموی ابی", "ammehAbi": "عمهٔ ابی", "daieAbi": "دایی ابی",
    "khalehAbi": "خالهٔ ابی", "amooOmmi": "عموی امی", "ammehOmmi": "عمهٔ امی", "daieOmmi": "دایی امی",
    "khalehOmmi": "خالهٔ امی", "hakem": "حاکم (بیت‌المال)",
}
CLASS2_KEYS = ["jaddAbi", "jaddehAbi", "jaddOmmi", "jaddehOmmi", "baradarAbaveyni", "khaharAbaveyni",
               "baradarAbi", "khaharAbi", "siblingOmmi"]
CLASS3_KEYS = ["amooAbaveyni", "ammehAbaveyni", "daieAbaveyni", "khalehAbaveyni", "amooAbi", "ammehAbi",
               "daieAbi", "khalehAbi", "amooOmmi", "ammehOmmi", "daieOmmi", "khalehOmmi"]
GRANDPARENTS = {"jaddAbi", "jaddehAbi", "jaddOmmi", "jaddehOmmi"}   # هر کدام حداکثر یک نفر

A = {  # مواد قانون مدنی (خلاصهٔ متن رسمی)
    866: "ماده ۸۶۶ ـ در صورت نبودن وارث، امر ترکهٔ متوفی راجع به حاکم است.",
    892: ("ماده ۸۹۲ (بند ب) ـ وقتی برای میت چند برادر و خواهر باشد، مادر از بردن بیش از یک سدس محروم می‌شود "
          "مشروط بر اینکه لااقل دو برادر یا یک برادر با دو خواهر یا چهار خواهر باشند، پدرشان زنده باشد و "
          "ابوینی یا ابی تنها باشند."),
    906: ("ماده ۹۰۶ ـ اگر برای متوفی اولاد یا اولاد اولاد موجود نباشد، هر یک از ابوین در صورت انفراد تمام ارث "
          "را می‌برد و اگر پدر و مادر هر دو زنده باشند مادر یک ثلث و پدر دو ثلث می‌برد؛ لیکن اگر مادر حاجب "
          "داشته باشد سدس ترکه متعلق به مادر و بقیه مال پدر است."),
    907: ("ماده ۹۰۷ ـ اگر متوفی ابوین نداشته و یک یا چند اولاد داشته باشد: فرزند منحصر تمام ترکه را می‌برد؛ "
          "اولاد متعدد همه پسر یا همه دختر بالسویه؛ و اگر پسر و دختر باشند پسر دو برابر دختر می‌برد."),
    908: ("ماده ۹۰۸ ـ هرگاه پدر یا مادر یا هر دو با یک دختر باشند، فرض هر یک از ابوین سدس و فرض دختر نصف است و "
          "مابقی بین همه به نسبت فرضشان تقسیم می‌شود مگر اینکه مادر حاجب داشته باشد که از مابقی چیزی نمی‌برد."),
    909: ("ماده ۹۰۹ ـ هرگاه پدر یا مادر یا هر دو با چند دختر باشند، فرض دخترها دو ثلث (بالسویه) و فرض هر یک از "
          "ابوین یک سدس است و مابقی اگر باشد بین همه به نسبت فرضشان تقسیم می‌شود مگر اینکه مادر حاجب داشته باشد."),
    "910": ("ابوین با اولاد ذکور: هر یک از ابوین یک سدس می‌برد و باقی بین اولاد تقسیم می‌شود (پسر دو برابر دختر)؛ "
            "قانون مدنی در این فرض صریح نیست و این قاعده مطابق فقه امامیه است."),
    911: ("ماده ۹۱۱ ـ هرگاه میت اولاد بلاواسطه نداشته باشد، اولاد اولاد او قائم‌مقام اولاد بوده و با ابوین "
          "ارث می‌برند. تقسیم بین اولاد اولاد برحسب نسل است: هر نسل حصهٔ کسی را می‌برد که به توسط او به میت "
          "می‌رسد و در هر نسل پسر دو برابر دختر می‌برد."),
    913: ("ماده ۹۱۳ ـ زوج نصف و زوجه ربع ترکه را می‌برد اگر میت اولاد یا اولاد اولاد نداشته باشد، و زوج ربع و "
          "زوجه ثمن را اگر اولاد یا اولاد اولاد داشته باشد؛ مابقی بین سایر وراث تقسیم می‌شود."),
    914: ("ماده ۹۱۴ ـ اگر ترکه کفاف نصیب همهٔ صاحبان فرض را نکند، نقص بر بنت و بنتین وارد می‌شود و اگر زیادتی "
          "باشد و وارث قرابتی نباشد، زیاده بین صاحبان فرض به نسبت فرضشان رد می‌شود؛ لیکن زوج و زوجه مطلقاً و "
          "مادر اگر حاجب داشته باشد از زیادی چیزی نمی‌برند."),
    917: "ماده ۹۱۷ ـ هر یک از وراث طبقهٔ دوم اگر تنها باشد تمام ارث را می‌برد.",
    918: ("ماده ۹۱۸ ـ اگر میت اخوهٔ ابوینی داشته باشد اخوهٔ ابی ارث نمی‌برند؛ اخوهٔ ابوینی و ابی هیچ‌کدام "
          "اخوهٔ امی را محروم نمی‌کنند."),
    919: "ماده ۹۱۹ ـ چند برادر یا چند خواهر ابوینی یا ابی ترکه را بالسویه تقسیم می‌کنند.",
    920: "ماده ۹۲۰ ـ برادران و خواهران ابوینی یا ابی: حصهٔ ذکور دو برابر اناث است.",
    921: "ماده ۹۲۱ ـ چند برادر و خواهر امی ترکه را بالسویه تقسیم می‌کنند.",
    922: ("ماده ۹۲۲ ـ اخوهٔ ابوینی (یا ابی) با اخوهٔ امی: کلالهٔ امی اگر یکی باشد سدس و اگر متعدد باشد ثلث "
          "(بالسویه) و بقیه مال اخوهٔ ابوینی یا ابی است."),
    923: ("ماده ۹۲۳ ـ اجداد: ابی‌ها ذکور دو برابر اناث، امی‌ها بالسویه؛ اگر جد ابی و امی با هم باشند ثلث به امی و "
          "دو ثلث به ابی می‌رسد."),
    924: ("ماده ۹۲۴ ـ اجداد با کلاله: دو ثلث به خویشان پدری (ذکور دو برابر اناث) و یک ثلث به خویشان مادری "
          "(بالسویه)؛ لیکن اگر خویش مادری فقط یک برادر یا خواهر امی باشد سدس می‌برد."),
    929: "ماده ۹۲۹ ـ هر یک از وراث طبقهٔ سوم اگر تنها باشد تمام ارث را می‌برد.",
    930: "ماده ۹۳۰ ـ با اعمام یا اخوال ابوینی، اعمام یا اخوال ابی ارث نمی‌برند.",
    931: ("ماده ۹۳۱ ـ چند عمو یا عمه همه ابوینی، همه ابی یا همه امی بالسویه؛ عمو و عمه با هم: امی‌ها بالسویه و "
          "ابوینی یا ابی‌ها ذکور دو برابر اناث."),
    932: ("ماده ۹۳۲ ـ اعمام امی با اعمام ابوینی یا ابی: امی اگر تنها باشد سدس و اگر متعدد ثلث (بالسویه)؛ باقی به "
          "اعمام ابوینی یا ابی (ذکور دو برابر اناث)."),
    933: "ماده ۹۳۳ ـ چند دایی یا خاله یا دایی و خاله، همه ابوینی یا همه ابی یا همه امی، بالسویه تقسیم می‌کنند.",
    934: ("ماده ۹۳۴ ـ دایی و خالهٔ ابوینی یا ابی با دایی و خالهٔ امی: امی اگر یکی سدس، اگر متعدد ثلث (بالسویه)؛ "
          "مابقی بین ابوینی یا ابی‌ها بالسویه."),
    935: ("ماده ۹۳۵ ـ اعمام با اخوال: ثلث به اخوال و دو ثلث به اعمام؛ در هر گروه امیِ تنها سدس و امی‌های متعدد "
          "ثلثِ حصهٔ آن گروه را می‌برند؛ بین اخوال بالسویه و بین اعمام ابوینی یا ابی ذکور دو برابر اناث."),
    949: ("ماده ۹۴۹ ـ در صورت نبودن هیچ وارث دیگر جز زوج یا زوجه، شوهر تمام ترکهٔ زن را می‌برد لیکن زن فقط "
          "نصیب خود را و بقیهٔ ترکه در حکم مال بلاوارث و تابع مادهٔ ۸۶۶ است."),
}


@dataclass
class Branch:
    """فرزند فوت‌شدهٔ متوفی که فرزند (نوه) دارد — نوه‌ها قائم‌مقام او هستند (مادهٔ ۹۱۱)."""
    child_is_son: bool
    grandsons: int = 0
    granddaughters: int = 0


@dataclass
class Heirs:
    marital: str = SINGLE
    wives: int = 1
    counts: dict = field(default_factory=dict)       # pedar/madar (۰ یا ۱) و تعداد بقیه
    branches: list = field(default_factory=list)     # فقط وقتی متوفی پسر و دختر زنده ندارد

    def n(self, key: str) -> int:
        return int(self.counts.get(key) or 0)


class InheritanceError(ValueError):
    pass


def _male_double(males: list, females: list, total: F) -> tuple:
    """تقسیم total بین گروه‌های مذکر و مؤنث (هر عضو مذکر دو سهم). خروجی: (سهم هر گروه مذکر، ... مؤنث)"""
    units = 2 * sum(males) + sum(females)
    if units == 0:
        return [F(0)] * len(males), [F(0)] * len(females)
    return ([total * 2 * m / units for m in males], [total * f / units for f in females])


class _Ledger:
    def __init__(self):
        self.groups = {}       # key → سهم کل گروه (Fraction از کل ترکه)
        self.extra_rows = {}   # برچسب → [سهم هر نفر، تعداد] — نوه‌ها (چند مرحله جمع می‌شود: فرض + رد/نقص)
        self.remainder = F(1)
        self.laws = []
        self.notes = []

    def give(self, key, share: F):
        if share == 0:
            return
        self.groups[key] = self.groups.get(key, F(0)) + share
        self.remainder -= share

    def law(self, k):
        t = A[k]
        if t not in self.laws:
            self.laws.append(t)


def calculate(h: Heirs, estate: int | None = None) -> dict:
    """
    خروجی: {"rows": [{"key","label","count","per_person","group"}], "laws", "notes", "estate", "inputs"}
    per_person و group از نوع Fraction (کسر از کل ترکه) هستند.
    """
    for k, v in h.counts.items():
        if k not in LABELS or int(v) < 0:
            raise InheritanceError(f"ورودی نامعتبر: {k}")
    for k in ("pedar", "madar", *GRANDPARENTS):
        if h.n(k) > 1:
            raise InheritanceError(f"{LABELS[k]} حداکثر یک نفر است.")
    if h.marital == HAS_WIFE and not (1 <= h.wives <= 4):
        raise InheritanceError("تعداد همسر باید بین ۱ تا ۴ باشد.")

    L = _Ledger()
    n = h.n
    branches = [b for b in h.branches if b.grandsons + b.granddaughters > 0] if not (n("pesar") or n("dokhtar")) else []
    has_desc = n("pesar") > 0 or n("dokhtar") > 0 or bool(branches)
    class1 = has_desc or n("pedar") or n("madar")
    class2 = any(n(k) for k in CLASS2_KEYS)
    class3 = any(n(k) for k in CLASS3_KEYS)

    # ── همسر (۹۱۳) ──
    if h.marital == HAS_HUSBAND:
        L.give("zoj", F(1, 4) if has_desc else F(1, 2))
        L.law(913)
    elif h.marital == HAS_WIFE:
        L.give("zojeh", F(1, 8) if has_desc else F(1, 4))
        L.law(913)

    if class1:
        _class1(h, L, branches)
    elif class2:
        _class2(h, L)
    elif class3:
        _class3(h, L)
    elif h.marital == HAS_HUSBAND:
        L.give("zoj", L.remainder)       # ۹۴۹
        L.law(949)
    else:
        L.give("hakem", L.remainder)     # ۸۶۶ / ۹۴۹
        L.law(949 if h.marital == HAS_WIFE else 866)

    if L.remainder != 0:   # نباید رخ دهد — نشانهٔ باگ منطق
        raise InheritanceError(f"جمع سهام برابر کل ترکه نشد (باقی‌مانده {L.remainder}).")

    rows = []
    for key, share in L.groups.items():
        cnt = h.wives if key == "zojeh" else (1 if key in ("zoj", "hakem") else max(1, n(key)))
        rows.append({"key": key, "label": LABELS[key], "count": cnt, "per_person": share / cnt, "group": share})
    for label, (per, cnt) in L.extra_rows.items():
        if per == 0:
            continue
        rows.append({"key": "naveh", "label": label, "count": cnt, "per_person": per, "group": per * cnt})
    return {"rows": rows, "laws": L.laws, "notes": L.notes, "estate": estate, "inputs": describe_inputs(h)}


# ══════════════════════════════════════════════════════════════════
# طبقهٔ اول
# ══════════════════════════════════════════════════════════════════
def _class1(h: Heirs, L: _Ledger, branches: list):
    n = h.n
    father, mother = bool(n("pedar")), bool(n("madar"))
    sons_b = [b for b in branches if b.child_is_son]
    daus_b = [b for b in branches if not b.child_is_son]
    S = n("pesar") + len(sons_b)       # واحدهای پسر (پسران زنده + شاخه‌های پسر فوت‌شده)
    D = n("dokhtar") + len(daus_b)
    # حجب مادر (۸۹۲ ب): پدر زنده + اخوهٔ ابوینی یا ابی کافی
    bro = n("baradarAbaveyni") + n("baradarAbi")
    sis = n("khaharAbaveyni") + n("khaharAbi")
    hajb = father and (bro >= 2 or (bro == 1 and sis >= 2) or sis >= 4)
    if branches:
        L.law(911)

    def to_children(total: F):
        """تقسیم بین فرزندان (پسر دو برابر دختر) و سپس شاخه‌های نوه‌ها."""
        if total == 0 or (S == 0 and D == 0):
            return
        s_share, d_share = _male_double([S], [D], total)
        per_son = s_share[0] / S if S else F(0)
        per_dau = d_share[0] / D if D else F(0)
        if n("pesar"):
            L.give("pesar", per_son * n("pesar"))
        if n("dokhtar"):
            L.give("dokhtar", per_dau * n("dokhtar"))
        for i, b in enumerate(branches, 1):
            unit = per_son if b.child_is_son else per_dau
            _to_branch(L, b, i, unit)

    if S == 0 and D == 0:          # فقط ابوین (۹۰۶)
        L.law(906)
        if father and mother:
            L.give("madar", F(1, 6) if hajb else F(1, 3))
            L.give("pedar", L.remainder)
        elif father:
            L.give("pedar", L.remainder)
        else:
            L.give("madar", L.remainder)
    elif not (father or mother):   # فقط اولاد (۹۰۷)
        L.law(907)
        to_children(L.remainder)
    elif S > 0:                    # ابوین با اولاد ذکور
        L.law("910")
        if father:
            L.give("pedar", F(1, 6))
        if mother:
            L.give("madar", F(1, 6))
        to_children(L.remainder)
    else:                          # ابوین با دختر/دختران (۹۰۸ / ۹۰۹)
        L.law(908 if D == 1 else 909)
        d_fard = F(1, 2) if D == 1 else F(2, 3)
        fard = {}
        if father:
            fard["pedar"] = F(1, 6)
        if mother:
            fard["madar"] = F(1, 6)
        for k, v in fard.items():
            L.give(k, v)
        to_children(d_fard)
        if L.remainder < 0:        # نقص بر دختر/دختران (۹۱۴)
            L.law(914)
            L.notes.append("ترکه کفاف همهٔ فروض را نمی‌کند؛ نقص بر سهم دختر(ان) وارد شد (مادهٔ ۹۱۴).")
            to_children(L.remainder)
        elif L.remainder > 0:      # رد مازاد به نسبت فرض (مادر حاجب‌دار و همسر سهمی ندارند)
            L.law(914)
            takers = {k: v for k, v in fard.items() if not (k == "madar" and hajb)}
            takers["_children"] = d_fard
            total = sum(takers.values())
            rest = L.remainder
            for k, v in takers.items():
                part = rest * v / total
                if k == "_children":
                    to_children(part)
                else:
                    L.give(k, part)
            L.notes.append("مازاد ترکه به نسبت فرض بین ابوین و دختر(ان) رد شد (مادهٔ ۹۱۴)"
                           + ("؛ مادر به‌دلیل حاجب از مازاد سهمی نبرد." if hajb and mother else "."))
    if hajb and mother:
        L.law(892)


def _to_branch(L: _Ledger, b: Branch, idx: int, unit: F):
    """سهم یک فرزند فوت‌شده بین نوه‌های همان شاخه (پسر دو برابر دختر)."""
    who = "پسر" if b.child_is_son else "دختر"
    s, d = _male_double([b.grandsons], [b.granddaughters], unit)
    for label, total, cnt in ((f"نوهٔ پسری از {who} فوت‌شده (شاخهٔ {idx})", s[0], b.grandsons),
                              (f"نوهٔ دختری از {who} فوت‌شده (شاخهٔ {idx})", d[0], b.granddaughters)):
        if cnt:
            row = L.extra_rows.setdefault(label, [F(0), cnt])
            row[0] += total / cnt
            L.remainder -= total


# ══════════════════════════════════════════════════════════════════
# طبقهٔ دوم
# ══════════════════════════════════════════════════════════════════
def _class2(h: Heirs, L: _Ledger):
    n = h.n
    c = {k: n(k) for k in CLASS2_KEYS}
    if (c["baradarAbaveyni"] or c["khaharAbaveyni"]) and (c["baradarAbi"] or c["khaharAbi"]):
        L.law(918)
        L.notes.append("اخوهٔ ابی به‌دلیل وجود اخوهٔ ابوینی ارث نمی‌برند (مادهٔ ۹۱۸).")
        c["baradarAbi"] = c["khaharAbi"] = 0
    present = [k for k, v in c.items() if v]
    gp_abi = c["jaddAbi"] + c["jaddehAbi"]
    gp_ommi = c["jaddOmmi"] + c["jaddehOmmi"]
    sib_pat = c["baradarAbaveyni"] + c["khaharAbaveyni"] + c["baradarAbi"] + c["khaharAbi"]
    sib_ommi = c["siblingOmmi"]
    has_gp = gp_abi + gp_ommi > 0
    has_sib = sib_pat + sib_ommi > 0

    def paternal(total: F):
        males = [c["jaddAbi"], c["baradarAbaveyni"], c["baradarAbi"]]
        females = [c["jaddehAbi"], c["khaharAbaveyni"], c["khaharAbi"]]
        ms, fs = _male_double(males, females, total)
        for k, v in zip(["jaddAbi", "baradarAbaveyni", "baradarAbi", "jaddehAbi", "khaharAbaveyni", "khaharAbi"],
                        ms + fs):
            if c[k]:
                L.give(k, v)

    def maternal_equal(total: F):
        people = c["jaddOmmi"] + c["jaddehOmmi"] + sib_ommi
        for k in ("jaddOmmi", "jaddehOmmi", "siblingOmmi"):
            if c[k]:
                L.give(k, total * c[k] / people)

    if len(present) == 1:
        k = present[0]
        L.law(917 if c[k] == 1 else (921 if k == "siblingOmmi" else 919))
        L.give(k, L.remainder)
    elif not has_gp:                       # فقط برادر و خواهر
        if sib_pat and sib_ommi:
            L.law(922)
            L.give("siblingOmmi", F(1, 6) if sib_ommi == 1 else F(1, 3))
        else:
            L.law(920)
        paternal(L.remainder)
    elif not has_sib:                      # فقط اجداد
        L.law(923)
        if gp_abi and gp_ommi:
            maternal_equal(F(1, 3))
            paternal(L.remainder)
        elif gp_abi:
            paternal(L.remainder)
        else:
            maternal_equal(L.remainder)
    else:                                  # اجداد با کلاله
        L.law(924)
        pat_side = gp_abi + sib_pat
        mat_side = gp_ommi + sib_ommi
        if pat_side and mat_side:
            maternal_equal(F(1, 6) if (gp_ommi == 0 and sib_ommi == 1) else F(1, 3))
            paternal(L.remainder)
        elif pat_side:
            paternal(L.remainder)
        else:
            maternal_equal(L.remainder)


# ══════════════════════════════════════════════════════════════════
# طبقهٔ سوم
# ══════════════════════════════════════════════════════════════════
def _class3(h: Heirs, L: _Ledger):
    n = h.n
    c = {k: n(k) for k in CLASS3_KEYS}
    if (c["amooAbaveyni"] or c["ammehAbaveyni"]) and (c["amooAbi"] or c["ammehAbi"]):
        L.law(930)
        L.notes.append("اعمام ابی به‌دلیل وجود اعمام ابوینی ارث نمی‌برند (مادهٔ ۹۳۰).")
        c["amooAbi"] = c["ammehAbi"] = 0
    if (c["daieAbaveyni"] or c["khalehAbaveyni"]) and (c["daieAbi"] or c["khalehAbi"]):
        L.law(930)
        L.notes.append("اخوال ابی به‌دلیل وجود اخوال ابوینی ارث نمی‌برند (مادهٔ ۹۳۰).")
        c["daieAbi"] = c["khalehAbi"] = 0
    present = [k for k, v in c.items() if v]
    amam_pat = c["amooAbaveyni"] + c["ammehAbaveyni"] + c["amooAbi"] + c["ammehAbi"]
    amam_ommi = c["amooOmmi"] + c["ammehOmmi"]
    akhwal_pat = c["daieAbaveyni"] + c["khalehAbaveyni"] + c["daieAbi"] + c["khalehAbi"]
    akhwal_ommi = c["daieOmmi"] + c["khalehOmmi"]

    def equal(keys, total):
        people = sum(c[k] for k in keys)
        for k in keys:
            if c[k]:
                L.give(k, total * c[k] / people)

    def amam_double(total):
        ms, fs = _male_double([c["amooAbaveyni"], c["amooAbi"]], [c["ammehAbaveyni"], c["ammehAbi"]], total)
        for k, v in zip(["amooAbaveyni", "amooAbi", "ammehAbaveyni", "ammehAbi"], ms + fs):
            if c[k]:
                L.give(k, v)

    ommi_amam = ["amooOmmi", "ammehOmmi"]
    ommi_akhwal = ["daieOmmi", "khalehOmmi"]
    pat_akhwal = ["daieAbaveyni", "khalehAbaveyni", "daieAbi", "khalehAbi"]

    if len(present) == 1:
        L.law(929)
        L.give(present[0], L.remainder)
    elif amam_pat + amam_ommi and not (akhwal_pat + akhwal_ommi):      # فقط اعمام
        if amam_pat and amam_ommi:
            L.law(932)
            equal(ommi_amam, F(1, 6) if amam_ommi == 1 else F(1, 3))
            amam_double(L.remainder)
        elif amam_pat:
            L.law(931)
            amam_double(L.remainder)
        else:
            L.law(931)
            equal(ommi_amam, L.remainder)
    elif akhwal_pat + akhwal_ommi and not (amam_pat + amam_ommi):      # فقط اخوال
        if akhwal_pat and akhwal_ommi:
            L.law(934)
            equal(ommi_akhwal, F(1, 6) if akhwal_ommi == 1 else F(1, 3))
            equal(pat_akhwal, L.remainder)
        else:
            L.law(933)
            equal(pat_akhwal + ommi_akhwal, L.remainder)
    else:                                                               # اعمام با اخوال (۹۳۵)
        L.law(935)
        akhwal_total = F(1, 3)
        if akhwal_ommi and akhwal_pat:
            ommi_part = akhwal_total * (F(1, 6) if akhwal_ommi == 1 else F(1, 3))
            equal(ommi_akhwal, ommi_part)
            equal(pat_akhwal, akhwal_total - ommi_part)
        else:
            equal(pat_akhwal + ommi_akhwal, akhwal_total)
        amam_total = L.remainder
        if amam_ommi and amam_pat:
            equal(ommi_amam, amam_total * (F(1, 6) if amam_ommi == 1 else F(1, 3)))
            amam_double(L.remainder)
        elif amam_pat:
            amam_double(L.remainder)
        else:
            equal(ommi_amam, L.remainder)


# ══════════════════════════════════════════════════════════════════
# نمایش
# ══════════════════════════════════════════════════════════════════
def describe_inputs(h: Heirs) -> list:
    out = [("وضعیت تأهل متوفی", MARITAL_TITLES[h.marital])]
    if h.marital == HAS_WIFE and h.wives > 1:
        out.append(("تعداد همسران", str(h.wives)))
    for k, v in h.counts.items():
        if v:
            out.append((LABELS[k], "زنده" if k in ("pedar", "madar", *GRANDPARENTS) else str(v)))
    for i, b in enumerate(h.branches, 1):
        if b.grandsons + b.granddaughters:
            out.append((f"شاخهٔ نوه {i}", f"فرزند فوت‌شده: {'پسر' if b.child_is_son else 'دختر'} — "
                                         f"{b.grandsons} نوهٔ پسر، {b.granddaughters} نوهٔ دختر"))
    return out


def _lcm(a: int, b: int) -> int:
    from math import gcd
    return a * b // gcd(a, b)


def common_denominator(rows: list) -> int:
    d = 1
    for r in rows:
        d = _lcm(d, r["per_person"].denominator)
    return d


def share_text(f: F, lcd: int = None) -> str:
    if lcd:
        return f"{f.numerator * (lcd // f.denominator)}/{lcd}"
    return f"{f.numerator}/{f.denominator}"


def format_result_fa(r: dict) -> str:
    rows, estate = r["rows"], r["estate"]
    lcd = common_denominator(rows)
    lines = ["⚖️ *نتیجهٔ تقسیم ترکه (سهم‌الارث)*", ""]
    for row in rows:
        each = "هر " if row["count"] > 1 else ""
        pct = float(row["per_person"]) * 100
        line = (f"• {each}{row['label']}"
                + (f" ({row['count']} نفر)" if row["count"] > 1 else "")
                + f": {share_text(row['per_person'])} — {pct:.2f}٪".replace(".00٪", "٪"))
        if estate:
            line += f"\n   💰 {round(row['per_person'] * estate):,} ریال"
        lines.append(line)
    if lcd > 1 and len(rows) > 1:
        lines += ["", f"🔢 با مخرج مشترک {lcd}: " + "، ".join(
            f"{('هر ' if row['count'] > 1 else '')}{row['label']} {share_text(row['per_person'], lcd)}" for row in rows)]
    if estate:
        lines += ["", f"🏦 ارزش ترکه: {estate:,} ریال"]
    for note in r["notes"]:
        lines.append(f"\n🔸 {note}")
    lines += ["", "ℹ️ سهام بدون در نظر گرفتن دیون، وصیت و مالیات بر ارث و صرفاً بر اساس قواعد ارث قانون مدنی است."]
    return "\n".join(lines)
