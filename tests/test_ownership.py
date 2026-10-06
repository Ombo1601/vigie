"""Ownership in the source list (docs/MIGRATION.md step 2, docs/EVENTS.md 7-8).

Falsifiable: every source declares a sourced ownership with an https reference
and a date; classes come from the closed list; Radio-Canada and CBC are one
owner group; Le Journal de Québec is Quebecor; an incomplete or unsourced
declaration reads as `unverified` and claims no owner link. The registry is
still LF and still parsed by the chancellery's own minimal loader. Helper
tests use invented sources only. No network.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import harness

import ingest_rss
import ownership


def _fixture() -> list[dict]:
    """An invented registry: two sister feeds, a shared owner, a broken row."""
    base = {"language": "fr", "ownership_asof": "2026-01-02"}
    return [
        {**base, "id": "alpha-city", "institution": "alpha", "ownership_class": "public_broadcaster",
         "owner_group": "north-public", "ownership_ref": "https://example.org/act"},
        {**base, "id": "alpha-nation", "institution": "alpha", "ownership_class": "public_broadcaster",
         "owner_group": "north-public", "ownership_ref": "https://example.org/act"},
        {**base, "id": "beta-en", "institution": "beta", "language": "en", "ownership_class": "public_broadcaster",
         "owner_group": "north-public", "ownership_ref": "https://example.org/act-en"},
        {**base, "id": "gamma", "institution": "gamma", "ownership_class": "cooperative",
         "owner_group": "gamma-coop", "ownership_ref": "http://example.org/coop"},
        {**base, "id": "delta", "institution": "delta", "ownership_class": UNVERIFIED_CLASS,
         "owner_group": "delta"},
        {**base, "id": "epsilon", "institution": "epsilon", "ownership_class": "billionaire",
         "owner_group": "eps", "ownership_ref": "https://example.org/e"},
    ]


UNVERIFIED_CLASS = ownership.UNVERIFIED


class LiveRegistryDeclaresOwnership(unittest.TestCase):
    """The real sources.yaml: declarations only (ids, classes, URLs), no publisher text."""

    def setUp(self) -> None:
        self.sources = ingest_rss.load_sources(harness.SOURCES)
        self.by_id = {s["id"]: s for s in self.sources}

    def test_registry_stays_lf_and_parses_with_the_existing_loader(self) -> None:
        raw = harness.SOURCES.read_bytes()
        self.assertNotIn(b"\r", raw)
        self.assertGreater(len(self.sources), 0)
        # The loaders the pipeline uses still see the same feeds.
        self.assertTrue(ingest_rss.load_enabled_rss(harness.SOURCES))
        self.assertTrue(ingest_rss.load_enabled_by_type(harness.SOURCES, "wzdx"))

    def test_every_enabled_source_has_class_group_ref_asof(self) -> None:
        enabled = [s for s in self.sources if s.get("enabled") is True]
        self.assertTrue(enabled)
        for src in enabled:
            with self.subTest(source=src["id"]):
                for field in ownership.FIELDS:
                    self.assertTrue(str(src.get(field) or "").strip(), f"missing {field}")
                self.assertIn(src["ownership_class"], ownership.CLASSES)
                self.assertTrue(ownership.is_https_ref(src["ownership_ref"]), src["ownership_ref"])
                self.assertTrue(str(src["ownership_ref"]).startswith("https://"))
                self.assertTrue(ownership.is_iso_date(src["ownership_asof"]), src["ownership_asof"])
                self.assertIn(src.get("language"), ("fr", "en"))

    def test_cut_sources_declare_ownership_too(self) -> None:
        # A cut feed keeps its history; its owner is still a fact about it.
        for src in self.sources:
            with self.subTest(source=src["id"]):
                self.assertIn(src.get("ownership_class"), ownership.CLASSES)
                self.assertIn(src.get("language"), ("fr", "en"))

    def test_registry_validates_clean(self) -> None:
        self.assertEqual(ownership.validate(self.sources), [])

    def test_radio_canada_and_cbc_share_one_group(self) -> None:
        rc = [s for s in self.sources if s.get("institution") == "radio-canada"]
        cbc = [s for s in self.sources if s.get("institution") == "cbc"]
        self.assertTrue(rc and cbc)
        groups = {ownership.owner_group_of(s["id"], self.sources) for s in rc + cbc}
        self.assertEqual(groups, {"cbc-radio-canada"})
        keys = {ownership.independence_key(s["id"], self.sources) for s in rc + cbc}
        self.assertEqual(keys, {"owner:cbc-radio-canada"})
        for s in rc + cbc:
            self.assertEqual(ownership.ownership_class_of(s["id"], self.sources), "public_broadcaster")

    def test_journal_de_quebec_is_quebecor(self) -> None:
        self.assertEqual(ownership.ownership_class_of("journal-de-quebec", self.sources), "quebecor")
        self.assertEqual(ownership.owner_group_of("journal-de-quebec", self.sources), "quebecor")

    def test_officials_are_government_and_media_are_not(self) -> None:
        for src in self.sources:
            with self.subTest(source=src["id"]):
                cls = ownership.ownership_class_of(src["id"], self.sources)
                if src.get("source_kind") == "official":
                    self.assertEqual(cls, "government")
                else:
                    self.assertNotEqual(cls, "government")

    def test_default_registry_is_the_repository_file(self) -> None:
        self.assertEqual(ownership.ownership_class_of("journal-de-quebec"), "quebecor")
        self.assertEqual(ownership.independence_key("radio-canada-quebec"),
                         ownership.independence_key("radio-canada-national"))

    def test_existing_keys_untouched_spot_check(self) -> None:
        # Adding ownership must not move a feed, a flag or a cut.
        self.assertEqual(self.by_id["radio-canada-quebec"]["url"], "https://ici.radio-canada.ca/rss/6104")
        self.assertIs(self.by_id["cbc-montreal"]["enabled"], False)
        self.assertEqual(self.by_id["cbc-montreal"]["cut_at"], "2026-10-06")
        self.assertEqual(self.by_id["hydro-quebec"]["max_items"], 40)


class HelpersOnInventedSources(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _fixture()

    def test_class_and_group_of_a_sound_declaration(self) -> None:
        self.assertEqual(ownership.ownership_class_of("alpha-city", self.fx), "public_broadcaster")
        self.assertEqual(ownership.owner_group_of("alpha-city", self.fx), "north-public")

    def test_shared_owner_is_one_independence_key(self) -> None:
        keys = {ownership.independence_key(sid, self.fx) for sid in ("alpha-city", "alpha-nation", "beta-en")}
        self.assertEqual(keys, {"owner:north-public"})

    def test_unsourced_or_invalid_claims_read_unverified(self) -> None:
        # http (not https) ref, explicit unverified, out-of-enum class: no claim survives.
        for sid in ("gamma", "delta", "epsilon"):
            with self.subTest(source=sid):
                self.assertEqual(ownership.ownership_class_of(sid, self.fx), UNVERIFIED_CLASS)
                self.assertIsNone(ownership.owner_group_of(sid, self.fx))
                self.assertEqual(ownership.independence_key(sid, self.fx), f"institution:{sid}")

    def test_unknown_source_is_its_own_key(self) -> None:
        self.assertEqual(ownership.ownership_class_of("nobody", self.fx), UNVERIFIED_CLASS)
        self.assertIsNone(ownership.owner_group_of("nobody", self.fx))
        self.assertEqual(ownership.independence_key("nobody", self.fx), "source:nobody")

    def test_validate_names_each_problem(self) -> None:
        errors = ownership.validate(self.fx)
        self.assertTrue(any(e.startswith("gamma: ownership_ref") for e in errors), errors)
        self.assertTrue(any(e.startswith("epsilon: ownership_class") for e in errors), errors)
        self.assertFalse(any(e.startswith("delta:") for e in errors), errors)  # honest unverified is valid
        self.assertFalse(any(e.startswith("alpha") for e in errors), errors)
        self.assertEqual(errors, sorted(errors))

    def test_validate_flags_sister_feed_disagreement_and_missing_language(self) -> None:
        fx = self.fx
        fx[1] = {**fx[1], "owner_group": "someone-else"}
        fx[2] = {**fx[2], "language": None}
        errors = ownership.validate(fx)
        self.assertTrue(any("institution alpha: sister feeds" in e for e in errors), errors)
        self.assertTrue(any(e.startswith("beta-en: language") for e in errors), errors)

    def test_validate_flags_one_group_with_two_classes(self) -> None:
        fx = self.fx + [{"id": "zeta", "institution": "zeta", "language": "fr", "ownership_class": "independent",
                         "owner_group": "north-public", "ownership_ref": "https://example.org/z",
                         "ownership_asof": "2026-01-02"}]
        self.assertTrue(any(e.startswith("owner_group north-public") for e in ownership.validate(fx)))

    def test_bad_dates_and_refs(self) -> None:
        self.assertFalse(ownership.is_iso_date("2026-13-01"))
        self.assertFalse(ownership.is_iso_date("06/10/2026"))
        self.assertTrue(ownership.is_iso_date("2026-10-06"))
        self.assertFalse(ownership.is_https_ref("javascript:alert(1)"))
        self.assertFalse(ownership.is_https_ref("https://localhost/x"))
        self.assertFalse(ownership.is_https_ref("https://a.org/x y"))
        self.assertTrue(ownership.is_https_ref("https://a.org/x"))

    def test_declaration_is_deterministic_and_sorted(self) -> None:
        d = ownership.declaration("alpha-city", self.fx)
        self.assertEqual(list(d), sorted(d))
        self.assertEqual(d["ownership_ref"], "https://example.org/act")
        u = ownership.declaration("gamma", self.fx)
        self.assertEqual((u["ownership_class"], u["owner_group"], u["ownership_ref"]), (UNVERIFIED_CLASS, None, None))
        rows = ownership.table(self.fx)
        self.assertEqual([r["institution"] for r in rows], sorted(r["institution"] for r in rows))
        self.assertEqual(rows, ownership.table(list(reversed(self.fx))))


class FailSoft(unittest.TestCase):
    def test_missing_registry_yields_unverified_not_an_exception(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.yaml"
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(ownership.load(missing), {})
            self.assertIn("ownership: could not read", buf.getvalue())

    def test_registry_without_sources_block_is_diagnosed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "sources.yaml"
            bad.write_text("version: 1\n", encoding="utf-8", newline="\n")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(ownership.load(bad), {})
                self.assertEqual(ownership.ownership_class_of("x", ownership.load(bad)), UNVERIFIED_CLASS)

    def test_garbage_inputs(self) -> None:
        for junk in (42, [None, "x", {"no": "id"}], {"a": "not-a-dict"}):
            with self.subTest(junk=junk):
                self.assertEqual(ownership.ownership_class_of("a", junk), UNVERIFIED_CLASS)
                self.assertEqual(ownership.independence_key("a", junk), "source:a")

    def test_cache_follows_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sources.yaml"
            row = ("sources:\n  - id: one\n    institution: one\n    language: fr\n"
                   "    ownership_class: {cls}\n    owner_group: one\n"
                   "    ownership_ref: https://example.org/one\n    ownership_asof: 2026-01-02\n")
            path.write_text(row.format(cls="independent"), encoding="utf-8", newline="\n")
            self.assertEqual(ownership.ownership_class_of("one", ownership.load(path)), "independent")
            path.write_text(row.format(cls="cooperative") + "\n", encoding="utf-8", newline="\n")
            self.assertEqual(ownership.ownership_class_of("one", ownership.load(path)), "cooperative")

    def test_cli_check_is_clean_on_the_registry(self) -> None:
        env = {**os.environ, "PYTHONHASHSEED": "1"}
        out = subprocess.run([sys.executable, "-X", "utf8", str(harness.SCRIPTS / "ownership.py"), "--check"],
                             capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("0 problem(s)", out.stdout)
        env["PYTHONHASHSEED"] = "7"
        again = subprocess.run([sys.executable, "-X", "utf8", str(harness.SCRIPTS / "ownership.py")],
                               capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        self.assertEqual(out.stdout, again.stdout)


if __name__ == "__main__":
    unittest.main()
