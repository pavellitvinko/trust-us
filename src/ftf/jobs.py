"""Add material in the background: save what was submitted, capture links, run the stages.

A submission is saved at once and registered by the intake worker. The persistent scheduler
reads independent items concurrently and publishes coherent campaign snapshots. SQLite
connections stay in the thread that made them; live updates use `ANALYSIS_LOCK`.
"""

from __future__ import annotations

import json
import queue
import shutil
import threading
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import IO
from urllib.parse import urlsplit

from .intake import (
    MAX_ZIP_BYTES,
    MAX_ZIP_FILES,
    CaptureTimeout,
    IntakeError,
    check_public,
    check_url,
    private_allowed,
)
from .intake import _capture as guarded_capture
from .library import record_cases
from .llm_checks import new_steps
from .store import Store

STAGE_NAMES = {
    "local": "Collecting local clues",
    "semantic": "Comparing meaning",
    "match": "Matching known patterns",
    "previews": "Preparing pictures",
    "image": "Reading images with the model",
    "page": "Reading pages with the model",
    "video": "Reading videos with the model",
    "expectations": "Checking expected page elements",
    "techniques": "Checking persuasion techniques",
    "warnings": "Checking warning lists",
    "rebuild": "Updating evidence and campaigns",
    "patterns": "Discovering and matching patterns",
    "finalize": "Updating the Library",
    "capture": "Capturing the link",
    "ingest": "Reading files",
    "extract": "Collecting clues",
    "enrich": "Checking the domain",
    "fingerprint": "Fingerprinting",
    "compare": "Comparing with the library",
    "group": "Grouping",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_name(text: str, fallback: str) -> str:
    out = "".join(c if c.isalnum() or c in "-_." else "_" for c in text).strip("._")
    return out[:60] or fallback


def _parts(name: str) -> tuple[str, ...]:
    """A client-supplied relative path, checked the same way zip members are."""
    clean = name.replace("\\", "/")
    parts = PurePosixPath(clean).parts
    if not parts or clean.startswith("/") or ".." in parts or ":" in parts[0]:
        raise IntakeError(f"unsafe file name: {name}")
    return parts


def save_files(files: Iterable[tuple[str, IO[bytes]]], uploads: Path) -> Path:
    """Write uploaded files into a new folder and return what to ingest.

    A chosen folder arrives with its own name as the first path part: kept, so a folder of
    sub-folders stays one case per sub-folder. Loose files become one case.
    """
    entries = [(_parts(n), body) for n, body in files]
    entries = [(p, b) for p, b in entries if p[-1] != ".DS_Store" and p[0] != "__MACOSX"]
    if not entries:
        raise IntakeError("no files to add")
    if len(entries) > MAX_ZIP_FILES:
        raise IntakeError("too many files")
    seen: set[tuple[str, ...]] = set()
    for parts, _ in entries:
        if parts in seen:  # e.g. a folder dragged in arrives without its sub-folders
            raise IntakeError(f"two files are both called {parts[-1]}: choose the folder instead")
        seen.add(parts)
    unique = uuid.uuid4().hex
    base = uploads / unique
    tops = {p[0] for p, _ in entries}
    as_folder = len(tops) == 1 and all(len(p) > 1 for p, _ in entries)
    if not as_folder:
        entries = [((f"case-{unique[:8]}", *p), b) for p, b in entries]
    root = (base / entries[0][0][0]).resolve()
    written = 0
    try:
        for parts, body in entries:
            path = (base / Path(*parts)).resolve()
            if not path.is_relative_to(base.resolve()):
                raise IntakeError(f"unsafe file name: {'/'.join(parts)}")
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("wb") as sink:
                while chunk := body.read(1024 * 1024):
                    written += len(chunk)
                    if written > MAX_ZIP_BYTES:
                        raise IntakeError("the files are too large")
                    sink.write(chunk)
        # The folder name becomes the case ID, so each submission gets its own.
        own_files = [p for p in root.iterdir() if p.is_file() and p.name != ".DS_Store"]
        subdirs = [p for p in root.iterdir() if p.is_dir()]
        if own_files or not subdirs:
            renamed = root.with_name(f"{_safe_name(root.name, 'case')}-{unique[:8]}")
            root.rename(renamed)
            return renamed
        for folder in subdirs:
            folder.rename(folder.with_name(f"{folder.name}-{unique[:8]}"))
        return root
    except (IntakeError, OSError) as exc:
        shutil.rmtree(base, ignore_errors=True)
        if isinstance(exc, IntakeError):
            raise
        raise IntakeError("these files cannot be saved") from exc


def _host(url: str) -> str:
    return _safe_name(urlsplit(url if "://" in url else f"https://{url}").hostname or "", "link")


def link_case(url: str, uploads: Path) -> Path:
    """What to ingest for links submitted without files: a folder holding one case folder,
    named after the first link, so every link of the submission joins that one case."""
    unique = uuid.uuid4().hex
    folder = uploads / unique / f"{_host(url)}-{unique[:8]}"
    folder.mkdir(parents=True)
    return folder.parent


def is_collection(path: Path) -> bool:
    own = [p for p in path.iterdir() if p.is_file() and p.name != ".DS_Store"]
    return not own and any(p.is_dir() for p in path.iterdir())


@dataclass
class Job:
    job_id: str
    path: Path  # what to ingest: one case folder or a folder of case folders
    names: list[str]  # what the person submitted, for the Library before ingestion
    urls: list[str] = field(default_factory=list)
    status: str = "queued"  # queued | running | done | failed
    stage: str | None = None
    error: str | None = None
    incidents: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    finished_at: str | None = None
    kind: str = "material"
    results: dict = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    steps: list[dict] = field(default_factory=new_steps)
    core: dict = field(default_factory=dict)
    view_ids: list[str] = field(default_factory=list)
    recovered: bool = False
    checks: list[str] | None = None

    @property
    def stage_name(self) -> str:
        return STAGE_NAMES.get(self.stage or "", "Queued")


Capture = Callable[[str, Path], object]
CAPTURE_TIMEOUT = 30.0  # navigation timeout; the whole capture gets intake's longer deadline


def check_link(text: str) -> str:
    """A pasted link as a capturable address: http(s) only, never the server's own networks
    (HAC-45). Raises IntakeError; returns the normalised URL (a bare host gets https://)."""
    url = check_url(text)
    if not url:
        raise IntakeError("paste a full link or a domain first")
    check_public(url)
    return url


def _capture(url: str, out_dir: Path) -> object:
    """Capture through HAC-45's guard: the address is checked again, and the browser runs in a
    child process behind an egress gate that only reaches public addresses, with a deadline."""
    url = check_link(url)
    try:
        return guarded_capture(url, out_dir, None, CAPTURE_TIMEOUT, not private_allowed())
    except CaptureTimeout as exc:
        raise IntakeError("the browser did not finish in time; try again later") from exc


class Worker:
    """Material intake and progress facade over one data folder's persistent scheduler."""

    def __init__(self, db_path: Path, data_dir: Path, capture: Capture = _capture):
        self.db_path, self.data_dir, self.capture = Path(db_path), Path(data_dir), capture
        self.jobs: dict[str, Job] = {}
        self.generation = 0  # bumped whenever stored results change
        self._queue: queue.Queue[str] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._mutex = threading.RLock()
        self._capture_slots = threading.Semaphore(2)
        self.item_checks: dict[str, dict] = {}
        try:
            self.item_checks = json.loads((self.data_dir / "item-checks.json").read_text())
        except (OSError, ValueError):
            pass
        for checks in self.item_checks.values():
            for check in checks.values():
                if check["state"] in ("queued", "running"):
                    check.update(state="failed", message="The server stopped before this check finished.")
        self.last_llm_run: dict | None = None
        try:
            self.last_llm_run = json.loads((self.data_dir / "llm-run.json").read_text())
        except (OSError, ValueError):
            pass
        if self.last_llm_run and self.last_llm_run["status"] in ("queued", "running"):
            self.last_llm_run.update(status="failed", error="The server stopped before this run finished.")
        from .scheduler import Scheduler

        self.scheduler = Scheduler(self)

    @property
    def uploads(self) -> Path:
        return self.data_dir / "uploads"

    def active(self) -> list[Job]:
        return [j for j in self.jobs.values() if j.status in ("queued", "running")]

    def submit(self, path: Path, names: list[str], urls: list[str] | None = None) -> Job:
        job = Job(job_id=uuid.uuid4().hex[:12], path=Path(path), names=names, urls=urls or [])
        return self._enqueue(job)

    def submit_llm(self, skipped: list[str] | None = None) -> Job:
        from .llm_checks import require_model_keys

        if {"image", "page", "expectations", "techniques", "patterns", "semantic"} - set(skipped or []):
            require_model_keys()
        with self._mutex:
            if any(j.kind == "llm" for j in self.active()):
                raise IntakeError("LLM checks are already queued or running.")
            job = Job(
                job_id=uuid.uuid4().hex[:12], path=self.data_dir,
                names=["All library LLM checks"], kind="llm",
            )
            allowed = {s["id"] for s in job.steps if s["skippable"]}
            if set(skipped or []) - allowed:
                raise IntakeError("Unknown or required processing step.")
            for step in job.steps:
                if step["id"] in (skipped or []):
                    step.update(skipRequested=True, status="skipped")
        return self.scheduler.schedule(job)

    def submit_video(self, view_id: str) -> Job:
        from .config import get_settings
        from .contracts import ViewKind

        with self.scheduler.lock:
            reader = Store(self.db_path)
            try:
                view = reader.get_view(view_id)
                if view.kind is not ViewKind.VIDEO or view.meta.get("shipped"):
                    raise IntakeError("This item has no source video to analyze.")
            finally:
                reader.close()
            existing = next((j for j in self.active() if j.kind == "video" and j.view_ids == [view_id]), None)
            if existing:
                return existing
            if not get_settings().gemini_api_key:
                raise IntakeError("Set GEMINI_API_KEY to analyze videos.")
            job = Job(uuid.uuid4().hex[:12], self.data_dir, ["Video analysis"],
                      kind="video", view_ids=[view_id])
            return self.scheduler.schedule(job, [view_id])

    def submit_item_checks(self, view_id: str, checks: list[str] | None = None) -> Job:
        from .config import get_settings
        from .processing import applicable_checks

        with self.scheduler.lock:
            reader = Store(self.db_path)
            try:
                view = reader.get_view(view_id)
                if view.meta.get("shipped"):
                    raise IntakeError("This item has no source material to check.")
                allowed = {s["id"] for s in applicable_checks(view)}
                from .patterns.library import PatternLibraryStore
                version = PatternLibraryStore(self.data_dir / "patterns/library").latest_version()
                patterns_ready = bool(version and version.patterns)
            finally:
                reader.close()
            selected = set(allowed if checks is None else checks)
            if not patterns_ready:
                if checks is not None and "patterns" in selected:
                    raise IntakeError("Process more cases and discover patterns in Library first.")
                selected.discard("patterns")
            if not selected or selected - allowed:
                raise IntakeError("Choose applicable checks for this item.")
            existing = next((j for j in self.active() if j.kind == "item" and j.view_ids == [view_id]
                             and set(j.checks or []) == selected), None)
            if existing:
                return existing
            settings = get_settings()
            if selected - {"warnings"} and view.kind.value != "video" and not settings.openai_api_key:
                raise IntakeError("Set OPENAI_API_KEY to run these item checks.")
            if ("patterns" in selected or view.kind.value == "video" and selected - {"warnings"}) and not settings.gemini_api_key:
                raise IntakeError("Set GEMINI_API_KEY to run these item checks.")
            if selected & {"techniques", "patterns"} and not settings.openai_api_key:
                raise IntakeError("Set OPENAI_API_KEY to run these item checks.")
            job = Job(uuid.uuid4().hex[:12], self.data_dir, ["Item checks"], kind="item",
                      view_ids=[view_id], checks=sorted(selected))
            return self.scheduler.schedule(job, [view_id])

    def skip_llm_step(self, job_id: str, step_id: str) -> None:
        with self._mutex:
            job = self.jobs.get(job_id)
            if job is None or job.kind != "llm" or job.status not in ("queued", "running"):
                raise IntakeError("This processing run is no longer active.")
            step = next((s for s in job.steps if s["id"] == step_id), None)
            if (step is None or not step["skippable"]
                    or step["status"] not in ("pending", "running")
                    or (step["status"] == "running" and not step["itemwise"])):
                raise IntakeError("This step can no longer be skipped.")
            step["skipRequested"] = True
            if step["status"] == "pending":
                step["status"] = "skipped"
            for checks in self.item_checks.values():
                check = checks.get(step_id)
                if check and check.get("runId") == job_id:
                    if check["state"] == "queued":
                        check.update(state="skipped", message="Skipped in this run.", updatedAt=_now())
                    elif check["state"] == "running":
                        check.update(message="Stop requested; finishing the current item's check.")
            self._save_llm(job)

    def _enqueue(self, job: Job) -> Job:
        self.jobs[job.job_id] = job
        if job.kind == "llm":
            from .processing import applicable_checks, check_result

            reader = Store(self.db_path)
            try:
                for view in reader.views():
                    if view.meta.get("shipped"):
                        continue
                    observations = reader.observations(view.view_id)
                    checks = self.item_checks.setdefault(view.view_id, {})
                    for spec in applicable_checks(view):
                        name = spec["id"]
                        skipped = next(s for s in job.steps if s["id"] == name)["skipRequested"]
                        checks[name] = {
                            "state": "skipped" if skipped else "queued",
                            "message": "Excluded from this run." if skipped else "Waiting for this check.",
                            "runId": job.job_id, "updatedAt": _now(),
                            "hasResult": check_result(name, observations)["hasResult"]
                            or checks.get(name, {}).get("hasResult", False),
                        }
            finally:
                reader.close()
            self._save_llm(job)
        self.scheduler.persist_job(job)
        with self._mutex:
            self._queue.put(job.job_id)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, daemon=True, name="ftf-jobs")
                self._thread.start()
        return job

    def _save_llm(self, job: Job) -> None:
        from .llm_checks import result_issues

        job.issues = result_issues(job.results)
        payload = {
            "id": job.job_id, "status": job.status, "stage": job.stage_name,
            "error": job.error, "results": job.results.copy(), "issues": job.issues,
            "createdAt": job.created_at, "finishedAt": job.finished_at,
            "steps": [s.copy() for s in job.steps],
            "core": job.core.copy(),
        }
        if job.kind not in ("video", "item") and (not self.last_llm_run or job.created_at >= self.last_llm_run["createdAt"]):
            self.last_llm_run = payload
            path = self.data_dir / "llm-run.json"
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(self.last_llm_run, indent=2))
            temp.replace(path)
        path = self.data_dir / "item-checks.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.item_checks, indent=2))
        temp.replace(path)

    def item_checks_snapshot(self) -> dict:
        with self._mutex:
            return {view_id: {name: check.copy() for name, check in checks.items()}
                    for view_id, checks in self.item_checks.items()}

    def invalidate(self) -> None:
        self.generation += 1

    def _loop(self) -> None:
        while True:  # the thread lives as long as the app, so no submit can miss it
            job = self.jobs[self._queue.get()]
            if job.urls:
                # Acquisition must not hold up an already uploaded file submission.
                threading.Thread(target=self._capture_job, args=(job,), daemon=True,
                                 name="ftf-acquisition").start()
            else:
                self.run(job)

    def _capture_job(self, job: Job) -> None:
        with self._capture_slots:
            self.run(job)

    def run(self, job: Job) -> Job:
        """Run one job in the calling thread (the worker thread, or a test)."""
        store = Store(self.db_path)
        try:
            job.status = "running"
            if job.kind == "llm":
                from .llm_checks import run_llm_checks

                def on_llm_stage(name: str) -> None:
                    with self._mutex:
                        job.stage = name
                        step = next(s for s in job.steps if s["id"] == name)
                        if not step["skipRequested"]:
                            step["status"] = "running"
                        self._save_llm(job)

                def on_progress(name: str, done: int, total: int, current: str | None) -> None:
                    with self._mutex:
                        next(s for s in job.steps if s["id"] == name).update(
                            done=done, total=total, current=current,
                        )
                        self._save_llm(job)

                def on_complete(name: str, skipped: bool) -> None:
                    with self._mutex:
                        next(s for s in job.steps if s["id"] == name).update(
                            status="skipped" if skipped else "done", current=None,
                        )
                        self._save_llm(job)

                def on_item(name: str, view_id: str, result: dict) -> None:
                    with self._mutex:
                        checks = self.item_checks.setdefault(view_id, {})
                        previous = checks.get(name, {})
                        checks[name] = {
                            **result, "updatedAt": _now(), "runId": job.job_id,
                            "hasResult": result.get("hasResult", previous.get("hasResult", False)),
                        }
                        self._save_llm(job)

                run_llm_checks(
                    store, on_llm_stage, job.results,
                    should_skip=lambda name: next(s for s in job.steps if s["id"] == name)["skipRequested"],
                    on_progress=on_progress, on_complete=on_complete,
                    on_item=on_item,
                )
                job.status = "done"
                return job
            if job.urls:
                job.stage = "capture"
                case = self._link_target(job)
                for url in job.urls:
                    if job.recovered and any(
                        self._captured_url(manifest) == url for manifest in job.path.rglob("manifest.json")
                    ):
                        continue
                    self._capture(url, case / f"capture-{_host(url)}-{uuid.uuid4().hex[:6]}")

            # Register sources immediately. The persistent coordinator extracts each item,
            # starts model reads as dependencies become ready, and publishes core results.
            from .intake import ingest_new_material

            core_created_at = _now() if job.urls else job.created_at
            job.stage = "ingest"
            added = ingest_new_material(store, job.path, recovering=job.recovered)
            ids = [v.view_id for _, found in added for v in found]
            automatic = Job(uuid.uuid4().hex[:12], self.data_dir, ["Automatic analysis"],
                            kind="llm", created_at=core_created_at,
                            view_ids=ids)
            existing = next((j for j in self.jobs.values() if j.kind == "llm" and j.view_ids == ids), None)
            if not (job.recovered and existing):
                self.scheduler.schedule(automatic, ids)
            job.incidents = sorted(incident.incident_id for incident, _ in added)
            record_cases(self.data_dir, [i for i in job.incidents])
            job.status = "done"
        except IntakeError as exc:
            job.status, job.error = "failed", str(exc)
        except Exception as exc:  # noqa: BLE001 - the person sees the failure, the app stays up
            job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"
        finally:
            store.close()
            job.finished_at = _now()
            self.generation += 1
            if job.kind == "material":
                self.scheduler.persist_job(job)
            if job.kind == "llm":
                for checks in self.item_checks.values():
                    for check in checks.values():
                        if check.get("runId") == job.job_id and check["state"] in ("queued", "running"):
                            check.update(state="failed" if job.status == "failed" else "unavailable",
                                         message="Processing stopped before this check finished." if job.status == "failed"
                                         else "No result was recorded for this check.")
                for step in job.steps:
                    if step["status"] == "running":
                        step.update(status="failed", current=None)
                self._save_llm(job)
        return job

    @staticmethod
    def _captured_url(manifest: Path) -> str | None:
        try:
            return json.loads(manifest.read_text()).get("page_url")
        except (OSError, ValueError):
            return None

    @staticmethod
    def _link_target(job: Job) -> Path:
        """The case folder links are captured into."""
        if not is_collection(job.path):  # one case: links join it, so funnels can be found
            return job.path
        cases = [p for p in job.path.iterdir() if p.is_dir()]
        if len(cases) == 1 and not any(cases[0].iterdir()):  # links only (`link_case`)
            return cases[0]
        # Several cases already: the links form a case of their own.
        case = job.path / f"{_host(job.urls[0])}-{uuid.uuid4().hex[:8]}"
        case.mkdir()
        return case

    def _capture(self, url: str, target: Path) -> None:
        try:
            self.capture(url, target)
        except Exception as exc:
            raise IntakeError(f"couldn't capture {url}: {exc}") from exc


_WORKERS: dict[str, Worker] = {}
_WORKERS_LOCK = threading.Lock()


def worker_for(db_path: Path, data_dir: Path) -> Worker:
    """The process-wide worker for one data folder, shared by every browser session."""
    with _WORKERS_LOCK:
        key = str(Path(db_path).resolve())
        if key not in _WORKERS:
            _WORKERS[key] = Worker(db_path, data_dir)
            for job in list(_WORKERS[key].jobs.values()):
                if job.kind == "material" and job.status in ("queued", "running"):
                    job.status, job.recovered = "queued", True
                    _WORKERS[key]._enqueue(job)
            if any(j.status in ("queued", "running") for j in _WORKERS[key].jobs.values()):
                _WORKERS[key].scheduler.start()
        return _WORKERS[key]
