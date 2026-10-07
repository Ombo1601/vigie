"""Atomic store writes (store_io) — locked as the one write law."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import harness  # noqa: F401 - puts scripts/ on sys.path

import store_io


class AtomicWrite(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "store.json"

    def test_writes_content_and_leaves_no_temp(self) -> None:
        store_io.write_json_atomic(self.path, {"a": 1})
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"a": 1})
        self.assertEqual(list(self.dir.glob("*.tmp")), [])

    def test_creates_parent_directories(self) -> None:
        nested = self.dir / "a" / "b" / "store.json"
        store_io.write_text_atomic(nested, "x")
        self.assertEqual(nested.read_text(encoding="utf-8"), "x")

    def test_a_failed_stage_preserves_the_previous_store_and_cleans_up(self) -> None:
        self.path.write_text("old", encoding="utf-8")
        real = Path.write_text

        def failing(self, data, *args, **kwargs):
            if self.name.endswith(".tmp"):
                raise OSError("disk full")
            return real(self, data, *args, **kwargs)

        with mock.patch.object(Path, "write_text", failing):
            with self.assertRaises(OSError):
                store_io.write_text_atomic(self.path, "new")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "old")
        self.assertEqual(list(self.dir.glob("*.tmp")), [])

    def test_replace_refusal_falls_back_to_a_direct_write(self) -> None:
        # Windows: an open reader can block os.replace with PermissionError.
        # The stage must still complete rather than fail the whole chain.
        with mock.patch.object(store_io.os, "replace", side_effect=PermissionError("locked")):
            store_io.write_text_atomic(self.path, "direct")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "direct")
        self.assertEqual(list(self.dir.glob("*.tmp")), [])

    def test_temp_names_are_unique_per_writer(self) -> None:
        # A fixed .tmp name lets two writers publish each other's partial file.
        with mock.patch.object(store_io.uuid, "uuid4", side_effect=[
                mock.Mock(hex="aaaa1111"), mock.Mock(hex="bbbb2222")]):
            store_io.write_text_atomic(self.path, "one")
            store_io.write_text_atomic(self.path, "two")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "two")
        self.assertEqual(list(self.dir.glob("*.tmp")), [])


class LineEndings(unittest.TestCase):
    """House law: text is LF. Python's text mode writes CRLF on Windows (the
    optional local fallback collector); store_io must not, or a Windows-built
    file differs, byte for byte, from the one GitHub Actions builds."""

    TEXT = "ligne un\nligne deux — é\n\nfin\n"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "page.html"

    def assert_lf(self) -> None:
        data = self.path.read_bytes()
        self.assertNotIn(b"\r", data)
        self.assertEqual(data, self.TEXT.encode("utf-8"))

    def test_the_atomic_path_writes_lf(self) -> None:
        store_io.write_text_atomic(self.path, self.TEXT)
        self.assert_lf()

    def test_the_fallback_direct_write_writes_lf(self) -> None:
        with mock.patch.object(store_io.os, "replace", side_effect=PermissionError("locked")):
            store_io.write_text_atomic(self.path, self.TEXT)
        self.assert_lf()

    def test_json_stores_are_lf_and_their_bytes_do_not_depend_on_the_platform(self) -> None:
        doc = {"b": [1, 2], "a": "é"}
        store_io.write_json_atomic(self.path, doc)
        self.assertNotIn(b"\r", self.path.read_bytes())
        self.assertEqual(self.path.read_bytes(),
                         json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8"))

    def test_both_writes_ask_for_lf_explicitly(self) -> None:
        # The behaviour above would also hold on POSIX without the fix: pin
        # the call itself, so the Windows behaviour is tested on every OS.
        seen = []
        real = Path.write_text

        def spy(self, data, *args, **kwargs):
            seen.append(kwargs.get("newline"))
            return real(self, data, *args, **kwargs)

        with mock.patch.object(Path, "write_text", spy), \
                mock.patch.object(store_io.os, "replace", side_effect=PermissionError("locked")):
            store_io.write_text_atomic(self.path, self.TEXT)
        self.assertEqual(seen, ["\n", "\n"])


class DedupWrite(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_identical_body_is_hard_linked_not_copied(self) -> None:
        previous = self.dir / "20260101T000000Z_abcd1234abcd.xml"
        previous.write_bytes(b"<rss>same</rss>")
        target = self.dir / "20260102T000000Z_abcd1234abcd.xml"
        store_io.write_bytes_dedup(target, b"<rss>same</rss>", previous)
        self.assertEqual(target.read_bytes(), b"<rss>same</rss>")
        try:
            self.assertEqual(previous.stat().st_ino, target.stat().st_ino)
        except (OSError, AttributeError):
            pass  # filesystem without inode/link support: content is what matters

    def test_linking_failure_falls_back_to_a_normal_write(self) -> None:
        previous = self.dir / "prev.xml"
        previous.write_bytes(b"same")
        target = self.dir / "new.xml"
        with mock.patch.object(store_io.os, "link", side_effect=OSError("cross-volume")):
            store_io.write_bytes_dedup(target, b"same", previous)
        self.assertEqual(target.read_bytes(), b"same")

    def test_without_previous_it_writes_plainly(self) -> None:
        target = self.dir / "only.xml"
        store_io.write_bytes_dedup(target, b"body")
        self.assertEqual(target.read_bytes(), b"body")


if __name__ == "__main__":
    unittest.main()
