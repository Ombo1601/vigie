"""Ambient morning digest — same Approaches as Lookout Arrival + Stage.

House law:
- Reuse latest_issues + latest_ranked only (presentation of store).
- Never a second ranking brain. Never LLM digest. Never For You.
- Morning HTML/JSON/TXT/widget are twins of Arrival Approaches (and Stage fights).

Usage (from repo root):
  python scripts/ambient_pulse.py
  # or emitted automatically at end of rank_display.main()
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import rank_display
import store_io

ROOT = Path(__file__).resolve().parents[1]
ISSUES = rank_display.ISSUES
RANKED = rank_display.OUT_JSON
OUT_DIR = ROOT / "data" / "pulse"
OUT_JSON = OUT_DIR / "latest_morning.json"
OUT_TXT = OUT_DIR / "latest_morning.txt"
OUT_WIDGET = OUT_DIR / "latest_morning.widget.txt"
OUT_HTML = ROOT / "public" / "morning.html"

METHOD = "ambient-pulse-v0.2 same-store-as-arrival-and-stage"
NOTE = (
    "Identical Approaches order to Lookout Arrival and Stage fights. "
    "Not a second ranking. No LLM digest. No personalization feed."
)
TRUST_NOTE = (
    "Des sources réunies ne sont pas des confirmations indépendantes. "
    "Un désaccord entre elles n'est pas établi. Une absence dans les flux collectés "
    "ne prouve pas un silence éditorial."
)


def clustered_at_from_issues(issues: list[dict], fallback: str) -> str:
    for iss in issues or []:
        if isinstance(iss, dict) and iss.get("clustered_at"):
            return str(iss["clustered_at"])
    return fallback


def build_digest(
    issues: list[dict],
    ranked: list[dict],
    *,
    clustered_at: str | None = None,
    ranked_at: str | None = None,
    built_at: str | None = None,
) -> dict:
    """Build morning digest from the same builders Arrival uses. Pure presentation."""
    now = built_at or datetime.now(timezone.utc).isoformat()
    continuity = rank_display.build_continuity(issues or [], ranked or [])
    approaches = rank_display.build_approaches(issues or [], continuity)
    # Rebuilding an undated/empty store is not a new collection of news.
    # Deliberately falls back to "" (rendered "date unknown"), never to the
    # build clock: an absent collection date must stay absent (test-locked).
    c_at = clustered_at or clustered_at_from_issues(issues or [], "")
    pulse = rank_display.pulse_payload(approaches, c_at)
    # Same filter-then-slice as rank_display.build_approaches: a malformed entry
    # must not shift the idx- fallback keys and misattribute a publisher.
    fight_issues = [x for x in (issues or []) if isinstance(x, dict)][:rank_display.APPROACH_MAX]
    return {
        "kind": "morning_digest",
        "method": METHOD,
        "note": NOTE,
        "trust_note": TRUST_NOTE,
        "built_at": now,
        "clustered_at": c_at,
        "ranked_at": ranked_at,
        "approaches": approaches,
        "pulse": pulse,
        "source_details": {
            str(iss.get("issue_id") or iss.get("scar") or f"idx-{i}"): {
                "label_kind": iss.get("label_kind"),
                "label_source": iss.get("label_source"),
                "evidence": iss.get("evidence") or {},
            }
            for i, iss in enumerate(fight_issues)
        },
        "identity": {
            "issue_ids": [str(a.get("issue_id") or "") for a in approaches],
            "fingerprints": [rank_display.approach_fingerprint(a) for a in approaches],
            "questions": [str(a.get("question") or "") for a in approaches],
        },
    }


def digest_identity_tuple(digest: dict) -> tuple:
    """Stable identity for tests — order + fingerprints, nothing else."""
    ident = digest.get("identity") or {}
    return (
        tuple(ident.get("issue_ids") or []),
        tuple(ident.get("fingerprints") or []),
        tuple(ident.get("questions") or []),
    )


def approaches_identity_tuple(approaches: list[dict]) -> tuple:
    return (
        tuple(str(a.get("issue_id") or "") for a in approaches),
        tuple(rank_display.approach_fingerprint(a) for a in approaches),
        tuple(str(a.get("question") or "") for a in approaches),
    )


def digest_matches_approaches(digest: dict, approaches: list[dict]) -> bool:
    return digest_identity_tuple(digest) == approaches_identity_tuple(approaches)


def digest_matches_arrival_pulse(digest: dict, approaches: list[dict]) -> bool:
    """Pulse twin of Arrival `#vigie-pulse` — refuse a second ranking priest."""
    c_at = str(digest.get("clustered_at") or "")
    expected = rank_display.pulse_payload(approaches, c_at)
    return (digest.get("pulse") or {}) == expected


def stage_fight_issue_ids(issues: list[dict]) -> list[str]:
    """Stage panels = same store fights Arrival/Ambient present (cap APPROACH_MAX).

    Filter then slice, exactly like build_approaches: slicing first would let a
    malformed entry shift the cap and make the twin guard fail spuriously.
    """
    out: list[str] = []
    for i, iss in enumerate(
        [x for x in (issues or []) if isinstance(x, dict)][: rank_display.APPROACH_MAX]
    ):
        out.append(str(iss.get("issue_id") or iss.get("scar") or f"idx-{i}"))
    return out


def digest_matches_stage_fights(digest: dict, issues: list[dict]) -> bool:
    return list((digest.get("identity") or {}).get("issue_ids") or []) == stage_fight_issue_ids(
        issues
    )


def digest_approach_link_html(ap: dict, details: dict | None = None) -> str:
    """Glance row as link into Arrival deep-link — same Approach object as Arrival."""
    ap = ap if isinstance(ap, dict) else {}
    q = rank_display.esc((ap.get("question") or "Issue")[:110])
    nest_key = ap.get("nest") or "linked"
    issue_id = rank_display.esc(str(ap.get("issue_id") or ""))
    fp = rank_display.esc(rank_display.approach_fingerprint(ap))
    idx = rank_display.safe_int(ap.get("index"))
    remix_cls = " approach-remix" if ap.get("remix") else ""
    meta = rank_display.approach_meta_chips_html(ap) + rank_display.approach_silence_preview_html(
        ap
    )
    href = f"/explorer.html?approach={idx}"
    origin = ""
    if (details or {}).get("label_kind") == "attributed_headline":
        source = (details or {}).get("label_source") or {}
        name = rank_display.esc(str(source.get("source_name") or source.get("source_id") or "la source"))
        origin = f"<span class='approach-origin'>Titre de {name} · rapprochement proposé</span>"
    return (
        f"<a class='approach{remix_cls}' href=\"{href}\" "
        f"data-i='{idx}' data-issue-id='{issue_id}' data-fp='{fp}' "
        f"data-nest='{rank_display.esc(str(nest_key))}' "
        f"data-voices='{rank_display.safe_int(ap.get('voices'))}' "
        f"data-silent='{rank_display.safe_int(ap.get('silent'))}' "
        f"data-units='{len([u for u in (ap.get('units') or []) if isinstance(u, dict)])}'>"
        f"<span class='approach-top'>"
        f"<span class='approach-q'>{q}</span>"
        f"</span>"
        f"{origin}"
        f"<span class='approach-meta'>{meta}</span>"
        f"</a>"
    )


def render_morning_txt(digest: dict) -> str:
    """Plain glance for future alert channel — store questions only, no LLM."""
    lines = [
        "Vigie — morning pulse",
        NOTE,
        TRUST_NOTE,
        f"clustered_at: {digest.get('clustered_at')}",
        f"method: {digest.get('method')}",
        "",
    ]
    for i, ap in enumerate(digest.get("approaches") or [], start=1):
        if not isinstance(ap, dict):
            continue
        nest = rank_display.NEST_LABEL.get(str(ap.get("nest") or ""), ap.get("nest") or "?")
        silent = ap.get("silent")
        silent_n = 0 if silent is None else rank_display.safe_int(silent)
        lines.append(
            f"{i}. [{nest}] {ap.get('question') or 'Issue'} "
            f"({rank_display.safe_int(ap.get('voices'))} voices · {silent_n} silent)"
        )
        details = (digest.get("source_details") or {}).get(str(ap.get("issue_id") or "")) or {}
        if details.get("label_kind") == "attributed_headline":
            source = details.get("label_source") or {}
            lines.append(f"   Titre de {source.get('source_name') or source.get('source_id') or 'la source'} · rapprochement proposé")
        units = [u for u in (ap.get("units") or []) if isinstance(u, dict)]
        unit_raw = [str(u.get("raw") or "").strip() for u in units if str(u.get("raw") or "").strip()]
        if unit_raw:
            for raw in unit_raw:
                lines.append(f"   unit: {raw}")
        else:
            lines.append("   unit: no units yet")
        quiet = [str(n).strip() for n in (ap.get("quiet_names") or []) if str(n).strip()]
        if quiet:
            lines.append(f"   quiet: {' · '.join(quiet[:2])}")
    lines.append("")
    lines.append("Open lookout: /index.html")
    lines.append("Compare sources: /explorer.html")
    lines.append("This digest: /morning.html")
    return "\n".join(lines) + "\n"


def render_morning_widget(digest: dict) -> str:
    """Optional widget / alert paste — same store twin, no LLM, no second ranking."""
    clock = rank_display.format_store_clock(str(digest.get("clustered_at") or ""))
    lines = [
        "Vigie morning",
        f"store {clock if digest.get('clustered_at') else 'date unknown'}",
        "same pulse as Arrival + Stage · no second ranking",
        TRUST_NOTE,
        "",
    ]
    approaches = digest.get("approaches") or []
    if not approaches:
        lines.append("(no Approaches — scars need ≥2 institutions)")
    for i, ap in enumerate(approaches, start=1):
        if not isinstance(ap, dict):
            continue
        nest = rank_display.NEST_LABEL.get(str(ap.get("nest") or ""), ap.get("nest") or "?")
        silent = ap.get("silent")
        silent_n = 0 if silent is None else rank_display.safe_int(silent)
        q = str(ap.get("question") or "Issue").strip()
        if len(q) > 72:
            q = q[:69] + "…"
        unit_bits = [
            str(u.get("raw") or "").strip()
            for u in (ap.get("units") or [])
            if isinstance(u, dict) and str(u.get("raw") or "").strip()
        ]
        unit = unit_bits[0] if unit_bits else "no units yet"
        lines.append(
            f"{i}. {nest} · {q} · {rank_display.safe_int(ap.get('voices'))}v/{silent_n}s · {unit}"
        )
    lines.append("")
    lines.append("/morning.html · /index.html")
    return "\n".join(lines) + "\n"


def render_morning_html(digest: dict) -> str:
    """Thin ambient surface — one composition, same Approaches, deep-link to Arrival."""
    approaches = [a for a in (digest.get("approaches") or []) if isinstance(a, dict)]
    source_details = digest.get("source_details") or {}
    rows = "".join(digest_approach_link_html(a, source_details.get(str(a.get("issue_id") or ""))) for a in approaches)
    if not rows:
        rows = (
            "<p class='empty'>"
            "No Approaches yet — scars need ≥2 institutions. "
            "Lookout Arrival still holds Near me."
            "</p>"
        )
    pulse = digest.get("pulse") or {}
    pulse_json = (
        json.dumps(pulse, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
    )
    c_at = rank_display.esc(str(digest.get("clustered_at") or ""))
    built = rank_display.esc(str(digest.get("built_at") or ""))
    method = rank_display.esc(str(digest.get("method") or METHOD))
    note = rank_display.esc(NOTE)
    trust_note = rank_display.esc(TRUST_NOTE)
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Vigie — Morning pulse</title>
  <link rel="canonical" href="https://vigieqc.com/morning.html" />
  <link rel="stylesheet" href="/assets/fonts.css" />
  <style>
    :root {{
      /* beauty-without-fog-v0.1 — shared Arrival tokens */
      --bg: #d4dde4;
      --bg-deep: #b7c5d0;
      --paper: #eef2f5;
      --ink: #121a20;
      --muted: #4a5864;
      --line: #a8b6c2;
      --accent: #0b4f4a;
      --chip: #e2e8ec;
      --radius: 10px;
      --font-display: "Newsreader", "Iowan Old Style", "Palatino Linotype", Palatino, serif;
      --font-ui: "Figtree", "Avenir Next", "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--bg);
      line-height: 1.45;
      font-family: var(--font-ui);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; padding: 0 0 2.5rem;
      min-height: 100vh;
      background: linear-gradient(180deg, #e8eef2 0%, var(--bg) 42%, var(--bg-deep) 100%);
      background-attachment: fixed;
    }}
    .wrap {{
      width: 100%; max-width: 40rem; margin: 0 auto;
      padding: clamp(1.4rem, 5vh, 2.75rem) clamp(.85rem, 2.5vw, 1.75rem) 0;
    }}
    .brand {{
      font-family: var(--font-display);
      font-size: clamp(3.6rem, 14vw, 6.25rem);
      font-weight: 600; letter-spacing: -.045em; line-height: .92;
      margin: 0 0 .7rem; color: var(--ink);
    }}
    .line {{
      font-family: var(--font-display);
      font-size: clamp(1.25rem, 3.2vw, 1.7rem);
      font-weight: 500; margin: 0 0 .5rem; letter-spacing: -.02em;
    }}
    .sub {{ color: var(--muted); margin: 0 0 1rem; font-size: .98rem; max-width: 34rem; }}
    .trust-note {{ color: var(--ink); margin: 0 0 1rem; font-size: .88rem; max-width: 36rem; }}
    .approach-origin {{ display: block; font-size: .75rem; color: var(--muted); margin-top: .35rem; }}
    .stamp {{ font-size: .72rem; color: var(--muted); margin: 0 0 1rem; opacity: .92; }}
    .approaches {{ display: grid; gap: .55rem; margin: 0 0 1.25rem; }}
    a.approach {{
      display: block; text-decoration: none; color: inherit;
      background: rgba(238,242,245,.72); border: 1px solid var(--line);
      border-radius: var(--radius); padding: .9rem 1rem .85rem 1.05rem;
      box-shadow: inset 3px 0 0 #7a8792;
      transition: border-color .18s ease, transform .18s ease;
    }}
    a.approach:hover {{ border-color: var(--accent); transform: translateY(-1px); }}
    a.approach-remix {{ background: color-mix(in srgb, rgba(238,242,245,.72) 88%, #c4a574 12%); }}
    .approach-top {{ display: flex; gap: .5rem; align-items: flex-start; }}
    .approach-q {{
      font-family: var(--font-display); font-size: 1.08rem; font-weight: 500;
      letter-spacing: -.01em; flex: 1;
    }}
    .approach-meta {{ display: flex; flex-wrap: wrap; gap: .3rem; margin-top: .45rem; }}
    .chip {{
      display: inline-block; background: transparent; border: 1px solid var(--line);
      border-radius: 4px; padding: .12rem .4rem; font-size: .72rem; color: var(--muted);
    }}
    .chip.geo {{ color: var(--accent); font-weight: 600; }}
    .chip.silence, .chip.official {{ color: #5a3d12; }}
    .chip.unit {{ font-variant-numeric: tabular-nums; }}
    .chip.unit.empty {{ border-style: dashed; font-weight: 500; }}
    .approach-silence-preview {{
      display: block; width: 100%; margin-top: .35rem;
      font-size: .72rem; color: var(--muted);
    }}
    .approach-silence-label {{
      font-weight: 700; text-transform: uppercase; font-size: .66rem;
      color: var(--accent); margin-right: .25rem;
    }}
    .cta {{
      display: flex; flex-wrap: wrap; gap: .75rem 1.1rem;
      align-items: center; margin: 0 0 1.5rem;
    }}
    .cta-primary {{
      display: inline-block; background: var(--accent); color: #f3f7f6;
      text-decoration: none; border-radius: 8px; padding: .55rem 1.1rem;
      font-weight: 600; font-size: .9rem;
    }}
    .cta a:not(.cta-primary) {{ color: var(--accent); font-size: .9rem; }}
    .empty {{ color: var(--muted); }}
    footer {{
      font-size: .78rem; color: var(--muted); margin-top: 1.25rem;
      padding-top: .85rem; border-top: 1px solid var(--line);
    }}
    footer a {{ color: var(--accent); }}
    @media (prefers-reduced-motion: reduce) {{
      a.approach {{ transition: none; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <p class="brand">Vigie</p>
    <h1 class="line">Morning pulse</h1>
    <p class="sub">{note}</p>
    <p class="trust-note">{trust_note}</p>
    <p class="stamp">Regroupement : {c_at or 'date inconnue'} · Page générée : {built} · {method}. Ces dates ne sont pas les dates de publication des articles.</p>
    <div class="approaches" id="approaches" aria-label="Morning Approaches">
      {rows}
    </div>
    <script type="application/json" id="vigie-pulse">{pulse_json}</script>
    <div class="cta">
      <a class="cta-primary" href="/index.html">L'essentiel à Québec</a>
      <a href="/explorer.html">Comparer les sources</a>
      <a href="/methode/frictions.html">FRICTION</a>
      <a href="/methode/classement.html">ranking</a>
    </div>
    <footer>
      Ambient channel = store twin. Aggregate only. No infinite scroll. No personalization feed.
      Method: <a href="/methode/vision.html">VISION</a> ·
      <a href="/methode/classement.html">ranking</a> ·
      <a href="/methode/sources.html">sources</a> ·
      <a href="/methode/financement.html">RENT</a> ·
      <a href="/methode/frictions.html">FRICTION</a> ·
      <a href="/methode/facettes.html">FACETS</a> ·
      <a href="/methode/design.html">DESIGN</a>.
      Morning pulse stays store order; life facets are an Arrival opt-in lens only.
    </footer>
  </div>
</body>
</html>
"""


def write_digest(digest: dict) -> dict[str, Path]:
    store_io.write_json_atomic(OUT_JSON, digest)
    store_io.write_text_atomic(OUT_TXT, render_morning_txt(digest))
    store_io.write_text_atomic(OUT_WIDGET, render_morning_widget(digest))
    store_io.write_text_atomic(OUT_HTML, render_morning_html(digest))
    return {
        "json": OUT_JSON,
        "txt": OUT_TXT,
        "widget": OUT_WIDGET,
        "html": OUT_HTML,
    }


def load_store() -> tuple[list[dict], list[dict], str | None]:
    """Read the stores fail-soft: a corrupt file means no facts, never a crash
    after the ranked store was already written."""
    issues: list[dict] = []
    if ISSUES.exists():
        try:
            loaded = json.loads(ISSUES.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            loaded = None
        if isinstance(loaded, dict) and isinstance(loaded.get("issues"), list):
            issues = loaded["issues"]
    ranked: list[dict] = []
    ranked_at: str | None = None
    if RANKED.exists():
        try:
            payload = json.loads(RANKED.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = None
        if isinstance(payload, dict):
            ranked = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
            ranked_at = payload.get("ranked_at")
    return issues, ranked, ranked_at


def load_store_clock() -> str:
    """The issues store's top-level collection clock, read fail-soft.

    Same source the arrival pulse and the other store-clock readers use; an
    absent or unreadable store yields "" (an absent collection date stays
    absent, never the build clock).
    """
    if not ISSUES.exists():
        return ""
    try:
        loaded = json.loads(ISSUES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if isinstance(loaded, dict):
        return str(loaded.get("clustered_at") or "")
    return ""


def emit_from_store(
    *,
    issues: list[dict] | None = None,
    ranked: list[dict] | None = None,
    ranked_at: str | None = None,
    clustered_at: str | None = None,
    built_at: str | None = None,
) -> dict:
    """Emit digest. Prefer in-memory store from rank_display so paint twins cannot drift.

    ``clustered_at`` is the issues store's top-level collection clock, handed in
    by rank_display so the ambient digest and the arrival ``#vigie-pulse`` read
    the same value. When it is absent the digest keeps the per-issue fallback,
    then "" — never the build clock.
    """
    if issues is None or ranked is None:
        loaded_issues, loaded_ranked, loaded_ranked_at = load_store()
        if issues is None:
            issues = loaded_issues
        if ranked is None:
            ranked = loaded_ranked
        if ranked_at is None:
            ranked_at = loaded_ranked_at
    if clustered_at is None:
        clustered_at = load_store_clock() or None
    digest = build_digest(
        issues,
        ranked,
        clustered_at=clustered_at,
        ranked_at=ranked_at,
        built_at=built_at,
    )
    # Prove identity against Arrival builders + Stage fight set before write.
    continuity = rank_display.build_continuity(issues, ranked)
    approaches = rank_display.build_approaches(issues, continuity)
    if not digest_matches_approaches(digest, approaches):
        raise SystemExit("ambient digest identity failed — refuse write")
    if not digest_matches_arrival_pulse(digest, approaches):
        raise SystemExit("ambient pulse twin failed — refuse write")
    if not digest_matches_stage_fights(digest, issues):
        raise SystemExit("ambient Stage fight twin failed — refuse write")
    paths = write_digest(digest)
    print(f"ambient morning -> {paths['json']}")
    print(f"ambient morning -> {paths['html']}")
    print(f"ambient morning -> {paths['txt']}")
    print(f"ambient morning -> {paths['widget']}")
    print(f"approaches: {len(digest.get('approaches') or [])} (store order)")
    return digest


def main() -> int:
    if not ISSUES.exists() and not RANKED.exists():
        raise SystemExit(
            f"Missing store ({ISSUES.name} / {RANKED.name}). Run pipeline / rank_display first."
        )
    emit_from_store()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
