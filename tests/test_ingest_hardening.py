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
import os
import subprocess
import sys
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



class DeterminismAcrossHashSeeds(unittest.TestCase):
    """The same bytes give the same meta whatever PYTHONHASHSEED is."""

    def test_meta_digest_does_not_depend_on_the_hash_seed(self) -> None:
        code = (
            "import sys, tempfile, hashlib; from pathlib import Path\n"
            "sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[2])\n"
            "import test_ingest_hardening as t\n"
            "with tempfile.TemporaryDirectory() as tmp:\n"
            "    _, meta = t.run_ingest(t.RSS_FIXTURE, root=Path(tmp))\n"
            "print(hashlib.sha256(meta).hexdigest())\n")
        here = Path(__file__).resolve().parent
        outs = set()
        for seed in ("0", "1", "4242"):
            env = dict(os.environ, PYTHONHASHSEED=seed)
            done = subprocess.run([sys.executable, "-X", "utf8", "-c", code, str(here), str(here.parent / "scripts")],
                                  capture_output=True, text=True, env=env, timeout=60, check=True)
            outs.add(done.stdout.strip())
        self.assertEqual(outs, {GOLDEN["rss"]})


def rss(*items: str) -> bytes:
    return ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>t</title>'
            + "".join(items) + "</channel></rss>").encode("utf-8")


def item(n: int, title: str | None = None) -> str:
    return f"<item><title>{title or f'Brève inventée {n}'}</title><link>https://news.example/a/{n}</link></item>"


def run_main_with(result: dict) -> dict:
    """The run-log row main() writes for one source whose ingest returned `result`."""
    with tempfile.TemporaryDirectory() as tmp:
        raw_dir = Path(tmp) / "raw"
        with mock.patch.object(ingest_rss, "ROOT", Path(tmp)), mock.patch.object(ingest_rss, "RAW_DIR", raw_dir), \
                mock.patch.object(ingest_rss, "load_enabled_rss", return_value=[{"id": "news"}]), \
                mock.patch.object(ingest_rss, "ingest_one", side_effect=lambda *a, **k: dict(result)), \
                mock.patch.object(ingest_rss, "prune_raw_snapshots", return_value=0):
            assert ingest_rss.main() == 0
        run = json.loads(next(raw_dir.glob("_run_*.json")).read_text(encoding="utf-8"))
    return run["results"][0]


class TolerantParsing(unittest.TestCase):
    def parse(self, raw: bytes, stats: dict | None = None) -> list[dict]:
        return ingest_rss.parse_feed(raw, URL, stats if stats is not None else {})

    def test_strict_feed_is_never_touched_and_records_no_mode(self) -> None:
        stats: dict = {}
        out = self.parse(RSS_FIXTURE, stats)
        self.assertEqual(out[0]["title"], "Incendie fictif au Port & au quai")
        self.assertNotIn("parse_mode", stats)

    def test_bare_ampersand_is_repaired_and_the_text_is_verbatim(self) -> None:
        stats: dict = {}
        out = self.parse(rss(item(1, "Rock & Roll au Vieux-Port")), stats)
        self.assertEqual(out[0]["title"], "Rock & Roll au Vieux-Port")
        self.assertEqual(stats["parse_mode"], "tolerant")
        self.assertEqual(stats["entities_repaired"], 1)

    def test_bare_ampersand_in_a_link(self) -> None:
        raw = rss('<item><title>t</title><link>https://news.example/a?x=1&y=2</link></item>')
        self.assertEqual(self.parse(raw)[0]["url"], "https://news.example/a?x=1&y=2")

    def test_html_entities_stay_the_publishers_literal_text(self) -> None:
        out = self.parse(rss(item(1, "Caf&eacute; &nbsp;du port")))
        self.assertEqual(out[0]["title"], "Caf&eacute; &nbsp;du port")
        # the pipeline's own plain-text step then reads them as the publisher meant
        self.assertEqual(normalize.plain_text(out[0]["title"], 500), "Café du port")

    def test_illegal_numeric_reference_is_repaired(self) -> None:
        stats: dict = {}
        out = self.parse(rss(item(1, "a&#0;b &#x1F; c &#233; d")), stats)
        self.assertEqual(stats["parse_mode"], "tolerant")
        self.assertEqual(out[0]["title"], "a&#0;b &#x1F; c é d")  # a legal reference is still decoded

    def test_predefined_and_legal_references_are_left_alone(self) -> None:
        repaired = ingest_rss.sanitize_entities(b"<a>&amp; &lt; &gt; &quot; &apos; &#233; &#xE9; AT&T</a>")
        self.assertEqual(repaired, (b"<a>&amp; &lt; &gt; &quot; &apos; &#233; &#xE9; AT&amp;T</a>", 1))

    def test_cdata_content_is_copied_verbatim(self) -> None:
        raw = rss("<item><title>Fish & Chips</title><link>https://news.example/a/1</link>"
                  "<description><![CDATA[<p>R&D &nbsp; stays &literal</p>]]></description></item>")
        out = self.parse(raw)
        self.assertEqual(out[0]["body"], "<p>R&D &nbsp; stays &literal</p>")
        self.assertEqual(ingest_rss.sanitize_entities(raw)[1], 1)

    def test_oversized_numeric_reference_never_reaches_int(self) -> None:
        raw = b"<a>&#" + b"9" * 5000 + b";</a>"
        self.assertEqual(ingest_rss.sanitize_entities(raw)[1], 1)

    def test_malformed_beyond_entities_stays_a_diagnosed_failure(self) -> None:
        with self.assertRaises(ingest_rss.ET.ParseError) as ctx:
            self.parse(rss(item(1, "Rock & Roll")).replace(b"</channel>", b""))
        self.assertIn("tolerant entity repair of 1", str(ctx.exception))

    def test_nothing_to_repair_raises_the_original_strict_error(self) -> None:
        with self.assertRaises(ingest_rss.ET.ParseError) as ctx:
            self.parse(b"<rss><channel></rss>")
        self.assertNotIn("tolerant", str(ctx.exception))

    def test_cdata_split_matches_the_reference_regex(self) -> None:
        import random
        import re
        reference = re.compile(br"(<!\[CDATA\[.*?\]\]>)", re.S)
        rng = random.Random(7)
        atoms = [b"<![CDATA[", b"]]>", b"&", b"x", b"<a>", b"]]", b"<![CDATA", b"\n"]
        for _ in range(500):
            raw = b"".join(rng.choice(atoms) for _ in range(rng.randint(0, 14)))
            self.assertEqual(ingest_rss._split_cdata(raw), reference.split(raw), raw)

    def test_unterminated_cdata_openers_are_linear_not_quadratic(self) -> None:
        import time
        raw = (b"<rss><channel><item><title>A & B</title>"
               + b"<![CDATA[" * 120000 + b"</item></channel></rss>")
        self.assertGreater(len(raw), 1_000_000)
        started = time.monotonic()
        repaired = ingest_rss.sanitize_entities(raw)
        with self.assertRaises(ingest_rss.ET.ParseError):
            ingest_rss.parse_feed(raw)
        elapsed = time.monotonic() - started
        self.assertEqual(repaired[1], 1)
        self.assertLess(elapsed, 2.0, f"sanitising took {elapsed:.1f}s")

    def test_utf16_is_never_byte_repaired(self) -> None:
        raw = "<rss><channel><item><title>A & B</title></item></channel></rss>".encode("utf-16")
        self.assertIsNone(ingest_rss.sanitize_entities(raw))
        with self.assertRaises(Exception):
            self.parse(raw)

    def test_doctype_is_still_refused_before_any_repair(self) -> None:
        raw = b'<!DOCTYPE rss [<!ENTITY x "y">]><rss><channel><item><title>A & B</title></item></channel></rss>'
        with self.assertRaisesRegex(ValueError, "not allowed"):
            self.parse(raw)

    def test_other_ascii_compatible_encodings_survive(self) -> None:
        raw = ('<?xml version="1.0" encoding="ISO-8859-1"?><rss><channel><item><title>Café & thé</title>'
               '<link>https://news.example/a</link></item></channel></rss>').encode("latin-1")
        self.assertEqual(self.parse(raw)[0]["title"], "Café & thé")

    def test_ingest_records_tolerant_in_meta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload, meta = run_ingest(rss(item(1, "Rock & Roll"), item(2)), root=Path(tmp))
        doc = json.loads(meta)
        self.assertTrue(payload["ok"])
        self.assertEqual((doc["parse_mode"], doc["entities_repaired"]), ("tolerant", 1))
        self.assertEqual(doc["items"][0]["title"], "Rock & Roll")
        self.assertIsNone(doc["parse_error"])

    def test_strict_meta_carries_no_parse_mode_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, meta = run_ingest(RSS_FIXTURE, root=Path(tmp))
        self.assertNotIn("parse_mode", json.loads(meta))

    def test_a_failed_tolerant_pass_is_a_diagnosed_feed_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload, _ = run_ingest(b"<rss><channel><item><title>A & B</title></channel></rss>", root=Path(tmp))
        self.assertFalse(payload["ok"])
        self.assertIn("ParseError", payload["parse_error"])
        self.assertNotIn("parse_mode", payload)

    def test_tolerant_output_is_deterministic(self) -> None:
        raw = rss(item(1, "A & B &nbsp; C"), item(2))
        with tempfile.TemporaryDirectory() as t1, tempfile.TemporaryDirectory() as t2:
            _, m1 = run_ingest(raw, root=Path(t1))
            _, m2 = run_ingest(raw, root=Path(t2))
        self.assertEqual(m1, m2)

    def test_run_row_says_tolerant(self) -> None:
        row = run_main_with({"source_id": "news", "ok": True, "item_count": 1,
                             "parse_mode": "tolerant", "entities_repaired": 3})
        self.assertEqual((row["parse_mode"], row["entities_repaired"]), ("tolerant", 3))

    def test_run_row_stays_unchanged_for_strict(self) -> None:
        row = run_main_with({"source_id": "news", "ok": True, "item_count": 1})
        self.assertNotIn("parse_mode", row)
        self.assertNotIn("pages_requested", row)


class Pagination(unittest.TestCase):
    def setUp(self) -> None:
        self.sleeps: list[float] = []
        self.fetched: list[str] = []
        self.robots_requests: list[str] = []
        self.robots_body: object = b""
        self.responses: dict[str, object] = {}
        ingest_rss._ROBOTS_CACHE.clear()
        self.addCleanup(ingest_rss._ROBOTS_CACHE.clear)
        for name, value in (("_sleep", self.sleeps.append), ("_RUN_DEADLINE", None),
                            ("ROBOTS_FETCHER", self._robots)):
            patcher = mock.patch.object(ingest_rss, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _robots(self, url: str) -> bytes:
        self.robots_requests.append(url)
        if isinstance(self.robots_body, Exception):
            raise self.robots_body
        return self.robots_body  # type: ignore[return-value]

    def run_pages(self, src: dict, root: Path, first: bytes):
        raw_dir = root / "data" / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        def fake(url: str, *, stalled_hosts=None):
            self.fetched.append(url)
            res = first if url == src["url"] else self.responses.get(url)
            if isinstance(res, Exception):
                raise res
            if res is None:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            return res, "application/xml"

        with mock.patch.object(ingest_rss, "ROOT", root), mock.patch.object(ingest_rss, "RAW_DIR", raw_dir), \
                mock.patch.object(ingest_rss, "fetch_bytes", side_effect=fake):
            return ingest_rss.ingest_one(src, FETCHED)

    def src(self, **extra) -> dict:
        return dict(SRC, **extra)

    def test_paged_url_shapes(self) -> None:
        self.assertEqual(ingest_rss.paged_url("https://n.example/feed/", 2), "https://n.example/feed/?paged=2")
        self.assertEqual(ingest_rss.paged_url("https://n.example/feed/?a=1&b=%C3%A9", 3),
                         "https://n.example/feed/?a=1&b=%C3%A9&paged=3")
        self.assertEqual(ingest_rss.paged_url("https://n.example/feed/?paged=9&a=1", 2),
                         "https://n.example/feed/?a=1&paged=2")
        self.assertEqual(ingest_rss.paged_url("https://n.example/feed/", 2, "page"), "https://n.example/feed/?page=2")

    def test_page_count_reading(self) -> None:
        self.assertEqual(ingest_rss.page_count({}), (1, None))
        self.assertEqual(ingest_rss.page_count({"pages": 3}), (3, None))
        self.assertEqual(ingest_rss.page_count({"pages": 99})[0], ingest_rss.PAGES_MAX)
        self.assertIn("pages_clamped", ingest_rss.page_count({"pages": 99})[1])
        for bad in (0, -2, True, "3", 2.5):
            count, note = ingest_rss.page_count({"pages": bad})
            self.assertEqual(count, 1)
            self.assertIn("pages_invalid", note)

    def test_default_is_one_request_and_no_new_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = self.run_pages(self.src(), Path(tmp), rss(item(1)))
        self.assertEqual(self.fetched, [URL])
        self.assertEqual(self.sleeps, [])
        self.assertEqual(self.robots_requests, [])
        for key in ("pages", "pages_requested", "pages_note", "parse_mode"):
            self.assertNotIn(key, payload)

    def test_explicit_pages_one_is_the_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = self.run_pages(self.src(pages=1), Path(tmp), rss(item(1)))
        self.assertEqual(self.fetched, [URL])
        self.assertNotIn("pages", payload)

    def test_three_pages_merge_in_order_with_spacing_and_robots(self) -> None:
        self.responses[URL + "?paged=2"] = rss(item(3), item(4))
        self.responses[URL + "?paged=3"] = rss(item(5))
        with tempfile.TemporaryDirectory() as tmp:
            payload = self.run_pages(self.src(pages=3), Path(tmp), rss(item(1), item(2)))
        self.assertEqual(self.fetched, [URL, URL + "?paged=2", URL + "?paged=3"])
        self.assertEqual([it["url"] for it in payload["items"]], [f"https://news.example/a/{n}" for n in range(1, 6)])
        self.assertEqual(self.sleeps, [ingest_rss.PAGE_SPACING_SECONDS] * 2)
        self.assertEqual(self.robots_requests, ["https://news.example/robots.txt"])  # once per host per run
        self.assertEqual([r["status"] for r in payload["pages"]], ["ok", "ok", "ok"])
        self.assertEqual(payload["pages_requested"], 3)
        self.assertEqual(payload["raw_item_count"], 5)
        self.assertTrue(payload["ok"])

    def test_duplicates_across_pages_are_counted_not_repeated(self) -> None:
        self.responses[URL + "?paged=2"] = rss(item(2), item(3))
        with tempfile.TemporaryDirectory() as tmp:
            payload = self.run_pages(self.src(pages=2), Path(tmp), rss(item(1), item(2)))
        self.assertEqual(len(payload["items"]), 3)
        self.assertEqual(payload["pages"][1]["item_count"], 2)
        self.assertEqual(payload["pages"][1]["new_item_count"], 1)

    def test_max_items_caps_the_merged_list(self) -> None:
        self.responses[URL + "?paged=2"] = rss(item(3), item(4))
        with tempfile.TemporaryDirectory() as tmp:
            payload = self.run_pages(self.src(pages=2, max_items=3), Path(tmp), rss(item(1), item(2)))
        self.assertEqual(payload["item_count"], 3)
        self.assertTrue(payload["capped"])
        self.assertEqual(payload["raw_item_count"], 4)

    def test_a_refusal_on_page_two_is_final_and_page_one_stands(self) -> None:
        self.responses[URL + "?paged=2"] = urllib.error.HTTPError(URL, 403, "Forbidden", {}, None)
        with tempfile.TemporaryDirectory() as tmp:
            payload = self.run_pages(self.src(pages=4), Path(tmp), rss(item(1)))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["item_count"], 1)
        self.assertEqual(self.fetched, [URL, URL + "?paged=2"])  # no page 3, no retry, no alternate
        rows = payload["pages"]
        self.assertEqual((rows[1]["status"], rows[1]["refused"]), ("fetch_failed", 403))
        self.assertEqual([r["status"] for r in rows[2:]], ["skipped_after_stop"] * 2)

    def test_out_of_range_page_404_ends_the_walk_but_is_recorded(self) -> None:
        self.responses[URL + "?paged=2"] = rss(item(2))
        with tempfile.TemporaryDirectory() as tmp:  # page 3 is unset: the fake answers 404
            payload = self.run_pages(self.src(pages=3), Path(tmp), rss(item(1)))
        self.assertEqual(payload["item_count"], 2)
        self.assertEqual(payload["pages"][2]["refused"], 404)
        self.assertTrue(payload["ok"])

    def test_transport_failure_on_a_later_page_is_a_row(self) -> None:
        self.responses[URL + "?paged=2"] = ConnectionResetError("reset")
        with tempfile.TemporaryDirectory() as tmp:
            payload = self.run_pages(self.src(pages=2), Path(tmp), rss(item(1)))
        self.assertTrue(payload["ok"])
        self.assertIn("ConnectionResetError", payload["pages"][1]["error"])
        self.assertNotIn("refused", payload["pages"][1])

    def test_robots_disallow_stops_before_any_extra_request(self) -> None:
        self.robots_body = b"User-agent: *\nDisallow: /feed/\n"
        with tempfile.TemporaryDirectory() as tmp:
            payload = self.run_pages(self.src(pages=3), Path(tmp), rss(item(1)))
        self.assertEqual(self.fetched, [URL])
        self.assertEqual(payload["pages"][1]["status"], "robots_disallow")
        self.assertEqual(self.sleeps, [])
        self.assertTrue(payload["ok"])

    def test_unreadable_robots_fails_closed_for_extra_pages_only(self) -> None:
        self.robots_body = TimeoutError("slow")
        with tempfile.TemporaryDirectory() as tmp:
            payload = self.run_pages(self.src(pages=2), Path(tmp), rss(item(1)))
        self.assertEqual(self.fetched, [URL])
        self.assertEqual(payload["pages"][1]["status"], "robots_unreachable")
        self.assertEqual(payload["item_count"], 1)

    def test_the_run_budget_is_honoured(self) -> None:
        self.responses[URL + "?paged=2"] = rss(item(2))
        with mock.patch.object(ingest_rss, "_RUN_DEADLINE", 101.0), \
                mock.patch.object(ingest_rss, "_monotonic", return_value=100.0):
            with tempfile.TemporaryDirectory() as tmp:
                payload = self.run_pages(self.src(pages=3), Path(tmp), rss(item(1)))
        self.assertEqual(self.fetched, [URL])  # 100 + 2 s spacing would pass the 101 deadline
        self.assertEqual([r["status"] for r in payload["pages"]], ["ok", "skipped_budget", "skipped_after_stop"])
        self.assertTrue(payload["ok"])

    def test_a_budget_with_room_lets_the_walk_proceed(self) -> None:
        self.responses[URL + "?paged=2"] = rss(item(2))
        with mock.patch.object(ingest_rss, "_RUN_DEADLINE", 500.0), \
                mock.patch.object(ingest_rss, "_monotonic", return_value=100.0):
            with tempfile.TemporaryDirectory() as tmp:
                payload = self.run_pages(self.src(pages=2), Path(tmp), rss(item(1)))
        self.assertEqual(payload["item_count"], 2)

    def test_page_one_parse_failure_triggers_no_extra_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = self.run_pages(self.src(pages=3), Path(tmp), b"<html>not a feed</html>")
        self.assertFalse(payload["ok"])
        self.assertEqual(self.fetched, [URL])
        self.assertNotIn("pages", payload)
        self.assertIn("pages_not_walked", payload["pages_note"])

    def test_extra_page_parse_failure_is_a_row_not_a_feed_failure(self) -> None:
        self.responses[URL + "?paged=2"] = b"<rss><channel><item></channel></rss>"
        with tempfile.TemporaryDirectory() as tmp:
            payload = self.run_pages(self.src(pages=2), Path(tmp), rss(item(1)))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["pages"][1]["status"], "parse_failed")
        self.assertIn("ParseError", payload["pages"][1]["error"])

    def test_empty_page_ends_the_walk(self) -> None:
        self.responses[URL + "?paged=2"] = rss()
        with tempfile.TemporaryDirectory() as tmp:
            payload = self.run_pages(self.src(pages=4), Path(tmp), rss(item(1)))
        self.assertEqual(self.fetched, [URL, URL + "?paged=2"])
        self.assertEqual([r["status"] for r in payload["pages"]],
                         ["ok", "empty", "skipped_after_stop", "skipped_after_stop"])

    def test_tolerant_parsing_applies_per_page_and_is_recorded(self) -> None:
        self.responses[URL + "?paged=2"] = rss(item(2, "Fish & Chips"))
        with tempfile.TemporaryDirectory() as tmp:
            payload = self.run_pages(self.src(pages=2), Path(tmp), rss(item(1)))
        self.assertEqual(payload["items"][1]["title"], "Fish & Chips")
        self.assertEqual(payload["pages"][1]["parse_mode"], "tolerant")
        self.assertNotIn("parse_mode", payload)  # page 1 was strict

    def test_invalid_and_clamped_pages_are_diagnosed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = self.run_pages(self.src(pages="many"), Path(tmp), rss(item(1)))
        self.assertIn("pages_invalid", payload["pages_note"])
        self.assertEqual(self.fetched, [URL])
        self.responses.update({f"{URL}?paged={n}": rss(item(10 + n)) for n in range(2, 6)})
        self.fetched.clear()
        with tempfile.TemporaryDirectory() as tmp:
            payload = self.run_pages(self.src(pages=50), Path(tmp), rss(item(1)))
        self.assertIn("pages_clamped", payload["pages_note"])
        self.assertEqual(len(self.fetched), ingest_rss.PAGES_MAX)

    def test_custom_page_parameter(self) -> None:
        self.responses[URL + "?page=2"] = rss(item(2))
        with tempfile.TemporaryDirectory() as tmp:
            payload = self.run_pages(self.src(pages=2, page_param="page"), Path(tmp), rss(item(1)))
        self.assertEqual(payload["item_count"], 2)
        self.assertEqual(self.fetched[1], URL + "?page=2")

    def test_extra_pages_are_snapshotted_without_confusing_the_xml_lookup(self) -> None:
        self.responses[URL + "?paged=2"] = rss(item(2))
        with tempfile.TemporaryDirectory() as tmp:
            payload = self.run_pages(self.src(pages=2), Path(tmp), rss(item(1)))
            dest = Path(tmp) / "data" / "raw" / "news"
            self.assertEqual(len(list(dest.glob("*.p2.page"))), 1)
            self.assertEqual([p.name for p in dest.glob("*.xml")], [Path(payload["xml_file"]).name])
            self.assertEqual(len(list(dest.glob("*.json"))), 1)  # one meta per run: normalize reads the newest

    def test_paginated_meta_is_deterministic(self) -> None:
        self.responses[URL + "?paged=2"] = rss(item(2))
        metas = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as tmp:
                payload = self.run_pages(self.src(pages=2), Path(tmp), rss(item(1)))
                metas.append((Path(tmp) / payload["meta_file"]).read_bytes())
        self.assertEqual(metas[0], metas[1])

    def test_main_sets_a_budget_clears_it_and_summarises_pages(self) -> None:
        seen = []

        def fake(src, fetched_at, stalled_hosts):
            seen.append(ingest_rss._RUN_DEADLINE)
            return {"source_id": "news", "ok": True, "item_count": 1,
                    "pages": [{"page": 1, "status": "ok"}, {"page": 2, "status": "skipped_budget"}],
                    "pages_requested": 2, "pages_note": "pages_clamped: x"}

        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            with mock.patch.object(ingest_rss, "ROOT", Path(tmp)), mock.patch.object(ingest_rss, "RAW_DIR", raw_dir), \
                    mock.patch.object(ingest_rss, "load_enabled_rss", return_value=[{"id": "news"}]), \
                    mock.patch.object(ingest_rss, "ingest_one", side_effect=fake), \
                    mock.patch.object(ingest_rss, "prune_raw_snapshots", return_value=0):
                ingest_rss.main()
            run = json.loads(next(raw_dir.glob("_run_*.json")).read_text(encoding="utf-8"))
        self.assertIsNotNone(seen[0])
        self.assertIsNone(ingest_rss._RUN_DEADLINE)
        row = run["results"][0]
        self.assertEqual((row["pages_requested"], row["pages_ok"]), (2, 1))
        self.assertEqual(row["pages_note"], "pages_clamped: x")

    def test_pagination_builds_no_request_of_its_own(self) -> None:
        # Extra pages travel through the same fetch_bytes (honest UA, public
        # guard, refusal-final); this code never constructs a request or header.
        import inspect
        text = inspect.getsource(ingest_rss._fetch_more_pages)
        for forbidden in ("urllib.request", "User-Agent", "Referer", "build_opener", "headers"):
            self.assertNotIn(forbidden, text)


class AtomEntries(unittest.TestCase):
    def wrap(self, entry: str) -> bytes:
        return f'<feed xmlns="http://www.w3.org/2005/Atom"><title>f</title>{entry}</feed>'.encode("utf-8")

    def test_entry_fields(self) -> None:
        first, second = ingest_rss.parse_feed(ATOM_FIXTURE, URL, {})
        self.assertEqual(first["title"], "Entrée inventée un")
        self.assertEqual(first["url"], "https://news.example/atom/1")  # alternate, not self
        self.assertEqual(first["published_at"], "2026-10-05T20:00:00Z")
        self.assertEqual(first["updated_at"], "2026-10-05T22:00:00Z")
        self.assertEqual(first["author"], "Alex Exemple")
        self.assertEqual(first["guid"], "tag:news.example,2026:1")
        self.assertEqual(first["body"], "Résumé inventé.")
        self.assertEqual(second["url"], "https://news.example/atom/2")  # relative href, no rel = alternate
        self.assertIsNone(second["published_at"])  # never guessed from updated
        self.assertEqual(second["updated_at"], "2026-10-05T23:00:00Z")
        self.assertEqual(second["body"], "<p>Contenu inventé</p>")

    def test_html_alternate_preferred_over_other_alternate_types(self) -> None:
        raw = self.wrap('<entry><title>t</title>'
                        '<link rel="alternate" type="application/pdf" href="https://news.example/x.pdf"/>'
                        '<link rel="alternate" type="text/html" href="https://news.example/x"/></entry>')
        self.assertEqual(ingest_rss.parse_feed(raw, URL, {})[0]["url"], "https://news.example/x")

    def test_non_article_links_are_never_the_url(self) -> None:
        raw = self.wrap('<entry><title>Seulement un titre</title>'
                        '<link rel="self" href="https://news.example/self"/>'
                        '<link rel="replies" href="https://news.example/r"/>'
                        '<link rel="enclosure" href="https://news.example/e.mp3"/></entry>')
        out = ingest_rss.parse_feed(raw, URL, {})
        self.assertEqual(len(out), 1)
        self.assertIsNone(out[0]["url"])  # kept for its title; the absence is the fact

    def test_email_only_author_is_dropped(self) -> None:
        raw = self.wrap('<entry><title>t</title><link href="https://news.example/x"/>'
                        '<author><name>desk@news.example</name><email>desk@news.example</email></author></entry>')
        self.assertIsNone(ingest_rss.parse_feed(raw, URL, {})[0]["author"])

    def test_entry_with_neither_url_nor_title_is_counted_dropped(self) -> None:
        raw = self.wrap('<entry><id>x</id></entry><entry><title>ok</title></entry>')
        stats: dict = {}
        out = ingest_rss.parse_feed(raw, URL, stats)
        self.assertEqual(len(out), 1)
        self.assertEqual((stats["item_nodes"], stats["dropped_no_url_title"]), (2, 1))

    def test_atom_with_a_bare_ampersand_is_repaired_too(self) -> None:
        raw = self.wrap('<entry><title>Black & White</title><link href="https://news.example/x"/></entry>')
        stats: dict = {}
        self.assertEqual(ingest_rss.parse_feed(raw, URL, stats)[0]["title"], "Black & White")
        self.assertEqual(stats["parse_mode"], "tolerant")

    def test_atom_goes_through_ingest_and_normalize(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload, _ = run_ingest(ATOM_FIXTURE, root=Path(tmp))
        cand = normalize.normalize_item(payload["items"][0], payload)
        self.assertEqual((cand["author"], cand["url"]), ("Alex Exemple", "https://news.example/atom/1"))
        self.assertEqual(cand["published_at"], "2026-10-05T20:00:00+00:00")


class SourceMetadataPassThrough(unittest.TestCase):
    OWN = {"owner_group": "quebecor", "ownership_class": "quebecor"}

    def test_declared_ownership_reaches_meta_and_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload, meta = run_ingest(RSS_FIXTURE, dict(SRC, **self.OWN), root=Path(tmp))
        doc = json.loads(meta)
        self.assertEqual((doc["owner_group"], doc["ownership_class"]), ("quebecor", "quebecor"))
        cand = normalize.normalize_item(payload["items"][0], payload)
        self.assertEqual((cand["owner_group"], cand["ownership_class"], cand["language"]),
                         ("quebecor", "quebecor", "fr"))

    def test_existing_values_are_unchanged_by_the_pass_through(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plain, _ = run_ingest(RSS_FIXTURE, root=Path(tmp))
        with tempfile.TemporaryDirectory() as tmp:
            rich, _ = run_ingest(RSS_FIXTURE, dict(SRC, **self.OWN), root=Path(tmp))
        for key in plain:
            self.assertEqual(plain[key], rich[key], key)
        before = [normalize.normalize_item(it, plain) for it in plain["items"]]
        after = [normalize.normalize_item(it, rich) for it in rich["items"]]
        for old, new in zip(before, after):
            self.assertEqual({k: v for k, v in new.items() if k not in self.OWN}, old)

    def test_undeclared_or_blank_ownership_adds_no_key(self) -> None:
        for extra in ({}, {"owner_group": None, "ownership_class": ""}, {"owner_group": 5, "ownership_class": "  "}):
            with tempfile.TemporaryDirectory() as tmp:
                payload, meta = run_ingest(RSS_FIXTURE, dict(SRC, **extra), root=Path(tmp))
            self.assertNotIn("owner_group", json.loads(meta))
            cand = normalize.normalize_item(payload["items"][0], payload)
            self.assertNotIn("owner_group", cand)
            self.assertNotIn("ownership_class", cand)

    def test_values_are_stripped_and_language_still_flows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload, _ = run_ingest(ATOM_FIXTURE, dict(SRC, owner_group=" cn2i ", language="en"), root=Path(tmp))
        self.assertEqual(payload["owner_group"], "cn2i")
        self.assertEqual(payload["items"][0]["language"], "en")

    def test_the_registry_loader_reads_the_new_optional_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sources.yaml"
            path.write_text("sources:\n  - id: x\n    url: https://x.example/feed\n    type: rss\n"
                            "    enabled: true\n    pages: 3\n    owner_group: quebecor\n"
                            "    ownership_class: quebecor\n", encoding="utf-8", newline="\n")
            (rec,) = ingest_rss.load_enabled_rss(path)
        self.assertEqual((rec["pages"], rec["owner_group"], rec["ownership_class"]), (3, "quebecor", "quebecor"))

    def test_the_shipped_registry_gets_no_pagination_from_this_change(self) -> None:
        for rec in ingest_rss.load_sources(ingest_rss.SOURCES_PATH):
            self.assertNotIn("pages", rec)
            self.assertNotIn("page_param", rec)


if __name__ == "__main__":
    unittest.main()
