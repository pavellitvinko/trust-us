from ftf.contracts import Observation, Purpose, State, View, ViewKind
from ftf.processing import check_result, item_processing


def view(kind=ViewKind.IMAGE):
    return View(view_id="one", incident_id="case", kind=kind, label="One")


def observation(extractor="perception.image", state=State.PRESENT):
    return Observation(view_id="one", signal_id="A1", namespace="image.text", value="hello",
                       raw="timeout" if state == State.NOT_OBSERVABLE else "hello",
                       state=state, purpose=Purpose.BOTH, extractor=extractor, version="1")


def test_only_applicable_checks_count_and_missing_outputs_are_not_negative_results():
    image = item_processing(view(), [])
    page = item_processing(view(ViewKind.PAGE), [])
    assert image["state"] == "not_run" and image["total"] == 4
    assert {c["id"] for c in image["checks"]} == {"image", "techniques", "warnings", "patterns"}
    assert page["total"] == 5
    assert all(c["state"] == "not_run" for c in page["checks"])


def test_zero_findings_is_a_completed_run_and_failed_rerun_keeps_old_results():
    assert check_result("image", [], {"views": 1, "read": 1})["state"] == "complete"
    retained = check_result("image", [observation()], {"kept": 1})
    assert retained["state"] == "retained" and retained["hasResult"]


def test_partial_observation_coverage_does_not_claim_complete():
    rows = [observation(), observation(state=State.NOT_OBSERVABLE)]
    check = check_result("image", rows)
    assert check["state"] == "partial"
    assert "timed out" in check["message"]
    assert check["hasResult"]


def test_skip_and_waiting_are_per_item_and_keep_stored_results_separate():
    tracked = {"image": {"state": "skipped", "updatedAt": "2026-11-22T06:00:00Z"},
               "techniques": {"state": "running", "message": "Checking this item."}}
    payload = item_processing(view(), [observation()], tracked)
    assert payload["state"] == "running" and payload["current"] == "Find persuasion techniques"
    image = next(c for c in payload["checks"] if c["id"] == "image")
    assert image["state"] == "skipped" and image["hasResult"]
    assert "Earlier stored results" in image["message"]
    assert payload["complete"] == 0


def test_an_earlier_empty_success_survives_a_later_skip():
    payload = item_processing(view(), [], {"image": {"state": "skipped", "hasResult": True}})
    assert payload["checks"][0]["hasResult"]
    assert payload["checks"][0]["state"] == "skipped"


def test_shipped_sources_are_explicitly_unavailable_when_not_checked():
    shipped = view().model_copy(update={"meta": {"shipped": True}})
    payload = item_processing(shipped, [])
    assert payload["state"] == "unavailable"
    assert all(c["state"] == "unavailable" for c in payload["checks"])


def test_changed_cli_outputs_supersede_an_older_ui_completion():
    earlier = check_result("image", [observation()], {"read": 1})
    payload = item_processing(view(), [observation(state=State.NOT_OBSERVABLE)], {"image": earlier})
    assert payload["checks"][0]["state"] == "unavailable"


def test_skipping_a_step_does_not_relabel_an_item_that_already_finished(tmp_path):
    from ftf.jobs import Job, Worker

    worker = Worker(tmp_path / "db.sqlite", tmp_path)
    job = Job("run", tmp_path, [], kind="llm", status="running")
    next(s for s in job.steps if s["id"] == "image")["status"] = "running"
    worker.jobs[job.job_id] = job
    worker.item_checks = {
        "done": {"image": {"state": "complete", "runId": "run", "hasResult": True}},
        "waiting": {"image": {"state": "queued", "runId": "run"}},
    }
    worker.skip_llm_step("run", "image")
    assert worker.item_checks["done"]["image"]["state"] == "complete"
    assert worker.item_checks["waiting"]["image"]["state"] == "skipped"
    restarted = Worker(worker.db_path, tmp_path)
    assert restarted.item_checks["done"]["image"]["state"] == "complete"
    assert restarted.item_checks["waiting"]["image"]["state"] == "skipped"


def test_worker_persists_item_outcomes_and_marks_interrupted_items(tmp_path, monkeypatch):
    from ftf.jobs import Job, Worker

    def run(store, on_stage, results, *, on_item, **controls):
        on_stage("image")
        on_item("image", "done", {"state": "complete", "message": "No findings.", "hasResult": True})
        on_item("image", "interrupted", {"state": "running", "message": "Checking."})
        raise RuntimeError("interrupted")

    monkeypatch.setattr("ftf.llm_checks.run_llm_checks", run)
    worker = Worker(tmp_path / "db.sqlite", tmp_path)
    worker.run(Job("run", tmp_path, [], kind="llm"))
    restarted = Worker(worker.db_path, tmp_path)
    assert restarted.item_checks["done"]["image"]["state"] == "complete"
    assert restarted.item_checks["done"]["image"]["hasResult"]
    assert restarted.item_checks["interrupted"]["image"]["state"] == "failed"
