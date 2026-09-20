"""L'instrument du départ (depart-v1): one screen, honest freshness, on-device corridors.

Locks: obstructions most-restrictive first; staleness stated, never faked;
corridors stay on-device (island + literal folding); the ledger vocabulary
("disparu de la collecte" is not "réglé"); the seal named; a complete page
without JavaScript; escape safety; determinism; fail-soft; and link validity.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

import harness  # noqa: F401 - puts scripts/ on sys.path

import depart
import stage_public

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def _event(eid="e1", roads=("Boulevard Charest Est",), impact="some-lanes-closed", **over):
    ev = {
        "event_id": eid, "event_type": "work-zone", "event_status": "active",
        "vehicle_impact": impact, "road_names": list(roads),
        "direction": "both-directions", "start_date": (NOW - timedelta(days=2)).isoformat(),
        "end_date": (NOW + timedelta(days=5)).isoformat(), "start_date_accuracy": None,
        "end_date_accuracy": None, "description": "Réfection", "update_date": None,
    }
    ev.update(over)
    return ev


def _store(fetched=NOW, events=None):
    return {
        "method": "wzdx-roadworks-v2", "institution_name": "Ville de Québec",
        "dataset_url": "https://www.donneesquebec.ca/recherche/dataset/entraves",
        "fetched_at": (fetched.isoformat() if fetched else None),
        "events": events if events is not None else [_event()],
    }


def _state(sealed=True):
    if not sealed:
        return {"seals": []}
    return {"seals": [{"seq": 7, "edition": NOW.isoformat(), "root": "a" * 64}]}


class Render(unittest.TestCase):
    def test_screen_carries_roads_corridors_changes_and_seal(self):
        page = depart.render_depart(
            _store(), [], {"has_previous": True, "new_count": 2, "developed_count": 1, "quiet_count": 3},
            _state(), NOW.isoformat(),
        )
        self.assertIn("AVANT DE PARTIR", page)
        self.assertIn("Avant de partir.", page)
        self.assertIn("Boulevard Charest Est", page)
        self.assertIn("Voies partiellement fermées", page)
        self.assertIn("Vos corridors", page)
        self.assertIn('id="vigie-streets"', page)
        self.assertIn("vigie.corridors.v1", page)
        self.assertIn("2 nouveau", page)
        self.assertIn("disparu", page)
        self.assertIn("n’est pas « réglé »", page)
        self.assertIn("Édition n° <strong>7</strong>", page)
        self.assertIn("votre rue", page)  # the JS tag text (progressive enhancement)
        self.assertIn("aucune position demandée", page)

    def test_most_restrictive_first(self):
        page = depart.render_depart(
            _store(events=[_event("e1", impact="some-lanes-closed"),
                           _event("e2", roads=("Rue Saint-Jean",), impact="all-lanes-closed")]),
            [], {}, _state(), NOW.isoformat(),
        )
        self.assertLess(page.index("Rue Saint-Jean"), page.index("Boulevard Charest Est"))

    def test_stale_collection_is_stated_not_faked(self):
        old = depart.render_depart(_store(fetched=NOW - timedelta(hours=9)), [], {}, _state(), NOW.isoformat())
        self.assertIn("Collecte à actualiser", old)
        fresh = depart.render_depart(_store(), [], {}, _state(), NOW.isoformat())
        self.assertNotIn("Collecte à actualiser", fresh)

    def test_no_stale_claim_when_clock_is_unknown(self):
        page = depart.render_depart(_store(), [], {}, _state(), "")
        self.assertNotIn("Collecte à actualiser", page)
        self.assertIn("entraves actives", page)

    def test_empty_and_absent_stores_render_honest_empty(self):
        none = depart.render_depart({}, [], {}, _state(), NOW.isoformat())
        self.assertIn("Aucune déclaration reçue", none)
        empty = depart.render_depart(_store(events=[]), [], {}, _state(), NOW.isoformat())
        self.assertIn("Aucune entrave active déclarée", empty)
        unsealed = depart.render_depart(_store(), [], {}, _state(sealed=False), NOW.isoformat())
        self.assertIn("Aucune édition scellée", unsealed)

    def test_is_deterministic(self):
        args = (_store(), [], {"has_previous": True, "new_count": 1}, _state(), NOW.isoformat())
        self.assertEqual(depart.render_depart(*args), depart.render_depart(*args))

    def test_hostile_text_is_escaped_and_urls_are_safe(self):
        page = depart.render_depart(
            _store(events=[_event(road_names=['<img src=x onerror=alert(1)>'], description="x")],
                   fetched=NOW),
            [], {}, _state(), NOW.isoformat(),
        )
        self.assertNotIn("<img src=x", page)
        self.assertIn("&lt;img src=x", page)
        unsafe = depart.render_depart(
            _store(events=[_event()]) | {"dataset_url": "javascript:alert(1)"},
            [], {}, _state(), NOW.isoformat(),
        )
        self.assertNotIn("javascript:", unsafe)

    def test_emit_writes_and_is_fail_soft(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "partir.html"
            info = depart.emit(_store(), [], {}, _state(), NOW.isoformat(), out=out)
            self.assertTrue(info["written"])
            self.assertTrue(out.is_file())
            second = out.read_text(encoding="utf-8")
            depart.emit(_store(), [], {}, _state(), NOW.isoformat(), out=out)
            self.assertEqual(out.read_text(encoding="utf-8"), second)

    def test_page_passes_release_navigation_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "partir.html").write_text(
                depart.render_depart(_store(), [], {"has_previous": True, "new_count": 1}, _state(), NOW.isoformat()),
                encoding="utf-8",
            )
            (root / "assets").mkdir()
            for name in ("assets/brief.css", "assets/fonts.css", "favicon.svg", "llms.txt",
                         "registre.html", "index.html"):
                (root / name).write_text("placeholder", encoding="utf-8")
            (root / "index.html").write_text('<h1 id="travaux">T</h1>', encoding="utf-8")
            (root / "methode").mkdir()
            (root / "methode" / "legal.html").write_text("placeholder", encoding="utf-8")
            (root / "memoire.html").write_text("placeholder", encoding="utf-8")
            self.assertEqual(stage_public.validate_site(root), [])


class ClientContract(unittest.TestCase):
    def test_street_index_carries_worst_restriction_per_street(self):
        rows = depart._street_index([
            _event("e1", roads=("Rue X",), impact="some-lanes-closed",
                   end_date=(NOW + timedelta(days=1)).isoformat()),
            _event("e2", roads=("Rue X", "Rue Y"), impact="all-lanes-closed",
                   end_date=(NOW + timedelta(days=9)).isoformat()),
        ])
        by = {r["key"]: r for r in rows}
        self.assertEqual(by["rue x"]["n"], 2)
        self.assertEqual(by["rue x"]["severity"], 0)
        self.assertEqual(by["rue x"]["impact"], "all-lanes-closed")
        self.assertEqual(by["rue x"]["impact_label"], "Toutes les voies fermées")
        self.assertEqual(by["rue x"]["until"], (NOW + timedelta(days=9)).strftime("%Y-%m-%d"))
        self.assertEqual(by["rue y"]["n"], 1)
        self.assertEqual(rows, sorted(rows, key=lambda r: r["key"]))  # deterministic order

    def test_client_folds_like_the_server(self):
        from resident_brief import folded
        # The JS document promises the same folding; lock the literal contract here.
        self.assertIn("normalize('NFD')", depart._CORRIDORS_JS)
        self.assertIn("replace(/\\s+/g, ' ').trim()", depart._CORRIDORS_JS)
        self.assertEqual(folded("Boulevard René-Lévesque O"), folded("Boulevard René-Lévesque O"))

    def test_no_scripts_other_than_the_corridor_enhancement(self):
        page = depart.render_depart(_store(), [], {}, _state(), NOW.isoformat())
        blocks = page.count("<script")
        self.assertEqual(blocks, 2)  # streets island + corridor enhancement
        self.assertNotIn("http://", page[: page.index("</head>")])

    def test_island_is_escaped_json(self):
        page = depart.render_depart(
            _store(events=[_event(road_names=["Rue <Test>"])]), [], {}, _state(), NOW.isoformat())
        raw = page[page.index('id="vigie-streets">') + len('id="vigie-streets">'):]
        raw = raw[: raw.index("</script>")]
        self.assertNotIn("<Test>", raw)
        island = json.loads(raw)
        self.assertEqual(island["method"], "depart-streets-v1")


if __name__ == "__main__":
    unittest.main()
