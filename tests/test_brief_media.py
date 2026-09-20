"""Brief preview images: publisher og:image only, locally served, never invented.

The front door never makes a reader's browser contact a publisher: images are
fetched at collection time through the guarded RSS network boundary, sniffed
for their true format, and served from our own origin under /media/.
"""
from __future__ import annotations

import functools
import hashlib
import json
import shutil
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import urlopen

import harness  # noqa: F401 - puts scripts/ on sys.path

import fetch_brief_media as fbm
import pipeline
import resident_brief as brief
import serve
import stage_public

from test_resident_brief import NOW, collection, story

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
GIF = b"GIF89a" + b"\x00" * 100
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 100
AVIF = b"\x00\x00\x00\x20ftypavif" + b"\x00" * 100
OG_HTML = '<meta property="og:image" content="https://cdn.example/a.jpg">'


class Sniffing(unittest.TestCase):
    def test_true_format_from_magic_bytes(self) -> None:
        for raw, ext in ((JPEG, "jpg"), (PNG, "png"), (GIF, "gif"), (WEBP, "webp"), (AVIF, "avif")):
            with self.subTest(ext=ext):
                self.assertEqual(fbm.sniff_image(raw), ext)

    def test_html_svg_text_and_garbage_are_never_images(self) -> None:
        payloads = (
            b"<html><body>hi</body></html>",
            b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><script/></svg>',
            b"GIF8xxxx not really a gif",
            b"",
            b"\xff\xd8",  # truncated JPEG magic
        )
        for raw in payloads:
            with self.subTest(raw=raw[:12]):
                self.assertIsNone(fbm.sniff_image(raw))


class FetchImageGuards(unittest.TestCase):
    def response(self, body: bytes, headers: dict | None = None):
        resp = mock.MagicMock()
        resp.headers = headers or {}
        resp.read.return_value = body
        resp.__enter__.return_value = resp
        return resp

    def test_private_target_rejected_before_opener(self) -> None:
        with mock.patch.object(fbm, "public_opener") as opener:
            self.assertIsNone(fbm.fetch_image("http://127.0.0.1/x.jpg"))
            opener.assert_not_called()

    def test_guarded_opener_and_bounded_read(self) -> None:
        opener = mock.Mock()
        opener.open.return_value = self.response(JPEG)
        with mock.patch.object(fbm, "public_http_url") as check, \
                mock.patch.object(fbm, "public_opener", return_value=opener):
            got = fbm.fetch_image("https://cdn.example/x.jpg")
            check.assert_called_once_with("https://cdn.example/x.jpg", resolve=True)
        self.assertEqual(got, (JPEG, "jpg"))
        opener.open.return_value.read.assert_called_once_with(fbm.MAX_IMAGE_BYTES + 1)

    def test_oversized_body_rejected_not_truncated(self) -> None:
        opener = mock.Mock()
        opener.open.return_value = self.response(b"\xff\xd8\xff" + b"x" * 50)
        with mock.patch.object(fbm, "public_http_url"), \
                mock.patch.object(fbm, "public_opener", return_value=opener), \
                mock.patch.object(fbm, "MAX_IMAGE_BYTES", 32):
            self.assertIsNone(fbm.fetch_image("https://cdn.example/x.jpg"))

    def test_content_length_over_cap_rejected_without_reading(self) -> None:
        opener = mock.Mock()
        opener.open.return_value = self.response(b"", headers={"Content-Length": "99999999"})
        with mock.patch.object(fbm, "public_http_url"), \
                mock.patch.object(fbm, "public_opener", return_value=opener):
            self.assertIsNone(fbm.fetch_image("https://cdn.example/x.jpg"))
        opener.open.return_value.read.assert_not_called()

    def test_mislabeled_html_is_not_an_image(self) -> None:
        opener = mock.Mock()
        opener.open.return_value = self.response(
            b"<html>not an image</html>", headers={"Content-Type": "image/jpeg"}
        )
        with mock.patch.object(fbm, "public_http_url"), \
                mock.patch.object(fbm, "public_opener", return_value=opener):
            self.assertIsNone(fbm.fetch_image("https://cdn.example/x.jpg"))

    def test_guarded_redirect_failure_is_not_retried(self) -> None:
        opener = mock.Mock()
        opener.open.side_effect = ValueError("Non-public redirect")
        with mock.patch.object(fbm, "public_http_url"), \
                mock.patch.object(fbm, "public_opener", return_value=opener):
            self.assertIsNone(fbm.fetch_image("https://cdn.example/x.jpg", retries=3))
        self.assertEqual(opener.open.call_count, 1)


class IdentityMatchesTheRenderer(unittest.TestCase):
    def test_uid_equals_prepare_items_uid(self) -> None:
        item = story()
        rows, _ = brief.prepare_items([item], NOW)
        self.assertEqual(fbm.brief_uid(item["url"]), rows[0]["uid"])

    def test_unsafe_urls_have_no_uid(self) -> None:
        for url in ("javascript:alert(1)", "http://127.0.0.1/x", None, 42):
            with self.subTest(url=repr(url)):
                self.assertEqual(fbm.brief_uid(url), "")


class ManifestGuards(unittest.TestCase):
    def load(self, content: str) -> dict:
        with tempfile.TemporaryDirectory() as raw:
            p = Path(raw) / "brief_manifest.json"
            p.write_text(content, encoding="utf-8")
            with mock.patch.object(brief, "MEDIA_MANIFEST", p):
                return brief.load_brief_media()

    def test_only_strict_local_filenames_pass(self) -> None:
        uid = "ab" * 10
        doc = json.dumps({"method": "brief-media-v1", "media": {
            uid: {"file": uid + ".jpg"},
            "cd" * 10: {"file": "../../etc/passwd"},
            "ef" * 10: {"file": "evil.html"},
            "a1" * 10: {"file": ("A1" * 10) + ".JPG"},
            "b2" * 10: {"file": None},
            "0f" * 10: "not-a-dict",
        }})
        self.assertEqual(self.load(doc), {uid: {"file": uid + ".jpg", "credit": None}})

    def test_dimensions_pass_through_the_manifest_loader(self) -> None:
        uid = "ab" * 10
        doc = json.dumps({"method": "brief-media-v2", "media": {
            uid: {"file": uid + ".jpg", "width": 800, "height": 450},
            "cd" * 10: {"file": ("cd" * 10) + ".jpg", "width": "big", "height": None},
        }})
        loaded = self.load(doc)
        self.assertEqual(loaded[uid]["width"], 800)
        self.assertNotIn("width", loaded["cd" * 10])

    def test_credit_passes_only_as_a_bounded_string(self) -> None:
        uid = "ab" * 10
        doc = json.dumps({"method": "brief-media-v2", "media": {
            uid: {"file": uid + ".jpg", "credit": "  Jean Tremblay  "},
            "cd" * 10: {"file": ("cd" * 10) + ".jpg", "credit": 42},
            "ef" * 10: {"file": ("ef" * 10) + ".png", "credit": "x" * 500},
        }})
        loaded = self.load(doc)
        self.assertEqual(loaded[uid]["credit"], "Jean Tremblay")
        self.assertIsNone(loaded["cd" * 10]["credit"])
        self.assertEqual(len(loaded["ef" * 10]["credit"]), 120)

    def test_foreign_corrupt_or_missing_manifest_renders_no_images(self) -> None:
        for content in ('{"method": "other-v9", "media": {}}', "garbage", "[]", ""):
            with self.subTest(content=content[:20]):
                self.assertEqual(self.load(content), {})


class RendererImages(unittest.TestCase):
    def row(self):
        rows, _ = brief.prepare_items([story()], NOW)
        return rows[0]

    def test_image_renders_as_local_lazy_img(self) -> None:
        row = self.row()
        html = brief.article_html(row, 2, [], media={row["uid"]: {"file": row["uid"] + ".jpg", "credit": None}})
        self.assertIn(
            f'<figure class="story-media"><img src="/media/{row["uid"]}.jpg" alt="" loading="lazy" decoding="async">',
            html,
        )
        self.assertIn("<figcaption class=\"media-credit\">Photo : Source locale</figcaption>", html)
        self.assertEqual(html.count("<img"), 1)

    def test_first_story_image_is_prioritised_for_lcp(self) -> None:
        row = self.row()
        html = brief.article_html(
            row, 1, [], media={row["uid"]: {"file": row["uid"] + ".jpg", "credit": None}}, priority=True)
        self.assertIn('loading="eager" fetchpriority="high"', html)
        self.assertNotIn('loading="lazy"', html)

    def test_intrinsic_dimensions_render_on_the_image(self) -> None:
        row = self.row()
        html = brief.article_html(row, 2, [], media={row["uid"]: {
            "file": row["uid"] + ".jpg", "credit": None, "width": 800, "height": 450}})
        self.assertIn('alt="" width="800" height="450" loading="lazy"', html)

    def test_photographer_credit_renders_beside_the_source(self) -> None:
        row = self.row()
        html = brief.article_html(row, 1, [], media={row["uid"]: {
            "file": row["uid"] + ".jpg", "credit": "Simon Séguin-Bertrand"}})
        self.assertIn(
            '<figcaption class="media-credit">Photo : Simon Séguin-Bertrand / Source locale</figcaption>',
            html,
        )

    def test_credit_is_escaped_and_never_invents_markup(self) -> None:
        row = self.row()
        html = brief.article_html(row, 1, [], media={row["uid"]: {
            "file": row["uid"] + ".jpg", "credit": '<script>alert("x")</script>'}})
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_no_media_no_img(self) -> None:
        row = self.row()
        self.assertNotIn("<img", brief.article_html(row, 1, []))
        self.assertNotIn("<img", brief.article_html(row, 1, [], media={}))

    def test_hostile_media_values_never_render(self) -> None:
        row = self.row()
        for bad in ("../../evil.jpg", "https://evil.example/x.jpg", ("x" * 20) + ".svg",
                    ("A" * 20) + ".jpg", None, 42, "",
                    {"file": "../../evil.jpg"}, {"file": ("x" * 20) + ".svg"},
                    {"file": None}, {"file": 42}, "not-a-dict"):
            with self.subTest(bad=repr(bad)):
                html = brief.article_html(row, 1, [], media={row["uid"]: bad})
                self.assertNotIn("<img", html)

    def test_render_brief_injects_media(self) -> None:
        item = story()
        uid = self.row()["uid"]
        page = brief.render_brief([item], NOW.isoformat(), [], collection(), media={uid: uid + ".png"})
        self.assertIn(f"/media/{uid}.png", page)

    def test_render_brief_garbage_media_is_ignored(self) -> None:
        page = brief.render_brief([story()], NOW.isoformat(), [], collection(), media="garbage")
        self.assertIn("<html", page)
        self.assertNotIn("<img", page)

    def test_render_brief_loads_manifest_from_disk(self) -> None:
        item = story()
        uid = self.row()["uid"]
        with tempfile.TemporaryDirectory() as raw:
            p = Path(raw) / "brief_manifest.json"
            p.write_text(json.dumps({
                "method": "brief-media-v1",
                "media": {uid: {"file": uid + ".webp"}},
            }), encoding="utf-8")
            with mock.patch.object(brief, "MEDIA_MANIFEST", p):
                page = brief.render_brief([item], NOW.isoformat(), [], collection())
        self.assertIn(f"/media/{uid}.webp", page)

    def test_privacy_disclosure_covers_images(self) -> None:
        page = brief.render_brief([story()], NOW.isoformat(), [], collection(), media={})
        self.assertIn("og:image", page)
        self.assertIn("ne contacte aucun éditeur", page)
        self.assertIn("aucune image n’est inventée", page)


class UpdateMedia(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.object(fbm, "SLEEP", 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        d = Path(self._tmp.name)
        self.media_dir = d / "brief"
        self.manifest = d / "brief_manifest.json"
        self.raw_dir = d / "raw"
        self.raw_dir.mkdir()
        self.health = d / "media_health.json"
        for name, value in (("RAW_DIR", self.raw_dir), ("HEALTH", self.health)):
            patcher = mock.patch.object(fbm, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.cand = {"id": "c1", "url": "https://news.example/a"}
        self.uid = fbm.brief_uid(self.cand["url"])

    def stored_files(self) -> list[str]:
        return sorted(p.name for p in self.media_dir.iterdir()) if self.media_dir.is_dir() else []

    def test_fetch_stores_sniffed_bytes_and_manifest(self) -> None:
        with mock.patch.object(fbm.fetch_media, "fetch_html", return_value=OG_HTML), \
                mock.patch.object(fbm, "fetch_image", return_value=(JPEG, "jpg")):
            doc = fbm.update_media([self.cand], media_dir=self.media_dir, manifest_path=self.manifest)
        self.assertEqual(doc["with_image"], 1)
        entry = doc["media"][self.uid]
        # Content-addressed: the bytes decide the filename, so a replaced
        # publisher image can never serve a stale cached copy.
        self.assertEqual(entry["file"], hashlib.sha256(JPEG).hexdigest()[:20] + ".jpg")
        self.assertEqual(entry["image_url"], "https://cdn.example/a.jpg")
        self.assertEqual(entry["status"], "proposed")
        self.assertEqual((self.media_dir / entry["file"]).read_bytes(), JPEG)
        stored = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(stored["method"], fbm.METHOD)
        self.assertEqual(stored["media"][self.uid]["file"], entry["file"])

    def test_reused_image_gains_its_dimensions(self) -> None:
        png = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR"
               + (800).to_bytes(4, "big") + (450).to_bytes(4, "big") + b"\x00" * 20)
        self.media_dir.mkdir(parents=True)
        name = self.uid + ".png"
        (self.media_dir / name).write_bytes(png)
        self.manifest.write_text(json.dumps({
            "method": fbm.METHOD,
            "media": {self.uid: {"file": name, "article_url": self.cand["url"]}},
        }), encoding="utf-8")
        with mock.patch.object(fbm.fetch_media, "fetch_html") as fh:
            doc = fbm.update_media([self.cand], media_dir=self.media_dir, manifest_path=self.manifest)
        fh.assert_not_called()
        entry = doc["media"][self.uid]
        self.assertEqual((entry.get("width"), entry.get("height")), (800, 450))

    def test_stored_dimensions_are_measured_from_the_header(self) -> None:
        png = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR"
               + (800).to_bytes(4, "big") + (450).to_bytes(4, "big") + b"\x00" * 20)
        with mock.patch.object(fbm.fetch_media, "fetch_html", return_value=OG_HTML), \
                mock.patch.object(fbm, "fetch_image", return_value=(png, "png")):
            doc = fbm.update_media([self.cand], media_dir=self.media_dir, manifest_path=self.manifest)
        entry = doc["media"][self.uid]
        self.assertEqual((entry.get("width"), entry.get("height")), (800, 450))

    def test_second_run_reuses_without_fetching(self) -> None:
        with mock.patch.object(fbm.fetch_media, "fetch_html", return_value=OG_HTML), \
                mock.patch.object(fbm, "fetch_image", return_value=(JPEG, "jpg")):
            fbm.update_media([self.cand], media_dir=self.media_dir, manifest_path=self.manifest)
        with mock.patch.object(fbm.fetch_media, "fetch_html") as fh, \
                mock.patch.object(fbm, "fetch_image") as fi:
            doc = fbm.update_media([self.cand], media_dir=self.media_dir, manifest_path=self.manifest)
        fh.assert_not_called()
        fi.assert_not_called()
        self.assertEqual(doc["reused"], 1)
        self.assertEqual(doc["fetched_this_run"], 0)

    def test_offline_makes_no_requests(self) -> None:
        with mock.patch.object(fbm.fetch_media, "fetch_html") as fh, \
                mock.patch.object(fbm, "fetch_image") as fi:
            doc = fbm.update_media([self.cand], offline=True,
                                   media_dir=self.media_dir, manifest_path=self.manifest)
        fh.assert_not_called()
        fi.assert_not_called()
        self.assertEqual(doc["with_image"], 0)
        self.assertEqual(doc["media"], {})

    def test_silent_publisher_gets_no_image_not_a_placeholder(self) -> None:
        with mock.patch.object(fbm.fetch_media, "fetch_html", return_value="<html>no meta</html>"), \
                mock.patch.object(fbm, "fetch_image") as fi:
            doc = fbm.update_media([self.cand], media_dir=self.media_dir, manifest_path=self.manifest)
        fi.assert_not_called()
        entry = doc["media"][self.uid]
        self.assertIsNone(entry["file"])
        self.assertEqual(entry["reason"], "no_publisher_image")
        self.assertEqual(doc["with_image"], 0)
        self.assertEqual(self.stored_files(), [])

    def test_rejected_bytes_are_never_stored(self) -> None:
        with mock.patch.object(fbm.fetch_media, "fetch_html", return_value=OG_HTML), \
                mock.patch.object(fbm, "fetch_image", return_value=None):
            doc = fbm.update_media([self.cand], media_dir=self.media_dir, manifest_path=self.manifest)
        self.assertIsNone(doc["media"][self.uid]["file"])
        self.assertEqual(doc["media"][self.uid]["reason"], "image_rejected")
        self.assertEqual(self.stored_files(), [])

    def test_negative_result_is_retried_next_run(self) -> None:
        with mock.patch.object(fbm.fetch_media, "fetch_html", return_value=None):
            doc = fbm.update_media([self.cand], media_dir=self.media_dir, manifest_path=self.manifest)
        self.assertEqual(doc["media"][self.uid]["reason"], "article_fetch_failed")
        with mock.patch.object(fbm.fetch_media, "fetch_html", return_value=OG_HTML), \
                mock.patch.object(fbm, "fetch_image", return_value=(PNG, "png")):
            doc = fbm.update_media([self.cand], media_dir=self.media_dir, manifest_path=self.manifest)
        self.assertEqual(doc["with_image"], 1)
        self.assertEqual(doc["media"][self.uid]["file"], hashlib.sha256(PNG).hexdigest()[:20] + ".png")

    def test_orphans_and_parts_pruned_after_manifest_write(self) -> None:
        self.media_dir.mkdir(parents=True)
        orphan = "f" * 20 + ".png"
        (self.media_dir / orphan).write_bytes(PNG)
        (self.media_dir / ".leftover.part").write_bytes(b"x")
        with mock.patch.object(fbm.fetch_media, "fetch_html", return_value=None):
            doc = fbm.update_media([self.cand], media_dir=self.media_dir, manifest_path=self.manifest)
        self.assertFalse((self.media_dir / orphan).exists())
        self.assertFalse((self.media_dir / ".leftover.part").exists())
        self.assertEqual(doc["pruned"], 2)

    def test_entry_with_missing_file_is_dropped(self) -> None:
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        self.manifest.write_text(json.dumps({
            "method": fbm.METHOD,
            "media": {self.uid: {"file": self.uid + ".jpg", "article_url": "https://news.example/a"}},
        }), encoding="utf-8")
        with mock.patch.object(fbm.fetch_media, "fetch_html") as fh:
            doc = fbm.update_media([self.cand], offline=True,
                                   media_dir=self.media_dir, manifest_path=self.manifest)
        fh.assert_not_called()
        self.assertEqual(doc["media"], {})

    def test_out_of_scope_entries_retired_and_pruned(self) -> None:
        other = "cd" * 10
        self.media_dir.mkdir(parents=True)
        (self.media_dir / f"{other}.jpg").write_bytes(JPEG)
        self.manifest.write_text(json.dumps({
            "method": fbm.METHOD, "media": {other: {"file": f"{other}.jpg"}},
        }), encoding="utf-8")
        with mock.patch.object(fbm.fetch_media, "fetch_html", return_value=None):
            doc = fbm.update_media([self.cand], media_dir=self.media_dir, manifest_path=self.manifest)
        self.assertNotIn(other, doc["media"])
        self.assertFalse((self.media_dir / f"{other}.jpg").exists())

    def test_fetch_budget_is_capped(self) -> None:
        cands = [{"id": f"c{i}", "url": f"https://news.example/{i}"} for i in range(5)]
        with mock.patch.object(fbm, "FETCH_CAP", 2), \
                mock.patch.object(fbm.fetch_media, "fetch_html", return_value=None) as fh:
            fbm.update_media(cands, media_dir=self.media_dir, manifest_path=self.manifest)
        self.assertEqual(fh.call_count, 2)

    def test_foreign_manifest_starts_fresh(self) -> None:
        self.manifest.write_text(json.dumps({
            "method": "other-v9", "media": {"x": {"file": "x.jpg"}},
        }), encoding="utf-8")
        with mock.patch.object(fbm.fetch_media, "fetch_html", return_value=None):
            doc = fbm.update_media([self.cand], media_dir=self.media_dir, manifest_path=self.manifest)
        self.assertEqual(doc["method"], fbm.METHOD)
        self.assertNotIn("x", doc["media"])

    def test_unsafe_and_duplicate_candidates(self) -> None:
        cands = [
            {"id": "bad", "url": "javascript:alert(1)"},
            self.cand,
            dict(self.cand),  # same URL twice must fetch once
        ]
        with mock.patch.object(fbm.fetch_media, "fetch_html", return_value=None) as fh:
            doc = fbm.update_media(cands, media_dir=self.media_dir, manifest_path=self.manifest)
        self.assertEqual(fh.call_count, 1)
        self.assertEqual(doc["scope_count"], 1)


class MediaStaging(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.public = self.root / "public"
        self.public.mkdir()
        self.output = self.root / "deploy" / "public"
        for name in stage_public.METHODS:
            (self.root / name).write_text("Public method", encoding="utf-8")
        self.uid = "ab" * 10
        (self.public / "index.html").write_text(
            f'<img src="/media/{self.uid}.jpg" alt=""><a href="/morning.html">M</a>',
            encoding="utf-8",
        )
        (self.public / "morning.html").write_text("<h1>M</h1>", encoding="utf-8")
        (self.public / "explorer.html").write_text("<h1>Workbench</h1>", encoding="utf-8")
        (self.public / "favicon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
        self.media = self.root / "data" / "media" / "brief"
        self.media.mkdir(parents=True)
        (self.media / f"{self.uid}.jpg").write_bytes(JPEG)

    def test_media_files_are_staged_byte_identical(self) -> None:
        manifest = stage_public.stage(self.root, self.output)
        staged = self.output / "media" / f"{self.uid}.jpg"
        self.assertEqual(staged.read_bytes(), JPEG)
        self.assertIn(f"media/{self.uid}.jpg", manifest["files"])

    def test_missing_media_file_blocks_the_release(self) -> None:
        (self.media / f"{self.uid}.jpg").unlink()
        with self.assertRaisesRegex(ValueError, "missing local target"):
            stage_public.stage(self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_unsupported_media_name_blocks_the_release(self) -> None:
        (self.media / "evil.html").write_text("x", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Unsupported media asset"):
            stage_public.stage(self.root, self.output)

    def test_oversized_media_blocks_the_release(self) -> None:
        with mock.patch.object(stage_public, "MEDIA_MAX_BYTES", 10):
            with self.assertRaisesRegex(ValueError, "Oversized media asset"):
                stage_public.stage(self.root, self.output)

    def test_crashed_part_file_is_skipped_not_fatal(self) -> None:
        (self.media / f".{self.uid}.jpg.part").write_bytes(JPEG)
        manifest = stage_public.stage(self.root, self.output)
        self.assertNotIn(f"media/.{self.uid}.jpg.part", manifest["files"])
        self.assertIn(f"media/{self.uid}.jpg", manifest["files"])

    def test_symlinked_media_file_blocks_the_release(self) -> None:
        try:
            (self.media / ("cd" * 10 + ".jpg")).symlink_to(self.root / "VISION.md")
        except OSError:
            self.skipTest("OS does not permit symlink creation")
        with self.assertRaisesRegex(ValueError, "Unsafe media asset"):
            stage_public.stage(self.root, self.output)

    def test_no_media_dir_stages_clean(self) -> None:
        shutil.rmtree(self.media)
        (self.public / "index.html").write_text('<a href="/morning.html">M</a>', encoding="utf-8")
        stage_public.stage(self.root, self.output)
        self.assertFalse((self.output / "media").exists())


class ServeMedia(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        public = root / "public"
        public.mkdir()
        (public / "index.html").write_text("<h1>Vigie</h1>", encoding="utf-8")
        self.media = root / "media"
        self.media.mkdir()
        self.uid = "ab" * 10
        (self.media / f"{self.uid}.jpg").write_bytes(JPEG)
        handler = functools.partial(serve.VigieHandler, directory=public, methods={}, media=self.media)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"

    def get(self, path: str):
        try:
            with urlopen(self.base + path) as resp:
                return resp.status, resp.headers.get("Content-Type"), resp.read()
        except HTTPError as exc:
            return exc.code, None, b""

    def test_media_served_with_true_type(self) -> None:
        status, ctype, body = self.get(f"/media/{self.uid}.jpg")
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "image/jpeg")
        self.assertEqual(body, JPEG)

    def test_traversal_and_unknown_names_404(self) -> None:
        for path in ("/media/..%2Fsecret.jpg", "/media/evil.html",
                     f"/media/{self.uid}.svg", "/media/nope", "/media/" + "ab" * 10 + ".jpg%00.png"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path)[0], 404)


class ServeWiring(unittest.TestCase):
    """Regression: dev preview must serve the local media store.

    main() used to pass directory=PUBLIC explicitly, which silently disabled
    the /media/ route (media_root is only wired when directory is None), so
    every brief image 404'd in the documented two-terminal dev flow.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.public = root / "public"
        self.public.mkdir()
        (self.public / "index.html").write_text("<h1>Vigie</h1>", encoding="utf-8")
        self.media = root / "media"
        self.media.mkdir()
        self.uid = "ab" * 10
        (self.media / f"{self.uid}.jpg").write_bytes(JPEG)
        self.method = root / "VISION.md"
        self.method.write_text("method", encoding="utf-8")
        for name, value in (("ROOT", root), ("PUBLIC", self.public), ("MEDIA_DIR", self.media),
                            ("METHOD_FILES", {"/VISION.md": self.method})):
            patcher = mock.patch.object(serve, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def serve_with(self, handler) -> str:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return f"http://127.0.0.1:{httpd.server_port}"

    def get(self, base: str, path: str):
        try:
            with urlopen(base + path) as resp:
                return resp.status, resp.headers.get("Content-Type"), resp.read()
        except HTTPError as exc:
            return exc.code, None, b""

    def test_dev_preview_serves_media_and_method_files(self) -> None:
        base = self.serve_with(serve.build_handler(None))
        status, ctype, body = self.get(base, f"/media/{self.uid}.jpg")
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "image/jpeg")
        self.assertEqual(body, JPEG)
        status, ctype, _ = self.get(base, "/VISION.md")
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "text/plain; charset=utf-8")

    def test_staged_directory_serves_its_own_media_only(self) -> None:
        staged = Path(self.temp.name) / "staged"
        (staged / "media").mkdir(parents=True)
        (staged / "index.html").write_text("<h1>S</h1>", encoding="utf-8")
        (staged / "media" / f"{self.uid}.jpg").write_bytes(JPEG)
        base = self.serve_with(serve.build_handler(staged))
        status, _, body = self.get(base, f"/media/{self.uid}.jpg")
        self.assertEqual(status, 200)
        self.assertEqual(body, JPEG)
        # Staged mode is self-contained: no dev method routes, no dev media store.
        self.assertEqual(self.get(base, "/VISION.md")[0], 404)
        (staged / "media" / f"{self.uid}.jpg").unlink()
        self.assertEqual(self.get(base, f"/media/{self.uid}.jpg")[0], 404)


class PipelineWiring(unittest.TestCase):
    def test_media_step_sits_between_cluster_and_rank(self) -> None:
        s = pipeline.SCRIPTS
        self.assertLess(s.index("cluster_issues.py"), s.index("fetch_brief_media.py"))
        self.assertLess(s.index("fetch_brief_media.py"), s.index("rank_display.py"))

    def test_offline_runs_the_media_step_without_network(self) -> None:
        calls: list[tuple] = []
        with mock.patch.object(pipeline, "run", side_effect=lambda name, *extra: calls.append((name, extra))), \
                mock.patch("sys.argv", ["pipeline.py", "--offline"]):
            self.assertEqual(pipeline.main(), 0)
        self.assertIn(("fetch_brief_media.py", ("--offline",)), calls)
        self.assertIn(("ingest_wzdx.py", ("--offline",)), calls)
        self.assertNotIn("ingest_rss.py", [name for name, _ in calls])

    def test_render_only_skips_the_media_fetch(self) -> None:
        calls: list[tuple] = []
        with mock.patch.object(pipeline, "run", side_effect=lambda name, *extra: calls.append((name, extra))), \
                mock.patch("sys.argv", ["pipeline.py", "--render-only"]):
            self.assertEqual(pipeline.main(), 0)
        self.assertEqual([name for name, _ in calls], ["rank_display.py"])

    def test_fetch_brief_media_is_fail_soft(self) -> None:
        # No ranked/enriched stores in a bare temp root: exit 0, keep previous.
        with mock.patch.object(fbm, "load_scope", return_value=[]):
            self.assertEqual(fbm.main([]), 0)
        with mock.patch.object(fbm, "load_scope", return_value=[{"url": "https://news.example/a"}]), \
                mock.patch.object(fbm, "update_media", side_effect=OSError("disk on fire")):
            self.assertEqual(fbm.main([]), 0)


class ImageDimensions(unittest.TestCase):
    """Header-only intrinsic size: reserve layout space without a decoder."""

    def test_png_gif_and_webp_vp8x(self) -> None:
        png = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR"
               + (1200).to_bytes(4, "big") + (630).to_bytes(4, "big"))
        self.assertEqual(fbm.image_dimensions(png), (1200, 630))
        gif = b"GIF89a" + (640).to_bytes(2, "little") + (360).to_bytes(2, "little") + b"\x00" * 8
        self.assertEqual(fbm.image_dimensions(gif), (640, 360))
        vp8x = (b"RIFF" + b"\x00" * 4 + b"WEBP" + b"VP8X" + b"\x00" * 8
                + (99).to_bytes(3, "little") + (49).to_bytes(3, "little"))
        self.assertEqual(fbm.image_dimensions(vp8x), (100, 50))

    def test_unknown_or_truncated_input_is_not_guessed(self) -> None:
        self.assertEqual(fbm.image_dimensions(b""), (None, None))
        self.assertEqual(fbm.image_dimensions(b"not an image at all"), (None, None))
        self.assertEqual(fbm.image_dimensions(b"\xff\xd8\xff\xe0" + b"\x00" * 40), (None, None))


if __name__ == "__main__":
    unittest.main()
