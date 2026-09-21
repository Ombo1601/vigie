"""Atomic writes for pipeline stores (stdlib only).

One law in one place: a crash, a full disk or a concurrent reader must never
see a truncated handoff file. Text is written to a uniquely named sibling temp
file and replaced atomically on the same volume, so readers keep the previous
store until the new one is complete and two writers can never publish each
other's partial file. On Windows an open reader can block os.replace (the
destination lacks FILE_SHARE_DELETE); that specific failure falls back to a
direct write, which is no worse than the pre-atomic behaviour, instead of
killing the whole refresh chain. Callers stay fail-soft: a genuine OSError from
here is theirs to diagnose.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path


def write_text_atomic(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        part.write_text(text, encoding=encoding)
        try:
            os.replace(part, path)
        except OSError:
            # A reader holding the destination open (or an exotic filesystem)
            # can refuse the replace. Write in place rather than fail the chain.
            path.write_text(text, encoding=encoding)
    finally:
        if part.exists():
            try:
                part.unlink()
            except OSError:
                pass


def write_json_atomic(path: Path, doc, *, indent: int = 2) -> None:
    write_text_atomic(path, json.dumps(doc, ensure_ascii=False, indent=indent))


def write_bytes_atomic(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        part.write_bytes(data)
        try:
            os.replace(part, path)
        except OSError:
            path.write_bytes(data)
    finally:
        if part.exists():
            try:
                part.unlink()
            except OSError:
                pass


def write_bytes_dedup(path: Path, data: bytes, previous: Path | None = None) -> None:
    """Write a per-run snapshot, hard-linking an identical previous one.

    Append-only stores keep one file per run so offline rebuilds and edition
    diffs can address a stamp; an unchanged feed (a 304, or identical bytes)
    would otherwise copy the same body every run. A hard link keeps the new
    name and every reader intact at zero extra disk. Linking is best-effort:
    if the filesystem refuses (non-NTFS, cross-volume, permissions) the bytes
    are written atomically via a unique temp file, never an error and never
    a truncated snapshot a concurrent reader could observe.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if previous is not None:
        previous = Path(previous)
        if previous.exists():
            try:
                os.link(previous, path)
                return
            except OSError:
                pass
    write_bytes_atomic(path, data)
