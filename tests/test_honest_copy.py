"""Honest copy: prose never hard-codes what the data derives, never gives a
measured state a verdict wording, and never promises a cadence the platform
cannot keep.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import harness  # noqa: F401 - puts scripts/ on sys.path

import memoire
import promesse
import registre

ROOT = Path(__file__).resolve().parent.parent
SEAL_RANGE = re.compile(r"(?:seals?|sceaux|sceau)\s+(?:n°\s*)?\d+\s*(?:–|-|à|to)\s*\d+", re.I)


CADENCE = re.compile(r"\b(?:runs?|refresh(?:es)?)\s+(?:\*\*)?(?:every|hourly)|\bevery 6 h\)|\ban hourly `\.github|, hourly\)", re.I)


def _tracked_markdown():
    try:
        out = subprocess.run(["git", "ls-files", "*.md"], cwd=ROOT, capture_output=True,
                             text=True, check=True, timeout=30).stdout.split()
        paths = [ROOT / n for n in out if (ROOT / n).is_file()]
        if paths:
            return sorted(paths)
    except (OSError, subprocess.SubprocessError):
        pass
    return sorted(ROOT.glob("*.md"))  # fallback: the published root list


def _prose_files():
    yield from _tracked_markdown()
    yield from sorted((ROOT / "scripts").glob("*.py"))


class SealRangeIsDerived(unittest.TestCase):
    def test_no_prose_hard_codes_a_seal_range(self):
        for path in _prose_files():
            text = path.read_text(encoding="utf-8")
            for match in SEAL_RANGE.finditer(text):
                # Templates that interpolate the derived range are fine.
                if "{" in match.group(0):
                    continue
                self.fail(f"{path.name}: hard-coded seal range {match.group(0)!r} (derive it from the chain)")

    def test_correction_names_first_seal_with_facts(self):
        def seal(seq, with_facts):
            record = {"collection": {"x": {"items": 1}}} if with_facts else {}
            return {"seq": seq, "record": record or {"dossiers": []}}

        state = {"seals": [seal(1, False), seal(2, False), seal(3, True), seal(4, True)]}
        c = registre.correction_notice(state)
        self.assertEqual((c["affects_seal_min"], c["affects_seal_max"], c["first_seal_with_facts"]), (1, 2, 3))
        self.assertIn("n° 1 à 2", memoire._correction_note(c))
        self.assertIn("n° 3", memoire._correction_note(c))
        self.assertIsNone(registre.correction_notice({"seals": [seal(3, True)]}))

    def test_index_range_comes_from_full_chain_not_public_window(self):
        def seal(seq):
            record = {"collection": {"x": {"items": 1}}} if seq > 48 else {"dossiers": []}
            return {"seq": seq, "edition": "2026-10-01T12:00:00+00:00", "record": record}

        state = {"seals": [seal(n) for n in range(1, 206)]}
        row = {"spoke": [], "published": [], "no_items": [], "collection_gap": [], "not_established": []}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(registre, "voice_row", return_value=row):
            tmp = Path(tmp)
            memoire.emit(state, [], out_index=tmp / "index.html", out_dir=tmp / "m")
            page = (tmp / "index.html").read_text(encoding="utf-8")
        self.assertIn("n° 1 à 48", page)


class NoItemsIsNotAGap(unittest.TestCase):
    def _html(self, row):
        with mock.patch.object(registre, "voice_row", return_value=row):
            return memoire._voices_html({}, {})

    def test_headings_are_distinct_and_from_one_source(self):
        heads = {registre.state_heading_fr(k) for k in registre.VOICE_KEYS}
        self.assertEqual(len(heads), len(registre.VOICE_KEYS))
        self.assertNotEqual(registre.state_heading_fr(registre.STATE_NO_ITEMS),
                            registre.state_heading_fr(registre.STATE_COLLECTION_GAP))

    def test_memoire_does_not_fold_no_items_into_collection_gap(self):
        row = {"spoke": [], "published": [], "no_items": ["a"], "collection_gap": [], "not_established": []}
        html = self._html(row)
        self.assertIn(registre.state_heading_fr(registre.STATE_NO_ITEMS), html)
        self.assertNotIn("<h3>" + registre.state_heading_fr(registre.STATE_COLLECTION_GAP), html)
        row = {"spoke": [], "published": [], "no_items": [], "collection_gap": ["a"], "not_established": []}
        self.assertIn("<h3>" + registre.state_heading_fr(registre.STATE_COLLECTION_GAP), self._html(row))

    def test_memoire_index_counts_them_apart(self):
        seal = {"seq": 1, "edition": "2026-10-01T12:00:00+00:00", "record": {"dossiers": [{"spoke": []}]}}
        row = {"spoke": [], "published": [], "no_items": ["a", "b"], "collection_gap": ["c"], "not_established": []}
        with mock.patch.object(registre, "voice_row", return_value=row):
            page = memoire.render_index([seal], {}, total=1)
        self.assertIn("2 sans article collecté", page)
        self.assertIn("1 collecte manquée", page)


class NeutralWording(unittest.TestCase):
    def test_promesse_statuses_are_measured_presence(self):
        timeline = [{"ts": f"2026-09-1{i}T12:00:00+00:00", "official": 0} for i in range(3)]
        status = promesse.status_of({"tracking": {"timeline": timeline}})
        self.assertEqual(status["status"], "no_official_recorded")
        self.assertNotIn("answer", " ".join(str(k) + str(v) for k, v in status.items()))


class CadenceIsNotPromised(unittest.TestCase):
    def test_docs_do_not_state_a_fixed_cadence(self):
        for name in ("README.md", "AGENTS.md", "TECHNICAL_PROCESS.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            for m in CADENCE.finditer(re.sub(r"\s+", " ", text)):
                self.fail(f"{name}: unqualified cadence {m.group(0)!r}")
            self.assertIn("scheduled about", text, name)


if __name__ == "__main__":
    unittest.main()
