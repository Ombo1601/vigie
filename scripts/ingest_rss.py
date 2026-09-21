"""Vigie v0 — ingest enabled RSS/Atom feeds into data/raw (append-only).

Stdlib only. No pip. Parses the subset of sources.yaml we actually ship.
Does not enrich, rank, or invent news. Fetch is the scar.
"""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
import sys
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import store_io

ROOT = Path(__file__).resolve().parent.parent
SOURCES_PATH = ROOT / "sources.yaml"
RAW_DIR = ROOT / "data" / "raw"
# Identity law (LEGAL_RISK.md R5/R9): the honest reader identity is the
# default. Some origins (CBC is a documented scar) stall or reset transport
# for automated-reader identities; those hosts are remembered in
# _ua_policy.json and read with the disclosed browser identity instead. An
# HTTP refusal (403/406/410/429) is ALWAYS respected - never retried under
# another identity, never circumvented. Both identities are disclosed in
# legal.md.
USER_AGENT = "Vigie/0.2 (+https://vigieqc.com/methode/legal.html; news aggregator; non-commercial)"
FALLBACK_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Vigie/0.2"
)
UA_POLICY_PATH = RAW_DIR / "_ua_policy.json"
TIMEOUT = 25
RETRIES = 3
MAX_FEED_BYTES = 8 * 1024 * 1024
RETENTION_DAYS = 30  # raw snapshot retention (LEGAL_RISK.md R6)
UA_POLICY_MAX_AGE_DAYS = 30  # re-probe the browser identity after this window

# CBC often resets Python urllib; URL alternates stay as a second path.
URL_ALTERNATES = {
    "https://rss.cbc.ca/lineup/canada-montreal.xml": [
        "https://www.cbc.ca/cmlink/rss-canada-montreal",
        "https://www.cbc.ca/webfeed/rss/rss-canada-montreal",
    ],
    "https://rss.cbc.ca/lineup/politics.xml": [
        "https://www.cbc.ca/cmlink/rss-politics",
        "https://www.cbc.ca/webfeed/rss/rss-politics",
    ],
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _load_ua_policy() -> dict:
    try:
        doc = json.loads(UA_POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _policy_is_fresh(entry: dict) -> bool:
    marked = entry.get("marked_at")
    if not isinstance(marked, str):
        return False
    try:
        when = datetime.fromisoformat(marked)
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return utc_now() - when <= timedelta(days=UA_POLICY_MAX_AGE_DAYS)


def choose_user_agent(url: str) -> str:
    """Honest identity by default; the disclosed browser identity only for a
    host that stalled an automated reader at transport level (never for a
    refusal), and only while the marker is fresh. A stale marker is re-probed
    under the honest identity, so one bad network day cannot switch a host
    forever."""
    entry = _load_ua_policy().get(_host_of(url))
    if isinstance(entry, dict) and entry.get("identity") == "browser" and _policy_is_fresh(entry):
        return FALLBACK_USER_AGENT
    return USER_AGENT


def is_transport_stall(exc: BaseException) -> bool:
    """True only for a server-side transport stall of the honest identity:
    a timeout, reset, refused connection, truncated exchange or a TLS
    handshake the server aborted. Local failures (DNS resolution, certificate
    trust, the SSRF guard) are never blamed on the origin and never switch
    identity. HTTP refusals are handled by the caller and never reach this
    predicate."""
    if isinstance(exc, (socket.gaierror, ssl.SSLCertVerificationError, ssl.CertificateError,
                        http.client.InvalidURL, http.client.UnknownProtocol)):
        return False
    reason = getattr(exc, "reason", None)
    if isinstance(reason, BaseException):
        return is_transport_stall(reason)
    return isinstance(exc, (TimeoutError, ConnectionError, http.client.HTTPException,
                            urllib.error.ContentTooShortError, ssl.SSLError))


def mark_browser_identity(url: str, reason: str) -> None:
    """Record a transport-level stall for a host. Fail-soft: an unwritable
    policy file only means the stall is paid again on the next run."""
    host = _host_of(url)
    if not host:
        return
    try:
        policy = _load_ua_policy()
        policy[host] = {"identity": "browser", "reason": str(reason)[:80],
                        "marked_at": utc_now().isoformat(timespec="seconds")}
        store_io.write_json_atomic(UA_POLICY_PATH, policy)
    except OSError:
        pass


def _scalar(val: str):
    val = val.strip()
    quoted = (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'"))
    if quoted:
        return val[1:-1]
    # YAML comments start at a '#' that begins the value or follows whitespace;
    # a '#' inside a URL is data, never a comment.
    if val.startswith("#") or " #" in val:
        val = val.split("#", 1)[0].strip()
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        return val[1:-1]
    if val in ("", "|", ">", "null", "~"):
        return None
    if val.lower() in ("true", "false"):
        return val.lower() == "true"
    if re.fullmatch(r"-?\d+", val):
        return int(val)
    return val


def load_enabled_by_type(path: Path, source_type: str) -> list[dict]:
    """Minimal parser for our sources.yaml list-of-maps. Not a general YAML engine."""
    text = path.read_text(encoding="utf-8")
    m = re.search(r"(?ms)^sources:\n(.*?)(?=^[a-zA-Z].*:|\Z)", text)
    if not m:
        raise SystemExit(f"No sources: block in {path}")
    body = m.group(1)
    chunks = re.split(r"\n  - id:", "\n" + body)
    out: list[dict] = []
    for chunk in chunks:
        chunk = chunk.strip("\n")
        if not chunk.strip() or chunk.strip().startswith("#"):
            continue
        if not chunk.lstrip().startswith("id:"):
            chunk = "id:" + chunk
        rec: dict = {}
        for raw_line in chunk.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            if line.startswith("- "):
                line = line[2:]
            key, _, val = line.partition(":")
            rec[key.strip()] = _scalar(val)
        if rec.get("enabled") is True and rec.get("type") == source_type and rec.get("id") and rec.get("url"):
            out.append(rec)
    return out


def load_enabled_rss(path: Path) -> list[dict]:
    return load_enabled_by_type(path, "rss")


def public_http_url(url: str, *, resolve: bool = False) -> str:
    """Reject executable URLs and local network targets before using feed input."""
    if not isinstance(url, str) or any(ord(ch) < 32 for ch in url) or "\\" in url:
        raise ValueError("Invalid URL characters")
    parsed = urlsplit(url.strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() not in {"https", "http"} or not host:
        raise ValueError("An absolute HTTP(S) URL is required")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Credentials in URLs are not allowed")
    if parsed.port not in (None, 80, 443):
        raise ValueError("Only web ports are allowed")
    if ("." not in host and ":" not in host) or host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or "%" in host:
        raise ValueError("Local hostnames are not allowed")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is None and re.fullmatch(r"(?:0x[0-9a-f]+|\d+)(?:\.(?:0x[0-9a-f]+|\d+))*", host, re.I):
        raise ValueError("Ambiguous numeric hostnames are not allowed")
    if address is not None and not _public_address(address):
        raise ValueError("Non-public IP addresses are not allowed")
    if resolve:
        addresses = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        if not addresses or any(not _public_address(ipaddress.ip_address(info[4][0])) for info in addresses):
            raise ValueError("Host does not resolve exclusively to public addresses")
    return url.strip()


def _public_address(address) -> bool:
    return address.is_global and not address.is_multicast


# http.client signals "no explicit timeout" with socket's private sentinel.
# Capture it once with a fallback so a future stdlib rename degrades to
# "always set the timeout" instead of an AttributeError at import.
_DEFAULT_TIMEOUT = getattr(socket, "_GLOBAL_DEFAULT_TIMEOUT", object())


def _public_connection(address, timeout=_DEFAULT_TIMEOUT, source_address=None):
    """Resolve once, validate every answer, then connect to an exact IP address.

    HTTP Host and HTTPS certificate/SNI still use the original hostname. No second
    resolver lookup is allowed between validation and opening the socket.
    """
    host, port = address
    answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not answers or any(not _public_address(ipaddress.ip_address(info[4][0])) for info in answers):
        raise ValueError("Connection destination is not public")
    last_error = None
    for family, socktype, proto, _, sockaddr in answers:
        connection = socket.socket(family, socktype, proto)
        try:
            if timeout is not _DEFAULT_TIMEOUT:
                connection.settimeout(timeout)
            if source_address:
                connection.bind(source_address)
            connection.connect(sockaddr)
            return connection
        except OSError as exc:
            connection.close()
            last_error = exc
    raise last_error or OSError("No usable public address")


class _PublicHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _public_connection


class _PublicHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _public_connection


class _PublicHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PublicHTTPConnection, req)


class _PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PublicHTTPSConnection, req, context=self._context)


class PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    max_redirections = 5
    max_repeats = 2

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_http_url(newurl, resolve=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def public_opener():
    """Shared outbound HTTP boundary for feeds and optional publisher metadata."""
    ctx = ssl.create_default_context()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), PublicRedirectHandler(),
        _PublicHTTPHandler(), _PublicHTTPSHandler(context=ctx),
    )


def _http_cache_path() -> Path:
    """Resolved through RAW_DIR at call time so tests can redirect it."""
    return RAW_DIR / "_http_cache.json"


def _body_cache_path(url: str) -> Path:
    return RAW_DIR / "_bodies" / (hashlib.sha256(url.encode()).hexdigest()[:32] + ".body")


def _load_http_cache() -> dict:
    try:
        doc = json.loads(_http_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _save_http_cache(cache: dict) -> None:
    try:
        store_io.write_json_atomic(_http_cache_path(), cache, indent=1)
    except (OSError, ValueError, TypeError):
        pass


def _read_body_cache(url: str) -> bytes | None:
    try:
        raw = _body_cache_path(url).read_bytes()
    except OSError:
        return None
    return raw or None


def _write_body_cache(url: str, raw: bytes) -> None:
    try:
        store_io.write_bytes_atomic(_body_cache_path(url), raw)
    except OSError:
        pass


def _delete_body_cache(url: str) -> None:
    try:
        _body_cache_path(url).unlink()
    except OSError:
        pass


def fetch_bytes(url: str) -> tuple[bytes, str | None]:
    """Fetch with retries, optional alternate URLs (CBC scar) and conditional GET.

    Bandwidth is rent: when a server previously sent an ETag or
    Last-Modified, the next request carries the matching conditional header
    and a 304 answer returns the cached body - zero payload bytes on the
    wire, caller unchanged (the snapshot digest simply repeats, which
    ingest_one records as not_modified). A validator without a cached body
    falls back to one unconditional fetch. Cache files live beside the raw
    snapshots; a corrupt cache degrades to unconditional fetching, never to
    a wrong body.
    """
    candidates = [url] + list(URL_ALTERNATES.get(url, []))
    opener = public_opener()
    cache = _load_http_cache()
    last_err: Exception | None = None
    for candidate in candidates:
        ua = choose_user_agent(candidate)
        entry = cache.get(candidate)
        entry = entry if isinstance(entry, dict) else {}
        validators: dict[str, str] = {}
        if isinstance(entry.get("etag"), str) and entry["etag"]:
            validators["If-None-Match"] = entry["etag"]
        if isinstance(entry.get("last_modified"), str) and entry["last_modified"]:
            validators["If-Modified-Since"] = entry["last_modified"]
        refused = False
        for conditional in ((True, False) if validators else (False,)):
            for attempt in range(1, RETRIES + 1):
                try:
                    public_http_url(candidate, resolve=True)
                    headers = {
                        "User-Agent": ua,
                        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
                        "Accept-Language": "en-CA,fr-CA;q=0.9,en;q=0.8",
                    }
                    if conditional:
                        headers.update(validators)
                    req = urllib.request.Request(candidate, headers=headers, method="GET")
                    with opener.open(req, timeout=TIMEOUT) as resp:
                        content_type = resp.headers.get("Content-Type")
                        content_length = resp.headers.get("Content-Length")
                        # Header hint only — a malformed or absent value must not
                        # fail the fetch; the bounded read below is the real cap.
                        try:
                            declared = int(content_length) if content_length else None
                        except ValueError:
                            declared = None
                        if declared is not None and declared > MAX_FEED_BYTES:
                            raise ValueError("Feed exceeds size limit")
                        body = resp.read(MAX_FEED_BYTES + 1)
                        if len(body) > MAX_FEED_BYTES:
                            raise ValueError("Feed exceeds size limit")
                        etag = resp.headers.get("ETag")
                        last_modified = resp.headers.get("Last-Modified")
                        has_etag = isinstance(etag, str) and bool(etag)
                        has_lm = isinstance(last_modified, str) and bool(last_modified)
                        if has_etag or has_lm:
                            cache[candidate] = {
                                "etag": etag if has_etag else None,
                                "last_modified": last_modified if has_lm else None,
                                "content_type": content_type,
                                "updated_at": utc_now().isoformat(timespec="seconds"),
                            }
                            _save_http_cache(cache)
                            _write_body_cache(candidate, body)
                        else:
                            # A 200 with no validators invalidates any previous
                            # ETag/body pair: a later confused 304 must never
                            # resurrect a body older than the one just observed.
                            if candidate in cache:
                                cache.pop(candidate, None)
                                _save_http_cache(cache)
                            _delete_body_cache(candidate)
                        return body, content_type
                except ValueError:
                    raise
                except urllib.error.HTTPError as e:
                    if e.code == 304 and conditional:
                        cached_body = _read_body_cache(candidate)
                        if cached_body is not None:
                            return cached_body, entry.get("content_type")
                        break  # validator without body: refetch unconditionally
                    # An HTTP refusal is respected: never identity-switched, and
                    # a 4xx on the plain request is final for this URL. But a
                    # 4xx raised *because of* the validators (412, a proxy that
                    # dislikes If-None-Match) is the very case the unconditional
                    # second pass exists for - so let it fall through first.
                    last_err = e
                    if 400 <= e.code < 500:
                        if not conditional:
                            refused = True
                        break
                    continue
                except Exception as e:
                    if ua == USER_AGENT and is_transport_stall(e):
                        # Server-side stall of the honest identity (the CBC
                        # scar): mark the host and retry under the disclosed
                        # browser identity. Local failures and refusals never
                        # land here.
                        mark_browser_identity(candidate, type(e).__name__)
                        ua = FALLBACK_USER_AGENT
                    last_err = e
                    continue
            if refused:
                break
    if last_err is None:
        # Unreachable in practice (candidates is never empty), but `assert`
        # is stripped under `python -O` and `raise None` would mask the real
        # failure with a TypeError.
        raise RuntimeError("fetch failed without a recorded error")
    raise last_err


def _local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _text(el: ET.Element | None) -> str | None:
    if el is None:
        return None
    t = " ".join("".join(el.itertext()).split())
    return t or None


def _child(el: ET.Element, name: str) -> ET.Element | None:
    return next((child for child in el if _local(child.tag).lower() == name.lower()), None)


def _link_text(item: ET.Element) -> str | None:
    """The article URL from an RSS/RDF item <link>.

    An item is often preceded by a namespaced <atom:link rel="self" href="…"/>
    whose URL lives in an attribute and whose text is empty. Matching the first
    child by local name would let that namesake steal the article URL and drop
    the item silently, so prefer an unnamespaced <link> that carries text."""
    fallback = None
    for child in item:
        if _local(child.tag).lower() != "link":
            continue
        text = _text(child)
        if not text:
            continue
        if isinstance(child.tag, str) and not child.tag.startswith("{"):
            return text
        fallback = fallback or text
    return fallback


EMAIL_AUTHOR_RE = re.compile(r"^[^\s@]+@[^\s@]+\s*\(([^)]+)\)")
# Linear token regex: \S*@\S* is quadratic on a long @-free token (each start
# position scans to the end), so a hostile author field could hang ingest. The
# [^\s@] classes cannot cross a separator, so matching stays linear.
EMAIL_TOKEN_RE = re.compile(r"[^\s@]*@[^\s@]*")


def _clean_person(value: str | None, limit: int = 120) -> str | None:
    """Person fields can arrive as 'mail (Name)' (RSS 2.0 author), as a bare
    e-mail, or as 'Name <mail>' / 'Name mail'. An e-mail address is never a
    displayable name: it is dropped, never published."""
    if not isinstance(value, str) or not value.strip():
        return None
    # Cap before matching: the output is <=limit, and a megabyte-long hostile
    # creator must never be scanned.
    value = " ".join(value.split())[:1000]
    m = EMAIL_AUTHOR_RE.match(value)
    if m:
        value = m.group(1)
    value = " ".join(EMAIL_TOKEN_RE.sub(" ", value).split())
    value = value.strip(" -–—,;|<>()[]")
    return value[:limit] or None


def _item_author(item: ET.Element) -> str | None:
    """Article author as given by the publisher's own feed (dc:creator, author).

    Attribution law (LEGAL_RISK.md R1): fair dealing for news reporting
    requires the author name when the source gives it - both Canadian cases
    lost on missing author credit (Cedrom-SNi, Stross)."""
    return (_clean_person(_text(_child(item, "creator")))
            or _clean_person(_text(_child(item, "author"))))


def _item_credit(item: ET.Element) -> str | None:
    """Photographer credit attached to the item (MRSS media:credit), when the
    publisher gives one. Only ever rendered beside a feed-sourced image."""
    for child in item.iter():
        tag = child.tag if isinstance(child.tag, str) else ""
        if tag.startswith("{") and _local(tag).lower() == "credit":
            cleaned = _clean_person(_text(child))
            if cleaned:
                return cleaned
    return None


def parse_feed(xml_bytes: bytes, base_url: str | None = None,
               stats: dict | None = None) -> list[dict]:
    if len(xml_bytes) > MAX_FEED_BYTES:
        raise ValueError("Feed exceeds size limit")
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", xml_bytes.replace(b"\x00", b""), re.I):
        raise ValueError("Feed document type and entity declarations are not allowed")
    root = ET.fromstring(xml_bytes)
    tag = _local(root.tag).lower()
    items: list[dict] = []
    if tag == "rss" or tag == "rdf":
        nodes = [n for n in root.iter() if _local(n.tag).lower() == "item"]
        for item in nodes:
            title = _text(_child(item, "title"))
            link = _link_text(item)
            if not link:
                guid_el = _child(item, "guid")
                if guid_el is not None and guid_el.attrib.get("isPermaLink", "true").lower() != "false":
                    guid_text = _text(guid_el)
                    if guid_text and guid_text.startswith(("https://", "http://")):
                        link = guid_text
            desc = _text(_child(item, "description")) or _text(_child(item, "encoded"))
            pub = _text(_child(item, "pubdate")) or _text(_child(item, "date"))
            guid_el = _child(item, "guid")
            guid = _text(guid_el)
            items.append(
                {
                    "title": title,
                    "url": link,
                    "body": desc,
                    "published_at": pub,
                    "guid": guid,
                    "author": _item_author(item),
                    "credit": _item_credit(item),
                }
            )
    elif tag == "feed":
        for entry in list(root):
            if _local(entry.tag).lower() != "entry":
                continue
            title = _text(entry.find("title"))
            if title is None:
                for child in entry:
                    if _local(child.tag).lower() == "title":
                        title = _text(child)
                        break
            link = None
            for child in entry:
                if _local(child.tag).lower() == "link":
                    href = child.attrib.get("href")
                    rel = child.attrib.get("rel", "alternate")
                    if href and rel in ("alternate", ""):
                        link = href
                        break
            summary = None
            for child in entry:
                loc = _local(child.tag).lower()
                if loc in ("summary", "content"):
                    summary = _text(child)
                    if summary:
                        break
            published = _text(_child(entry, "published"))
            updated = _text(_child(entry, "updated"))
            eid = None
            for child in entry:
                if _local(child.tag).lower() == "id":
                    eid = _text(child)
                    break
            author_el = _child(entry, "author")
            author = None
            if author_el is not None:
                author = (_clean_person(_text(_child(author_el, "name")))
                          or _clean_person(_text(author_el)))
            items.append(
                {
                    "title": title,
                    "url": link,
                    "body": summary,
                    "published_at": published,
                    "updated_at": updated,
                    "guid": eid,
                    "author": author,
                    "credit": _item_credit(entry),
                }
            )
    else:
        raise ValueError(f"Unsupported feed root: {tag}")
    if base_url:
        for item in items:
            if item.get("url"):
                item["url"] = urljoin(base_url, item["url"])
    kept = [it for it in items if it.get("url") or it.get("title")]
    if stats is not None:
        # "every miss must be diagnosed": an item carrying neither URL nor
        # title is dropped downstream, so it is counted here, never hidden.
        stats["item_nodes"] = len(items)
        stats["dropped_no_url_title"] = len(items) - len(kept)
    return kept


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ingest_one(src: dict, fetched_at: datetime) -> dict:
    source_id = src["id"]
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", source_id):
        raise ValueError("Invalid source identifier")
    url = src["url"]
    dest_dir = RAW_DIR / source_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = fetched_at.strftime("%Y%m%dT%H%M%SZ")
    try:
        raw, content_type = fetch_bytes(url)
    except Exception as e:
        err = {
            "ok": False,
            "source_id": source_id,
            "url": url,
            "error": f"{type(e).__name__}: {e}",
            "fetched_at": fetched_at.isoformat(),
        }
        err_path = dest_dir / f"{stamp}_error.json"
        try:
            store_io.write_json_atomic(err_path, err)
        except OSError:
            pass
        return err

    digest = sha256_hex(raw)
    prev_xmls = sorted(dest_dir.glob("*.xml"))
    # The collection happened; the bytes simply repeat the previous snapshot
    # (a 304 or an unchanged feed). Recorded as a fact, never hidden. Identical
    # bytes are hard-linked to the previous run instead of copied again.
    not_modified = bool(prev_xmls) and prev_xmls[-1].name.endswith(f"_{digest[:12]}.xml")
    xml_path = dest_dir / f"{stamp}_{digest[:12]}.xml"
    store_io.write_bytes_dedup(xml_path, raw, prev_xmls[-1] if not_modified else None)

    parse_error = None
    items: list[dict] = []
    parse_stats: dict = {}
    try:
        items = parse_feed(raw, url, parse_stats)
    except Exception as e:
        parse_error = f"{type(e).__name__}: {e}"

    raw_item_count = len(items)
    max_items = src.get("max_items")
    capped = False
    if isinstance(max_items, int) and max_items > 0 and len(items) > max_items:
        items = items[:max_items]
        capped = True

    source_kind = (src.get("source_kind") or "media").strip().lower()
    if source_kind not in ("media", "official"):
        source_kind = "media"

    def rel_or_abs(p: Path) -> str:
        try:
            return str(p.relative_to(ROOT)).replace("\\", "/")
        except ValueError:
            return str(p).replace("\\", "/")

    payload = {
        "ok": parse_error is None,
        "source_id": source_id,
        "source_name": src.get("name"),
        "institution": src.get("institution") or source_id,
        "institution_name": src.get("institution_name") or src.get("name"),
        "language": src.get("language"),
        "geo": src.get("geo"),
        "nest_role": src.get("nest_role"),
        "source_kind": source_kind,
        "feed_url": url,
        "fetched_at": fetched_at.isoformat(),
        "content_type": content_type,
        "bytes": len(raw),
        "sha256": digest,
        "xml_file": rel_or_abs(xml_path),
        "raw_item_count": raw_item_count,
        "dropped_no_url_title": parse_stats.get("dropped_no_url_title", 0),
        "not_modified": not_modified,
        "max_items": max_items if isinstance(max_items, int) else None,
        "capped": capped,
        "item_count": len(items),
        "parse_error": parse_error,
        "error": parse_error,
        "items": [
            {
                "source_id": source_id,
                "source_kind": source_kind,
                "url": it.get("url"),
                "title": it.get("title"),
                "body": it.get("body"),
                "published_at": it.get("published_at"),
                "updated_at": it.get("updated_at"),
                "guid": it.get("guid"),
                "author": it.get("author"),
                "credit": it.get("credit"),
                "language": src.get("language"),
                "fetched_at": fetched_at.isoformat(),
            }
            for it in items
        ],
    }
    meta_path = dest_dir / f"{stamp}_{digest[:12]}.json"
    store_io.write_json_atomic(meta_path, payload)
    payload["meta_file"] = rel_or_abs(meta_path)
    return payload


SNAP_STAMP_RE = re.compile(r"^(?:_run_)?(\d{8}T\d{6}Z)")


def prune_raw_snapshots(raw_dir: Path | None = None, days: int = RETENTION_DAYS,
                        now: datetime | None = None) -> int:
    """Retention law (RENT.md, LEGAL_RISK.md R6): raw snapshots older than the
    window are deleted; the newest snapshot *pair* of every source (and the
    newest run log) always survives, so offline rebuilds keep working. Files
    are grouped by their full stamp, so an XML snapshot and the meta that
    describes it are pruned together, never split. Fail-soft: a pruning
    problem never fails the ingest."""
    raw_dir = Path(raw_dir) if raw_dir is not None else RAW_DIR
    now = now or utc_now()
    cutoff = (now - timedelta(days=days)).strftime("%Y%m%d")
    removed = 0

    def prune_group(files: list[Path]) -> None:
        nonlocal removed
        groups: dict[str, list[Path]] = {}
        for path in files:
            m = SNAP_STAMP_RE.match(path.name)
            if m:
                groups.setdefault(m.group(1), []).append(path)
        if not groups:
            return
        newest = max(groups)
        for stamp, members in groups.items():
            if stamp == newest or stamp[:8] >= cutoff:
                continue
            for path in members:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    continue

    try:
        entries = sorted(raw_dir.iterdir())
        prune_group([p for p in entries if p.is_file() and SNAP_STAMP_RE.match(p.name)])
        for child in entries:
            if child.is_dir() and not child.name.startswith(("_bodies", ".")):
                prune_group([p for p in sorted(child.iterdir())
                             if p.is_file() and SNAP_STAMP_RE.match(p.name)])
    except OSError:
        return removed
    return removed


def main() -> int:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    sources = load_enabled_rss(SOURCES_PATH)
    fetched_at = utc_now()
    run = {
        "fetched_at": fetched_at.isoformat(),
        "sources_file": "sources.yaml",
        "enabled_rss": [s["id"] for s in sources],
        "results": [],
    }
    print(f"Vigie ingest {fetched_at.isoformat()} — {len(sources)} enabled RSS")
    for src in sources:
        print(f"  fetch {src['id']} ...", flush=True)
        try:
            result = ingest_one(src, fetched_at)
        except OSError as exc:
            # A transient snapshot write failure (antivirus/search indexer
            # holding a handle) must cost this one source, never the run.
            result = {
                "source_id": src["id"],
                "ok": False,
                "error": f"store_write_failed: {type(exc).__name__}: {exc}",
            }
        slim = {
            "source_id": result["source_id"],
            "ok": result["ok"],
            "item_count": result.get("item_count", 0),
            "dropped_no_url_title": result.get("dropped_no_url_title", 0),
            "error": result.get("error"),
            "parse_error": result.get("parse_error"),
            "xml_file": result.get("xml_file"),
            "meta_file": result.get("meta_file"),
        }
        run["results"].append(slim)
        if result["ok"]:
            print(f"    ok  items={slim['item_count']}  {slim.get('meta_file')}")
        else:
            print(f"    FAIL {slim['error']}")
    stamp = fetched_at.strftime("%Y%m%dT%H%M%SZ")
    run_path = RAW_DIR / f"_run_{stamp}.json"
    run["pruned_snapshots"] = prune_raw_snapshots(now=fetched_at)
    store_io.write_json_atomic(run_path, run)
    ok_n = sum(1 for r in run["results"] if r["ok"])
    items_n = sum(r.get("item_count") or 0 for r in run["results"])
    print(f"run log: {run_path.relative_to(ROOT)}")
    tail = f", pruned {run['pruned_snapshots']} old snapshots" if run["pruned_snapshots"] else ""
    print(f"done: {ok_n}/{len(sources)} feeds ok, {items_n} items{tail}")
    return 0 if ok_n > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
