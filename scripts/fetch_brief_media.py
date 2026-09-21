"""Vigie brief media v2 - locally served publisher images, self-diagnosed.

The front door must never make a reader's browser contact a publisher: that
would trade the product's core promise (no account, no tracking, one origin)
for decoration. So preview images are fetched HERE, at collection time,
through the same guarded network boundary as RSS (public_http_url +
public_opener, HTTPS-only, public addresses only), sniffed for their true
format, size-capped, and stored under data/media/brief/. The renderer only
ever emits same-origin /media/<uid>.<ext> references; staging refuses any
media file that does not match that shape.

House law:
  * publisher-provided images only: og:image / twitter:image on the article,
    or an image enclosure / media:content carried by the publisher's OWN feed
    when the article page is blocked or silent. Never stock, never generated,
    never a crop we invent. A publisher silent on both channels means no
    image, not a filler.
  * bytes are sniffed, not trusted: the declared Content-Type never decides
    the stored extension, and SVG is never stored - a scriptable format is
    never served from our own origin.
  * identity is the renderer's identity: uid = sha256(safe_url(url))[:20],
    the exact derivation prepare_items uses, so bookmarks and marks survive.
  * every missing image carries a diagnosed reason (article_http_403,
    article_timeout, image_too_large with its byte count, ...) - "no image"
    is a fact Vigie can explain, not a shrug.
  * the wallet is protected from itself: dead articles (404/410) and guard
    rejections are permanent; bot walls become permanent after two attempts;
    transient failures back off to one retry per day after four attempts.
  * each run leaves a health ledger (data/ops/media_health.json): reason
    histogram, per-domain failures, capped run history - the machine room
    learns which publishers block, which go quiet, and what the fixes cost.
  * fail-soft like the roadworks feed: a media outage keeps the previous
    manifest and never blocks the news pipeline (always exits 0).
  * the manifest never references a missing file; orphan files are pruned
    only after the new manifest is durable on disk.
  * new articles get their image on the next refresh - an honest crawl lag,
    never a placeholder.

No LLM. Runs between cluster and rank so the rendered edition picks up the
fresh manifest. --offline reuses the cache and makes no network requests.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import fetch_media  # noqa: E402  (fetch_html, extract_og, safe_image_url, TIMEOUT)
import resident_brief as brief  # noqa: E402  (safe_url - identity must match the renderer)
from ingest_rss import (  # noqa: E402
    MAX_FEED_BYTES, public_http_url, public_opener,
    choose_user_agent, mark_browser_identity, is_transport_stall,
    USER_AGENT, FALLBACK_USER_AGENT,
)
import ingest_rss  # noqa: E402  (_item_credit - one attribution parser, one law)
import store_io  # noqa: E402  (unique-temp atomic writes for the manifest/health)

RANKED = ROOT / "data" / "normalized" / "latest_ranked.json"
ENRICHED = ROOT / "data" / "normalized" / "latest_enriched.json"
MEDIA_DIR = ROOT / "data" / "media" / "brief"
MANIFEST = ROOT / "data" / "media" / "brief_manifest.json"
RAW_DIR = ROOT / "data" / "raw"
HEALTH = ROOT / "data" / "ops" / "media_health.json"

METHOD = "brief-media-v2"
# v1 manifests carry the same uid/file shape; accepting them migrates the
# existing image store without a blind refetch storm on upgrade.
LOADABLE_METHODS = frozenset({"brief-media-v1", METHOD})
SCOPE_CAP = 60        # top articles in display order whose images the brief tracks
FETCH_CAP = 40        # network fetch rounds per run ceiling - wallet thin
MAX_IMAGE_BYTES = 900_000
SLEEP = 0.08
FILE_RE = re.compile(r"[a-f0-9]{20}\.(?:jpg|jpeg|png|webp|avif|gif)")

# Media-sentinel retry policy: diagnosed facts decide what deserves a retry.
PERMANENT_REASONS = frozenset({
    "article_http_404", "article_http_410",
    "article_guard_rejected", "image_guard_rejected",
})
BLOCKED_REASONS = frozenset({"article_http_403", "image_http_403"})
BLOCKED_ATTEMPTS_CAP = 2     # a bot wall that stood twice stands
TRANSIENT_ATTEMPTS_CAP = 4   # timeouts/network: retry every run, then back off
QUIET_ATTEMPTS_CAP = 2       # publisher silent on both channels: check daily
BACKOFF_HOURS = 24

# Feed-media fallback: the publisher's own RSS/Atom attachments, read from
# the raw XML snapshots ingest_rss already keeps on disk. Local reads only.
FEED_INDEX_CAP = 4000
FEED_INDEX_PER_SOURCE_CAP = 400
# Some publishers (Journal de Québec) declare the MRSS namespace with the
# https variant; both spellings are the same vocabulary.
MEDIA_NS = frozenset({"http://search.yahoo.com/mrss/", "https://search.yahoo.com/mrss/"})
IMG_EXT_RE = re.compile(r"\.(?:jpe?g|png|webp|avif|gif)(?:[?#]|$)", re.I)
DOCTYPE_RE = re.compile(br"<!\s*(?:DOCTYPE|ENTITY)\b", re.I)

HEALTH_METHOD = "media-health-v1"
HEALTH_HISTORY_CAP = 28


def sniff_image(raw: bytes) -> str | None:
    """True format from magic bytes; the declared Content-Type never decides.

    SVG is deliberately absent: a scriptable format is never served from our
    own origin, whatever a publisher's header claims.
    """
    if not isinstance(raw, bytes):
        return None
    if raw[:3] == b"\xff\xd8\xff":
        return "jpg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    if raw[4:8] == b"ftyp" and raw[8:12] in (b"avif", b"avis"):
        return "avif"
    return None


def image_dimensions(raw: bytes) -> tuple[int | None, int | None]:
    """Intrinsic size from the file header — no decoder, no dependency.

    Used only to reserve layout space (`width`/`height` attributes) so a phone
    never has to guess. Unknown or malformed input yields (None, None): a guess
    would be worse than nothing.
    """
    if not isinstance(raw, bytes) or len(raw) < 16:
        return (None, None)
    try:
        if raw[:8] == b"\x89PNG\r\n\x1a\n" and raw[12:16] == b"IHDR":
            return (int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big"))
        if raw[:6] in (b"GIF87a", b"GIF89a"):
            return (int.from_bytes(raw[6:8], "little"), int.from_bytes(raw[8:10], "little"))
        if raw[:2] == b"\xff\xd8":  # JPEG: walk segments to a SOF marker
            i = 2
            while i + 4 <= len(raw):
                if raw[i] != 0xFF:
                    i += 1
                    continue
                marker = raw[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                seg_len = int.from_bytes(raw[i + 2:i + 4], "big")
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                              0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    if i + 9 <= len(raw):
                        return (int.from_bytes(raw[i + 7:i + 9], "big"),
                                int.from_bytes(raw[i + 5:i + 7], "big"))
                    break
                i += 2 + max(2, seg_len)
            return (None, None)
        if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            fourcc = raw[12:16]
            if fourcc == b"VP8X" and len(raw) >= 30:
                return (1 + int.from_bytes(raw[24:27], "little"),
                        1 + int.from_bytes(raw[27:30], "little"))
            if fourcc == b"VP8 ":
                idx = raw.find(b"\x9d\x01\x2a", 20, 40)
                if idx > 0 and idx + 7 <= len(raw):
                    return (int.from_bytes(raw[idx + 3:idx + 5], "little") & 0x3FFF,
                            int.from_bytes(raw[idx + 5:idx + 7], "little") & 0x3FFF)
            if fourcc == b"VP8L" and len(raw) >= 25 and raw[20] == 0x2F:
                bits = int.from_bytes(raw[21:25], "little")
                return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
        if raw[4:8] == b"ftyp" and raw[8:12] in (b"avif", b"avis"):
            idx = raw.find(b"ispe")
            if idx > 0 and idx + 16 <= len(raw):
                return (int.from_bytes(raw[idx + 8:idx + 12], "big"),
                        int.from_bytes(raw[idx + 12:idx + 16], "big"))
    except (IndexError, ValueError):
        pass
    return (None, None)


def fetch_image(url: str, referer: str = "", *, retries: int = 2,
                diag: dict | None = None) -> tuple[bytes, str] | None:
    """Guarded image GET -> (bytes, true extension) or None. Never raises.

    When `diag` is a dict it receives {"reason", "detail"} classifying the
    rejection: image_http_403/404/410/4xx/5xx, image_timeout,
    image_network_error, image_guard_rejected, image_too_large (with the
    measured size), image_unsupported_format (with the declared type).
    """
    last: tuple[str, str] = ("image_rejected", "")

    def fail(reason: str, detail: str = "") -> None:
        if isinstance(diag, dict):
            diag["reason"] = reason
            diag["detail"] = str(detail)[:160]

    try:
        public_http_url(url, resolve=True)
    except (ValueError, TypeError) as exc:
        fail("image_guard_rejected", f"{type(exc).__name__}: {exc}")
        return None
    except OSError as exc:
        # Transient DNS failure, not a policy rejection: retry later rather
        # than marking the image permanently unavailable.
        fail("image_network_error", f"{type(exc).__name__}: {exc}")
        return None
    ua = choose_user_agent(url)
    headers = {
        "User-Agent": ua,
        "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*,*/*;q=0.8",
    }
    if referer:
        headers["Referer"] = referer
    opener = public_opener()
    attempts = max(1, min(retries, 3))
    for attempt in range(attempts):
        ctype = ""
        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with opener.open(req, timeout=fetch_media.TIMEOUT) as resp:
                length = resp.headers.get("Content-Length")
                ctype = resp.headers.get("Content-Type") or ""
                if length and str(length).isdigit() and int(length) > MAX_IMAGE_BYTES:
                    fail("image_too_large", f"content_length={length}")
                    return None
                raw = resp.read(MAX_IMAGE_BYTES + 1)
        except ValueError as exc:
            fail("image_guard_rejected", f"{type(exc).__name__}: {exc}")
            return None  # a guarded redirect failure is never retried
        except urllib.error.HTTPError as exc:
            last = (fetch_media._classify_http_error(exc.code, "image"), f"HTTP {exc.code}")
            exc.close()
            if attempt + 1 < attempts:
                time.sleep(0.35 * (attempt + 1))
            continue  # an HTTP refusal is respected - never identity-switched
        except (OSError, urllib.error.URLError, http.client.HTTPException) as exc:
            reason = "image_timeout" if fetch_media._is_timeout(exc) else "image_network_error"
            last = (reason, f"{type(exc).__name__}: {exc}")
            if ua == USER_AGENT and is_transport_stall(exc):
                # Server-side stall of the honest identity: mark the host and
                # continue under the disclosed browser identity. Local failures
                # and HTTP refusals never switch identity.
                mark_browser_identity(url, type(exc).__name__)
                ua = FALLBACK_USER_AGENT
                headers["User-Agent"] = ua
            if attempt + 1 < attempts:
                time.sleep(0.35 * (attempt + 1))
            continue
        if len(raw) > MAX_IMAGE_BYTES:
            fail("image_too_large", f"bytes>{MAX_IMAGE_BYTES}")
            return None
        ext = sniff_image(raw)
        if not ext:
            fail("image_unsupported_format", f"declared={ctype[:60]}")
            return None
        return (raw, ext)
    fail(*last)
    return None


def _norm_url(url: object) -> str:
    return url.strip().rstrip("/") if isinstance(url, str) else ""


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _feed_item_image(item: ET.Element) -> str | None:
    """First publisher image attached to a feed item, guarded HTTPS or None.

    Accepts RSS <enclosure type="image/..."> and MRSS <media:content> /
    <media:thumbnail> - attachments from the publisher's own feed, the same
    provenance class as og:image. Everything else (comments, inline HTML,
    private hosts, plain HTTP, SVG-by-extension) is skipped.
    """
    for child in item.iter():
        tag = child.tag if isinstance(child.tag, str) else ""
        local = _local(tag)
        ns = tag[1:].split("}", 1)[0] if tag.startswith("{") else ""
        url = ""
        if local == "enclosure":
            typ = (child.attrib.get("type") or "").lower()
            candidate = child.attrib.get("url") or ""
            if typ.startswith("image/") or (not typ and IMG_EXT_RE.search(candidate)):
                url = candidate
        elif ns in MEDIA_NS and local in ("content", "thumbnail"):
            typ = (child.attrib.get("type") or "").lower()
            medium = (child.attrib.get("medium") or "").lower()
            candidate = child.attrib.get("url") or ""
            if medium == "image" or typ.startswith("image/") or (not typ and not medium and IMG_EXT_RE.search(candidate)):
                url = candidate
        if url:
            safe = fetch_media.safe_image_url(url)
            if safe and not safe.lower().endswith(".svg"):
                return safe
    return None


def _feed_item_link(item: ET.Element) -> str | None:
    """Item link, mirroring parse_feed: RSS text link, permalink guid, Atom href."""
    for child in item:
        if _local(child.tag) == "link":
            text = " ".join("".join(child.itertext()).split())
            if text:
                return text
            href = child.attrib.get("href")
            rel = child.attrib.get("rel", "alternate")
            if href and rel in ("alternate", ""):
                return href
    guid_el = next((c for c in item if _local(c.tag) == "guid"), None)
    if guid_el is not None and guid_el.attrib.get("isPermaLink", "true").lower() != "false":
        text = " ".join("".join(guid_el.itertext()).split())
        if text.startswith(("https://", "http://")):
            return text
    return None


def build_feed_media_index(raw_dir: Path = RAW_DIR) -> dict[str, dict]:
    """normalized article URL -> {"image", "credit"} from the latest raw XML
    snapshot per source. `credit` is the publisher's own MRSS media:credit
    (photographer name) when the feed gives one - rendered beside the image
    only when the image actually came from that feed (LEGAL_RISK.md R2).
    Local reads only; corrupt, oversized or entity-declaring XML is skipped,
    never fatal."""
    index: dict[str, dict] = {}
    raw_dir = Path(raw_dir)
    if not raw_dir.is_dir():
        return index
    for src_dir in sorted(raw_dir.iterdir()):
        if not src_dir.is_dir() or len(index) >= FEED_INDEX_CAP:
            continue
        xmls = sorted(src_dir.glob("*.xml"))
        if not xmls:
            continue
        try:
            xml_bytes = xmls[-1].read_bytes()
        except OSError:
            continue
        if len(xml_bytes) > MAX_FEED_BYTES or DOCTYPE_RE.search(xml_bytes.replace(b"\x00", b"")):
            continue
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError:
            continue
        added = 0
        for item in root.iter():
            if added >= FEED_INDEX_PER_SOURCE_CAP or len(index) >= FEED_INDEX_CAP:
                break
            if _local(item.tag) not in ("item", "entry"):
                continue
            link = _feed_item_link(item)
            if not link or not link.startswith(("https://", "http://")):
                continue
            image = _feed_item_image(item)
            if image and index.setdefault(_norm_url(link),
                                          {"image": image, "credit": ingest_rss._item_credit(item)}) is not None:
                added += 1
    return index


def _gate_active(entry: dict, now: datetime) -> bool:
    """True while a backed-off entry must not be retried."""
    stamp = entry.get("retry_after")
    if not isinstance(stamp, str):
        return False
    try:
        when = datetime.fromisoformat(stamp)
    except (ValueError, TypeError):
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when > now


def _apply_policy(entry: dict, reason: str, attempts: int, now: datetime) -> None:
    """Classify a negative result: permanent, capped-then-permanent, or backed off."""
    entry["attempts"] = attempts
    entry["reason"] = reason
    entry.pop("retry_after", None)
    if reason in PERMANENT_REASONS:
        entry["permanent"] = True
        return
    entry.pop("permanent", None)
    if reason in BLOCKED_REASONS:
        cap = BLOCKED_ATTEMPTS_CAP
    elif reason == "no_publisher_image":
        cap = QUIET_ATTEMPTS_CAP
    else:
        cap = TRANSIENT_ATTEMPTS_CAP
    if attempts >= cap:
        if reason in BLOCKED_REASONS:
            entry["permanent"] = True
        else:
            entry["retry_after"] = (now + timedelta(hours=BACKOFF_HOURS)).isoformat(timespec="seconds")


def write_health_ledger(doc: dict, path: Path = HEALTH) -> None:
    """Machine-room record: why images are missing, per domain, over time.

    Fail-soft by design - a ledger problem never breaks the media step.
    """
    try:
        entries = doc.get("media") if isinstance(doc.get("media"), dict) else {}
        reasons: dict[str, int] = {}
        by_domain: dict[str, dict] = {}
        for entry in entries.values():
            if not isinstance(entry, dict) or entry.get("file"):
                continue
            reason = str(entry.get("reason") or "unknown")
            reasons[reason] = reasons.get(reason, 0) + 1
            host = (urlparse(str(entry.get("article_url") or "")).hostname or "?").lower()
            dom = by_domain.setdefault(host, {"missing": 0, "reasons": {}})
            dom["missing"] += 1
            dom["reasons"][reason] = dom["reasons"].get(reason, 0) + 1
        snapshot = {
            "fetched_at": doc.get("fetched_at"),
            "scope": doc.get("scope_count"),
            "with_image": doc.get("with_image"),
            "missing": sum(reasons.values()),
            "reasons": dict(sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))),
            "by_domain": {
                host: {"missing": d["missing"], "top_reason": max(d["reasons"], key=d["reasons"].get)}
                for host, d in sorted(by_domain.items(), key=lambda kv: -kv[1]["missing"])
            },
            "permanent": sum(1 for e in entries.values() if isinstance(e, dict) and e.get("permanent")),
            "backed_off": sum(1 for e in entries.values() if isinstance(e, dict) and e.get("retry_after")),
            "budget_used": doc.get("fetched_this_run"),
            "feed_resolved": doc.get("feed_resolved"),
        }
        path = Path(path)
        history: list[dict] = []
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(prev, dict) and prev.get("method") == HEALTH_METHOD and isinstance(prev.get("history"), list):
                history = [h for h in prev["history"] if isinstance(h, dict)]
        except (OSError, ValueError):
            history = []
        history.append({k: snapshot[k] for k in ("fetched_at", "with_image", "missing", "budget_used", "feed_resolved")})
        out = {
            "method": HEALTH_METHOD,
            "updated_at": snapshot["fetched_at"],
            "latest": snapshot,
            "history": history[-HEALTH_HISTORY_CAP:],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        store_io.write_json_atomic(path, out)
    except (OSError, ValueError, TypeError, AttributeError):
        pass


def brief_uid(url: object) -> str:
    """Same identity as the renderer: sha256 of the canonical safe URL, 20 hex."""
    safe = brief.safe_url(url)
    return hashlib.sha256(safe.encode()).hexdigest()[:20] if safe else ""


def load_scope() -> list[dict]:
    """Top SCOPE_CAP candidates in display order.

    Prefers the previous ranked edition (the order the brief shows); falls
    back to the current enriched store. New articles enter the scope on the
    next run - an honest crawl lag, never a placeholder.
    """
    for path in (RANKED, ENRICHED):
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, list):
            cands = payload
        elif isinstance(payload, dict):
            cands = payload.get("candidates") or payload.get("ranked") or []
        else:
            continue
        cands = [c for c in cands if isinstance(c, dict) and c.get("url")]
        if cands:
            return cands[:SCOPE_CAP]
    return []


def load_manifest(path: Path = MANIFEST) -> dict:
    """uid -> entry from a brief-media manifest; foreign or corrupt -> {}.

    Accepts v1 as well as the current method so an upgrade never discards
    the existing image store (same uid/file shape; v1 negatives simply
    carry no attempt history yet).
    """
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict) or doc.get("method") not in LOADABLE_METHODS:
        return {}
    media = doc.get("media")
    if not isinstance(media, dict):
        return {}
    return {str(k): v for k, v in media.items() if isinstance(v, dict)}


def update_media(scope: list[dict], *, offline: bool = False,
                 media_dir: Path = MEDIA_DIR, manifest_path: Path = MANIFEST,
                 raw_dir: Path | None = None, health_path: Path | None = None) -> dict:
    """Advance the local image store by one collection; returns the new manifest.

    Keeps still-scoped entries whose file exists, retries negatives per the
    diagnosed-retry policy (permanent / blocked / backed-off entries cost
    nothing), fills gaps within the fetch budget - og:image first, the
    publisher's own feed attachment as fallback - rewrites the manifest
    durably, records the health ledger, then prunes orphan files. The
    manifest never references a missing file.

    raw_dir / health_path default to the module attributes resolved at call
    time so tests can patch them.
    """
    raw_root = Path(raw_dir) if raw_dir is not None else RAW_DIR
    health_file = Path(health_path) if health_path is not None else HEALTH
    previous = load_manifest(manifest_path)
    scoped: list[tuple[str, dict]] = []
    seen: set[str] = set()
    for cand in scope or []:
        if not isinstance(cand, dict):
            continue
        uid = brief_uid(cand.get("url"))
        if uid and uid not in seen:
            seen.add(uid)
            scoped.append((uid, cand))

    media_dir = Path(media_dir)
    manifest_path = Path(manifest_path)
    entries: dict[str, dict] = {}
    reused = 0
    now = datetime.now(timezone.utc)
    for uid, _ in scoped:
        prev = previous.get(uid)
        if not isinstance(prev, dict):
            continue
        file = prev.get("file")
        if isinstance(file, str) and FILE_RE.fullmatch(file) and not (media_dir / file).is_file():
            # The manifest named a file that is gone. Do not publish it, and
            # do not forget the article: a negative row is the diagnosed
            # absence, and the next online run can fetch again.
            entries[uid] = {
                "status": "proposed",
                "file": None,
                "article_url": prev.get("article_url"),
                "reason": "cache_file_missing",
                "fetched_at": now.isoformat(timespec="seconds"),
            }
            continue
        if isinstance(file, str) and FILE_RE.fullmatch(file) and (media_dir / file).is_file():
            # Backfill the intrinsic size for images stored before dimensions
            # were measured: reading a header is free and the browser then
            # reserves the box from the markup.
            if not (isinstance(prev.get("width"), int) and isinstance(prev.get("height"), int)):
                try:
                    with (media_dir / file).open("rb") as handle:
                        width, height = image_dimensions(handle.read(65536))
                except OSError:
                    width, height = None, None
                if width and height:
                    prev = {**prev, "width": width, "height": height}
            entries[uid] = prev
            reused += 1
        elif not file:
            entries[uid] = prev  # negative result - retried per policy below

    budget = 0 if offline else FETCH_CAP
    fetched = 0
    feed_resolved = 0
    index: dict[str, dict] | None = None
    for uid, cand in scoped:
        if budget <= 0:
            break
        prev = entries.get(uid)
        if isinstance(prev, dict) and prev.get("file"):
            continue
        if isinstance(prev, dict) and (prev.get("permanent") or _gate_active(prev, now)):
            continue  # diagnosed dead end or backed off - costs nothing
        url = brief.safe_url(cand.get("url"))
        if not url:
            entries.pop(uid, None)
            continue
        if index is None:
            index = build_feed_media_index(raw_root)
        feed_hit = index.get(_norm_url(url))
        feed_hit = feed_hit if isinstance(feed_hit, dict) else {}
        feed_img = feed_hit.get("image")
        feed_credit = feed_hit.get("credit")
        prev_reason = prev.get("reason") if isinstance(prev, dict) else None
        try:
            attempts = int(prev.get("attempts") or 0) if isinstance(prev, dict) else 0
        except (TypeError, ValueError):
            attempts = 0
        # The publisher was silent on the page itself but their own feed
        # carries an image: skip the HTML round-trip entirely.
        image_only = prev_reason == "no_publisher_image" and bool(feed_img)
        budget -= 1
        fetched += 1
        attempts += 1
        entry: dict = {
            "status": "proposed", "file": None, "image_url": None, "article_url": url,
            "fetched_at": now.isoformat(timespec="seconds"),
        }
        diag: dict = {}
        html = None if image_only else fetch_media.fetch_html(url, diag=diag)
        image_url = None
        source = None
        if html:
            image_url = fetch_media.extract_og(html, url)
            if image_url:
                source = "og:image"
            elif feed_img:
                image_url, source = feed_img, "feed"
        elif feed_img and diag.get("reason") not in PERMANENT_REASONS:
            # Article page unreachable (bot wall, timeout) but the publisher's
            # own feed attaches an image - same provenance, honest fallback.
            image_url, source = feed_img, "feed"
        if image_url:
            img_diag: dict = {}
            got = fetch_image(image_url, referer=url, diag=img_diag)
            if got is None:
                reason = img_diag.get("reason") or "image_rejected"
                entry.update(reason=reason, image_url=image_url, image_source=source,
                             last_error=img_diag.get("detail", ""))
                _apply_policy(entry, reason, attempts, now)
            else:
                raw, ext = got
                # Content-addressed name: the bytes decide the filename. A
                # publisher replacing an image at the same URL yields a new
                # file (the old one is pruned), and /media/* can be cached
                # immutably with no stale-copy risk.
                name = f"{hashlib.sha256(raw).hexdigest()[:20]}.{ext}"
                media_dir.mkdir(parents=True, exist_ok=True)
                part = media_dir / f".{name}.part"
                part.write_bytes(raw)
                part.replace(media_dir / name)
                entry.update(reason="publisher og:image" if source == "og:image" else "publisher feed media",
                             image_url=image_url, image_source=source,
                             file=name, bytes=len(raw), format=ext, attempts=attempts)
                width, height = image_dimensions(raw)
                if width and height:
                    entry["width"], entry["height"] = width, height
                if source == "feed":
                    feed_resolved += 1
                    if isinstance(feed_credit, str) and feed_credit.strip():
                        # The photographer name the publisher attached to THIS
                        # feed image - only honest beside a feed-sourced file.
                        entry["credit"] = feed_credit.strip()[:120]
        else:
            reason = "no_publisher_image" if html else (diag.get("reason") or "article_fetch_failed")
            entry.update(reason=reason, last_error=diag.get("detail", ""))
            _apply_policy(entry, reason, attempts, now)
        entries[uid] = entry
        time.sleep(SLEEP)

    if not offline:
        # Scoped articles the fetch budget never reached are an absence, not
        # a silent hole in the manifest. They are retried on the next run.
        for uid, cand in scoped:
            if uid in entries:
                continue
            url = brief.safe_url(cand.get("url"))
            if not url:
                continue
            entries[uid] = {
                "status": "proposed",
                "file": None,
                "article_url": url,
                "reason": "fetch_budget_exhausted",
                "fetched_at": now.isoformat(timespec="seconds"),
            }

    # The manifest only ever references files that exist on disk.
    entries = {
        uid: e for uid, e in entries.items()
        if not e.get("file") or (media_dir / str(e["file"])).is_file()
    }
    reason_counts: dict[str, int] = {}
    for e in entries.values():
        if not e.get("file"):
            r = str(e.get("reason") or "unknown")
            reason_counts[r] = reason_counts.get(r, 0) + 1
    doc = {
        "method": METHOD,
        "status": "proposed",
        "fetched_at": now.isoformat(timespec="seconds"),
        "scope_count": len(scoped),
        "entry_count": len(entries),
        "with_image": sum(1 for e in entries.values() if e.get("file")),
        "fetched_this_run": fetched,
        "reused": reused,
        "feed_resolved": feed_resolved,
        "permanent_count": sum(1 for e in entries.values() if e.get("permanent")),
        "backed_off_count": sum(1 for e in entries.values() if e.get("retry_after")),
        "reasons": dict(sorted(reason_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "media": entries,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    store_io.write_json_atomic(manifest_path, doc)
    write_health_ledger(doc, health_file)

    # Prune orphans only after the new manifest is durable.
    referenced = {str(e["file"]) for e in entries.values() if e.get("file")}
    pruned = 0
    if media_dir.is_dir():
        for path in sorted(media_dir.iterdir()):
            if not path.is_file() or path.is_symlink():
                continue
            stale_part = path.name.startswith(".") and path.name.endswith(".part")
            if path.name in referenced or not (stale_part or FILE_RE.fullmatch(path.name)):
                continue
            try:
                path.unlink()
                pruned += 1
            except OSError:
                pass
    doc["pruned"] = pruned
    return doc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch publisher preview images for the brief.")
    parser.add_argument("--offline", action="store_true",
                        help="Reuse the cached store; make no network requests")
    args = parser.parse_args(argv)
    scope = load_scope()
    if not scope:
        print("brief media: no candidates to scope; keeping previous manifest")
        return 0
    try:
        doc = update_media(scope, offline=args.offline)
    except (OSError, ValueError, TypeError) as exc:
        # The media sentinel is an aid: a corrupt manifest field must never stop
        # the chain (module law: always exits 0, keeping the previous manifest).
        print(f"brief media: FAIL {exc} - keeping previous manifest")
        return 0
    print(f"brief media -> {MANIFEST.relative_to(ROOT)}")
    print(f"  scope={doc['scope_count']} with_image={doc['with_image']} "
          f"fetched={doc['fetched_this_run']} reused={doc['reused']} "
          f"feed={doc['feed_resolved']} permanent={doc['permanent_count']} "
          f"pruned={doc['pruned']}"
          + (" (offline)" if args.offline else ""))
    if doc["reasons"]:
        top = ", ".join(f"{r}={n}" for r, n in list(doc["reasons"].items())[:4])
        print(f"  missing: {top}")
    # Fail-soft by design: a media outage never blocks the news pipeline.
    return 0


if __name__ == "__main__":
    sys.exit(main())
