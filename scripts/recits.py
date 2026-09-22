"""Les Récits — the complete, addressable record of one dossier.

The brief shows a dossier in brief: capped voices, capped headlines, progressive
disclosure. A récit page is the whole dossier as its own record page — every
voice with every verbatim headline, the full silence roster, the collection
timeline, the measured evidence and the checkable units — linkable, shareable,
printable, zero JavaScript. Same stores as the brief and the registre: no
second brain, no new retention, no new ranking.

House law holds here exactly as on the front door: titles verbatim, attribution
on every item, grouping stays a rapprochement (never a contradiction), absence
is never a resolution, and several media are never several independent
confirmations. Pages exist only for dossiers of the current edition: a dossier
absent from this collection loses its page on the next render, while its
tracking counters stay in the durable history and the registre — no publisher
text lingers beyond the edition window.

Usage:
  python scripts/recits.py   # emit public/dossiers.html + public/dossiers/*.html
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import resident_brief as brief  # noqa: E402
import promesse  # noqa: E402
import store_io  # noqa: E402

METHOD = "recits-v1"
OUT_DIR = ROOT / "public" / "dossiers"
OUT_INDEX = ROOT / "public" / "dossiers.html"

_WORDMARK_SVG = (
    '<svg width="28" height="32" viewBox="0 0 28 32" aria-hidden="true">'
    '<path d="M2 5 14 28 26 5M8 5l6 12 6-12" fill="none" stroke="currentColor" stroke-width="2.5"/>'
    "</svg>"
)


def _ledger_entry(ledger: dict, issue_id: str) -> dict | None:
    """This dossier's row in the change ledger, if the ledger recorded one."""
    ledger = ledger if isinstance(ledger, dict) else {}
    for key in ("new", "developed", "quiet"):
        for entry in ledger.get(key) or []:
            if isinstance(entry, dict) and str(entry.get("issue_id")) == issue_id:
                return {**entry, "change": key}
    return None


def _status_html(iss: dict, ledger_entry: dict | None) -> str:
    """Glance chips: nest, voices, silence, remix, ledger change. Facts only."""
    chips: list[str] = []
    nest = brief.dossier_nest(iss.get("geo_focus"))
    chips.append(f'<span class="recit-chip">{brief.esc(brief.NEST_LABELS.get(nest, nest))}</span>')
    spoke = brief.safe_int(iss.get("source_count"))
    chips.append(
        f'<span class="recit-chip">{"1 institution a parlé" if spoke == 1 else f"{spoke} institutions ont parlé"}</span>'
    )
    official = brief.safe_int(iss.get("official_voice_count"))
    if official:
        chips.append(
            f'<span class="recit-chip official">{official} voix officielle{"s" if official != 1 else ""}</span>'
        )
    silence = iss.get("silence") if isinstance(iss.get("silence"), dict) else {}
    silent = brief.safe_int(silence.get("silent_count"), len(silence.get("silent") or []))
    chips.append(f'<span class="recit-chip">{silent} n’ont pas parlé dans cette collecte</span>')
    if iss.get("media_remix"):
        chips.append('<span class="recit-chip developed">reprise médiatique seulement</span>')
    if ledger_entry:
        change = ledger_entry.get("change")
        if change == "new":
            chips.append('<span class="recit-chip new">Nouveau</span>')
        elif change == "developed":
            delta_text = brief._delta_text(ledger_entry.get("delta"))
            chips.append(
                '<span class="recit-chip developed">Développé'
                + (f" · {brief.esc(delta_text)}" if delta_text else "")
                + "</span>"
            )
        elif change == "quiet":
            chips.append('<span class="recit-chip quiet">Disparu de cette collecte</span>')
    return "".join(chips)


def _voice_block(tension: dict) -> str:
    """One institution, every usable verbatim headline, claims quoted as stored."""
    tension = tension if isinstance(tension, dict) else {}
    name = brief.plain(tension.get("institution_name") or tension.get("source_name") or "Source")[:120]
    kind = str(tension.get("source_kind") or "").lower()
    chip = '<span class="recit-official">officiel</span>' if kind == "official" else ""
    rows: list[str] = []
    for entry in tension.get("items") or []:
        if not isinstance(entry, dict):
            continue
        url = brief.safe_url(entry.get("url"))
        if not url:
            continue
        title = brief.plain(entry.get("title"))
        if len(title) > brief.TITLE_CAP:
            # The ellipsis counts: a récit title stays within the published cap.
            title = title[: brief.TITLE_CAP - 1].rstrip() + "…"
        if not title:
            continue
        source = brief.esc(entry.get("source_name") or entry.get("source_id") or "")
        when = brief.date_html(entry.get("published_at"), fallback="Date non précisée")
        claims: list[str] = []
        for claim in (entry.get("claims") or [])[:3]:
            if not isinstance(claim, dict):
                continue
            quote = brief.plain(claim.get("quote"))[:160]
            if not quote:
                continue
            speaker = brief.esc(claim.get("speaker") or "")
            who = f'<span class="recit-claim-speaker">{speaker}</span> — ' if speaker else ""
            claims.append(
                f'<li class="recit-claim">{who}<span>« {brief.esc(quote)} »</span></li>'
            )
        claims_html = f'<ul class="recit-claims">{"".join(claims)}</ul>' if claims else ""
        rows.append(
            '<li class="recit-item">'
            f'<a href="{brief.esc(url)}" rel="noopener noreferrer">{brief.esc(title)}'
            '<span class="arrow" aria-hidden="true"> ↗</span></a>'
            f'<span class="recit-item-meta"><span>{source}</span>{when}</span>'
            f"{claims_html}</li>"
        )
    if not rows:
        return ""
    return (
        '<section class="recit-voice"><div class="recit-voice-head">'
        f'<span class="recit-voice-name">{brief.esc(name)}</span>{chip}'
        f'</div><ul class="recit-items">{"".join(rows)}</ul></section>'
    )


def _evidence_html(iss: dict) -> str:
    """What the collection measured, and what it did not assess."""
    evidence = iss.get("evidence") if isinstance(iss.get("evidence"), dict) else {}
    oldest = evidence.get("publication_oldest")
    latest = evidence.get("publication_latest")
    unknown = brief.safe_int(evidence.get("publication_unknown_count"))
    duplicates = brief.safe_int(evidence.get("duplicate_headline_count"))
    note = brief.plain(evidence.get("note"))[:320]
    lines: list[str] = []
    if oldest or latest:
        lines.append(
            "<dt>Publications</dt><dd>"
            f"{brief.date_html(oldest) if oldest else '—'} → {brief.date_html(latest) if latest else '—'}"
            "</dd>"
        )
    if unknown:
        lines.append(f"<dt>Sans date exploitable</dt><dd>{unknown}</dd>")
    if duplicates:
        label = "titre identique" if duplicates == 1 else "titres identiques"
        lines.append(
            f"<dt>Doublons de titres</dt><dd>{duplicates} {label} relayé"
            f"{'' if duplicates == 1 else 's'} (comptés, jamais fusionnés)</dd>"
        )
    lines.append("<dt>Indépendance des sources</dt><dd>non évaluée</dd>")
    lines.append("<dt>Source officielle = confirmation</dt><dd>non</dd>")
    note_html = f'<p class="fine">{brief.esc(note)}</p>' if note else ""
    return (
        '<section class="recit-section recit-evidence">'
        "<h2>Ce que la collecte a mesuré</h2>"
        f'<dl>{"".join(lines)}</dl>{note_html}</section>'
    )


def _edge_line_html(iss: dict, edge_streets: dict, edge_issues: dict) -> str:
    """Proposed street-level join to the official roadworks, pointing at the brief."""
    record = edge_issues.get(str(iss.get("issue_id") or ""))
    if not isinstance(record, dict):
        return ""
    keys = [
        k for k in (record.get("streets") or [])
        if isinstance(edge_streets.get(k), dict)
    ][:2]
    rows = [
        (str(edge_streets[k].get("display") or k), brief.safe_int(edge_streets[k].get("active_count")))
        for k in keys
    ]
    rows = [(display, count) for display, count in rows if display and count > 0]
    if not rows:
        return ""
    parts = " ; ".join(
        f"{count} entrave{'s' if count != 1 else ''} active{'s' if count != 1 else ''} "
        f"sur « {brief.esc(display)} »"
        for display, count in rows
    )
    return (
        '<p class="dossier-edge fine">Rapprochement proposé : la collecte officielle '
        f"déclare {parts} — rue{'s' if len(rows) != 1 else ''} mentionnée{'s' if len(rows) != 1 else ''} "
        'dans ce dossier. <a href="/#travaux">Voir les entraves</a> · '
        '<a href="/methode/rues.html">méthode</a>. Un nom de rue partagé, pas une preuve '
        "géographique.</p>"
    )


def render_recit(iss: dict, eligible: dict, ledger: dict, *, slug: str,
                 rw_ok: bool, edge_streets: dict, edge_issues: dict) -> str:
    """The complete record page of one dossier. Deterministic, zero scripts."""
    iss = iss if isinstance(iss, dict) else {}
    question = brief.plain(iss.get("question"))[:300] or "Sujet suivi"
    label_kind = str(iss.get("label_kind") or "")
    attributed = label_kind == "attributed_headline"
    label_source = iss.get("label_source") if isinstance(iss.get("label_source"), dict) else {}
    ledger_entry = _ledger_entry(ledger, str(iss.get("issue_id") or ""))
    tensions = [t for t in (iss.get("tensions") or []) if isinstance(t, dict)]
    spoke_names = {
        brief.plain(t.get("institution_name") or t.get("source_name") or "")
        for t in tensions
    }
    spoke_names.discard("")
    spoke_count = brief.safe_int(iss.get("source_count"), len(spoke_names))
    voice_blocks = "".join(block for t in tensions if (block := _voice_block(t)))
    attrib_html = ""
    if attributed:
        owner = brief.plain(label_source.get("source_name"))[:120]
        attrib_html = (
            '<p class="recit-attrib">Titre d’un éditeur, cité tel quel'
            + (f" — {brief.esc(owner)}" if owner else "")
            + ".</p>"
        )
    why = (
        '<p class="recit-why">Pourquoi ici : rapprochement proposé — '
        f"{spoke_count} institution{'s' if spoke_count != 1 else ''}, même sujet. "
        "Pas une contradiction, pas un verdict.</p>"
    )
    remix_warning = (
        '<p class="dossier-remix fine">Aucune source officielle sur ce dossier — '
        "rapprochement de médias seulement.</p>"
        if iss.get("media_remix") else ""
    )
    units = brief.dossier_units(iss, eligible)
    units_line = (
        '<p class="dossier-units">Repères à vérifier : '
        + "".join(f'<span class="unit">{brief.esc(unit[:48])}</span>' for unit in units)
        + "</p>"
    ) if units else ""
    roster = brief.dossier_voices_html(iss)
    roster_section = (
        '<section class="recit-section recit-roster">'
        "<h2>Toutes les institutions suivies</h2>"
        f"{roster}</section>"
    ) if roster else ""
    edge_line = _edge_line_html(iss, edge_streets, edge_issues) if rw_ok and edge_streets else ""
    extra = units_line + edge_line
    extra_section = (
        f'<section class="recit-section recit-extra">{extra}</section>'
        if extra else ""
    )
    clustered = brief.date_html(iss.get("clustered_at"), fallback="date non précisée")
    title_attr = question + (" (titre d’un éditeur)" if attributed else "")
    description = (
        "Dossier proposé par Vigie : " + question[:160]
        + " Rapprochement automatique à vérifier ; pas une contradiction, pas un verdict."
    )
    page_url = f"{brief.SITE_URL}/dossiers/{brief.esc(slug)}.html"
    return f"""<!doctype html>
<html lang="fr-CA"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{brief.esc(title_attr[:120])} — Vigie</title>
<meta name="description" content="{brief.esc(description[:260])}">
<link rel="canonical" href="{page_url}"><meta name="robots" content="index, follow">
<link rel="describedby" href="/llms.txt">
<meta property="og:type" content="article"><meta property="og:site_name" content="Vigie"><meta property="og:locale" content="fr_CA">
<meta property="og:title" content="{brief.esc(title_attr[:120])}"><meta property="og:description" content="{brief.esc(question[:160])}"><meta property="og:url" content="{page_url}">
<meta property="og:image" content="{brief.SITE_OG_IMAGE}">
<meta name="twitter:card" content="summary"><meta name="twitter:title" content="{brief.esc(title_attr[:120])}"><meta name="twitter:description" content="{brief.esc(question[:160])}">
<meta name="theme-color" content="#f5f8f8" media="(prefers-color-scheme: light)"><meta name="theme-color" content="#0e1518" media="(prefers-color-scheme: dark)"><meta name="color-scheme" content="light dark"><meta name="referrer" content="no-referrer">
<link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="/assets/fonts.css"><link rel="stylesheet" href="/assets/brief.css"></head>
<body class="recit-body"><a class="skip-link" href="#recit">Aller au dossier</a>
<header class="masthead"><a class="wordmark" href="/" aria-label="Vigie, accueil">{_WORDMARK_SVG}vigie<span class="wordmark-dot">.</span></a><span class="edition">LE REGARD CROISÉ</span><nav aria-label="Navigation principale"><a href="/">Le point</a><a href="/dossiers.html">Tous les dossiers</a><a href="/registre.html">Le registre</a><a href="/methode/classement.html">Méthode</a></nav></header>
<main id="recit">
<header class="recit-head"><p class="eyebrow">DOSSIER PROPOSÉ — ÉDITION COURANTE</p>
<h1 class="recit-title">{brief.esc(question)}</h1>{attrib_html}
<div class="recit-meta">{_status_html(iss, ledger_entry)}</div>{why}{promesse.html_of(iss)}{remix_warning}</header>
{brief.tracking_html(iss)}{brief.dossier_timeline_html(iss)}
<section class="recit-section recit-voices"><h2>Les voix, côte à côte</h2>{voice_blocks or '<p class="no-data">Aucun article exploitable dans ce dossier cette édition.</p>'}</section>
{roster_section}
{_evidence_html(iss)}
{extra_section}
<p class="recit-clock">Collecte du {clustered}. Cette page est celle de l’édition courante : un dossier absent de la collecte n’a pas de page — une absence n’est pas une résolution. Les compteurs de suivi restent dans <a href="/registre.html">le registre</a> et <a href="/memoire.html">la mémoire</a>.</p>
</main>
<footer><a class="wordmark" href="/">vigie<span class="wordmark-dot">.</span></a><p>Un peu plus au courant.<br>Un peu plus libre de votre temps.</p><span>Fait pour Québec.<br>Dossier de l’édition courante.</span><a class="legal-link" href="/methode/legal.html">Mentions légales, attribution et retrait</a></footer></body></html>
"""


def render_index(dossiers: list[dict], slug_of: dict[str, str]) -> str:
    """The complete list of this edition's dossiers, each linked to its record page."""
    items: list[str] = []
    for iss in dossiers:
        iid = str(iss.get("issue_id") or "")
        slug = slug_of.get(iid) or brief.dossier_slug(iss)
        question = brief.plain(iss.get("question"))[:220] or "Sujet suivi"
        spoke = brief.safe_int(iss.get("source_count"))
        silence = iss.get("silence") if isinstance(iss.get("silence"), dict) else {}
        silent = brief.safe_int(silence.get("silent_count"), len(silence.get("silent") or []))
        official = brief.safe_int(iss.get("official_voice_count"))
        meta = (
            "1 institution a parlé" if spoke == 1 else f"{spoke} institutions ont parlé"
        ) + f" · {silent} n’ont pas parlé dans cette collecte"
        if official:
            meta += f" · {official} voix officielle{'s' if official != 1 else ''}"
        tracking = iss.get("tracking") if isinstance(iss.get("tracking"), dict) else {}
        seen = brief.safe_int(tracking.get("editions_seen"))
        if seen > 1:
            meta += f" · suivi depuis {brief.date_html(tracking.get('first_seen'), fallback='date non précisée')}"
        elif seen == 1:
            meta += " · suivi depuis cette édition"
        items.append(
            '<article class="recit-index-item">'
            f'<h2 class="recit-index-title"><a href="/dossiers/{brief.esc(slug)}.html">{brief.esc(question)}'
            '<span class="arrow" aria-hidden="true"> ↗</span></a></h2>'
            f'<p class="recit-index-meta">{meta}</p>'
            f'<a class="recit-more" href="/dossiers/{brief.esc(slug)}.html">Récit complet ↗</a>'
            "</article>"
        )
    listing = "".join(items) or (
        '<p class="no-data">Aucun dossier cette édition. Cela ne dit rien de la '
        "couverture ailleurs — et un rapprochement n’est jamais une contradiction.</p>"
    )
    count = len(dossiers)
    count_note = (
        f"{count} dossier{'s' if count != 1 else ''} proposé{'s' if count != 1 else ''}"
        "<br>cette édition."
    )
    return f"""<!doctype html>
<html lang="fr-CA"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Tous les dossiers — Vigie</title>
<meta name="description" content="Tous les dossiers proposés de l’édition courante de Vigie : chaque dossier sur sa propre page, toutes les voix et tous les titres tels quels.">
<link rel="canonical" href="{brief.SITE_URL}/dossiers.html"><meta name="robots" content="index, follow">
<link rel="describedby" href="/llms.txt">
<meta property="og:type" content="website"><meta property="og:site_name" content="Vigie"><meta property="og:locale" content="fr_CA">
<meta property="og:title" content="Tous les dossiers — Vigie"><meta property="og:description" content="Chaque dossier de l’édition sur sa propre page : toutes les voix, tous les titres tels quels."><meta property="og:url" content="{brief.SITE_URL}/dossiers.html">
<meta property="og:image" content="{brief.SITE_OG_IMAGE}">
<meta name="twitter:card" content="summary"><meta name="twitter:title" content="Tous les dossiers — Vigie">
<meta name="theme-color" content="#f5f8f8" media="(prefers-color-scheme: light)"><meta name="theme-color" content="#0e1518" media="(prefers-color-scheme: dark)"><meta name="color-scheme" content="light dark"><meta name="referrer" content="no-referrer">
<link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="/assets/fonts.css"><link rel="stylesheet" href="/assets/brief.css"></head>
<body class="recit-body"><a class="skip-link" href="#dossiers">Aller aux dossiers</a>
<header class="masthead"><a class="wordmark" href="/" aria-label="Vigie, accueil">{_WORDMARK_SVG}vigie<span class="wordmark-dot">.</span></a><span class="edition">LE REGARD CROISÉ</span><nav aria-label="Navigation principale"><a href="/">Le point</a><a href="/dossiers.html">Tous les dossiers</a><a href="/registre.html">Le registre</a><a href="/methode/classement.html">Méthode</a></nav></header>
<main id="dossiers">
<header class="recit-head"><p class="eyebrow">REGARD CROISÉ — VERSION COMPLÈTE</p>
<h1 class="recit-title">Tous les dossiers, en entier.</h1>
<p class="recit-attrib">Chaque dossier de cette édition a sa propre page : toutes les voix, tous les titres tels quels, le suivi de collecte et qui n’a pas parlé.</p></header>
<section class="recit-index"><div class="section-top"><div><h2 class="recit-section-title">Les dossiers de l’édition</h2></div><p class="section-note">{count_note}</p></div>{listing}</section>
<p class="recit-clock">Un rapprochement n’est pas une contradiction ; plusieurs médias ne sont pas plusieurs confirmations indépendantes. Les pages existent pour l’édition courante seulement.</p>
</main>
<footer><a class="wordmark" href="/">vigie<span class="wordmark-dot">.</span></a><p>Un peu plus au courant.<br>Un peu plus libre de votre temps.</p><span>Fait pour Québec.<br>Dossiers de l’édition courante.</span><a class="legal-link" href="/methode/legal.html">Mentions légales, attribution et retrait</a></footer></body></html>
"""


def emit(issues: list[dict], ranked: list[dict], ledger: dict, roadworks: dict,
         edges: dict, *, out_dir: Path = OUT_DIR, out_index: Path = OUT_INDEX) -> dict:
    """Write the index and one record page per current-edition dossier.

    Stale pages are pruned (retention law): a quiet dossier keeps its counters
    in the durable history and the registre, never its page. Deterministic and
    fail-soft: malformed entries are skipped, a corrupt store yields an empty
    index, and nothing here raises.
    """
    dossiers = [
        i for i in (issues or [])
        if isinstance(i, dict) and i.get("issue_id")
    ]
    eligible = {
        str(c.get("id")): c for c in (ranked or [])
        if isinstance(c, dict) and c.get("id")
    }
    rw = roadworks if isinstance(roadworks, dict) else {}
    rw_ok = (
        isinstance(rw.get("events"), list)
        and brief.parse_date(rw.get("fetched_at")) is not None
    )
    if rw_ok:
        edge_streets, edge_issues = brief.valid_edges(edges)
    else:
        edge_streets, edge_issues = {}, {}
    out_dir.mkdir(parents=True, exist_ok=True)
    slug_of: dict[str, str] = {}
    written: set[str] = set()
    for iss in sorted(dossiers, key=lambda i: str(i.get("issue_id") or "")):
        iid = str(iss.get("issue_id") or "")
        slug = brief.dossier_slug(iss)
        slug_of[iid] = slug
        if slug in written:
            continue  # duplicate issue_id in a malformed store: keep the first page
        page = render_recit(
            iss, eligible, ledger, slug=slug,
            rw_ok=rw_ok, edge_streets=edge_streets, edge_issues=edge_issues,
        )
        store_io.write_text_atomic(out_dir / f"{slug}.html", page)
        written.add(f"{slug}.html")
    for stale in sorted(out_dir.glob("*.html")):
        if stale.name not in written:
            try:
                stale.unlink()
            except OSError:
                pass
    store_io.write_text_atomic(out_index, render_index(dossiers, slug_of))
    print(f"recits: {len(dossiers)} dossiers, {len(written)} pages -> {out_index}")
    return {"method": METHOD, "dossiers": len(dossiers), "pages": len(written)}


def main() -> int:
    import registre

    issues_doc = registre.load_json(ROOT / "data" / "issues" / "latest_issues.json")
    ranked_doc = registre.load_json(ROOT / "data" / "normalized" / "latest_ranked.json")
    emit(
        issues_doc.get("issues") if isinstance(issues_doc.get("issues"), list) else [],
        ranked_doc.get("candidates") or [],
        issues_doc.get("change_ledger") if isinstance(issues_doc.get("change_ledger"), dict) else {},
        registre.load_json(registre.ROADWORKS),
        registre.load_json(ROOT / "data" / "edges" / "latest_edges.json"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
