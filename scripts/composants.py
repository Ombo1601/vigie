"""composants — the bilingual UI kit of the event-first front door.

Pure functions that return escaped HTML strings. No I/O except
`write_demo_site()`, no network, no wall clock, no template engine, stdlib
only. Every dynamic string goes through the one `esc()` below; every Vigie
sentence comes from the catalogue (`scripts/i18n/{fr,en}.json`, read with
`i18n.t`). The CSS is `public/assets/evenements.css` and the only script is
`public/assets/evenements.js` (progressive enhancement; every page is complete
and readable with JavaScript disabled). Pages carry no inline `style`
attribute, no `<style>` block and no executed inline script, so they fit the
strict CSP of vercel.json (`default-src 'none'; script-src 'self';
style-src 'self'`); the one JSON data block is not executed.

Nothing here is wired into the pipeline: a later integrator calls
`edition_page()` / `event_page()` from the render step and writes the files.

R8/R1/R2: publisher text (titles, excerpts, bylines, photo credits, headline
history, shared words, figures) appears only on *current* pages, always inside
an element that carries its own `lang`, verbatim (an excerpt is cut at
EXCERPT_MAX characters, never reworded), with author and photo credit when
the view carries them and an outbound link (`rel="noopener noreferrer"`).
A view with `permanent = True` renders NO publisher text at all: only Vigie's
labels, ids, institution names, times, links, counts, anchors and seals.

--------------------------------------------------------------------------
View contract (plain dicts, tolerant: a missing optional key renders nothing)
--------------------------------------------------------------------------
Text that exists in both languages is `{"fr": str, "en": str}` (Vigie's own
words: labels, notes); a bare string is accepted for names (an institution
keeps the name it uses). Times are ISO 8601 strings (UTC offsets accepted);
declared dates are `YYYY-MM-DD`; money is integer cents; nothing is a float.

EventView (one event, from docs/EVENTS.md plus the render fields)::

  event_id       "ev-" + 16 hex            (page path; DOM-safe)
  permanent      bool                      True = no publisher text rendered
  type           controlled code           (docs/I18N.md table A)
  places         [codes]
  label          {fr, en}                  "type . place", Vigie's words
  type_label     {fr, en}                  kind chip, from the vocabulary
  place_label    {fr, en}                  place chip, from the vocabulary
  tier           "certain" | "probable" | "possible" | None   grouping level
  window_state   "in_window" | "out_of_window"
  activity       "new" | "developed" | "quiet"
  born_edition, last_edition   ISO        collection clocks
  seals          [int]                     main-chain seq numbers
  members        [MemberView]              any order; the kit sorts them
  independence   {"groups": [[item_id..]..], "count": int}
  language_pairs [{"fr": item_id, "en": item_id, "rule": str, "same_owner": bool}]
  anchors        [AnchorView]              official-record pointers
  facts          [{"slot": {"kind","unit","subject"},
                   "values": [{"value", "stated_by": [item_id], "institutions": [id]}],
                   "divergent": bool}]     media-derived: never on permanent pages
  why            {"shared": [{"label", "alias": bool}], "gap_seconds": int|None,
                  "window_hours": int, "min_institutions": int,
                  "place_anchor": {fr,en}|str|None, "rule": str}
  words          [{"k": str, "label": str, "forms": [str], "alias": bool}]
                                           shared words for the highlight panel
  silence        [{"institution_name": str, "state": "no_linked_item" |
                   "collection_gap" | "not_established"}]
  neighbours     [{"member": MemberView, "reason": "shared_word" |
                   "same_thread" | "outside_window", "why": {fr,en}|None}]
  ledger         {"changes": [{"at", "institution_name", "before", "after"}]}
                                           headline history (publisher text)

MemberView::

  item_id, institution (id), institution_name (str|{fr,en}), source_id,
  language "fr"|"en"  (the declared source language, never guessed),
  published_at (ISO|None), first_seen (ISO), date_suspect (bool),
  origin_class  official|wire|press_release|own_reporting|unknown,
  ownership_class  public_broadcaster|quebecor|cooperative|independent|government,
  owner_group (code), owner_name (str|{fr,en}, optional group display name),
  url (http(s) only, absent when the source is withdrawn: R10),
  -- publisher text, current pages only --
  title, excerpt, author, photo_credit

AnchorView: {"type": official_item|roadwork|consultation|outage|edition_seal,
  "ref": str, "rule": str, "status": "linked_by_rule",
  "institution_name": str, "published_at": ISO, "url": str,
  "title": str  (publisher text: dropped on permanent pages)}

EditionView (the front door)::

  clock               ISO collection clock of the edition
  period              "morning"|"afternoon"|"evening"|"night" (derived from the
                      local hour of `clock` when absent)
  next_collection     ISO | None             "next collection around" (no promise)
  institutions_followed  int
  events              [EventView]            in published ranking order; the
                                             kit never reorders
  roster              [{"name", "state": in_events|outside|declared|no_items|
                        collection_gap|not_established, "events": int,
                        "articles": int}]
  seal                {"seq": int, "root": hex} | None   a PUBLISHED seal
  official            [{"institution_name", "ownership_class", "published_at",
                        "title", "url", "language"}]
                      declared items linked to no event shown above
  suggestions         [str]                  examples for the on-device search

RoadworksView (its own view model: the official lane is refreshed hourly and
may be newer than the edition; it is never derived from the EditionView)::

  collected_at    ISO         collection time of the declarations (always printed)
  institution_name str        "Ville de Québec"
  total           int         declarations in the collection
  closed          int         of which "all lanes closed"
  planned         int | None  of which planned (optional)
  stale           bool        the builder compares collected_at with its own
                              render clock (the kit has no clock)
  rows            [{"id", "street", "places", "text", "direction", "what",
                    "status": active|planned|pending, "from", "to",
                    "estimated": bool, "impact": the City's vehicle_impact code}]
                  already ordered by the published rule (most restrictive
                  first); the kit never reorders
  attribution, dataset_url, map_url   optional

Page assembly: `edition_page(edition, lang, roadworks=...)`,
`event_page(event, lang, edition=..., followed=...)`; fragments for the
integrator: `event_card`, `roadworks_block`, `official_block`, `end_card`,
`site_header`, `site_footer`, `head_meta`, `document`.
"""
from __future__ import annotations

import html
import json
import re
import shutil
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import i18n  # noqa: E402
from i18n import t, tn  # noqa: E402

SITE_URL = "https://vigieqc.com"
CSS_HREF = "/assets/evenements.css"
FONTS_HREF = "/assets/fonts.css"
JS_SRC = "/assets/evenements.js"
OG_IMAGE = f"{SITE_URL}/assets/og-default.png"
EXCERPT_MAX = 240
RW_MAP_URL = "https://carte.ville.quebec.qc.ca/"

# French paths that have an /en counterpart today. Step 10 of the migration
# widens this tuple surface by surface; a surface is linked in English only
# when it is complete (docs/I18N.md, MIGRATION.md step 10).
EN_MIRROR = ("/", "/evenements.html", "/evenements/")

TIERS = ("certain", "probable", "possible")
ORIGINS = ("official", "wire", "press_release", "own_reporting", "unknown")
OWNERSHIPS = ("public_broadcaster", "quebecor", "cooperative", "independent", "government")
ACTIVITIES = ("new", "developed", "quiet")
ANCHOR_TYPES = ("official_item", "roadwork", "consultation", "outage", "edition_seal")
SILENCE_STATES = ("no_linked_item", "collection_gap", "not_established")
NEIGHBOUR_REASONS = ("shared_word", "same_thread", "outside_window")
ROSTER_STATES = ("in_events", "outside", "declared", "no_items", "collection_gap", "not_established")
PERIODS = ("morning", "afternoon", "evening", "night")
IMPACTS = ("all-lanes-closed", "some-lanes-closed", "alternating-one-way",
           "some-lanes-closed-intermittent-or-short-duration", "all-lanes-open",
           "no-lanes-closed", "unknown")
RW_STATUSES = ("active", "planned", "pending")
RW_DIRECTIONS = ("northbound", "southbound", "eastbound", "westbound", "both-directions")
RW_TYPES = ("road-work", "work-zone", "detour", "incident", "accident", "event")
FACT_UNITS = ("persons_dead", "persons_injured", "persons_arrested", "housing_units", "jobs",
              "vehicles", "buildings", "customers_without_power", "total", "per_month",
              "per_year", "percent_bp")
FACT_KINDS = ("count", "amount", "date", "place", "entity")

# Ownership class -> colour class. Colour never carries meaning alone: the
# class label is always printed next to the swatch.
OWN_COLOR = {
    "public_broadcaster": "c-public",
    "cooperative": "c-coop",
    "quebecor": "c-private",
    "independent": "c-indep",
    "government": "c-gov",
}

_SPOOF = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\xad​-‏‪-‮⁦-⁩﻿\ud800-\udfff]"
)
_ID_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


# --------------------------------------------------------------------------- #
# Primitives
# --------------------------------------------------------------------------- #
def esc(value: object) -> str:
    """The single escape for every dynamic string: display-spoofing controls
    stripped, then HTML-escaped for text and attribute contexts alike."""
    return html.escape(_SPOOF.sub("", str(value if value is not None else "")), quote=True)


def safe_url(value: object) -> str:
    """Only absolute http(s) URLs with a host become links; anything else
    (javascript:, data:, relative, malformed) is dropped, never repaired."""
    raw = _SPOOF.sub("", str(value or "")).strip()
    if not raw or re.search(r"[\s<>\"']", raw):
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    return raw


def dom_id(prefix: str, value: object) -> str:
    return f"{prefix}-{_ID_UNSAFE.sub('', str(value or ''))[:80]}"


def loc(value: object, lang: str) -> str:
    """A `{fr, en}` text (or a bare string) in `lang`; falls back to the other
    language only for data, never for catalogue strings."""
    if isinstance(value, dict):
        other = "en" if lang == "fr" else "fr"
        return str(value.get(lang) or value.get(other) or "")
    return str(value or "")


def _int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError, OverflowError):
        return default


def _has(key: str, lang: str) -> bool:
    return key in i18n.catalogue(lang)


def _pct(x: float) -> str:
    return f"{round(x, 2):g}%"


def _cls(*names: object) -> str:
    return " ".join(str(n) for n in names if n)


def _sr(text: str) -> str:
    return f'<span class="sr">{esc(text)}</span>'


# --------------------------------------------------------------------------- #
# Paths and language links (docs/I18N.md section 1)
# --------------------------------------------------------------------------- #
def has_mirror(fr_path: str) -> bool:
    return any(fr_path == p or (p.endswith("/") and p != "/" and fr_path.startswith(p)) for p in EN_MIRROR)


def en_path(fr_path: str) -> str:
    return "/en" + fr_path


def path_for(lang: str, fr_path: str) -> str:
    """Site-relative URL of a French path in `lang`; an English request for a
    surface without an English mirror yields the French page (never a 404)."""
    if lang == "en" and has_mirror(fr_path):
        return en_path(fr_path)
    return fr_path


def abs_url(lang: str, fr_path: str) -> str:
    return SITE_URL + path_for(lang, fr_path)


def event_path(event_id: object) -> str:
    return f"/evenements/{_ID_UNSAFE.sub('', str(event_id or ''))}.html"


def link_lang_attrs(lang: str, fr_path: str) -> str:
    """When an English page links to a French-only surface, say so to
    assistive technology (WCAG 3.1.2)."""
    if lang == "en" and not has_mirror(fr_path):
        return ' lang="fr" hreflang="fr-CA"'
    return ""


def internal_link(lang: str, fr_path: str, text: str, *, cls: str = "", fragment: str = "") -> str:
    href = path_for(lang, fr_path) + fragment
    c = f' class="{esc(cls)}"' if cls else ""
    return f'<a{c} href="{esc(href)}"{link_lang_attrs(lang, fr_path)}>{esc(text)}</a>'


# --------------------------------------------------------------------------- #
# Document shell: head, header, footer
# --------------------------------------------------------------------------- #
def head_meta(*, lang: str, fr_path: str, title: str, description: str,
              counterpart: bool = True, robots: str = "index, follow",
              scripts: bool = True, page_type: str = "website") -> str:
    """Everything inside <head>.

    canonical is self-referential per language (never cross-language);
    hreflang fr-CA / en-CA / x-default (French) only when the page has a
    counterpart; Open Graph; stylesheet links (self-hosted, no external host).
    """
    here = abs_url(lang, fr_path) if (lang == "fr" or has_mirror(fr_path)) else abs_url("fr", fr_path)
    full_title = f"{title} — {t('site.name', lang)}" if title else t("site.name", lang)
    locale, other_locale = ("fr_CA", "en_CA") if lang == "fr" else ("en_CA", "fr_CA")
    out = [
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="color-scheme" content="light dark">',
        f"<title>{esc(full_title)}</title>",
        f'<meta name="description" content="{esc(description)}">',
        f'<meta name="robots" content="{esc(robots)}">',
        f'<link rel="canonical" href="{esc(here)}">',
    ]
    if counterpart and has_mirror(fr_path):
        out += [
            f'<link rel="alternate" hreflang="fr-CA" href="{esc(abs_url("fr", fr_path))}">',
            f'<link rel="alternate" hreflang="en-CA" href="{esc(abs_url("en", fr_path))}">',
            f'<link rel="alternate" hreflang="x-default" href="{esc(abs_url("fr", fr_path))}">',
        ]
    out += [
        '<link rel="icon" href="/favicon.svg" type="image/svg+xml">',
        '<meta name="theme-color" content="#0a5963">',
        f'<meta property="og:type" content="{esc(page_type)}">',
        f'<meta property="og:site_name" content="{esc(t("site.name", lang))}">',
        f'<meta property="og:title" content="{esc(full_title)}">',
        f'<meta property="og:description" content="{esc(description)}">',
        f'<meta property="og:url" content="{esc(here)}">',
        f'<meta property="og:locale" content="{locale}">',
        f'<meta property="og:image" content="{esc(OG_IMAGE)}">',
    ]
    if counterpart and has_mirror(fr_path):
        out.append(f'<meta property="og:locale:alternate" content="{other_locale}">')
    out += [
        '<meta name="twitter:card" content="summary_large_image">',
        f'<link rel="stylesheet" href="{FONTS_HREF}">',
        f'<link rel="stylesheet" href="{CSS_HREF}">',
    ]
    return "\n".join(out)


_BRAND_SVG = (
    '<svg viewBox="0 0 32 32" fill="none" aria-hidden="true" focusable="false">'
    '<circle cx="16" cy="16" r="11" stroke="currentColor" stroke-width="2.4"/>'
    '<circle cx="16" cy="16" r="4.2" fill="currentColor"/>'
    '<path d="M16 1.5v4M16 26.5v4" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg>'
)


def lang_link(lang: str, fr_path: str, *, counterpart: bool = True) -> str:
    """The language switch: the current language as plain text, the other as one
    real link ("English" on French pages, "Français" on English pages). No
    cookie, no redirect, no memory (docs/I18N.md section 2)."""
    if not (counterpart and has_mirror(fr_path)):
        return ""
    if lang == "fr":
        cur = f'<span class="seg-cur" aria-current="true" lang="fr">{esc(t("lang.cur", "fr"))}</span>'
        other = (f'<a class="seg-link" lang="en" hreflang="en-CA" href="{esc(path_for("en", fr_path))}">'
                 f'{esc(t("lang.other_name", "fr"))}</a>')
        return f'<div class="seg" role="group" aria-label="{esc(t("lang.group", "fr"))}">{cur}{other}</div>'
    cur = f'<span class="seg-cur" aria-current="true" lang="en">{esc(t("lang.cur", "en"))}</span>'
    other = (f'<a class="seg-link" lang="fr" hreflang="fr-CA" href="{esc(path_for("fr", fr_path))}">'
             f'{esc(t("lang.other_name", "en"))}</a>')
    return f'<div class="seg" role="group" aria-label="{esc(t("lang.group", "en"))}">{other}{cur}</div>'


def site_header(lang: str, fr_path: str, *, counterpart: bool = True, mine_href: str = "#chez-moi") -> str:
    """Skip link first, then the banner: brand, "Chez moi", language link."""
    home = path_for(lang, "/")
    return (
        f'<a class="skip" href="#main">{esc(t("skip", lang))}</a>\n'
        '<header class="top"><div class="wrap">'
        f'<a class="brand" href="{esc(home)}" aria-label="{esc(t("brand.home", lang))}">{_BRAND_SVG}'
        f'{esc(t("brand.name", lang))}<small>{esc(t("brand.sub", lang))}</small></a>'
        '<div class="spacer"></div>'
        f'<a class="btn" id="mine-link" href="{esc(mine_href)}">{esc(t("nav.mine", lang))}</a>'
        f'{lang_link(lang, fr_path, counterpart=counterpart)}'
        "</div></header>"
    )


def site_footer(lang: str) -> str:
    links = [
        ("/methode/", "foot.method", ""),
        ("/registre.html", "foot.registre", ""),
        ("/memoire.html", "foot.memoire", ""),
        ("/methode/legal.html", "foot.legal", ""),
        ("/methode/legal.html", "foot.takedown", "#retrait-et-contact"),
    ]
    items = "".join(internal_link(lang, p, t(k, lang), fragment=frag) for p, k, frag in links)
    return (
        '<footer class="site"><div class="wrap">'
        f'<nav class="links" aria-label="{esc(t("foot.nav", lang))}">{items}</nav>'
        f'<p>{esc(t("foot.text", lang))}</p>'
        "</div></footer>"
    )


def _js_island(lang: str) -> str:
    strings = {k[3:]: v for k, v in sorted(i18n.catalogue(lang).items()) if k.startswith("js.")}
    blob = json.dumps(strings, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    blob = blob.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return f'<script type="application/json" id="vigie-i18n">{blob}</script>'


def document(*, lang: str, fr_path: str, title: str, description: str, main: str,
             counterpart: bool = True, robots: str = "index, follow", mine_href: str = "#chez-moi",
             page_type: str = "website") -> str:
    """A complete page: head, skip link, banner, main, contentinfo, one script."""
    return (
        "<!doctype html>\n"
        f'<html lang="{"fr-CA" if lang == "fr" else "en-CA"}">\n<head>\n'
        f"{head_meta(lang=lang, fr_path=fr_path, title=title, description=description, counterpart=counterpart, robots=robots, page_type=page_type)}\n"
        "</head>\n<body>\n"
        f"{site_header(lang, fr_path, counterpart=counterpart, mine_href=mine_href)}\n"
        f'<main id="main" tabindex="-1"><div class="wrap">\n{main}\n</div></main>\n'
        f"{site_footer(lang)}\n"
        f"{_js_island(lang)}\n"
        f'<script src="{JS_SRC}" defer></script>\n'
        "</body>\n</html>\n"
    )


# --------------------------------------------------------------------------- #
# Chips (never colour alone: every chip prints its meaning)
# --------------------------------------------------------------------------- #
def chip(text: str, *classes: str, sr: str = "", title: str = "") -> str:
    ttl = f' title="{esc(title)}"' if title else ""
    extra = _sr(sr) if sr else ""
    return f'<span class="{esc(_cls("chip", *classes))}"{ttl}>{esc(text)}{extra}</span>'


def lang_chip(code: object, lang: str) -> str:
    """`FR` / `EN` with the full words for assistive technology."""
    code = str(code or "")
    if code not in ("fr", "en"):
        return ""
    return (f'<span class="chip lang"><span aria-hidden="true">{code.upper()}</span>'
            f'{_sr(t("lang.chip." + code, lang))}</span>')


def event_lang_chip(languages: object, lang: str) -> str:
    """`FR + EN`, a single language chip, or the English-only label."""
    langs = sorted({str(x) for x in (languages or []) if str(x) in ("fr", "en")})
    if langs == ["en", "fr"]:
        return (f'<span class="chip lang"><span aria-hidden="true">FR + EN</span>'
                f'{_sr(t("lang.chip.both", lang))}</span>')
    if langs == ["en"]:
        return chip(t("chip.en_only", lang), "lang", "en-only")
    if langs == ["fr"]:
        return lang_chip("fr", lang)
    return ""


def tier_chip(tier: object, lang: str) -> str:
    tier = str(tier or "")
    if tier not in TIERS:
        return ""
    return chip(t(f"tier.{tier}", lang), tier)


def activity_chip(activity: object, lang: str) -> str:
    activity = str(activity or "")
    if activity not in ACTIVITIES:
        return ""
    return chip(t(f"act.{activity}", lang), "act", f"act-{activity}")


def owner_chip(member: dict, lang: str) -> str:
    own = str(member.get("ownership_class") or "")
    if own not in OWNERSHIPS:
        return ""
    color = OWN_COLOR[own]
    return (f'<span class="chip own {color}"><span class="sw" aria-hidden="true"></span>'
            f'{esc(t("own." + own, lang))}</span>')


def origin_chip(member: dict, lang: str) -> str:
    origin = str(member.get("origin_class") or "unknown")
    if origin not in ORIGINS:
        origin = "unknown"
    return chip(t(f"origin.{origin}", lang), "origin", f"origin-{origin}")


# --------------------------------------------------------------------------- #
# Member helpers
# --------------------------------------------------------------------------- #
def _epoch(iso: object) -> float | None:
    dt = i18n.parse_instant(iso)
    return None if dt is None else dt.timestamp()


def _shown_instant(m: dict) -> str:
    """The declared publication time; only when absent, the collection clock
    (flagged in the voice card). Never guessed, never corrected."""
    return str(m.get("published_at") or m.get("first_seen") or "")


def _sorted_members(ev: dict) -> list[dict]:
    ms = [m for m in (ev.get("members") or []) if isinstance(m, dict)]

    def key(m: dict) -> tuple:
        e = _epoch(_shown_instant(m))
        return (0 if e is not None else 1, e or 0.0, str(m.get("item_id") or ""))

    return sorted(ms, key=key)


def _inst(m: dict, lang: str) -> str:
    return loc(m.get("institution_name"), lang) or str(m.get("institution") or "")


def _color(m: dict) -> str:
    return OWN_COLOR.get(str(m.get("ownership_class") or ""), "c-gov")


def _orgs(ms: list[dict]) -> int:
    return len({str(m.get("owner_group") or m.get("institution") or m.get("item_id") or "") for m in ms})


def _origin_groups(ev: dict, ms: list[dict]) -> list[list[dict]]:
    """Independence groups as member lists; falls back to owner groups."""
    by_id = {str(m.get("item_id")): m for m in ms}
    groups: list[list[dict]] = []
    seen: set[str] = set()
    raw = (ev.get("independence") or {}).get("groups") or []
    for g in raw:
        row = [by_id[str(i)] for i in g if str(i) in by_id and str(i) not in seen]
        for m in row:
            seen.add(str(m.get("item_id")))
        if row:
            groups.append(row)
    rest = [m for m in ms if str(m.get("item_id")) not in seen]
    if not raw:
        order: dict[str, list[dict]] = {}
        for m in rest:
            order.setdefault(str(m.get("owner_group") or m.get("institution") or ""), []).append(m)
        groups.extend(order.values())
    else:
        groups.extend([m] for m in rest)
    return groups


def _lead(ms: list[dict], lang: str) -> dict | None:
    """The headline shown on a card: the first published in the interface
    language, else the first published (docs: published rule)."""
    for m in ms:
        if m.get("language") == lang:
            return m
    return ms[0] if ms else None


def _span_seconds(ms: list[dict]) -> int | None:
    ts = [e for e in (_epoch(_shown_instant(m)) for m in ms) if e is not None]
    return int(max(ts) - min(ts)) if len(ts) > 1 else None


def _bold(text: str) -> str:
    return f"<b>{esc(text)}</b>"


# --------------------------------------------------------------------------- #
# Timelines (inline SVG: positions are attributes, never style)
# --------------------------------------------------------------------------- #
def mini_timeline(ev_or_members: object, lang: str) -> str:
    """The card's strip: first and last declared time around a dotted track."""
    ms = _sorted_members(ev_or_members) if isinstance(ev_or_members, dict) else list(ev_or_members)  # type: ignore[arg-type]
    ts = [(m, _epoch(_shown_instant(m))) for m in ms]
    ts = [(m, e) for m, e in ts if e is not None]
    if not ts:
        return ""
    a, b = ts[0][1], ts[-1][1]
    dots = []
    for i, (m, e) in enumerate(ts):
        x = 50.0 if a == b else 4 + ((e - a) / (b - a)) * 92
        if i == 0 and len(ts) > 1:
            dots.append(f'<circle class="dot-ring {_color(m)}" cx="{_pct(x)}" cy="10" r="8.5"/>')
        dots.append(f'<circle class="dot {_color(m)}" cx="{_pct(x)}" cy="10" r="6"/>')
    first_t = i18n.fmt_time(_shown_instant(ts[0][0]), lang)
    last_t = i18n.fmt_time(_shown_instant(ts[-1][0]), lang) if len(ts) > 1 else ""
    return (
        '<div class="mtl" aria-hidden="true">'
        f"<span>{esc(first_t)}</span>"
        '<svg class="mtl-track" width="100%" height="20" focusable="false">'
        '<line class="tl-line" x1="0" x2="100%" y1="10" y2="10"/>' + "".join(dots) + "</svg>"
        f"<span>{esc(last_t)}</span></div>"
    )


def _axis(ts: list[float]) -> tuple[float, float]:
    mn, mx = min(ts), max(ts)
    pad = max(45 * 60.0, (mx - mn) * 0.15)
    a = float(int((mn - pad) // 3600) * 3600)
    b = float(-int(-(mx + pad) // 3600) * 3600)
    if b - a < 3 * 3600:
        b = a + 3 * 3600
    return a, b


def _tick_label(epoch: float, lang: str) -> str:
    text = i18n.fmt_time(datetime.fromtimestamp(epoch, timezone.utc), lang)
    return re.sub(r"\s?(?:a\.m\.|p\.m\.)", "", text) if lang == "en" else text


def full_timeline(ev_or_members: object, lang: str, *, linked: bool = True) -> str:
    """Who said what, and when: one lane per institution, one SVG per lane.

    `linked=True` (current pages): each dot is an anchor to the voice card, so
    the jump works without JavaScript (the script only adds the smooth scroll
    and the flash). `linked=False` (permanent pages): plain dots, no targets.
    """
    ms = _sorted_members(ev_or_members) if isinstance(ev_or_members, dict) else list(ev_or_members)  # type: ignore[arg-type]
    pts = [(m, _epoch(_shown_instant(m))) for m in ms]
    pts = [(m, e) for m, e in pts if e is not None]
    if not pts:
        return ""
    a, b = _axis([e for _m, e in pts])

    def pos(e: float) -> float:
        return (e - a) / (b - a) * 100

    lanes: list[tuple[str, list[tuple[dict, float]]]] = []
    for m, e in pts:
        inst = str(m.get("institution") or _inst(m, lang))
        for key, row in lanes:
            if key == inst:
                row.append((m, e))
                break
        else:
            lanes.append((inst, [(m, e)]))
    multi = len(pts) > 1
    first_id = str(pts[0][0].get("item_id"))
    out = []
    for _inst_key, row in lanes:
        head = row[0][0]
        dots = []
        for m, e in row:
            x = pos(e)
            label = i18n.fmt_time(_shown_instant(m), lang)
            anchor = "start" if x < 6 else ("end" if x > 94 else "middle")
            ring = (f'<circle class="dot-ring {_color(m)}" cx="{_pct(x)}" cy="20" r="13"/>'
                    if (multi and str(m.get("item_id")) == first_id) else "")
            body = (f'<circle class="tl-hit" cx="{_pct(x)}" cy="20" r="22"/>{ring}'
                    f'<circle class="tl-dot {_color(m)}" cx="{_pct(x)}" cy="20" r="9"/>')
            name = f"{_inst(m, lang)}, {label}"
            if linked:
                dots.append(f'<a class="tl-a {_color(m)}" href="#{esc(dom_id("v", m.get("item_id")))}" '
                            f'data-jump="{esc(dom_id("v", m.get("item_id")))}" aria-label="{esc(name)}">{body}</a>')
            else:
                dots.append(f'<g class="{_color(m)}" role="img" aria-label="{esc(name)}">{body}</g>')
            dots.append(f'<text class="tl-t" x="{_pct(x)}" y="49" text-anchor="{anchor}">{esc(label)}</text>')
        out.append(
            '<div class="tl-lane">'
            f'<div class="tl-lbl">{esc(_inst(head, lang))}<span>{lang_chip(head.get("language"), lang)}</span></div>'
            '<svg class="tl-track" width="100%" height="56" focusable="false">'
            '<line class="tl-line" x1="0" x2="100%" y1="20" y2="20"/>' + "".join(dots) + "</svg></div>"
        )
    ticks = []
    step = 2 if (b - a) > 7 * 3600 else 1
    tm, i = a, 0
    while tm <= b:
        if i % step == 0:
            x = pos(tm)
            ticks.append(f'<line class="tl-tickline" x1="{_pct(x)}" x2="{_pct(x)}" y1="0" y2="5"/>'
                         f'<text class="tl-tick" x="{_pct(x)}" y="17" text-anchor="middle">{esc(_tick_label(tm, lang))}</text>')
        tm += 3600.0
        i += 1
    axis = ('<div class="tl-axis"><span></span><svg class="tl-ticks" width="100%" height="22" aria-hidden="true" focusable="false">'
            '<line class="tl-axisline" x1="0" x2="100%" y1="0" y2="0"/>' + "".join(ticks) + "</svg></div>")
    return f'<div class="tl" role="group" aria-label="{esc(t("tl.h", lang))}">{"".join(out)}{axis}</div>'


# --------------------------------------------------------------------------- #
# Event card (front door)
# --------------------------------------------------------------------------- #
def _excerpt(m: dict) -> str:
    text = " ".join(str(m.get("excerpt") or "").split())
    return text if len(text) <= EXCERPT_MAX else text[:EXCERPT_MAX].rstrip() + "…"


def _colon(lang: str) -> str:
    return t("colon", lang)


def _why_rows(ev: dict, ms: list[dict], lead: dict | None, lang: str) -> list[tuple[str, str]]:
    n, orgs = len(ms), _orgs(ms)
    first_t = i18n.fmt_time(_shown_instant(ms[0]), lang) if ms else ""
    last_t = i18n.fmt_time(_shown_instant(ms[-1]), lang) if ms else ""
    if first_t == last_t:
        fresh = t("why.fresh.single", lang, a=first_t)
    else:
        fresh = t("why.fresh.range", lang, a=first_t, b=last_t)
    if ms:
        fresh += " · " + i18n.fmt_day(_shown_instant(ms[0]), lang)
    if n > 1:
        voices = t("why.voices.many", lang, articles=tn("n.articles", n, lang), orgs=tn("n.orgs", orgs, lang))
    else:
        voices = t("why.voices.single", lang)
    rows = [
        (t("why.place", lang), loc(ev.get("place_label"), lang)),
        (t("why.fresh", lang), fresh),
        (t("why.voices", lang), voices),
        (t("why.label", lang), t("why.label.text", lang, k=loc(ev.get("type_label"), lang))),
    ]
    if n > 1 and lead is not None:
        rows.append((t("why.lead", lang), t("why.lead.first", lang) if lead is ms[0] else t("why.lead.lang", lang)))
    return [(k, v) for k, v in rows if v]


def event_card(ev: dict, lang: str) -> str:
    """One event on the front door: the attributed headline of the lead voice,
    its time strip, the counts, and a "why is this here?" disclosure. A
    permanent view shows the Vigie label instead of any publisher text."""
    ms = _sorted_members(ev)
    eid = ev.get("event_id")
    perm = bool(ev.get("permanent"))
    href = path_for(lang, event_path(eid))
    lead = None if perm else _lead(ms, lang)
    n, orgs = len(ms), _orgs(ms)
    kind = loc(ev.get("type_label"), lang)
    top = "".join(x for x in (
        chip(kind, "kind") if kind else "", activity_chip(ev.get("activity"), lang), tier_chip(ev.get("tier"), lang),
    ) if x)
    label = loc(ev.get("label"), lang)
    if lead is not None and lead.get("title"):
        code = esc(lead.get("language") if lead.get("language") in ("fr", "en") else lang)
        head = f'<h2 class="ev-h"><a href="{esc(href)}" lang="{code}" data-hl>{esc(lead.get("title"))}</a></h2>'
        by = [f"<b>{esc(_inst(lead, lang))}</b>", esc(i18n.fmt_time(_shown_instant(lead), lang)),
              esc(i18n.fmt_day(_shown_instant(lead), lang))]
        if n > 1 and lead is ms[0]:
            by.append(f'<span title="{esc(t("first.tip", lang))}">{esc(t("first", lang).lower())}</span>')
        if lead.get("author"):
            by.append(f'{esc(t("by", lang))} <span lang="{code}">{esc(lead.get("author"))}</span>')
        byline = '<p class="ev-by">' + " · ".join(by) + "</p>"
        summary = (f'<p class="ev-sum" lang="{code}" data-hl>{esc(_excerpt(lead))}</p>' if lead.get("excerpt") else "")
    else:
        head = f'<h2 class="ev-h"><a href="{esc(href)}">{esc(label)}</a></h2>'
        byline = summary = ""
    langs = event_lang_chip(ev.get("languages") or sorted({m.get("language") for m in ms}), lang)
    facts = f'<b>{esc(tn("n.voices", n, lang))}</b> · {esc(tn("n.orgs", orgs, lang))} · {langs}'
    primary = " primary" if n > 1 else ""
    action = t("compare", lang, n=i18n.fmt_int(n, lang)) if n > 1 else t("see", lang)
    why = "".join(f"<li><b>{esc(k)}{esc(_colon(lang))}</b>{esc(v)}</li>" for k, v in _why_rows(ev, ms, lead, lang))
    return (
        f'<article class="card ev" id="{esc(dom_id("c", eid))}" data-mine-card>'
        f'<div class="ev-top">{top}</div>{head}{byline}{summary}{mini_timeline(ms, lang)}'
        f'<div class="ev-meta"><div class="facts">{facts}</div>'
        f'<a class="btn{primary}" href="{esc(href)}">{esc(action)}{_sr(" (" + label + ")")}</a></div>'
        f'<details class="why"><summary>{esc(t("why", lang))}</summary><ul>{why}</ul></details>'
        "</article>"
    )


# --------------------------------------------------------------------------- #
# Event page: voices and panels
# --------------------------------------------------------------------------- #
def _panel(title: str, body: str, *, hid: str = "", cls: str = "") -> str:
    if not body:
        return ""
    ident = f' id="{esc(hid)}"' if hid else ""
    label = f' aria-labelledby="{esc(hid)}"' if hid else ""
    extra = f" {esc(cls)}" if cls else ""
    return (f'<section class="card panel{extra}"{label}>'
            f'<h2 class="sect-title"{ident}>{esc(title)}</h2>{body}</section>')


def _link(url: object, text: str, *, lang_code: str = "") -> str:
    safe = safe_url(url)
    if not safe:
        return ""
    lc = f' lang="{esc(lang_code)}"' if lang_code in ("fr", "en") else ""
    return f'<a href="{esc(safe)}"{lc} target="_blank" rel="noopener noreferrer">{esc(text)}</a>'


def _lang_code(m: dict, lang: str) -> str:
    code = str(m.get("language") or "")
    return code if code in ("fr", "en") else lang


def voice_card(m: dict, lang: str, *, first: bool = False, multi: bool = False) -> str:
    """One voice, as published: verbatim headline and excerpt in their own
    language, the author and the photo credit (R1/R2), a link to the original."""
    code = _lang_code(m, lang)
    inst = _inst(m, lang)
    shown = _shown_instant(m)
    header = [
        f'<span class="v-time">{esc(i18n.fmt_time(shown, lang))}</span>',
        f'<span class="v-org">{esc(inst)}</span>',
        owner_chip(m, lang), lang_chip(code, lang), origin_chip(m, lang),
    ]
    if first and multi:
        header.append(f'<span class="chip first" title="{esc(t("first.tip", lang))}">{esc(t("first", lang))}</span>')
    if not m.get("published_at"):
        header.append(chip(t("voice.collected_time", lang), "note"))
    if m.get("date_suspect"):
        header.append(chip(t("voice.date_suspect", lang), "warn"))
    bits = []
    if m.get("author"):
        bits.append(f'{esc(t("by", lang))} <span lang="{code}">{esc(m.get("author"))}</span>')
    if m.get("photo_credit"):
        bits.append(f'{esc(t("photo", lang))}{esc(_colon(lang))}<span lang="{code}">{esc(m.get("photo_credit"))}</span>')
    credit = f'<span>{" · ".join(bits)}</span>' if bits else ""
    read = _link(m.get("url"), t("read", lang, o=inst))
    title = f'<h3 lang="{code}" data-hl>{esc(m.get("title"))}</h3>' if m.get("title") else ""
    excerpt = f'<p lang="{code}" data-hl>{esc(_excerpt(m))}</p>' if m.get("excerpt") else ""
    return (
        f'<article class="card voice {_color(m)}" id="{esc(dom_id("v", m.get("item_id")))}" tabindex="-1">'
        f'<header>{"".join(header)}</header>{title}{excerpt}'
        f'<footer>{credit}<span>{esc(i18n.fmt_day(shown, lang))}</span>{read}</footer></article>'
    )


def coverage_list(ev: dict, lang: str) -> str:
    """Permanent pages: who covered it and when, with links, and no text."""
    rows = []
    for m in _sorted_members(ev):
        shown = _shown_instant(m)
        link = _link(m.get("url"), t("read", lang, o=_inst(m, lang)))
        rows.append(
            f'<li class="cover-row {_color(m)}"><span class="v-time">{esc(i18n.fmt_time(shown, lang))}</span>'
            f'<span class="v-org">{esc(_inst(m, lang))}</span>{owner_chip(m, lang)}{lang_chip(m.get("language"), lang)}'
            f'{origin_chip(m, lang)}<span class="small muted">{esc(i18n.fmt_day(shown, lang))}</span>{link}</li>'
        )
    return f'<ol class="cover">{"".join(rows)}</ol>' if rows else ""


_FOLD_PAIRS = (("œ", "oe"), ("Œ", "oe"), ("æ", "ae"), ("Æ", "ae"),
               ("’", "'"), ("‘", "'"), ("`", "'"), ("´", "'"))


def fold(text: object) -> str:
    """Lower-case, ligatures and typographic apostrophes folded, diacritics
    removed: the same key evenements.js builds for matching."""
    s = str(text or "")
    for a, b in _FOLD_PAIRS:
        s = s.replace(a, b)
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c)).lower()


def _word_present(forms: list[str], folded_text: str) -> bool:
    for form in forms:
        f = fold(form)
        if not f:
            continue
        start = 0
        while True:
            i = folded_text.find(f, start)
            if i < 0:
                break
            before = folded_text[i - 1] if i > 0 else " "
            after = folded_text[i + len(f)] if i + len(f) < len(folded_text) else " "
            if not before.isalnum() and not after.isalnum():
                return True
            start = i + 1
    return False


def words_panel(ev: dict, lang: str) -> str:
    """Shared words as chips with one pip per voice (present or absent). The
    server prints everything; the script only upgrades each chip to a toggle
    that highlights the word in the voices."""
    if ev.get("permanent"):
        return ""
    ms = _sorted_members(ev)
    items = []
    for w in ev.get("words") or []:
        forms = [str(f) for f in (w.get("forms") or [w.get("label")]) if f]
        present = [_word_present(forms, fold(f'{m.get("title") or ""} {m.get("excerpt") or ""}')) for m in ms]
        n = sum(present)
        if not n:
            continue
        pips = "".join(f'<span class="pip {_color(m)}{"" if ok else " off"}"></span>' for m, ok in zip(ms, present))
        items.append(
            f'<li><span class="word" data-forms="{esc("|".join(forms))}" '
            f'title="{esc(t("words.in", lang, n=n, m=len(ms)))}">'
            f'<span class="pips" aria-hidden="true">{pips}</span>{esc(w.get("label") or forms[0])}'
            f'<i>{n}/{len(ms)}</i></span></li>'
        )
    if not items:
        return ""
    body = f'<p class="small muted m0 mb-s">{esc(t("words.p", lang))}</p><ul class="words">{"".join(items)}</ul>'
    return _panel(t("words.h", lang), body, hid="wp-h", cls="words-panel")


def _group_label(group: list[dict], lang: str) -> tuple[str, str]:
    head = group[0]
    owners = {str(m.get("owner_group") or "") for m in group}
    insts = sorted({_inst(m, lang) for m in group})
    if len(owners) == 1 and head.get("owner_name"):
        name = loc(head.get("owner_name"), lang)
    elif len(insts) == 1:
        name = insts[0]
    else:
        name = " · ".join(insts)
    own = str(head.get("ownership_class") or "")
    return name, (t(f"own.{own}", lang) if own in OWNERSHIPS else "")


def origins_panel(ev: dict, lang: str) -> str:
    """n articles -> n origins, one row per independence group, and the
    same-owner note when a language pair is one owner's two desks."""
    ms = _sorted_members(ev)
    if not ms:
        return ""
    groups = _origin_groups(ev, ms)
    n_origins = _int((ev.get("independence") or {}).get("count"), len(groups)) or len(groups)
    rows = []
    for g in groups:
        name, own = _group_label(g, lang)
        own_html = f"<span>{esc(own)}</span>" if own else ""
        rows.append(f'<li><span class="n">{len(g)}</span><div><b>{esc(name)}</b>{own_html}</div></li>')
    note = ""
    by_id = {str(m.get("item_id")): m for m in ms}
    for pair in ev.get("language_pairs") or []:
        if pair.get("same_owner") and str(pair.get("fr")) in by_id and str(pair.get("en")) in by_id:
            a, b = by_id[str(pair["fr"])], by_id[str(pair["en"])]
            group = loc(a.get("owner_name"), lang) or str(a.get("owner_group") or "")
            text = t("orig.same", lang, a=_inst(a, lang), b=_inst(b, lang), g=group)
            note = f'<p class="fact warn mt-m">{esc(text)}</p>'
            break
    body = (f'<p class="orig-sum">{esc(tn("n.articles", len(ms), lang))} → {esc(tn("n.origins", n_origins, lang))}</p>'
            f'<ul class="orig">{"".join(rows)}</ul>{note}<p class="legend">{esc(t("orig.note", lang))}</p>')
    return _panel(t("orig.h", lang), body, hid="op-h")


def _fact_value(slot: dict, value: object, lang: str) -> str:
    kind, unit = str(slot.get("kind") or ""), str(slot.get("unit") or "")
    if kind == "count":
        return i18n.fmt_int(value, lang)
    if kind == "amount":
        return i18n.fmt_percent_bp(value, lang) if unit == "percent_bp" else i18n.fmt_money(value, lang)
    if kind == "date":
        return i18n.fmt_ymd(value, lang, short=False) or str(value)
    return str(value)


def numbers_panel(ev: dict, lang: str) -> str:
    """Figures stated in the coverage, side by side with who stated them. A
    divergence is shown, never reconciled and never called a disagreement.
    Derived from publisher text, so never on a permanent page."""
    if ev.get("permanent") or not ev.get("facts"):
        return ""
    names = {str(m.get("institution")): _inst(m, lang) for m in _sorted_members(ev)}
    rows = []
    for fact in ev.get("facts") or []:
        slot = fact.get("slot") or {}
        unit, kind = str(slot.get("unit") or ""), str(slot.get("kind") or "")
        if _has(f"unit.{unit}", lang):
            label = t(f"unit.{unit}", lang)
        elif _has(f"kind.{kind}", lang):
            label = t(f"kind.{kind}", lang)
        else:
            label = unit
        vals = []
        for v in fact.get("values") or []:
            who = ", ".join(names.get(str(i), str(i)) for i in v.get("institutions") or [])
            who_html = f'<span class="who">{esc(who)}</span>' if who else ""
            vals.append(f'<span class="val"><code>{esc(_fact_value(slot, v.get("value"), lang))}</code>{who_html}</span>')
        flag = chip(t("nums.divergent", lang), "note") if fact.get("divergent") else ""
        rows.append(f'<div><b>{esc(label)}</b><span class="v">{"".join(vals)}{flag}</span></div>')
    body = f'<p class="small muted m0">{esc(t("nums.p", lang))}</p><div class="nums">{"".join(rows)}</div>'
    return _panel(t("nums.h", lang), body, hid="np-h")


def why_panel(ev: dict, lang: str) -> str:
    """Why these articles are one event: the rule, the shared anchors, the time
    gap, the organisations, the grouping level. Every grouping explains itself."""
    ms = _sorted_members(ev)
    if len(ms) < 2:
        return _panel(t("grp.h", lang), f'<p class="m0">{esc(t("why.single", lang))}</p>', hid="gp-h")
    why = ev.get("why") or {}
    perm = bool(ev.get("permanent"))
    rows: list[tuple[str, str]] = []
    shared = why.get("shared") or []
    if shared and not perm:
        parts = []
        for s in shared:
            label = loc(s.get("label") if isinstance(s, dict) else s, lang)
            alias = f" ({t('alias', lang)})" if isinstance(s, dict) and s.get("alias") else ""
            parts.append(esc(label + alias))
        rows.append((t("why.shared", lang), esc(t("sep", lang)).join(parts)))
    gap = why.get("gap_seconds")
    if gap is None:
        gap = _span_seconds(ms)
    if gap is not None:
        text = t("why.window.v", lang, d=i18n.fmt_duration(gap, lang), h=_int(why.get("window_hours"), 72))
        rows.append((t("why.window", lang), esc(text)))
    rows.append((t("why.orgs", lang), esc(t("why.orgs.v", lang, n=_orgs(ms), m=_int(why.get("min_institutions"), 2)))))
    anchor = loc(why.get("place_anchor"), lang)
    if anchor and not perm:
        rows.append((t("why.anchor", lang), esc(t("why.anchor.v", lang, place=anchor))))
    if why.get("rule"):
        rows.append((t("why.rule", lang), f'<code>{esc(why.get("rule"))}</code>'))
    tier = str(ev.get("tier") or "")
    if tier in TIERS:
        rows.append((t("why.level", lang), tier_chip(tier, lang)))
    dl = "".join(f"<dt>{esc(k)}</dt><dd>{v}</dd>" for k, v in rows)
    level = ""
    if tier in TIERS:
        warn = " warn" if tier != "certain" else ""
        level = f'<p class="fact{warn} mt-m">{esc(t(f"why.level.{tier}", lang))}</p>'
    return _panel(t("grp.h", lang), f'<dl class="why-dl">{dl}</dl>{level}', hid="gp-h")


def silence_panel(ev: dict, lang: str) -> str:
    """Institutions followed with no linked article in this collection: a
    measured fact of our feeds, never an accusation, never a proof of silence."""
    rows = []
    for s in ev.get("silence") or []:
        state = str(s.get("state") or "no_linked_item")
        state = state if state in SILENCE_STATES else "no_linked_item"
        rows.append(f'<li><b>{esc(loc(s.get("institution_name"), lang))}</b><span>{esc(t("sil." + state, lang))}</span></li>')
    if not rows:
        return ""
    body = f'<p class="small muted m0 mb-xs">{esc(t("sil.p", lang))}</p><ul class="sil">{"".join(rows)}</ul>'
    return _panel(t("sil.h", lang), body, hid="sl-h")


def official_link_panel(ev: dict, lang: str) -> str:
    """Official-record anchors, attached by a named rule. A link to an
    official record is never a confirmation; none attached is a measured
    absence."""
    perm = bool(ev.get("permanent"))
    rows = []
    anchors = [x for x in (ev.get("anchors") or []) if isinstance(x, dict)]
    for a in sorted(anchors, key=lambda x: (str(x.get("type")), str(x.get("ref")))):
        typ = str(a.get("type") or "")
        if typ not in ANCHOR_TYPES:
            continue
        meta = [chip(t(f"anchor.{typ}", lang))]
        if a.get("institution_name"):
            meta.append(esc(loc(a.get("institution_name"), lang)))
        if a.get("published_at"):
            meta.append(esc(i18n.fmt_day(a.get("published_at"), lang)))
        ref = str(a.get("ref") or "")
        target = ""
        if typ == "edition_seal" and ref.isdigit():
            seal_path = f"/memoire/{int(ref)}.html"
            target = (f'<a href="{esc(path_for(lang, seal_path))}"{link_lang_attrs(lang, seal_path)}>'
                      f'{esc(t("anchor.seal_link", lang, seq=int(ref)))}</a>')
        elif a.get("url") and a.get("title") and not perm:
            target = _link(a.get("url"), str(a.get("title")), lang_code=str(a.get("language") or "fr"))
        elif a.get("url"):
            target = _link(a.get("url"), t("read", lang, o=loc(a.get("institution_name"), lang) or ref))
        ref_html = f" · <code>{esc(ref)}</code>" if ref and typ != "edition_seal" else ""
        rows.append(f'<li><span class="m">{" ".join(meta)}</span>{target}'
                    f'<span class="small muted">{esc(t("anchor.rule", lang))}{esc(_colon(lang))}<code>{esc(a.get("rule"))}</code>{ref_html}</span></li>')
    if rows:
        body = f'<ul class="off-list">{"".join(rows)}</ul><p class="legend">{esc(t("offlink.note", lang))}</p>'
    else:
        body = f'<p class="m0">{esc(t("offlink.none", lang))}</p>'
    return _panel(t("offlink.h", lang), body, hid="ol-h")


def neighbours_panel(ev: dict, lang: str) -> str:
    """Close by a word or a thread but judged different, and not grouped. Their
    titles are publisher text: never on a permanent page."""
    if ev.get("permanent"):
        return ""
    items = []
    for n in ev.get("neighbours") or []:
        m = n.get("member") or {}
        if not m.get("title"):
            continue
        code = _lang_code(m, lang)
        reason = str(n.get("reason") or "")
        why = loc(n.get("why"), lang) or (t(f"neigh.reason.{reason}", lang) if reason in NEIGHBOUR_REASONS else "")
        shown = _shown_instant(m)
        read = _link(m.get("url"), t("read", lang, o=_inst(m, lang)))
        why_html = f'<p class="mt-xs">{esc(why)}</p>' if why else ""
        items.append(
            f'<li>{tier_chip("possible", lang)}<span class="t" lang="{esc(code)}">{esc(m.get("title"))}</span>'
            f'<span class="w">{esc(_inst(m, lang))} · {esc(i18n.fmt_time(shown, lang))}, {esc(i18n.fmt_day(shown, lang))}</span>'
            f'{why_html}{read}</li>'
        )
    if not items:
        return ""
    body = f'<p class="small muted m0 mb-xs">{esc(t("neigh.p", lang))}</p><ul class="neigh">{"".join(items)}</ul>'
    return _panel(t("neigh.h", lang), body, hid="nb-h")


def ledger_panel(ev: dict, lang: str) -> str:
    """Headline history. Current pages list each change with both headlines;
    permanent pages keep only how many changes were observed."""
    changes = [c for c in ((ev.get("ledger") or {}).get("changes") or []) if isinstance(c, dict)]
    if not changes:
        body = f'<p class="small m0">{esc(t("ledger.none", lang))}</p>'
    elif ev.get("permanent"):
        body = f'<p class="small m0">{esc(tn("ledger.count", len(changes), lang))}</p>'
    else:
        rows = []
        for c in changes:
            code = _lang_code(c, lang)
            rows.append(
                f'<li><span class="small muted">{esc(loc(c.get("institution_name"), lang))} · '
                f'{esc(i18n.fmt_time(c.get("at"), lang))}, {esc(i18n.fmt_day(c.get("at"), lang))}</span>'
                f'<span lang="{esc(code)}" class="gone">{esc(c.get("before"))}</span>'
                f'<span lang="{esc(code)}">{esc(c.get("after"))}</span></li>')
        body = f'<ul class="ledger">{"".join(rows)}</ul>'
    return _panel(t("ledger.h", lang), body, hid="lg-h")


def archive_panel(ev: dict, lang: str) -> str:
    """What this page keeps after the edition, and what disappears."""
    ms = _sorted_members(ev)
    perm = bool(ev.get("permanent"))
    label = loc(ev.get("label"), lang)
    parts = [
        f'{esc(t("k.label", lang))}{esc(_colon(lang))}« {esc(label)} »',
        f'{esc(t("k.counts", lang))} ({esc(tn("n.voices", len(ms), lang))}, {esc(tn("n.orgs", _orgs(ms), lang))})',
    ]
    if ms:
        first_t = i18n.fmt_time(_shown_instant(ms[0]), lang)
        last_t = i18n.fmt_time(_shown_instant(ms[-1]), lang)
        span = f" → {esc(last_t)}" if len(ms) > 1 else ""
        parts.append(f'{esc(t("k.times", lang))} ({esc(first_t)}{span})')
    parts.append(esc(t("k.links", lang)))
    if str(ev.get("tier") or "") in TIERS:
        parts.append(esc(t("k.tier", lang)))
    seals = sorted({_int(s) for s in (ev.get("seals") or []) if _int(s) > 0})
    seal_line = ""
    if seals:
        nums = []
        for s in seals:
            sp = f"/memoire/{s}.html"
            nums.append(f'<a href="{esc(path_for(lang, sp))}"{link_lang_attrs(lang, sp)}>{s}</a>')
        seal_line = f'<p class="seal mt-s">{esc(t("seal.ev", lang))} {", ".join(nums)}</p>'
    window = ""
    if ev.get("window_state") == "out_of_window":
        window = f'<p class="small mt-s">{esc(t("window.out", lang))}</p>'
    opened = " open" if perm else ""
    keep = " · ".join(parts)
    body = (f'<details class="disc"{opened}><summary>{esc(t("arch.h.perm" if perm else "arch.h", lang))}</summary>'
            f'<p class="small muted mt-xs">{esc(t("arch.p", lang))}</p>'
            f'<table class="keep"><tbody><tr><th scope="row">{esc(t("k.stay", lang))}</th><td>{keep}</td></tr>'
            f'<tr><th scope="row">{esc(t("k.gone", lang))}</th><td><span class="gone">{esc(t("k.titles", lang))}</span></td></tr></tbody></table>'
            f'{window}{seal_line}</details>')
    return f'<section class="card panel">{body}</section>'


def _crumb(ev: dict, lang: str, edition: dict | None) -> str:
    clock = (edition or {}).get("clock") or ev.get("last_edition")
    if ev.get("permanent") or not clock:
        text = t("crumb.index", lang)
    else:
        text = t("crumb.back", lang, d=i18n.fmt_date(clock, lang, weekday=True))
    return internal_link(lang, "/evenements.html", text, cls="crumb")


def event_page(ev: dict, lang: str, *, edition: dict | None = None, followed: int | None = None,
               robots: str = "index, follow") -> str:
    """A complete event page. `ev["permanent"]` selects the permanent variant:
    no publisher text, links and counts only."""
    ms = _sorted_members(ev)
    perm = bool(ev.get("permanent"))
    eid = ev.get("event_id")
    n, orgs = len(ms), _orgs(ms)
    span = _span_seconds(ms)
    langs = sorted({str(m.get("language")) for m in ms if m.get("language") in ("fr", "en")})
    label = loc(ev.get("label"), lang)
    facts = [_bold(tn("n.articles", n, lang)), _bold(tn("n.orgs", orgs, lang))]
    if langs:
        facts.append(_bold(" + ".join(x.upper() for x in sorted(langs, reverse=True))))
    line = " · ".join(facts)
    if span:
        line += " · " + esc(t("ep.span", lang, d="")).replace("", _bold(i18n.fmt_duration(span, lang)))
    place = loc(ev.get("place_label"), lang)
    chips = "".join(x for x in (
        chip(loc(ev.get("type_label"), lang), "kind") if ev.get("type_label") else "",
        activity_chip(ev.get("activity"), lang), tier_chip(ev.get("tier"), lang),
        chip(place) if place else "", event_lang_chip(langs, lang) if langs == ["en"] else "",
    ) if x)
    if any(p.get("same_owner") for p in (ev.get("language_pairs") or []) if isinstance(p, dict)):
        chips += chip(t("chip.same_owner", lang), "same-owner")
    head = (f'<header class="evp-head"><div class="ev-top">{chips}</div>'
            f'<h1 id="h1" tabindex="-1">{esc(label)}</h1><p class="facts-line">{line}</p></header>')
    legend = esc(t("tl.legend.static" if perm else "tl.legend", lang))
    strip = full_timeline(ms, lang, linked=not perm)
    timeline = (_panel(t("tl.h", lang), strip + f'<p class="legend">{legend}</p>', hid="tl-h", cls="tl-card") if strip else "")
    if perm:
        left = (f'<h2 class="sect-title">{esc(t("cover.h", lang))}</h2>'
                f'<p class="fact mb-m">{esc(t("cover.note", lang))}</p>{coverage_list(ev, lang)}')
    else:
        notices = ""
        if len({m.get("language") for m in ms}) > 1:
            notices += f'<p class="fact mb-m">{esc(t("voices.notranslate", lang))}</p>'
        if n == 1:
            single = t("single.text", lang, n=followed) if followed else t("single.text.plain", lang)
            notices += f'<p class="fact mb-m"><b>{esc(t("single", lang))}.</b> {esc(single)}</p>'
        cards = "".join(voice_card(m, lang, first=(i == 0), multi=n > 1) for i, m in enumerate(ms))
        left = f'<h2 class="sect-title">{esc(t("voices.h", lang))}</h2>{notices}<div class="voices" id="voices">{cards}</div>'
    stack = "".join((neighbours_panel(ev, lang), ledger_panel(ev, lang), archive_panel(ev, lang)))
    aside = "".join((words_panel(ev, lang), origins_panel(ev, lang), numbers_panel(ev, lang), why_panel(ev, lang),
                     silence_panel(ev, lang), official_link_panel(ev, lang)))
    main = (f'{_crumb(ev, lang, edition)}{head}{timeline}'
            f'<div class="grid"><div>{left}<div class="stack mt-s">{stack}</div></div>'
            f'<aside class="aside stack" aria-label="{esc(t("aside.event", lang))}">{aside}</aside></div>')
    if perm:
        desc = t("meta.desc.event.perm", lang, label=label)
    else:
        desc = t("meta.desc.event", lang, label=label, articles=tn("n.articles", n, lang), orgs=tn("n.orgs", orgs, lang))
    return document(lang=lang, fr_path=event_path(eid), title=label, description=desc, main=main,
                    robots=robots, mine_href=path_for(lang, "/evenements.html") + "#chez-moi", page_type="article")


# --------------------------------------------------------------------------- #
# Edition blocks (front door)
# --------------------------------------------------------------------------- #
def mine_panel(lang: str, suggestions: list[str] | None = None) -> str:
    """"Chez moi": street or neighbourhood matching, on this device only.

    The panel and its explanation are always in the page; the form is hidden
    until the script runs (`js-only`), and a <noscript> line says what the
    search needs. Nothing is sent anywhere: the script reads the page it
    belongs to and keeps the typed names, optionally, in this browser's
    localStorage."""
    sugg = "".join(
        f'<li><button type="button" data-act="add" data-v="{esc(s)}">+ {esc(s)}</button></li>'
        for s in (suggestions or []) if str(s).strip()
    )
    return (
        f'<section class="card panel mine" id="chez-moi" aria-labelledby="mine-h" data-mine>'
        f'<h2 class="h-panel" id="mine-h">{esc(t("mine.h", lang))}</h2>'
        f'<p class="small muted">{esc(t("mine.p", lang))}</p>'
        f'<noscript><p class="small">{esc(t("mine.nojs", lang))}</p></noscript>'
        '<div class="js-only" hidden>'
        '<form class="mine-input" data-act="form" action="#chez-moi">'
        f'<input id="mine-in" type="text" autocomplete="off" aria-label="{esc(t("mine.lbl", lang))}" placeholder="{esc(t("mine.lbl", lang))}">'
        f'<button class="btn primary" type="submit">{esc(t("mine.add", lang))}</button></form>'
        '<div class="chips-mine" data-mine-chips></div>'
        f'<ul class="sugg" data-mine-sugg>{sugg}</ul>'
        '<p class="small" data-mine-status role="status" aria-live="polite"></p>'
        '<ul class="hits" data-mine-hits></ul></div></section>'
    )


def _rw_label(kind: str, code: object, lang: str) -> str:
    code = str(code or "")
    key = f"{kind}.{code}"
    return t(key, lang) if (code and _has(key, lang)) else ""


def _rw_dates(row: dict, lang: str) -> str:
    start, end = i18n.fmt_ymd(row.get("from"), lang), i18n.fmt_ymd(row.get("to"), lang)
    if start and end:
        text = t("roads.on", lang, a=start) if row.get("from") == row.get("to") else t("roads.from_to", lang, a=start, b=end)
    elif start:
        text = t("roads.since", lang, a=start)
    elif end:
        text = t("roads.until", lang, a=end)
    else:
        return ""
    if row.get("estimated"):
        text += " " + t("roads.estimated", lang)
    return text


def roadworks_block(rw: dict | None, lang: str) -> str:
    """"Before you leave": the City's declared obstructions, as published.

    Its own view model (see the module docstring): the official lane is
    refreshed hourly and can be newer than the edition. The collection time,
    the counts and the age note are ALWAYS printed, including when the lane
    delivered nothing; the age note is a fixed sentence (the kit has no
    clock), and the script adds the relative age on the reader's device."""
    rw = rw if isinstance(rw, dict) else {}
    rows = [r for r in (rw.get("rows") or []) if isinstance(r, dict)]
    inst = loc(rw.get("institution_name"), lang) or t("roads.inst_default", lang)
    collected = str(rw.get("collected_at") or "")
    parsed = i18n.parse_instant(collected)
    if parsed is not None:
        when = esc(t("roads.collected", lang, d=i18n.fmt_date(collected, lang, short=True), tm=i18n.fmt_time(collected, lang)))
        iso = esc(parsed.strftime("%Y-%m-%dT%H:%M:%SZ"))
        time_html = f'<time class="age-time" datetime="{iso}" data-age>{when}</time><span class="age-rel" data-age-rel></span>'
    else:
        time_html = esc(t("roads.time_unknown", lang))
    total = _int(rw.get("total")) if rw.get("total") is not None else None
    closed = _int(rw.get("closed")) if rw.get("closed") is not None else None
    if total is None:
        counts = esc(t("roads.counts_unknown", lang))
    elif closed is None:
        counts = esc(tn("roads.total_only", total, lang))
    else:
        counts = esc(tn("roads.total", total, lang, c=i18n.fmt_int(closed, lang)))
    if total is not None and rw.get("planned") is not None:
        counts += " " + esc(tn("roads.planned_n", _int(rw.get("planned")), lang))
    stale = ""
    if rw.get("stale"):
        stale = f'<p class="fact warn mt-s" data-rw-stale>{esc(t("roads.stale", lang))}</p>'
    items = []
    for r in rows:
        status = str(r.get("status") or "")
        impact = str(r.get("impact") or "")
        name = r.get("street") or r.get("places") or ""
        place_line = ""
        if r.get("places") and r.get("street") and r.get("places") != r.get("street"):
            place_line = str(r.get("places"))
        detail = str(r.get("text") or "")
        what = _rw_label("type", r.get("what"), lang) or loc(r.get("what"), lang)
        bt = " · ".join(x for x in (place_line, what) if x)
        closed_chip = ""
        impact_text = _rw_label("impact", impact, lang)
        if impact_text:
            closed_chip = chip(impact_text, "closed") if impact == "all-lanes-closed" else chip(impact_text, "impact")
        status_text = _rw_label("status", status, lang)
        status_chip = chip(status_text) if status_text else ""
        direction = _rw_label("dir", r.get("direction"), lang) or loc(r.get("direction"), lang)
        dates = _rw_dates(r, lang)
        meta = " · ".join(x for x in (direction, dates) if x)
        detail_html = f'<span class="bt" lang="fr">{esc(detail)}</span>' if detail else ""
        bt_html = f'<span class="bt">{esc(bt)}</span>' if bt else ""
        meta_html = f'<span class="small muted">{esc(meta)}</span>' if meta else ""
        items.append(
            f'<li data-rw><span class="st">{esc(name)}</span>{bt_html}{detail_html}'
            f'<span class="row">{closed_chip}{status_chip}{meta_html}</span></li>'
        )
    if items:
        shown = tn("roads.shown", len(items), lang)
        listing = f'<ul class="rw" data-rw-list>{"".join(items)}</ul><p class="small muted mt-s">{esc(shown)}</p>'
    elif parsed is None:
        listing = f'<p class="small m0">{esc(t("roads.nodata", lang))}</p>'
    else:
        listing = f'<p class="small m0">{esc(t("roads.none", lang))}</p>'
    links = [internal_link(lang, "/partir.html", t("roads.all", lang), cls="btn")]
    map_url = safe_url(rw.get("map_url") or RW_MAP_URL)
    if map_url:
        links.append(f'<a class="btn" href="{esc(map_url)}" target="_blank" rel="noopener noreferrer" lang="fr">{esc(t("roads.map", lang))}</a>')
    attribution = str(rw.get("attribution") or "") or t("roads.attr", lang)
    return (
        '<section class="card panel roads" aria-labelledby="road-h" data-roadworks>'
        f'<h2 class="h-panel" id="road-h">{esc(t("roads.h", lang))}</h2>'
        f'<p class="small muted">{esc(t("roads.sub", lang, inst=inst))}</p>'
        f'<p class="small rw-when"><b>{time_html}</b> · {counts}</p>'
        f'<p class="small muted">{esc(t("roads.age", lang))}</p>{stale}'
        f'{listing}<div class="rw-links mt-s">{"".join(links)}</div>'
        f'<p class="legend small muted mt-s">{esc(attribution)}</p></section>'
    )


def official_block(items: list[dict] | None, lang: str) -> str:
    """Official releases, as published, none linked to an event above."""
    rows = []
    for o in items or []:
        if not isinstance(o, dict) or not o.get("title"):
            continue
        code = _lang_code(o, "fr")
        color = OWN_COLOR.get(str(o.get("ownership_class") or "government"), "c-gov")
        inst = loc(o.get("institution_name"), lang)
        link = _link(o.get("url"), str(o.get("title")), lang_code=code) or f'<span lang="{esc(code)}">{esc(o.get("title"))}</span>'
        rows.append(
            f'<li><span class="m"><span class="chip own {color}"><span class="sw" aria-hidden="true"></span>{esc(inst)}</span>'
            f'{esc(i18n.fmt_day(o.get("published_at"), lang))}</span>{link}</li>'
        )
    body = (f'<ul class="off-list mt-s">{"".join(rows)}</ul>' if rows
            else f'<p class="small m0 mt-s">{esc(t("off.empty", lang))}</p>')
    sub = f'<p class="small muted">{esc(t("off.sub", lang))}</p>' if rows else ""
    return (f'<section class="card panel" aria-labelledby="off-h"><h2 class="h-panel" id="off-h">{esc(t("off.h", lang))}</h2>'
            f'{sub}{body}</section>')


def _period(ed: dict) -> str:
    p = str(ed.get("period") or "")
    if p in PERIODS:
        return p
    hour = i18n.local_hour(ed.get("clock"))
    if hour is None:
        return "morning"
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 22:
        return "evening"
    return "night"


def roster_block(ed: dict, lang: str) -> str:
    rows = []
    for r in ed.get("roster") or []:
        if not isinstance(r, dict):
            continue
        state = str(r.get("state") or "not_established")
        state = state if state in ROSTER_STATES else "not_established"
        if state == "in_events":
            text = tn("roster.in_events", _int(r.get("events")), lang)
        elif state == "outside":
            text = tn("roster.outside", _int(r.get("articles")), lang)
        elif state == "declared":
            text = tn("roster.declared", _int(r.get("articles")), lang)
        else:
            text = t(f"roster.{state}", lang)
        rows.append(f'<li><span>{esc(loc(r.get("name"), lang))}</span><span>{esc(text)}</span></li>')
    if not rows:
        return ""
    return (f'<div class="roster"><h3 class="sect-title">{esc(t("roster.h", lang))}</h3>'
            f'<ul>{"".join(rows)}</ul></div>')


def seal_line(ed: dict, lang: str) -> str:
    seal = ed.get("seal") if isinstance(ed.get("seal"), dict) else None
    if not seal or _int(seal.get("seq")) <= 0:
        return ""
    seq = _int(seal.get("seq"))
    root = re.sub(r"[^0-9a-f]", "", str(seal.get("root") or "").lower())[:12]
    path = f"/memoire/{seq}.html"
    link = f'<a href="{esc(path_for(lang, path))}"{link_lang_attrs(lang, path)}>{esc(t("seal.n", lang, seq=seq))}</a>'
    return f'<p class="seal">{esc(t("seal.lead", lang))} {link} · <code>{esc(root)}…</code> {esc(t("seal.tail", lang))}</p>'


_TICK_SVG = ('<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" '
             'stroke-linecap="round" stroke-linejoin="round" focusable="false"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>')


def end_card(ed: dict, lang: str) -> str:
    """The edition is finite: it ends, says when the next collection is
    expected (never a promise), and shows who spoke and who did not."""
    period = _period(ed)
    events = [e for e in (ed.get("events") or []) if isinstance(e, dict)]
    text = t("end.p", lang, end=t(f"ed.end.{period}", lang), events=tn("n.events", len(events), lang))
    nxt = i18n.fmt_time(ed.get("next_collection"), lang)
    if nxt:
        text += " " + t("end.next", lang, tm=nxt)
    return (
        '<section class="card end" aria-labelledby="end-h">'
        f'<div class="tick" aria-hidden="true">{_TICK_SVG}</div>'
        f'<h2 id="end-h">{esc(t("end.h", lang))}</h2>'
        f'<p class="muted end-p">{esc(text)}</p>{roster_block(ed, lang)}{seal_line(ed, lang)}</section>'
    )


def edition_page(ed: dict, lang: str, *, roadworks: dict | None = None, fr_path: str = "/evenements.html",
                 robots: str = "index, follow") -> str:
    """The events index / front door: edition head, event cards in the given
    order, the aside (Chez moi, roadworks, official declarations), the end.

    `roadworks` is a RoadworksView of its own, normally fresher than the
    edition (hourly lane)."""
    events = [e for e in (ed.get("events") or []) if isinstance(e, dict)]
    period = _period(ed)
    followed = _int(ed.get("institutions_followed"))
    clock = ed.get("clock")
    eyebrow = t("ed.line", lang, period=t(f"ed.title.{period}", lang), date=i18n.fmt_date(clock, lang, weekday=True))
    lede = t("ed.lede", lang, events=tn("n.events", len(events), lang),
             institutions=tn("n.institutions", followed, lang), tm=i18n.fmt_time(clock, lang))
    rules = "".join(f"<li><b>{esc(t(f'rules.{i}.h', lang))}</b>{esc(t(f'rules.{i}.p', lang))}</li>" for i in (1, 2, 3))
    pledge = "".join(chip(t(f"pledge.{i}", lang)) for i in (1, 2, 3))
    head = (
        '<section class="edition" aria-labelledby="h1">'
        f'<p class="eyebrow">{esc(eyebrow)}</p><h1 id="h1" tabindex="-1">{esc(t("ed.h1", lang))}</h1>'
        f'<p class="lede">{esc(lede)}</p>'
        f'<details class="disc rules-d" open data-rules><summary>{esc(t("rules.sum", lang))}</summary><ul class="rules mt-xs">{rules}</ul></details>'
        f'<div class="pledge">{pledge}</div></section>'
    )
    if events:
        cards = "".join(event_card(e, lang) for e in events)
    else:
        cards = f'<p class="card panel">{esc(t("ed.empty", lang))}</p>'
    suggestions = [str(s) for s in (ed.get("suggestions") or [])][:6]
    aside = (f'<aside class="aside stack" aria-label="{esc(t("roads.h", lang))}">'
             f'{mine_panel(lang, suggestions)}{roadworks_block(roadworks, lang)}{official_block(ed.get("official"), lang)}</aside>')
    main = (f'{head}<div class="grid"><div class="stack" id="cards">{cards}</div>{aside}</div>{end_card(ed, lang)}')
    return document(lang=lang, fr_path=fr_path, title=t("ed.h1", lang), description=t("meta.desc.home", lang),
                    main=main, robots=robots)


# --------------------------------------------------------------------------- #
# Roadworks adapter: the live store -> RoadworksView
# --------------------------------------------------------------------------- #
# Same order as resident_brief.RW_SEVERITY (a test pins the equality).
RW_SEVERITY = {
    "all-lanes-closed": 0, "some-lanes-closed": 1, "alternating-one-way": 2,
    "some-lanes-closed-intermittent-or-short-duration": 3, "all-lanes-open": 4, "no-lanes-closed": 5,
}
RW_ROWS_DEFAULT = 6


def roadworks_view(store: dict | None, *, limit: int = RW_ROWS_DEFAULT, stale_after_hours: float = 6.0,
                   render_clock: str = "") -> dict:
    """Build the RoadworksView from `data/roadworks/latest_roadworks.json`.

    Pure: the caller passes its own `render_clock` (an ISO string) when it
    wants the `stale` flag; with none, `stale` stays False. Ordering is the
    departure screen's rule, not a second ranking: severity, then newest
    update, then event id. A foreign or corrupt store yields an empty view
    (collected_at empty: the block prints that the time is unknown)."""
    doc = store if isinstance(store, dict) else {}
    events = [e for e in (doc.get("events") or []) if isinstance(e, dict) and e.get("event_id")]
    ordered = sorted(events, key=lambda e: str(e.get("event_id") or ""))
    ordered.sort(key=lambda e: str(e.get("update_date") or ""), reverse=True)
    ordered.sort(key=lambda e: RW_SEVERITY.get(str(e.get("vehicle_impact") or ""), 6))
    rows = []
    for e in ordered[: max(0, int(limit))]:
        names = [str(x).strip() for x in (e.get("road_names") or []) if str(x or "").strip()]
        accuracy = (str(e.get("start_date_accuracy") or ""), str(e.get("end_date_accuracy") or ""))
        rows.append({
            "id": str(e.get("event_id")),
            "street": names[0] if names else "",
            "places": " · ".join(names[:3]),
            "text": " ".join(str(e.get("description") or "").split())[:200],
            "direction": str(e.get("direction") or ""),
            "what": str(e.get("event_type") or ""),
            "status": str(e.get("event_status") or ""),
            "from": str(e.get("start_date") or "")[:10],
            "to": str(e.get("end_date") or "")[:10],
            "estimated": "estimated" in accuracy,
            "impact": str(e.get("vehicle_impact") or "unknown"),
        })
    closed = sum(1 for e in events if e.get("vehicle_impact") == "all-lanes-closed")
    planned = sum(1 for e in events if e.get("event_status") in ("planned", "pending"))
    collected = str(doc.get("fetched_at") or "")
    stale = False
    a, b = i18n.parse_instant(collected), i18n.parse_instant(render_clock)
    if a is not None and b is not None:
        age = (b - a).total_seconds()
        stale = age > stale_after_hours * 3600 or age < -300
    return {
        "collected_at": collected if a is not None else "",
        "institution_name": str(doc.get("institution_name") or ""),
        "total": len(events), "closed": closed, "planned": planned, "stale": stale, "rows": rows,
        "dataset_url": str(doc.get("dataset_url") or ""),
    }


# --------------------------------------------------------------------------- #
# Demo: invented data, a complete static site for review and tests
# --------------------------------------------------------------------------- #
def _inst_row(inst: str, name: str, own: str, group: str, lang_code: str, owner_name: str = "") -> dict:
    row = {"institution": inst, "institution_name": name, "ownership_class": own, "owner_group": group,
           "language": lang_code, "source_id": inst + "-main"}
    if owner_name:
        row["owner_name"] = owner_name
    return row


_RF = _inst_row("radio-fleuve", "Radio Fleuve", "public_broadcaster", "fleuve", "fr",
                {"fr": "Société Fleuve (fictive)", "en": "Fleuve Corporation (fictional)"})
_FN = _inst_row("fleuve-news", "Fleuve News", "public_broadcaster", "fleuve", "en",
                {"fr": "Société Fleuve (fictive)", "en": "Fleuve Corporation (fictional)"})
_CC = _inst_row("courrier-cap", "Le Courrier du Cap", "cooperative", "courrier-cap", "fr")
_PL = _inst_row("phare-libre", "Le Phare Libre", "independent", "phare", "fr")
_HP = _inst_row("harbour-post", "Harbour Post", "independent", "harbour", "en")
_VD = _inst_row("ville-demo", "Ville de Démo", "government", "ville-demo", "fr")
_JF = "Le Journal du Faubourg"


def _member(base: dict, item_id: str, published: str, first_seen: str, **extra: object) -> dict:
    m = dict(base)
    m.update({"item_id": item_id, "published_at": published, "first_seen": first_seen,
              "origin_class": "own_reporting", "date_suspect": False})
    m.update(extra)
    return m


def demo_views() -> dict:
    """INVENTED views that exercise every component: a three-voice grouping, a
    bilingual same-owner pair with divergent figures and official anchors, an
    English-only single voice, an official single voice, a quiet single voice.
    No publisher text of any real outlet appears here."""
    ed_clock = "2026-10-06T14:12:00+00:00"
    seen = "2026-10-06T14:12:00+00:00"
    ev1 = {
        "event_id": "ev-1a2b3c4d5e6f7081", "permanent": False, "type": "municipal-bylaw", "places": ["quebec-city"],
        "label": {"fr": "Règlement municipal · Québec", "en": "Municipal bylaw · Québec City"},
        "type_label": {"fr": "Règlement municipal", "en": "Municipal bylaw"},
        "place_label": {"fr": "Québec", "en": "Québec City"},
        "tier": "certain", "window_state": "in_window", "activity": "developed",
        "born_edition": "2026-10-06T08:05:00+00:00", "last_edition": ed_clock, "seals": [57],
        "languages": ["fr"],
        "members": [
            _member(_RF, "a1b2c3d4e5f60001", "2026-10-06T13:12:10Z", seen,
                    title="Abribus chauffants : le conseil adopte le règlement",
                    excerpt="Le règlement autorise 40 abribus chauffants dans les quartiers centraux, a indiqué la présidente du comité.",
                    author="J. Exemple", url="https://radio-fleuve.example/nouvelles/abribus-chauffants"),
            _member(_CC, "a1b2c3d4e5f60002", "2026-10-06T14:05:00Z", seen,
                    title="Un règlement sur les abribus chauffants voté à l'unanimité",
                    excerpt="Les élus ont voté sans opposition le règlement encadrant les abribus chauffants. Le coût reste à préciser.",
                    author="P. Démo", photo_credit="Photo Démo", origin_class="own_reporting",
                    url="https://courrier-cap.example/conseil/abribus"),
            _member(_PL, "a1b2c3d4e5f60003", "2026-10-06T15:40:00Z", seen,
                    title="Abribus : feu vert des élus, l'opposition s'abstient",
                    excerpt="Le conseil donne son feu vert aux abribus. L'opposition s'est abstenue lors du vote final.",
                    author="A. Exemple", origin_class="unknown", url="https://phare-libre.example/politique/abribus"),
        ],
        "independence": {"groups": [["a1b2c3d4e5f60001"], ["a1b2c3d4e5f60002"], ["a1b2c3d4e5f60003"]], "count": 3},
        "language_pairs": [], "anchors": [], "facts": [],
        "why": {"shared": [{"label": "abribus"}, {"label": "règlement"}, {"label": "conseil"}],
                "gap_seconds": None, "window_hours": 72, "min_institutions": 2,
                "place_anchor": {"fr": "Québec", "en": "Québec City"},
                "rule": "complete-link"},
        "words": [
            {"k": "abribus", "label": "abribus", "forms": ["abribus"], "alias": False},
            {"k": "reglement", "label": "règlement", "forms": ["règlement"], "alias": False},
            {"k": "conseil", "label": "conseil", "forms": ["conseil"], "alias": False},
            {"k": "feu", "label": "feu vert", "forms": ["feu vert"], "alias": False},
        ],
        "silence": [
            {"institution_name": _JF, "state": "no_linked_item"},
            {"institution_name": "Fleuve News", "state": "no_linked_item"},
            {"institution_name": "Harbour Post", "state": "collection_gap"},
        ],
        "neighbours": [
            {"member": _member(_CC, "a1b2c3d4e5f60011", "2026-10-06T12:00:00Z", seen,
                               title="Abribus : un citoyen réclame plus de bancs",
                               url="https://courrier-cap.example/citoyens/bancs"), "reason": "shared_word", "why": None},
            {"member": _member(_RF, "a1b2c3d4e5f60012", "2026-10-05T19:30:00Z", seen,
                               title="Transport en commun : un autre chantier sur l'avenue Démo",
                               url="https://radio-fleuve.example/nouvelles/chantier-demo"), "reason": "same_thread", "why": None},
        ],
        "ledger": {"changes": [{"at": "2026-10-06T14:40:00Z", "institution_name": "Le Courrier du Cap", "language": "fr",
                                "before": "Abribus : un règlement voté à l'unanimité",
                                "after": "Un règlement sur les abribus chauffants voté à l'unanimité"}]},
    }
    ev2 = {
        "event_id": "ev-2b3c4d5e6f708192", "permanent": False, "type": "fire-building", "places": ["limoilou"],
        "label": {"fr": "Incendie de bâtiment · Limoilou", "en": "Building fire · Limoilou"},
        "type_label": {"fr": "Incendie de bâtiment", "en": "Building fire"},
        "place_label": {"fr": "Limoilou", "en": "Limoilou"},
        "tier": "probable", "window_state": "in_window", "activity": "new",
        "born_edition": ed_clock, "last_edition": ed_clock, "seals": [57], "languages": ["en", "fr"],
        "members": [
            _member(_RF, "b1b2c3d4e5f60001", "2026-10-06T11:20:00Z", seen,
                    title="Incendie dans un entrepôt de Limoilou : deux personnes soignées",
                    excerpt="Un incendie s'est déclaré dans un entrepôt de Limoilou. Deux personnes ont été soignées sur place.",
                    author="J. Exemple", url="https://radio-fleuve.example/nouvelles/incendie-limoilou"),
            _member(_FN, "b1b2c3d4e5f60002", "2026-10-06T11:55:00Z", seen,
                    title="Warehouse fire in Limoilou leaves two people treated",
                    excerpt="Firefighters battled a fire at a Limoilou warehouse. Two people were treated at the scene.",
                    author="S. Example", photo_credit="Demo Photo", url="https://fleuve-news.example/news/limoilou-fire"),
            _member(_HP, "b1b2c3d4e5f60003", "2026-10-06T12:30:00Z", seen, origin_class="wire",
                    title="Limoilou warehouse blaze: three taken to hospital",
                    excerpt="Three people were taken to hospital after a blaze at a Limoilou warehouse, the wire report says.",
                    url="https://harbour-post.example/local/limoilou-blaze"),
        ],
        "independence": {"groups": [["b1b2c3d4e5f60001", "b1b2c3d4e5f60002"], ["b1b2c3d4e5f60003"]], "count": 2},
        "language_pairs": [{"fr": "b1b2c3d4e5f60001", "en": "b1b2c3d4e5f60002", "rule": "bilingual-complete-link", "same_owner": True}],
        "anchors": [
            {"type": "official_item", "ref": "c1b2c3d4e5f60001", "rule": "membership", "status": "linked_by_rule",
             "institution_name": "Ville de Démo", "published_at": "2026-10-06T12:05:00Z", "language": "fr",
             "title": "Fermeture temporaire de la rue Démo", "url": "https://ville-demo.example/avis/rue-demo"},
            {"type": "roadwork", "ref": "DEMO-20261006-01", "rule": "same-road-overlapping-dates", "status": "linked_by_rule"},
            {"type": "edition_seal", "ref": "57", "rule": "event-present-in-edition", "status": "linked_by_rule"},
        ],
        "facts": [{"slot": {"kind": "count", "unit": "persons_injured", "subject": "fire-building"},
                   "values": [{"value": 2, "stated_by": ["b1b2c3d4e5f60001", "b1b2c3d4e5f60002"], "institutions": ["radio-fleuve", "fleuve-news"]},
                              {"value": 3, "stated_by": ["b1b2c3d4e5f60003"], "institutions": ["harbour-post"]}],
                   "divergent": True, "method": "facts-v1", "status": "proposed"}],
        "why": {"shared": [{"label": "Limoilou"}, {"label": "incendie ≙ fire", "alias": True},
                           {"label": "entrepôt ≙ warehouse", "alias": True}],
                "gap_seconds": None, "window_hours": 72, "min_institutions": 2,
                "place_anchor": {"fr": "Limoilou", "en": "Limoilou"},
                "rule": "bilingual-complete-link"},
        "words": [
            {"k": "limoilou", "label": "Limoilou", "forms": ["Limoilou"], "alias": False},
            {"k": "feu", "label": "incendie ≙ fire", "forms": ["incendie", "fire"], "alias": True},
            {"k": "entrepot", "label": "entrepôt ≙ warehouse", "forms": ["entrepôt", "warehouse"], "alias": True},
        ],
        "silence": [{"institution_name": "Le Courrier du Cap", "state": "no_linked_item"},
                    {"institution_name": "Le Phare Libre", "state": "no_linked_item"}],
        "neighbours": [], "ledger": {"changes": []},
    }
    ev3 = {
        "event_id": "ev-3c4d5e6f70819203", "permanent": False, "type": "pedestrian-cyclist-struck", "places": ["saint-roch"],
        "label": {"fr": "Piéton ou cycliste heurté · Saint-Roch", "en": "Pedestrian or cyclist struck · Saint-Roch"},
        "type_label": {"fr": "Piéton ou cycliste heurté", "en": "Pedestrian or cyclist struck"},
        "place_label": {"fr": "Saint-Roch", "en": "Saint-Roch"},
        "tier": None, "window_state": "in_window", "activity": "new",
        "born_edition": ed_clock, "last_edition": ed_clock, "seals": [], "languages": ["en"],
        "members": [_member(_HP, "d1b2c3d4e5f60001", "2026-10-06T13:45:00Z", seen,
                            title="Cyclist hurt in collision on a Saint-Roch street",
                            excerpt="A cyclist was taken to hospital after a collision with a car in Saint-Roch on Tuesday morning.",
                            author="S. Example", url="https://harbour-post.example/local/saint-roch-collision")],
        "independence": {"groups": [["d1b2c3d4e5f60001"]], "count": 1}, "language_pairs": [], "anchors": [], "facts": [],
        "why": {}, "words": [],
        "silence": [{"institution_name": "Radio Fleuve", "state": "no_linked_item"},
                    {"institution_name": "Le Courrier du Cap", "state": "no_linked_item"},
                    {"institution_name": "Le Phare Libre", "state": "no_linked_item"}],
        "neighbours": [], "ledger": {"changes": []},
    }
    ev4 = {
        "event_id": "ev-4d5e6f7081920314", "permanent": False, "type": "water-advisory", "places": ["vanier"],
        "label": {"fr": "Avis sur l'eau potable · Vanier", "en": "Drinking-water advisory · Vanier"},
        "type_label": {"fr": "Avis sur l'eau potable", "en": "Drinking-water advisory"},
        "place_label": {"fr": "Vanier", "en": "Vanier"},
        "tier": None, "window_state": "in_window", "activity": "new",
        "born_edition": ed_clock, "last_edition": ed_clock, "seals": [57], "languages": ["fr"],
        "members": [_member(_VD, "e1b2c3d4e5f60001", "2026-10-06T10:00:00Z", seen, origin_class="official",
                            title="Avis d'ébullition préventif dans le secteur Démo",
                            excerpt="La Ville demande de faire bouillir l'eau du robinet dans le secteur Démo jusqu'à nouvel ordre.",
                            url="https://ville-demo.example/avis/ebullition")],
        "independence": {"groups": [["e1b2c3d4e5f60001"]], "count": 1}, "language_pairs": [],
        "anchors": [{"type": "official_item", "ref": "e1b2c3d4e5f60001", "rule": "membership", "status": "linked_by_rule",
                     "institution_name": "Ville de Démo", "published_at": "2026-10-06T10:00:00Z"}],
        "facts": [], "why": {}, "words": [],
        "silence": [{"institution_name": "Radio Fleuve", "state": "no_linked_item"},
                    {"institution_name": "Le Journal du Faubourg", "state": "not_established"}],
        "neighbours": [], "ledger": {"changes": []},
    }
    ev5 = {
        "event_id": "ev-5e6f708192031425", "permanent": False, "type": "festival-event", "places": ["vieux-port"],
        "label": {"fr": "Festival ou grand événement · Vieux-Port", "en": "Festival or major event · Old Port"},
        "type_label": {"fr": "Festival ou grand événement", "en": "Festival or major event"},
        "place_label": {"fr": "Vieux-Port", "en": "Old Port"},
        "tier": None, "window_state": "in_window", "activity": "developed",
        "born_edition": "2026-10-05T14:00:00+00:00", "last_edition": ed_clock, "seals": [56, 57], "languages": ["fr"],
        "members": [_member(_CC, "f1b2c3d4e5f60001", "2026-10-06T09:00:00Z", seen,
                            title="Le festival des lanternes revient au Vieux-Port en novembre",
                            excerpt="La programmation complète sera dévoilée la semaine prochaine, selon les organisateurs.",
                            author="P. Démo", photo_credit="Photo Démo", url="https://courrier-cap.example/culture/lanternes")],
        "independence": {"groups": [["f1b2c3d4e5f60001"]], "count": 1}, "language_pairs": [], "anchors": [],
        "facts": [], "why": {}, "words": [],
        "silence": [{"institution_name": "Radio Fleuve", "state": "no_linked_item"},
                    {"institution_name": "Fleuve News", "state": "no_linked_item"}],
        "neighbours": [], "ledger": {"changes": []},
    }
    events = [ev1, ev2, ev3, ev4, ev5]
    edition = {
        "clock": ed_clock, "period": "", "next_collection": "2026-10-06T20:10:00+00:00",
        "institutions_followed": 9, "events": events,
        "roster": [
            {"name": "Radio Fleuve", "state": "in_events", "events": 2, "articles": 3},
            {"name": "Fleuve News", "state": "in_events", "events": 1, "articles": 1},
            {"name": "Le Courrier du Cap", "state": "in_events", "events": 2, "articles": 4},
            {"name": "Le Phare Libre", "state": "in_events", "events": 1, "articles": 1},
            {"name": "Harbour Post", "state": "in_events", "events": 2, "articles": 2},
            {"name": "Le Journal du Faubourg", "state": "outside", "events": 0, "articles": 18},
            {"name": "Ville de Démo", "state": "declared", "events": 1, "articles": 4},
            {"name": "Hydro-Démo", "state": "no_items", "events": 0, "articles": 0},
            {"name": "Gouvernement démo", "state": "collection_gap", "events": 0, "articles": 0},
        ],
        "seal": {"seq": 57, "root": "0a8246b0c1d2e3f405162738495a6b7c8d9e0f1a2b3c4d5e6f708192a3b4c5d6"},
        "official": [
            {"institution_name": "Ville de Démo", "ownership_class": "government", "published_at": "2026-10-05T14:00:00Z",
             "title": "La promotion « Sortir ensemble » est de retour cet automne", "url": "https://ville-demo.example/promos/automne", "language": "fr"},
            {"institution_name": "Gouvernement démo", "ownership_class": "government", "published_at": "2026-10-04T10:30:00Z",
             "title": "Les navettes fluviales ferment leur saison", "url": "https://gouv-demo.example/navettes", "language": "fr"},
        ],
        "suggestions": ["Limoilou", "Saint-Roch", "Avenue des Érables"],
    }
    roadworks = {
        "collected_at": "2026-10-06T22:53:00+00:00", "institution_name": "Ville de Québec",
        "total": 128, "closed": 31, "planned": 44, "stale": False,
        "rows": [
            {"id": "DEMO-1", "street": "Avenue des Érables", "places": "Avenue des Érables", "text": "Avenue des Érables entre la rue des Fabricants et la rue du Quai, travaux d'aqueduc et d'égouts.",
             "direction": "both-directions", "what": "road-work", "status": "active", "from": "2026-09-15", "to": "2026-10-20", "estimated": False, "impact": "all-lanes-closed"},
            {"id": "DEMO-2", "street": "Rue des Fabricants", "places": "Rue des Fabricants · Rue du Quai", "text": "",
             "direction": "eastbound", "what": "road-work", "status": "planned", "from": "2026-10-07", "to": "2026-10-09", "estimated": True, "impact": "all-lanes-closed"},
            {"id": "DEMO-3", "street": "Boulevard de la Rivière", "places": "Boulevard de la Rivière", "text": "Voie de droite fermée pour l'entretien des chaussées.",
             "direction": "northbound", "what": "work-zone", "status": "active", "from": "2026-10-01", "to": "2026-10-12", "estimated": False, "impact": "some-lanes-closed"},
            {"id": "DEMO-4", "street": "Rue du Quai", "places": "Rue du Quai", "text": "", "direction": "", "what": "detour",
             "status": "active", "from": "2026-10-06", "to": "2026-10-06", "estimated": False, "impact": "alternating-one-way"},
        ],
    }
    return {"edition": edition, "roadworks": roadworks, "events": events}


def permanent_view(ev: dict) -> dict:
    """The same view flagged permanent: the renderer then drops all publisher text."""
    out = dict(ev)
    out["permanent"] = True
    return out


def demo_pages() -> dict[str, str]:
    """Every page of the demo site, keyed by its path under the site root."""
    views = demo_views()
    ed, rw, events = views["edition"], views["roadworks"], views["events"]
    pages: dict[str, str] = {}
    for lang in ("fr", "en"):
        base = "" if lang == "fr" else "en/"
        front = edition_page(ed, lang, roadworks=rw, fr_path="/evenements.html", robots="noindex, nofollow")
        pages[f"{base}evenements.html"] = front
        pages[f"{base}index.html"] = edition_page(ed, lang, roadworks=rw, fr_path="/", robots="noindex, nofollow")
        for ev in events:
            name = f"{base}evenements/{ev['event_id']}.html"
            pages[name] = event_page(ev, lang, edition=ed, followed=ed["institutions_followed"], robots="noindex, nofollow")
            pages[f"permanent/{name}"] = event_page(permanent_view(ev), lang, edition=ed, robots="noindex, nofollow")
    return dict(sorted(pages.items()))


def write_demo_site(out_dir: Path | str) -> list[str]:
    """Write the demo site (pages + the real assets) under `out_dir`; returns
    the sorted relative paths. Only for review and tests: noindex, invented data."""
    import store_io
    root = Path(out_dir)
    written = []
    for rel, text in demo_pages().items():
        store_io.write_text_atomic(root / rel, text)
        written.append(rel)
    for asset in ("evenements.css", "evenements.js", "fonts.css"):
        target = root / "assets" / asset
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "public" / "assets" / asset, target)
        written.append(f"assets/{asset}")
    fonts = ROOT / "public" / "assets" / "fonts"
    if fonts.is_dir():
        shutil.copytree(fonts, root / "assets" / "fonts", dirs_exist_ok=True)
    return sorted(written)


def main(argv: list[str] | None = None) -> int:
    """`python -X utf8 scripts/composants.py <dir>` writes the demo site."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: composants.py <output-dir>   (writes the invented-data demo site)")
        return 2
    paths = write_demo_site(args[0])
    print(f"composants: demo site written ({len(paths)} files) under {args[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
