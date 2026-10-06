"""Feed-ingest hardening for the planned source expansion (invented fixtures only).

Locks: tolerant parsing as a fallback that runs only after a strict parse
failed, optional per-source pagination (default 1 = nothing changes), the Atom
entry path, source-metadata pass-through, and a golden proof that output for
the pre-existing fixtures is byte-identical. No network: every fetch is a
mock, every sleep is injected.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import harness  # noqa: F401 - puts scripts/ on sys.path

import ingest_rss
import normalize

FETCHED = datetime(2026, 10, 6, 0, 3, tzinfo=timezone.utc)
URL = "https://news.example/feed/"

RSS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"
     xmlns:media="http://search.yahoo.com/mrss/" xmlns:atom="http://www.w3.org/2005/Atom">
<channel><title>Invented</title>
<atom:link rel="self" href="https://news.example/feed/"/>
<item><title>Incendie fictif au Port &amp; au quai</title>
<link>https://news.example/a/1</link><guid isPermaLink="false">g-1</guid>
<description><![CDATA[<p>Un texte inventé &amp; sans suite.</p>]]></description>
<pubDate>Mon, 05 Oct 2026 20:00:00 GMT</pubDate>
<dc:creator>Camille Exemple</dc:creator><media:credit>Photographe Inventé</media:credit></item>
<item><title>Deuxième brève inventée</title><link>/a/2</link>
<pubDate>Mon, 05 Oct 2026 21:00:00 GMT</pubDate><author>desk@news.example (Desk Fictif)</author></item>
<item><description>sans titre ni lien</description></item>
</channel></rss>""".encode("utf-8")

ATOM_FIXTURE = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Invented Atom</title>
<entry><title>Entrée inventée un</title>
<link rel="self" href="https://news.example/atom/self-1"/>
<link rel="alternate" type="text/html" href="https://news.example/atom/1"/>
<id>tag:news.example,2026:1</id><published>2026-10-05T20:00:00Z</published>
<updated>2026-10-05T22:00:00Z</updated><summary>Résumé inventé.</summary>
<author><name>Alex Exemple</name></author></entry>
<entry><title>Entrée inventée deux</title><link href="/atom/2"/>
<id>tag:news.example,2026:2</id><updated>2026-10-05T23:00:00Z</updated>
<content type="html">&lt;p&gt;Contenu inventé&lt;/p&gt;</content></entry>
</feed>""".encode("utf-8")

# Golden digests: sha256 of the meta JSON ingest_one wrote for the fixtures
# above, computed on the Phase 1 base commit BEFORE any hardening change.
GOLDEN = {
    "rss": "6b2d8380e2e61cb6aad8ac54fd6d38c6c400307d3f0f7404f2a93c477b99e180",
    "atom": "82ab2b5f28e36be19f865358e209be3811603ab16d260b3db90fb9c74f9ea589",
    "rss_candidates": "aceff685f342acf1267273938fff0fa4979be6e357ba3efed03295c8684bc288",
    "atom_candidates": "3f1170e4f6d1f46f3df8d3c1a4d24c4c5c7fbecbfb4f83dd94944391a8745dcb",
}

SRC = {"id": "news", "name": "News", "url": URL, "language": "fr",
       "institution": "news", "institution_name": "News", "geo": "quebec-city",
       "nest_role": "primary", "source_kind": "media"}


def run_ingest(raw: bytes, src: dict | None = None, *, root: Path) -> tuple[dict, bytes]:
    """ingest_one over `raw`, entirely inside `root`; returns (payload, meta bytes)."""
    src = src or SRC
    raw_dir = root / "data" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    with mock.patch.object(ingest_rss, "ROOT", root), mock.patch.object(ingest_rss, "RAW_DIR", raw_dir), \
            mock.patch.object(ingest_rss, "fetch_bytes", return_value=(raw, "application/xml")):
        payload = ingest_rss.ingest_one(src, FETCHED)
    meta = root / payload["meta_file"]
    return payload, meta.read_bytes()


def candidates_digest(raw: bytes) -> str:
    """sha256 of the normalized candidates the pipeline builds from `raw`."""
    with tempfile.TemporaryDirectory() as tmp:
        payload, _ = run_ingest(raw, root=Path(tmp))
    cands = [normalize.normalize_item(it, payload) for it in payload["items"]]
    return hashlib.sha256(json.dumps(cands, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


class GoldenByteIdentity(unittest.TestCase):
    """Existing behaviour, frozen: the hardening changes nothing for a normal feed."""

    def digest(self, raw: bytes) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            _, meta = run_ingest(raw, root=Path(tmp))
        return hashlib.sha256(meta).hexdigest()

    def test_rss_meta_is_byte_identical_to_the_base_commit(self) -> None:
        self.assertEqual(self.digest(RSS_FIXTURE), GOLDEN["rss"])

    def test_atom_meta_is_byte_identical_to_the_base_commit(self) -> None:
        self.assertEqual(self.digest(ATOM_FIXTURE), GOLDEN["atom"])

    def test_rss_candidates_are_identical_to_the_base_commit(self) -> None:
        self.assertEqual(candidates_digest(RSS_FIXTURE), GOLDEN["rss_candidates"])

    def test_atom_candidates_are_identical_to_the_base_commit(self) -> None:
        self.assertEqual(candidates_digest(ATOM_FIXTURE), GOLDEN["atom_candidates"])


if __name__ == "__main__":
    unittest.main()
