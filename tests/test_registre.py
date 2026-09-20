"""Le Registre: sealed edition chain, voice register, verification, fail-soft.

Law under test: a seal is deterministic and idempotent on the collection clock;
a later seal never rewrites an earlier one; silence is arithmetic over absence
and never a verdict; the public chain carries identifiers only (no publisher
text); a missing or corrupt store yields no seal, never a crash.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import harness  # noqa: F401 - puts scripts/ on sys.path

import registre


def issue(iid, *, question="Le tramway", spoke=("ville-quebec", "le-soleil"), silent=("gouv-quebec",),
          items=3, official=1, names=None):
    names = names or {"ville-quebec": ("Ville de Québec", "official"), "le-soleil": ("Le Soleil", "media"),
                      "gouv-quebec": ("Gouvernement du Québec", "official"), "cbc": ("CBC", "media")}
    return {
        "issue_id": iid, "question": question, "item_count": items,
        "official_voice_count": official, "sources": list(spoke), "source_count": len(spoke),
        "media_remix": official == 0,
        "tensions": [
            {"institution_id": s, "institution_name": names[s][0], "source_kind": names[s][1],
             "items": [{"candidate_id": f"{iid}-{s}", "title": f"Titre <b>{s}</b>", "url": f"https://exemple.test/{iid}/{s}",
                        "source_name": names[s][0], "institution_id": s, "published_at": "2026-09-10T12:00:00+00:00"}]}
            for s in spoke
        ],
        "silence": {"silent": [
            {"institution_id": s, "institution_name": names[s][0], "source_kind": names[s][1], "feed_ids": [s]}
            for s in silent
        ]},
    }


def payload(issues, *, followed=("ville-quebec", "le-soleil", "gouv-quebec", "cbc"), ledger=None):
    return {
        "clustered_at": "2026-09-10T13:00:00+00:00",
        "chancellery_institutions": list(followed),
        "change_ledger": ledger or {"has_previous": False, "new": [], "developed": [], "quiet": []},
        "issues": issues,
    }


def history(ts):
    return {"method": "dossier-history-v1", "updated_at": ts, "edition_count": 1, "dossiers": {}}


class Primitives(unittest.TestCase):
    def test_canonical_json_is_sorted_compact_utf8(self):
        self.assertEqual(registre.canonical({"b": 1, "a": "é"}), '{"a":"é","b":1}'.encode("utf-8"))

    def test_chain_hash_matches_published_recipe(self):
        rec = {"x": 1}
        leaf = hashlib.sha256(b'{"x":1}').hexdigest()
        self.assertEqual(registre.leaf_of(rec), leaf)
        self.assertEqual(registre.chain_hash("", leaf), hashlib.sha256(leaf.encode()).hexdigest())
        self.assertEqual(registre.chain_hash("ab", leaf), hashlib.sha256(("ab" + leaf).encode()).hexdigest())


class EditionRecord(unittest.TestCase):
    def test_record_carries_ids_and_counts_only(self):
        rec = registre.edition_record(payload([issue("a")]), "2026-09-10T12:30:00+00:00")
        self.assertEqual(rec["edition"], "2026-09-10T12:30:00+00:00")
        self.assertEqual(rec["followed"], ["cbc", "gouv-quebec", "le-soleil", "ville-quebec"])
        self.assertEqual(rec["dossiers"][0]["spoke"], ["le-soleil", "ville-quebec"])
        self.assertEqual(rec["dossiers"][0]["silent"], ["gouv-quebec"])
        dumped = json.dumps(rec, ensure_ascii=False)
        self.assertNotIn("Titre", dumped)          # no publisher title
        self.assertNotIn("exemple.test", dumped)   # no publisher URL
        self.assertNotIn("Le Soleil", dumped)      # names live in the state, not the seal

    def test_attributed_headline_never_enters_the_seal(self):
        iss = issue("a", question="Un titre d’éditeur repris tel quel")
        iss["label_kind"] = "attributed_headline"
        iss["label_source"] = {"source_name": "Le Soleil", "url": "https://exemple.test/a/le-soleil"}
        rec = registre.edition_record(payload([iss]), "2026-09-10T12:30:00+00:00")
        self.assertEqual(rec["dossiers"][0]["question"], "")
        self.assertEqual(rec["dossiers"][0]["label_kind"], "attributed_headline")
        self.assertNotIn("titre d’éditeur", json.dumps(rec, ensure_ascii=False))

    def test_record_is_deterministic_regardless_of_input_order(self):
        a = registre.edition_record(payload([issue("a"), issue("b")]), "2026-09-10T12:30:00+00:00")
        b = registre.edition_record(payload([issue("b"), issue("a")]), "2026-09-10T12:30:00+00:00")
        self.assertEqual(registre.canonical(a), registre.canonical(b))

    def test_missing_edition_or_issues_yield_no_record(self):
        self.assertIsNone(registre.edition_record(payload([issue("a")]), ""))
        self.assertIsNone(registre.edition_record({"issues": "nope"}, "2026-09-10T12:30:00+00:00"))

    def test_edition_key_prefers_collection_clock(self):
        self.assertEqual(registre.edition_key(payload([]), history("2026-09-10T12:30:00Z")), "2026-09-10T12:30:00+00:00")
        self.assertEqual(registre.edition_key(payload([]), {}), "2026-09-10T13:00:00+00:00")
        self.assertEqual(registre.edition_key({}, {}), "")


class Chain(unittest.TestCase):
    def _rec(self, ts, issues=None):
        return registre.edition_record(payload(issues if issues is not None else [issue("a")]), ts)

    def test_append_then_verify(self):
        state = registre.empty_state()
        state, act1 = registre.seal_edition(state, self._rec("2026-09-10T12:00:00+00:00"))
        state, act2 = registre.seal_edition(state, self._rec("2026-09-10T18:00:00+00:00", [issue("a"), issue("b")]))
        self.assertEqual((act1, act2), ("appended", "appended"))
        self.assertEqual([s["seq"] for s in state["seals"]], [1, 2])
        self.assertEqual(state["seals"][0]["prev"], "")
        self.assertEqual(state["seals"][1]["prev"], state["seals"][0]["root"])
        ok, msg = registre.verify_chain(state["seals"])
        self.assertTrue(ok, msg)

    def test_same_edition_replaces_last_seal_byte_identically(self):
        state = registre.empty_state()
        state, _ = registre.seal_edition(state, self._rec("2026-09-10T12:00:00+00:00"))
        before = json.dumps(state["seals"], sort_keys=True)
        state, act = registre.seal_edition(state, self._rec("2026-09-10T12:00:00+00:00"))
        self.assertEqual(act, "replaced")
        self.assertEqual(json.dumps(state["seals"], sort_keys=True), before)
        self.assertEqual(len(state["voice"]), 1)

    def test_older_edition_is_ignored_forward_only(self):
        state = registre.empty_state()
        state, _ = registre.seal_edition(state, self._rec("2026-09-10T18:00:00+00:00"))
        state, act = registre.seal_edition(state, self._rec("2026-09-10T12:00:00+00:00"))
        self.assertEqual(act, "ignored")
        self.assertEqual(len(state["seals"]), 1)

    def test_tampered_record_fails_verification(self):
        state = registre.empty_state()
        state, _ = registre.seal_edition(state, self._rec("2026-09-10T12:00:00+00:00"))
        state, _ = registre.seal_edition(state, self._rec("2026-09-10T18:00:00+00:00"))
        seals = json.loads(json.dumps(state["seals"]))
        seals[0]["record"]["dossiers"][0]["silent"] = []   # erase a recorded silence
        ok, msg = registre.verify_chain(seals)
        self.assertFalse(ok)
        self.assertIn("leaf mismatch", msg)

    def test_public_chain_anchor_allows_partial_verification(self):
        state = registre.empty_state()
        cap = registre.PUBLIC_SEAL_CAP
        for n in range(cap + 3):
            ts = f"2026-01-01T00:{n // 60:02d}:{n % 60:02d}+00:00" if n < 3600 else ""
            state, _ = registre.seal_edition(state, self._rec(ts))
        pub = registre.public_chain(state)
        self.assertEqual(len(pub["seals"]), cap)
        self.assertEqual(pub["size"], cap + 3)
        self.assertEqual(pub["anchor_root"], state["seals"][2]["root"])
        ok, msg = registre.verify_chain(pub["seals"], pub["anchor_root"])
        self.assertTrue(ok, msg)


class Roadworks(unittest.TestCase):
    def rw(self, fetched, ids):
        return {"fetched_at": fetched, "events": [{"event_id": i} for i in ids]}

    def test_one_seal_per_change_of_active_set(self):
        state = registre.empty_state()
        state, a1 = registre.seal_roadworks(state, self.rw("2026-09-10T10:00:00+00:00", ["x", "y"]))
        state, a2 = registre.seal_roadworks(state, self.rw("2026-09-10T11:00:00+00:00", ["y", "x"]))
        state, a3 = registre.seal_roadworks(state, self.rw("2026-09-10T12:00:00+00:00", ["x"]))
        self.assertEqual((a1, a2, a3), ("appended", "unchanged", "appended"))
        self.assertEqual(len(state["travaux"]["seals"]), 2)
        self.assertEqual(state["travaux"]["latest_record"]["active"], ["x"])

    def test_missing_store_is_a_no_op(self):
        state = registre.empty_state()
        state, act = registre.seal_roadworks(state, {})
        self.assertEqual(act, "no-store")
        self.assertEqual(state["travaux"]["seals"], [])


class VoiceRegister(unittest.TestCase):
    def test_streaks_count_consecutive_established_editions_only(self):
        state = registre.empty_state()
        p1 = payload([issue("a", spoke=("ville-quebec", "le-soleil"), silent=("gouv-quebec", "cbc"))])
        p2 = payload([issue("a", spoke=("ville-quebec", "cbc"), silent=("gouv-quebec", "le-soleil"))])
        p3 = payload([])  # no dossier: silence not established, must not count
        p4 = payload([issue("a", spoke=("le-soleil", "cbc"), silent=("gouv-quebec", "ville-quebec"))])
        for n, p in enumerate((p1, p2, p3, p4), start=1):
            for iid, meta in registre.institution_names(p).items():
                state["names"][iid] = meta
            state, _ = registre.seal_edition(state, registre.edition_record(p, f"2026-09-1{n}T12:00:00+00:00"))
        reg = {r["institution_id"]: r for r in registre.institution_register(state)}
        self.assertEqual(reg["gouv-quebec"]["silent_streak"], 3)
        self.assertEqual(reg["gouv-quebec"]["current"], "silent")
        self.assertIsNone(reg["gouv-quebec"]["last_spoke"])
        self.assertEqual(reg["ville-quebec"]["silent_streak"], 1)
        self.assertEqual(reg["ville-quebec"]["last_spoke"], "2026-09-12T12:00:00+00:00")
        self.assertEqual(reg["le-soleil"]["current"], "spoke")
        self.assertEqual(reg["le-soleil"]["editions_spoke"], 2)
        self.assertEqual(reg["le-soleil"]["editions_silent"], 1)
        # officials first, then the longest silence
        order = [r["institution_id"] for r in registre.institution_register(state)]
        self.assertEqual(order[:2], ["gouv-quebec", "ville-quebec"])

    def test_checkpoint_text_shape(self):
        state = registre.empty_state()
        self.assertEqual(registre.checkpoint_text(state).splitlines()[:2], [registre.ORIGIN, "0"])
        state, _ = registre.seal_edition(state, registre.edition_record(payload([issue("a")]), "2026-09-10T12:00:00+00:00"))
        lines = registre.checkpoint_text(state).splitlines()
        self.assertEqual(lines[0], registre.ORIGIN)
        self.assertEqual(lines[1], "1")
        self.assertEqual(lines[2], state["seals"][0]["root"])
        self.assertEqual(lines[4], "edition 2026-09-10T12:00:00+00:00")


class HumanView(unittest.TestCase):
    def test_page_escapes_names_and_links_to_the_machine_files(self):
        state = registre.empty_state()
        p = payload([issue("a", names={"ville-quebec": ("Ville <script>alert(1)</script>", "official"),
                                       "le-soleil": ("Le Soleil", "media"), "gouv-quebec": ("Gouv", "official"),
                                       "cbc": ("CBC", "media")})])
        for iid, meta in registre.institution_names(p).items():
            state["names"][iid] = meta
        state, _ = registre.seal_edition(state, registre.edition_record(p, "2026-09-10T12:00:00+00:00"))
        page = registre.render_registre_html(state)
        self.assertNotIn("<script>", page)
        self.assertIn("Ville alert(1)", page)  # markup stripped from names, words kept, nothing executes
        self.assertIn('<html lang="fr-CA">', page)
        for href in ("/registre/chain.json", "/registre/checkpoint.txt", "/registre/institutions.json", "/methode/registre.html", "/llms.txt"):
            self.assertIn(f'href="{href}"', page)
        self.assertIn(state["seals"][0]["root"], page)
        self.assertIn("n’a pas parlé", page)

    def test_empty_state_still_renders_a_page(self):
        page = registre.render_registre_html(registre.empty_state())
        self.assertIn("Aucune édition scellée", page)


class EmitFailSoft(unittest.TestCase):
    def test_emit_writes_every_artefact_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path, out_dir, out_html = root / "state.json", root / "registre", root / "registre.html"
            p = payload([issue("a")])
            h = history("2026-09-10T12:00:00+00:00")
            rw = {"fetched_at": "2026-09-10T11:00:00+00:00", "events": [{"event_id": "e1"}]}
            s1 = registre.emit(p, rw, history=h, state_path=state_path, out_dir=out_dir, out_html=out_html)
            first = {name: (out_dir / name).read_bytes() for name in ("chain.json", "institutions.json", "travaux.json", "checkpoint.txt")}
            s2 = registre.emit(p, rw, history=h, state_path=state_path, out_dir=out_dir, out_html=out_html)
            self.assertEqual(len(s2["seals"]), 1)
            self.assertEqual(s1["seals"], s2["seals"])
            for name, data in first.items():
                self.assertEqual((out_dir / name).read_bytes(), data, name)
            chain = json.loads((out_dir / "chain.json").read_text(encoding="utf-8"))
            self.assertTrue(registre.verify_chain(chain["seals"], chain["anchor_root"])[0])
            self.assertIn("<html", out_html.read_text(encoding="utf-8"))

    def test_corrupt_state_starts_fresh_and_missing_edition_leaves_chain_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "state.json"
            state_path.write_text("{not json", encoding="utf-8")
            state = registre.emit({}, {}, history={}, state_path=state_path,
                                  out_dir=root / "registre", out_html=root / "registre.html")
            self.assertEqual(state["seals"], [])
            self.assertEqual((root / "registre" / "checkpoint.txt").read_text(encoding="utf-8").splitlines()[1], "0")
            self.assertIn("<html", (root / "registre.html").read_text(encoding="utf-8"))

    def test_verify_cli_accepts_a_downloaded_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registre.emit(payload([issue("a")]), {}, history=history("2026-09-10T12:00:00+00:00"),
                          state_path=root / "s.json", out_dir=root / "registre", out_html=root / "r.html")
            import subprocess
            import sys
            proc = subprocess.run([sys.executable, "-X", "utf8", str(harness.ROOT / "scripts" / "registre.py"),
                                   "--verify", str(root / "registre" / "chain.json")],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
