"""Persistent, prioritized processing. Only the coordinator writes to the live store.

Provider workers deserialize a SQLite snapshot into their own in-memory connection. Their
validated output is committed only if the inputs still match. No network wait holds the
analysis lock. The small corpus still uses full, two-pass fusion at publication boundaries.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import threading
import time
import uuid
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from . import pipeline
from .config import get_settings
from .contracts import FingerprintKind, ViewKind
from .fusion import run_method
from .intake import ANALYSIS_LOCK, IntakeError
from .models.gateway import request_budget
from .processing import CHECK_EXTRACTORS, check_result
from .registry import Context, extractors, layers, load_plugins
from .store import Store

log = logging.getLogger(__name__)
CORE_SECONDS = 300
READ_SECONDS = 45
PUBLISH_SECONDS = 2
TERMINAL = {"complete", "unavailable", "failed", "skipped", "cancelled"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS processing_runs (id TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS processing_tasks (id TEXT PRIMARY KEY, body TEXT NOT NULL);
"""
STEPS = {
    "local": ("Collect local clues", "core"),
    "enrich": ("Refresh domain information", "additional"),
    "semantic": ("Build semantic fingerprints", "additional"),
    "match": ("Match existing patterns", "additional"),
    "previews": ("Prepare pictures", "additional"),
}


def now():
    return datetime.now(UTC).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def source_key(store, ids):
    """Uploaded sources are immutable; also detect edits to primary/sidecar files."""
    result = []
    for vid in ids:
        view = store.get_view(vid)
        root = Path(store.get_incident(view.incident_id).root)
        files = []
        for name in dict.fromkeys([view.primary, view.har, *view.attachments]):
            if not name:
                continue
            path = root / name
            try:
                with path.open("rb") as stream:
                    value = hashlib.file_digest(stream, "sha256").hexdigest()
            except OSError:
                value = "missing"
            files.append((name, value))
        # Expectation canonicalization can change page.kind observations, never the source.
        result.append(
            (
                view.model_dump(mode="json"),
                files,
                [(a.path, a.sha256) for a in store.artifacts(view.incident_id)],
            )
        )
    return result


def observation_map(store):
    rows = defaultdict(list)
    for vid, ext, body in store.db.execute("SELECT view_id,extractor,body FROM observations"):
        rows[(vid, ext)].append(body)
    return {key: sorted(value) for key, value in rows.items()}


def signature(store, task, settings):
    stage, ids = task["stage"], task["views"]
    versions = []
    if stage == "local":
        from .extract.video_file import VERSION

        versions = [(e.name, e.version) for e in extractors.all() if e.name != "video.media"]
        versions.append(("video.file", VERSION))
        inputs = []
    else:
        modules = {
            "image": "perception.image",
            "page": "perception.page",
            "video": "perception.video",
            "techniques": "perception.techniques",
            "expectations": "perception.expectations",
            "faces": "perception.faces",
            "match": "patterns.matching",
            "patterns": "patterns.induction",
        }
        if stage in modules:
            module = importlib.import_module(f"ftf.{modules[stage]}")
            versions = [getattr(module, "VERSION", ""), getattr(module, "PROMPT_VERSION", "")]
        own = CHECK_EXTRACTORS.get(
            stage, "perception.pattern" if stage in ("match", "patterns") else ""
        )
        inputs = []
        for vid in ids:
            obs = store.observations(vid)
            if stage in ("page", "image", "video"):
                obs = [o for o in obs if not o.extractor.startswith(("perception.", "enrich."))]
            elif stage == "enrich":
                obs = [o for o in obs if not o.extractor.startswith("enrich.")]
            elif stage == "techniques":
                obs = [
                    o
                    for o in obs
                    if o.extractor not in (own, "perception.pattern", "perception.expectations")
                ]
            else:
                obs = [o for o in obs if o.extractor != own]
            inputs.extend(sorted(o.model_dump_json() for o in obs))
    extras = []
    if stage in ("match", "patterns"):
        from .patterns.library import PatternLibraryStore

        version = PatternLibraryStore(settings.data_dir / "patterns/library").latest_version()
        if stage == "match":
            extras.append(version.version_id if version else "no_library")
        else:
            # A discovery is invalidated by new cards or campaign assignments, not by itself.
            inputs = sorted(
                o.model_dump_json()
                for v in store.views()
                for o in store.observations(v.view_id)
                if o.extractor == "perception.technique"
            )
            extras.append(
                [
                    (r.view_a, r.view_b, r.type.value)
                    for r in store.relations()
                    if r.type.value in ("same_campaign", "duplicate")
                ]
            )
    if stage == "enrich":
        extras.extend([settings.enrich_mode, datetime.now(UTC).date().isoformat()])
    if stage == "faces":
        # A face check reads only the source files and the reference list.
        from .faces.enroll import MANIFEST

        inputs = []
        extras.append(digest(MANIFEST.read_text(encoding="utf-8")))
    if stage == "expectations":
        grounding = settings.data_dir / "expectations/grounding.json"
        extras.append(grounding.read_text() if grounding.is_file() else "")
    return digest(
        [
            "scheduler-v1",
            stage,
            source_key(store, ids),
            versions,
            inputs,
            extras,
            settings.openai_model,
            settings.embedding_model,
            settings.video_model,
        ]
    )


def compute(snapshot: bytes, task: dict, settings, seconds: float):
    """All computation happens against a private store; return only changed output."""
    store = Store(":memory:")
    store.db.deserialize(snapshot)
    before = observation_map(store)
    fps_before = {(f.view_id, f.layer_id): f.model_dump_json() for f in store.fingerprints()}
    metrics = {}
    started = time.monotonic()
    stage, ids = task["stage"], task["views"]
    ctx = Context(store)
    try:
        with request_budget(seconds, metrics):
            if stage == "local":
                summary = {"observations": pipeline.run_extract(store, ids, fast=True)}
                if any(
                    store.get_view(vid).kind is ViewKind.VIDEO
                    and (
                        not store.get_view(vid).primary
                        or not ctx.path(store.get_view(vid), store.get_view(vid).primary).is_file()
                    )
                    for vid in ids
                ):
                    summary["reason"] = "no_file"
            elif stage in ("image", "page", "video", "techniques", "expectations"):
                module, function = {
                    "image": ("image", "run_perceive"),
                    "page": ("page", "run_perceive_pages"),
                    "video": ("video", "run_perceive_videos"),
                    "techniques": ("techniques", "run_techniques"),
                    "expectations": ("expectations", "run_expectations"),
                }[stage]
                if stage == "video":
                    # Re-encoded-video matching is part of this explicit action too.
                    pipeline.run_extract(store, ids)
                summary = getattr(importlib.import_module(f"ftf.perception.{module}"), function)(
                    ctx, ids, settings=settings
                )
            elif stage == "faces":
                from .perception.faces import run_face_check

                summary = run_face_check(ctx, ids)
            elif stage == "enrich":
                summary = {"observations": sum(pipeline.run_enrich(store, ids).values())}
                summary["not_observable"] = sum(
                    o.state.value == "not_observable"
                    for vid in ids
                    for o in store.observations(vid)
                    if o.extractor.startswith("enrich.") and o.extractor != "enrich.warning_lists"
                )
            elif stage == "warnings":
                from .enrich.warning_lists import run_warning_lists
                from .enrich.warning_sources import load_sources
                sources = load_sources(settings.data_dir / "warning_lists", fetch_missing=settings.enrich_mode != "off")
                summary = run_warning_lists(store, settings.data_dir / "warning_lists", view_ids=ids, sources=sources)
            elif stage == "semantic":
                from .layers._embedder import FakeEmbedder, GatewayEmbedder, using_embedder

                embedder = (
                    FakeEmbedder()
                    if any(v.meta.get("shipped") for v in store.views())
                    else GatewayEmbedder(settings.embedding_model, settings)
                )
                with using_embedder(embedder):
                    summary = {"fingerprints": pipeline.run_fingerprint(store, ids)}
                if isinstance(embedder, GatewayEmbedder) and embedder.failures:
                    summary["reason"] = str(dict(embedder.failures))
            elif stage in ("match", "patterns"):
                from .patterns.workflow import run_discover, run_match

                summary = (run_match if stage == "match" else run_discover)(
                    ctx, ids, settings=settings
                )
            elif stage == "previews":
                from .previews import picture, render_missing, saved_page

                summary = {"pictures": render_missing(store, settings.data_dir)}
                summary["not_observable"] = sum(
                    bool(
                        saved_page(
                            store.get_view(vid),
                            Path(store.get_incident(store.get_view(vid).incident_id).root),
                        )
                    )
                    and not picture(
                        store.get_view(vid),
                        Path(store.get_incident(store.get_view(vid).incident_id).root),
                        settings.data_dir,
                    )
                    for vid in ids
                )
            else:
                raise ValueError(f"Unknown task: {stage}")
        after = observation_map(store)
        changes = {key: value for key, value in after.items() if before.get(key, []) != value}
        changes.update({key: [] for key in before.keys() - after.keys()})
        fps = [
            f
            for f in store.fingerprints()
            if fps_before.get((f.view_id, f.layer_id)) != f.model_dump_json()
            and stage == "semantic"
            and layers.get(f.layer_id).kind is FingerprintKind.VECTOR
        ]
        metrics["executionSeconds"] = round(time.monotonic() - started, 4)
        return changes, fps, summary, metrics, {key: before.get(key, []) for key in changes}
    finally:
        store.close()


class Scheduler:
    def __init__(self, worker):
        self.worker = worker
        self.lock = threading.RLock()
        self.tasks = {}
        self.futures: dict[str, tuple[Future, float]] = {}
        self.max_workers = max(1, min(32, int(os.getenv("FTF_MODEL_CONCURRENCY", "8"))))
        self.capacity = self.max_workers
        self.pool = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="ftf-read")
        self._thread = None
        self._stop = threading.Event()
        self.dirty = set()
        self.last_publish = 0.0
        self.successes = 0
        self.not_before = 0.0
        load_plugins()
        store = Store(worker.db_path)
        try:
            store.db.executescript(SCHEMA)
            from .jobs import Job

            for _, body in store.db.execute("SELECT id,body FROM processing_runs"):
                data = json.loads(body)
                data["path"] = Path(data["path"])
                job = Job(**data)
                self.worker.jobs[job.job_id] = job
            for tid, body in store.db.execute("SELECT id,body FROM processing_tasks"):
                task = json.loads(body)
                if task["state"] == "running":
                    task["state"] = "queued"
                    task["recovered"] = True
                self.tasks[tid] = task
        finally:
            store.close()

    def start(self):
        with self.lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._loop, daemon=True, name="ftf-coordinator")
            self._thread.start()

    def close(self, *, wait=False):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        self.pool.shutdown(wait=wait, cancel_futures=True)

    def _save(self, store, task=None, job=None):
        with store.db:
            if task:
                store.db.execute(
                    "INSERT OR REPLACE INTO processing_tasks VALUES (?,?)",
                    (task["id"], json.dumps(task)),
                )
            if job:
                data = asdict(job)
                data["path"] = str(data["path"])
                store.db.execute(
                    "INSERT OR REPLACE INTO processing_runs VALUES (?,?)",
                    (job.job_id, json.dumps(data)),
                )

    def persist_job(self, job):
        with ANALYSIS_LOCK:
            store = Store(self.worker.db_path)
            try:
                self._save(store, job=job)
            finally:
                store.close()

    def schedule(self, job, ids=None, *, include_additional=True):
        with self.lock, ANALYSIS_LOCK:
            store = Store(self.worker.db_path)
            try:
                source = [
                    v
                    for v in store.views()
                    if not v.meta.get("shipped") and (ids is None or v.view_id in ids)
                ]
                # Round-robin cases: every case gets a representative before its next item.
                cases = defaultdict(list)
                for v in source:
                    cases[v.incident_id].append(v)
                ordered = [
                    group[i]
                    for i in range(max(map(len, cases.values()), default=0))
                    for group in cases.values()
                    if i < len(group)
                ]
                self.worker.jobs[job.job_id] = job
                job.core = {
                    "status": "queued",
                    "total": len(source),
                    "done": 0,
                    "targetSeconds": None if job.kind in ("video", "item") else CORE_SECONDS,
                    "startedAt": job.created_at,
                    "finishedAt": None,
                    "targetMet": None,
                }
                for name, (label, phase) in STEPS.items():
                    if not any(s["id"] == name for s in job.steps):
                        job.steps.append(
                            {
                                "id": name,
                                "label": label,
                                "description": label,
                                "phase": phase,
                                "skippable": phase != "core",
                                "itemwise": True,
                                "status": "pending",
                                "done": 0,
                                "total": None,
                                "current": None,
                                "skipRequested": False,
                            }
                        )

                def add(stage, selected, deps=(), phase="additional", priority=10):
                    tid = uuid.uuid4().hex
                    skipped = (phase == "additional" and not include_additional) or any(
                        (s["id"] == stage or stage == "match" and s["id"] == "patterns")
                        and s["skipRequested"]
                        for s in job.steps
                    )
                    if job.kind == "item":
                        requested = set(job.checks or [])
                        needed = requested | {"local"}
                        if requested & {"expectations", "techniques", "patterns", "image", "page", "video"}:
                            needed.add(source[0].kind.value)
                        skipped = stage not in needed and not (stage == "match" and "patterns" in requested)
                        if stage == "patterns":
                            skipped = True  # matching is item-scoped; induction needs independent campaigns
                    task = {
                        "id": tid,
                        "run": job.job_id,
                        "stage": stage,
                        "views": selected,
                        "deps": list(deps),
                        "phase": phase,
                        "priority": priority,
                        "state": "skipped" if skipped else "queued",
                        "createdAt": now(),
                        "result": {},
                        "metrics": {},
                    }
                    self.tasks[tid] = task
                    self._save(store, task=task)
                    return tid

                reads, techniques = [], []
                for v in ordered:
                    local = add("local", [v.view_id], phase="core", priority=0)
                    if v.kind is ViewKind.VIDEO and job.kind not in ("video", "item"):
                        for stage in ("video", "techniques", "faces", "patterns"):
                            self.worker.item_checks.setdefault(v.view_id, {}).setdefault(
                                stage,
                                {
                                    "state": "on_demand",
                                    "message": "Run Analyze video on this item's page.",
                                    "hasResult": check_result(stage, store.observations(v.view_id))[
                                        "hasResult"
                                    ],
                                },
                            )
                        continue
                    stage = v.kind.value
                    read = add(stage, [v.view_id], [local], phase="core", priority=1)
                    reads.append(read)
                    if job.kind == "item":
                        add("warnings", [v.view_id], [read], priority=3)
                    techniques.append(add("techniques", [v.view_id], [read], priority=12))
                    add("faces", [v.view_id], [local], priority=11)
                    add("enrich", [v.view_id], [read], priority=15)
                selected = [
                    v.view_id
                    for v in ordered
                    if v.kind is not ViewKind.VIDEO or job.kind in ("video", "item")
                ]
                pages = [v.view_id for v in ordered if v.kind is ViewKind.PAGE]
                if selected:
                    add("semantic", selected, reads, priority=14)
                    add("match", selected, reads, priority=10)
                    add("patterns", selected, [*reads, *techniques], priority=20)
                if pages:
                    add("expectations", pages, reads, priority=13)
                    add("previews", pages, priority=30)
                self._save(store, job=job)
                self._report(store, job)
            finally:
                store.close()
        self.start()
        return job

    def cancel(self, job_id):
        with self.lock, ANALYSIS_LOCK:
            job = self.worker.jobs.get(job_id)
            if not job or job.kind != "video":
                raise IntakeError("No such video analysis.")
            store = Store(self.worker.db_path)
            try:
                for task in self.tasks.values():
                    if task["run"] == job_id and task["state"] not in TERMINAL:
                        task.update(state="cancelled", finishedAt=now())
                        self._save(store, task=task)
                job.status = "done"
                self._report(store, job)
            finally:
                store.close()

    def _loop(self):
        while not self._stop.wait(0.05):
            try:
                self.tick()
            except Exception:
                log.exception("processing coordinator failed")
                self._stop.wait(0.5)

    def tick(self):
        with self.lock:
            store = Store(self.worker.db_path)
            try:
                settings = replace(get_settings(), data_dir=self.worker.data_dir)
                for tid, (future, started) in list(self.futures.items()):
                    if not future.done():
                        continue
                    del self.futures[tid]
                    task = self.tasks[tid]
                    if task["state"] == "cancelled":
                        continue
                    with ANALYSIS_LOCK:
                        try:
                            changes, fps, result, metrics, expected = future.result()
                            if signature(store, task, settings) != task["signature"]:
                                task.update(
                                    state="queued",
                                    message="Inputs changed; checking the current version.",
                                )
                                self._save(store, task=task)
                                continue
                            if any(
                                sorted(
                                    row[0]
                                    for row in store.db.execute(
                                        "SELECT body FROM observations WHERE view_id=? AND extractor=?",
                                        key,
                                    )
                                )
                                != value
                                for key, value in expected.items()
                            ):
                                task.update(
                                    state="queued",
                                    message="Newer output arrived; retaining it and checking current inputs.",
                                )
                                self._save(store, task=task)
                                continue
                            from .contracts import Observation

                            for (vid, ext), rows in changes.items():
                                store.put_observations(
                                    vid, ext, [Observation.model_validate_json(row) for row in rows]
                                )
                                with store.db:
                                    for fp in store.fingerprints(view_id=vid):
                                        if layers.get(fp.layer_id).kind is FingerprintKind.VECTOR:
                                            store.db.execute(
                                                "DELETE FROM fingerprints WHERE view_id=? AND layer_id=?",
                                                (vid, fp.layer_id),
                                            )
                                self.dirty.add(vid)
                            for fp in fps:
                                store.put_fingerprint(fp)
                                self.dirty.add(fp.view_id)
                            failed = bool(
                                result.get("reason")
                                or result.get("not_observable")
                                or result.get("kept")
                                or result.get("errors")
                                or any(
                                    result.get("matching", {}).get(k)
                                    for k in ("reason", "not_observable", "kept")
                                )
                            )
                            reasons = {
                                json.loads(row).get("raw")
                                for rows in changes.values()
                                for row in rows
                                if json.loads(row).get("state") == "not_observable"
                            }
                            if failed and reasons:
                                result.setdefault(
                                    "reason", next(iter(sorted(r for r in reasons if r)), "error")
                                )
                            metrics["queueSeconds"] = task["metrics"].get("queueSeconds", 0)
                            combined_metrics = {
                                k: task["metrics"].get(k, 0) + v for k, v in metrics.items()
                            }
                            combined_metrics["queueSeconds"] = metrics["queueSeconds"]
                            task.update(
                                state="unavailable" if failed else "complete",
                                result=result,
                                metrics=combined_metrics,
                                finishedAt=now(),
                                output=self._output(store, task),
                            )
                            if (
                                failed
                                and task["phase"] == "core"
                                and not task.get("deferred")
                                and (
                                    reasons & {"timeout", "rate_limit", "error", "schema"}
                                    or metrics.get("rateLimits")
                                    or any(
                                        metrics.get(f"failures.{why}")
                                        for why in ("timeout", "rate_limit", "error", "schema")
                                    )
                                )
                            ):
                                task.update(
                                    state="queued",
                                    phase="additional",
                                    priority=9,
                                    deferred=True,
                                    message="The provider could not finish the core read; retrying in the background.",
                                )
                            if metrics.get("rateLimits"):
                                self.capacity = max(1, self.capacity // 2)
                                self.successes = 0
                                self.not_before = max(
                                    self.not_before,
                                    time.monotonic() + min(60, metrics.get("retryAfterSeconds", 2)),
                                )
                            elif not failed:
                                self.successes += 1
                                if self.successes >= self.capacity:
                                    self.capacity = min(self.max_workers, self.capacity + 1)
                                    self.successes = 0
                            if task["stage"] == "previews" and result.get("pictures"):
                                self.worker.invalidate()
                        except Exception as exc:
                            log.exception("processing task %s failed", task["stage"])
                            task.update(
                                state="failed",
                                result={"reason": "error"},
                                message=f"{type(exc).__name__}: {exc}",
                                finishedAt=now(),
                            )
                        self._save(store, task=task)
                # Resolve core milestones before scheduling background work.
                for task in self.tasks.values():
                    job = self.worker.jobs[task["run"]]
                    elapsed = (
                        datetime.now(UTC) - datetime.fromisoformat(job.created_at)
                    ).total_seconds()
                    if (
                        task["phase"] == "core"
                        and task["stage"] != "local"
                        and task["state"] not in TERMINAL
                        and job.kind not in ("video", "item")
                        and elapsed >= CORE_SECONDS - 15
                    ):
                        task.update(
                            phase="additional",
                            priority=9,
                            deferred=True,
                            message="Continuing after the core deadline; coverage is incomplete.",
                        )
                        self._save(store, task=task)
                core_pending = any(
                    t["phase"] == "core" and t["state"] not in TERMINAL for t in self.tasks.values()
                )
                eligible = [
                    t
                    for t in self.tasks.values()
                    if t["state"] == "queued"
                    and all(self.tasks[d]["state"] in TERMINAL for d in t["deps"])
                ]
                eligible.sort(key=lambda t: (t["priority"], t["createdAt"]))
                local_ran = False
                for task in eligible:
                    job = self.worker.jobs[task["run"]]
                    step = next((s for s in job.steps if s["id"] == task["stage"]), None)
                    if step and step["skipRequested"]:
                        task.update(state="skipped", finishedAt=now())
                        self._save(store, task=task)
                        continue
                    if task["phase"] != "core" and core_pending:
                        continue
                    if task["stage"] == "local" and local_ran:
                        continue
                    if task["stage"] != "local" and time.monotonic() < self.not_before:
                        continue
                    if task["stage"] != "local" and len(self.futures) >= (
                        self.capacity if core_pending else min(2, self.capacity)
                    ):
                        continue
                    keys = (
                        ["gemini_api_key"]
                        if task["stage"] in ("semantic", "video")
                        else ["openai_api_key"]
                    )
                    if task["stage"] in ("match", "patterns"):
                        keys.append("gemini_api_key")
                    if task["stage"] not in ("local", "enrich", "previews", "warnings", "faces") and any(
                        not getattr(settings, key) for key in keys
                    ):
                        task.update(
                            state="unavailable", result={"reason": "no_key"}, finishedAt=now()
                        )
                        self._save(store, task=task)
                        continue
                    with ANALYSIS_LOCK:
                        task["signature"] = signature(store, task, settings)
                        reuse = next(
                            (
                                t
                                for t in reversed(list(self.tasks.values()))
                                if t["id"] != task["id"]
                                and t["state"] == "complete"
                                and t["stage"] == task["stage"]
                                and t["views"] == task["views"]
                                and t.get("signature") == task["signature"]
                                and t.get("output") == self._output(store, task)
                            ),
                            None,
                        )
                        if job.kind == "item" and (task["stage"] in (job.checks or []) or task["stage"] == "match"):
                            reuse = None
                        if reuse:
                            task.update(
                                state="complete",
                                result=reuse["result"],
                                metrics={"reusedTasks": 1},
                                finishedAt=now(),
                                output=reuse["output"],
                            )
                            self._save(store, task=task)
                            continue
                        task.update(state="running", startedAt=now())
                        task["metrics"]["queueSeconds"] = (
                            datetime.now(UTC) - datetime.fromisoformat(job.created_at)
                        ).total_seconds()
                        snapshot = store.db.serialize()
                        self._save(store, task=task)
                    elapsed = (
                        datetime.now(UTC) - datetime.fromisoformat(job.created_at)
                    ).total_seconds()
                    if task["phase"] == "core" and job.kind not in ("video", "item") and task["stage"] != "local":
                        budget = min(READ_SECONDS, CORE_SECONDS - 15 - elapsed)
                        if budget <= 0:
                            task.update(
                                state="queued",
                                phase="additional",
                                priority=9,
                                deferred=True,
                                message="Deferred to keep the core analysis within five minutes.",
                            )
                            self._save(store, task=task)
                            continue
                    else:
                        budget = (
                            900
                            if task["stage"] == "video"
                            else max(120, 180 * len(task["views"]))
                            if task["stage"] in ("semantic", "expectations", "patterns", "match")
                            else 120
                        )
                    if task["stage"] == "local":
                        # Local work is short and serialized; model work starts after each item.
                        future = Future()
                        try:
                            future.set_result(compute(snapshot, task, settings, budget))
                        except Exception as exc:  # noqa: BLE001 - report local extraction failures
                            future.set_exception(exc)
                        local_ran = True
                    else:
                        future = self.pool.submit(compute, snapshot, task.copy(), settings, budget)
                    self.futures[task["id"]] = future, time.monotonic()
                if self.dirty and time.monotonic() - self.last_publish >= PUBLISH_SECONDS:
                    self._publish(store)
                for job in list(self.worker.jobs.values()):
                    if job.status in ("queued", "running") and any(
                        t["run"] == job.job_id for t in self.tasks.values()
                    ):
                        self._report(store, job)
            finally:
                store.close()

    def _output(self, store, task):
        stage = task["stage"]
        if stage == "local":
            names = {e.name for e in extractors.all() if e.name != "video.media"}
            names.add("video.file")
        else:
            names = {
                CHECK_EXTRACTORS.get(
                    stage, "perception.pattern" if stage in ("match", "patterns") else ""
                )
            }
        if stage == "semantic":
            return digest(
                [
                    f.model_dump_json()
                    for vid in task["views"]
                    for f in store.fingerprints(view_id=vid)
                    if layers.get(f.layer_id).kind is FingerprintKind.VECTOR
                ]
            )
        if stage == "enrich":
            return digest(
                [
                    o.model_dump_json()
                    for vid in task["views"]
                    for o in store.observations(vid)
                    if o.extractor.startswith("enrich.")
                ]
            )
        return digest(
            sorted(
                o.model_dump_json()
                for vid in task["views"]
                for o in store.observations(vid)
                if o.extractor in names
            )
        )

    def _publish(self, store):
        started = time.monotonic()
        with ANALYSIS_LOCK:
            ids = list(self.dirty)
            from .enrich.warning_lists import run_warning_lists

            warning_ids = []
            for vid in ids:
                owning = [
                    self.worker.jobs[t["run"]] for t in self.tasks.values() if vid in t["views"]
                ]
                latest = max(owning, key=lambda j: j.created_at, default=None)
                if not latest or not any(
                    s["id"] == "warnings" and s["skipRequested"] for s in latest.steps
                ):
                    warning_ids.append(vid)
            warning_result = run_warning_lists(
                store, self.worker.data_dir / "warning_lists", view_ids=warning_ids
            )
            pipeline.run_fingerprint(store, ids, local_only=True)
            pipeline.run_cloaking(store)
            # Replace derived pair rows so a vanished match cannot leave stale evidence.
            with store.batch():
                store.db.execute("DELETE FROM evidence")
                store.db.execute("DELETE FROM relations")
                run_method(store)
            self.worker.invalidate()
            self.dirty.clear()
            self.last_publish = time.monotonic()
        for job in self.worker.jobs.values():
            if job.status in ("running", "queued"):
                job.results["warnings"] = warning_result
                job.results.setdefault("publication", {}).update(
                    seconds=round(time.monotonic() - started, 4), publishedAt=now()
                )
                job.results["publication"].setdefault("firstAt", now())

    def _report(self, store, job):
        tasks = [t for t in self.tasks.values() if t["run"] == job.job_id]
        if not tasks:
            return
        elapsed_now = (datetime.now(UTC) - datetime.fromisoformat(job.created_at)).total_seconds()
        report_key = digest(
            [
                (t["id"], t["state"], t.get("deferred"), t.get("metrics"), t.get("signature"))
                for t in tasks
            ]
            + [self.worker.generation, elapsed_now >= CORE_SECONDS]
        )
        if job.results.get("reportKey") == report_key:
            return
        job.results["reportKey"] = report_key
        job.status = "running"
        for task in tasks:
            if job.kind == "item" and task["state"] == "skipped":
                continue
            stage = task["stage"]
            if stage in CHECK_EXTRACTORS or stage == "match":
                for vid in task["views"]:
                    key = "patterns" if stage == "match" else stage
                    previous = self.worker.item_checks.get(vid, {}).get(key, {})
                    previous_job = self.worker.jobs.get(previous.get("runId"))
                    if previous_job and previous_job.created_at > job.created_at:
                        continue
                    obs = store.observations(vid)
                    result = check_result(key, obs, task.get("result"))
                    coverage_state = result["state"]
                    state = task["state"]
                    if state != "complete":
                        result.update(
                            state="skipped"
                            if state == "cancelled"
                            else "deferred"
                            if task.get("deferred") and state in ("queued", "running")
                            else state,
                            message=task.get("message")
                            or {
                                "queued": "Waiting for dependencies.",
                                "running": "Checking this item.",
                                "skipped": "Skipped in this run.",
                                "cancelled": "Analysis cancelled; earlier results remain available.",
                            }.get(state, result["message"]),
                        )
                        if state == "unavailable" and coverage_state == "partial":
                            result.update(state="partial")
                        elif state == "unavailable" and result["hasResult"]:
                            result.update(
                                state="retained",
                                message="Check could not finish. Try again.",
                            )
                    result.update(
                        runId=job.job_id,
                        updatedAt=task.get("finishedAt", task.get("startedAt", task["createdAt"])),
                    )
                    self.worker.item_checks.setdefault(vid, {})[key] = result
        for step in job.steps:
            selected = [t for t in tasks if t["stage"] == step["id"]]
            if not selected:
                if step["id"] == "warnings" and step["skipRequested"]:
                    step.update(status="skipped", total=0, done=0)
                    for vid in {vid for t in tasks for vid in t["views"]}:
                        self.worker.item_checks.setdefault(vid, {})["warnings"] = {
                            "state": "skipped",
                            "message": "Skipped in this run; earlier results remain available.",
                            "hasResult": check_result("warnings", store.observations(vid))[
                                "hasResult"
                            ],
                            "runId": job.job_id,
                            "updatedAt": now(),
                        }
                    continue
                if step["id"] in ("rebuild", "finalize"):
                    done = (
                        bool(job.core.get("finishedAt"))
                        if step["id"] == "rebuild"
                        else all(t["state"] in TERMINAL for t in tasks)
                    )
                    step.update(status="done" if done else "pending", total=1, done=int(done))
                else:
                    step.update(
                        status="skipped" if step["id"] == "video" else "done", total=0, done=0
                    )
                continue
            done = sum(t["state"] in TERMINAL for t in selected)
            step.update(
                done=done,
                total=len(selected),
                current=None,
                status="skipped"
                if all(t["state"] in ("skipped", "cancelled") for t in selected)
                else "done"
                if done == len(selected)
                else "running"
                if any(t["state"] == "running" for t in selected)
                else "pending",
            )
            summary = {}
            for t in selected:
                for k, v in t.get("result", {}).items():
                    if isinstance(v, int):
                        summary[k] = summary.get(k, 0) + v
                    elif k in ("reason", "matching"):
                        summary[k] = v
            if any(t["state"] in ("failed", "unavailable") for t in selected):
                summary["unavailable"] = sum(
                    t["state"] in ("failed", "unavailable") for t in selected
                )
            if any(t.get("deferred") and t["state"] != "complete" for t in selected):
                summary["deferred"] = True
            if all(t["state"] in ("skipped", "cancelled") for t in selected):
                summary["skipped"] = True
            job.results[step["id"]] = summary
        core = [t for t in tasks if t["phase"] == "core" or t.get("deferred")]
        resolved = all(t["state"] in TERMINAL for t in core) or (
            job.kind not in ("video", "item") and elapsed_now >= CORE_SECONDS
        )
        if resolved and not job.core.get("finishedAt"):
            if self.dirty:
                self._publish(store)
            finished = now()
            elapsed = (
                datetime.fromisoformat(finished) - datetime.fromisoformat(job.created_at)
            ).total_seconds()
            complete = all(t["state"] == "complete" for t in core)
            job.core.update(
                status="complete"
                if complete and (elapsed <= CORE_SECONDS or job.kind in ("video", "item"))
                else "partial"
                if elapsed <= CORE_SECONDS
                else "missed",
                targetMet=None if job.kind in ("video", "item") else complete and elapsed <= CORE_SECONDS,
                finishedAt=finished,
                elapsedSeconds=round(elapsed, 3),
            )
        if not job.core.get("finishedAt"):
            job.core["status"] = "running"
        job.core["done"] = sum(
            all(t["state"] == "complete" for t in core if vid in t["views"])
            for vid in {vid for t in core for vid in t["views"]}
        )
        active = next((t for t in tasks if t["state"] == "running"), None)
        job.stage = active["stage"] if active else "finalize"
        if all(t["state"] in TERMINAL for t in tasks):
            if self.dirty:
                self._publish(store)
            job.status, job.finished_at = "done", now()
        for step in job.steps:
            if (
                step["id"] == "rebuild"
                and job.core.get("finishedAt")
                or step["id"] == "finalize"
                and job.status == "done"
            ):
                step.update(status="done", total=1, done=1)
        job.results["timing"] = {
            "tasks": [
                {
                    "stage": t["stage"],
                    "views": t["views"],
                    "state": t["state"],
                    **t.get("metrics", {}),
                }
                for t in tasks
            ],
            "concurrency": self.capacity,
        }
        with self.worker._mutex:
            self.worker._save_llm(job)
        self._save(store, job=job)
