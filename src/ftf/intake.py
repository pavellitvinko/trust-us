"""Add material: safe zip unpacking and the deterministic stages, with progress callbacks."""

from __future__ import annotations

import shutil
import stat
import uuid
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import IO, Any

from . import pipeline
from .fusion import run_method
from .ingest import ingest_path
from .store import Store

MAX_ZIP_FILES = 20_000
MAX_ZIP_BYTES = 2 * 1024**3
STAGES = ("ingest", "extract", "fingerprint", "compare", "group")


class IntakeError(ValueError):
    """The material cannot be added; the message is safe to show to the analyst."""


def safe_extract(archive: IO[bytes] | Path, target: Path) -> None:
    """Unpack regular files only, refusing path traversal, links and oversized archives."""
    try:
        with zipfile.ZipFile(archive) as bundle:
            members = [m for m in bundle.infolist() if not m.is_dir()]
            if len(members) > MAX_ZIP_FILES:
                raise IntakeError("archive has too many files")
            if sum(m.file_size for m in members) > MAX_ZIP_BYTES:
                raise IntakeError("archive is too large when unpacked")
            root = target.resolve()
            written = 0
            for member in members:
                parts = PurePosixPath(member.filename.replace("\\", "/")).parts
                if member.filename.startswith(("/", "\\")) or ".." in parts:
                    raise IntakeError(f"unsafe path in archive: {member.filename}")
                if parts and (parts[0] == "__MACOSX" or parts[-1] == ".DS_Store"):
                    continue
                if stat.S_ISLNK(member.external_attr >> 16):
                    raise IntakeError(f"link in archive: {member.filename}")
                path = (root / Path(*parts)).resolve()
                if not path.is_relative_to(root):
                    raise IntakeError(f"unsafe path in archive: {member.filename}")
                path.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as source, path.open("wb") as sink:
                    # The declared size can lie, so count what is actually written.
                    while chunk := source.read(1024 * 1024):
                        written += len(chunk)
                        if written > MAX_ZIP_BYTES:
                            raise IntakeError("archive is too large when unpacked")
                        sink.write(chunk)
    except zipfile.BadZipFile as exc:
        raise IntakeError("not a valid zip archive") from exc


def add_zip(archive: IO[bytes], name: str, uploads: Path) -> Path:
    """Unpack an upload into its own folder and return the folder to ingest."""
    stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in Path(name).stem) or "upload"
    target = uploads / uuid.uuid4().hex / stem
    target.mkdir(parents=True)
    try:
        safe_extract(archive, target)
    except IntakeError:
        shutil.rmtree(target.parent, ignore_errors=True)
        raise
    return target


def run_stages(
    store: Store, path: Path, on_stage: Callable[[str, int, int], None] | None = None
) -> dict[str, Any]:
    """Ingest a folder and run the model-free stages, reporting each one as it starts."""
    path = Path(path)
    if not path.is_dir():
        raise IntakeError(f"folder not found: {path}")

    def stage(name: str) -> None:
        if on_stage:
            on_stage(name, STAGES.index(name), len(STAGES))

    stage("ingest")
    added = ingest_path(path, store)
    views = sum(len(found) for _, found in added)
    if not views:
        raise IntakeError("no views found in this material")
    stage("extract")
    observations = pipeline.run_extract(store)
    stage("fingerprint")
    fingerprints = pipeline.run_fingerprint(store)
    stage("compare")
    evidence = pipeline.run_compare_all(store)
    stage("group")
    groups = run_method(store)
    return {
        "incidents": [incident.incident_id for incident, _ in added],
        "views": views,
        "observations": observations,
        "fingerprints": fingerprints,
        "evidence": evidence,
        "campaigns": len(groups.campaigns),
        "families": len(groups.families),
    }
