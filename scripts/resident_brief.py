"""A finite, French-first resident brief. No generated news or inferred advice.

Rendering is deterministic, offline, and uses the same public ranking. Search and
saved stories run in the browser; no account, analytics, or location collection.
"""
from __future__ import annotations

import hashlib
import html
import json
import math
import re
import unicodedata
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from ingest_rss import public_http_url
import edge_atlas

ROOT = Path(__file__).resolve().parents[1]
WINDOW_DAYS = 7
TOPICS = {
    "housing": "Se loger", "transport": "Se déplacer", "energy/hydro": "Énergie",
    "education": "Éducation", "law": "Vie publique", "health": "Santé",
    "trade": "Économie", "economy": "Économie", "security": "Sécurité",
    "environment": "Environnement", "death": "Société", "culture": "Culture",
    "other": "Vie locale",
}
AREAS = {
    "cite": ("La Cité-Limoilou", r"\b(?:limoilou|saint[- ]roch|saint[- ]sauveur|montcalm|saint[- ]sacrement|vieux[- ]quebec|saint[- ]jean[- ]baptiste|lairet|maizerets)\b"),
    "rivières": ("Les Rivières", r"\b(?:les rivieres|duberger|les saules|lebourgneuf|(?:quartier|secteur|a) vanier)\b"),
    "foy": ("Sainte-Foy–Sillery–Cap-Rouge", r"\b(?:sainte[- ]foy|sillery|cap[- ]rouge)\b"),
    "charlesbourg": ("Charlesbourg", r"\bcharlesbourg\b"),
    "beauport": ("Beauport", r"\bbeauport\b"),
    "haute": ("La Haute-Saint-Charles", r"\b(?:haute[- ]saint[- ]charles|val[- ]belair|loretteville|saint[- ]emile|lac[- ]saint[- ]charles)\b"),
    "levis": ("Lévis", r"\blevis\b"),
}
SERVICES = (
    ("01", "Avant de partir", "Entraves et travaux", "La carte officielle des travaux sur votre trajet.", "https://carte.ville.quebec.qc.ca/"),
    ("02", "Transport en commun", "Mon parcours RTC", "Horaires, avis et outils du Réseau de transport de la Capitale.", "https://www.rtcquebec.ca/restez-informe"),
    ("03", "Avoir son mot à dire", "Consultations publiques", "Les projets sur lesquels la Ville consulte les citoyens.", "https://participationcitoyenne.ville.quebec.qc.ca/"),
    ("04", "L’hiver à Québec", "Alertes de déneigement", "S’abonner directement aux avis de la Ville.", "https://www.ville.quebec.qc.ca/apropos/espace-presse/abonnement/alertes_sms.aspx"),
)


# Display-spoofing control characters (C0/C1 except tab/LF/CR, soft hyphen,
# zero-width marks and bidi overrides) are stripped from relayed text. The
# visible wording stays verbatim; these characters are display instructions,
# not content, and a hostile feed must not be able to spoof what a title says.
_SPOOF = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\xad\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]"
)

TITLE_CAP = 300     # relayed titles are truncated, never padded or rewritten
SUMMARY_CAP = 2000  # excerpt source and search index; longer summaries add no recall

# Discoverability / sharing identity — one source for the head.
SITE_URL = "https://vigieqc.com"
SITE_CANONICAL = f"{SITE_URL}/"
SITE_TITLE = "Vigie — Québec, à hauteur de vie"
SITE_DESCRIPTION = (
    "Comprendre ce qui bouge à Québec. Un point local, des sources à comparer "
    "et des repères pour agir. Sans compte, sans fil infini."
)


def sanitize(value: object) -> str:
    return _SPOOF.sub("", str(value if value is not None else ""))


def safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def esc(value: object) -> str:
    return html.escape(sanitize(value), quote=True)


def plain(value: object) -> str:
    # Strip real markup first, then decode entities, then strip display-spoofing
    # controls (numeric entities can decode to bidi marks). A literal "<" that is
    # not markup - "5 < 6 > 7 M$" - must survive verbatim instead of being eaten.
    raw = re.sub(r"</?[a-zA-Z][^>]*>", " ", str(value or ""))
    return re.sub(r"\s+", " ", sanitize(html.unescape(raw))).strip()


# Folding law: ligatures and typographic apostrophes are mapped before the
# diacritics are stripped, so the client fold in brief.js and this server-side
# search index agree (searching "oeuvre" must match a stored "œuvre").
_FOLD_LIGATURES = str.maketrans({"œ": "oe", "Œ": "oe", "æ": "ae", "Æ": "ae",
                                 "’": "'", "‘": "'", "`": "'", "´": "'"})


def folded(value: str) -> str:
    mapped = str(value).translate(_FOLD_LIGATURES)
    return "".join(c for c in unicodedata.normalize("NFKD", mapped.casefold()) if not unicodedata.combining(c))


def safe_url(value: object) -> str:
    value = str(value or "").strip()
    try:
        if re.search(r"[\x00-\x20\x7f\\]", value):
            return ""
        return public_http_url(value)
    except (ValueError, TypeError):
        return ""


def parse_date(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    for parser in (lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")), parsedate_to_datetime):
        try:
            dt = parser(raw.strip())
            if dt.tzinfo is not None:
                return dt.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            pass
    return None


def date_html(value: object, *, fallback: str = "Date non précisée") -> str:
    dt = parse_date(value)
    if dt is None:
        return esc(fallback)
    # Browser localizes to Québec, including DST. UTC is an honest no-JS fallback.
    return f'<time datetime="{dt.isoformat()}">{dt:%Y-%m-%d %H:%M} UTC</time>'


def collection_status(run: dict, now: datetime) -> dict:
    results = run.get("results") or []
    by_id = {r.get("source_id"): r for r in results if isinstance(r, dict)}
    enabled = run.get("enabled_rss") or list(by_id)
    ok = sum(bool(by_id.get(s, {}).get("ok")) and not by_id.get(s, {}).get("parse_error") for s in enabled)
    stamp = parse_date(run.get("fetched_at"))
    age = (now - stamp).total_seconds() if stamp else None
    stale = not enabled or age is None or age > 6 * 3600 or age < -300
    return {"ok": ok, "total": len(enabled), "stale": stale,
            "partial": not enabled or ok < len(enabled), "at": stamp.isoformat() if stamp else "",
            "failed": [s for s in enabled if not by_id.get(s, {}).get("ok") or by_id.get(s, {}).get("parse_error")]}


def latest_run() -> dict:
    paths = sorted((ROOT / "data" / "raw").glob("_run_*.json"), reverse=True)
    if paths:
        try:
            return json.loads(paths[0].read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return {}


MEDIA_MANIFEST = ROOT / "data" / "media" / "brief_manifest.json"
_MEDIA_FILE = re.compile(r"[a-f0-9]{20}\.(?:jpg|jpeg|png|webp|avif|gif)")


def load_brief_media() -> dict:
    """uid -> {"file", "credit"} for publisher preview images (brief-media v1/v2).

    Images are fetched at collection time and served from this site: reading
    the brief never contacts a publisher. `credit` is the photographer name
    from the publisher's own feed media, when there is one - it is only ever
    set on feed-sourced images, so the caption can never misattribute an
    og:image. A foreign or corrupt manifest renders no images rather than
    guessing, and only strict filenames pass, so a hostile store can never
    aim an <img> outside /media/.
    """
    try:
        doc = json.loads(MEDIA_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict) or doc.get("method") not in ("brief-media-v1", "brief-media-v2"):
        return {}
    media = doc.get("media")
    if not isinstance(media, dict):
        return {}
    out = {}
    for uid, entry in media.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
            continue
        if not _MEDIA_FILE.fullmatch(entry["file"]):
            continue
        credit = entry.get("credit")
        out[str(uid)] = {
            "file": entry["file"],
            "credit": credit.strip()[:120] if isinstance(credit, str) and credit.strip() else None,
        }
    return out


def prepare_items(ranked: list[dict], now: datetime) -> tuple[list[dict], int]:
    """Preserve rank order; age gate by publication, never fetch time."""
    rows, excluded, seen = [], 0, set()
    for item in ranked or []:
        if not isinstance(item, dict):
            excluded += 1
            continue
        url = safe_url(item.get("url"))
        title = plain(item.get("title"))
        if len(title) > TITLE_CAP:
            title = title[:TITLE_CAP].rstrip() + "…"
        when = parse_date(item.get("published_at"))
        if not title or not url:
            excluded += 1
            continue
        if when is None or (now - when).total_seconds() < -300 or (now - when).days >= WINDOW_DAYS:
            excluded += 1
            continue
        if url in seen:
            continue
        seen.add(url)
        enrich = item.get("enrich")
        enrich = enrich if isinstance(enrich, dict) else {}
        geo_block = enrich.get("geo")
        geo = (geo_block.get("geo") if isinstance(geo_block, dict) else None) or item.get("display_geo", "linked")
        topics_block = enrich.get("topics")
        topic_ids = [
            str(t.get("topic") if t.get("topic") is not None else "other")
            for t in (topics_block if isinstance(topics_block, list) else [])
            if isinstance(t, dict)
        ] or ["other"]
        summary = plain(item.get("summary"))[:SUMMARY_CAP]
        text = folded(title + " " + summary)
        areas = [key for key, (_, pattern) in AREAS.items() if geo == "quebec-city" and re.search(pattern, text)]
        # IDs are derived from canonical URLs: syndication/feed changes do not erase bookmarks.
        uid = hashlib.sha256(url.encode()).hexdigest()[:20]
        rows.append({**item, "uid": uid, "url": url, "title": title,
                     "summary": summary, "published": when.isoformat(),
                     "geo": geo, "topics": topic_ids, "areas": areas})
    return rows, excluded


def related_sources(item: dict, issues: list[dict], eligible: dict[str, dict]) -> list[dict]:
    rows, seen = [], {item["url"]}
    for issue in issues or []:
        if not isinstance(issue, dict):
            continue
        entries = [
            it
            for voice in (issue.get("tensions") or [])
            if isinstance(voice, dict)
            for it in (voice.get("items") or [])
            if isinstance(it, dict)
        ]
        if not any(it.get("candidate_id") == item.get("id") for it in entries):
            continue
        for entry in entries:
            other = eligible.get(entry.get("candidate_id"))
            if other and other["url"] not in seen:
                seen.add(other["url"])
                rows.append(other)
    return rows[:5]


NEST_LABELS = {"quebec-city": "Québec et environs", "quebec": "Au Québec", "linked": "Ailleurs"}


def dossier_nest(geo_focus: object) -> str:
    geos = geo_focus if isinstance(geo_focus, list) else []
    if "quebec-city" in geos:
        return "quebec-city"
    if "quebec" in geos:
        return "quebec"
    return "linked"


def dossier_units(issue: dict, eligible: dict) -> list[str]:
    """Checkable units already on dossier items in this brief — never invented."""
    raws: list[str] = []
    if not isinstance(issue, dict):
        return raws
    seen: set[str] = set()
    for tension in issue.get("tensions") or []:
        if not isinstance(tension, dict):
            continue
        for entry in tension.get("items") or []:
            if not isinstance(entry, dict):
                continue
            row = eligible.get(entry.get("candidate_id"))
            if not isinstance(row, dict):
                continue
            enrich = row.get("enrich")
            enrich = enrich if isinstance(enrich, dict) else {}
            for impact in enrich.get("impacts") or []:
                if not isinstance(impact, dict):
                    continue
                for unit in impact.get("units") or []:
                    if not isinstance(unit, dict):
                        continue
                    raw = str(unit.get("raw") or "").strip()
                    if raw and raw not in seen:
                        seen.add(raw)
                        raws.append(raw)
    return raws[:4]


def tracking_html(issue: dict) -> str:
    """Durable-history line: collection facts only.

    Absence is never a resolution; editions_seen is never importance.
    """
    tracking = issue.get("tracking")
    tracking = tracking if isinstance(tracking, dict) else {}
    seen = safe_int(tracking.get("editions_seen"))
    if seen <= 0:
        return ""
    if seen == 1:
        text = "Suivi depuis cette édition."
    else:
        first = date_html(tracking.get("first_seen"), fallback="date non précisée")
        text = f"Suivi depuis le {first} — présent dans {seen} éditions collectées."
    missed = safe_int(tracking.get("editions_missed"))
    if missed > 0:
        label = "édition" if missed == 1 else "éditions"
        text += (
            f" Absent de {missed} {label} — une absence de la collecte"
            " n’est pas une résolution."
        )
    return f'<p class="dossier-tracking fine">{text}</p>'


def dossier_timeline_html(issue: dict) -> str:
    """Field-level collection counters across editions (progressive disclosure).

    A count is a collection fact: growth is never escalation, and an edition the
    dossier missed is an absence, not a resolution. Renders only with two or more
    recorded editions, so a first edition stays quiet.
    """
    tracking = issue.get("tracking") if isinstance(issue.get("tracking"), dict) else {}
    timeline = tracking.get("timeline") if isinstance(tracking.get("timeline"), list) else []
    rows = [t for t in timeline if isinstance(t, dict) and t.get("ts")]
    if len(rows) < 2:
        return ""
    entries = []
    for entry in rows[-5:]:
        sources = safe_int(entry.get("sources"))
        bits = [f'{sources} source{"s" if sources != 1 else ""}']
        if "items" in entry:
            items = safe_int(entry.get("items"))
            bits.append(f'{items} article{"s" if items != 1 else ""}')
        if "official" in entry and safe_int(entry.get("official")):
            official = safe_int(entry.get("official"))
            bits.append(f'{official} source{"s" if official != 1 else ""} '
                        f'officielle{"s" if official != 1 else ""}')
        when = date_html(entry.get("ts"), fallback="date non précisée")
        entries.append(f'<li>{when} — {" · ".join(bits)}</li>')
    return (
        '<details class="dossier-timeline"><summary>Repères de collecte '
        f'({len(rows)} éditions)</summary><ul>{"".join(entries)}</ul>'
        '<p class="fine">Compteurs de collecte, pas une escalade. Une absence '
        "d’une édition n’est pas une résolution.</p></details>"
    )


def dossier_voices_html(issue: dict) -> str:
    """The full chambre at a glance: every followed institution and its state.

    Spoke institutions first (with their usable article count), then the quiet
    ones — including media silence the official-only block does not name. A
    rapprochement is never a contradiction; absence is never proven editorial
    silence; several media are never several independent confirmations.
    """
    issue = issue if isinstance(issue, dict) else {}
    tensions = [t for t in (issue.get("tensions") or []) if isinstance(t, dict)]
    silence = issue.get("silence") if isinstance(issue.get("silence"), dict) else {}
    rows: list[str] = []
    spoke_names: list[str] = []
    for tension in tensions:
        name = str(tension.get("institution_name") or tension.get("source_name") or "").strip()
        if not name or name in spoke_names:
            continue
        spoke_names.append(name)
        kind = str(tension.get("source_kind") or tension.get("kind") or "").lower()
        chip = '<span class="dv-kind">officiel</span>' if kind == "official" else ""
        usable = [
            it for it in (tension.get("items") or [])
            if isinstance(it, dict) and safe_url(it.get("url"))
        ]
        count = len(usable)
        state = f'a parlé · {count} article{"s" if count != 1 else ""}' if count else "a parlé"
        rows.append(
            f'<li class="dv-row dv-spoke">{chip}<span class="dv-inst">{esc(name)}</span>'
            f'<span class="dv-state">{state}</span></li>'
        )
    quiet: list[str] = []
    for entry in (silence.get("silent") or []):
        if not isinstance(entry, dict):
            continue
        name = str(
            entry.get("institution_name") or entry.get("source_name") or entry.get("source_id") or ""
        ).strip()
        if name and name not in spoke_names and name not in quiet:
            quiet.append(name)
    quiet.sort(key=lambda value: value.casefold())
    for name in quiet:
        rows.append(
            f'<li class="dv-row dv-quiet"><span class="dv-inst">{esc(name)}</span>'
            '<span class="dv-state">n’a pas parlé dans cette collecte</span></li>'
        )
    if not rows:
        return ""
    n_spoke, n_quiet = len(spoke_names), len(quiet)
    head = (
        f'{n_spoke} institution{"s" if n_spoke != 1 else ""} '
        f'{"ont" if n_spoke != 1 else "a"} parlé · '
        f'{n_quiet} n’{"ont" if n_quiet != 1 else "a"} pas parlé'
    )
    return (
        '<div class="dossier-voices">'
        f'<p class="dv-head">{head}</p>'
        f'<ul class="dv-list">{"".join(rows)}</ul>'
        '<p class="fine">Toutes les institutions suivies. Une absence dans nos flux '
        "n’est pas un silence éditorial prouvé, et ce n’est pas un indicateur de biais.</p></div>"
    )


def dossier_html(issue: dict, eligible: dict, edge_streets: dict | None = None,
                 edge_issues: dict | None = None) -> str:
    """One dossier: the question, who spoke, who stayed silent, sources to compare.

    Grouping is never a contradiction; absence is never proven editorial silence;
    several media are never several independent confirmations. Judgment stays with
    the reader. A malformed store entry is skipped, never fatal.
    """
    issue = issue if isinstance(issue, dict) else {}
    question = esc(issue.get("question") or "Sujet suivi")
    nest = dossier_nest(issue.get("geo_focus"))
    tensions = [t for t in (issue.get("tensions") or []) if isinstance(t, dict)]
    spoke_names = {
        str(t.get("institution_name") or t.get("source_name") or "").strip() for t in tensions
    }
    spoke_names.discard("")
    spoke_count = safe_int(issue.get("source_count"), len(spoke_names))
    bits: list[str] = []
    for tension in tensions:
        inst = esc(str(tension.get("institution_name") or "Source").strip())
        for entry in (tension.get("items") or [])[:2]:
            if not isinstance(entry, dict):
                continue
            url = safe_url(entry.get("url"))
            if not url:
                continue
            bits.append(
                f'<li><span class="dossier-inst">{inst}</span>'
                f'<a href="{esc(url)}" rel="noopener noreferrer">{esc(entry.get("title") or "Sans titre")}'
                f'<span class="arrow" aria-hidden="true"> ↗</span></a></li>'
            )
    sources = f'<ul class="dossier-sources">{"".join(bits)}</ul>' if bits else ""
    silence = issue.get("silence") or {}
    if not isinstance(silence, dict):
        silence = {}
    quiet = [
        str(s.get("institution_name") or s.get("source_id") or "").strip()
        for s in (silence.get("silent") or [])
        if isinstance(s, dict) and (s.get("source_kind") or "").lower() == "official"
    ]
    quiet = [n for n in quiet if n]
    silence_line = (
        '<p class="dossier-silence">Officiellement muets dans cette collecte : <strong>'
        + " · ".join(esc(n) for n in quiet[:3])
        + '</strong>. <span class="fine">Une absence dans nos flux n’est pas un silence '
        'éditorial prouvé, et ce n’est pas un indicateur de biais.</span></p>'
    ) if quiet else ""
    remix_line = (
        '<p class="dossier-remix fine">Aucune source officielle sur ce dossier — '
        "rapprochement de médias seulement.</p>"
        if issue.get("media_remix")
        else ""
    )
    units = dossier_units(issue, eligible)
    units_line = (
        '<p class="dossier-units">Repères à vérifier : '
        + "".join(f'<span class="unit">{esc(u[:48])}</span>' for u in units)
        + "</p>"
    ) if units else ""
    edge_line = dossier_edge_line(issue.get("issue_id"), edge_streets or {}, edge_issues or {})
    return (
        f'<article class="dossier" data-nest="{esc(nest)}">'
        f'<div class="dossier-head"><span class="dossier-nest">{esc(NEST_LABELS[nest])}</span>'
        f'<span class="dossier-count">{spoke_count} sources · rapprochement proposé</span></div>'
        f'<h3 class="dossier-q">{question}</h3>'
        f"{tracking_html(issue)}{dossier_timeline_html(issue)}{dossier_voices_html(issue)}"
        f"{sources}{silence_line}{remix_line}{units_line}{edge_line}"
        f"</article>"
    )


def dossiers_section(issues: list[dict], eligible: dict, edge_streets: dict | None = None,
                     edge_issues: dict | None = None) -> str:
    """Cross-source dossiers on the resident front door — the lookout, in French."""
    dossiers = [iss for iss in (issues or []) if isinstance(iss, dict) and iss.get("question")]
    count = len(dossiers)
    if dossiers:
        cards = "".join(dossier_html(iss, eligible, edge_streets, edge_issues) for iss in dossiers[:6])
        label = "dossier proposé" if count == 1 else "dossiers proposés"
        if count > 6:
            note = f"{count} {label} cette édition — les 6 premiers affichés ici."
        else:
            note = f"{count} {label}<br>cette édition."
        listing = f'<div class="dossier-list">{cards}</div>'
    else:
        note = "Aucun dossier<br>cette édition."
        listing = (
            '<p class="no-data">Aucun sujet n’a été rapproché entre plusieurs institutions '
            "dans cette collecte. Cela ne dit rien de la couverture ailleurs.</p>"
        )
    return (
        '<section class="dossiers" id="dossiers" aria-labelledby="dossiers-title">'
        '<div class="section-top"><div><p class="eyebrow">REGARD CROISÉ</p>'
        '<h2 id="dossiers-title">Ce que les sources racontent ensemble.</h2></div>'
        f'<p class="section-note">{note}</p></div>'
        '<p class="dossiers-intro">Quand plusieurs institutions parlent du même sujet, Vigie '
        "les rassemble — sans décider qui a raison. Un rapprochement n’est pas une "
        "contradiction, et plusieurs médias ne sont pas plusieurs confirmations "
        "indépendantes.</p>"
        f"{listing}</section>"
    )


def _delta_text(delta: dict | None) -> str:
    delta = delta if isinstance(delta, dict) else {}
    parts: list[str] = []
    added = safe_int(delta.get("items_added"))
    if added:
        parts.append(f"+{added} article" + ("s" if added > 1 else ""))
    voices = safe_int(delta.get("voices_added"))
    if voices:
        parts.append(f"+{voices} source" + ("s" if voices > 1 else ""))
    if delta.get("official_voice_joined"):
        parts.append("une source officielle a rejoint")
    if delta.get("newer_publication"):
        parts.append("publication plus récente")
    return " · ".join(parts)


def change_section(ledger: dict | None) -> str:
    """Editorial “what changed in the city since the last edition” — public.

    Distinct from the client-side “Depuis mon repère” reading marker, which is
    personal and article-level. This is dossier-level and identical for every
    reader. Returns "" when no prior edition was archived, so a first edition
    never implies a comparison that did not happen. New ≠ important; developed
    ≠ escalation; quiet ≠ resolved. Judgment stays with the reader.
    """
    ledger = ledger or {}
    if not ledger.get("has_previous"):
        return ""
    new = ledger.get("new") or []
    developed = ledger.get("developed") or []
    quiet = ledger.get("quiet") or []
    if not (new or developed or quiet):
        body = (
            '<p class="no-data">Aucun dossier n’a changé depuis la dernière édition '
            "(aucun nouveau, aucun développé, aucun retiré de la collecte). Cela ne dit "
            "rien de la couverture ailleurs.</p>"
        )
    else:
        blocks: list[str] = []

        def truncated(total: int, shown: int = 6) -> str:
            return (
                f'<p class="fine">+ {total - shown} autres dans les données'
                " de cette édition.</p>"
                if total > shown else ""
            )

        if new:
            items = "".join(
                '<li><span class="chg-tag chg-new">Nouveau</span>'
                f'<a href="#dossiers">{esc(e.get("question") or "Dossier suivi")}</a></li>'
                for e in new[:6]
            )
            blocks.append(
                f'<div class="chg-group"><h3>Nouveaux dossiers <span class="chg-n">{len(new)}</span></h3>'
                f'<ul class="chg-list">{items}</ul>{truncated(len(new))}</div>'
            )
        if developed:
            items = "".join(
                '<li><span class="chg-tag chg-dev">Développé</span>'
                f'<a href="#dossiers">{esc(e.get("question") or "Dossier suivi")}</a>'
                f'<span class="chg-delta">{esc(_delta_text(e.get("delta")))}</span></li>'
                for e in developed[:6]
            )
            blocks.append(
                f'<div class="chg-group"><h3>Dossiers développés <span class="chg-n">{len(developed)}</span></h3>'
                f'<ul class="chg-list">{items}</ul>{truncated(len(developed))}</div>'
            )
        if quiet:
            items = "".join(
                '<li><span class="chg-tag chg-quiet">Retiré</span>'
                f'<span class="chg-q">{esc(e.get("question") or "Dossier suivi")}</span></li>'
                for e in quiet[:6]
            )
            blocks.append(
                f'<div class="chg-group"><h3>Disparus de cette collecte <span class="chg-n">{len(quiet)}</span></h3>'
                f'<ul class="chg-list">{items}</ul>{truncated(len(quiet))}'
                '<p class="fine">« Retiré » signifie absent de cette collecte, pas réglé. '
                "Une absence n’est pas un silence éditorial prouvé.</p></div>"
            )
        body = "".join(blocks)
    return (
        '<section class="changes" id="changements" aria-labelledby="changes-title">'
        '<div class="section-top"><div><p class="eyebrow">CE QUI A CHANGÉ</p>'
        '<h2 id="changes-title">Depuis la dernière édition.</h2></div>'
        '<p class="section-note">Comparaison<br>des dossiers proposés.</p></div>'
        '<p class="dossiers-intro">Vigie compare les dossiers de cette édition à la '
        "précédente. « Nouveau » signifie nouvellement rapproché, pas plus important. "
        "Un rapprochement n’est pas une contradiction, et plusieurs médias ne sont pas "
        "plusieurs confirmations indépendantes.</p>"
        f"{body}</section>"
    )


RW_IMPACT_LABELS = {
    "all-lanes-closed": "Toutes les voies fermées",
    "some-lanes-closed": "Voies partiellement fermées",
    "alternating-one-way": "Circulation en alternance",
    "some-lanes-closed-intermittent-or-short-duration": "Fermetures intermittentes ou de courte durée",
    "all-lanes-open": "Toutes les voies ouvertes",
    "no-lanes-closed": "Aucune voie fermée",
    "unknown": "Impact non précisé",
}
RW_DIRECTION_LABELS = {
    "northbound": "direction nord", "southbound": "direction sud",
    "eastbound": "direction est", "westbound": "direction ouest",
    "both-directions": "deux directions",
}
RW_EVENT_TYPE_LABELS = {
    "road-work": "Travaux", "work-zone": "Zone de travaux", "detour": "Détour",
    "incident": "Incident", "accident": "Accident", "event": "Événement",
}
# The City's own status vocabulary, relayed literally. "active" needs no badge;
# planned/pending declarations are labeled so a future window is never presented
# as a current closure.
RW_STATUS_LABELS = {"planned": "Planifiée", "pending": "En attente"}
RW_SEVERITY = {
    "all-lanes-closed": 0, "some-lanes-closed": 1, "alternating-one-way": 2,
    "some-lanes-closed-intermittent-or-short-duration": 3,
    "all-lanes-open": 4, "no-lanes-closed": 5,
}
RW_DISPLAY_CAP = 8
RW_STREET_INDEX_CAP = 400
RW_MAP_URL = "https://carte.ville.quebec.qc.ca/"

# Sidecar stores (edge-atlas-v1 / anomaly-beacon-v1). A foreign, corrupt or
# empty store collapses to zero HTML — the brief stays byte-identical.
EDGES_METHOD = "edge-atlas-v1"
ANOMALIES_METHOD = "anomaly-beacon-v1"
ANOMALY_DISPLAY_CAP = 3
DOSSIER_EDGE_CAP = 2


def valid_anomalies(doc: object) -> list[dict]:
    """Verified verdict rows (uncapped). The method guard mirrors compile_anomalies;
    the display cap and its honest "+ N autres" note are applied at render time."""
    if not isinstance(doc, dict) or doc.get("method") != ANOMALIES_METHOD:
        return []
    rows = doc.get("anomalies")
    if not isinstance(rows, list):
        return []
    return [
        row for row in rows
        if isinstance(row, dict) and str(row.get("claim") or "").strip()
    ]


def anomaly_total(doc: object, rows: list[dict]) -> int:
    """Exact measured count when the verdict carries one, never below what we render."""
    count = doc.get("anomaly_count") if isinstance(doc, dict) else None
    if isinstance(count, int) and not isinstance(count, bool):
        return max(count, len(rows))
    return len(rows)


def valid_edges(doc: object) -> tuple[dict, dict]:
    """Verified (streets, issues) indexes. The method guard mirrors edge_atlas."""
    if not isinstance(doc, dict) or doc.get("method") != EDGES_METHOD:
        return {}, {}
    streets = doc.get("streets")
    issues = doc.get("issues")
    streets = {
        str(k): v for k, v in streets.items() if isinstance(v, dict)
    } if isinstance(streets, dict) else {}
    issues = {
        str(k): v for k, v in issues.items() if isinstance(v, dict)
    } if isinstance(issues, dict) else {}
    return streets, issues


def anomalies_html(rows: list[dict], total: int | None = None) -> str:
    """Structural reading of the official collection — measured facts only.

    Fixed-threshold rules published in anomalies.md; no prediction, no
    importance judgment, no alert chrome. Empty verdict, empty HTML. When the
    verdict measured more rows than the display cap, the remainder is announced
    exactly like every other capped section — never silently dropped.
    """
    if not rows:
        return ""
    shown = rows[:ANOMALY_DISPLAY_CAP]
    items = []
    for row in shown:
        evidence = row.get("evidence_event_ids")
        n = len(evidence) if isinstance(evidence, list) else 0
        cited = (
            f'<span class="fine">{n} entrave{"s" if n != 1 else ""} citée{"s" if n != 1 else ""}'
            ' · <a href="/anomalies.md">comment nous mesurons</a></span>'
        ) if n else '<span class="fine"><a href="/anomalies.md">comment nous mesurons</a></span>'
        items.append(
            f'<li class="rw-anomaly"><span class="rw-tag rw-t-anom">{esc(row.get("rule_label") or "Anomalie")}</span>'
            f'<span class="rw-anomaly-claim">{esc(row.get("claim"))}</span>{cited}</li>'
        )
    hidden = max(0, (total if isinstance(total, int) else len(rows)) - len(shown))
    more = (
        f'<p class="fine">+ {hidden} autre anomalie mesurée dans cette collecte.</p>'
        if hidden == 1 else
        f'<p class="fine">+ {hidden} autres anomalies mesurées dans cette collecte.</p>'
        if hidden > 1 else ""
    )
    return (
        '<div class="rw-anomalies" id="anomalies">'
        '<p class="eyebrow">LECTURE STRUCTURELLE</p>'
        '<h3>Ce qui sort de l’ordinaire dans cette collecte.</h3>'
        f'<ul class="rw-anomaly-list">{"".join(items)}</ul>'
        f"{more}"
        '<p class="fine">Règles à seuils fixes, publiées dans anomalies.md. Une anomalie '
        'est un fait de collecte mesuré — pas une prédiction, pas un jugement '
        'd’importance.</p></div>'
    )


def rw_edge_line(event: dict, edge_streets: dict) -> str:
    """Proposed street-level join between one obstruction and the edition's
    dossiers. A shared street name, never geographic proof."""
    if not edge_streets:
        return ""
    for raw in event.get("road_names") or []:
        key = edge_atlas.street_key(raw)
        info = edge_streets.get(key) if key else None
        if not isinstance(info, dict):
            continue
        matched = info.get("matched_issue_ids")
        n = len(matched) if isinstance(matched, list) else 0
        if not n:
            continue
        label = "un dossier proposé" if n == 1 else f"{n} dossiers proposés"
        return (
            f'<p class="rw-edge fine">Rapprochement proposé : {label} de cette édition '
            f'mentionne « {esc(info.get("display") or raw)} » — '
            '<a href="#dossiers">voir les dossiers</a> · '
            '<a href="/edge.md">méthode</a>. Une mention textuelle, pas une '
            'preuve géographique.</p>'
        )
    return ""


def dossier_edge_line(issue_id: object, edge_streets: dict, edge_issues: dict) -> str:
    """Reverse join: active official obstructions on a street this dossier
    names. Rendered only when the roadworks section exists (anchor safety)."""
    rec = edge_issues.get(str(issue_id or ""))
    if not isinstance(rec, dict) or not edge_streets:
        return ""
    keys = [
        k for k in (rec.get("streets") or [])
        if isinstance(edge_streets.get(k), dict)
    ][:DOSSIER_EDGE_CAP]
    rows = [
        (str(edge_streets[k].get("display") or k), safe_int(edge_streets[k].get("active_count")))
        for k in keys
    ]
    rows = [(display, count) for display, count in rows if display and count > 0]
    if not rows:
        return ""
    parts = " ; ".join(
        f"{count} entrave{'s' if count != 1 else ''} active{'s' if count != 1 else ''} "
        f"sur « {esc(display)} »"
        for display, count in rows
    )
    return (
        '<p class="dossier-edge fine">Rapprochement proposé : la collecte officielle '
        f'déclare {parts} — rue{"" if len(rows) == 1 else "s"} mentionnée{"" if len(rows) == 1 else "s"} '
        'dans ce dossier. <a href="#travaux">Voir les entraves</a> · '
        '<a href="/edge.md">méthode</a>. Un nom de rue partagé, pas une preuve '
        'géographique.</p>'
    )


def _rw_places(event: dict) -> str:
    roads = [str(r).strip() for r in (event.get("road_names") or []) if str(r or "").strip()]
    return " · ".join(roads[:3])


def _rw_dates(event: dict) -> str:
    start, end = event.get("start_date"), event.get("end_date")
    estimated = "estimated" in (
        str(event.get("start_date_accuracy") or ""), str(event.get("end_date_accuracy") or "")
    )
    note = ' <span class="fine">(dates estimées par la Ville)</span>' if estimated else ""
    if start and end:
        return f'<p class="rw-dates">Du {date_html(start)} au {date_html(end)}{note}</p>'
    if start:
        return f'<p class="rw-dates">Depuis le {date_html(start)}{note}</p>'
    if end:
        return f'<p class="rw-dates">Jusqu’au {date_html(end)}{note}</p>'
    return ""


def _rw_history_line(event: dict, history: dict) -> str:
    """Durable per-event collection facts. An absence is never an end of works."""
    rec = history.get(str(event.get("event_id")))
    if not isinstance(rec, dict):
        return ""
    seen = safe_int(rec.get("collections_seen"))
    if seen <= 0:
        return ""
    if seen == 1:
        text = "Première collecte où cette entrave apparaît."
    else:
        first = date_html(rec.get("first_seen"), fallback="date non précisée")
        text = f"Dans nos collectes depuis le {first} — {seen} collectes."
    missed = safe_int(rec.get("collections_missed"))
    if missed > 0:
        label = "collecte" if missed == 1 else "collectes"
        text += (
            f" Auparavant absente de {missed} {label} — une absence"
            " n’est pas une fin des travaux."
        )
    return f'<p class="rw-history fine">{text}</p>'


def _street_key(raw: object) -> str:
    """Folded literal street key shared with the client and the atlas: a shared
    name is a match, never geographic proof."""
    return folded(plain(str(raw or "")))


def _rw_card(event: dict, new_ids: set, changed_by_id: dict, has_previous: bool,
             history: dict | None = None, edge_streets: dict | None = None) -> str:
    eid = str(event.get("event_id"))
    impact = str(event.get("vehicle_impact") or "")
    severity = RW_SEVERITY.get(impact, 6)
    kicker = [
        label for label in (
            RW_STATUS_LABELS.get(str(event.get("event_status") or "")),
            RW_EVENT_TYPE_LABELS.get(str(event.get("event_type") or "")),
            RW_IMPACT_LABELS.get(impact),
            RW_DIRECTION_LABELS.get(str(event.get("direction") or "")),
        ) if label
    ]
    tags = ""
    if has_previous:
        if eid in new_ids:
            tags += '<span class="rw-tag rw-t-new">Nouvelle</span>'
        change = changed_by_id.get(eid)
        if isinstance(change, dict):
            # The City's own date revision, relayed with its direction. Postponed
            # is not extended works; advanced is not finished.
            moved = change.get("end_date_moved")
            if moved == "later":
                tags += '<span class="rw-tag rw-t-post">Fin reportée</span>'
            elif moved == "earlier":
                tags += '<span class="rw-tag rw-t-adv">Fin avancée</span>'
            else:
                tags += '<span class="rw-tag rw-t-chg">Modifiée</span>'
    kicker_html = f'<p class="rw-kicker">{" · ".join(esc(k) for k in kicker)}</p>' if kicker else ""
    desc = plain(event.get("description"))
    if len(desc) > 200:
        desc = desc[:200].rsplit(" ", 1)[0] + "…"
    desc_html = f'<p class="rw-desc">{esc(desc)}</p>' if desc else ""
    road_keys = " ".join(
        key for key in (_street_key(raw) for raw in (event.get("road_names") or [])[:6]) if key
    )
    return (
        f'<li class="rw-item rw-sev-{severity}" data-roads="{esc(road_keys)}">'
        f'<div class="rw-head">{tags}<span class="rw-roads">{esc(_rw_places(event) or "Lieu non précisé")}</span></div>'
        f"{kicker_html}{_rw_dates(event)}{_rw_history_line(event, history or {})}"
        f"{rw_edge_line(event, edge_streets or {})}{desc_html}</li>"
    )


RW_SKETCH_W = 720.0
RW_SKETCH_H = 320.0
RW_SKETCH_PAD = 10.0


def _rw_street_rows(events: list[dict]) -> list[dict]:
    """Distinct declared street names with their active-obstruction count.

    Derived from the official road_names only, folded for literal matching.
    Deterministic order: count desc, then name, then key.
    """
    counts: dict[str, list] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        for raw in event.get("road_names") or []:
            display = plain(str(raw or ""))
            key = _street_key(raw)
            if not display or not key:
                continue
            row = counts.get(key)
            if row is None:
                counts[key] = [display, 1]
            else:
                row[1] += 1
    rows = [{"name": display, "key": key, "n": count} for key, (display, count) in counts.items()]
    rows.sort(key=lambda row: (-row["n"], row["name"].casefold(), row["key"]))
    return rows[:RW_STREET_INDEX_CAP]


def _rw_corridors_html(rows: list[dict]) -> str:
    """Opt-in saved corridors: on-device only, literal street matching.

    No geolocation, no route effect, no server: the reader follows a declared
    street name and Vigie marks the declarations that literally name it. The
    index island lets the client resolve counts over every active event, not
    only the eight displayed cards.
    """
    if not rows:
        return ""
    island = json.dumps(
        {"method": "rw-streets-v1", "streets": rows},
        ensure_ascii=False, separators=(",", ":"),
    ).replace("<", "\\u003c")
    return (
        f'<script type="application/json" id="vigie-streets">{island}</script>'
        '<div class="rw-corridors js-only" id="rw-corridors" hidden>'
        '<p class="eyebrow">VOS CORRIDORS</p>'
        '<p class="rw-corridors-note">Suivez une ou deux rues que vous empruntez souvent. '
        "La correspondance est littérale (le nom déclaré par la Ville), sur cet appareil "
        "seulement : aucune position demandée, aucun effet sur votre trajet calculé, et "
        "un nom partagé n’est pas une preuve géographique.</p>"
        '<div class="rw-corridors-add">'
        '<label class="sr-only" for="rw-corridor-input">Ajouter une rue déclarée</label>'
        '<input id="rw-corridor-input" type="text" list="rw-street-options" '
        'placeholder="Une rue déclarée (ex. rue Dorchester)" autocomplete="off" maxlength="120">'
        '<datalist id="rw-street-options"></datalist>'
        '<button type="button" id="rw-corridor-add">Suivre</button></div>'
        '<ul id="rw-corridor-list" class="rw-corridor-list"></ul>'
        '<p class="fine" id="rw-corridor-status"></p></div>'
    )


def _rw_sketch(rw: dict, events: list[dict]) -> str:
    """Static spatial scheme of the declared obstructions — no basemap.

    Dot density over the official collection's own coordinates: where the
    declarations cluster, never a road map and never geographic proof. No
    basemap is drawn (none is licensed here); the official map stays the
    reference. Deterministic: events sorted by id, and the whole block collapses
    to zero HTML without usable coordinates.
    """
    bbox = rw.get("bbox")
    if (not isinstance(bbox, list) or len(bbox) != 4
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in bbox)):
        return ""
    min_lon, min_lat, max_lon, max_lat = (float(v) for v in bbox)
    if max_lon <= min_lon or max_lat <= min_lat:
        return ""
    pts: list[tuple[float, float, bool]] = []
    for event in sorted((e for e in events if isinstance(e, dict)),
                        key=lambda e: str(e.get("event_id") or "")):
        point = event.get("point")
        if not isinstance(point, list) or len(point) != 2:
            continue
        try:
            lon, lat = float(point[0]), float(point[1])
        except (TypeError, ValueError):
            continue
        if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
            continue
        pts.append((lon, lat, str(event.get("vehicle_impact") or "") == "all-lanes-closed"))
    if not pts:
        return ""

    def sx(lon: float) -> float:
        return RW_SKETCH_PAD + (lon - min_lon) / (max_lon - min_lon) * (RW_SKETCH_W - 2 * RW_SKETCH_PAD)

    def sy(lat: float) -> float:
        return RW_SKETCH_PAD + (max_lat - lat) / (max_lat - min_lat) * (RW_SKETCH_H - 2 * RW_SKETCH_PAD)

    grid = "".join(
        f'<line class="rw-grid" x1="{sx(min_lon + (max_lon - min_lon) * i / 4):.0f}" y1="{RW_SKETCH_PAD:.0f}" '
        f'x2="{sx(min_lon + (max_lon - min_lon) * i / 4):.0f}" y2="{RW_SKETCH_H - RW_SKETCH_PAD:.0f}" />'
        for i in range(1, 4)
    ) + "".join(
        f'<line class="rw-grid" x1="{RW_SKETCH_PAD:.0f}" y1="{sy(min_lat + (max_lat - min_lat) * i / 4):.0f}" '
        f'x2="{RW_SKETCH_W - RW_SKETCH_PAD:.0f}" y2="{sy(min_lat + (max_lat - min_lat) * i / 4):.0f}" />'
        for i in range(1, 4)
    )
    dots = "".join(
        f'<circle class="{"rw-dot-closed" if closed else "rw-dot"}" '
        f'cx="{sx(lon):.1f}" cy="{sy(lat):.1f}" r="{3.1 if closed else 2.2:.1f}" />'
        for lon, lat, closed in pts
    )
    span_km = (max_lon - min_lon) * 111.32 * math.cos(math.radians((min_lat + max_lat) / 2))
    bar_km = max(1, round(span_km / 5)) if span_km else 1
    bar_px = (bar_km / span_km * (RW_SKETCH_W - 2 * RW_SKETCH_PAD)) if span_km else 0.0
    n = len(pts)
    n_closed = sum(1 for _, _, closed in pts if closed)
    legend = (
        f'<span><span class="rw-key rw-key-dot"></span>{n} entrave'
        f'{"s" if n != 1 else ""} active{"s" if n != 1 else ""} déclarée{"s" if n != 1 else ""}</span>'
        + (
            f'<span><span class="rw-key rw-key-closed"></span>{n_closed} fermeture'
            f'{"s" if n_closed != 1 else ""} complète{"s" if n_closed != 1 else ""}</span>'
            if n_closed else ""
        )
        + f'<span><span class="rw-key rw-key-bar"></span>échelle ≈ {bar_km} km</span>'
    )
    return (
        '<figure class="rw-sketch">'
        f'<svg viewBox="0 0 {RW_SKETCH_W:.0f} {RW_SKETCH_H:.0f}" role="img" '
        f'aria-label="Schéma de répartition : {n} entraves déclarées dans la zone couverte. '
        'La liste complète suit.">'
        f'<rect class="rw-sketch-frame" x="{RW_SKETCH_PAD:.0f}" y="{RW_SKETCH_PAD:.0f}" '
        f'width="{RW_SKETCH_W - 2 * RW_SKETCH_PAD:.0f}" height="{RW_SKETCH_H - 2 * RW_SKETCH_PAD:.0f}" />'
        f"{grid}"
        f'<line class="rw-scale" x1="{RW_SKETCH_PAD:.0f}" y1="{RW_SKETCH_H - RW_SKETCH_PAD - 6:.0f}" '
        f'x2="{RW_SKETCH_PAD + bar_px:.1f}" y2="{RW_SKETCH_H - RW_SKETCH_PAD - 6:.0f}" />'
        f"{dots}</svg>"
        f'<figcaption class="rw-sketch-cap">{legend}</figcaption>'
        '<p class="fine">Schéma de répartition projeté sur les coordonnées officielles — '
        "pas une carte routière, pas une preuve géographique, aucun effet sur votre trajet. "
        "La carte officielle reste la référence.</p></figure>"
    )


def roadworks_section(rw: dict | None, now: datetime, anomalies: dict | None = None,
                      edges: dict | None = None) -> str:
    """Official road obstructions — structured change data, never articles.

    Renders only when a store exists with a parseable collection timestamp, so
    the section never implies data that was not collected. Removed ≠ ended;
    estimated dates stay marked estimated; no personal-route effect is ever
    computed. Judgment stays with the reader, on the official map.

    Sidecars: a verified anomaly verdict adds the structural-reading block
    (fixed-threshold collection facts); a verified edge atlas adds proposed
    street-level joins to the edition's dossiers. Both collapse to zero HTML
    when absent, corrupt, foreign or empty.
    """
    rw = rw if isinstance(rw, dict) else {}
    events = rw.get("events")
    fetched = parse_date(rw.get("fetched_at"))
    if not isinstance(events, list) or fetched is None:
        return ""
    events = [e for e in events if isinstance(e, dict) and e.get("event_id")]
    diff = rw.get("diff") if isinstance(rw.get("diff"), dict) else {}
    has_previous = bool(diff.get("has_previous"))
    stale = (now - fetched).total_seconds() > 6 * 3600 or (now - fetched).total_seconds() < -300
    # Stable three-pass sort: severity first, then most recently updated, then id.
    ordered = sorted(events, key=lambda e: str(e.get("event_id")))
    ordered.sort(key=lambda e: str(e.get("update_date") or ""), reverse=True)
    ordered.sort(key=lambda e: RW_SEVERITY.get(str(e.get("vehicle_impact") or ""), 6))
    new_ids = {e.get("event_id") for e in (diff.get("new") or []) if isinstance(e, dict)}
    changed_by_id = {
        e.get("event_id"): e for e in (diff.get("changed") or []) if isinstance(e, dict)
    }
    hist_doc = rw.get("event_history") if isinstance(rw.get("event_history"), dict) else {}
    # Method guard mirrors ingest_wzdx.HISTORY_METHOD: a foreign-history store
    # renders no collection facts rather than misreading another model's record.
    history = (
        hist_doc.get("events")
        if hist_doc.get("method") == "wzdx-event-history-v1"
        and isinstance(hist_doc.get("events"), dict)
        else {}
    )
    edge_streets, _ = valid_edges(edges)
    anomaly_rows = valid_anomalies(anomalies)
    beacon = anomalies_html(anomaly_rows, total=anomaly_total(anomalies, anomaly_rows))
    sketch = _rw_sketch(rw, ordered)
    corridors = _rw_corridors_html(_rw_street_rows(ordered))
    cards = "".join(
        _rw_card(e, new_ids, changed_by_id, has_previous, history, edge_streets)
        for e in ordered[:RW_DISPLAY_CAP]
    )
    count = len(ordered)
    count_note = f"{count} entrave déclarée<br>du flux officiel." if count == 1 else f"{count} entraves déclarées<br>du flux officiel."
    if ordered:
        listing = f'<ul class="rw-list">{cards}</ul>'
        more = (
            f'<p class="rw-more">+ {count - RW_DISPLAY_CAP} autres entraves déclarées dans cette collecte.</p>'
            if count > RW_DISPLAY_CAP else ""
        )
    else:
        listing = (
            '<p class="no-data">Aucune entrave déclarée dans cette collecte. Cela ne signifie '
            "pas qu’aucun travail n’a lieu ailleurs sur le réseau.</p>"
        )
        more = ""
    changes = ""
    if has_previous:
        bits = []
        new_count = safe_int(diff.get("new_count"))
        changed_count = safe_int(diff.get("changed_count"))
        removed_count = safe_int(diff.get("removed_count"))
        if new_count:
            bits.append(f"+{new_count} nouvelle" + ("s" if new_count > 1 else ""))
        if changed_count:
            bits.append(f"~{changed_count} modifiée" + ("s" if changed_count > 1 else ""))
        if removed_count:
            bits.append(f"−{removed_count} retirée" + ("s" if removed_count > 1 else ""))
        if bits:
            removed_note = (
                ' <span class="fine">« Retirée » signifie absente de cette collecte, '
                "pas nécessairement terminée.</span>" if removed_count else ""
            )
            changes = f'<p class="rw-changes">Depuis la dernière collecte : {" · ".join(bits)}.{removed_note}</p>'
        declared: dict[str, int] = {}
        for entry in diff.get("removed") or []:
            if not isinstance(entry, dict):
                continue
            state = str(entry.get("city_declared_status") or "").strip()
            if state:
                declared[state] = declared.get(state, 0) + 1
        if declared:
            # The City's own ended vocabulary, relayed literally: a declaration,
            # never a resolution verified by Vigie.
            parts = ", ".join(
                f'{n} entrave{"s" if n > 1 else ""} « {esc(state)} »'
                for state, n in sorted(declared.items())
            )
            changes += (
                f'<p class="rw-ended">La Ville déclare depuis la dernière collecte : {parts}. '
                '<span class="fine">Statuts officiels relayés tels quels — une déclaration, '
                "pas une vérification sur le terrain.</span></p>"
            )
    stale_html = (
        '<p class="rw-stale warning">Collecte à actualiser : ces données ont plus de six '
        "heures. Vérifiez la carte officielle avant de partir.</p>" if stale else ""
    )
    institution = esc(str(rw.get("institution_name") or "Ville de Québec").strip())
    dataset = safe_url(rw.get("dataset_url"))
    dataset_link = (
        f'<a href="{esc(dataset)}" rel="noopener noreferrer">{institution} — Entraves à la circulation en temps réel</a>'
        if dataset else f"{institution} — Entraves à la circulation en temps réel"
    )
    return (
        '<section class="roadworks" id="travaux" aria-labelledby="roadworks-title">'
        '<div class="section-top"><div><p class="eyebrow">DONNÉES OFFICIELLES</p>'
        '<h2 id="roadworks-title">Travaux et entraves.</h2></div>'
        f'<p class="section-note">{count_note}</p></div>'
        '<p class="dossiers-intro">Les entraves déclarées par la Ville dans son flux '
        "officiel en temps réel, relayées telles quelles. Vigie ne recalcule aucun "
        "effet sur votre trajet et ne classe pas ces données avec les articles.</p>"
        f"{sketch}{changes}{beacon}{corridors}{listing}{more}{stale_html}"
        f'<p class="rw-map"><a href="{RW_MAP_URL}" rel="noopener noreferrer">Ouvrir la carte officielle des travaux <span aria-hidden="true">↗</span></a></p>'
        f'<p class="rw-attr fine">Données : {dataset_link} (CC-BY 4.0, via Données Québec). '
        f"Collecte du {date_html(fetched.isoformat())}. Les dates marquées « estimées » "
        "le sont par la Ville, pas par Vigie.</p></section>"
    )


def article_html(item: dict, index: int, related: list[dict], media: dict | None = None) -> str:
    title = esc(item["title"])
    source = esc(item.get("source_name") or item.get("source_id") or "Source")
    media_entry = media.get(item["uid"]) if isinstance(media, dict) else None
    if isinstance(media_entry, str):  # plain filename (older callers/tests)
        media_file, media_credit = media_entry, None
    elif isinstance(media_entry, dict):
        media_file, media_credit = media_entry.get("file"), media_entry.get("credit")
    else:
        media_file, media_credit = None, None
    if not isinstance(media_file, str) or not _MEDIA_FILE.fullmatch(media_file):
        media_file = None
    if media_file:
        # Attribution law (LEGAL_RISK.md R2): the publisher's name always sits
        # beside the photo; a photographer name only when the publisher
        # attached one to this very feed image - never guessed, never for an
        # og:image we cannot credit.
        credit = media_credit.strip() if isinstance(media_credit, str) and media_credit.strip() else None
        caption = f"Photo : {esc(credit)} / {source}" if credit else f"Photo : {source}"
        media_html = (
            f'<figure class="story-media"><img src="/media/{media_file}" alt="" loading="lazy" decoding="async">'
            f'<figcaption class="media-credit">{caption}</figcaption></figure>'
        )
    else:
        media_html = ""
    geo = {"quebec-city": "Québec et environs", "quebec": "Au Québec", "linked": "Ailleurs"}.get(item["geo"], "Ailleurs")
    topic = TOPICS.get(item["topics"][0], "Vie locale")
    summary = item["summary"]
    truncated = len(summary) > 240
    excerpt_base = summary[:240].rsplit(" ", 1)[0] if truncated else summary
    excerpt = excerpt_base + "…" if truncated else summary
    excerpt_html = f'<p class="excerpt">{esc(excerpt)}</p><span class="excerpt-label">Extrait du flux de {source}</span>' if excerpt else '<p class="excerpt-label">Le flux ne fournit pas de résumé. Consultez l’article original.</p>'
    # Attribution law (LEGAL_RISK.md R1): the author name when the publisher's
    # feed gives one - s. 29.2 requires source AND author for news reporting.
    author = plain(item.get("author"))[:120]
    byline_author = f'Par {esc(author)}<span aria-hidden="true"> · </span>' if author else ""
    peers = "".join(f'<li><span>{esc(r.get("source_name") or r.get("source_id"))}</span><a href="{esc(safe_url(r.get("url")))}" rel="noopener noreferrer">{esc(r.get("title"))}</a>{date_html(r.get("published"))}</li>' for r in related)
    related_html = f'<p class="evidence-label">Autres articles du dossier proposé</p><ul class="source-list">{peers}</ul><p class="fine">Rapprochement automatique à vérifier. Plusieurs médias ne constituent pas plusieurs confirmations indépendantes.</p>' if peers else '<p class="fine">Aucun autre article rapproché dans cette collecte. Cela ne dit rien de la couverture ailleurs.</p>'
    kind = '<span class="official">Source officielle</span>' if item.get("source_kind") == "official" else ''
    place_reason = "Un lieu ou acteur local a été repéré dans le titre ou l’extrait." if item["geo"] == "quebec-city" else "Le classement géographique est proposé à partir du titre et de l’extrait."
    if item.get("source_kind") == "official" and item["geo"] == "quebec-city":
        place_reason = "Ce document provient d’une source officielle locale."
    # The search index carries exactly what the reader sees (title + displayed
    # excerpt), never the fuller internal summary (LEGAL_RISK.md R4).
    search_text = esc(folded(item['title'] + ' ' + excerpt_base + ' ' + str(item.get('source_name') or '')))
    return f'''<article class="story" id="article-{esc(item['uid'])}" data-id="{esc(item['uid'])}" data-geo="{esc(item['geo'])}" data-topics="{esc(' '.join(item['topics']))}" data-areas="{esc(' '.join(item['areas']))}" data-search="{search_text}" data-published="{esc(item['published'])}">
      <div class="story-number" aria-hidden="true">{index:02}</div><div class="story-body">
      {media_html}<div class="story-kicker"><span>{esc(topic)}</span><span>{geo}</span>{kind}<span class="new-label" hidden>Nouveau dans la collecte</span></div>
      <h3><a href="{esc(safe_url(item['url']))}" rel="noopener noreferrer">{title}<span class="arrow" aria-hidden="true"> ↗</span></a></h3>
      <p class="byline">{byline_author}{source}<span aria-hidden="true"> · </span>{date_html(item['published'])}{'<span class="language">Article en anglais</span>' if item.get('language') == 'en' else ''}</p>
      {excerpt_html}
      <div class="story-actions"><details class="evidence"><summary>Sources et contexte <span aria-hidden="true">＋</span></summary><div class="evidence-body"><p>{place_reason} Ce repérage ne prouve pas un effet sur votre situation.</p>{related_html}</div></details>
      <button class="save js-only" type="button" data-save="{esc(item['uid'])}" aria-pressed="false" aria-label="Garder : {title}">Garder <span aria-hidden="true">＋</span></button></div>
      </div></article>'''


def _glance_item(label: str, text: str, href: str) -> str:
    return (
        f'<a class="glance-item" href="{href}">'
        f'<span class="glance-label">{esc(label)}</span>'
        f'<span class="glance-text">{text} '
        '<span class="glance-go" aria-hidden="true">↗</span></span></a>'
    )


def digest_html(rows: list[dict], status: dict, ledger: dict | None, roadworks: dict | None,
                issues: list[dict], now: datetime, *, has_changes: bool) -> str:
    """Answer-first digest: orient in ten seconds, then let the reader drill down.

    Sentences, not a stat strip; every line is either a measured fact of this
    collection or it is absent. Each line links to the section that carries the
    detail. Deterministic: same inputs, same bytes.
    """
    items: list[str] = []
    total = len(rows)
    local = sum(1 for r in rows if isinstance(r, dict) and r.get("geo") == "quebec-city")
    if total:
        if local:
            text = (f"<strong>{local}</strong> article{'s' if local != 1 else ''} "
                    f"touchent Québec et ses environs, sur <strong>{total}</strong> retenus.")
        else:
            text = (f"Aucun article localement ancré sur <strong>{total}</strong> retenus : "
                    "le point lointain reste visible, jamais gonflé.")
        items.append(_glance_item("Ici", text, "#stories"))
    if isinstance(roadworks, dict):
        counts = roadworks.get("counts") if isinstance(roadworks.get("counts"), dict) else {}
        active = safe_int(counts.get("active"))
        fetched = parse_date(roadworks.get("fetched_at"))
        age = (now - fetched).total_seconds() if fetched else None
        stale = age is None or age > 6 * 3600 or age < -300
        if active or stale:
            text = (f"<strong>{active}</strong> entrave{'s' if active != 1 else ''} "
                    f"déclarée{'s' if active != 1 else ''} par la Ville"
                    + (", collecte à actualiser." if stale else " dans la dernière collecte."))
            items.append(_glance_item("Travaux", text, "#travaux"))
    if has_changes and isinstance(ledger, dict) and ledger.get("has_previous"):
        new = safe_int(ledger.get("new_count"))
        dev = safe_int(ledger.get("developed_count"))
        quiet = safe_int(ledger.get("quiet_count"))
        if new or dev or quiet:
            text = (f"<strong>{new}</strong> nouveau{'x' if new != 1 else ''}, "
                    f"<strong>{dev}</strong> développé{'s' if dev != 1 else ''}, "
                    f"<strong>{quiet}</strong> disparu{'s' if quiet != 1 else ''} de la collecte. "
                    "Jamais « résolu ».")
        else:
            text = "Aucun changement de dossier depuis la dernière édition."
        items.append(_glance_item("Dernière édition", text, "#changements"))
    names: set[str] = set()
    for iss in issues or []:
        if not isinstance(iss, dict):
            continue
        silence = iss.get("silence") if isinstance(iss.get("silence"), dict) else {}
        for entry in silence.get("silent") or []:
            if isinstance(entry, dict):
                name = str(entry.get("institution_name") or entry.get("source_name") or "").strip()
                if name:
                    names.add(name)
    if names:
        n = len(names)
        if n == 1:
            text = "<strong>1</strong> institution suivie n’a pas parlé dans les dossiers de cette édition."
        else:
            text = (f"<strong>{n}</strong> institutions suivies n’ont pas parlé dans les "
                    "dossiers de cette édition.")
        items.append(_glance_item("Silence", text, "#dossiers"))
    if not items:
        return ""
    return ('<nav class="glance" aria-label="En un coup d’œil">' + "".join(items) + "</nav>")


def render_brief(ranked: list[dict], generated_at: str, issues: list[dict], run: dict | None = None, ledger: dict | None = None, roadworks: dict | None = None, media: dict | None = None, anomalies: dict | None = None, edges: dict | None = None) -> str:
    now = parse_date(generated_at) or datetime.now(timezone.utc)
    rows, excluded = prepare_items(ranked, now)
    rw_html = roadworks_section(roadworks, now, anomalies, edges)
    edge_streets, edge_issues = valid_edges(edges)
    if not rw_html:
        # Anchor safety: dossier edge lines point at #travaux, which only
        # exists when the roadworks section rendered.
        edge_streets, edge_issues = {}, {}
    run = latest_run() if run is None else run
    if media is None:
        media = load_brief_media()
    elif not isinstance(media, dict):
        media = {}
    status = collection_status(run, now)
    eligible = {r.get("id"): r for r in rows}
    stories = "".join(article_html(r, n + 1, related_sources(r, issues, eligible), media) for n, r in enumerate(rows))
    areas = "".join(f'<option value="{esc(k)}">{esc(v[0])}</option>' for k, v in AREAS.items())
    topics = (("all", "Tout"), ("transport", "Se déplacer"), ("housing", "Se loger"), ("health", "Santé"), ("law", "Vie publique"), ("culture", "Culture"))
    filters = "".join(f'<button type="button" data-topic="{k}" aria-pressed="{"true" if k == "all" else "false"}">{v}</button>' for k, v in topics)
    service_html = "".join(f'<a class="service" href="{url}" rel="noopener noreferrer"><span class="service-index">{num} / {esc(eyebrow)}</span><h3>{esc(title)} <span aria-hidden="true">↗</span></h3><p>{esc(desc)}</p></a>' for num, eyebrow, title, desc, url in SERVICES)
    outcomes = {r.get("source_id"): r for r in run.get("results", []) if isinstance(r, dict)}
    source_rows = "".join(f'<li><span>{esc(sid)}</span><span>{"Collecté" if outcomes.get(sid, {}).get("ok") and not outcomes.get(sid, {}).get("parse_error") else "Indisponible"}</span></li>' for sid in (run.get("enabled_rss") or list(outcomes)))
    status_label = "État des sources inconnu" if not status["total"] else "Collecte indisponible" if not status["ok"] else "Collecte à actualiser" if status["stale"] else "Collecte partielle" if status["partial"] else "Dernière collecte"
    coverage = f'{status["ok"]} flux disponibles sur {status["total"]}' if status["total"] else 'État des sources inconnu'
    empty = '<p class="no-data">Aucun article récent avec une date de publication exploitable. Consultez les sources officielles ci-dessous.</p>' if not rows else ''
    change_html = change_section(ledger)
    dossier_html = dossiers_section(issues, eligible, edge_streets, edge_issues)
    glance = digest_html(rows, status, ledger, roadworks, issues, now, has_changes=bool(change_html))
    # The edition stamp is the article collection, never the render clock: an
    # hourly roads-only re-render must not relabel the edition as new.
    edition_at = status.get("at") or generated_at
    return f'''<!doctype html>
<html lang="fr-CA"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="{SITE_DESCRIPTION}">
<link rel="canonical" href="{SITE_CANONICAL}">
<meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1">
<meta property="og:type" content="website"><meta property="og:site_name" content="Vigie"><meta property="og:locale" content="fr_CA">
<meta property="og:title" content="{SITE_TITLE}"><meta property="og:description" content="{SITE_DESCRIPTION}"><meta property="og:url" content="{SITE_CANONICAL}">
<meta name="twitter:card" content="summary"><meta name="twitter:title" content="{SITE_TITLE}"><meta name="twitter:description" content="{SITE_DESCRIPTION}">
<meta name="theme-color" content="#f5f8f8" media="(prefers-color-scheme: light)"><meta name="theme-color" content="#0e1518" media="(prefers-color-scheme: dark)"><meta name="color-scheme" content="light dark"><meta name="referrer" content="no-referrer">
<title>{SITE_TITLE}</title><link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="/assets/fonts.css"><link rel="stylesheet" href="/assets/brief.css"><script src="/assets/brief.js" defer></script></head>
<body><a class="skip-link" href="#essentiel">Aller aux nouvelles</a>
<header class="masthead"><a class="wordmark" href="/" aria-label="Vigie, accueil"><svg width="28" height="32" viewBox="0 0 28 32" aria-hidden="true"><path d="M2 5 14 28 26 5M8 5l6 12 6-12" fill="none" stroke="currentColor" stroke-width="2.5"/></svg>vigie<span class="wordmark-dot">.</span></a><span class="edition">QUÉBEC, À HAUTEUR DE VIE</span><nav aria-label="Navigation principale"><a href="#essentiel">Le point</a><a href="#dossiers">Les dossiers</a><a href="#agir">Repères utiles</a><a href="#methode">Notre méthode</a></nav><button class="cmdk-open js-only" type="button" id="cmdk-open" aria-haspopup="dialog" aria-controls="cmdk">Recherche rapide <kbd>Ctrl K</kbd></button></header>
<main><section class="intro" aria-labelledby="intro-title"><div><p class="eyebrow">UNE VILLE. VOTRE QUOTIDIEN.</p><h1 id="intro-title">Moins de bruit.<br><em>Plus de Québec.</em></h1><p class="intro-text">Les nouvelles locales. Les sources pour comprendre. Les repères pour agir. Puis, reprenez votre journée.</p></div>
<aside class="edition-note" aria-label="Fraîcheur des informations"><div class="compass" aria-hidden="true"><span>N</span><svg viewBox="0 0 120 120"><circle cx="60" cy="60" r="43"/><path d="M60 5v22M60 93v22M5 60h22M93 60h22M60 31l13 42-13-8-13 8Z"/></svg></div><p class="eyebrow">LE POINT DE REPÈRE</p><p id="freshness-label" role="status" class="freshness{' warning' if status['stale'] or status['partial'] else ''}" data-fetched="{esc(status['at'])}" data-partial="{str(status['partial']).lower()}" data-total="{status['total']}" data-ok="{status['ok']}">{status_label}</p><p class="edition-time">{date_html(status['at'], fallback='Aucune collecte horodatée')}</p><a class="coverage-link" href="#couverture">{coverage} <span aria-hidden="true">↗</span></a><p class="fine">Un instantané des sources. Pas un service d’alerte en temps réel.</p></aside></section>
<section class="brief" id="essentiel" aria-labelledby="brief-title"><div class="section-top"><div><p class="eyebrow">L’ESSENTIEL, À VOTRE ÉCHELLE</p><h2 id="brief-title">Faire le point.</h2></div><p class="section-note">7 jours de publications.<br>Édition du {date_html(edition_at)}.</p></div>
{glance}
<div class="visit-strip js-only"><p id="visit-status" role="status">Une première visite ? Prenez vos repères.</p><button id="remember" type="button">Mémoriser ce point de lecture</button><ul class="visit-list" id="visit-list" hidden></ul></div>
<div class="controls js-only"><div class="view-tabs" role="group" aria-label="Vue des articles"><button type="button" data-view="brief" aria-pressed="true">Le point local</button><button type="button" data-view="new" aria-pressed="false">Depuis mon repère <span id="new-count"></span></button><button type="button" data-view="saved" aria-pressed="false">Mes articles gardés <span id="saved-count"></span></button></div>
<div class="search-row"><label class="search-label"><span class="sr-only">Rechercher dans les titres et extraits</span><svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="8" cy="8" r="5.5"/><path d="m12 12 5 5"/></svg><input id="search" type="search" placeholder="Une rue, un sujet, un nom…" autocomplete="off" maxlength="200"></label><label class="select-label"><span>Territoire</span><select id="scope"><option value="local">Québec et environs</option><option value="province">Tout le Québec</option><option value="all">Tous les flux</option></select></label><label class="select-label"><span>Lieu mentionné</span><select id="area"><option value="all">Tous les lieux</option>{areas}</select></label></div><div class="topic-filters" role="group" aria-label="Thème des articles">{filters}</div><p class="filter-note">Les lieux et thèmes sont repérés automatiquement. Un lieu absent d’un extrait peut échapper au filtre.</p></div>
<noscript><p class="notice">Tous les articles récents sont affichés. La recherche et les repères personnels nécessitent JavaScript.</p></noscript>
<div class="results-bar"><p id="result-count" role="status">{len(rows)} articles récents dans les flux collectés</p><button class="text-button js-only" type="button" id="reset-filters">Réinitialiser les filtres</button></div><div id="stories">{stories}{empty}</div>
<div id="no-results" class="no-data" hidden><h3>Aucun article dans cette vue.</h3><p>Essayez un autre lieu ou élargissez le territoire. Une absence dans nos flux ne signifie pas qu’il ne se passe rien.</p><button type="button" id="empty-reset">Voir le point local</button></div>
<div class="brief-end"><p id="end-note">Vous avez fait le tour de cette sélection.</p><button class="js-only" id="show-more" type="button">Voir les autres articles</button><span class="fine">Pas de défilement infini. Revenez quand vous en avez besoin.</span></div></section>
{rw_html}
{change_html}
{dossier_html}
<section class="services" id="agir" aria-labelledby="services-title"><div class="section-top"><div><p class="eyebrow">L’INFORMATION DEVIENT UTILE</p><h2 id="services-title">Et maintenant ?</h2></div><p class="section-note">Quatre accès directs<br>aux services officiels.</p></div><div class="service-grid">{service_html}</div><p class="fine">Ces liens ouvrent les services officiels. Leurs avis ne sont pas collectés par Vigie.</p></section>
<section class="method" id="methode" aria-labelledby="method-title"><div><p class="eyebrow">LA CONFIANCE SE VÉRIFIE</p><h2 id="method-title">Les sources d’abord.<br>Le jugement vous appartient.</h2><p>Vigie rassemble des titres et des extraits. Il ne réécrit pas l’actualité et ne décide pas de ce qui est vrai à votre place.</p></div><div class="method-details"><details><summary>Comment les articles sont-ils choisis ?</summary><p>Proximité géographique (60 %) et fraîcheur de publication (40 %). La fraîcheur diminue de moitié après 36 heures. Seuls les articles datés des 7 jours précédant cette édition entrent dans ce point. Aucun poids pour les clics ou la publicité.</p><p>{excluded} articles écartés de ce point : trop anciens, date absente ou invalide, ou lien inexploitable. Les filtres changent la sélection, jamais l’ordre public.</p><a href="/ranking.md">Lire le classement publié ↗</a></details><details id="couverture"><summary>Quelles sont les limites de la couverture ?</summary><p>{coverage}. Collecte : {date_html(status['at'])}. Un flux peut omettre des articles, être tronqué ou indisponible. Cette liste n’est pas toute l’actualité de Québec.</p><ul class="coverage-list">{source_rows}</ul><a href="/sources.yaml">Consulter la liste des sources ↗</a></details><details><summary>Mes repères restent-ils privés ?</summary><p>Les articles gardés, votre point de lecture et vos corridors restent sur cet appareil, dans ce navigateur. Aucun compte, suivi publicitaire ou accès à votre position. Les recherches restent dans la page. Les sites sources ont leurs propres pratiques.</p><p>Les images d’aperçu proviennent des éditeurs (og:image ou média attaché à leur propre flux) : Vigie les récupère au moment de la collecte et les sert depuis ce site — votre navigateur ne contacte aucun éditeur en lisant ce point. Un article dont l’éditeur ne publie pas d’image reste sans image : aucune image n’est inventée.</p><button id="clear-local" type="button" class="js-only">Effacer mes repères sur cet appareil</button><p id="privacy-status" role="status"></p></details><details><summary>Qui finance Vigie ?</summary><p>Le projet est actuellement financé par son fondateur. Aucun achat de placement dans le classement.</p><a href="/RENT.md">Lire le financement déclaré ↗</a></details><details><summary>Explorer le prototype et ses dossiers</summary><p>L’atelier conserve les comparaisons de sources et la méthode expérimentale. Les regroupements sont proposés, les contradictions et l’indépendance des sources ne sont pas établies.</p><a href="/explorer.html">Ouvrir l’atelier de recherche ↗</a></details></div></section></main>
<footer><a class="wordmark" href="/">vigie<span class="wordmark-dot">.</span></a><p>Un peu plus au courant.<br>Un peu plus libre de votre temps.</p><span>Fait pour Québec.<br>Édition expérimentale.</span><a class="legal-link" href="/legal.md">Mentions légales, attribution et retrait</a></footer><div class="cmdk js-only" id="cmdk" hidden role="dialog" aria-modal="true" aria-labelledby="cmdk-title"><div class="cmdk-panel"><h2 id="cmdk-title" class="sr-only">Recherche rapide</h2><input id="cmdk-input" class="cmdk-input" type="text" role="combobox" aria-expanded="true" aria-controls="cmdk-list" aria-autocomplete="list" placeholder="Chercher un article, un lieu, une section…" autocomplete="off" maxlength="200"><ul id="cmdk-list" class="cmdk-list" role="listbox" aria-label="Résultats"></ul><p class="cmdk-hint">Entrée pour ouvrir · Échap pour fermer · Ctrl ou ⌘ + K</p></div></div><div id="toast" role="status" aria-live="polite"></div></body></html>'''
