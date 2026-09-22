"""Vigie v0 — ingest the City's public-participation calendar (HTML table).

Structured official change data, not articles: this bypasses normalize/enrich/
cluster/rank entirely. Raw HTML snapshots are append-only (fetch is the scar).
Identity is the City's own `IdProjet` on fiche.aspx. Titles and date windows
are relayed verbatim — Vigie never invents an ISO closing date or a reminder.

A feed outage, a robots Disallow, a 403 or an unreadable page never kills the
news pipeline: the previous store is kept and this exits 0. Removal from a
collection is never reported as ended or resolved.

Stdlib only. No pip. Does not invent, rank, or interpret anything.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from ingest_rss import (
    SOURCES_PATH,
    USER_AGENT,
    fetch_bytes,
    load_enabled_by_type,
    public_http_url,
    sha256_hex,
    utc_now,
)
import store_io

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
STORE_PATH = ROOT / "data" / "civic" / "latest_consultations.json"
METHOD = "civic-html-v1"
TITLE_CAP = 300
WINDOW_CAP = 200
MODE_CAP = 300
SKIP_REASONS = ("malformed", "missing_identifier", "missing_title", "unsafe_url",
                "duplicate_identifier")
ID_RE = re.compile(r"[?&]IdProjet=(\d+)", re.I)
WINDOW_RE = re.compile(
    r"(?i)^(jusqu['’]au|du\s+\d|lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)"
)
COMPARE_FIELDS = ("title", "window_text", "mode_text", "url")


def _parse_iso(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def decode_html(raw: bytes, content_type: str | None) -> str:
    charset = "utf-8"
    if isinstance(content_type, str):
        m = re.search(r"charset=([A-Za-z0-9_-]+)", content_type, re.I)
        if m:
            charset = m.group(1)
    try:
        return raw.decode(charset)
    except (LookupError, UnicodeDecodeError):
        return raw.decode("utf-8", errors="replace")


def robots_url_for(page_url: str) -> str:
    parts = urlsplit(page_url)
    return f"{parts.scheme}://{parts.netloc}/robots.txt"


def path_allowed(robots_text: str | None, page_url: str, ua: str = USER_AGENT) -> bool:
    """True when robots.txt is absent or does not Disallow this path."""
    if not robots_text:
        return True
    rp = RobotFileParser()
    rp.parse(robots_text.splitlines())
    return bool(rp.can_fetch(ua, page_url))


def read_robots(page_url: str, fetch) -> tuple[bool, str | None]:
    """Fetch robots.txt under the same SSRF/identity rules as the page.

    Missing file (404/410) = allowed. A refusal or an unreadable robots.txt
    does not authorize a scrape: keep the previous store.
    """
    robots_url = robots_url_for(page_url)
    try:
        public_http_url(robots_url, resolve=False)
        raw, ctype = fetch(robots_url)
    except urllib.error.HTTPError as exc:
        try:
            if exc.code in (404, 410):
                return True, None
            return False, "robots_refused"
        finally:
            try:
                exc.close()
            except Exception:
                pass
    except Exception:
        return False, "robots_unreadable"
    text = decode_html(raw, ctype)
    if path_allowed(text, page_url):
        return True, None
    return False, "robots_disallow"


class ActivityTable(HTMLParser):
    """Walk the City calendar table: date in <th>, title link + mode in <td>."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict] = []
        self._tr = False
        self._cell = False
        self._a = False
        self._skip = False
        self._cell_parts: list[str] = []
        self._cells: list[str] = []
        self._links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._a_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True
            return
        if tag == "tr":
            self._tr = True
            self._cells = []
            self._links = []
        elif tag in ("td", "th") and self._tr:
            self._cell = True
            self._cell_parts = []
        elif tag == "a" and self._cell:
            self._a = True
            self._href = dict(attrs).get("href")
            self._a_parts = []
        elif tag in ("br", "p") and self._cell:
            self._cell_parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False
            return
        if tag == "a" and self._a:
            text = " ".join("".join(self._a_parts).split())
            if self._href:
                self._links.append((self._href, text))
            if text:
                self._cell_parts.append(text)
            self._a = False
            self._href = None
        elif tag in ("td", "th") and self._cell:
            self._cells.append(" ".join("".join(self._cell_parts).split()))
            self._cell = False
        elif tag == "tr" and self._tr:
            if self._cells or self._links:
                self.rows.append({"cells": self._cells, "links": self._links})
            self._tr = False

    def handle_data(self, data):
        if self._skip:
            return
        if self._a:
            self._a_parts.append(data)
        elif self._cell:
            self._cell_parts.append(data)


def _cap(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit or limit < 1:
        return text
    # The ellipsis counts toward the published cap.
    return text[: limit - 1].rstrip() + "…"


def parse_row(row: dict, base_url: str) -> tuple[dict | None, str | None]:
    if not isinstance(row, dict):
        return None, "malformed"
    chosen = None
    for href, text in row.get("links") or []:
        if not isinstance(href, str):
            continue
        match = ID_RE.search(href)
        if match:
            chosen = (href, str(text or "").strip(), match.group(1))
            break
    if not chosen:
        return None, "missing_identifier"
    href, title, event_id = chosen
    if not title:
        return None, "missing_title"
    try:
        url = public_http_url(urljoin(base_url, href), resolve=False)
    except ValueError:
        return None, "unsafe_url"
    window = ""
    for cell in row.get("cells") or []:
        if isinstance(cell, str) and WINDOW_RE.search(cell.strip()):
            window = cell.strip()
            break
    mode = ""
    for cell in row.get("cells") or []:
        if not isinstance(cell, str) or title not in cell:
            continue
        rest = cell.replace(title, " ", 1).strip()
        if window and rest.startswith(window):
            rest = rest[len(window):].strip()
        mode = rest
        break
    return {
        "event_id": event_id,
        "title": _cap(title, TITLE_CAP),
        "window_text": _cap(window, WINDOW_CAP),
        "mode_text": _cap(mode, MODE_CAP),
        "url": url,
    }, None


def looks_like_calendar(html: str) -> bool:
    lower = html.lower()
    return "calendrier-activites" in lower or "idprojet=" in lower


def parse_activities(html: str, base_url: str) -> tuple[list[dict], dict]:
    """HTML calendar → (events, counts). Never invents identifiers or dates."""
    counts = {reason: 0 for reason in SKIP_REASONS}
    counts["rows"] = 0
    if not looks_like_calendar(html):
        return [], {**counts, "parse_error": "no_activity_table"}
    parser = ActivityTable()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:
        return [], {**counts, "parse_error": f"{type(exc).__name__}: {exc}"}
    events: list[dict] = []
    seen: set[str] = set()
    for row in parser.rows:
        counts["rows"] += 1
        event, reason = parse_row(row, base_url)
        if event is None:
            counts[reason or "malformed"] += 1
            continue
        if event["event_id"] in seen:
            counts["duplicate_identifier"] += 1
            continue
        seen.add(event["event_id"])
        events.append(event)
    events.sort(key=lambda e: (0, int(e["event_id"])) if str(e["event_id"]).isdigit()
                else (1, str(e["event_id"])))
    counts["parsed"] = len(events)
    return events, counts


def load_previous(store_path: Path, method: str = METHOD) -> list[dict] | None:
    try:
        doc = json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("method") != method:
        return None
    events = doc.get("events")
    return events if isinstance(events, list) else None


def diff_events(current: list[dict], previous: list[dict] | None) -> dict:
    """Presence diff only. Removed ≠ closed, cancelled, or resolved."""
    if previous is None:
        return {
            "has_previous": False,
            "new_count": 0, "removed_count": 0, "changed_count": 0,
            "new": [], "removed": [], "changed": [],
        }
    prev_map = {
        str(e.get("event_id")): e
        for e in previous if isinstance(e, dict) and e.get("event_id")
    }
    cur_map = {str(e["event_id"]): e for e in current}
    new_ids = sorted(set(cur_map) - set(prev_map), key=lambda x: (0, int(x)) if x.isdigit() else (1, x))
    removed_ids = sorted(set(prev_map) - set(cur_map), key=lambda x: (0, int(x)) if x.isdigit() else (1, x))
    changed = []
    for eid in sorted(set(cur_map) & set(prev_map), key=lambda x: (0, int(x)) if x.isdigit() else (1, x)):
        a, b = cur_map[eid], prev_map[eid]
        if any(a.get(field) != b.get(field) for field in COMPARE_FIELDS):
            changed.append(eid)
    return {
        "has_previous": True,
        "new_count": len(new_ids),
        "removed_count": len(removed_ids),
        "changed_count": len(changed),
        "new": new_ids,
        "removed": removed_ids,
        "changed": changed,
    }


def build_store(src: dict, events: list[dict], counts: dict, diff: dict,
                fetched_at: datetime) -> dict:
    return {
        "method": METHOD,
        "fetched_at": fetched_at.isoformat(),
        "source_id": src.get("id"),
        "source_name": src.get("name"),
        "institution_name": src.get("institution_name") or src.get("name"),
        "source_url": src.get("url"),
        "homepage": src.get("homepage") or src.get("url"),
        "attribution": src.get("institution_name") or "Ville de Québec",
        "license_note": src.get("license_note") or "",
        "events": events,
        "counts": {**counts, "active": len(events)},
        "diff": diff,
    }


def latest_snapshot(raw_dir: Path, source_id: str) -> Path | None:
    directory = raw_dir / source_id
    if not directory.is_dir():
        return None
    snapshots = sorted(directory.glob("*.html"))
    return snapshots[-1] if snapshots else None


def snapshot_fetched_at(snapshot: Path) -> datetime | None:
    meta = snapshot.with_suffix(".json")
    if meta.exists():
        try:
            doc = json.loads(meta.read_text(encoding="utf-8"))
            if isinstance(doc, dict):
                return _parse_iso(doc.get("fetched_at"))
        except (OSError, ValueError):
            pass
    return None


def collect(sources: list[dict], now: datetime, *, offline: bool = False,
            raw_dir: Path = RAW_DIR, store_path: Path = STORE_PATH,
            fetch=fetch_bytes) -> dict:
    """Rebuild the consultations store. Failure keeps the previous store."""
    if not sources:
        print("  no enabled civic-html sources; keeping previous store")
        return {"ok": False, "reason": "no_sources"}
    previous = load_previous(store_path)
    all_events: list[dict] = []
    fetched_ats: list[datetime] = []
    merged_counts = {reason: 0 for reason in SKIP_REASONS}
    merged_counts.update({"rows": 0, "parsed": 0})
    for src in sources:
        source_id = str(src.get("id") or "")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", source_id):
            print(f"  FAIL {source_id!r}: invalid source identifier; keeping previous store")
            return {"ok": False, "reason": "invalid_source_id"}
        dest_dir = raw_dir / source_id
        page_url = str(src.get("url") or "")
        try:
            page_url = public_http_url(page_url, resolve=False)
        except ValueError as exc:
            print(f"  FAIL {source_id}: {exc}; keeping previous store")
            return {"ok": False, "reason": "invalid_url"}
        if offline:
            snapshot = latest_snapshot(raw_dir, source_id)
            if snapshot is None:
                print(f"  {source_id}: no raw snapshot to reuse offline; keeping previous store")
                return {"ok": False, "reason": "no_snapshot"}
            try:
                raw = snapshot.read_bytes()
            except OSError as exc:
                print(f"  FAIL {source_id}: unreadable snapshot ({exc}); keeping previous store")
                return {"ok": False, "reason": "snapshot_unreadable"}
            fetched_at = snapshot_fetched_at(snapshot) or now
            content_type = "text/html"
            print(f"  {source_id}: reusing {snapshot.name} (fetched {fetched_at.isoformat()})")
            archived = False
        else:
            allowed, robots_reason = read_robots(page_url, fetch)
            if not allowed:
                print(f"  FAIL {source_id}: {robots_reason} — keeping previous store")
                return {"ok": False, "reason": robots_reason}
            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                print(f"  FAIL {source_id}: unwritable raw directory ({exc}); keeping previous store")
                return {"ok": False, "reason": "raw_dir_unwritable"}
            stamp = now.strftime("%Y%m%dT%H%M%SZ")
            try:
                raw, content_type = fetch(page_url)
            except Exception as exc:
                error = {
                    "ok": False, "source_id": source_id, "url": page_url,
                    "error": f"{type(exc).__name__}: {exc}", "fetched_at": now.isoformat(),
                }
                try:
                    (dest_dir / f"{stamp}_error.json").write_text(
                        json.dumps(error, ensure_ascii=False, indent=2), encoding="utf-8")
                except OSError:
                    pass
                print(f"  FAIL {source_id}: {error['error']} — keeping previous store")
                return {"ok": False, "reason": "fetch_failed"}
            fetched_at = now
            digest = sha256_hex(raw)
            snapshot = dest_dir / f"{stamp}_{digest[:12]}.html"
            archived = True
            try:
                prev_snaps = sorted(dest_dir.glob("*.html"))
                same = bool(prev_snaps) and prev_snaps[-1].name.endswith(f"_{digest[:12]}.html")
                store_io.write_bytes_dedup(snapshot, raw, prev_snaps[-1] if same else None)
            except OSError as exc:
                archived = False
                print(f"  WARN {source_id}: snapshot not archived ({exc}); continuing from memory")
        html = decode_html(raw, content_type if not offline else None)
        events, counts = parse_activities(html, page_url)
        parse_error = counts.pop("parse_error", None)
        if not offline and archived:
            meta = {
                "ok": parse_error is None, "source_id": source_id,
                "source_name": src.get("name"),
                "institution": src.get("institution") or source_id,
                "type": "civic-html", "feed_url": page_url,
                "fetched_at": fetched_at.isoformat(),
                "content_type": content_type if parse_error is None else None,
                "bytes": len(raw), "sha256": sha256_hex(raw),
                "html_file": str(snapshot.relative_to(raw_dir.parent.parent)).replace("\\", "/")
                if snapshot.is_relative_to(raw_dir.parent.parent) else str(snapshot),
                "item_count": counts.get("parsed", 0), "parse_error": parse_error,
            }
            try:
                snapshot.with_suffix(".json").write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError as exc:
                print(f"  WARN {source_id}: meta not archived ({exc}); continuing")
        if parse_error:
            print(f"  FAIL {source_id}: {parse_error} — keeping previous store")
            return {"ok": False, "reason": "parse_failed"}
        for event in events:
            event["source_id"] = source_id
            all_events.append(event)
        for key, value in counts.items():
            merged_counts[key] = merged_counts.get(key, 0) + value
        fetched_ats.append(fetched_at)
    store_fetched_at = min(fetched_ats)
    seen_ids: set[str] = set()
    unique: list[dict] = []
    for event in all_events:
        if event["event_id"] in seen_ids:
            merged_counts["duplicate_identifier"] += 1
            continue
        seen_ids.add(event["event_id"])
        unique.append(event)
    unique.sort(key=lambda e: (0, int(e["event_id"])) if str(e["event_id"]).isdigit()
                else (1, str(e["event_id"])))
    merged_counts["parsed"] = len(unique)
    diff = diff_events(unique, previous)
    store = build_store(sources[0], unique, merged_counts, diff, store_fetched_at)
    try:
        store_io.write_json_atomic(store_path, store)
    except OSError as exc:
        print(f"  FAIL store write ({type(exc).__name__}: {exc}); keeping previous store")
        return {"ok": False, "reason": "store_write_failed"}
    return {"ok": True, "store": store, "store_path": store_path}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true",
                        help="Reuse the latest raw snapshot; make no network requests")
    args = parser.parse_args(argv)
    sources = load_enabled_by_type(SOURCES_PATH, "civic-html")
    now = utc_now()
    print(f"Vigie civic ingest {now.isoformat()} — {len(sources)} enabled civic-html"
          + (" (offline snapshots)" if args.offline else ""))
    result = collect(sources, now, offline=args.offline)
    if result.get("ok"):
        store = result["store"]
        c, d = store["counts"], store["diff"]
        skipped = sum(c.get(reason, 0) for reason in SKIP_REASONS)
        print(f"  rows={c.get('rows', 0)} parsed={c['parsed']} skipped={skipped}")
        if d["has_previous"]:
            print(f"  diff: +{d['new_count']} new, -{d['removed_count']} removed, "
                  f"~{d['changed_count']} changed (absence n’est pas une clôture)")
        else:
            print("  diff: no previous collection to compare")
        try:
            print(f"store: {result['store_path'].relative_to(ROOT)}")
        except ValueError:
            print(f"store: {result['store_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
