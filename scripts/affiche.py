"""L'affiche — the neighbourhood sheet. One page. Paper first.

The people most touched by roadworks, housing and hydro notices are the least
likely to open a French-first static site or to own an agent. A sheet taped in
a dépanneur, a laundromat or a bus shelter cannot be scraped, de-ranked or
disintermediated. It carries only what fits: verbatim titles with publisher
and date, the City's most restrictive obstructions, who spoke and who did not,
and the seal of the edition so anyone can check that the sheet on the wall is
the edition that was published.

Print with Ctrl+P (or the browser's print command); the stylesheet does the
layout. Stdlib only, no JavaScript, deterministic.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import registre  # noqa: E402
import resident_brief as brief  # noqa: E402
import store_io  # noqa: E402

OUT_HTML = ROOT / "public" / "affiche.html"
SITE_URL = "https://vigieqc.com"

LEAD_CAP = 7          # citywide stories
AREA_CAP = 3          # stories per quartier
AREAS_SHOWN = 4       # quartiers with the most local mentions
ROADS_CAP = 6
SILENT_NAMES_CAP = 6

esc = brief.esc


def _day(value: object) -> str:
    dt = brief.parse_date(value)
    return f'<time datetime="{dt.isoformat()}">{dt:%d/%m}</time>' if dt else ""


def _story(r: dict) -> str:
    src = esc(r.get("source_name") or r.get("source_id") or "")
    url = brief.safe_url(r.get("url"))
    title = esc(r.get("title"))
    # Attribution/link-out is house law on every surface: a sheet read on a
    # screen must still reach the original. (On paper the wordmark and source
    # line carry the citation.)
    inner = (
        f'<a href="{esc(url)}" rel="noopener noreferrer">{title}</a>'
        if url else title
    )
    return (f'<li><span class="af-title">{inner}</span>'
            f'<span class="af-src">{src} · {_day(r.get("published"))}</span></li>')


def area_blocks(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    """Quartiers ordered by the number of local stories that mention them."""
    per: dict[str, list[dict]] = {k: [] for k in brief.AREAS}
    for r in rows:
        for key in r.get("areas") or []:
            if key in per:
                per[key].append(r)
    ranked = sorted((k for k in per if per[k]), key=lambda k: (-len(per[k]), k))
    return [(brief.AREAS[k][0], per[k][:AREA_CAP]) for k in ranked[:AREAS_SHOWN]]


def roads_rows(roadworks: dict | None) -> list[dict]:
    rw = roadworks if isinstance(roadworks, dict) else {}
    events = rw.get("events")
    if not isinstance(events, list) or not brief.parse_date(rw.get("fetched_at")):
        return []
    events = [e for e in events if isinstance(e, dict) and e.get("event_id")]
    ordered = sorted(events, key=lambda e: str(e.get("event_id")))
    ordered.sort(key=lambda e: str(e.get("update_date") or ""), reverse=True)
    ordered.sort(key=lambda e: brief.rw_sort_rank(e.get("vehicle_impact")))
    return ordered[:ROADS_CAP]


def render_affiche(ranked: list[dict], issues: list[dict], roadworks: dict | None,
                   state: dict, generated_at: str, run: dict | None = None) -> str:
    now = brief.parse_date(generated_at) or datetime.now(timezone.utc)
    rows, _ = brief.prepare_items(ranked or [], now)
    run = brief.latest_run() if run is None else run
    status = brief.collection_status(run, now)
    edition_at = status.get("at") or generated_at
    state = state if isinstance(state, dict) else {}
    seals = state.get("seals") if isinstance(state.get("seals"), list) else []
    seal = seals[-1] if seals else None
    if not (isinstance(seal, dict) and isinstance(seal.get("seq"), int)
            and isinstance(seal.get("root"), str)):
        seal = None

    local = [r for r in rows if r.get("geo") == "quebec-city"]
    lead = local[:LEAD_CAP] if local else rows[:LEAD_CAP]
    lead_html = "".join(_story(r) for r in lead) or '<li><span class="af-title">Aucun article local daté dans cette collecte.</span></li>'

    areas = area_blocks(local)
    areas_html = "".join(
        f'<section class="af-area"><h3>{esc(name)}</h3><ul>{"".join(_story(r) for r in items)}</ul></section>'
        for name, items in areas
    )

    roads = roads_rows(roadworks)
    roads_html = "".join(
        f'<li><span class="af-title">{esc(", ".join(str(x) for x in (e.get("road_names") or []) if isinstance(x, str)) or "Voie non précisée")}</span>'
        f'<span class="af-src">{esc(brief.RW_IMPACT_LABELS.get(str(e.get("vehicle_impact") or ""), brief.RW_IMPACT_LABELS["unknown"]))}'
        + (f' · jusqu’au {_day(e.get("end_date"))}' + (" (estimé)" if e.get("end_date_accuracy") == "estimated" else "") if brief.parse_date(e.get("end_date")) else "")
        + "</span></li>"
        for e in roads
    )
    rw_fetched = brief.parse_date((roadworks or {}).get("fetched_at")) if isinstance(roadworks, dict) else None

    register = registre.institution_register(state)
    spoke = [r for r in register if r["current"] == "spoke"]
    silent = [r for r in register if r["current"] == "silent"]
    silent_names = ", ".join(esc(r["institution_name"]) for r in silent[:SILENT_NAMES_CAP])
    if len(silent) > SILENT_NAMES_CAP:
        rest = len(silent) - SILENT_NAMES_CAP
        silent_names += f" et {rest} autre" + ("s" if rest != 1 else "")
    voices_html = (
        f'<p><strong>{len(spoke)}</strong> institution{"s" if len(spoke) != 1 else ""} suivie{"s" if len(spoke) != 1 else ""} '
        f'{"ont" if len(spoke) != 1 else "a"} parlé dans les dossiers de cette édition ; '
        f'<strong>{len(silent)}</strong> {"n’ont" if len(silent) != 1 else "n’a"} pas parlé'
        + (f" : {silent_names}." if silent_names else ".")
        + "</p>"
        if register else "<p>Registre des voix : pas encore d’édition avec dossiers.</p>"
    )

    seal_html = (
        f'<p class="af-seal">Sceau de l’édition n° {seal["seq"]} · <code>{esc(seal["root"][:16])}…</code><br>'
        f'Vérifiable sur {SITE_URL}/registre.html</p>'
        if seal else f'<p class="af-seal">Édition non scellée. {SITE_URL}/registre.html</p>'
    )

    return f'''<!doctype html>
<html lang="fr-CA"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>L’affiche — Vigie, Québec</title>
<meta name="description" content="La feuille de quartier de Vigie : les nouvelles locales, les entraves déclarées par la Ville et qui a parlé, sur une seule page à imprimer et à afficher.">
<link rel="canonical" href="{SITE_URL}/affiche.html"><meta name="robots" content="index, follow"><meta name="referrer" content="no-referrer">
<link rel="describedby" href="/llms.txt">
<meta name="color-scheme" content="light only">
<link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="/assets/fonts.css"><link rel="stylesheet" href="/assets/affiche.css"></head>
<body>
<p class="af-screen-only"><a href="/">← Le point</a> · Cette page est faite pour le papier : imprimez-la (Ctrl + P), affichez-la, photographiez-la. Une feuille par édition.</p>
<main class="af-sheet">
<header class="af-head"><div class="af-brand"><svg width="34" height="38" viewBox="0 0 28 32" aria-hidden="true"><path d="M2 5 14 28 26 5M8 5l6 12 6-12" fill="none" stroke="currentColor" stroke-width="2.5"/></svg><span>vigie.</span></div><div class="af-ed"><p class="af-eyebrow">L’AFFICHE DE QUARTIER · QUÉBEC</p><h1>Ce qui touche votre semaine.</h1><p class="af-date">Édition du {brief.date_html(edition_at, fallback="collecte non horodatée")} · {status.get("ok", 0)} flux sur {status.get("total", 0)}</p></div></header>
<section class="af-lead"><h2>À Québec</h2><ol>{lead_html}</ol></section>
<div class="af-areas">{areas_html}</div>
<section class="af-roads"><h2>Entraves déclarées par la Ville</h2>{f'<ul>{roads_html}</ul><p class="af-fine">Collecte officielle du {brief.date_html(rw_fetched.isoformat())}. Retiré du flux ≠ terminé. Carte : carte.ville.quebec.qc.ca</p>' if roads else '<p class="af-fine">Aucune entrave déclarée dans la collecte, ou flux officiel indisponible.</p>'}</section>
<section class="af-voices"><h2>Qui a parlé, qui n’a pas parlé</h2>{voices_html}<p class="af-fine">« N’a pas parlé » = absent des flux que Vigie suit dans cette édition. Ce n’est pas la preuve d’un silence ailleurs.</p></section>
<footer class="af-foot"><p>Titres cités tels quels, propriété de leurs éditeurs. Vigie ne réécrit rien et ne vend rien. Liste des sources et méthode : {SITE_URL}</p>{seal_html}</footer>
</main></body></html>'''


def emit(ranked: list[dict], issues: list[dict], roadworks: dict | None, state: dict,
         generated_at: str, *, run: dict | None = None, out_html: Path = OUT_HTML) -> None:
    out_html.parent.mkdir(parents=True, exist_ok=True)
    store_io.write_text_atomic(out_html, render_affiche(ranked, issues, roadworks, state, generated_at, run))
    print(f"affiche -> {out_html}")


def main() -> int:
    ranked_doc = registre.load_json(ROOT / "data" / "normalized" / "latest_ranked.json")
    issues_doc = registre.load_json(registre.ISSUES)
    emit(
        ranked_doc.get("candidates") or [],
        issues_doc.get("issues") if isinstance(issues_doc.get("issues"), list) else [],
        registre.load_json(registre.ROADWORKS),
        registre.load_state(),
        str(ranked_doc.get("ranked_at") or datetime.now(timezone.utc).isoformat()),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
