"""Pack/unpack the cross-edition state so the collector can run off-laptop.

The public repo must never carry publisher content, and `data/` is gitignored on
purpose (raw snapshots are internal, 30-day retention). So the memory that makes
"depuis la dernière édition", the dossier timeline and the roadworks collection
diff survive travels as a single tarball in a **private** state store, and CI
restores it before a run and persists it after.

Only the inputs the pipeline cannot regenerate are packed:

  * the newest snapshot pair per RSS source (and the newest WZDX geojson / civic HTML),
  * the newest raw run log plus the identity/conditional-GET caches,
  * `issues/latest_issues.json` and `issues/history.json` (the diff memory),
  * `roadworks/latest_roadworks.json` (with its per-event history),
  * the `ops/` ledgers,
  * the re-hosted brief media and its manifest.

Everything else (normalized, enriched, clustered, edges, anomalies, pulse) is
rebuilt from these each run. Deterministic: members are added in sorted order,
so the same inputs produce the same archive.

Usage:
  python scripts/state_pack.py pack   out.tar.gz
  python scripts/state_pack.py unpack in.tar.gz
  python scripts/state_pack.py list
"""
from __future__ import annotations

import os
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

# Explicit small files (relative to data/).
EXPLICIT = (
    "issues/latest_issues.json",
    "issues/history.json",
    # Le Registre: the sealed edition chain and voice rows. Losing it would not
    # corrupt anything, but it would reset the public record to a genesis seal.
    "registre/registre.json",
    "roadworks/latest_roadworks.json",
    "civic/latest_consultations.json",
    "media/brief_manifest.json",
    "raw/_ua_policy.json",
    # The latest rendered inputs, so the hourly roads-only lane can re-render
    # without re-running normalize/enrich/cluster (no new edition).
    "normalized/latest_candidates.json",
    "normalized/latest_enriched.json",
    "normalized/latest_ranked.json",
    # The event store (docs/EVENTS.md section 4): ids are minted once and read
    # back, so without it every CI run would re-mint every id. The current
    # view travels too, so the roads-only lane can render without re-running
    # the event builder.
    "events/store.json",
    "events/latest_events.json",
)


def _newest_per_source() -> list[Path]:
    """Newest snapshot pair per source directory (xml/geojson + its .json meta)."""
    out: list[Path] = []
    raw = DATA / "raw"
    if not raw.is_dir():
        return out
    for source in sorted(raw.iterdir()):
        if not source.is_dir() or source.name.startswith("_"):
            continue
        snapshots = sorted(
            (p for p in source.iterdir()
             if p.is_file() and p.suffix.lower() in {".xml", ".geojson", ".html"}),
            key=lambda p: p.name,
        )
        if not snapshots:
            continue
        newest = snapshots[-1]
        out.append(newest)
        meta = newest.with_suffix(".json")
        if meta.is_file():
            out.append(meta)
    return out


def _raw_root_files() -> list[Path]:
    """Raw root: newest run log + the identity/conditional-GET caches."""
    raw = DATA / "raw"
    if not raw.is_dir():
        return []
    runs = sorted(raw.glob("_run_*.json"), key=lambda p: p.name)
    keep: list[Path] = runs[-1:] if runs else []
    for pattern in ("_*.json",):
        for path in sorted(raw.glob(pattern)):
            if path.is_file() and not path.name.startswith("_run_"):
                keep.append(path)
    return keep


def _body_cache() -> list[Path]:
    bodies = DATA / "raw" / "_bodies"
    if not bodies.is_dir():
        return []
    return [p for p in sorted(bodies.iterdir()) if p.is_file() and not p.name.startswith(".")]


def _media() -> list[Path]:
    media = DATA / "media" / "brief"
    if not media.is_dir():
        return []
    return [p for p in sorted(media.iterdir()) if p.is_file() and not p.name.startswith(".")]


def _ops() -> list[Path]:
    """Ops ledgers, minus anything that is process state, not a fact.

    The refresh lock (`refresh.lock` and its `.takeover` sidecar) must never
    travel in the archive: a run killed before its `finally` would otherwise
    pack the lock, CI would unpack it with a fresh mtime, and every later
    refresh would refuse to run ("another refresh is already running") while
    reporting success. Partial writes (`.tmp`) are never a store either.
    """
    ops = DATA / "ops"
    if not ops.is_dir():
        return []
    return [
        p for p in sorted(ops.iterdir())
        if p.is_file() and not _is_process_state(p.name)
    ]


_PROCESS_STATE_SUFFIXES = (".lock", ".lock.takeover", ".takeover", ".tmp")


def _is_process_state(name: str) -> bool:
    return name.startswith(".") or name.endswith(_PROCESS_STATE_SUFFIXES)


def members() -> list[Path]:
    """Every file to pack, sorted and de-duplicated, absolute paths."""
    found: list[Path] = []
    found.extend(DATA / name for name in EXPLICIT)
    found.extend(_raw_root_files())
    found.extend(_newest_per_source())
    found.extend(_body_cache())
    found.extend(_media())
    found.extend(_ops())
    unique: dict[str, Path] = {}
    for path in found:
        if path.is_file():
            unique[path.relative_to(ROOT).as_posix()] = path
    return [unique[key] for key in sorted(unique)]


def pack(out_path: Path) -> int:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w:gz") as tar:
        for path in members():
            tar.add(path, arcname=path.relative_to(ROOT).as_posix(), recursive=False)
    return len(members())


# A state archive carries ~100 files and ~10 MB; anything far beyond is not a
# state snapshot (a decompression bomb from a compromised asset, or a packing bug).
MAX_MEMBERS = 10_000
MAX_BYTES = 1 << 30


def unpack(in_path: Path) -> int:
    src = Path(in_path)
    data_root = (ROOT / "data").resolve()
    with tarfile.open(src, "r:gz") as tar:
        infos = tar.getmembers()
        if len(infos) > MAX_MEMBERS:
            raise ValueError(f"archive has {len(infos)} members (cap {MAX_MEMBERS})")
        total = sum(info.size for info in infos if info.isfile())
        if total > MAX_BYTES:
            raise ValueError(f"archive expands to {total} bytes (cap {MAX_BYTES})")
        plan: list[tuple[tarfile.TarInfo, Path]] = []
        for info in infos:
            name = info.name.replace("\\", "/")
            # Mirror the pack-side filter: an archive packed by an older run can
            # still carry refresh.lock, and unpacking it with a fresh mtime
            # would silently skip every future refresh.
            if _is_process_state(Path(name).name):
                continue
            target = (ROOT / name).resolve()
            # A state snapshot only ever carries data/ inputs. Refuse absolute
            # paths, traversal, and any member that would overwrite code or
            # method files (a compromised state asset must not become RCE).
            if name.startswith("/") or not target.is_relative_to(data_root):
                raise ValueError(f"unsafe archive member: {name}")
            if info.isfile():
                plan.append((info, target))  # dirs and links skipped: plain files only
        # Extract everything into a sibling staging dir first, then move into
        # place: a member that fails to read leaves data/ exactly as it was.
        data_root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".unpack-", dir=data_root))
        staged: list[tuple[Path, Path]] = []
        try:
            for n, (info, target) in enumerate(plan):
                extracted = tar.extractfile(info)
                if extracted is None:
                    continue
                part = staging / str(n)
                with extracted, open(part, "wb") as out:
                    shutil.copyfileobj(extracted, out)
                staged.append((part, target))
            for part, target in staged:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(part, target)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    return len(staged)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 1 and args[0] == "list":
        for path in members():
            print(path.relative_to(ROOT).as_posix())
        print(f"{len(members())} file(s)", file=sys.stderr)
        return 0
    if len(args) == 2 and args[0] == "pack":
        print(f"packed {pack(Path(args[1]))} file(s) -> {args[1]}")
        return 0
    if len(args) == 2 and args[0] == "unpack":
        print(f"unpacked {unpack(Path(args[1]))} file(s) from {args[1]}")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
