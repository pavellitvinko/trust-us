"""Concurrency, recovery, deadline and incremental contracts without paid calls."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image

from ftf.contracts import Incident, Observation, Purpose, State, View, ViewKind
from ftf.intake import ANALYSIS_LOCK
from ftf.jobs import Job, Worker
from ftf.models.gateway import cached_call, request_budget
from ftf.store import Store


@pytest.fixture
def worker(tmp_path, monkeypatch):
    monkeypatch.setenv("FTF_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("FTF_EMBEDDER", "fake")
    # Explicit ticks make scheduling assertions independent of thread timing.
    monkeypatch.setattr("ftf.scheduler.Scheduler.start", lambda self: None)
    monkeypatch.setattr("ftf.previews.render_missing", lambda *a: 0)
    instance = Worker(tmp_path / "ftf.sqlite", tmp_path)
    store = Store(instance.db_path)
    for i in range(3):
        path = tmp_path / f"case-{i}"
        path.mkdir()
        Image.new("RGB", (32, 32), (i * 80, 10, 40)).save(path / "shot.png")
        store.put_incident(
            Incident(incident_id=str(i), root=str(path)),
            [],
            [
                View(
                    view_id=str(i),
                    incident_id=str(i),
                    kind=ViewKind.IMAGE,
                    label=str(i),
                    primary="shot.png",
                ),
            ],
        )
    store.close()
    yield instance
    instance.scheduler.close()


def reading(view, ctx, **kwargs):
    return [
        Observation(
            view_id=view.view_id,
            signal_id="E11",
            namespace="image.text",
            value="An observed claim",
            raw="An observed claim",
            state=State.PRESENT,
            purpose=Purpose.BOTH,
            extractor="perception.image",
            version="1",
        )
    ], None


def schedule(worker, ids=None, created_at=None):
    job = Job(str(len(worker.jobs)), worker.data_dir, ["Test"], kind="llm")
    if created_at:
        job.created_at = created_at
    for step in job.steps:
        if step["id"] in ("expectations", "techniques", "patterns"):
            step.update(skipRequested=True, status="skipped")
    worker.scheduler.schedule(job, ids)
    return job


def drain(worker, job, limit=10):
    deadline = time.monotonic() + limit
    while job.status != "done" and time.monotonic() < deadline:
        worker.scheduler.tick()
        time.sleep(0.005)
    assert job.status == "done", [
        (t["stage"], t["state"], t.get("message")) for t in worker.scheduler.tasks.values()
    ]


def test_concurrent_reads_use_snapshots_and_publish_during_other_requests(worker, monkeypatch):
    gate, started = threading.Event(), threading.Event()
    count = 0
    mutex = threading.Lock()

    def read(view, ctx, **kwargs):
        nonlocal count
        with mutex:
            count += 1
            if count >= 2:
                started.set()
        assert ctx.store.db.execute("PRAGMA database_list").fetchone()[2] == ""
        if view.view_id != "0":
            assert gate.wait(5)
        return reading(view, ctx)

    monkeypatch.setattr("ftf.perception.image.perceive_view", read)
    job = schedule(worker)
    try:
        for _ in range(30):
            worker.scheduler.tick()
            if started.is_set():
                break
        assert started.wait(1)
        # A slow provider does not hold the analysis lock or block an earlier item.
        assert ANALYSIS_LOCK.acquire(blocking=False)
        ANALYSIS_LOCK.release()
        worker.scheduler.last_publish = 0
        for _ in range(20):
            worker.scheduler.tick()
            time.sleep(0.005)
        store = Store(worker.db_path)
        try:
            assert any(o.extractor == "perception.image" for o in store.observations("0"))
            assert not any(o.extractor == "perception.image" for o in store.observations("1"))
            assert store.relations()
        finally:
            store.close()
        assert worker.generation > 0 and not job.core["finishedAt"]
    finally:
        gate.set()
    drain(worker, job)
    assert job.core["targetMet"] is True and job.core["done"] == 3


def test_unchanged_items_reuse_tasks_and_changed_source_runs_again(worker, monkeypatch):
    calls = []

    def read(view, ctx, **kwargs):
        calls.append(view.view_id)
        return reading(view, ctx)

    monkeypatch.setattr("ftf.perception.image.perceive_view", read)
    first = schedule(worker)
    drain(worker, first)
    assert sorted(calls) == ["0", "1", "2"]
    second = schedule(worker)
    drain(worker, second)
    assert len(calls) == 3
    assert any(
        t.get("metrics", {}).get("reusedTasks")
        for t in worker.scheduler.tasks.values()
        if t["run"] == second.job_id
    )
    Image.new("RGB", (32, 32), "yellow").save(worker.data_dir / "case-1/shot.png")
    third = schedule(worker, ["1"])
    drain(worker, third)
    assert calls == ["0", "1", "2", "1"]


def test_persistent_tasks_resume_after_restart(worker, monkeypatch):
    monkeypatch.setattr("ftf.perception.image.perceive_view", reading)
    queued = schedule(worker)
    recovered = Worker(worker.db_path, worker.data_dir)
    try:
        job = recovered.jobs[queued.job_id]
        drain(recovered, job)
        assert job.core["targetMet"] and job.core["done"] == 3
    finally:
        recovered.scheduler.close()


def test_overdue_reads_are_deferred_without_claiming_target_met(worker, monkeypatch):
    monkeypatch.setattr("ftf.perception.image.perceive_view", reading)
    created_at = (datetime.now(UTC) - timedelta(seconds=310)).isoformat()
    job = schedule(worker, created_at=created_at)
    for _ in range(20):
        worker.scheduler.tick()
        time.sleep(0.005)
        if job.core.get("finishedAt"):
            break
    assert job.core["finishedAt"] and job.core["targetMet"] is False
    assert any(t.get("deferred") for t in worker.scheduler.tasks.values())
    drain(worker, job)
    assert job.core["targetMet"] is False  # late success cannot rewrite the milestone


def test_library_run_never_reads_video_and_video_submission_is_idempotent(worker, monkeypatch):
    monkeypatch.setattr("ftf.perception.image.perceive_view", reading)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    store = Store(worker.db_path)
    store.put_incident(
        Incident(incident_id="video", root=str(worker.data_dir)),
        [],
        [
            View(
                view_id="video",
                incident_id="video",
                kind=ViewKind.VIDEO,
                label="Video",
                primary="clip.mp4",
            ),
        ],
    )
    store.close()
    automatic = schedule(worker, ["video"])
    drain(worker, automatic)
    assert not any(t["stage"] == "video" for t in worker.scheduler.tasks.values())
    assert worker.item_checks["video"]["video"]["state"] == "on_demand"
    explicit = worker.submit_video("video")
    assert worker.submit_video("video") is explicit
    assert any(t["stage"] == "video" for t in worker.scheduler.tasks.values())
    worker.scheduler.cancel(explicit.job_id)
    assert explicit.status == "done"
    assert all(
        t["state"] == "cancelled"
        for t in worker.scheduler.tasks.values()
        if t["run"] == explicit.job_id
    )
    assert worker.last_llm_run["id"] == automatic.job_id


def test_rate_limits_reduce_concurrency_and_retry_after_core(worker, monkeypatch):
    from ftf.models.gateway import record_metric

    calls = set()

    def read(view, ctx, **kwargs):
        if view.view_id not in calls:
            calls.add(view.view_id)
            record_metric("rateLimits")
            row = reading(view, ctx)[0][0].model_copy(
                update={"state": State.NOT_OBSERVABLE, "raw": "rate_limit"}
            )
            return [row], "rate_limit"
        return reading(view, ctx)

    monkeypatch.setattr("ftf.perception.image.perceive_view", read)
    job = schedule(worker)
    drain(worker, job)
    assert job.core["targetMet"] is True  # the retry finished inside the run's deadline
    assert any(t.get("deferred") for t in worker.scheduler.tasks.values())
    assert all(
        t["state"] == "complete" for t in worker.scheduler.tasks.values() if t["stage"] == "image"
    )


def test_identical_requests_in_flight_share_one_computation(tmp_path):
    started, release = threading.Event(), threading.Event()
    calls = []

    def compute():
        calls.append(1)
        started.set()
        assert release.wait(2)
        return {"value": "answer"}

    def call():
        with request_budget(2, {}):
            return cached_call(tmp_path, "test", "1", "model", {"text": "same"}, compute)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(call) for _ in range(4)]
        assert started.wait(1)
        time.sleep(0.02)
        release.set()
        assert [f.result() for f in futures] == [{"value": "answer"}] * 4
    assert calls == [1]


def test_publication_batch_is_atomic_and_rolls_back(tmp_path):
    writer = Store(tmp_path / "atomic.sqlite")
    reader = Store(tmp_path / "atomic.sqlite")
    try:
        with writer.batch():
            writer.put_incident(Incident(incident_id="one", root=str(tmp_path)), [], [])
            assert reader.incidents() == []
        assert len(reader.incidents()) == 1
        with pytest.raises(RuntimeError), writer.batch():
            writer.put_incident(Incident(incident_id="two", root=str(tmp_path)), [], [])
            raise RuntimeError("Publication failed")
        assert [i.incident_id for i in reader.incidents()] == ["one"]
    finally:
        writer.close()
        reader.close()


def test_recovered_intake_preserves_existing_results_and_uses_registered_ids(worker):
    from ftf.intake import ingest_new_material

    store = Store(worker.db_path)
    try:
        path = worker.data_dir / "case-0"
        # Already registered material can be resumed, without replacing its derived rows.
        before = store.get_view("0")
        store.put_observations("0", "perception.image", reading(before, None)[0])
        found = ingest_new_material(store, path, recovering=True)
        # This folder has a new incident id relative to the fixture; a second recovery
        # must use the stored ids and preserve all results.
        vid = found[0][1][0].view_id
        view = store.get_view(vid)
        store.put_observations(vid, "perception.image", reading(view, None)[0])
        again = ingest_new_material(store, path, recovering=True)
        assert again[0][1][0].view_id == vid
        assert any(o.extractor == "perception.image" for o in store.observations(vid))
    finally:
        store.close()


def test_concurrent_pipeline_matches_serial_replay(worker, monkeypatch):
    from scripts.benchmark_processing import sequential_parity

    monkeypatch.setattr("ftf.perception.image.perceive_view", reading)
    job = schedule(worker)
    drain(worker, job)
    parity = sequential_parity(worker)
    assert parity == {
        "observationsEqual": True,
        "relationsEqual": True,
        "cacheOnly": True,
        "pairs": 3,
    }


def test_slow_url_acquisition_does_not_block_uploaded_material(worker):
    from ftf.jobs import link_case

    capture_started, release = threading.Event(), threading.Event()

    def capture(url, target):
        capture_started.set()
        assert release.wait(3)
        target.mkdir(parents=True)
        (target / "dom.html").write_text("<html><body>Captured page</body></html>")
        (target / "manifest.json").write_text(
            json.dumps({"schema": "ftf.capture/1", "page_url": url, "status": "ok"})
        )

    worker.capture = capture
    url = "https://example.test/"
    slow = worker.submit(link_case(url, worker.uploads), [url], [url])
    assert capture_started.wait(1)
    folder = worker.data_dir / "uploaded"
    folder.mkdir()
    Image.new("RGB", (16, 16), "red").save(folder / "image.png")
    fast = worker.submit(folder, ["image.png"])
    try:
        deadline = time.monotonic() + 2
        while fast.status != "done" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert fast.status == "done", fast.error
        assert slow.status == "running"
        assert any(j.kind == "llm" and j.view_ids for j in worker.jobs.values())
    finally:
        release.set()
        deadline = time.monotonic() + 2
        while slow.status == "running" and time.monotonic() < deadline:
            time.sleep(0.01)


def test_failed_item_does_not_block_others_or_report_success(worker, monkeypatch):
    def read(view, ctx, **kwargs):
        if view.view_id == "0":
            raise RuntimeError("Simulated reading failure")
        return reading(view, ctx)

    monkeypatch.setattr("ftf.perception.image.perceive_view", read)
    job = schedule(worker)
    drain(worker, job)
    assert job.core["targetMet"] is False
    assert job.core["done"] == 2
    assert "image: error" in job.issues
    assert worker.item_checks["0"]["image"]["state"] == "failed"
    assert worker.item_checks["1"]["image"]["state"] == "complete"


def test_individual_check_runs_only_requested_item_and_keeps_other_check_statuses(worker, monkeypatch):
    monkeypatch.setattr("ftf.perception.image.perceive_view", reading)
    worker.item_checks["0"] = {"techniques": {"state": "skipped", "hasResult": False}}
    job = worker.submit_item_checks("0", ["image"])
    assert worker.submit_item_checks("0", ["image"]) is job
    drain(worker, job)
    assert worker.item_checks["0"]["image"]["state"] == "complete"
    assert worker.item_checks["0"]["techniques"]["state"] == "skipped"
    assert not worker.item_checks.get("1")
    assert all(t["views"] == ["0"] for t in worker.scheduler.tasks.values())
    assert all(t["state"] == "skipped" for t in worker.scheduler.tasks.values()
               if t["stage"] not in ("local", "image"))
    assert worker.last_llm_run is None


def test_individual_warning_check_needs_no_model_key(worker, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    job = worker.submit_item_checks("0", ["warnings"])
    drain(worker, job)
    assert worker.item_checks["0"]["warnings"]["state"] not in ("queued", "running", "not_run")
    assert all(t["state"] == "skipped" for t in worker.scheduler.tasks.values()
               if t["stage"] in ("image", "page", "video", "techniques", "patterns", "match", "semantic"))


def test_absolute_stream_budget_closes_a_trickling_request():
    import importlib
    from types import SimpleNamespace

    from ftf.models.gateway import TransientModelError
    from ftf.models.openai_client import _send

    importlib.import_module("openai")
    importlib.import_module("httpx2")
    closed = threading.Event()

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def close(self):
            closed.set()

        def get_final_response(self):
            assert closed.wait(1)
            raise RuntimeError("stream closed")

    client = SimpleNamespace(responses=SimpleNamespace(stream=lambda **kwargs: Stream()))
    started = time.monotonic()
    with request_budget(0.03, {}), pytest.raises(TransientModelError, match="timeout"):
        _send(client)
    assert closed.is_set() and time.monotonic() - started < 1
