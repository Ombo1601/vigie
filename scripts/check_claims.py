"""Validate excerpt provenance in collected text, never truth or attribution.
An empty claim set is valid: a feed need not contain quoted speech.
"""
from __future__ import annotations
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def normalized_span(value: object) -> str:
    return " ".join(str(value or "").split())


def _entries(doc: object, key: str) -> list:
    if not isinstance(doc, dict):
        return []
    value = doc.get(key)
    return value if isinstance(value, list) else []


def _claims_of(candidate: dict) -> list[dict]:
    if not isinstance(candidate, dict):
        return []
    enrich = candidate.get("enrich") if isinstance(candidate.get("enrich"), dict) else {}
    claims = enrich.get("claims") if isinstance(enrich.get("claims"), list) else []
    return [c for c in claims if isinstance(c, dict)]


def audit_claims(enriched: dict, issues: dict) -> dict:
    candidates = _entries(enriched, "candidates")
    by_id = {str(c["id"]): c for c in candidates if isinstance(c, dict) and c.get("id")}
    errors: list[dict] = []
    attrs: Counter = Counter()
    claim_count = 0
    speakers = 0
    for candidate in candidates:
        if not isinstance(candidate, dict):
            errors.append({"candidate_id": None, "reason": "candidate_not_an_object"})
            continue
        for claim in _claims_of(candidate):
            claim_count += 1
            attrs[str(claim.get("attribution") or "unknown")] += 1
            speakers += bool(claim.get("speaker"))
            reason = None
            field = claim.get("field")
            quote = normalized_span(claim.get("quote"))
            text = normalized_span(candidate.get(field)) if field in ("title", "summary") else ""
            if claim.get("status") != "proposed":
                reason = "claim_status_not_proposed"
            elif not text or not quote or quote not in text:
                reason = "excerpt_not_in_declared_source_field"
            elif claim.get("speaker") and normalized_span(claim["speaker"]) not in text:
                reason = "speaker_not_in_declared_source_field"
            if reason:
                errors.append({"candidate_id": candidate.get("id"), "reason": reason})
    for issue in _entries(issues, "issues"):
        if not isinstance(issue, dict):
            errors.append({"issue_id": None, "reason": "issue_not_an_object"})
            continue
        tensions = [t for t in (issue.get("tensions") or []) if isinstance(t, dict)]
        voices = {t.get("institution_id") for t in tensions if t.get("institution_id")}
        if len(voices) < 2:
            errors.append({"issue_id": issue.get("issue_id"), "reason": "fewer_than_two_institutions"})
        for tension in tensions:
            for item in [i for i in (tension.get("items") or []) if isinstance(i, dict)]:
                source = by_id.get(str(item.get("candidate_id")))
                if source is None:
                    errors.append({"candidate_id": item.get("candidate_id"), "reason": "issue_article_missing_from_snapshot"})
                    continue
                expected = _claims_of(source)
                for claim in item.get("claims") or []:
                    if claim not in expected:
                        errors.append({"candidate_id": item.get("candidate_id"), "reason": "issue_claim_lost_or_changed_provenance"})
    return {
        "method": "extraction-integrity-v1-not-truth-verification",
        "candidate_count": len(candidates),
        "claim_count": claim_count,
        "candidates_with_claims": sum(bool(_claims_of(c)) for c in candidates if isinstance(c, dict)),
        "with_speaker": speakers,
        "attributions": dict(attrs),
        "errors": errors,
        "ok": not errors,
        "limitation": "Containment does not validate truth, context, speaker attribution, or independent confirmation.",
    }


def main() -> int:
    try:
        enriched = json.loads((ROOT / "data/normalized/latest_enriched.json").read_text(encoding="utf-8"))
        issues = json.loads((ROOT / "data/issues/latest_issues.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        print(f"check_claims: FAIL unreadable store ({type(exc).__name__}: {exc})")
        return 1
    report = audit_claims(enriched, issues)
    try:
        path = ROOT / "data/normalized/_check_claims.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"check_claims: WARN report not written ({exc})")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
