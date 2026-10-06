"""Takedowns (R10) — publisher removal requests, enforced at every boundary.

A publisher or rights-holder asks that a feed, a domain, an article or an image
stop being relayed. The request becomes one entry in the versioned public file
`takedowns.yaml` (repo root), and every stage reads that one file:

  * collection — a withdrawn source is never fetched (ingest_rss.load_enabled_by_type);
  * normalize  — matching items never enter an edition;
  * render     — matching articles, dossier items and preview images are
                 dropped even when only the display step runs (hourly roads lane);
  * media      — a matching image is never fetched, never re-hosted, and any
                 stored copy (by URL or by sha256) is deleted;
  * staging    — stage_public refuses a media file or a page that still matches
                 an active takedown, and refuses an unreadable/invalid file;
  * this script — purges a withdrawn source's raw snapshots, conditional-fetch
                 bodies and preview images from data/.

Kinds: `source` (a sources.yaml id), `host` (a domain and its subdomains),
`url` (one article URL), `image` (an image URL, or the sha256 of the stored
file: 20 to 64 hex characters). `by` is the publisher's or rights-holder's
NAME only — never an e-mail, a phone number or any other personal data. The
public sources page lists publisher, kind and date — never the value.

Stdlib only. Enforcement is fail-soft (a valid entry is always applied; an
invalid one cannot be interpreted), and the release gate is strict: any error
in the file blocks staging, diagnosed. A missing or empty file is a no-op.

Usage:
  python -X utf8 scripts/takedown.py                     # enforce: purge withdrawn data
  python -X utf8 scripts/takedown.py --check             # validate takedowns.yaml (exit 1 on error)
  python -X utf8 scripts/takedown.py --purge-source ID   # purge one source's raw data (e.g. a cut source)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import store_io  # noqa: E402

METHOD = "takedowns-v1"
TAKEDOWNS_PATH = ROOT / "takedowns.yaml"
SOURCES_PATH = ROOT / "sources.yaml"
DATA = ROOT / "data"
RAW_DIR = DATA / "raw"
MEDIA_DIR = DATA / "media" / "brief"
MANIFEST = DATA / "media" / "brief_manifest.json"
STORE_NAMES = (
    "normalized/latest_ranked.json",
    "normalized/latest_enriched.json",
    "normalized/latest_candidates.json",
    "issues/latest_issues.json",
)
ARTICLE_STORES = tuple(DATA / name for name in STORE_NAMES)

KINDS = ("source", "host", "url", "image")
STATUSES = ("active", "lifted")
KIND_LABEL_FR = {"source": "flux entier", "host": "domaine entier", "url": "article", "image": "image"}
STATUS_LABEL_FR = {"active": "retiré", "lifted": "retrait levé à la demande de l’éditeur"}
WITHDRAWN_LABEL_FR = "retiré à la demande de l’éditeur"

_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_SOURCE_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]*")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
_SHA = re.compile(r"[a-f0-9]{20,64}")
_IMG_EXT = re.compile(r"\.(?:jpg|jpeg|png|webp|avif|gif)$")
_MEDIA_FILE = re.compile(r"[a-f0-9]{20}\.(?:jpg|jpeg|png|webp|avif|gif)")


# --------------------------------------------------------------------------- #
# Reading the file
# --------------------------------------------------------------------------- #
def _canon(url: object) -> str:
    """The pipeline's canonical article URL (normalize.canonical_url), or ""."""
    if not isinstance(url, str) or not url.strip():
        return ""
    import normalize  # noqa: PLC0415 - lazy: normalize imports this module

    return normalize.canonical_url(url.strip()) or ""


def _host(url: object) -> str:
    try:
        return (urlsplit(str(url or "").strip()).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _bare_host(host: str) -> str:
    host = host.lower().strip().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def parse(text: str, name: str = "takedowns.yaml") -> tuple[list[dict], list[str]]:
    """(entries, errors). Same minimal YAML subset as sources.yaml.

    An entry that cannot be interpreted is reported and skipped; an unknown
    status is reported and *enforced* as active (fail-closed for the reader of
    the request, never for the publisher).
    """
    import ingest_rss  # noqa: PLC0415 - one scalar parser for both registries

    lines = text.splitlines()
    meaningful = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
    if not meaningful:
        return [], []
    start = next((i for i, ln in enumerate(lines) if re.match(r"^takedowns:", ln)), None)
    if start is None:
        return [], [f"{name}: no `takedowns:` block"]
    head = lines[start].partition(":")[2].split("#", 1)[0].strip()
    if head == "[]":
        return [], []
    if head:
        return [], [f"{name}: `takedowns:` must be a list (or [])"]
    block: list[str] = []
    for ln in lines[start + 1:]:
        if ln.strip() and not ln.startswith((" ", "\t", "#")):
            break
        block.append(ln)
    chunks: list[list[str]] = []
    for ln in block:
        stripped = ln.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            chunks.append([stripped[2:]])
        elif chunks:
            chunks[-1].append(stripped)
        else:
            return [], [f"{name}: content before the first `- ` entry"]
    entries: list[dict] = []
    errors: list[str] = []
    seen: set[str] = set()
    for n, chunk in enumerate(chunks, 1):
        rec: dict = {}
        for line in chunk:
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            rec[key.strip()] = ingest_rss._scalar(val)
        entry, problems = _validate(rec, n)
        errors.extend(f"{name}: {p}" for p in problems)
        if entry is None:
            continue
        if entry["id"] in seen:
            errors.append(f"{name}: entry {entry['id']}: duplicate id")
            continue
        seen.add(entry["id"])
        entries.append(entry)
    return entries, errors


def _validate(rec: dict, n: int) -> tuple[dict | None, list[str]]:
    problems: list[str] = []
    ident = str(rec.get("id") or "").strip()
    label = f"entry {ident or f'#{n}'}"
    if not _ID.fullmatch(ident):
        problems.append(f"{label}: id must match [a-z0-9][a-z0-9_-]*")
    kind = str(rec.get("kind") or "").strip().lower()
    if kind not in KINDS:
        problems.append(f"{label}: kind must be one of {', '.join(KINDS)}")
    raw_value = str(rec.get("value") or "").strip()
    value = ""
    if kind == "source":
        value = raw_value if _SOURCE_ID.fullmatch(raw_value) else ""
    elif kind == "host":
        value = _bare_host(raw_value) if _HOST.fullmatch(raw_value.lower().rstrip(".")) else ""
    elif kind == "url":
        value = _canon(raw_value)
    elif kind == "image":
        lowered = _IMG_EXT.sub("", raw_value.lower())
        value = lowered if _SHA.fullmatch(lowered) else _canon(raw_value)
    if kind in KINDS and not value:
        problems.append(f"{label}: value is not a valid {kind}")
    requested = str(rec.get("requested_at") or "").strip()
    if not _DATE.fullmatch(requested):
        problems.append(f"{label}: requested_at must be a YYYY-MM-DD date")
    by = " ".join(str(rec.get("by") or "").split())
    if not by or len(by) > 120:
        problems.append(f"{label}: by must name the publisher or rights-holder (1-120 characters)")
    elif "@" in by or "://" in by or re.search(r"\d{3}\D?\d{3}\D?\d{4}", by):
        problems.append(f"{label}: by must be a name only — no e-mail, address or phone number")
    status = str(rec.get("status") or "").strip().lower()
    if status not in STATUSES:
        problems.append(f"{label}: status must be one of {', '.join(STATUSES)} (enforced as active)")
        status = "active"
    hard = [p for p in problems if "enforced as active" not in p]
    if hard:
        return None, problems
    return {
        "id": ident, "kind": kind, "value": value, "requested_at": requested,
        "by": by, "status": status,
    }, problems


def load(path: Path | None = None) -> tuple[list[dict], list[str]]:
    """(entries, errors) from takedowns.yaml. A missing file is a no-op."""
    path = Path(path) if path is not None else TAKEDOWNS_PATH
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return [], []
    except (OSError, UnicodeDecodeError) as exc:
        return [], [f"{path.name}: unreadable ({type(exc).__name__}: {exc})"]
    return parse(text, path.name)


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #
class Rules:
    """The active entries, indexed. Falsy when nothing is withdrawn."""

    def __init__(self, entries: list[dict] | None = None) -> None:
        self.entries = [e for e in (entries or []) if e.get("status") == "active"]
        self.sources: dict[str, dict] = {}
        self.hosts: dict[str, dict] = {}
        self.urls: dict[str, dict] = {}
        self.image_urls: dict[str, dict] = {}
        self.image_shas: list[tuple[str, dict]] = []
        for e in self.entries:
            kind, value = e["kind"], e["value"]
            if kind == "source":
                self.sources.setdefault(value, e)
            elif kind == "host":
                self.hosts.setdefault(value, e)
            elif kind == "url":
                self.urls.setdefault(value, e)
            elif _SHA.fullmatch(value):
                self.image_shas.append((value, e))
            else:
                self.image_urls.setdefault(value, e)

    def __bool__(self) -> bool:
        return bool(self.entries)

    def match_host(self, url: object) -> dict | None:
        host = _bare_host(_host(url))
        if not host or not self.hosts:
            return None
        for value, entry in self.hosts.items():
            if host == value or host.endswith("." + value):
                return entry
        return None

    def match_url(self, url: object) -> dict | None:
        """An article/page URL withdrawn as an article, an image or a domain."""
        if not isinstance(url, str) or not url.strip():
            return None
        canon = _canon(url) or url.strip()
        return self.urls.get(canon) or self.image_urls.get(canon) or self.match_host(url)

    def match_source(self, src: dict) -> dict | None:
        """A sources.yaml record withdrawn by id or by its feed's domain."""
        if not isinstance(src, dict):
            return None
        hit = self.sources.get(str(src.get("id") or ""))
        return hit or self.match_host(src.get("url")) or self.match_host(src.get("homepage"))

    def match_item(self, item: dict) -> dict | None:
        """A collected article withdrawn by its source, domain or URL."""
        if not isinstance(item, dict):
            return None
        hit = self.sources.get(str(item.get("source_id") or ""))
        return hit or self.match_url(item.get("url"))

    def match_image(self, url: object = None, *, sha: str | None = None,
                    file: str | None = None) -> dict | None:
        """An image withdrawn by URL, domain, full sha256 or stored file name."""
        if url:
            canon = _canon(url) or str(url).strip()
            hit = self.image_urls.get(canon) or self.match_host(url)
            if hit:
                return hit
        digest = (sha or "").lower()
        stem = str(file or "").lower().rsplit(".", 1)[0]
        for value, entry in self.image_shas:
            if digest and digest.startswith(value):
                return entry
            if stem and len(stem) == 20 and value[:20] == stem:
                return entry
        return None


def load_rules(path: Path | None = None) -> Rules:
    entries, _errors = load(path)
    return Rules(entries)


# --------------------------------------------------------------------------- #
# Enforcement helpers (pure: callers own the stores)
# --------------------------------------------------------------------------- #
def filter_items(items: list, rules: Rules | None = None) -> tuple[list, int]:
    """(kept, dropped). Identity when nothing is withdrawn."""
    rules = rules if rules is not None else load_rules()
    if not rules or not isinstance(items, list):
        return items, 0
    kept = [it for it in items if not (isinstance(it, dict) and rules.match_item(it))]
    return kept, len(items) - len(kept)


def filter_issues(issues: list, rules: Rules | None = None) -> list:
    """Dossiers without withdrawn articles. A dossier labelled by a withdrawn
    headline, or left with fewer than two institutions, is dropped whole."""
    rules = rules if rules is not None else load_rules()
    if not rules or not isinstance(issues, list):
        return issues
    out: list = []
    for iss in issues:
        if not isinstance(iss, dict):
            out.append(iss)
            continue
        label = iss.get("label_source") if isinstance(iss.get("label_source"), dict) else None
        if label and rules.match_item(label):
            continue
        tensions = []
        for t in iss.get("tensions") or []:
            if not isinstance(t, dict):
                continue
            items = [it for it in (t.get("items") or []) if not (isinstance(it, dict) and rules.match_item(it))]
            if items:
                tensions.append({**t, "items": items})
        voices = sorted({str(t.get("institution_id")) for t in tensions if t.get("institution_id")})
        if len(voices) < 2:
            continue
        if tensions == [t for t in iss.get("tensions") or [] if isinstance(t, dict)]:
            out.append(iss)
            continue
        feeds = sorted({str(it.get("source_id")) for t in tensions for it in t["items"]
                        if isinstance(it, dict) and it.get("source_id")})
        official = sum(1 for t in tensions if str(t.get("source_kind")) == "official")
        silence = iss.get("silence") if isinstance(iss.get("silence"), dict) else None
        rebuilt = {**iss, "tensions": tensions, "sources": voices, "source_count": len(voices),
                   "source_feeds": feeds, "item_count": sum(len(t["items"]) for t in tensions),
                   "official_voice_count": official, "media_remix": official == 0}
        if silence is not None:
            rebuilt["silence"] = {**silence, "spoke_count": len(voices)}
        out.append(rebuilt)
    return out


def filter_edges(edges: dict, kept_issue_ids: set[str]) -> dict:
    """The street-atlas sidecar restricted to the dossiers still shown, so no
    street ever points at a dossier dropped by a takedown."""
    if not isinstance(edges, dict) or not edges:
        return edges
    out = dict(edges)
    if isinstance(edges.get("issues"), dict):
        out["issues"] = {k: v for k, v in edges["issues"].items() if k in kept_issue_ids}
        out["matched_issue_count"] = len(out["issues"])
    if isinstance(edges.get("streets"), dict):
        out["streets"] = {
            k: ({**v, "matched_issue_ids": [i for i in v.get("matched_issue_ids") or [] if i in kept_issue_ids]}
                if isinstance(v, dict) and isinstance(v.get("matched_issue_ids"), list) else v)
            for k, v in edges["streets"].items()
        }
    return out


def filter_ledger(ledger: dict, rules: Rules | None = None) -> dict:
    """The change ledger without links to withdrawn articles (the row stays:
    its counts are Vigie's own facts; only the publisher link goes)."""
    rules = rules if rules is not None else load_rules()
    if not rules or not isinstance(ledger, dict):
        return ledger
    out = dict(ledger)
    for key in ("new", "developed", "quiet"):
        rows = ledger.get(key)
        if not isinstance(rows, list):
            continue
        fixed = []
        for row in rows:
            src = row.get("label_source") if isinstance(row, dict) else None
            if isinstance(src, dict) and rules.match_item(src):
                row = {**row, "label_source": {k: v for k, v in src.items() if k != "url"}}
            fixed.append(row)
        out[key] = fixed
    return out


def withdrawn_sources(rules: Rules | None = None, sources_path: Path | None = None) -> list[dict]:
    """sources.yaml records withdrawn by an active takedown, with the request."""
    rules = rules if rules is not None else load_rules()
    if not rules:
        return []
    try:
        import ingest_rss  # noqa: PLC0415

        records = ingest_rss.load_sources(Path(sources_path) if sources_path else SOURCES_PATH)
    except (OSError, SystemExit, ValueError):
        return []
    out = []
    for rec in records:
        hit = rules.match_source(rec)
        if hit:
            out.append({**rec, "takedown": hit})
    return out


def withdrawn_institutions(rules: Rules | None = None, sources_path: Path | None = None) -> dict[str, dict]:
    """institution id -> {name, kind, feed_ids, requested_at, by} for every
    institution with no feed left after the takedowns. One withdrawn sister
    feed does not withdraw the voice; the last one does."""
    rules = rules if rules is not None else load_rules()
    if not rules:
        return {}
    try:
        import ingest_rss  # noqa: PLC0415

        records = ingest_rss.load_sources(Path(sources_path) if sources_path else SOURCES_PATH)
    except (OSError, SystemExit, ValueError):
        return {}
    by_inst: dict[str, list[tuple[dict, dict | None]]] = {}
    for rec in records:
        iid = str(rec.get("institution") or rec.get("id") or "")
        hit = rules.match_source(rec)
        if iid and (hit or rec.get("enabled") is True):
            by_inst.setdefault(iid, []).append((rec, hit))
    out: dict[str, dict] = {}
    for iid, feeds in sorted(by_inst.items()):
        hits = [h for _, h in feeds if h]
        if not hits or len(hits) != len(feeds):
            continue
        first = feeds[0][0]
        out[iid] = {
            "institution_id": iid,
            "institution_name": str(first.get("institution_name") or first.get("name") or iid),
            "source_kind": "official" if str(first.get("source_kind") or "") == "official" else "media",
            "feed_ids": sorted(str(r.get("id")) for r, _ in feeds),
            "requested_at": min(h["requested_at"] for h in hits),
            "by": sorted({h["by"] for h in hits})[0],
            "status": "withdrawn_on_request",
            "label": WITHDRAWN_LABEL_FR,
        }
    return out


def public_rows(entries: list[dict] | None = None) -> list[dict]:
    """What the public page may show: publisher, kind, date, status — never the value."""
    if entries is None:
        entries, _ = load()
    rows = [
        {
            "by": e["by"],
            "kind": e["kind"],
            "kind_label": KIND_LABEL_FR.get(e["kind"], e["kind"]),
            "requested_at": e["requested_at"],
            "status": e["status"],
            "status_label": STATUS_LABEL_FR.get(e["status"], e["status"]),
        }
        for e in entries
    ]
    rows.sort(key=lambda r: (r["requested_at"], r["by"], r["kind"]), reverse=True)
    return rows


# --------------------------------------------------------------------------- #
# Purges (data/ only; deploy/ is rebuilt by staging)
# --------------------------------------------------------------------------- #
def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def purge_media(rules: Rules | None = None, *, media_dir: Path | None = None,
                manifest_path: Path | None = None, article_urls: set[str] | None = None) -> dict:
    """Delete every stored preview image matching a takedown — by image URL,
    article URL, domain, sha256 or file name — and drop its manifest entry.

    `article_urls` adds articles to purge whatever the rules say (a purged
    source's articles). Returns {"files": n, "entries": n}. The manifest is
    rewritten atomically and never references a deleted file.
    """
    rules = rules if rules is not None else load_rules()
    media_dir = Path(media_dir) if media_dir is not None else MEDIA_DIR
    manifest_path = Path(manifest_path) if manifest_path is not None else MANIFEST
    extra = {(_canon(u) or u) for u in (article_urls or set()) if isinstance(u, str)}
    if not rules and not extra:
        return {"files": 0, "entries": 0}
    doomed_files: set[str] = set()
    removed_entries = 0
    try:
        doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = None
    media = doc.get("media") if isinstance(doc, dict) and isinstance(doc.get("media"), dict) else None
    if media is not None:
        kept = {}
        for uid, entry in media.items():
            if not isinstance(entry, dict):
                kept[uid] = entry
                continue
            article = entry.get("article_url")
            hit = (
                rules.match_image(entry.get("image_url"), file=entry.get("file"))
                or rules.match_url(article)
                or ((_canon(article) or article) in extra if isinstance(article, str) else False)
            )
            if hit:
                removed_entries += 1
                if isinstance(entry.get("file"), str):
                    doomed_files.add(entry["file"])
                continue
            kept[uid] = entry
        if removed_entries:
            doc["media"] = kept
            doc["entry_count"] = len(kept)
            doc["with_image"] = sum(1 for e in kept.values() if isinstance(e, dict) and e.get("file"))
            store_io.write_json_atomic(manifest_path, doc)
    removed_files = 0
    if media_dir.is_dir():
        for path in sorted(media_dir.iterdir()):
            if not path.is_file() or path.is_symlink() or not _MEDIA_FILE.fullmatch(path.name):
                continue
            doomed = path.name in doomed_files
            if not doomed and rules.image_shas:
                try:
                    doomed = rules.match_image(sha=_sha256_file(path)) is not None
                except OSError:
                    doomed = False
            if doomed:
                try:
                    path.unlink()
                    removed_files += 1
                except OSError:
                    pass
        if removed_files and media is not None:
            # A sha-matched file may still be referenced: never leave a dangling entry.
            present = {p.name for p in media_dir.iterdir() if p.is_file()}
            current = doc.get("media") if isinstance(doc.get("media"), dict) else {}
            dangling = {u for u, e in current.items()
                        if isinstance(e, dict) and isinstance(e.get("file"), str) and e["file"] not in present}
            if dangling:
                doc["media"] = {u: e for u, e in current.items() if u not in dangling}
                doc["entry_count"] = len(doc["media"])
                doc["with_image"] = sum(1 for e in doc["media"].values() if isinstance(e, dict) and e.get("file"))
                store_io.write_json_atomic(manifest_path, doc)
                removed_entries += len(dangling)
    return {"files": removed_files, "entries": removed_entries}


def source_article_urls(source_ids: set[str] | str, stores: tuple[Path, ...]) -> set[str]:
    """Article URLs of the given sources found in candidate/ranked/issue stores."""
    wanted = {source_ids} if isinstance(source_ids, str) else set(source_ids)
    urls: set[str] = set()
    if not wanted:
        return urls
    for path in stores:
        try:
            doc = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows = doc.get("candidates") if isinstance(doc, dict) else doc
        rows = list(rows) if isinstance(rows, list) else []
        for iss in (doc.get("issues") or []) if isinstance(doc, dict) and isinstance(doc.get("issues"), list) else []:
            for t in (iss.get("tensions") or []) if isinstance(iss, dict) else []:
                rows.extend((t.get("items") or []) if isinstance(t, dict) else [])
        for row in rows:
            if isinstance(row, dict) and row.get("source_id") in wanted and isinstance(row.get("url"), str):
                urls.add(row["url"])
    return urls


def purge_source(source_id: str, *, feed_urls: list[str] | None = None, raw_dir: Path | None = None,
                 media_dir: Path | None = None, manifest_path: Path | None = None,
                 article_stores: tuple[Path, ...] | None = None) -> dict:
    """Delete a cut source's raw snapshots, its conditional-fetch bodies and
    cache rows, and the preview images of its articles. Idempotent."""
    if not _SOURCE_ID.fullmatch(str(source_id or "")):
        raise ValueError(f"invalid source id: {source_id!r}")
    raw_dir = Path(raw_dir) if raw_dir is not None else RAW_DIR
    out = {"source_id": source_id, "raw_files": 0, "bodies": 0, "cache_rows": 0, "media_files": 0}
    src_dir = raw_dir / source_id
    if src_dir.is_dir() and not src_dir.is_symlink() and src_dir.resolve().parent == raw_dir.resolve():
        out["raw_files"] = sum(1 for p in src_dir.rglob("*") if p.is_file())
        shutil.rmtree(src_dir)
    if feed_urls is None:
        try:
            import ingest_rss  # noqa: PLC0415

            rec = next((r for r in ingest_rss.load_sources(SOURCES_PATH) if r.get("id") == source_id), {})
        except (OSError, SystemExit, ValueError):
            rec = {}
        feed_urls = [rec["url"]] if isinstance(rec.get("url"), str) else []
    import ingest_rss  # noqa: PLC0415

    urls = [u for u in feed_urls if isinstance(u, str)]
    urls += [alt for u in list(urls) for alt in ingest_rss.URL_ALTERNATES.get(u, [])]
    for url in urls:
        body = raw_dir / "_bodies" / (hashlib.sha256(url.encode()).hexdigest()[:32] + ".body")
        try:
            body.unlink()
            out["bodies"] += 1
        except OSError:
            pass
    cache_path = raw_dir / "_http_cache.json"
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = None
    if isinstance(cache, dict):
        drop = [u for u in urls if u in cache]
        if drop:
            for u in drop:
                cache.pop(u, None)
            store_io.write_json_atomic(cache_path, cache, indent=1)
            out["cache_rows"] = len(drop)
    article_urls = source_article_urls(source_id, article_stores if article_stores is not None else ARTICLE_STORES)
    if article_urls:
        media = purge_media(Rules([]), media_dir=media_dir, manifest_path=manifest_path, article_urls=article_urls)
        out["media_files"] = media["files"]
    return out


def enforce(rules: Rules | None = None, *, raw_dir: Path | None = None, media_dir: Path | None = None,
            manifest_path: Path | None = None) -> dict:
    """Apply every active takedown to data/: purge withdrawn sources and images."""
    rules = rules if rules is not None else load_rules()
    report = {"sources": [], "media": {"files": 0, "entries": 0}}
    if not rules:
        return report
    for rec in withdrawn_sources(rules):
        report["sources"].append(purge_source(
            str(rec["id"]), feed_urls=[rec["url"]] if isinstance(rec.get("url"), str) else [],
            raw_dir=raw_dir, media_dir=media_dir, manifest_path=manifest_path,
        ))
    report["media"] = purge_media(rules, media_dir=media_dir, manifest_path=manifest_path)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="Validate takedowns.yaml and exit (1 on error)")
    parser.add_argument("--purge-source", metavar="ID", help="Purge one source's raw data, bodies and images")
    args = parser.parse_args(argv)
    entries, errors = load()
    for err in errors:
        print(f"takedowns: ERROR {err}")
    if args.check:
        active = sum(1 for e in entries if e["status"] == "active")
        print(f"takedowns: {len(entries)} entries ({active} active), {len(errors)} error(s)")
        return 1 if errors else 0
    if args.purge_source:
        print(f"takedowns: purge {json.dumps(purge_source(args.purge_source), ensure_ascii=False)}")
        return 0
    try:
        report = enforce(Rules(entries))
    except OSError as exc:
        # Diagnosed, never silent; the release gate still refuses matching media.
        print(f"takedowns: purge FAILED ({type(exc).__name__}: {exc})")
        return 0
    active = sum(1 for e in entries if e["status"] == "active")
    print(f"takedowns: {active} active; purged {len(report['sources'])} source(s), "
          f"{report['media']['files']} image file(s), {report['media']['entries']} media entr(ies)")
    for row in report["sources"]:
        print(f"  {row['source_id']}: {row['raw_files']} raw file(s), {row['bodies']} bod(ies), "
              f"{row['media_files']} image(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
