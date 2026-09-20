"""Media sentinel: every missing image is a diagnosed fact, never a shrug.

Locks brief-media-v2: fine-grained failure classification, the publisher
feed-media fallback (og:image first, the feed's own enclosure/media:content
second - never stock, never invented), the retry policy that protects the
wallet (permanent dead ends, capped bot walls, backed-off transients), the
v1 manifest migration, and the machine-room health ledger.
"""
from __future__ import annotations

import hashlib
import json
import socket
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import harness  # noqa: F401 - puts scripts/ on sys.path

import fetch_brief_media as fbm
import fetch_media
import resident_brief as brief

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
OG_HTML = '<meta property="og:image" content="https://cdn.example/a.jpg">'
ART = "https://news.example/a"

RSS_ENCLOSURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>F</title>
<item><title>A</title><link>https://news.example/a</link>
<enclosure url="https://cdn.example/feed-a.jpg" type="image/jpeg" length="1000"/></item>
<item><title>C</title><link>https://news.example/c/</link>
<enclosure url="https://cdn.example/c.jpg" type="image/jpeg"/></item>
<item><title>P</title><link>https://news.example/pod</link>
<enclosure url="https://cdn.example/pod.mp3" type="audio/mpeg"/></item>
</channel></rss>"""

RSS_MRSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel>
<item><link>https://news.example/m</link>
<media:content url="https://cdn.example/m.jpg" medium="image"/></item>
<item><link>https://news.example/t</link>
<media:thumbnail url="https://cdn.example/t.jpg"/></item>
<item><link>https://news.example/n</link>
<media:content url="https://cdn.example/n.mp4" medium="video"/></item>
</channel></rss>"""

ATOM_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">
<entry><title>A</title><link rel="alternate" href="https://news.example/atom"/>
<media:content url="https://cdn.example/atom.jpg" type="image/jpeg"/></entry>
</feed>"""


def write_feed(raw: Path, source: str, xml: str,
               stamp: str = "20260919T000000Z_abcdef012345") -> Path:
    d = raw / source
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{stamp}.xml"
    path.write_text(xml, encoding="utf-8")
    return path


def failing_html(reason: str, detail: str = "diagnosed"):
    """fetch_html stand-in that classifies like the real diagnosed fetch."""
    def _fail(url, *, retries=2, diag=None):
        if isinstance(diag, dict):
            diag.update(reason=reason, detail=detail)
        return None
    return _fail


def failing_image(reason: str, detail: str = "diagnosed"):
    def _fail(url, referer="", *, retries=2, diag=None):
        if isinstance(diag, dict):
            diag.update(reason=reason, detail=detail)
        return None
    return _fail


class HtmlDiagnosis(unittest.TestCase):
    """fetch_html fills diag with the failure class - the investigation step."""

    def run_html(self, *, exc=None, body=b"<html></html>", headers=None,
                 url=ART, guard_error=None):
        diag: dict = {}
        opener = mock.Mock()
        resp = mock.MagicMock()
        resp.headers = headers or {}
        resp.read.return_value = body
        resp.__enter__.return_value = resp
        if exc is not None:
            opener.open.side_effect = exc
        else:
            opener.open.return_value = resp
        guard = mock.Mock(side_effect=guard_error) if guard_error else mock.Mock(side_effect=lambda u, resolve=False: u)
        with mock.patch.object(fetch_media, "public_http_url", guard), \
                mock.patch.object(fetch_media, "public_opener", return_value=opener):
            html = fetch_media.fetch_html(url, retries=1, diag=diag)
        return html, diag, opener

    def http_error(self, code: int) -> urllib.error.HTTPError:
        return urllib.error.HTTPError(ART, code, "err", {}, None)

    def test_http_codes_are_classified(self) -> None:
        expected = {403: "article_http_403", 404: "article_http_404", 410: "article_http_410",
                    500: "article_http_5xx", 503: "article_http_5xx", 429: "article_http_4xx"}
        for code, reason in expected.items():
            with self.subTest(code=code):
                html, diag, _ = self.run_html(exc=self.http_error(code))
                self.assertIsNone(html)
                self.assertEqual(diag["reason"], reason)
                self.assertEqual(diag["detail"], f"HTTP {code}")

    def test_timeout_is_classified(self) -> None:
        for exc in (TimeoutError("timed out"), urllib.error.URLError(TimeoutError("timed out"))):
            with self.subTest(exc=type(exc).__name__):
                _, diag, _ = self.run_html(exc=exc)
                self.assertEqual(diag["reason"], "article_timeout")

    def test_network_error_is_classified(self) -> None:
        _, diag, _ = self.run_html(exc=urllib.error.URLError(socket.gaierror("dns down")))
        self.assertEqual(diag["reason"], "article_network_error")
        self.assertIn("dns down", diag["detail"])

    def test_guard_rejection_never_opens(self) -> None:
        _, diag, opener = self.run_html(guard_error=ValueError("Local hostnames are not allowed"))
        self.assertEqual(diag["reason"], "article_guard_rejected")
        opener.open.assert_not_called()

    def test_guarded_redirect_failure_is_guard_rejected(self) -> None:
        _, diag, _ = self.run_html(exc=ValueError("Non-public redirect"))
        self.assertEqual(diag["reason"], "article_guard_rejected")

    def test_oversized_content_length_is_too_large(self) -> None:
        _, diag, _ = self.run_html(headers={"Content-Length": "99999999"})
        self.assertEqual(diag["reason"], "article_too_large")
        self.assertIn("99999999", diag["detail"])

    def test_malformed_content_length_still_reads(self) -> None:
        html, diag, _ = self.run_html(headers={"Content-Length": "abc"}, body=b"<html>ok</html>")
        self.assertEqual(html, "<html>ok</html>")
        self.assertEqual(diag, {})

    def test_success_leaves_diag_empty(self) -> None:
        html, diag, _ = self.run_html(body=b"<html>fine</html>")
        self.assertEqual(html, "<html>fine</html>")
        self.assertEqual(diag, {})


class ImageDiagnosis(unittest.TestCase):
    def response(self, body: bytes, headers=None):
        resp = mock.MagicMock()
        resp.headers = headers or {}
        resp.read.return_value = body
        resp.__enter__.return_value = resp
        return resp

    def run_image(self, *, exc=None, body=JPEG, headers=None, url="https://cdn.example/a.jpg"):
        diag: dict = {}
        opener = mock.Mock()
        if exc is not None:
            opener.open.side_effect = exc
        else:
            opener.open.return_value = self.response(body, headers)
        with mock.patch.object(fbm, "public_http_url", side_effect=lambda u, resolve=False: u), \
                mock.patch.object(fbm, "public_opener", return_value=opener):
            got = fbm.fetch_image(url, retries=1, diag=diag)
        return got, diag

    def test_http_403_is_classified(self) -> None:
        got, diag = self.run_image(exc=urllib.error.HTTPError("u", 403, "err", {}, None))
        self.assertIsNone(got)
        self.assertEqual(diag["reason"], "image_http_403")

    def test_oversized_content_length_reports_the_measure(self) -> None:
        got, diag = self.run_image(headers={"Content-Length": "99999999"})
        self.assertIsNone(got)
        self.assertEqual(diag["reason"], "image_too_large")
        self.assertIn("content_length=99999999", diag["detail"])

    def test_oversized_body_reports_the_cap(self) -> None:
        with mock.patch.object(fbm, "MAX_IMAGE_BYTES", 32):
            got, diag = self.run_image(body=b"\xff\xd8\xff" + b"x" * 50)
        self.assertIsNone(got)
        self.assertEqual(diag["reason"], "image_too_large")
        self.assertIn("bytes>32", diag["detail"])

    def test_unsupported_format_reports_declared_type(self) -> None:
        got, diag = self.run_image(body=b"<html>not an image</html>",
                                   headers={"Content-Type": "image/jpeg"})
        self.assertIsNone(got)
        self.assertEqual(diag["reason"], "image_unsupported_format")
        self.assertIn("declared=image/jpeg", diag["detail"])

    def test_guard_rejection_is_classified(self) -> None:
        diag: dict = {}
        with mock.patch.object(fbm, "public_http_url", side_effect=ValueError("private")):
            self.assertIsNone(fbm.fetch_image("http://127.0.0.1/x.jpg", diag=diag))
        self.assertEqual(diag["reason"], "image_guard_rejected")

    def test_timeout_is_classified(self) -> None:
        got, diag = self.run_image(exc=TimeoutError("timed out"))
        self.assertIsNone(got)
        self.assertEqual(diag["reason"], "image_timeout")

    def test_success_keeps_diag_empty(self) -> None:
        got, diag = self.run_image(body=JPEG)
        self.assertEqual(got, (JPEG, "jpg"))
        self.assertEqual(diag, {})


class FeedIndex(unittest.TestCase):
    """The publisher's own feed attachments, read from local raw XML only."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.raw = Path(self._tmp.name) / "raw"
        self.raw.mkdir()

    def test_rss_enclosure_and_trailing_slash_normalization(self) -> None:
        write_feed(self.raw, "src", RSS_ENCLOSURE)
        index = fbm.build_feed_media_index(self.raw)
        self.assertEqual(index["https://news.example/a"]["image"], "https://cdn.example/feed-a.jpg")
        self.assertEqual(index["https://news.example/c"]["image"], "https://cdn.example/c.jpg")
        self.assertNotIn("https://news.example/pod", index)  # audio enclosure

    def test_mrss_content_and_thumbnail(self) -> None:
        write_feed(self.raw, "src", RSS_MRSS)
        index = fbm.build_feed_media_index(self.raw)
        self.assertEqual(index["https://news.example/m"]["image"], "https://cdn.example/m.jpg")
        self.assertEqual(index["https://news.example/t"]["image"], "https://cdn.example/t.jpg")
        self.assertNotIn("https://news.example/n", index)  # video medium

    def test_atom_entry_link_href(self) -> None:
        write_feed(self.raw, "src", ATOM_FEED)
        index = fbm.build_feed_media_index(self.raw)
        self.assertEqual(index["https://news.example/atom"]["image"], "https://cdn.example/atom.jpg")

    def test_mrss_https_namespace_variant(self) -> None:
        # Journal de Québec declares xmlns:media with the https spelling.
        write_feed(self.raw, "jdq", """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="https://search.yahoo.com/mrss/"><channel>
<item><link>https://news.example/j</link>
<media:content url="https://cdn.example/j.jpg?impolicy=crop-resize&amp;width=1440"
 type="image/jpeg" medium="image" width="240" height="135"/></item>
</channel></rss>""")
        index = fbm.build_feed_media_index(self.raw)
        self.assertEqual(index["https://news.example/j"]["image"],
                         "https://cdn.example/j.jpg?impolicy=crop-resize&width=1440")

    def test_photographer_credit_is_captured_when_the_feed_gives_one(self) -> None:
        write_feed(self.raw, "src", """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel>
<item><link>https://news.example/credited</link>
<media:content url="https://cdn.example/p.jpg" medium="image">
<media:credit>Simon Séguin-Bertrand</media:credit></media:content></item>
<item><link>https://news.example/plain</link>
<enclosure url="https://cdn.example/q.jpg" type="image/jpeg"/></item>
</channel></rss>""")
        index = fbm.build_feed_media_index(self.raw)
        self.assertEqual(index["https://news.example/credited"]["credit"], "Simon Séguin-Bertrand")
        self.assertIsNone(index["https://news.example/plain"]["credit"])

    def test_corrupt_and_hostile_xml_are_skipped_not_fatal(self) -> None:
        write_feed(self.raw, "broken", "<rss><channel>")
        write_feed(self.raw, "doctype",
                   '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x "y">]><rss/>')
        write_feed(self.raw, "good", RSS_ENCLOSURE)
        index = fbm.build_feed_media_index(self.raw)
        self.assertIn("https://news.example/a", index)

    def test_private_and_plain_http_images_are_never_indexed(self) -> None:
        write_feed(self.raw, "src", """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><link>https://news.example/evil1</link>
<enclosure url="http://127.0.0.1/evil.jpg" type="image/jpeg"/></item>
<item><link>https://news.example/evil2</link>
<enclosure url="http://cdn.example/insecure.jpg" type="image/jpeg"/></item>
<item><link>https://news.example/evil3</link>
<enclosure url="https://cdn.example/anim.svg" type="image/svg+xml"/></item>
</channel></rss>""")
        self.assertEqual(fbm.build_feed_media_index(self.raw), {})

    def test_relative_links_are_skipped(self) -> None:
        write_feed(self.raw, "src", """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><link>/a</link><enclosure url="https://cdn.example/a.jpg" type="image/jpeg"/></item>
</channel></rss>""")
        self.assertEqual(fbm.build_feed_media_index(self.raw), {})

    def test_latest_snapshot_wins(self) -> None:
        write_feed(self.raw, "src", RSS_ENCLOSURE, stamp="20260918T000000Z_000000000000")
        write_feed(self.raw, "src", RSS_MRSS, stamp="20260919T000000Z_000000000000")
        index = fbm.build_feed_media_index(self.raw)
        self.assertNotIn("https://news.example/a", index)
        self.assertIn("https://news.example/m", index)

    def test_per_source_cap(self) -> None:
        items = "".join(
            f'<item><link>https://news.example/{i}</link>'
            f'<enclosure url="https://cdn.example/{i}.jpg" type="image/jpeg"/></item>'
            for i in range(5))
        write_feed(self.raw, "src", f'<?xml version="1.0"?><rss version="2.0"><channel>{items}</channel></rss>')
        with mock.patch.object(fbm, "FEED_INDEX_PER_SOURCE_CAP", 2):
            self.assertEqual(len(fbm.build_feed_media_index(self.raw)), 2)

    def test_missing_dir_is_empty_index(self) -> None:
        self.assertEqual(fbm.build_feed_media_index(self.raw / "nope"), {})


class SentinelFlow(unittest.TestCase):
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
        self.cand = {"id": "c1", "url": ART}
        self.uid = fbm.brief_uid(ART)
        self.kwargs = dict(media_dir=self.media_dir, manifest_path=self.manifest,
                           raw_dir=self.raw_dir, health_path=self.health)

    def seed(self, entry: dict, method: str = fbm.METHOD) -> None:
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        self.manifest.write_text(json.dumps({"method": method, "media": {self.uid: entry}}),
                                 encoding="utf-8")

    @staticmethod
    def _wrap(obj):
        if isinstance(obj, mock.Mock):
            return obj
        if obj is None:
            return mock.Mock(return_value=None)
        return mock.Mock(side_effect=obj)

    def _run(self, html, image, cands):
        with mock.patch.object(fbm.fetch_media, "fetch_html", self._wrap(html)) as fh, \
                mock.patch.object(fbm, "fetch_image", self._wrap(image)) as fi:
            doc = fbm.update_media(cands or [self.cand], **self.kwargs)
        return doc, fh, fi

    def test_bot_wall_resolved_via_publisher_feed(self) -> None:
        write_feed(self.raw_dir, "jdq", RSS_ENCLOSURE)
        doc, _, fi = self._run(failing_html("article_http_403", "HTTP 403"),
                               mock.Mock(return_value=(JPEG, "jpg")), None)
        entry = doc["media"][self.uid]
        self.assertEqual(entry["file"], hashlib.sha256(JPEG).hexdigest()[:20] + ".jpg")
        self.assertEqual(entry["reason"], "publisher feed media")
        self.assertEqual(entry["image_source"], "feed")
        self.assertEqual(entry["image_url"], "https://cdn.example/feed-a.jpg")
        self.assertEqual(doc["feed_resolved"], 1)
        self.assertEqual(fi.call_args[0][0], "https://cdn.example/feed-a.jpg")

    def test_feed_resolved_image_carries_the_photographer_credit(self) -> None:
        write_feed(self.raw_dir, "src", """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel>
<item><link>https://news.example/a</link>
<media:content url="https://cdn.example/feed-a.jpg" medium="image">
<media:credit>Simon Séguin-Bertrand</media:credit></media:content></item>
</channel></rss>""")
        doc, _, _ = self._run(failing_html("article_http_403", "HTTP 403"),
                              mock.Mock(return_value=(JPEG, "jpg")), None)
        entry = doc["media"][self.uid]
        self.assertEqual(entry["image_source"], "feed")
        self.assertEqual(entry["credit"], "Simon Séguin-Bertrand")

    def test_og_image_never_carries_a_feed_credit(self) -> None:
        # The feed credit belongs to the feed's image; an og:image we cannot
        # credit must never inherit a photographer name (misattribution).
        write_feed(self.raw_dir, "src", """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel>
<item><link>https://news.example/a</link>
<media:content url="https://cdn.example/other.jpg" medium="image">
<media:credit>Quelqu'un</media:credit></media:content></item>
</channel></rss>""")
        doc, _, _ = self._run(mock.Mock(return_value=OG_HTML),
                              mock.Mock(return_value=(JPEG, "jpg")), None)
        entry = doc["media"][self.uid]
        self.assertEqual(entry["image_source"], "og:image")
        self.assertNotIn("credit", entry)

    def test_silent_page_with_feed_image_uses_the_feed(self) -> None:
        write_feed(self.raw_dir, "src", RSS_ENCLOSURE)
        doc, _, _ = self._run(mock.Mock(return_value="<html>no meta</html>"),
                              mock.Mock(return_value=(JPEG, "jpg")), None)
        entry = doc["media"][self.uid]
        self.assertEqual(entry["reason"], "publisher feed media")
        self.assertEqual(entry["image_source"], "feed")

    def test_quiet_publisher_then_feed_image_skips_the_html_round_trip(self) -> None:
        self.seed({"status": "proposed", "file": None, "image_url": None, "article_url": ART,
                   "reason": "no_publisher_image", "attempts": 1})
        write_feed(self.raw_dir, "src", RSS_ENCLOSURE)
        doc, fh, fi = self._run(mock.Mock(return_value=OG_HTML),
                                mock.Mock(return_value=(JPEG, "jpg")), None)
        fh.assert_not_called()
        self.assertEqual(fi.call_count, 1)
        self.assertEqual(doc["media"][self.uid]["image_source"], "feed")

    def test_dead_article_is_permanent_and_never_refetched(self) -> None:
        doc, _, _ = self._run(failing_html("article_http_404", "HTTP 404"), None, None)
        entry = doc["media"][self.uid]
        self.assertTrue(entry["permanent"])
        self.assertEqual(entry["attempts"], 1)
        doc2, fh, _ = self._run(failing_html("article_http_404"), None, None)
        fh.assert_not_called()
        self.assertTrue(doc2["media"][self.uid]["permanent"])

    def test_bot_wall_becomes_permanent_after_two_attempts(self) -> None:
        doc, _, _ = self._run(failing_html("article_http_403", "HTTP 403"), None, None)
        entry = doc["media"][self.uid]
        self.assertNotIn("permanent", entry)
        self.assertEqual(entry["attempts"], 1)
        doc2, _, _ = self._run(failing_html("article_http_403", "HTTP 403"), None, None)
        self.assertTrue(doc2["media"][self.uid]["permanent"])
        doc3, fh, _ = self._run(failing_html("article_http_403"), None, None)
        fh.assert_not_called()

    def test_transient_failures_back_off_after_the_cap(self) -> None:
        self.seed({"status": "proposed", "file": None, "image_url": None, "article_url": ART,
                   "reason": "article_timeout", "attempts": fbm.TRANSIENT_ATTEMPTS_CAP - 1})
        doc, _, _ = self._run(failing_html("article_timeout", "timed out"), None, None)
        entry = doc["media"][self.uid]
        self.assertEqual(entry["attempts"], fbm.TRANSIENT_ATTEMPTS_CAP)
        self.assertIn("retry_after", entry)
        doc2, fh, _ = self._run(failing_html("article_timeout"), None, None)
        fh.assert_not_called()  # gated - the wallet is not retried into the wall
        self.assertEqual(doc2["media"][self.uid]["retry_after"], entry["retry_after"])

    def test_image_too_large_records_the_measure(self) -> None:
        doc, _, _ = self._run(mock.Mock(return_value=OG_HTML),
                              failing_image("image_too_large", "content_length=966977"), None)
        entry = doc["media"][self.uid]
        self.assertEqual(entry["reason"], "image_too_large")
        self.assertIn("966977", entry["last_error"])
        self.assertNotIn("permanent", entry)

    def test_feed_image_urls_are_guarded(self) -> None:
        write_feed(self.raw_dir, "src", """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><link>https://news.example/a</link>
<enclosure url="http://127.0.0.1/evil.jpg" type="image/jpeg"/></item>
</channel></rss>""")
        doc, _, fi = self._run(failing_html("article_http_403"), None, None)
        fi.assert_not_called()
        self.assertEqual(doc["media"][self.uid]["reason"], "article_http_403")

    def test_budget_counts_feed_only_fetches(self) -> None:
        self.seed({"status": "proposed", "file": None, "image_url": None, "article_url": ART,
                   "reason": "no_publisher_image", "attempts": 1})
        write_feed(self.raw_dir, "src", RSS_ENCLOSURE)
        other = {"id": "c2", "url": "https://news.example/z"}
        with mock.patch.object(fbm, "FETCH_CAP", 1), \
                mock.patch.object(fbm.fetch_media, "fetch_html") as fh, \
                mock.patch.object(fbm, "fetch_image", return_value=(JPEG, "jpg")) as fi:
            fbm.update_media([self.cand, other], **self.kwargs)
        self.assertEqual(fi.call_count, 1)
        fh.assert_not_called()


class ManifestMigration(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.object(fbm, "SLEEP", 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        d = Path(self._tmp.name)
        self.media_dir = d / "brief"
        self.media_dir.mkdir(parents=True)
        self.manifest = d / "brief_manifest.json"
        self.raw_dir = d / "raw"
        self.raw_dir.mkdir()
        self.health = d / "health.json"
        self.cand = {"id": "c1", "url": ART}
        self.uid = fbm.brief_uid(ART)
        self.kwargs = dict(media_dir=self.media_dir, manifest_path=self.manifest,
                           raw_dir=self.raw_dir, health_path=self.health)

    def test_v1_images_are_carried_without_refetch(self) -> None:
        name = self.uid + ".jpg"
        (self.media_dir / name).write_bytes(JPEG)
        self.manifest.write_text(json.dumps({
            "method": "brief-media-v1",
            "media": {self.uid: {"status": "proposed", "file": name, "image_url": "https://cdn.example/a.jpg",
                                 "article_url": ART, "reason": "publisher og:image"}},
        }), encoding="utf-8")
        with mock.patch.object(fbm.fetch_media, "fetch_html") as fh, \
                mock.patch.object(fbm, "fetch_image") as fi:
            doc = fbm.update_media([self.cand], **self.kwargs)
        fh.assert_not_called()
        fi.assert_not_called()
        self.assertEqual(doc["method"], fbm.METHOD)
        self.assertEqual(doc["with_image"], 1)
        self.assertEqual(doc["reused"], 1)

    def test_v1_negatives_are_retried_under_v2_policy(self) -> None:
        self.manifest.write_text(json.dumps({
            "method": "brief-media-v1",
            "media": {self.uid: {"status": "proposed", "file": None, "image_url": None,
                                 "article_url": ART, "reason": "fetch_failed"}},
        }), encoding="utf-8")
        with mock.patch.object(fbm.fetch_media, "fetch_html", return_value=OG_HTML), \
                mock.patch.object(fbm, "fetch_image", return_value=(PNG, "png")):
            doc = fbm.update_media([self.cand], **self.kwargs)
        self.assertEqual(doc["with_image"], 1)
        self.assertEqual(doc["media"][self.uid]["reason"], "publisher og:image")


class RendererManifests(unittest.TestCase):
    def load(self, content: str) -> dict:
        with tempfile.TemporaryDirectory() as raw:
            p = Path(raw) / "brief_manifest.json"
            p.write_text(content, encoding="utf-8")
            with mock.patch.object(brief, "MEDIA_MANIFEST", p):
                return brief.load_brief_media()

    def test_v1_and_v2_render_and_foreign_still_does_not(self) -> None:
        uid = "ab" * 10
        for method in ("brief-media-v1", "brief-media-v2"):
            with self.subTest(method=method):
                doc = json.dumps({"method": method, "media": {uid: {"file": uid + ".jpg"}}})
                self.assertEqual(self.load(doc), {uid: {"file": uid + ".jpg", "credit": None}})
        self.assertEqual(self.load('{"method": "other-v9", "media": {}}'), {})


class HealthLedger(unittest.TestCase):
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
        self.cand = {"id": "c1", "url": ART}
        self.kwargs = dict(media_dir=self.media_dir, manifest_path=self.manifest,
                           raw_dir=self.raw_dir, health_path=self.health)

    def run_negative(self, reason="article_http_403"):
        with mock.patch.object(fbm.fetch_media, "fetch_html", failing_html(reason)), \
                mock.patch.object(fbm, "fetch_image", return_value=None):
            return fbm.update_media([self.cand], **self.kwargs)

    def ledger(self) -> dict:
        return json.loads(self.health.read_text(encoding="utf-8"))

    def test_ledger_records_reasons_and_domains(self) -> None:
        doc = self.run_negative()
        led = self.ledger()
        self.assertEqual(led["method"], "media-health-v1")
        self.assertEqual(led["latest"]["missing"], 1)
        self.assertEqual(led["latest"]["reasons"], {"article_http_403": 1})
        self.assertEqual(led["latest"]["by_domain"]["news.example"]["top_reason"], "article_http_403")
        self.assertEqual(led["latest"]["budget_used"], doc["fetched_this_run"])
        self.assertEqual(len(led["history"]), 1)

    def test_history_is_capped(self) -> None:
        self.health.write_text(json.dumps({
            "method": "media-health-v1",
            "history": [{"fetched_at": f"old{i}"} for i in range(fbm.HEALTH_HISTORY_CAP)],
        }), encoding="utf-8")
        self.run_negative()
        led = self.ledger()
        self.assertEqual(len(led["history"]), fbm.HEALTH_HISTORY_CAP)
        self.assertNotIn("old0", [h.get("fetched_at") for h in led["history"]])

    def test_corrupt_ledger_starts_fresh(self) -> None:
        self.health.write_text("garbage", encoding="utf-8")
        self.run_negative()
        self.assertEqual(len(self.ledger()["history"]), 1)

    def test_unwritable_ledger_is_never_fatal(self) -> None:
        blocker = Path(self._tmp.name) / "blocker"
        blocker.write_text("file, not a directory", encoding="utf-8")
        kwargs = dict(self.kwargs, health_path=blocker / "media_health.json")
        with mock.patch.object(fbm.fetch_media, "fetch_html", failing_html("article_timeout")), \
                mock.patch.object(fbm, "fetch_image", return_value=None):
            doc = fbm.update_media([self.cand], **kwargs)  # must not raise
        self.assertEqual(doc["entry_count"], 1)

    def test_offline_run_still_records_the_ledger(self) -> None:
        with mock.patch.object(fbm.fetch_media, "fetch_html") as fh, \
                mock.patch.object(fbm, "fetch_image") as fi:
            fbm.update_media([self.cand], offline=True, **self.kwargs)
        fh.assert_not_called()
        fi.assert_not_called()
        led = self.ledger()
        self.assertEqual(led["latest"]["budget_used"], 0)


if __name__ == "__main__":
    unittest.main()
