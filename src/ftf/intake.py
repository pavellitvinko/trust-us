"""Add material: safe zip unpacking and the deterministic stages, with progress callbacks."""

from __future__ import annotations

import re
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
from .ingest import ingest_folder, ingest_path
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


def _ingest(path: Path, store: Store, single: bool):
    """One incident folder, or (unless `single`) a folder whose sub-folders are incidents."""
    return [ingest_folder(path, store)] if single else ingest_path(path, store)


def _probe(path: Path, single: bool = False):
    with tempfile.TemporaryDirectory() as scratch:
        probe = Store(Path(scratch) / "probe.sqlite")
        try:
            return _ingest(path, probe, single)
        finally:
            probe.close()


def refuse_known_overwrite(store: Store, path: Path) -> None:
    """Stop a new folder from replacing a shipped incident that has the same name."""
    shipped = {v.incident_id for v in store.views() if v.meta.get("shipped")}
    if not shipped:
        return
    repeated = sorted(i.incident_id for i, _ in _probe(path) if i.incident_id in shipped)
    if repeated:
        raise IntakeError(
            f"would replace known incidents: {', '.join(repeated)}. Rename the folder, "
            "or use --replace-known with your own copy of the sample."
        )


def _check_new_material(store: Store, path: Path, single: bool = False) -> None:
    """Dry-run ingestion, so a rejected folder never touches stored incidents."""
    found = _probe(path, single)
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
    single_incident: bool = False,
) -> dict[str, Any]:
    """Ingest a folder and run the model-free stages, reporting each one as it starts.

    `single_incident` reads the folder as one incident even when it holds only sub-folders,
    as a capture does (`<incident>/bundle/`).
    """
    path = Path(path)
    if not path.is_dir():
        raise IntakeError(f"folder not found: {path}")

    def stage(name: str) -> None:
        if on_stage:
            on_stage(name, STAGES.index(name), len(STAGES))

    with ANALYSIS_LOCK:
        stage("ingest")
        _check_new_material(store, path, single_incident)
        added = _ingest(path, store, single_incident)
        views = sum(len(found) for _, found in added)
        stage("extract")
        observations = pipeline.run_extract(store)
        stage("enrich")
        if enrich:
            pipeline.run_enrich(store, [v.view_id for _, found in added for v in found])
        run_warning_lists(
            store,
            get_settings().data_dir / "warning_lists",
            view_ids=[v.view_id for v in store.views() if not v.meta.get("shipped")],
        )
        stage("fingerprint")
        fingerprints = pipeline.run_fingerprint(store)
        pipeline.run_cloaking(store)
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


# --- URL input (live capture or replay of an archived HAR) --------------------------------

CAPTURE_STAGES = ("capture", *STAGES)
_URL_SCHEMES = ("http", "https")


def capture_dir(url: str | None, replay: Path | None) -> Path:
    """A new incident folder for one capture, named after the host and the time."""
    from datetime import UTC, datetime
    from urllib.parse import urlsplit

    host = urlsplit(url if url and "//" in url else f"//{url or ''}").hostname
    name = host or (replay.stem if replay else "capture")
    name = "".join(c if c.isalnum() or c in "-_." else "_" for c in name) or "capture"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return get_settings().data_dir / "captures" / f"{name}-{stamp}-{uuid.uuid4().hex[:6]}"


def check_url(text: str) -> str:
    """A web address to open: http(s) only, a bare host gets https://."""
    from urllib.parse import urlsplit

    url = text.strip()
    if not url:
        return ""
    if any(c.isspace() for c in url):
        raise IntakeError("a URL cannot contain spaces")
    if "://" not in url and re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:(?!\d)", url):
        raise IntakeError("only http and https addresses can be captured")  # javascript:, data:
    parts = urlsplit(url if "://" in url else f"https://{url}")
    if parts.scheme.lower() not in _URL_SCHEMES or not parts.hostname:
        raise IntakeError("only http and https addresses can be captured")
    return parts.geturl()


def save_upload(data: IO[bytes], name: str, uploads: Path) -> Path:
    """Store an uploaded file (an archived HAR) under its own folder."""
    stem = "".join(c if c.isalnum() or c in "-_." else "_" for c in Path(name).name) or "upload"
    target = uploads / uuid.uuid4().hex / stem
    target.parent.mkdir(parents=True)
    with target.open("wb") as sink:
        shutil.copyfileobj(data, sink)
    return target


def _capture(url: str | None, out_dir: Path, replay_har: Path | None, timeout: float):
    """Run the browser in its own thread: Playwright's sync API refuses a thread that already
    runs an event loop, and the caller's thread (Streamlit's) may."""
    from concurrent.futures import ThreadPoolExecutor

    from .capture import capture_url

    with ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(capture_url, url, out_dir, replay_har=replay_har, timeout=timeout)
        return job.result()


def capture_and_run(
    store: Store,
    url: str | None,
    on_stage: Callable[[str, int, int], None] | None = None,
    *,
    replay_har: Path | None = None,
    enrich: bool = True,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Capture a URL (or replay an archived HAR) into a new incident, then run the stages.

    A capture that reached no page (dead domain, refused, timed out) is still material: its
    view keeps the failure as `capture.status` and gets DNS and RDAP, as with `ftf capture`.
    """
    url = check_url(url or "")
    if not url and replay_har is None:
        raise IntakeError("give a URL, or an archived HAR to replay")
    total = len(CAPTURE_STAGES)
    if on_stage:
        on_stage("capture", 0, total)
    incident_dir = capture_dir(url or None, replay_har)
    try:
        result = _capture(url or None, incident_dir / "bundle", replay_har, timeout)
    except ImportError as exc:
        raise IntakeError("live capture needs Playwright: uv sync") from exc
    except ValueError as exc:
        raise IntakeError(str(exc)) from exc
    except Exception as exc:  # browser missing or crashed: say so plainly
        first = (str(exc).strip().splitlines() or [type(exc).__name__])[0][:200]
        hint = " (run: uv run playwright install chromium)" if "Executable" in first else ""
        raise IntakeError(f"the browser could not run: {first}{hint}") from exc

    def shifted(name: str, index: int, _total: int) -> None:
        if on_stage:
            on_stage(name, index + 1, total)

    out = run_stages(store, incident_dir, shifted, enrich=enrich, single_incident=True)
    man = result.manifest
    out["capture"] = {
        "mode": man.mode,
        "status": man.status.value,
        "ok": result.ok,
        "page_url": man.page_url,
        "final_url": man.final_url,
        "error": man.error,
        "redirects": len(man.redirects),
        "bundle": str(result.bundle),
    }
    return out
