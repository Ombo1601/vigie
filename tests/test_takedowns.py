"""R10 — publisher takedowns: one file, enforced at every boundary.

takedowns.yaml lists what a publisher or rights-holder asked Vigie to stop
relaying (a source, a domain, an article URL, an image by URL or sha256).
These tests lock: the file format (a missing or empty file is a no-op; an
invalid entry is diagnosed), each enforcement point (collection, normalize,
render, media, purge, staging), the public transparency (publisher, scope and
date — never the value), and that the registre's seals never move.
All hermetic: temp directories, injected fetchers, no network.
"""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import harness

import cluster_issues
import fetch_brief_media as fbm
import ingest_rss
import method_site
import normalize
import registre
import resident_brief as brief
import stage_public
import takedown

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100
OTHER_JPEG = b"\xff\xd8\xff\xe0" + b"\x01" * 100
OG_HTML = '<meta property="og:image" content="https://cdn.example/a.jpg">'

SOURCES = (
    "sources:\n"
    "  - id: keep\n    name: Gardé\n    institution: keep-inst\n    institution_name: Gardé Média\n"
    "    type: rss\n    source_kind: media\n    url: https://news.example/feed\n    enabled: true\n"
    "  - id: gone\n    name: Retiré\n    institution: gone-inst\n    institution_name: Retiré Média\n"
    "    type: rss\n    source_kind: media\n    url: https://gone.example/rss\n    enabled: true\n"
    "  - id: sister-a\n    name: Sœur A\n    institution: sisters\n    institution_name: Les Sœurs\n"
    "    type: rss\n    url: https://sisters.example/a\n    enabled: true\n"
    "  - id: sister-b\n    name: Sœur B\n    institution: sisters\n    institution_name: Les Sœurs\n"
    "    type: rss\n    url: https://sisters.example/b\n    enabled: true\n"
    "  - id: old-cut\n    name: Ancienne coupe\n    type: rss\n    url: https://old.example/rss\n"
    "    enabled: false\n    cut_reason: Flux mort depuis 2026-09-01\n"
)


def entry(ident: str, kind: str, value: str, *, by: str = "Exemple Média",
          status: str = "active", date: str = "2026-10-05") -> str:
    return (f"  - id: {ident}\n    kind: {kind}\n    value: {value}\n"
            f"    requested_at: {date}\n    by: {by}\n    status: {status}\n")


def takedowns_text(*entries: str) -> str:
    return "version: 1\ntakedowns:\n" + "".join(entries) if entries else "version: 1\ntakedowns: []\n"


class TempRoot(unittest.TestCase):
    """A temp tree with sources.yaml + takedowns.yaml, wired into the modules."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.sources = self.root / "sources.yaml"
        self.sources.write_text(SOURCES, encoding="utf-8")
        self.file = self.root / "takedowns.yaml"
        for target, name, value in (
            (takedown, "TAKEDOWNS_PATH", self.file),
            (takedown, "SOURCES_PATH", self.sources),
        ):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def withdraw(self, *entries: str) -> takedown.Rules:
        self.file.write_text(takedowns_text(*entries), encoding="utf-8")
        loaded, errors = takedown.load(self.file)
        self.assertEqual(errors, [])
        return takedown.Rules(loaded)


# --------------------------------------------------------------------------- #
# The file
# --------------------------------------------------------------------------- #
class FileFormat(TempRoot):
    def test_missing_file_is_a_no_op(self) -> None:
        self.assertEqual(takedown.load(self.root / "absent.yaml"), ([], []))
        self.assertFalse(takedown.load_rules(self.root / "absent.yaml"))

    def test_empty_comment_only_and_empty_list_are_no_ops(self) -> None:
        for text in ("", "\n\n", "# nothing yet\n", "version: 1\ntakedowns: []\n",
                     "version: 1\ntakedowns:\n  # none\n"):
            with self.subTest(text=text):
                self.file.write_text(text, encoding="utf-8")
                self.assertEqual(takedown.load(self.file), ([], []))

    def test_the_shipped_file_is_valid(self) -> None:
        entries, errors = takedown.load(harness.ROOT / "takedowns.yaml")
        self.assertEqual(errors, [])
        raw = (harness.ROOT / "takedowns.yaml").read_bytes()
        self.assertNotIn(b"\r\n", raw, "takedowns.yaml must stay LF")
        for e in entries:
            self.assertNotIn("@", e["by"])

    def test_each_kind_is_parsed_and_normalized(self) -> None:
        self.file.write_text(takedowns_text(
            entry("s1", "source", "gone"),
            entry("h1", "host", "www.Blocked.example"),
            entry("u1", "url", "https://news.example/story?id=7&utm_source=x"),
            entry("i1", "image", "https://cdn.example/a.jpg"),
            entry("i2", "image", hashlib.sha256(JPEG).hexdigest()[:20] + ".jpg"),
        ), encoding="utf-8")
        entries, errors = takedown.load(self.file)
        self.assertEqual(errors, [])
        values = {e["id"]: e["value"] for e in entries}
        self.assertEqual(values["s1"], "gone")
        self.assertEqual(values["h1"], "blocked.example")
        self.assertEqual(values["u1"], "https://news.example/story?id=7")
        self.assertEqual(values["i1"], "https://cdn.example/a.jpg")
        self.assertEqual(values["i2"], hashlib.sha256(JPEG).hexdigest()[:20])

    def test_invalid_entries_are_diagnosed_and_not_guessed(self) -> None:
        self.file.write_text(takedowns_text(
            entry("bad-kind", "article", "https://news.example/a"),
            entry("mail", "url", "https://news.example/a", by="jean@exemple.ca"),
            entry("phone", "url", "https://news.example/b", by="Jean 418-555-0199"),
            entry("dup", "source", "gone"),
            entry("dup", "source", "keep"),
            entry("nodate", "source", "gone", date="hier"),
            entry("local", "url", "http://127.0.0.1/x"),
        ), encoding="utf-8")
        entries, errors = takedown.load(self.file)
        self.assertEqual([e["id"] for e in entries], ["dup"])
        joined = "\n".join(errors)
        for needle in ("bad-kind", "mail", "phone", "duplicate id", "nodate", "local"):
            self.assertIn(needle, joined)

    def test_unknown_status_is_reported_and_enforced(self) -> None:
        self.file.write_text(takedowns_text(entry("s1", "source", "gone", status="maybe")), encoding="utf-8")
        entries, errors = takedown.load(self.file)
        self.assertEqual(len(errors), 1)
        self.assertTrue(takedown.Rules(entries).match_source({"id": "gone"}))

    def test_lifted_requests_are_kept_but_not_enforced(self) -> None:
        rules = self.withdraw(entry("s1", "source", "gone", status="lifted"))
        self.assertFalse(rules)
        rows = takedown.public_rows(takedown.load(self.file)[0])
        self.assertEqual(rows[0]["status"], "lifted")


# --------------------------------------------------------------------------- #
# Matching and pure filters
# --------------------------------------------------------------------------- #
class Matching(TempRoot):
    def test_each_kind_matches_only_its_scope(self) -> None:
        rules = self.withdraw(
            entry("s1", "source", "gone"),
            entry("h1", "host", "blocked.example"),
            entry("u1", "url", "https://news.example/story"),
            entry("i1", "image", "https://cdn.example/a.jpg"),
            entry("i2", "image", hashlib.sha256(JPEG).hexdigest()),
        )
        self.assertTrue(rules.match_item({"source_id": "gone", "url": "https://gone.example/x"}))
        self.assertTrue(rules.match_item({"source_id": "keep", "url": "https://www.blocked.example/a"}))
        self.assertTrue(rules.match_item({"source_id": "keep", "url": "https://sub.blocked.example/a"}))
        self.assertFalse(rules.match_item({"source_id": "keep", "url": "https://notblocked.example/a"}))
        self.assertTrue(rules.match_item({"source_id": "keep", "url": "https://news.example/story?utm_medium=rss"}))
        self.assertFalse(rules.match_item({"source_id": "keep", "url": "https://news.example/story-2"}))
        self.assertTrue(rules.match_image("https://cdn.example/a.jpg"))
        self.assertTrue(rules.match_image(sha=hashlib.sha256(JPEG).hexdigest()))
        self.assertTrue(rules.match_image(file=hashlib.sha256(JPEG).hexdigest()[:20] + ".jpg"))
        self.assertFalse(rules.match_image("https://cdn.example/b.jpg", sha=hashlib.sha256(OTHER_JPEG).hexdigest()))
        self.assertTrue(rules.match_source({"id": "keep", "url": "https://blocked.example/rss"}))

    def test_filters_are_identity_without_takedowns(self) -> None:
        rules = takedown.Rules([])
        items = [{"source_id": "gone", "url": "https://gone.example/x"}]
        issues = [{"issue_id": "i"}]
        ledger = {"new": [{"label_source": {"url": "https://gone.example/x"}}]}
        self.assertIs(takedown.filter_items(items, rules)[0], items)
        self.assertIs(takedown.filter_issues(issues, rules), issues)
        self.assertIs(takedown.filter_ledger(ledger, rules), ledger)

    def test_dossiers_lose_withdrawn_items_and_fall_below_two_voices(self) -> None:
        rules = self.withdraw(entry("u1", "url", "https://news.example/gone"),
                              entry("u2", "url", "https://news.example/headline"))

        def tension(iid, *urls):
            return {"institution_id": iid, "items": [{"source_id": iid, "url": u} for u in urls]}

        survives = {"issue_id": "a", "tensions": [tension("x", "https://news.example/1", "https://news.example/gone"),
                                                  tension("y", "https://news.example/2")]}
        collapses = {"issue_id": "b", "tensions": [tension("x", "https://news.example/gone"),
                                                   tension("y", "https://news.example/3")]}
        labelled = {"issue_id": "c", "label_source": {"url": "https://news.example/headline", "source_id": "x"},
                    "tensions": [tension("x", "https://news.example/4"), tension("y", "https://news.example/5")]}
        out = takedown.filter_issues([survives, collapses, labelled], rules)
        self.assertEqual([i["issue_id"] for i in out], ["a"])
        self.assertEqual(out[0]["item_count"], 2)
        self.assertNotIn("https://news.example/gone", json.dumps(out))
        ledger = takedown.filter_ledger({"new": [{"issue_id": "c", "label_source": labelled["label_source"]}]}, rules)
        self.assertNotIn("url", ledger["new"][0]["label_source"])
        edges = {"streets": {"s": {"matched_issue_ids": ["a", "b"]}}, "issues": {"a": {}, "b": {}},
                 "matched_issue_count": 2}
        kept = takedown.filter_edges(edges, {i["issue_id"] for i in out})
        self.assertEqual(kept["streets"]["s"]["matched_issue_ids"], ["a"])
        self.assertEqual((list(kept["issues"]), kept["matched_issue_count"]), (["a"], 1))


# --------------------------------------------------------------------------- #
# Collection and normalize
# --------------------------------------------------------------------------- #
class CollectionAndNormalize(TempRoot):
    def test_a_withdrawn_source_or_domain_is_never_fetched_nor_followed(self) -> None:
        self.assertEqual({s["id"] for s in ingest_rss.load_enabled_rss(self.sources)},
                         {"keep", "gone", "sister-a", "sister-b"})
        self.withdraw(entry("s1", "source", "gone"), entry("h1", "host", "sisters.example"))
        self.assertEqual([s["id"] for s in ingest_rss.load_enabled_rss(self.sources)], ["keep"])

    def test_normalize_drops_every_kind_and_counts_it(self) -> None:
        raw, out = self.root / "raw", self.root / "normalized"
        now = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
        items = {
            "keep": ["https://news.example/ok", "https://news.example/gone?utm_source=rss",
                     "https://blocked.example/a"],
            "gone": ["https://gone.example/1"],
        }
        for sid, urls in items.items():
            (raw / sid).mkdir(parents=True)
            (raw / sid / "20261005T110000Z_ok.json").write_text(json.dumps({
                "ok": True, "source_id": sid, "fetched_at": "2026-10-05T11:00:00+00:00",
                "items": [{"title": f"t{n}", "url": u} for n, u in enumerate(urls)],
            }), encoding="utf-8")
        self.withdraw(entry("s1", "source", "gone"), entry("h1", "host", "blocked.example"),
                      entry("u1", "url", "https://news.example/gone"))
        with mock.patch.multiple(normalize, RAW_DIR=raw, OUT_DIR=out, SOURCES_PATH=self.sources), \
                mock.patch.object(normalize, "utc_now", return_value=now):
            self.assertEqual(normalize.main(), 0)
        doc = json.loads((out / "latest_candidates.json").read_text(encoding="utf-8"))
        self.assertEqual([c["url"] for c in doc["candidates"]], ["https://news.example/ok"])
        self.assertEqual(doc["source_status"]["keep"]["dropped"]["withdrawn_on_request"], 2)
        self.assertNotIn("gone", doc["source_status"])

    def test_normalize_is_unchanged_without_takedowns(self) -> None:
        raw, out = self.root / "raw", self.root / "normalized"
        (raw / "keep").mkdir(parents=True)
        (raw / "keep" / "20261005T110000Z_ok.json").write_text(json.dumps({
            "ok": True, "source_id": "keep", "fetched_at": "2026-10-05T11:00:00+00:00",
            "items": [{"title": "t", "url": "https://news.example/gone"}],
        }), encoding="utf-8")
        with mock.patch.multiple(normalize, RAW_DIR=raw, OUT_DIR=out, SOURCES_PATH=self.sources), \
                mock.patch.object(normalize, "utc_now", return_value=datetime(2026, 10, 5, 12, tzinfo=timezone.utc)):
            self.assertEqual(normalize.main(), 0)
        doc = json.loads((out / "latest_candidates.json").read_text(encoding="utf-8"))
        self.assertEqual(len(doc["candidates"]), 1)
        self.assertEqual(doc["source_status"]["keep"]["dropped"]["withdrawn_on_request"], 0)


# --------------------------------------------------------------------------- #
# Media: never fetched, never re-hosted, stored copies deleted
# --------------------------------------------------------------------------- #
class Media(TempRoot):
    def setUp(self) -> None:
        super().setUp()
        self.media_dir = self.root / "media" / "brief"
        self.manifest = self.root / "media" / "brief_manifest.json"
        for name, value in (("SLEEP", 0), ("RAW_DIR", self.root / "raw"), ("HEALTH", self.root / "health.json")):
            patcher = mock.patch.object(fbm, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.cand = {"id": "c1", "source_id": "keep", "url": "https://news.example/a"}
        self.uid = fbm.brief_uid(self.cand["url"])

    def run_media(self, image=(JPEG, "jpg")):
        with mock.patch.object(fbm.fetch_media, "fetch_html", return_value=OG_HTML) as html, \
                mock.patch.object(fbm, "fetch_image", return_value=image) as img:
            doc = fbm.update_media([self.cand], media_dir=self.media_dir, manifest_path=self.manifest)
        return doc, html, img

    def files(self) -> list[str]:
        return sorted(p.name for p in self.media_dir.iterdir()) if self.media_dir.is_dir() else []

    def test_withdrawn_article_is_never_scoped_or_fetched(self) -> None:
        self.withdraw(entry("u1", "url", "https://news.example/a"))
        doc, html, img = self.run_media()
        html.assert_not_called()
        img.assert_not_called()
        self.assertEqual(doc["media"], {})

    def test_withdrawn_image_url_is_never_fetched(self) -> None:
        self.withdraw(entry("i1", "image", "https://cdn.example/a.jpg"))
        doc, _, img = self.run_media()
        img.assert_not_called()
        self.assertEqual(doc["media"][self.uid]["reason"], "image_withdrawn_on_request")
        self.assertIsNone(doc["media"][self.uid]["file"])
        self.assertEqual(self.files(), [])

    def test_same_bytes_under_another_url_are_never_stored(self) -> None:
        self.withdraw(entry("i1", "image", hashlib.sha256(JPEG).hexdigest()))
        doc, _, _ = self.run_media()
        self.assertIsNone(doc["media"][self.uid]["file"])
        self.assertEqual(self.files(), [])

    def test_a_stored_copy_is_deleted_on_the_next_run(self) -> None:
        doc, _, _ = self.run_media()
        stored = doc["media"][self.uid]["file"]
        self.assertEqual(self.files(), [stored])
        self.withdraw(entry("i1", "image", "https://cdn.example/a.jpg"))
        doc, _, img = self.run_media()
        img.assert_not_called()
        self.assertEqual(self.files(), [])
        self.assertIsNone(doc["media"][self.uid]["file"])

    def test_purge_media_by_sha_deletes_file_and_entry(self) -> None:
        self.run_media()
        self.assertEqual(len(self.files()), 1)
        rules = self.withdraw(entry("i1", "image", hashlib.sha256(JPEG).hexdigest()))
        report = takedown.purge_media(rules, media_dir=self.media_dir, manifest_path=self.manifest)
        self.assertEqual(report, {"files": 1, "entries": 1})
        self.assertEqual(self.files(), [])
        self.assertEqual(json.loads(self.manifest.read_text(encoding="utf-8"))["media"], {})

    def test_the_renderer_never_shows_a_withdrawn_image(self) -> None:
        self.run_media()
        with mock.patch.object(brief, "MEDIA_MANIFEST", self.manifest):
            self.assertIn(self.uid, brief.load_brief_media())
            self.withdraw(entry("i1", "image", "https://cdn.example/a.jpg"))
            self.assertNotIn(self.uid, brief.load_brief_media())


# --------------------------------------------------------------------------- #
# Purge of a cut source's data
# --------------------------------------------------------------------------- #
class Purge(TempRoot):
    def test_purge_source_deletes_raw_bodies_cache_and_images(self) -> None:
        raw = self.root / "raw"
        for sid in ("gone", "keep"):
            (raw / sid).mkdir(parents=True)
            (raw / sid / "20261005T000000Z_abc.xml").write_text("<rss/>", encoding="utf-8")
        bodies = raw / "_bodies"
        bodies.mkdir()
        gone_body = bodies / (hashlib.sha256(b"https://gone.example/rss").hexdigest()[:32] + ".body")
        keep_body = bodies / (hashlib.sha256(b"https://news.example/feed").hexdigest()[:32] + ".body")
        gone_body.write_bytes(b"x")
        keep_body.write_bytes(b"y")
        (raw / "_http_cache.json").write_text(json.dumps({
            "https://gone.example/rss": {"etag": "1"}, "https://news.example/feed": {"etag": "2"}}), encoding="utf-8")
        media_dir = self.root / "media"
        media_dir.mkdir()
        gone_file = hashlib.sha256(JPEG).hexdigest()[:20] + ".jpg"
        keep_file = hashlib.sha256(OTHER_JPEG).hexdigest()[:20] + ".jpg"
        (media_dir / gone_file).write_bytes(JPEG)
        (media_dir / keep_file).write_bytes(OTHER_JPEG)
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"method": "brief-media-v2", "media": {
            "u1": {"file": gone_file, "article_url": "https://gone.example/1"},
            "u2": {"file": keep_file, "article_url": "https://news.example/1"},
        }}), encoding="utf-8")
        store = self.root / "latest_enriched.json"
        store.write_text(json.dumps({"candidates": [
            {"source_id": "gone", "url": "https://gone.example/1"},
            {"source_id": "keep", "url": "https://news.example/1"}]}), encoding="utf-8")
        kwargs = dict(raw_dir=raw, media_dir=media_dir, manifest_path=manifest, article_stores=(store,))
        report = takedown.purge_source("gone", **kwargs)
        self.assertEqual((report["raw_files"], report["bodies"], report["cache_rows"], report["media_files"]),
                         (1, 1, 1, 1))
        self.assertFalse((raw / "gone").exists())
        self.assertTrue((raw / "keep" / "20261005T000000Z_abc.xml").exists())
        self.assertFalse(gone_body.exists())
        self.assertTrue(keep_body.exists())
        self.assertEqual(list(json.loads((raw / "_http_cache.json").read_text(encoding="utf-8"))),
                         ["https://news.example/feed"])
        self.assertEqual(sorted(p.name for p in media_dir.iterdir()), [keep_file])
        self.assertEqual(list(json.loads(manifest.read_text(encoding="utf-8"))["media"]), ["u2"])
        again = takedown.purge_source("gone", **kwargs)
        self.assertEqual((again["raw_files"], again["bodies"], again["cache_rows"]), (0, 0, 0))

    def test_purge_refuses_a_path_like_source_id(self) -> None:
        for bad in ("../data", "", "a/b"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                takedown.purge_source(bad, raw_dir=self.root / "raw")

    def test_enforce_purges_withdrawn_sources_and_is_a_no_op_otherwise(self) -> None:
        raw = self.root / "raw"
        (raw / "gone").mkdir(parents=True)
        (raw / "gone" / "20261005T000000Z_abc.json").write_text("{}", encoding="utf-8")
        kwargs = dict(raw_dir=raw, media_dir=self.root / "m", manifest_path=self.root / "m.json")
        with mock.patch.object(takedown, "ARTICLE_STORES", ()):
            self.assertEqual(takedown.enforce(takedown.Rules([]), **kwargs)["sources"], [])
            self.assertTrue((raw / "gone").exists())
            report = takedown.enforce(self.withdraw(entry("s1", "source", "gone")), **kwargs)
        self.assertEqual([r["source_id"] for r in report["sources"]], ["gone"])
        self.assertFalse((raw / "gone").exists())


# --------------------------------------------------------------------------- #
# Staging refuses what still matches
# --------------------------------------------------------------------------- #
class StageRefusal(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.public = self.root / "public"
        (self.public / "methode").mkdir(parents=True)
        self.output = self.root / "deploy" / "public"
        for name in stage_public.METHODS:
            (self.root / name).write_text("Public method", encoding="utf-8")
        (self.public / "index.html").write_text(
            '<a href="https://news.example/ok">ok</a><a href="https://news.example/story?a=1&amp;b=2">s</a>',
            encoding="utf-8")
        (self.public / "morning.html").write_text("<h1>m</h1>", encoding="utf-8")
        (self.public / "explorer.html").write_text("<h1>e</h1>", encoding="utf-8")
        (self.public / "favicon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
        (self.public / "methode" / "sources.html").write_text(
            '<a href="https://blocked.example/">Accueil de la source</a>', encoding="utf-8")
        self.media = self.root / "data" / "media" / "brief"
        self.media.mkdir(parents=True)
        self.file_name = hashlib.sha256(JPEG).hexdigest()[:20] + ".jpg"
        (self.media / self.file_name).write_bytes(JPEG)

    def stage_with(self, *entries: str) -> dict:
        (self.root / "takedowns.yaml").write_text(takedowns_text(*entries), encoding="utf-8")
        return stage_public.stage(self.root, self.output)

    def test_no_takedowns_stages_normally(self) -> None:
        self.assertIn("media/" + self.file_name, self.stage_with()["files"])
        (self.root / "takedowns.yaml").unlink()
        self.assertIn("index.html", stage_public.stage(self.root, self.output)["files"])

    def test_a_withdrawn_media_file_blocks_the_release(self) -> None:
        with self.assertRaisesRegex(ValueError, r"R10.*\n.*media/" + self.file_name):
            self.stage_with(entry("i1", "image", hashlib.sha256(JPEG).hexdigest()))
        self.assertFalse(self.output.exists())

    def test_a_page_linking_a_withdrawn_article_blocks_the_release(self) -> None:
        with self.assertRaisesRegex(ValueError, r"index\.html: still references a withdrawn url"):
            self.stage_with(entry("u1", "url", "https://news.example/story?a=1&b=2"))

    def test_a_withdrawn_sources_article_still_in_the_stores_blocks_the_release(self) -> None:
        store = self.root / "data" / "normalized" / "latest_ranked.json"
        store.parent.mkdir(parents=True)
        store.write_text(json.dumps({"candidates": [{"source_id": "gone", "url": "https://news.example/ok"}]}),
                         encoding="utf-8")
        with self.assertRaisesRegex(ValueError, r"index\.html: still references a withdrawn source"):
            self.stage_with(entry("s1", "source", "gone"))

    def test_a_withdrawn_sources_stored_preview_is_purged_and_refused_at_the_gate(self) -> None:
        article = "https://news.example/gone-story"
        store = self.root / "data" / "normalized" / "latest_ranked.json"
        store.parent.mkdir(parents=True)
        store.write_text(json.dumps({"candidates": [{"source_id": "gone", "url": article}]}), encoding="utf-8")
        manifest = self.root / "data" / "media" / "brief_manifest.json"
        manifest.write_text(json.dumps({"media": {"u": {
            "file": self.file_name, "image_url": "https://cdn.other/x.jpg", "article_url": article}}}),
            encoding="utf-8")
        with self.assertRaisesRegex(ValueError, r"R10.*\n.*media/" + self.file_name):
            self.stage_with(entry("s1", "source", "gone"))
        rules = takedown.Rules(takedown.load(self.root / "takedowns.yaml")[0])
        hint = takedown.source_article_urls(set(rules.sources), (store,))
        report = takedown.purge_media(rules, media_dir=self.media, manifest_path=manifest, article_urls=hint)
        self.assertEqual(report, {"files": 1, "entries": 1})
        self.assertFalse((self.media / self.file_name).exists())

    def test_domain_takedown_spares_the_method_pages_only(self) -> None:
        self.stage_with(entry("h1", "host", "blocked.example"))  # homepage on a method page: allowed
        (self.public / "index.html").write_text('<a href="https://blocked.example/x">x</a>', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, r"index\.html: still references a withdrawn host"):
            self.stage_with(entry("h1", "host", "blocked.example"))

    def test_an_invalid_takedowns_file_blocks_the_release(self) -> None:
        with self.assertRaisesRegex(ValueError, "takedowns.yaml invalid"):
            self.stage_with(entry("bad", "article", "https://news.example/x"))


# --------------------------------------------------------------------------- #
# Transparency: sources page, silence map, registre
# --------------------------------------------------------------------------- #
class PublicRendering(TempRoot):
    def test_sources_page_lists_requests_without_their_value(self) -> None:
        self.withdraw(entry("s1", "source", "gone", by="Retiré Média", date="2026-10-04"),
                      entry("u1", "url", "https://news.example/secret-article", by="Gardé Média"))
        with mock.patch.object(method_site, "ROOT", self.root):
            page = method_site._sources_html()
        self.assertIn('id="retraits"', page)
        self.assertIn("Retiré Média", page)
        self.assertIn("2026-10-04", page)
        self.assertIn("flux entier", page)
        self.assertIn("article", page)
        self.assertNotIn("secret-article", page)
        # The withdrawn feed left the active table but stays named, with the reason.
        self.assertIn("<strong>Retiré</strong> — retiré à la demande de l’éditeur", page)
        self.assertNotIn("https://gone.example", page)
        # A registry cut (enabled: false) never vanishes either.
        self.assertIn("Flux mort depuis 2026-09-01", page)

    def test_sources_page_says_so_when_there_is_no_request(self) -> None:
        self.withdraw()
        with mock.patch.object(method_site, "ROOT", self.root):
            page = method_site._sources_html()
        self.assertIn("Aucune demande de retrait", page)

    def test_one_withdrawn_sister_feed_does_not_withdraw_the_voice(self) -> None:
        self.withdraw(entry("s1", "source", "sister-a"))
        self.assertEqual(takedown.withdrawn_institutions(sources_path=self.sources), {})
        self.withdraw(entry("s1", "source", "sister-a"), entry("s2", "source", "sister-b"))
        self.assertEqual(list(takedown.withdrawn_institutions(sources_path=self.sources)), ["sisters"])

    def test_silence_map_names_a_withdrawn_institution(self) -> None:
        self.withdraw(entry("s1", "source", "gone"))
        chancellery = ingest_rss.load_enabled_rss(self.sources)
        withdrawn = takedown.withdrawn_institutions(sources_path=self.sources)
        sm = cluster_issues.silence_map({"keep-inst"}, chancellery, withdrawn)
        self.assertNotIn("gone-inst", [s["institution_id"] for s in sm["silent"]])
        self.assertEqual([w["institution_id"] for w in sm["withdrawn"]], ["gone-inst"])
        self.assertEqual(sm["withdrawn"][0]["label"], "retiré à la demande de l’éditeur")
        self.assertNotIn("withdrawn", cluster_issues.silence_map({"keep-inst"}, chancellery))
        roster = brief.dossier_voices_html({"tensions": [], "silence": sm})
        self.assertIn("Retiré Média", roster)
        self.assertIn("retirée à la demande de l’éditeur", roster)

    def _sealed_state(self) -> dict:
        payload = {
            "chancellery_institutions": ["keep-inst", "gone-inst", "other"],
            "issues": [{"issue_id": "d1", "sources": ["keep-inst", "other"], "item_count": 2,
                        "silence": {"silent": [{"institution_id": "gone-inst", "institution_name": "Retiré Média"}]},
                        "tensions": [{"institution_id": "keep-inst", "institution_name": "Gardé Média"},
                                     {"institution_id": "other", "institution_name": "Autre"}]}],
        }
        state = registre.empty_state()
        state["names"] = registre.institution_names(payload)
        record = registre.edition_record(payload, "2026-10-05T06:00:00+00:00",
                                         {"gone-inst": {"items": 3, "feeds_ok": 1, "feeds_total": 1}})
        state, action = registre.seal_edition(state, record)
        self.assertEqual(action, "appended")
        return state

    def test_registre_view_reports_withdrawn_and_leaves_sealed_state_untouched(self) -> None:
        state = self._sealed_state()
        before = copy.deepcopy(state)
        self.assertEqual(
            {r["institution_id"]: r["current"] for r in registre.institution_register(state, {})}["gone-inst"],
            registre.STATE_PUBLISHED)
        self.withdraw(entry("s1", "source", "gone"))
        with mock.patch.object(registre, "SOURCES_PATH", self.sources):
            register = registre.institution_register(state)
            page = registre.render_registre_html(state)
            public = registre.public_institutions(state)
        row = {r["institution_id"]: r for r in register}["gone-inst"]
        self.assertEqual(row["current"], registre.STATE_WITHDRAWN)
        self.assertEqual(row["withdrawn_requested_at"], "2026-10-05")
        self.assertEqual(registre.state_label_fr(row), "retirée à la demande de l’éditeur")
        self.assertIn("retirée à la demande de l’éditeur", page)
        self.assertIn(registre.STATE_WITHDRAWN, public["states"])
        # Seals and voice rows are untouched: the chain still verifies byte for byte.
        self.assertEqual(state, before)
        self.assertEqual(registre.verify_chain(state["seals"]), (True, "1 seals verified"))
        self.assertEqual(state["seals"][0]["leaf"], registre.leaf_of(before["seals"][0]["record"]))
        strip = brief.silence_bar([], register)
        self.assertIn("Retirée à la demande de l’éditeur", strip)

    def test_registre_view_is_unchanged_without_takedowns(self) -> None:
        state = self._sealed_state()
        with mock.patch.object(registre, "SOURCES_PATH", self.sources):
            register = registre.institution_register(state)
        self.assertNotIn(registre.STATE_WITHDRAWN, {r["current"] for r in register})
        self.assertFalse(any("withdrawn_requested_at" in r for r in register))


class Wiring(unittest.TestCase):
    def test_the_purge_runs_after_collection_and_before_normalize(self) -> None:
        import pipeline

        s = pipeline.SCRIPTS
        self.assertLess(s.index("ingest_civic.py"), s.index("takedown.py"))
        self.assertLess(s.index("takedown.py"), s.index("normalize.py"))
        self.assertNotIn("takedown.py", pipeline.OFFLINE_FLAGGED)

    def test_check_flag_validates_the_shipped_file(self) -> None:
        self.assertEqual(takedown.main(["--check"]), 0)


if __name__ == "__main__":
    unittest.main()
