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


# The nine institutions Vigie followed in the 2026-09-24 production edition
# (the CBC desks were cut on 2026-10-06), with their real kinds.
NAMES9 = {
    "ville-quebec": ("Ville de Québec", "official"),
    "gouv-quebec": ("Gouvernement du Québec", "official"),
    "hydro-quebec": ("Hydro-Québec", "official"),
    "cbc": ("CBC", "media"),
    "journal-de-quebec": ("Le Journal de Québec", "media"),
    "la-presse": ("La Presse", "media"),
    "le-devoir": ("Le Devoir", "media"),
    "le-soleil": ("Le Soleil", "media"),
    "radio-canada": ("Radio-Canada", "media"),
}
ALL9 = tuple(sorted(NAMES9))

# The live edition that exposed the defect: two institutions clustered into the
# single dossier, the other seven were reported as having "never spoken" while
# their feeds had returned items successfully.
PROD_SPOKE = ("la-presse", "le-devoir")
PROD_ABSENT = tuple(i for i in ALL9 if i not in PROD_SPOKE)
PROD_ITEMS = {"ville-quebec": 9, "hydro-quebec": 40, "gouv-quebec": 10, "cbc": 39,
              "journal-de-quebec": 43, "le-soleil": 40, "radio-canada": 119,
              "la-presse": 10, "le-devoir": 37}


def prod_issue(iid="a"):
    """One dossier, exactly as clustered in production on 2026-09-24."""
    return issue(iid, spoke=PROD_SPOKE, silent=PROD_ABSENT, names=NAMES9, official=0)


def payload9(issues, ledger=None):
    return payload(issues, followed=ALL9, ledger=ledger)


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

    def test_a_published_root_never_moves_when_the_record_shape_changes(self):
        """The hazard the schema-2 fix introduced: an anchored root must not change.

        Production anchors each root in a git commit. If re-rendering the same
        edition with a richer record recomputed the leaf, the published checkpoint
        would contradict its own witness. A seal is immutable once written.
        """
        edition = "2026-09-10T12:00:00+00:00"
        state = registre.empty_state()
        # Seal as schema 1 (no collection facts), as production already did.
        state, _ = registre.seal_edition(state, self._rec(edition))
        anchored_leaf = state["seals"][-1]["leaf"]
        anchored_root = state["seals"][-1]["root"]
        before = json.dumps(state, sort_keys=True)
        # Re-render the same edition after the fix: the record now carries
        # record_schema + collection, so it recomputes to a different leaf.
        richer = registre.edition_record(
            payload([issue("a")]), edition,
            {"ville-quebec": {"items": 9, "feeds_ok": 1, "feeds_total": 1}},
        )
        self.assertNotEqual(registre.leaf_of(richer), anchored_leaf)
        state, act = registre.seal_edition(state, richer)
        self.assertEqual(act, "diverged-kept")
        self.assertEqual(state["seals"][-1]["leaf"], anchored_leaf)
        self.assertEqual(state["seals"][-1]["root"], anchored_root)
        self.assertEqual(json.dumps(state, sort_keys=True), before, "state must be untouched")
        self.assertNotIn("collection", state["seals"][-1]["record"])
        ok, msg = registre.verify_chain(state["seals"])
        self.assertTrue(ok, msg)
        # The next edition does pick up the richer shape.
        state, act = registre.seal_edition(state, registre.edition_record(
            payload([issue("a")]), "2026-09-10T18:00:00+00:00",
            {"ville-quebec": {"items": 9, "feeds_ok": 1, "feeds_total": 1}}))
        self.assertEqual(act, "appended")
        self.assertEqual(state["seals"][-1]["record"]["record_schema"], registre.RECORD_SCHEMA)
        self.assertEqual(state["seals"][-1]["prev"], anchored_root)

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


def collection(items, *, feeds_ok=None, feeds_total=1):
    """Collection facts as sealed at schema 2: {iid: {items, feeds_ok, feeds_total}}."""
    return {
        iid: {"items": n, "feeds_ok": feeds_total if feeds_ok is None else feeds_ok,
              "feeds_total": feeds_total}
        for iid, n in items.items()
    }


class VoiceRegister(unittest.TestCase):
    """The corrected contract: our clustering must never read as their silence."""

    def _seal(self, state, edition, issues, coll):
        p = payload9(issues)
        for iid, meta in registre.institution_names(p).items():
            state["names"][iid] = meta
        state, _ = registre.seal_edition(state, registre.edition_record(p, edition, coll))
        return state

    def test_collected_items_can_never_read_as_silence(self):
        """The regression that shipped to production: Hydro/City published, register said silent."""
        state = registre.empty_state()
        # Two institutions cluster into the single dossier, exactly as in the live
        # 2026-09-24 edition -- but all seven others DID publish, successfully.
        coll = collection(PROD_ITEMS)
        state = self._seal(state, "2026-09-24T12:00:00+00:00", [prod_issue()], coll)
        reg = {r["institution_id"]: r for r in registre.institution_register(state)}
        for iid in PROD_ABSENT:
            self.assertEqual(reg[iid]["current"], registre.STATE_PUBLISHED, iid)
            self.assertEqual(reg[iid]["items_collected"], PROD_ITEMS[iid], iid)
            self.assertEqual(reg[iid]["collection_gap_streak"], 0, iid)
            self.assertEqual(reg[iid]["editions_published_outside_dossiers"], 1, iid)
        for iid in PROD_SPOKE:
            self.assertEqual(reg[iid]["current"], registre.STATE_SPOKE, iid)
        # The two institutions the live registre called eternally silent:
        self.assertEqual(reg["hydro-quebec"]["editions_spoke"], 0)
        self.assertEqual(reg["hydro-quebec"]["current"], registre.STATE_PUBLISHED)
        # The old vocabulary must be gone entirely: no consumer can resurrect it.
        for row in registre.institution_register(state):
            self.assertNotIn("silent_streak", row)
            self.assertNotIn("editions_silent", row)
            self.assertNotEqual(row["current"], "silent")

    def test_failed_feed_is_our_gap_not_their_silence(self):
        state = registre.empty_state()
        ok = collection(PROD_ITEMS)
        broken = {k: dict(v) for k, v in ok.items()}
        broken["ville-quebec"] = {"items": 0, "feeds_ok": 0, "feeds_total": 1}
        state = self._seal(state, "2026-09-10T12:00:00+00:00", [prod_issue()], ok)
        state = self._seal(state, "2026-09-11T12:00:00+00:00", [prod_issue()], broken)
        state = self._seal(state, "2026-09-12T12:00:00+00:00", [prod_issue()], broken)
        reg = {r["institution_id"]: r for r in registre.institution_register(state)}
        self.assertEqual(reg["ville-quebec"]["current"], registre.STATE_COLLECTION_GAP)
        self.assertEqual(reg["ville-quebec"]["collection_gap_streak"], 2)
        self.assertEqual(reg["ville-quebec"]["editions_collection_gap"], 2)
        self.assertEqual(reg["hydro-quebec"]["current"], registre.STATE_PUBLISHED)
        self.assertEqual(reg["hydro-quebec"]["collection_gap_streak"], 0)
        # Our failures sort first: the register is self-critical before accusatory.
        self.assertEqual(registre.institution_register(state)[0]["institution_id"], "ville-quebec")

    def test_answered_feed_with_no_items_is_not_a_gap(self):
        state = registre.empty_state()
        coll = collection(PROD_ITEMS)
        coll["ville-quebec"] = {"items": 0, "feeds_ok": 1, "feeds_total": 1}
        state = self._seal(state, "2026-09-10T12:00:00+00:00", [prod_issue()], coll)
        reg = {r["institution_id"]: r for r in registre.institution_register(state)}
        self.assertEqual(reg["ville-quebec"]["current"], registre.STATE_NO_ITEMS)
        self.assertEqual(reg["ville-quebec"]["collection_gap_streak"], 0)

    def test_schema_one_seal_makes_no_claim(self):
        """Seals published before the correction stay 'not established', never silent."""
        state = registre.empty_state()
        p = payload([issue("a")])
        for iid, meta in registre.institution_names(p).items():
            state["names"][iid] = meta
        state, _ = registre.seal_edition(state, registre.edition_record(p, "2026-09-10T12:00:00+00:00"))
        row = registre.voice_row(state["seals"][-1]["record"])
        self.assertEqual(row["not_established"], ["cbc", "gouv-quebec"])
        self.assertNotIn("silent", row)
        reg = {r["institution_id"]: r for r in registre.institution_register(state)}
        self.assertEqual(reg["gouv-quebec"]["current"], registre.STATE_NOT_ESTABLISHED)
        self.assertIsNone(reg["gouv-quebec"]["items_collected"])

    def test_correction_names_the_affected_seals_from_the_chain(self):
        state = registre.empty_state()
        p = payload([issue("a")])
        for iid, meta in registre.institution_names(p).items():
            state["names"][iid] = meta
        state, _ = registre.seal_edition(state, registre.edition_record(p, "2026-09-10T12:00:00+00:00"))
        state, _ = registre.seal_edition(state, registre.edition_record(p, "2026-09-11T12:00:00+00:00"))
        state, _ = registre.seal_edition(
            state,
            registre.edition_record(p, "2026-09-12T12:00:00+00:00",
                                    collection({"ville-quebec": 9, "le-soleil": 8, "gouv-quebec": 4, "cbc": 6})),
        )
        c = registre.correction_notice(state)
        self.assertEqual((c["affects_seal_min"], c["affects_seal_max"], c["affects_count"]), (1, 2, 2))
        self.assertEqual(c["corrected_at"], registre.CORRECTION_DATE)
        # Once every seal carries facts, the correction retires itself.
        state["seals"] = state["seals"][-1:]
        state["voice"] = state["voice"][-1:]
        self.assertIsNone(registre.correction_notice(state))

    def test_voice_row_lists_partition_the_followed_institutions(self):
        state = registre.empty_state()
        coll = collection(PROD_ITEMS)
        coll["ville-quebec"] = {"items": 0, "feeds_ok": 1, "feeds_total": 1}
        coll["cbc"] = {"items": 0, "feeds_ok": 0, "feeds_total": 2}
        state = self._seal(state, "2026-09-10T12:00:00+00:00", [prod_issue()], coll)
        row = registre.voice_row(state["seals"][-1]["record"])
        buckets = [row[k] for k in registre.VOICE_KEYS]
        flat = [i for b in buckets for i in b]
        self.assertEqual(len(flat), len(set(flat)), "an institution must occupy exactly one state")
        self.assertEqual(sorted(flat), list(ALL9), "every followed institution is classified exactly once")
        self.assertEqual(row["not_established"], [])
        self.assertEqual(row["collection_gap"], ["cbc"])
        self.assertEqual(row["no_items"], ["ville-quebec"])
        self.assertEqual(sorted(row["spoke"]), list(PROD_SPOKE))
        self.assertEqual(row["absent_from_dossiers"], list(PROD_ABSENT))

    def test_collection_facts_are_rejected_for_a_mismatched_edition(self):
        """A seal must never borrow another edition's counts."""
        enriched = {"normalized_at": "2026-09-11T12:00:00+00:00",
                    "source_status": {"ville-quebec": {"status": "ok", "candidate_count": 9}}}
        self.assertEqual(registre.collection_for_edition("2026-09-10T12:00:00+00:00", enriched), {})
        self.assertEqual(registre.collection_for_edition("", enriched), {})
        self.assertEqual(registre.collection_for_edition("2026-09-11T12:00:00+00:00", {}), {})

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


class LegacyStateMigration(unittest.TestCase):
    """Production already holds 40 old-shape voice rows; they must not vanish."""

    FOLLOWED = ["cbc", "gouv-quebec", "hydro-quebec", "journal-de-quebec", "la-presse",
                "le-devoir", "le-soleil", "radio-canada", "ville-quebec"]

    def _legacy_state(self, editions=3):
        spoke = ["la-presse", "le-devoir"]
        silent = [i for i in self.FOLLOWED if i not in spoke]
        seals, voice = [], []
        for n in range(1, editions + 1):
            edition = f"2026-09-{n:02d}T12:00:00+00:00"
            rec = {"method": registre.METHOD, "edition": edition, "followed": self.FOLLOWED,
                   "dossiers": [{"issue_id": "a", "label_kind": "subject_label", "question": "Q",
                                 "item_count": 3, "official_voice_count": 0,
                                 "spoke": spoke, "silent": silent}],
                   "ledger": {"has_previous": n > 1, "new": [], "developed": [], "quiet": []}}
            leaf = registre.leaf_of(rec)
            prev = seals[-1]["root"] if seals else ""
            seals.append({"seq": n, "edition": edition, "prev": prev, "leaf": leaf,
                          "root": registre.chain_hash(prev, leaf), "record": rec})
            voice.append({"edition": edition, "spoke": spoke, "silent": silent, "established": True})
        return {"method": registre.METHOD, "origin": registre.ORIGIN, "seals": seals, "voice": voice,
                "names": {i: {"name": i, "kind": "official" if i in ("ville-quebec", "gouv-quebec", "hydro-quebec") else "media"}
                          for i in self.FOLLOWED},
                "travaux": {"method": registre.ROADS_METHOD, "seals": [], "latest_record": None}}

    def test_old_silent_rows_become_not_established_not_vanished(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registre.json"
            path.write_text(json.dumps(self._legacy_state()), encoding="utf-8")
            state = registre.load_state(path)
            self.assertNotIn("silent", state["voice"][0])
            self.assertEqual(state["voice"][0]["not_established"],
                             [i for i in self.FOLLOWED if i not in ("la-presse", "le-devoir")])
            reg = {r["institution_id"]: r for r in registre.institution_register(state)}
            # The regression: without migration, 7 of 9 institutions disappear.
            self.assertEqual(len(reg), len(self.FOLLOWED))
            self.assertEqual(reg["hydro-quebec"]["editions_not_established"], 3)
            self.assertEqual(reg["hydro-quebec"]["current"], registre.STATE_NOT_ESTABLISHED)
            self.assertEqual(reg["hydro-quebec"]["editions_spoke"], 0)
            self.assertEqual(reg["la-presse"]["editions_spoke"], 3)
            self.assertIsNone(reg["ville-quebec"]["items_collected"])
            ok, msg = registre.verify_chain(state["seals"])
            self.assertTrue(ok, msg)

    def test_migration_is_idempotent_and_leaves_new_rows_alone(self):
        row = {"edition": "2026-09-01T12:00:00+00:00", "spoke": ["a"], "published": ["b"],
               "no_items": [], "collection_gap": [], "not_established": [], "established": True}
        self.assertEqual(registre.migrate_voice_row(row), row)
        legacy = {"edition": "2026-09-01T12:00:00+00:00", "spoke": ["a"], "silent": ["b", "b"],
                  "established": True}
        once = registre.migrate_voice_row(legacy)
        self.assertEqual(registre.migrate_voice_row(once), once)
        self.assertEqual(once["not_established"], ["b"])

    def test_correction_covers_the_legacy_seals_after_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registre.json"
            path.write_text(json.dumps(self._legacy_state()), encoding="utf-8")
            state = registre.load_state(path)
            c = registre.correction_notice(state)
            self.assertEqual((c["affects_seal_min"], c["affects_seal_max"], c["affects_count"]), (1, 3, 3))
            # A schema-1 page must still render, and must not accuse anyone.
            page = registre.render_registre_html(state)
            self.assertIn("CORRECTION PUBLIÉE", page)
            self.assertIn("non établi", page)
            self.assertNotIn("n’a pas parlé depuis", page)


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


# --------------------------------------------------------------------------- #
# Golden: the edition chain and its public files are frozen byte for byte
# --------------------------------------------------------------------------- #
# Digests of what scripts/registre.py produced for the fixture below BEFORE the
# event chain existed (phase1/base, 2026-10-06), line endings normalised to LF
# (store_io writes text mode, so Windows writes CRLF). Any change to these is a
# change to every future seal and public file: it must be a deliberate,
# announced method change, never a side effect.
GOLDEN_FILES = {
    "chain.json": "a304ec427809e163bba287e2b8c13370f5e19b87c5dd135a2976af1fe9847f63",
    "checkpoint.txt": "906e3af7abd794d27806d31355f7dc2129c35d5eb9bac8ad1304e94e34d5c00c",
    "travaux.json": "6fbabfdee2bf8dad28e75801b01026898bff8a0c875ed21a38eb95b543470826",
}
GOLDEN_STATE = {   # sha256 of registre.canonical(state[key])
    "seals": "b69c925b3c609bc95a369acf5e45077b8f1981a33e76eae8091eaea4e061fa25",
    "voice": "d1a029ca98050e126fd2f62e2263f8f79c2d9292c15df306a9bba23c34512c76",
    "travaux": "ff2ebe6da00a2fd3436c741af35c22aab028825198e3c4cec2d48eb45765c5c8",
    "names": "722bbcba39887cafa2a7648effabf8b1fe4875ebe3744961259db4118a2c4d68",
}
GOLDEN_ROOT = "0d21c0ea2aca360ed16b8b9dd84739f901f2d6f08e07d1ae061d37c91dc7a638"
GOLDEN_FOLLOWED = ["cbc", "gouv-quebec", "le-soleil", "ville-quebec"]


def golden_issue(iid, question, spoke, silent):
    return {
        "issue_id": iid, "question": question, "item_count": 2 + len(spoke),
        "official_voice_count": sum(1 for s in spoke if s in ("ville-quebec", "gouv-quebec")),
        "sources": list(spoke), "label_kind": "subject_label",
        "tensions": [{"institution_id": s, "institution_name": s.upper(), "source_kind": "media",
                      "items": [{"title": f"Titre inventé {iid} {s}", "url": f"https://exemple.test/{iid}/{s}"}]}
                     for s in spoke],
        "silence": {"silent": [{"institution_id": s, "institution_name": s.upper(), "source_kind": "official"}
                               for s in silent]},
    }


def golden_payload(issues, ledger):
    return {"clustered_at": "2026-09-01T00:00:00+00:00", "chancellery_institutions": GOLDEN_FOLLOWED,
            "change_ledger": ledger, "issues": issues}


def golden_seed_state():
    """Three schema-1 seals and one roadworks seal, as an older production state holds them."""
    seals = []
    for n in range(1, 4):
        edition = f"2026-09-0{n}T12:00:00+00:00"
        rec = {"method": registre.METHOD, "edition": edition, "followed": GOLDEN_FOLLOWED,
               "dossiers": [{"issue_id": f"d{n}", "label_kind": "subject_label", "question": f"Sujet {n}",
                             "item_count": 3, "official_voice_count": 1,
                             "spoke": ["le-soleil", "ville-quebec"], "silent": ["cbc", "gouv-quebec"]}],
               "ledger": {"has_previous": n > 1, "new": [f"d{n}"], "developed": [], "quiet": []}}
        leaf = registre.leaf_of(rec)
        prev = seals[-1]["root"] if seals else ""
        seals.append({"seq": n, "edition": edition, "prev": prev, "leaf": leaf,
                      "root": registre.chain_hash(prev, leaf), "record": rec})
    trav_rec = {"method": registre.ROADS_METHOD, "fetched_at": "2026-09-03T11:00:00+00:00", "active": ["w0"]}
    trav_leaf = registre.leaf_of(trav_rec)
    return {"method": registre.METHOD, "origin": registre.ORIGIN, "seals": seals,
            "voice": [registre.voice_row(s["record"]) for s in seals],
            "names": {i: {"name": i.upper(), "kind": "media"} for i in GOLDEN_FOLLOWED},
            "travaux": {"method": registre.ROADS_METHOD, "latest_record": trav_rec,
                        "seals": [{"seq": 1, "fetched_at": trav_rec["fetched_at"], "prev": "",
                                   "leaf": trav_leaf, "root": registre.chain_hash("", trav_leaf),
                                   "signal": registre.digest(registre.canonical(["w0"])),
                                   "active_count": 1}]}}


def golden_steps():
    """(payload, history, roadworks, collection) of each render: append, re-render,
    append with collection facts, re-render with diverging facts (kept)."""
    led4 = {"has_previous": True, "new": [{"issue_id": "d4b"}], "developed": [{"issue_id": "d4a"}],
            "quiet": [{"issue_id": "d3"}]}
    p4 = golden_payload([golden_issue("d4b", "Le pont", ["le-soleil", "cbc"], ["ville-quebec", "gouv-quebec"]),
                         golden_issue("d4a", "Le tramway", ["ville-quebec", "le-soleil"], ["cbc", "gouv-quebec"])],
                        led4)
    h4 = {"method": "dossier-history-v1", "updated_at": "2026-09-04T12:00:00Z"}
    rw4 = {"fetched_at": "2026-09-04T11:30:00+00:00", "events": [{"event_id": "w2"}, {"event_id": "w1"}]}
    led5 = {"has_previous": True, "new": [], "developed": [{"issue_id": "d4b"}], "quiet": [{"issue_id": "d4a"}]}
    p5 = golden_payload([golden_issue("d4b", "Le pont", ["le-soleil", "cbc", "gouv-quebec"], ["ville-quebec"])], led5)
    h5 = {"method": "dossier-history-v1", "updated_at": "2026-09-05T12:00:00+00:00"}
    rw5 = {"fetched_at": "2026-09-05T11:30:00+00:00", "events": [{"event_id": "w2"}]}
    c5 = {"cbc": {"items": 4, "feeds_ok": 2, "feeds_total": 2},
          "gouv-quebec": {"items": 7, "feeds_ok": 1, "feeds_total": 1},
          "le-soleil": {"items": 11, "feeds_ok": 1, "feeds_total": 1},
          "ville-quebec": {"items": 0, "feeds_ok": 0, "feeds_total": 1}}
    c5_richer = dict(c5, **{"ville-quebec": {"items": 3, "feeds_ok": 1, "feeds_total": 1}})
    return [(p4, h4, rw4, {}), (p4, h4, rw4, {}), (p5, h5, rw5, c5), (p5, h5, rw5, c5_richer)]


def golden_event_view(history_doc):
    """An invented event view for the same edition as the step (state only)."""
    return {"edition": history_doc["updated_at"], "events": [
        {"event_id": "ev-0123456789abcdef", "type": "fire-building", "places": ["limoilou"],
         "activity": "new", "window_state": "in_window",
         "members": [{"item_id": "ab" * 32, "institution": "le-soleil", "language": "fr",
                      "origin_class": "own_reporting", "title": "Titre inventé", "url": "https://exemple.test/x"}],
         "institutions": ["le-soleil"], "languages": ["fr"], "independence": {"count": 1}}]}


class GoldenByteIdentity(unittest.TestCase):
    """edition_record, seal_edition, voice_row and the public chain stay frozen."""

    def emit_golden(self, root: Path, events_for=None):
        import contextlib
        import io
        state_path = root / "data" / "registre.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(golden_seed_state()), encoding="utf-8")
        out_dir, out_html = root / "public" / "registre", root / "public" / "registre.html"
        with contextlib.redirect_stdout(io.StringIO()):
            for p, h, rw, c in golden_steps():
                registre.emit(p, rw, history=h, collection=c, state_path=state_path,
                              out_dir=out_dir, out_html=out_html,
                              events=events_for(h) if events_for else None,
                              events_path=root / "absent" / "latest_events.json")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        files = {name: (out_dir / name).read_bytes() for name in (*GOLDEN_FILES, "institutions.json")}
        files["registre.html"] = out_html.read_bytes()
        return state, files

    def assert_golden(self, state, files):
        for name, expected in GOLDEN_FILES.items():
            lf = files[name].replace(b"\r\n", b"\n")
            self.assertEqual(hashlib.sha256(lf).hexdigest(), expected, name)
        for key, expected in GOLDEN_STATE.items():
            self.assertEqual(hashlib.sha256(registre.canonical(state[key])).hexdigest(), expected, key)
        self.assertEqual(state["seals"][-1]["root"], GOLDEN_ROOT)
        self.assertEqual([s["seq"] for s in state["seals"]], [1, 2, 3, 4, 5])

    def test_reemitted_fixture_chain_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, files = self.emit_golden(Path(tmp))
            self.assert_golden(state, files)
            self.assertEqual(state["evenements"]["seals"], [])

    def test_an_event_view_changes_no_edition_byte(self):
        hostile = lambda h: {"edition": h["updated_at"], "events": [{"event_id": "<b>x</b>"}]}  # noqa: E731
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b, \
                tempfile.TemporaryDirectory() as c:
            plain_state, plain = self.emit_golden(Path(a))
            state, files = self.emit_golden(Path(b), golden_event_view)
            self.assert_golden(state, files)
            self.assertEqual(files, plain)       # institutions.json and the page too
            self.assertEqual([s["seq"] for s in state["evenements"]["seals"]], [1, 2])
            self.assertEqual(state["evenements"]["seals"][0]["record"]["edition_root"],
                             state["seals"][3]["root"])
            bad_state, bad = self.emit_golden(Path(c), hostile)
            self.assert_golden(bad_state, bad)
            self.assertEqual(bad_state["evenements"]["seals"], [])
            self.assertEqual(plain_state["seals"], state["seals"])

    def test_verify_cli_still_accepts_an_existing_chain_json(self):
        """The chain.json checked here is byte-identical to the one the code wrote
        before the event chain existed (the golden digest proves it)."""
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state, files = self.emit_golden(root, golden_event_view)
            self.assert_golden(state, files)
            for target in (root / "public" / "registre" / "chain.json", root / "data" / "registre.json"):
                proc = subprocess.run([sys.executable, "-X", "utf8", str(harness.ROOT / "scripts" / "registre.py"),
                                       "--verify", str(target)], capture_output=True, text=True, timeout=120)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertIn("OK 5 seals verified", proc.stdout)


if __name__ == "__main__":
    unittest.main()
