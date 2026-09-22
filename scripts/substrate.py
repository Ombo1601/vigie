"""The substrate — Vigie for machines, without a second brain.

When nobody opens a browser, Vigie survives as the memory that stateless
agents do not have: what changed since a cursor, who spoke, who did not, and
where the official record stands. Three artefacts, all derived from the same
stores as the brief, all deterministic, all attribution-preserving:

  public/llms.txt          curated map for agents (llms.txt v2; rel="describedby")
  public/index.html.md     Markdown twin of the front door (rel="alternate")
  public/delta/latest.json cursor-addressed edition delta (delta-v1)

House law holds for machines exactly as for people: titles verbatim, source
URL and publisher name on every item, no rewriting, no excerpt beyond the
publisher's own summary (capped), silence stated as absence in the collected
feeds. Stdlib only; no network.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import registre  # noqa: E402
import resident_brief as brief  # noqa: E402
import store_io  # noqa: E402

METHOD = "delta-v1 edition-cursor"
SITE_URL = "https://vigieqc.com"
OUT_LLMS = ROOT / "public" / "llms.txt"
OUT_MD = ROOT / "public" / "index.html.md"
OUT_DELTA = ROOT / "public" / "delta" / "latest.json"

MD_STORIES_CAP = 24
MD_DOSSIER_ITEMS_CAP = 6
MD_ROADS_CAP = 10
DELTA_ITEMS_CAP = 5
SUMMARY_CAP = 280


def _plain(value: object, cap: int | None = None) -> str:
    """Verbatim words, truncated. The ellipsis counts, so the result never
    exceeds `cap` — the same ≤cap law the brief's own cards follow."""
    text = brief.plain(value)
    if cap is not None and len(text) > cap:
        text = text[:cap - 1].rstrip() + "…"
    return text


def _md(value: object, cap: int | None = None) -> str:
    """Markdown-safe inline text: verbatim words, neutralised markup characters."""
    text = _plain(value, cap)
    for ch in ("\\", "`", "*", "_", "[", "]", "<", ">", "#", "|"):
        text = text.replace(ch, "\\" + ch)
    return text


def _iso(value: object) -> str:
    dt = brief.parse_date(value)
    return dt.isoformat() if dt else ""


def _day(value: object) -> str:
    dt = brief.parse_date(value)
    return f"{dt:%Y-%m-%d}" if dt else "date non précisée"


# --------------------------------------------------------------------------- #
# Shared facts
# --------------------------------------------------------------------------- #
def voices_of(issue: dict, names: dict) -> tuple[list[dict], list[dict]]:
    spoke: list[dict] = []
    for voice in issue.get("tensions") or []:
        if not isinstance(voice, dict) or not voice.get("institution_id"):
            continue
        spoke.append({
            "institution_id": str(voice["institution_id"]),
            "institution_name": _plain(voice.get("institution_name") or voice["institution_id"], 120),
            "source_kind": "official" if voice.get("source_kind") == "official" else "media",
            "item_count": len([i for i in (voice.get("items") or []) if isinstance(i, dict)]),
        })
    spoke.sort(key=lambda v: (0 if v["source_kind"] == "official" else 1, v["institution_id"]))
    silence = issue.get("silence") if isinstance(issue.get("silence"), dict) else {}
    silent: list[dict] = []
    for row in silence.get("silent") or []:
        if not isinstance(row, dict):
            continue
        iid = str(row.get("institution_id") or row.get("source_id") or "")
        if not iid:
            continue
        meta = names.get(iid) if isinstance(names.get(iid), dict) else {}
        silent.append({
            "institution_id": iid,
            "institution_name": _plain(row.get("institution_name") or row.get("source_name") or meta.get("name") or iid, 120),
            "source_kind": "official" if row.get("source_kind") == "official" else "media",
        })
    silent.sort(key=lambda v: (0 if v["source_kind"] == "official" else 1, v["institution_id"]))
    return spoke, silent


def items_of(issue: dict, cap: int) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for voice in issue.get("tensions") or []:
        if not isinstance(voice, dict):
            continue
        for it in voice.get("items") or []:
            if not isinstance(it, dict):
                continue
            url = brief.safe_url(it.get("url"))
            title = _plain(it.get("title"), brief.TITLE_CAP)
            if not url or not title or url in seen:
                continue
            seen.add(url)
            rows.append({
                "title": title,
                "url": url,
                "source_name": _plain(it.get("source_name") or it.get("source_id") or "", 120),
                "institution_id": str(it.get("institution_id") or ""),
                "published_at": _iso(it.get("published_at")),
            })
    rows.sort(key=lambda r: (r["published_at"] or "", r["url"]), reverse=True)
    return rows[:cap]


def dossier_view(issue: dict, names: dict, cap: int) -> dict:
    spoke, silent = voices_of(issue, names)
    tracking = issue.get("tracking") if isinstance(issue.get("tracking"), dict) else {}
    view = {
        "issue_id": str(issue.get("issue_id") or ""),
        "question": _plain(issue.get("question"), registre.QUESTION_CAP),
        "label_kind": str(issue.get("label_kind") or ""),
        "geo_focus": sorted(str(g) for g in (issue.get("geo_focus") or []) if isinstance(g, str)),
        "item_count": registre._int(issue.get("item_count")),
        "official_voice_count": registre._int(issue.get("official_voice_count")),
        "media_remix": bool(issue.get("media_remix")),
        "spoke": spoke,
        "silent": silent,
        "items": items_of(issue, cap),
    }
    if tracking.get("editions_seen") is not None:
        view["tracking"] = {k: tracking.get(k) for k in ("first_seen", "last_seen", "editions_seen", "editions_missed") if tracking.get(k) is not None}
    # Publisher text must travel with its owner: an attributed dossier exposes
    # the source name + URL so a machine can cite it, never just the headline.
    if view["label_kind"] == "attributed_headline":
        source = issue.get("label_source")
        if isinstance(source, dict):
            view["label_source"] = {
                k: source.get(k) for k in ("source_id", "source_name", "url") if source.get(k)
            }
    return view


def roadworks_view(roadworks: dict | None, cap: int) -> dict | None:
    rw = roadworks if isinstance(roadworks, dict) else {}
    events = rw.get("events")
    fetched = _iso(rw.get("fetched_at"))
    if not isinstance(events, list) or not fetched:
        return None
    events = [e for e in events if isinstance(e, dict) and e.get("event_id")]
    ordered = sorted(events, key=lambda e: str(e.get("event_id")))
    ordered.sort(key=lambda e: str(e.get("update_date") or ""), reverse=True)
    ordered.sort(key=lambda e: brief.RW_SEVERITY.get(str(e.get("vehicle_impact") or ""), 6))
    diff = rw.get("diff") if isinstance(rw.get("diff"), dict) else {}
    return {
        "fetched_at": fetched,
        "active_count": len(events),
        "source": _plain(rw.get("institution_name") or rw.get("source_name") or "", 120),
        "dataset_url": brief.safe_url(rw.get("dataset_url")) or None,
        "license_note": _plain(rw.get("license_note"), 200) or None,
        "diff": {
            "has_previous": bool(diff.get("has_previous")),
            "new_count": registre._int(diff.get("new_count")),
            "removed_count": registre._int(diff.get("removed_count")),
            "changed_count": registre._int(diff.get("changed_count")),
        },
        "most_restrictive": [
            {
                "event_id": str(e.get("event_id")),
                "road_names": [_plain(r, 120) for r in (e.get("road_names") or []) if isinstance(r, str)][:4],
                "vehicle_impact": str(e.get("vehicle_impact") or "unknown"),
                "impact_label": brief.RW_IMPACT_LABELS.get(str(e.get("vehicle_impact") or ""), brief.RW_IMPACT_LABELS["unknown"]),
                "start_date": _iso(e.get("start_date")) or None,
                "end_date": _iso(e.get("end_date")) or None,
                "end_date_accuracy": str(e.get("end_date_accuracy") or "") or None,
            }
            for e in ordered[:cap]
        ],
        "note": "Déclarations officielles de la Ville de Québec, relayées telles quelles. Retiré du flux ≠ terminé.",
    }


def story_rows(ranked: list[dict], now: datetime) -> list[dict]:
    rows, _ = brief.prepare_items(ranked or [], now)
    return rows


# --------------------------------------------------------------------------- #
# delta/latest.json
# --------------------------------------------------------------------------- #
def build_delta(issues: list[dict], ledger: dict | None, roadworks: dict | None,
                state: dict, status: dict) -> dict:
    seals = state.get("seals") or []
    last = seals[-1] if seals else {}
    prev = seals[-2] if len(seals) > 1 else {}
    names = state.get("names") or {}
    by_id = {str(i.get("issue_id")): i for i in issues if isinstance(i, dict) and i.get("issue_id")}
    ledger = ledger if isinstance(ledger, dict) else {}
    has_previous = bool(ledger.get("has_previous"))

    def _bucket(key: str) -> list[dict]:
        out: list[dict] = []
        for entry in ledger.get(key) or []:
            if not isinstance(entry, dict) or not entry.get("issue_id"):
                continue
            iid = str(entry["issue_id"])
            if iid in by_id:
                view = dossier_view(by_id[iid], names, DELTA_ITEMS_CAP)
            else:
                # Quiet dossiers left the collection: identity, label and owner
                # only — never a publisher headline without its source.
                view = {"issue_id": iid, "question": _plain(entry.get("question"), registre.QUESTION_CAP),
                        "label_kind": str(entry.get("label_kind") or ""),
                        "geo_focus": sorted(str(g) for g in (entry.get("geo_focus") or []) if isinstance(g, str))}
                source = entry.get("label_source")
                if isinstance(source, dict) and view["label_kind"] == "attributed_headline":
                    view["label_source"] = {
                        k: source.get(k) for k in ("source_id", "source_name", "url") if source.get(k)
                    }
            if key == "developed" and isinstance(entry.get("delta"), dict):
                view["delta"] = dict(entry["delta"])
            out.append(view)
        out.sort(key=lambda d: d["issue_id"])
        return out

    register = registre.institution_register(state)
    silent_now = [r for r in register if r["current"] == "silent"]
    spoke_now = [r for r in register if r["current"] == "spoke"]
    return {
        "method": METHOD,
        "site": SITE_URL,
        "edition": last.get("edition") or "",
        "previous_edition": prev.get("edition") or "",
        "cursor": last.get("root") or "",
        "previous_cursor": prev.get("root") or last.get("prev") or "",
        "checkpoint": {"seq": last.get("seq"), "root": last.get("root"), "url": f"{SITE_URL}/registre/checkpoint.txt"} if last else None,
        "collection": {
            "at": status.get("at") or "",
            "feeds_ok": status.get("ok"),
            "feeds_total": status.get("total"),
            "partial": bool(status.get("partial")),
        },
        "dossiers": {
            "has_previous": has_previous,
            "new": _bucket("new") if has_previous else [],
            "developed": _bucket("developed") if has_previous else [],
            "quiet": _bucket("quiet") if has_previous else [],
            "all": [dossier_view(by_id[i], names, DELTA_ITEMS_CAP) for i in sorted(by_id)],
        },
        "institutions": {
            "spoke": [{k: r[k] for k in ("institution_id", "institution_name", "source_kind")} for r in spoke_now],
            "silent": [{k: r[k] for k in ("institution_id", "institution_name", "source_kind", "silent_streak", "last_spoke")} for r in silent_now],
        },
        "roadworks": roadworks_view(roadworks, DELTA_ITEMS_CAP),
        "links": {
            "brief": f"{SITE_URL}/",
            "brief_markdown": f"{SITE_URL}/index.html.md",
            "dossiers": f"{SITE_URL}/dossiers.html",
            "depart": f"{SITE_URL}/partir.html",
            "memoire": f"{SITE_URL}/memoire.html",
            "registre": f"{SITE_URL}/registre.html",
            "chain": f"{SITE_URL}/registre/chain.json",
            "institutions": f"{SITE_URL}/registre/institutions.json",
            "sources": f"{SITE_URL}/methode/sources.html",
            "method": f"{SITE_URL}/methode/registre.html",
            "legal": f"{SITE_URL}/methode/legal.html",
        },
        "rules_for_agents": [
            "Cite the original publisher (source_name + url) for every item; Vigie is an index, never the author.",
            "Titles are verbatim publisher titles (truncated only). Do not present Vigie text as a quotation of the publisher.",
            "'silent' means absent from the feeds Vigie collects in this edition — a measured absence, not a statement that the institution said nothing anywhere.",
            "'quiet' dossiers left this collection; nothing is 'resolved' or 'ended'.",
            "Verify this edition against registre/chain.json before treating it as a record.",
        ],
    }


# --------------------------------------------------------------------------- #
# index.html.md
# --------------------------------------------------------------------------- #
def render_markdown(rows: list[dict], issues: list[dict], ledger: dict | None, roadworks: dict | None,
                    state: dict, status: dict) -> str:
    seals = state.get("seals") or []
    last = seals[-1] if seals else {}
    names = state.get("names") or {}
    ledger = ledger if isinstance(ledger, dict) else {}
    lines: list[str] = []
    lines.append("# Vigie — Québec, à hauteur de vie")
    lines.append("")
    lines.append("> Un point local pour la ville de Québec : titres et extraits d’éditeurs cités tels quels, dossiers où plusieurs institutions se répondent, entraves officielles, et le registre de qui a parlé et qui n’a pas parlé. Vigie n’écrit pas la nouvelle et ne décide pas de ce qui est vrai.")
    lines.append("")
    if status.get("at"):
        lines.append(f"- Collecte : {status['at']} — {status.get('ok', 0)} flux disponibles sur {status.get('total', 0)}" + (" (collecte partielle)" if status.get("partial") else ""))
    if last:
        lines.append(f"- Édition scellée n° {last.get('seq')} : `{last.get('root')}` — vérifiable dans [chain.json]({SITE_URL}/registre/chain.json)")
    lines.append(f"- Version HTML : {SITE_URL}/ · Delta machine : {SITE_URL}/delta/latest.json · Carte du site pour agents : {SITE_URL}/llms.txt")
    lines.append("")

    lines.append("## Le point")
    lines.append("")
    if rows:
        for r in rows[:MD_STORIES_CAP]:
            src = _md(r.get("source_name") or r.get("source_id") or "source")
            lines.append(f"- **{_md(r.get('title'))}** — {src}, {_day(r.get('published'))}. <{r['url']}>")
            summary = _plain(r.get("summary"), SUMMARY_CAP)
            if summary:
                lines.append(f"  {_md(summary)}")
    else:
        lines.append("Aucun article récent avec une date de publication exploitable dans cette collecte.")
    lines.append("")

    lines.append("## Ce que les sources racontent ensemble")
    lines.append("")
    dossiers = [i for i in issues if isinstance(i, dict) and i.get("issue_id")]
    if dossiers:
        for iss in dossiers:
            view = dossier_view(iss, names, MD_DOSSIER_ITEMS_CAP)
            heading = _md(view["question"]) or view["issue_id"]
            if view.get("label_kind") == "attributed_headline":
                label_source = iss.get("label_source") if isinstance(iss.get("label_source"), dict) else {}
                owner = _md(label_source.get("source_name") or "", 120)
                heading += f" — titre de {owner}" if owner else " — titre d’un éditeur, cité tel quel"
            lines.append(f"### {heading}")
            spoke = ", ".join(f"{_md(v['institution_name'])}{' (officiel)' if v['source_kind'] == 'official' else ''}" for v in view["spoke"])
            silent = ", ".join(f"{_md(v['institution_name'])}{' (officiel)' if v['source_kind'] == 'official' else ''}" for v in view["silent"])
            lines.append(f"- Ont parlé ({len(view['spoke'])}) : {spoke or '—'}")
            lines.append(f"- N’ont pas parlé dans cette collecte ({len(view['silent'])}) : {silent or '—'}")
            if view.get("media_remix"):
                lines.append("- Aucun texte officiel dans ce dossier : reprise médiatique seulement.")
            for it in view["items"]:
                lines.append(f"- {_md(it['title'])} — {_md(it['source_name'])}, {_day(it['published_at'])}. <{it['url']}>")
            lines.append("")
    else:
        lines.append("Aucun dossier à plusieurs voix dans cette édition.")
        lines.append("")
    if dossiers:
        lines.append(f"Vue complète : {SITE_URL}/dossiers.html (un dossier par page, tous les titres tels quels).")
        lines.append("")

    if ledger.get("has_previous"):
        lines.append("## Depuis la dernière édition")
        lines.append("")
        for key, label in (("new", "Nouveaux dossiers"), ("developed", "Dossiers développés"), ("quiet", "Disparus de cette collecte (jamais « résolus »)")):
            entries = [e for e in (ledger.get(key) or []) if isinstance(e, dict)]
            names_out = []
            for e in entries[:6]:
                if str(e.get("label_kind") or "") == "attributed_headline":
                    src = e.get("label_source") if isinstance(e.get("label_source"), dict) else {}
                    owner = _md(src.get("source_name") or src.get("source_id") or "", 120)
                    names_out.append(f"titre d’un éditeur ({owner})" if owner else "titre d’un éditeur")
                else:
                    names_out.append(_md(e.get("question"), 120))
            lines.append(f"- {label} : {len(entries)}" + (" — " + "; ".join(names_out) if names_out else ""))
        lines.append("")

    rw = roadworks_view(roadworks, MD_ROADS_CAP)
    if rw:
        lines.append("## Travaux et entraves déclarés par la Ville")
        lines.append("")
        lines.append(f"- {rw['active_count']} entraves actives dans la collecte du {rw['fetched_at']}" + (
            f" ; depuis la collecte précédente : +{rw['diff']['new_count']} nouvelles, {rw['diff']['changed_count']} modifiées, {rw['diff']['removed_count']} retirées du flux (retiré ≠ terminé)."
            if rw["diff"]["has_previous"] else "."))
        for e in rw["most_restrictive"]:
            roads = ", ".join(_md(r) for r in e["road_names"]) or "voie non précisée"
            until = f", jusqu’au {_day(e['end_date'])}" + (" (estimé)" if e.get("end_date_accuracy") == "estimated" else "") if e.get("end_date") else ""
            lines.append(f"- {roads} — {e['impact_label']}{until}.")
        if rw.get("dataset_url"):
            lines.append(f"- Source officielle : <{rw['dataset_url']}>")
        lines.append("")

    register = registre.institution_register(state)
    if register:
        lines.append("## Le registre des voix")
        lines.append("")
        for r in register:
            tag = " (officiel)" if r["source_kind"] == "official" else ""
            if r["current"] == "spoke":
                stt = "a parlé dans cette édition"
            elif r["current"] == "silent":
                stt = "n’a pas parlé dans cette édition" if r["silent_streak"] <= 1 else f"n’a pas parlé depuis {r['silent_streak']} éditions"
            else:
                stt = "état non établi"
            lines.append(f"- {_md(r['institution_name'])}{tag} : {stt}.")
        lines.append("")
        lines.append(f"Registre complet et chaîne des éditions : {SITE_URL}/registre.html")
        lines.append("")

    lines.append("## Attribution et règles")
    lines.append("")
    lines.append("- Chaque titre appartient à son éditeur ; citez l’éditeur et son lien, jamais Vigie comme auteur.")
    lines.append("- Le silence est une absence dans les flux collectés par Vigie, pas la preuve qu’une institution n’a rien dit ailleurs.")
    lines.append(f"- Sources suivies : {SITE_URL}/methode/sources.html · Méthode : {SITE_URL}/methode/classement.html, {SITE_URL}/methode/registre.html · Mentions légales et retrait : {SITE_URL}/methode/legal.html")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# llms.txt
# --------------------------------------------------------------------------- #
def render_llms_txt(state: dict, status: dict) -> str:
    seals = state.get("seals") or []
    last = seals[-1] if seals else {}
    edition = last.get("edition") or status.get("at") or ""
    return f"""# Vigie

> Vigie is a free, non-commercial lookout for Québec City (French-first). It aggregates a finite, published list of sources, never rewrites them, groups articles into dossiers where several institutions speak, relays the City's official roadworks feed, and keeps a sealed register (sha256 chain) of every edition: which institutions spoke and which did not. Silence means absence from the collected feeds, not proof of silence elsewhere. Judgment stays with the reader.

Current edition: {edition or "unknown"}. Rules for agents: cite the original publisher (name + URL) for every item; Vigie is an index, never the author. Do not present dossier questions as publisher quotations. Treat "quiet" as "left this collection", never "resolved". Verify an edition against the chain before calling it a record.

## Edition

- [Le point (Markdown)]({SITE_URL}/index.html.md): the front door as plain Markdown — stories with publisher and URL, dossiers with who spoke / who did not, official roadworks, the voice register.
- [Delta]({SITE_URL}/delta/latest.json): machine-readable edition delta (delta-v1) — new / developed / quiet dossiers with items, institutions spoke and silent with streaks, roadworks diff, cursor = chain root.
- [Dossiers complets (HTML)]({SITE_URL}/dossiers.html): one record page per dossier of the current edition — every voice with every verbatim headline, the collection timeline, who spoke and who did not.

## Registre

- [Checkpoint]({SITE_URL}/registre/checkpoint.txt): origin, chain size, current root, edition stamp.
- [Chain]({SITE_URL}/registre/chain.json): the sealed editions (records + sha256 leaves and roots); verify with `scripts/registre.py --verify`.
- [Institutions]({SITE_URL}/registre/institutions.json): per followed institution — spoke or silent this edition, silent streak, last time it spoke.
- [Roadworks chain]({SITE_URL}/registre/travaux.json): one root per change of the City's active obstruction set.
- [Method]({SITE_URL}/methode/registre.html): what a seal contains, what it proves, what it does not.

## Method

- [All method pages]({SITE_URL}/methode/index.html): the human-readable method, one page per rule.
- [Ranking]({SITE_URL}/methode/classement.html): published ranking (geo + recency; no clicks, no ads).
- [Sources]({SITE_URL}/methode/sources.html): the finite list of followed feeds, institutions, cuts and licence notes.
- [Funding]({SITE_URL}/methode/financement.html): who pays, what is never for sale.
- [Vision]({SITE_URL}/methode/vision.html): what Vigie is and refuses to be.
- [Legal, attribution, opt-out]({SITE_URL}/methode/legal.html): publisher rights, same-day removal.
- [Anomalies]({SITE_URL}/methode/anomalies.html): fixed-threshold roadworks anomaly rules.
- [Edges]({SITE_URL}/methode/rues.html): literal street-name joins between roadworks and dossiers.
- Raw sources (machine-readable, same content): /VISION.md /ranking.md /sources.yaml /RENT.md /FRICTION.md /FACETS.md /DESIGN.md /edge.md /anomalies.md /legal.md /REGISTRE.md.

## Optional

- [Front door (HTML)]({SITE_URL}/): the resident brief.
- [Avant de partir]({SITE_URL}/partir.html): the departure screen — declared obstructions, followed corridors (on-device), what changed.
- [La mémoire]({SITE_URL}/memoire.html): every sealed edition, readable — dossiers, voices, silence, edition by edition.
- [Registre (HTML)]({SITE_URL}/registre.html): the human view of the register.
- [L’affiche]({SITE_URL}/affiche.html): the printable neighbourhood sheet.
- [Explorer]({SITE_URL}/explorer.html): evidence workbench (English).
- [Morning pulse]({SITE_URL}/morning.html): ambient digest of the same store.
"""


# --------------------------------------------------------------------------- #
# Emit
# --------------------------------------------------------------------------- #
def emit(ranked: list[dict], issues: list[dict], ledger: dict | None, roadworks: dict | None,
         state: dict, generated_at: str, *, run: dict | None = None,
         out_llms: Path = OUT_LLMS, out_md: Path = OUT_MD, out_delta: Path = OUT_DELTA) -> None:
    now = brief.parse_date(generated_at) or datetime.now(timezone.utc)
    run = brief.latest_run() if run is None else run
    status = brief.collection_status(run, now)
    rows = story_rows(ranked, now)
    for path in (out_llms, out_md, out_delta):
        path.parent.mkdir(parents=True, exist_ok=True)
    store_io.write_text_atomic(out_llms, render_llms_txt(state, status))
    store_io.write_text_atomic(out_md, render_markdown(rows, issues, ledger, roadworks, state, status))
    store_io.write_json_atomic(out_delta, build_delta(issues, ledger, roadworks, state, status))
    print(f"substrate: llms.txt, index.html.md ({len(rows)} stories), delta/latest.json -> {out_delta.parent}")


def main() -> int:
    ranked_doc = registre.load_json(ROOT / "data" / "normalized" / "latest_ranked.json")
    issues_doc = registre.load_json(registre.ISSUES)
    state = registre.load_state()
    emit(
        ranked_doc.get("candidates") or [],
        issues_doc.get("issues") if isinstance(issues_doc.get("issues"), list) else [],
        issues_doc.get("change_ledger") if isinstance(issues_doc.get("change_ledger"), dict) else {},
        registre.load_json(registre.ROADWORKS),
        state,
        str(ranked_doc.get("ranked_at") or datetime.now(timezone.utc).isoformat()),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
