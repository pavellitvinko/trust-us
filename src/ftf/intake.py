"""Add material: safe zip unpacking and the deterministic stages, with progress callbacks."""

from __future__ import annotations

import os
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
from .capture.network import blocked_address
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


def ingest_new_material(store: Store, path: Path, *, recovering: bool = False):
    """Parse/hash once in a private store, then atomically register a validated submission."""
    probe = Store(":memory:")
    try:
        found = _ingest(path, probe, False)
        if not sum(len(views) for _, views in found):
            raise IntakeError("no views found in this material")
        with ANALYSIS_LOCK, store.batch():
            existing = {i.incident_id: i for i in store.incidents()}
            repeated = [i.incident_id for i, _ in found if i.incident_id in existing
                        and not (recovering and existing[i.incident_id].root == i.root)]
            if repeated:
                raise IntakeError(f"already ingested: {', '.join(sorted(repeated))}")
            for incident, views in found:
                if recovering and incident.incident_id in existing:
                    continue
                store.put_incident(incident, probe.artifacts(incident.incident_id), views)
        return [(incident, store.views(incident.incident_id)) for incident, _ in found]
    finally:
        probe.close()


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
        added_ids = [v.view_id for _, found in added for v in found]
        observations = pipeline.run_extract(store, added_ids)
        stage("enrich")
        if enrich:
            pipeline.run_enrich(store, [v.view_id for _, found in added for v in found])
        run_warning_lists(
            store,
            get_settings().data_dir / "warning_lists",
            view_ids=added_ids,
        )
        stage("fingerprint")
        fingerprints = pipeline.run_fingerprint(store, added_ids)
        pipeline.run_cloaking(store)
        stage("compare")
        evidence = 0  # run_method compares with corpus-fitted weights in its two passes
        stage("group")
        groups = run_method(store)
        evidence = len(store.evidence())
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
_OTHER_SCHEMES = {
    "about", "blob", "chrome", "data", "file", "ftp", "javascript", "mailto", "sms", "tel",
    "vbscript", "view-source", "ws", "wss",
}  # fmt: skip
# Lets the interface open loopback and private addresses (a lab setup, tests). Off by default:
# otherwise anyone at the interface could make the server's browser read its own networks.
ALLOW_PRIVATE_ENV = "FTF_UI_ALLOW_PRIVATE"


def private_allowed() -> bool:
    return os.environ.get(ALLOW_PRIVATE_ENV, "").strip().lower() in ("1", "true", "yes")


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
    scheme = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*):(?!\d)", url)
    has_scheme = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url) is not None
    if not has_scheme and scheme:
        if scheme.group(1).lower() in _URL_SCHEMES:  # http:/host, a typo
            raise IntakeError("malformed address: write it as https://host/path")
        name = scheme.group(1).lower()
        if name in _OTHER_SCHEMES or "@" not in url.split("/", 1)[0]:  # not user:pass@host
            raise IntakeError("only http and https addresses can be captured")
    parts = urlsplit(url if has_scheme else f"https://{url}")
    if parts.scheme.lower() not in _URL_SCHEMES or not parts.hostname:
        raise IntakeError("only http and https addresses can be captured")
    return parts.geturl()


def save_upload(data: IO[bytes], name: str, uploads: Path) -> Path:
    """Store an uploaded file (an archived HAR) under its own folder."""
    stem = "".join(c if c.isalnum() or c in "-_." else "_" for c in Path(name).name) or "upload"
    target = uploads / uuid.uuid4().hex / stem
    target.parent.mkdir(parents=True)
    try:
        with target.open("wb") as sink:
            shutil.copyfileobj(data, sink)
    except OSError:
        shutil.rmtree(target.parent, ignore_errors=True)
        raise
    return target


def check_public(url: str) -> None:
    """Refuse an address on the server's own networks, unless the setup allows it."""
    from urllib.parse import urlsplit

    if private_allowed():
        return
    address = blocked_address(urlsplit(url).hostname)
    if address is not None:
        raise IntakeError(
            f"this address is on a local or private network ({address}) and is not captured"
        )


class CaptureTimeout(Exception):
    """The browser did not finish within the overall limit."""


def _deadline(timeout: float) -> float:
    """Navigation, settling, scrolling and the snapshot each have their own timeouts."""
    return timeout * 4 + 60


def _capture(
    url: str | None,
    out_dir: Path,
    replay_har: Path | None,
    timeout: float,
    public_only: bool = True,
):
    """Run the browser in a child process (see `capture.worker`). On the deadline the whole
    process group is killed, Playwright's driver and Chromium included."""
    import pickle
    import signal
    import subprocess
    import sys

    from .capture.worker import rebuild_error

    kwargs = {
        "url": url,
        "out_dir": out_dir,
        "replay_har": replay_har,
        "timeout": timeout,
        "public_only": public_only,
    }
    posix = os.name == "posix"
    proc = subprocess.Popen(
        [sys.executable, "-m", "ftf.capture.worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        start_new_session=posix,
    )
    try:
        reply, _ = proc.communicate(pickle.dumps(kwargs), timeout=_deadline(timeout))
    except subprocess.TimeoutExpired as exc:
        raise CaptureTimeout from exc
    finally:
        if proc.poll() is None or posix:
            try:  # the group outlives a dead leader when Chromium is still running
                os.killpg(proc.pid, signal.SIGKILL) if posix else proc.kill()
            except (ProcessLookupError, PermissionError):
                pass
            proc.wait(10)
    if not reply:
        raise RuntimeError(f"capture process exited with code {proc.returncode}")
    status, payload = pickle.loads(reply)  # our own child's reply
    if status == "error":
        raise rebuild_error(*payload)
    return payload


def _forget_capture(store: Store, incident_dir: Path) -> None:
    """Remove a capture folder that no stored incident uses (the capture or ingest failed)."""
    if incident_dir.name not in {i.incident_id for i in store.incidents()}:
        shutil.rmtree(incident_dir, ignore_errors=True)


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
    if url and replay_har is None:  # a replay never leaves the archive
        check_public(url)
    total = len(CAPTURE_STAGES)
    if on_stage:
        on_stage("capture", 0, total)
    incident_dir = capture_dir(url or None, replay_har)
    try:
        result = _capture(
            url or None, incident_dir / "bundle", replay_har, timeout, not private_allowed()
        )
    except ImportError as exc:
        _forget_capture(store, incident_dir)
        raise IntakeError("live capture needs Playwright: uv sync") from exc
    except CaptureTimeout as exc:
        _forget_capture(store, incident_dir)  # the capture process is gone
        raise IntakeError("the browser did not finish in time; try again later") from exc
    except ValueError as exc:  # an archive that cannot be replayed
        _forget_capture(store, incident_dir)
        message = str(exc)
        if replay_har is not None:
            message = message.replace(str(replay_har), replay_har.name)
        raise IntakeError(message) from exc
    except OSError as exc:  # disk full, permissions: not the browser's fault
        _forget_capture(store, incident_dir)
        raise IntakeError(f"the capture could not be saved: {exc.strerror or exc}") from exc
    except Exception as exc:  # browser missing or crashed: say so plainly
        _forget_capture(store, incident_dir)
        first = (str(exc).strip().splitlines() or [type(exc).__name__])[0][:200]
        hint = " (run: uv run playwright install chromium)" if "Executable" in first else ""
        raise IntakeError(f"the browser could not run: {first}{hint}") from exc

    def shifted(name: str, index: int, _total: int) -> None:
        if on_stage:
            on_stage(name, index + 1, total)

    try:
        out = run_stages(store, incident_dir, shifted, enrich=enrich, single_incident=True)
    except BaseException:
        _forget_capture(store, incident_dir)
        raise
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
