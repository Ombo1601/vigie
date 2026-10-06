"""i18n — the bilingual catalogue and the hand-written FR/EN formatters.

French is the default and the version that prevails; English mirrors Vigie's
own words only, never publisher text (docs/I18N.md section 4).

Catalogue
---------
`scripts/i18n/fr.json` and `scripts/i18n/en.json` are flat `{key: text}` maps
with the same keys. A text may carry `{name}` placeholders (`str.format`
syntax; literal braces are not used). Plural keys come in pairs
`<key>.one` / `<key>.other` and are read with `tn()`. A key missing from either
file, or a placeholder set that differs between the two files, is reported by
`validate()` and fails the test suite: there is never a silent fallback to
French, and `t()` raises `KeyError` on an unknown key.

Formatters
----------
No `locale` module (it depends on the operating system) and no `zoneinfo`
(it depends on installed tzdata): the Quebec City wall clock is computed from
the fixed North American rule (UTC-5, UTC-4 from the second Sunday of March
02:00 to the first Sunday of November 02:00, local), the rule in force since
2007 and applied to every year so the output never depends on the machine.
Everything here is pure: the same input gives the same bytes, with no wall
clock and no `PYTHONHASHSEED` dependence.

    FR  6 octobre 2026 · 14 h 05 · 1 234,50 $
    EN  October 6, 2026 · 2:05 p.m. · $1,234.50

Group separators are U+00A0 in French (fr-CA) and "," in English; negative
numbers use U+2212.
"""
from __future__ import annotations

import json
import re
import string
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

LANGS = ("fr", "en")
DEFAULT_LANG = "fr"
CATALOGUE_DIR = Path(__file__).resolve().parent / "i18n"
NBSP = " "
MINUS = "−"

_CACHE: dict[str, dict[str, str]] = {}
# "a.m." / "p.m." already end in a period: a sentence that ends on a time must not
# print two ("at 2:05 p.m.." -> "at 2:05 p.m.").
_DOUBLE_DOT = re.compile(r"\b([ap]\.m\.)\.")


# --------------------------------------------------------------------------- #
# Catalogue
# --------------------------------------------------------------------------- #
def catalogue(lang: str) -> dict[str, str]:
    if lang not in LANGS:
        raise KeyError(f"i18n: unsupported language {lang!r}")
    if lang not in _CACHE:
        doc = json.loads((CATALOGUE_DIR / f"{lang}.json").read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in doc.items()):
            raise ValueError(f"i18n: {lang}.json must be a flat string map")
        _CACHE[lang] = doc
    return _CACHE[lang]


def placeholders(text: str) -> frozenset[str]:
    return frozenset(name for _lit, name, _spec, _conv in string.Formatter().parse(text) if name)


def t(key: str, lang: str, **kw: object) -> str:
    """One catalogue text. Unknown key or missing placeholder raises KeyError."""
    cat = catalogue(lang)
    if key not in cat:
        raise KeyError(f"i18n: missing key {key!r} for {lang!r}")
    return _DOUBLE_DOT.sub(r"\1", cat[key].format(**kw))


def plural_form(n: int, lang: str) -> str:
    """French counts 0 and 1 as singular; English only 1."""
    n = abs(int(n))
    if lang == "fr":
        return "one" if n < 2 else "other"
    return "one" if n == 1 else "other"


def tn(key: str, n: int, lang: str, **kw: object) -> str:
    """Plural text: `<key>.one` / `<key>.other`; `{n}` is the formatted count."""
    return t(f"{key}.{plural_form(n, lang)}", lang, n=fmt_int(n, lang), **kw)


def validate() -> list[str]:
    """Problems in the catalogue pair (empty list = healthy)."""
    fr, en = catalogue("fr"), catalogue("en")
    problems: list[str] = []
    for key in sorted(set(fr) - set(en)):
        problems.append(f"en.json lacks {key}")
    for key in sorted(set(en) - set(fr)):
        problems.append(f"fr.json lacks {key}")
    for key in sorted(set(fr) & set(en)):
        if placeholders(fr[key]) != placeholders(en[key]):
            problems.append(f"placeholders differ for {key}: {sorted(placeholders(fr[key]))} vs {sorted(placeholders(en[key]))}")
    for lang, cat in (("fr", fr), ("en", en)):
        for key, text in sorted(cat.items()):
            if not text.strip():
                problems.append(f"{lang}.json: empty text for {key}")
        singles = {k[:-4] for k in cat if k.endswith(".one")}
        others = {k[:-6] for k in cat if k.endswith(".other")}
        for base in sorted(singles ^ others):
            problems.append(f"{lang}.json: plural pair incomplete for {base}")
    return problems


# --------------------------------------------------------------------------- #
# Time: fixed-rule America/Toronto
# --------------------------------------------------------------------------- #
def parse_instant(value: object) -> datetime | None:
    """ISO 8601 text (Z, +00:00 or any offset) to an aware UTC datetime.

    A zoneless stamp is read as UTC (the stores write UTC); anything that does
    not parse is None, never a guess.
    """
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00").replace("z", "+00:00"))
        except (ValueError, OverflowError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _nth_sunday(year: int, month: int, nth: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (nth - 1))


def toronto_offset_hours(utc: datetime) -> int:
    """-4 in daylight time, -5 otherwise (fixed rule, see module docstring)."""
    year = utc.year
    start = datetime.combine(_nth_sunday(year, 3, 2), datetime.min.time(), timezone.utc) + timedelta(hours=7)
    end = datetime.combine(_nth_sunday(year, 11, 1), datetime.min.time(), timezone.utc) + timedelta(hours=6)
    return -4 if start <= utc < end else -5


def local_dt(value: object) -> datetime | None:
    """Quebec City wall-clock fields as a naive datetime, or None."""
    utc = parse_instant(value)
    if utc is None:
        return None
    return (utc + timedelta(hours=toronto_offset_hours(utc))).replace(tzinfo=None)


MONTHS_LONG = {
    "fr": ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
           "septembre", "octobre", "novembre", "décembre"),
    "en": ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"),
}
MONTHS_SHORT = {
    "fr": ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juill.", "août",
           "sept.", "oct.", "nov.", "déc."),
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
}
WEEKDAYS = {
    "fr": ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"),
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
}


def _day(d: int, lang: str) -> str:
    return "1er" if (lang == "fr" and d == 1) else str(d)


def _date_text(y: int, m: int, d: int, lang: str, *, short: bool, year: bool) -> str:
    month = (MONTHS_SHORT if short else MONTHS_LONG)[lang][m - 1]
    if lang == "fr":
        return f"{_day(d, lang)} {month}" + (f" {y}" if year else "")
    return f"{month} {d}" + (f", {y}" if year else "")


def fmt_date(value: object, lang: str, *, weekday: bool = False, short: bool = False, year: bool = True) -> str:
    """`6 octobre 2026` / `October 6, 2026` (optional weekday, short month)."""
    dt = local_dt(value)
    if dt is None:
        return ""
    text = _date_text(dt.year, dt.month, dt.day, lang, short=short, year=year)
    if weekday:
        name = WEEKDAYS[lang][dt.weekday()]
        return f"{name} {text}" if lang == "fr" else f"{name}, {text}"
    return text


def fmt_day(value: object, lang: str) -> str:
    """Short day without year: `1er oct.` / `Oct 1`."""
    return fmt_date(value, lang, short=True, year=False)


def fmt_ymd(ymd: object, lang: str, *, short: bool = True) -> str:
    """A zoneless `YYYY-MM-DD` (a declared date, not an instant), with the year."""
    raw = str(ymd or "").strip()[:10]
    try:
        d = date.fromisoformat(raw)
    except ValueError:
        return ""
    return _date_text(d.year, d.month, d.day, lang, short=short, year=True)


def fmt_time(value: object, lang: str) -> str:
    """`14 h 05` (`14 h` on the hour) / `2:05 p.m.`."""
    dt = local_dt(value)
    if dt is None:
        return ""
    if lang == "fr":
        return f"{dt.hour} h {dt.minute:02d}" if dt.minute else f"{dt.hour} h"
    h12 = dt.hour % 12 or 12
    return f"{h12}:{dt.minute:02d} {'a.m.' if dt.hour < 12 else 'p.m.'}"


def fmt_datetime(value: object, lang: str, *, short: bool = False) -> str:
    d, tm = fmt_date(value, lang, short=short), fmt_time(value, lang)
    if not d or not tm:
        return ""
    return f"{d}, {tm}" if lang == "en" else f"{d} à {tm}"


def local_hour(value: object) -> int | None:
    dt = local_dt(value)
    return None if dt is None else dt.hour


def _group(digits: str, sep: str) -> str:
    out = []
    while len(digits) > 3:
        out.insert(0, digits[-3:])
        digits = digits[:-3]
    out.insert(0, digits)
    return sep.join(out)


def fmt_int(n: object, lang: str) -> str:
    try:
        v = int(n)  # type: ignore[call-overload]
    except (TypeError, ValueError, OverflowError):
        return ""
    sep = NBSP if lang == "fr" else ","
    return (MINUS if v < 0 else "") + _group(str(abs(v)), sep)


def fmt_money(cents: object, lang: str) -> str:
    """Integer cents CAD: `1 234,50 $` / `$1,234.50` (never a float)."""
    try:
        v = int(cents)  # type: ignore[call-overload]
    except (TypeError, ValueError, OverflowError):
        return ""
    whole, frac = divmod(abs(v), 100)
    sign = MINUS if v < 0 else ""
    if lang == "fr":
        return f"{sign}{_group(str(whole), NBSP)},{frac:02d}{NBSP}$"
    return f"{sign}${_group(str(whole), ',')}.{frac:02d}"


def fmt_percent_bp(bp: object, lang: str) -> str:
    """Basis points to a percentage: 350 -> `3,5 %` / `3.5%`."""
    try:
        v = int(bp)  # type: ignore[call-overload]
    except (TypeError, ValueError, OverflowError):
        return ""
    whole, frac = divmod(abs(v), 100)
    digits = f"{frac:02d}".rstrip("0")
    sign = MINUS if v < 0 else ""
    if lang == "fr":
        return f"{sign}{whole}{',' + digits if digits else ''}{NBSP}%"
    return f"{sign}{whole}{'.' + digits if digits else ''}%"


def fmt_duration(seconds: object, lang: str) -> str:
    """`45 min`, `2 h 05` / `2 hr 5 min`; never below one minute, never days."""
    try:
        secs = int(seconds)  # type: ignore[call-overload]
    except (TypeError, ValueError, OverflowError):
        return ""
    mins = max(1, round(abs(secs) / 60))
    if mins < 60:
        return f"{mins} min"
    h, m = divmod(mins, 60)
    if lang == "fr":
        return f"{h} h {m:02d}" if m else f"{h} h"
    return f"{h} hr {m} min" if m else f"{h} hr"
