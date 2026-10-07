"""Bilingual French/English lexicon: re-export of the production vocabulary.

Phase 0 kept the lexicon here; Phase 1 promoted it to scripts/event_lexicon.py
so the evaluation measures exactly the vocabulary the event matcher ships.
This module stays only so older imports (`import lexicon_fr_en as lex`) keep
working. It holds vocabulary, never publisher text.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from event_lexicon import (  # noqa: E402,F401
    ENTRIES, KIND_WEIGHT, META, SPECIFIC_PLACE_KINDS, SURFACE_TO_ID, WORD_CANON,
    find_terms, fold, kind_of, label_of, term_ids,
)
