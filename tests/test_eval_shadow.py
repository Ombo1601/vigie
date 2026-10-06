"""eval/shadow_live.py: counts only, fail-soft, sticky across replayed editions.

Every headline below is invented; "Zorblax" must never reach the output."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import harness  # noqa: F401  (puts scripts/ on sys.path)

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "eval"
if str(EVAL) not in sys.path:
    sys.path.insert(0, str(EVAL))

import shadow_live  # noqa: E402


def item(i, title, when, lang="fr", institution="radio-canada"):
    return {"id": i, "title": title, "summary": "", "published_at": when, "language": lang,
            "institution": institution}


ED1 = [item("a1", "Incendie Zorblax dans un entrepôt de Limoilou: 60 000 litres menacés", "2026-09-20T08:00:00+00:00"),
       item("a2", "Zorblax warehouse fire in Limoilou threatens 60,000 litres", "2026-09-20T09:00:00+00:00", "en", "cbc"),
       item("b1", "Grève des chauffeurs d'autobus du RTC à Charlesbourg", "2026-09-20T10:00:00+00:00")]
ED2 = ED1 + [item("a3", "Limoilou: l'incendie Zorblax de l'entrepôt est maîtrisé, 60 000 litres sauvés",
                  "2026-09-20T15:00:00+00:00", institution="le-soleil")]


def write(store: Path, name: str, edition: str, items: list[dict]) -> None:
    (store / name).write_text(json.dumps({"normalized_at": edition, "candidates": items}), encoding="utf-8")


class Shadow(unittest.TestCase):
    def _main(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = shadow_live.main(list(argv))
        return code, out.getvalue()

    def test_absent_store_is_fail_soft(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self._main("--data-dir", str(Path(tmp) / "nope"))
            self.assertEqual(code, 0)
            self.assertIn("skipped", out)
            code, out = self._main("--data-dir", tmp)
            self.assertEqual(code, 0)
            self.assertIn("skipped", out)

    def test_current_edition_prints_counts_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp)
            write(store, "latest_candidates.json", "2026-09-20T16:00:00+00:00", ED2)
            code, out = self._main("--data-dir", str(store))
        self.assertEqual(code, 0)
        self.assertNotIn("Zorblax", out)
        self.assertNotIn("Limoilou", out)
        counts = json.loads(out)
        self.assertEqual(counts["items"], 4)
        self.assertGreaterEqual(counts["multi_member_events"], 1)
        self.assertGreaterEqual(counts["bilingual_events"], 1)

    def test_replay_keeps_memberships(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp)
            write(store, "20260920T120000Z_candidates.json", "2026-09-20T12:00:00+00:00", ED1)
            write(store, "20260920T180000Z_candidates.json", "2026-09-20T18:00:00+00:00", ED2)
            code, out = self._main("--data-dir", str(store), "--replay")
        self.assertEqual(code, 0)
        self.assertNotIn("Zorblax", out)
        counts = json.loads(out)
        self.assertEqual(counts["editions"], 2)
        self.assertEqual(counts["unique_items"], 4)
        self.assertEqual(counts["memberships_moved"], 0)
        self.assertEqual(counts["attached"] + counts["minted"], 4)


if __name__ == "__main__":
    unittest.main()
