"""Add material: safe zip unpacking and the deterministic stages, with progress callbacks."""

from __future__ import annotations

import shutil
import stat
import tempfile
import threading
import uuid
import zipfile
import zlib
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import IO, Any

from . import pipeline
from .config import get_settings
from .enrich.warning_lists import run_warning_lists
from .fusion import run_method
from .ingest import ingest_path
from .store import Store

MAX_ZIP_FILES = 20_000
MAX_ZIP_BYTES = 2 * 1024**3
STAGES = ("ingest", "extract", "enrich", "fingerprint", "compare", "group")
# Fusion fits process-wide weights, so analyses and assignments must not overlap.
ANALYSIS_LOCK = threading.RLock()


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
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, zlib.error) as exc:
        raise IntakeError("this zip archive cannot be unpacked") from exc


def add_zip(archive: IO[bytes], name: str, uploads: Path) -> Path:
    """Unpack an upload into its own folder and return the folder to ingest."""
    stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in Path(name).stem) or "upload"
    unique = uuid.uuid4().hex
    # The folder name becomes the incident id, so each upload gets its own.
    target = uploads / unique / f"{stem}-{unique[:8]}"
    target.mkdir(parents=True)
    try:
        safe_extract(archive, target)
        entries = [p for p in target.iterdir() if p.name != ".DS_Store"]
        if entries and all(p.is_dir() for p in entries):
            for folder in entries:
                folder.rename(folder.with_name(f"{folder.name}-{unique[:8]}"))
    except (IntakeError, OSError) as exc:
        shutil.rmtree(target.parent, ignore_errors=True)
        raise IntakeError("this zip archive cannot be unpacked") from exc
    return target


def discard_upload(path: Path) -> None:
    shutil.rmtree(Path(path).parent, ignore_errors=True)


def allowed_folder(text: str, root: str | None) -> Path:
    """A server folder inside the configured root. Without a root, folder input is off."""
    if not root:
        raise IntakeError("folder input is off; upload a zip or set FTF_UI_FOLDER_ROOT")
    base = Path(root).expanduser().resolve()
    path = Path(text).expanduser().resolve()
    if not path.is_relative_to(base):
        raise IntakeError("this folder is outside the allowed root")
    return path


def _check_new_material(store: Store, path: Path) -> None:
    """Dry-run ingestion, so a rejected folder never touches stored incidents."""
    with tempfile.TemporaryDirectory() as scratch:
        probe = Store(Path(scratch) / "probe.sqlite")
        try:
            found = ingest_path(path, probe)
        finally:
            probe.close()
    if not sum(len(views) for _, views in found):
        raise IntakeError("no views found in this material")
    existing = {i.incident_id for i in store.incidents()}
    repeated = sorted(i.incident_id for i, _ in found if i.incident_id in existing)
    if repeated:
        raise IntakeError(f"already ingested: {', '.join(repeated)}")


def run_stages(
    store: Store,
    path: Path,
    on_stage: Callable[[str, int, int], None] | None = None,
    *,
    enrich: bool = True,
) -> dict[str, Any]:
    """Ingest a folder and run the model-free stages, reporting each one as it starts."""
    path = Path(path)
    if not path.is_dir():
        raise IntakeError(f"folder not found: {path}")

    def stage(name: str) -> None:
        if on_stage:
            on_stage(name, STAGES.index(name), len(STAGES))

    with ANALYSIS_LOCK:
        stage("ingest")
        _check_new_material(store, path)
        added = ingest_path(path, store)
        views = sum(len(found) for _, found in added)
        stage("extract")
        observations = pipeline.run_extract(store)
        stage("enrich")
        if enrich:
            pipeline.run_enrich(store)
        run_warning_lists(
            store,
            get_settings().data_dir / "warning_lists",
            view_ids=[v.view_id for _, found in added for v in found],
        )
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
