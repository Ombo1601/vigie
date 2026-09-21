"""PLAN probe: which claim shapes exist in latest_enriched.json.

A developer probe, never part of the edition. Safe to import: all I/O happens
inside main(), and a missing or corrupt store is reported, not raised.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import store_io

PATS = {
    "guillemets_fr": re.compile(r"«\s*.{8,180}?\s*»"),
    "ascii_quotes": re.compile(r'"([^"]{8,160})"'),
    "selon": re.compile(
        r"\b[Ss]elon\s+([A-ZÉÈÊÀÂÎÔÛÇ][\w\-''']+(?:\s+[A-ZÉÈÊÀÂÎÔÛÇ][\w\-''']+){0,3})"
    ),
    "dit_affirme": re.compile(
        r",\s*(?:dit|affirme|soutient|déclare|précise|estime)\s+"
        r"([A-ZÉÈÊÀÂÎÔÛÇ][\w\-''']+(?:\s+[A-ZÉÈÊÀÂÎÔÛÇ][\w\-''']+){0,2})",
        re.I,
    ),
    "speaker_colon_title": re.compile(
        r"^([A-ZÉÈÊÀÂÎÔÛÇ][^:]{2,55})\s*:\s*(.{8,160})$"
    ),
    "warns_that": re.compile(
        r"\b([A-ZÉÈÊÀÂÎÔÛÇ][\w\-''']+(?:\s+[A-ZÉÈÊÀÂÎÔÛÇ][\w\-''']+){0,2})\s+"
        r"(?:prévient|affirme|annonce|demande|dénonce|soutient)\s*[:：]?\s*(.{8,120})",
        re.I,
    ),
}


def main() -> int:
    try:
        payload = json.loads(
            (ROOT / "data/normalized/latest_enriched.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        print(f"probe_claims: no enriched store to probe ({type(exc).__name__}: {exc})")
        return 0
    candidates = payload.get("candidates") if isinstance(payload, dict) else None
    if not isinstance(candidates, list):
        print("probe_claims: enriched store has no candidates list")
        return 0

    hits: Counter = Counter()
    examples: dict[str, list] = {k: [] for k in PATS}
    for c in candidates:
        if not isinstance(c, dict):
            continue
        title = c.get("title") or ""
        summary = c.get("summary") or ""
        blob = f"{title} {summary}".strip()
        for k, p in PATS.items():
            m = p.search(title if k == "speaker_colon_title" else blob)
            if not m:
                continue
            hits[k] += 1
            if len(examples[k]) < 4:
                examples[k].append(
                    {
                        "source_id": c.get("source_id"),
                        "title": title[:120],
                        "match": m.group(0)[:140],
                        "groups": [g[:80] if isinstance(g, str) else g for g in m.groups()],
                    }
                )

    out = {
        "n": len(candidates),
        "hits": dict(hits),
        "examples": examples,
        "empty_claims_now": sum(
            1 for c in candidates
            if isinstance(c, dict) and not ((c.get("enrich") or {}).get("claims") or [])
        ),
    }
    try:
        path = ROOT / "data/normalized/_probe_claims.json"
        store_io.write_json_atomic(path, out)
    except OSError as exc:
        print(f"probe_claims: WARN report not written ({exc})")
        path = None
    print("n", out["n"], "hits", out["hits"], "empty", out["empty_claims_now"])
    if path is not None:
        print("wrote", path)
    for k, exs in examples.items():
        print("---", k, "n=", hits[k])
        for e in exs[:2]:
            print(" ", e["source_id"], "|", e["title"])
            print("   ", e["match"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
