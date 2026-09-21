"""Vigie v0 — normalize latest raw ingest JSON into StoryCandidates.

Reads newest ingest outcome for enabled sources; failed/stale feeds stay unavailable.
Writes append-only snapshot to data/normalized/.
Does not enrich, cluster, or rank. Dedupes by canonical URL within the run.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote_plus, urlparse, urlunparse

import store_io
from ingest_rss import load_enabled_rss, public_http_url

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
OUT_DIR = ROOT / "data" / "normalized"
SOURCES_PATH = ROOT / "sources.yaml"
MAX_FETCH_AGE_HOURS = 48
FUTURE_TOLERANCE_MINUTES = 15
HISTORY_RETENTION_DAYS = 30  # stamped candidate snapshots (mirrors raw retention)
STAMPED_NAME_RE = re.compile(r"^(\d{8})T\d{6}Z_candidates\.json$")
STAMPED_TMP_RE = re.compile(r"^(\d{8})T\d{6}Z_candidates\.json\..*\.tmp$")
TRACKING_PARAMS = {"fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "igshid"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        p = urlparse(public_http_url(url))
    except (ValueError, TypeError):
        return None
    scheme = p.scheme.lower()
    netloc = p.netloc.lower()
    if p.port == (443 if scheme == "https" else 80):
        netloc = netloc.rsplit(":", 1)[0]
    path = p.path or "/"
    # Preserve path semantics and functional query bytes (including signatures).
    # /article and /article/ may be different resources; only trackers are removed.
    query = []
    for part in p.query.split("&"):
        key = unquote_plus(part.partition("=")[0]).lower()
        if not key.startswith("utm_") and key not in TRACKING_PARAMS:
            query.append(part)
    cleaned_query = "&".join(query)
    return urlunparse((scheme, netloc, path, p.params, cleaned_query, ""))


def stable_id(url: str | None, source_id: str, title: str | None, guid: str | None) -> str:
    basis = canonical_url(url) or f"{source_id}|{guid or title or ''}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


def collection_timestamp(metas: list[Path], fallback: datetime) -> datetime:
    """The collection clock this snapshot represents, never the rebuild clock.

    One edition is one collection snapshot (see dossier_history): an offline
    rebuild of the same raw snapshots must keep the same ``normalized_at`` or
    it would inflate every dossier's ``editions_seen``. The newest ingest run
    document is authoritative; then the newest selected source snapshot; the
    caller's build clock is a last resort only.
    """
    for path in sorted(RAW_DIR.glob("_run_*.json"), reverse=True):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            stamp = parse_timestamp(doc.get("fetched_at")) if isinstance(doc, dict) else None
        except (OSError, ValueError):
            continue
        if stamp:
            return stamp
    stamps: list[datetime] = []
    for path in metas:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            stamp = parse_timestamp(doc.get("fetched_at")) if isinstance(doc, dict) else None
        except (OSError, ValueError):
            continue
        if stamp:
            stamps.append(stamp)
    return max(stamps) if stamps else fallback


def prune_candidate_history(out_dir: Path, days: int = HISTORY_RETENTION_DAYS,
                            now: datetime | None = None) -> int:
    """Retention law: stamped candidate snapshots older than the window are
    deleted; latest_candidates.json and the newest stamped snapshot always
    survive, so an offline rebuild and a diff always have an input. Fail-soft:
    a pruning problem never fails the normalize step."""
    out_dir = Path(out_dir)
    now = now or utc_now()
    cutoff = (now - timedelta(days=days)).strftime("%Y%m%d")
    removed = 0
    try:
        entries = [p for p in out_dir.iterdir() if p.is_file()]
    except OSError:
        return 0
    stamped = sorted(p for p in entries if STAMPED_NAME_RE.match(p.name))
    for path in stamped[:-1]:  # the newest stamped snapshot always survives
        if STAMPED_NAME_RE.match(path.name).group(1) < cutoff:
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
    # Orphaned temp files from a crashed atomic write are never a store.
    # Stamped temps carry their edition date in the name; latest_* temps carry
    # none, so they are aged by mtime (a day is far beyond any overlapping run).
    for path in entries:
        m = STAMPED_TMP_RE.match(path.name)
        if m and m.group(1) < cutoff:
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
        if path.name.startswith("latest_") and path.name.endswith(".tmp"):
            try:
                old = time.time() - path.stat().st_mtime > 24 * 3600
            except OSError:
                continue
            if old:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    continue
    return removed


def latest_meta_files(sources: list[dict] | None = None) -> list[Path]:
    files: list[Path] = []
    if not RAW_DIR.exists():
        return files
    if sources is None:
        sources = load_enabled_rss(SOURCES_PATH)
    for source in sources:
        src_dir = RAW_DIR / source["id"]
        if not src_dir.is_dir():
            continue
        metas = [
            p
            for p in src_dir.glob("*.json")
        ]
        if not metas:
            continue
        # Newest collection stamp first; when one stamp carries both an outcome
        # and an error document, the outcome wins (the error is the same attempt
        # retried, not a later failure).
        metas.sort(key=lambda p: (p.name[:16], not p.name.endswith("_error.json")), reverse=True)
        files.append(metas[0])
    return files


def parse_timestamp(raw: str | None) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        value = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            value = parsedate_to_datetime(raw)
        except (ValueError, TypeError, OverflowError):
            return None
    if value.tzinfo is None:
        return None  # never use the build machine's local timezone as publisher truth
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        return None


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"p", "br", "div", "li"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif tag in {"p", "div", "li"}:
            self.parts.append(" ")

    def handle_data(self, text):
        if not self.hidden:
            self.parts.append(text)


def plain_text(raw: str | None, limit: int) -> str | None:
    if not isinstance(raw, str):
        return None
    parser = _PlainText()
    parser.feed(raw)
    parser.close()  # flush a trailing "&" / incomplete charref, or it is lost
    value = " ".join("".join(parser.parts).split())
    return (value[:limit] + "…" if len(value) > limit else value) or None


def normalize_item(raw_item: dict, source_meta: dict) -> dict | None:
    title = plain_text(raw_item.get("title"), 500)
    url = canonical_url(raw_item.get("url"))
    if (raw_item.get("url") and not url) or (not title and not url):
        return None
    summary = plain_text(raw_item.get("body"), 2000)
    source_id = source_meta.get("source_id") or raw_item.get("source_id")
    source_kind = (
        source_meta.get("source_kind")
        or raw_item.get("source_kind")
        or "media"
    )
    if str(source_kind).lower() not in ("media", "official"):
        source_kind = "media"
    else:
        source_kind = str(source_kind).lower()
    fetched_raw = source_meta.get("fetched_at") or raw_item.get("fetched_at")
    fetched = parse_timestamp(fetched_raw)
    published_raw = raw_item.get("published_at")
    published = parse_timestamp(published_raw)
    date_status = "valid" if published else ("invalid" if published_raw else "missing")
    if published and fetched and published > fetched + timedelta(minutes=FUTURE_TOLERANCE_MINUTES):
        published = None
        date_status = "future"
    updated = parse_timestamp(raw_item.get("updated_at"))
    return {
        "id": stable_id(url, source_id or "unknown", title, raw_item.get("guid")),
        "title": title,
        "summary": summary,
        "published_at": published.isoformat() if published else None,
        "published_at_raw": published_raw,
        "publication_date_status": date_status,
        "updated_at": updated.isoformat() if updated else None,
        "source_id": source_id,
        "source_name": source_meta.get("source_name"),
        "institution": source_meta.get("institution") or source_id,
        "institution_name": source_meta.get("institution_name") or source_meta.get("source_name"),
        "source_kind": source_kind,
        "url": url,
        "language": raw_item.get("language") or source_meta.get("language"),
        "geo": source_meta.get("geo"),
        "nest_role": source_meta.get("nest_role"),
        "guid": raw_item.get("guid"),
        "author": plain_text(raw_item.get("author"), 120),
        "photo_credit": plain_text(raw_item.get("credit"), 120),
        "fetched_at": fetched.isoformat() if fetched else fetched_raw,
        "enrich_status": "pending",  # proposals come later; never truth
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sources = load_enabled_rss(SOURCES_PATH)
    source_by_id = {source["id"]: source for source in sources}
    metas = latest_meta_files(sources)
    if not metas:
        print("No raw meta JSON found. Run ingest_rss.py first.")
        return 1
    fetched_at = utc_now()
    stamp = fetched_at.strftime("%Y%m%dT%H%M%SZ")
    # Edition identity comes from the collection, not this rebuild: an offline
    # rerun over the same raw snapshots is the same edition.
    normalized_at = collection_timestamp(metas, fetched_at)
    candidates: list[dict] = []
    seen_ids: set[str] = set()
    per_source: dict[str, int] = {}
    source_status: dict[str, dict] = {source["id"]: {"status": "missing", "candidate_count": 0} for source in sources}
    for meta_path in metas:
        source_id = meta_path.parent.name
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Source metadata is not an object")
        except (OSError, ValueError) as exc:
            source_status[source_id] = {"status": "invalid", "error": str(exc), "candidate_count": 0}
            continue
        if not payload.get("ok") or payload.get("parse_error"):
            source_status[source_id] = {"status": "failed", "error": payload.get("error") or payload.get("parse_error"), "candidate_count": 0}
            continue
        if not isinstance(payload.get("items"), list):
            source_status[source_id] = {"status": "invalid", "error": "Source items must be a list", "candidate_count": 0}
            continue
        fetched = parse_timestamp(payload.get("fetched_at"))
        if not fetched or fetched_at - fetched > timedelta(hours=MAX_FETCH_AGE_HOURS) or fetched > fetched_at + timedelta(minutes=FUTURE_TOLERANCE_MINUTES):
            source_status[source_id] = {"status": "stale", "fetched_at": payload.get("fetched_at"), "candidate_count": 0}
            continue
        # The current registry is authoritative, including institution and geography.
        source = source_by_id[source_id]
        payload.update({"source_id": source_id, "source_name": source.get("name"),
                        **{key: source.get(key) for key in ("institution", "institution_name", "geo", "nest_role", "source_kind", "language")}})
        n = 0
        dropped = {"not_an_item": 0, "no_title_or_url": 0, "duplicate_id": 0}
        item_nodes = len(payload.get("items") or [])
        for it in payload.get("items") or []:
            if not isinstance(it, dict):
                dropped["not_an_item"] += 1
                continue
            cand = normalize_item(it, payload)
            if not cand:
                dropped["no_title_or_url"] += 1
                continue
            if cand["id"] in seen_ids:
                dropped["duplicate_id"] += 1
                continue
            seen_ids.add(cand["id"])
            candidates.append(cand)
            n += 1
        per_source[source_id] = n
        source_status[source_id] = {"status": "ok", "fetched_at": fetched.isoformat(),
                                    "candidate_count": n, "item_nodes": item_nodes,
                                    "dropped": dropped}
        print(f"  {source_id}: {n} candidates from {meta_path.name}")

    out = {
        "normalized_at": normalized_at.isoformat(),
        "source_meta_files": [str(p.relative_to(ROOT) if p.is_relative_to(ROOT) else p).replace("\\", "/") for p in metas],
        "max_fetch_age_hours": MAX_FETCH_AGE_HOURS,
        "source_status": source_status,
        "candidate_count": len(candidates),
        "per_source": per_source,
        "candidates": candidates,
    }
    out_path = OUT_DIR / f"{stamp}_candidates.json"
    store_io.write_json_atomic(out_path, out)
    latest = OUT_DIR / "latest_candidates.json"
    store_io.write_json_atomic(latest, out)
    print(f"wrote {out_path} ({len(candidates)} unique)")
    print(f"wrote {latest}")
    pruned = prune_candidate_history(OUT_DIR)
    if pruned:
        print(f"pruned {pruned} candidate snapshot(s) older than {HISTORY_RETENTION_DAYS} days")
    return 0


if __name__ == "__main__":
    sys.exit(main())
