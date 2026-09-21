"""Vigie faces v0.2 - map-scoped publisher faces. Never invent.

Fetches og:image / twitter:image only for what the lookout shows:
Near me, Province/Linked life-hit crown, fight Stage voices.
Not all 244. Empty strip when the publisher is silent.

NOTE: the v2 media pipeline (scripts/fetch_brief_media.py, brief-media-v2) is
the production image collector; this module's main() is legacy and not in the
pipeline. The network helpers (fetch_html, extract_og, safe_image_url) remain
the shared, guarded implementation imported by fetch_brief_media.
"""
from __future__ import annotations

import json
import http.client
import sys
import time
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import rank_display as rd  # noqa: E402
from ingest_rss import (  # noqa: E402
    public_http_url, public_opener, choose_user_agent, mark_browser_identity,
    is_transport_stall, USER_AGENT, FALLBACK_USER_AGENT,
)
import store_io  # noqa: E402 (atomic writes for the legacy faces store)

ENRICHED = ROOT / "data" / "normalized" / "latest_enriched.json"
RANKED = ROOT / "data" / "normalized" / "latest_ranked.json"
ISSUES = ROOT / "data" / "issues" / "latest_issues.json"
OUT = ROOT / "data" / "media" / "latest_faces.json"

TIMEOUT = 14
DISPLAY_CAP = 30
MAX_FETCH = 90  # map-scoped ceiling — wallet thin
SLEEP = 0.08
MAX_HTML_BYTES = 900_000


def safe_image_url(url: str | None, base: str = "") -> str | None:
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        value = public_http_url(urljoin(base, url.strip()))
    except (TypeError, ValueError):
        return None
    return value if urlparse(value).scheme.lower() == "https" else None


class _ImageMetadata(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.images: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "meta":
            return
        values = dict(attrs)
        name = (values.get("property") or values.get("name") or "").lower()
        if name in {"og:image", "twitter:image", "twitter:image:src"} and values.get("content"):
            self.images.append(values["content"])


def extract_og(html: str, base: str) -> str | None:
    parser = _ImageMetadata()
    parser.feed(html)
    for raw in parser.images:
        image = safe_image_url(raw, base)
        if image:
            return image
    return None


def _classify_http_error(code: int, prefix: str) -> str:
    if code in (403, 404, 410):
        return f"{prefix}_http_{code}"
    if 500 <= code <= 599:
        return f"{prefix}_http_5xx"
    return f"{prefix}_http_4xx"


def _is_timeout(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", None)
    return isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError)


def fetch_html(url: str, *, retries: int = 2, diag: dict | None = None) -> str | None:
    """Browser-like GET with one retry — Journal de Québec often fails once.

    When `diag` is a dict it receives {"reason", "detail"} classifying the
    failure (media-sentinel diagnosis): article_http_403/404/410/4xx/5xx,
    article_timeout, article_network_error, article_guard_rejected,
    article_too_large. An unfilled diag means the caller was mocked or the
    failure predates classification.
    """
    last: tuple[str, str] = ("article_fetch_failed", "")

    def fail(reason: str, detail: str = "") -> None:
        if isinstance(diag, dict):
            diag["reason"] = reason
            diag["detail"] = str(detail)[:160]

    try:
        public_http_url(url, resolve=True)
    except ValueError as exc:
        fail("article_guard_rejected", f"ValueError: {exc}")
        return None
    except OSError as exc:
        # A transient resolver failure (gaierror/SERVFAIL) is not a policy
        # rejection: classify it as network so it is retried, never marked
        # permanent and blinded forever.
        fail("article_network_error", f"{type(exc).__name__}: {exc}")
        return None
    host = urlparse(url).hostname or ""
    ua = choose_user_agent(url)
    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "fr-CA,fr;q=0.9,en-CA;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if host in {"journaldequebec.com", "www.journaldequebec.com", "journaldemontreal.com", "www.journaldemontreal.com"}:
        headers["Referer"] = "https://www.journaldequebec.com/"
    opener = public_opener()
    attempts = max(1, min(retries, 3))
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with opener.open(req, timeout=TIMEOUT) as resp:
                content_length = resp.headers.get("Content-Length")
                if content_length and str(content_length).isdigit() and int(content_length) > MAX_HTML_BYTES:
                    fail("article_too_large", f"content_length={content_length}")
                    return None
                raw = resp.read(MAX_HTML_BYTES + 1)
                if len(raw) > MAX_HTML_BYTES:
                    fail("article_too_large", f"bytes>{MAX_HTML_BYTES}")
                    return None
                return raw.decode("utf-8", errors="replace")
        except ValueError as exc:
            fail("article_guard_rejected", f"{type(exc).__name__}: {exc}")
            return None
        except urllib.error.HTTPError as exc:
            last = (_classify_http_error(exc.code, "article"), f"HTTP {exc.code}")
            # An HTTP refusal is respected - never retried under another identity.
        except (OSError, urllib.error.URLError, http.client.HTTPException) as exc:
            reason = "article_timeout" if _is_timeout(exc) else "article_network_error"
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
    fail(*last)
    return None


def load_candidates() -> list[dict]:
    if RANKED.exists():
        payload = json.loads(RANKED.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return payload
        cands = payload.get("candidates") or payload.get("ranked") or []
        if cands:
            return cands
    if ENRICHED.exists():
        return json.loads(ENRICHED.read_text(encoding="utf-8")).get("candidates") or []
    return []


def load_issues() -> list[dict]:
    if not ISSUES.exists():
        return []
    return json.loads(ISSUES.read_text(encoding="utf-8")).get("issues") or []


def issue_candidate_ids(iss: dict) -> list[str]:
    ids: list[str] = []
    for tension in iss.get("tensions") or []:
        for it in tension.get("items") or []:
            cid = it.get("candidate_id") or it.get("id")
            if cid:
                ids.append(str(cid))
    return ids


def map_scope(cands: list[dict], issues: list[dict]) -> list[dict]:
    """IDs the citizen can see: Near me, life-hit Province/Linked crowns, fight voices."""
    by_id = {str(c.get("id")): c for c in cands if c.get("id")}
    buckets: dict[str, list] = {"near": [], "province": [], "linked": []}
    for c in cands:
        nest = rd.section_for(c)
        if nest == "near" and rd.is_booth_or_brief(c):
            nest = "province"
        if nest in buckets:
            buckets[nest].append(c)

    scoped: dict[str, dict] = {}

    def add(c: dict) -> None:
        cid = str(c.get("id") or "")
        if cid and cid not in scoped:
            scoped[cid] = c

    for c in buckets["near"]:
        add(c)

    for nest in ("province", "linked"):
        ordered = rd.impact_first(buckets[nest])
        life = [c for c in ordered if not rd.is_booth_or_brief(c)]
        for c in life[:DISPLAY_CAP]:
            add(c)

    for iss in issues[:12]:
        for cid in issue_candidate_ids(iss):
            if cid in by_id:
                add(by_id[cid])

    return list(scoped.values())


def main() -> int:
    cands = load_candidates()
    if not cands:
        print(f"Missing candidates ({RANKED} / {ENRICHED})")
        return 1
    issues = load_issues()
    scope = map_scope(cands, issues)

    faces: dict[str, dict] = {}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
            faces = dict(prev.get("faces") or {})
        except Exception:
            faces = {}
    scoped_ids = {str(c.get("id")) for c in scope if c.get("id")}
    faces = {cid: face for cid, face in faces.items()
             if cid in scoped_ids and isinstance(face, dict)
             and (not face.get("image_url") or safe_image_url(face["image_url"]))}

    fetched = 0
    reused = 0
    retried = 0
    for c in scope:
        cid = str(c.get("id") or "")
        url = c.get("url") or ""
        if not cid or not url:
            continue
        try:
            url = public_http_url(url)
        except (TypeError, ValueError):
            faces.pop(cid, None)
            continue
        prev = faces.get(cid) or {}
        if prev.get("image_url") and prev.get("article_url") == url:
            reused += 1
            continue
        if fetched >= MAX_FETCH:
            continue
        # Retry prior fetch_failed / no-image for map-scoped (JdQ often needs a second try)
        if prev.get("reason") in ("fetch_failed", "no og:image"):
            retried += 1
        html = fetch_html(url, retries=2)
        fetched += 1
        if not html:
            faces[cid] = {
                "status": "proposed",
                "image_url": None,
                "source": "og:image",
                "article_url": url,
                "reason": "fetch_failed",
            }
            continue
        img = extract_og(html, url)
        faces[cid] = {
            "status": "proposed",
            "image_url": img,
            "source": "og:image",
            "article_url": url,
            "reason": "publisher og:image" if img else "no og:image",
        }
        time.sleep(SLEEP)

    out = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "method": "faces-v0.2 map-scoped og:image only; never invent; JdQ retry+Referer",
        "map_scope_count": len(scope),
        "face_count": len(faces),
        "with_image": sum(1 for v in faces.values() if v.get("image_url")),
        "fetched_this_run": fetched,
        "reused": reused,
        "retried_prior_fail": retried,
        "faces": faces,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    store_io.write_json_atomic(OUT, out)
    print(f"faces -> {OUT.relative_to(ROOT) if OUT.is_relative_to(ROOT) else OUT}")
    print(
        f"  map_scope={len(scope)} entries={len(faces)} with_image={out['with_image']} "
        f"fetched={fetched} reused={reused} retried={retried}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
