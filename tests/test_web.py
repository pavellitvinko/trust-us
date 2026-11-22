"""The web API: the Library, an item page, pictures, adding material and its guards."""

from pathlib import Path

import pytest
from PIL import Image

from ftf.contracts import Incident, View, ViewKind
from ftf.library import plain_clue, plain_finding, short_clue
from ftf.store import Store


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    case = tmp_path / "case"
    case.mkdir()
    Image.new("RGB", (64, 40), "red").save(case / "shot.png")
    (case / "page.html").write_text("<html><title>Oferta</title><body>Treść</body></html>")
    data = tmp_path / "data"
    store = Store(data / "ftf.sqlite")
    views = [
        View(view_id="7:shot", incident_id="7", kind=ViewKind.IMAGE, label="7 · shot", primary="shot.png"),
        View(view_id="7:page", incident_id="7", kind=ViewKind.PAGE, label="7 · page", primary="page.html"),
    ]  # fmt: skip
    store.put_incident(Incident(incident_id="7", root=str(case)), [], views)
    store.close()
    monkeypatch.setenv("FTF_DATA_DIR", str(data))
    monkeypatch.setenv("FTF_WEB_DIST", str(tmp_path / "no-dist"))
    monkeypatch.setattr("ftf.web.app.render_missing", lambda store, data_dir: 0)  # no browser
    from ftf.web.app import create_app

    with TestClient(create_app()) as c:
        yield c


def test_library_lists_items_with_words_for_a_newcomer(client):
    data = client.get("/api/library").json()
    assert {r["id"] for r in data["items"]} == {"7:shot", "7:page"}
    shot = next(r for r in data["items"] if r["id"] == "7:shot")
    assert shot["case"] == "Case 7" and shot["picture"] is True
    assert shot["checked"] is False  # nothing was looked at: the screen says "not checked"
    meta = client.get("/api/meta").json()
    assert set(meta["groups"]) >= {"case", "campaign", "kit", "duplicate"}


def test_campaigns_screen_counts_what_is_left_over(client):
    data = client.get("/api/campaigns").json()
    assert data["campaigns"] == [] and data["kits"] == [] and data["repeats"] == ""
    assert data["unlinked"] == "2 items aren't linked to anything yet."
    assert "busy" in data and "generation" in data
    assert client.get("/api/campaigns/nope").status_code == 404
    assert client.get("/api/kits/nope").status_code == 404


def test_a_campaign_row_and_page_say_what_it_is_and_why():
    from ftf.library import UNCHECKED_PLAIN, Campaign, Case, Item, Library, Match, Risk
    from ftf.web.views import campaign_detail, campaigns_payload

    blank = list(UNCHECKED_PLAIN)  # nothing was looked at

    pixel = Match(view_id="b", same_case=False, verdict="Same campaign", tiers=("identity",),
                  confidence="high", observed_groups=1, total_groups=1,
                  clue="Same pixel 1071875908635809", clue_ns="fb_pixel", clue_value="1071875908635809")  # fmt: skip
    items = [
        Item(
            "a",
            "1",
            ViewKind.PAGE,
            "Metabo RX",
            "a.top",
            [],
            Risk("none", unchecked=blank),
            matches=[pixel],
        ),
        Item("b", "2", ViewKind.PAGE, "Metabo RX", "b.top", [], Risk("none", unchecked=blank)),
        Item("c", "2", ViewKind.IMAGE, "shot.png", None, [], Risk("none")),
    ]
    campaign = Campaign("c1", "Campaign 1071875908635809", ["a", "b"], ["1", "2"],
                        "2026-11-20T10:00:00+00:00", [], title="Metabo RX")  # fmt: skip
    lib = Library(cases=[Case("1", "Case 1", "", items[:1]), Case("2", "Case 2", "", items[1:])],
                  journeys={}, links={}, campaigns={"c1": campaign})  # fmt: skip
    data = campaigns_payload(lib)
    row = data["campaigns"][0]
    assert row["title"] == "Metabo RX" and row["kit"] == ""
    assert row["sub"] == "2 pages · a.top, b.top" and row["cases"] == "1, 2"
    assert row["linkedBy"] == "same Facebook tracking pixel" and row["strength"] == "Strong"
    assert row["signs"]["text"] == "not checked"  # nothing was looked at
    assert data["count"] == "1 campaign in 3 items"
    assert data["unlinked"] == "1 item isn't linked to anything yet."

    page = campaign_detail(lib, campaign)
    assert {f["label"]: f["value"] for f in page["facts"]} == {
        "Made of": "2 pages",
        "Websites": "a.top, b.top",
        "Found in": "Cases 1, 2",
        "First seen": "20 Nov 2026",
    }
    assert page["title"] == "Metabo RX campaign" and page["short"] == "Metabo RX"
    assert page["summary"] == "2 pages on 2 websites, linked by same Facebook tracking pixel"
    assert page["why"]["text"] == "Same Facebook tracking pixel 1071875908635809"
    assert page["why"]["note"].startswith("Only whoever runs the pages")
    assert page["empty"] == "Not checked." and page["path"] is None and page["kit"] is None
    assert [(p["a"]["id"], p["b"]["id"], p["kind"]) for p in page["pairs"]] == [
        ("a", "b", "campaign")
    ]


def test_warning_signs_say_how_many_pages_show_them():
    from ftf.library import Campaign, Case, Finding, Item, Library, Risk
    from ftf.web.views import campaign_detail

    def risk(*signs):
        return Risk(
            "warning",
            findings=[Finding(kind="Domain", summary=s, source="page.html") for s in signs],
        )

    items = [
        Item("a", "1", ViewKind.PAGE, "A", "x.top", [], risk(".top address", "countdown")),
        Item("b", "1", ViewKind.PAGE, "B", "x.top", [], risk(".top address")),
    ]
    campaign = Campaign("c1", "Campaign x", ["a", "b"], ["1"], "", [], title="A")
    lib = Library(cases=[Case("1", "Case 1", "", items)], journeys={}, links={},
                  campaigns={"c1": campaign})  # fmt: skip
    signs = campaign_detail(lib, campaign)["signs"]
    assert [(s["text"], s["where"]) for s in signs] == [
        (".top address", "on 2 of 2 pages"),
        ("countdown", "on 1 of 2 pages"),
    ]


def test_item_page_and_pictures(client):
    item = client.get("/api/items/7:page").json()
    assert item["title"] == "page" and [b["id"] for b in item["sameCase"]] == ["7:shot"]
    assert client.get("/api/items/nope").status_code == 404
    card = client.get("/api/pictures/7:shot")
    assert card.status_code == 200 and card.headers["content-type"] == "image/jpeg"
    assert client.get("/api/pictures/7:page").status_code == 404  # no render in this test
    assert client.get("/api/pictures/7:shot?size=../etc").status_code == 422


def test_adding_material_needs_something_and_rejects_bad_links(client):
    assert client.post("/api/material").status_code == 400
    bad = client.post("/api/material", data={"links": ["mailto:x@example.com"]})
    assert bad.status_code == 400 and "http" in bad.json()["detail"]
    escape = client.post("/api/material", files=[("files", ("../evil.html", b"x", "text/html"))])
    assert escape.status_code == 400


def test_a_dropped_folder_is_named_by_its_folder(client, monkeypatch):
    submitted = {}

    def fake_submit(self, path, names, urls=None):
        from ftf.jobs import Job

        submitted.update(path=Path(path), names=names)
        return Job(job_id="j", path=Path(path), names=names)

    monkeypatch.setattr("ftf.jobs.Worker.submit", fake_submit)
    files = [
        ("files", ("finds/a.html", b"<html></html>", "text/html")),
        ("files", ("finds/a_files/x.css", b"", "text/css")),
        ("files", ("loose.png", b"x", "image/png")),
    ]
    job = client.post("/api/material", files=files).json()
    assert job["names"] == ["finds/", "loose.png"]
    assert any(
        p.name == "x.css" and p.parent.name == "a_files" for p in submitted["path"].rglob("*")
    )


def test_unknown_paths_do_not_reach_files(client):
    assert client.get("/api/report/../../etc/passwd").status_code == 404


def test_findings_and_clues_read_as_sentences():
    assert plain_finding("countdown timer: timer") == "A countdown timer to rush the visitor"
    assert ".top" in plain_finding("high-risk TLD class: ineptsolidoval.top")
    assert plain_finding("domain registered 12 days before capture").endswith(
        "before it was captured"
    )
    assert plain_finding("something new") == "Something new"
    assert plain_clue("Same pixel 123") == "Same Facebook tracking pixel 123"
    assert plain_clue("3 identical files") == "3 identical files (images or scripts)"
    assert short_clue("Same pixel 123") == "same Facebook tracking pixel"
    assert short_clue("Same campaign record 849a") == "same campaign ID"


def test_saved_pages_render_inside_their_own_folder(tmp_path):
    pytest.importorskip("playwright")
    from ftf.previews import picture, render_missing

    case = tmp_path / "case"
    case.mkdir()
    Image.new("RGB", (400, 300), (255, 0, 255)).save(tmp_path / "secret.png")  # outside the case
    Image.new("RGB", (400, 300), (0, 0, 255)).save(case / "own.png")  # inside it
    (case / "page.html").write_text(
        '<html><body style="margin:0;background:#00aa00">'
        '<img src="own.png" width="400" height="300" style="display:block">'
        '<img src="../secret.png" width="400" height="300" style="display:block"></body></html>'
    )
    store = Store(":memory:")
    view = View(
        view_id="1:p", incident_id="1", kind=ViewKind.PAGE, label="1 · p", primary="page.html"
    )
    store.put_incident(Incident(incident_id="1", root=str(case)), [], [view])
    try:
        made = render_missing(store, tmp_path / "data")
    finally:
        store.close()
    if made == 0:
        pytest.skip("no Chromium for Playwright here")
    path = picture(view, case, tmp_path / "data")
    assert path is not None and path.is_file()
    with Image.open(path) as im:
        colours = {c for _, c in im.convert("RGB").getcolors(maxcolors=1 << 20)}
    assert (0, 0, 255) in colours  # the page's own picture is shown
    assert (255, 0, 255) not in colours  # a file outside its folder never is


def test_a_folder_of_more_than_a_thousand_files_is_accepted(client, monkeypatch):
    from ftf.jobs import Job

    monkeypatch.setattr(
        "ftf.jobs.Worker.submit", lambda self, path, names, urls=None: Job("j", Path(path), names)
    )
    files = [("files", (f"site/page_files/{n}.css", b"", "text/css")) for n in range(1100)]
    files.append(("files", ("site/page.html", b"<html></html>", "text/html")))
    res = client.post("/api/material", files=files)
    assert res.status_code == 200 and res.json()["names"] == ["site/"]


@pytest.mark.parametrize("link", ["http://127.0.0.1:8000/", "http://192.168.1.1/", "localhost"])
def test_links_to_the_servers_own_networks_are_refused(client, monkeypatch, link):
    monkeypatch.delenv("FTF_UI_ALLOW_PRIVATE", raising=False)
    res = client.post("/api/material", data={"links": [link]})
    assert res.status_code == 400 and "private network" in res.json()["detail"]


def test_the_worker_captures_through_the_guarded_child_process(tmp_path, monkeypatch):
    from ftf import jobs
    from ftf.intake import IntakeError

    monkeypatch.delenv("FTF_UI_ALLOW_PRIVATE", raising=False)
    calls = []
    monkeypatch.setattr(
        jobs,
        "guarded_capture",
        lambda url, out, replay, timeout, public: calls.append((url, public)),
    )
    monkeypatch.setattr(jobs, "check_public", lambda url: None)  # no DNS in tests
    jobs._capture("example.com", tmp_path / "out")
    assert calls == [("https://example.com", True)]  # normalised, public addresses only
    monkeypatch.undo()
    with pytest.raises(IntakeError, match="private network"):
        jobs._capture("http://10.0.0.1/", tmp_path / "out")


def test_llm_checks_require_keys_without_queueing(client, monkeypatch):
    from ftf.config import Settings

    monkeypatch.setattr("ftf.llm_checks.get_settings", lambda: Settings(
        Path("unused"), "", "", "reader", "embedder", "video",
    ))
    response = client.post("/api/library/llm-checks")
    assert response.status_code == 409
    assert "OPENAI_API_KEY" in response.json()["detail"]
    assert client.get("/api/library").json()["llmRun"] is None


def test_llm_worker_reports_partial_coverage_and_survives_restart(tmp_path, monkeypatch):
    from ftf.jobs import Job, Worker

    def run(store, on_stage, results, **controls):
        on_stage("image")
        results["image"] = {"views": 2, "read": 1, "not_observable": 1}
        on_stage("patterns")
        results["patterns"] = {"matching": {"kept": 1}}

    monkeypatch.setattr("ftf.llm_checks.run_llm_checks", run)
    worker = Worker(tmp_path / "ftf.sqlite", tmp_path)
    job = Job("test-run", tmp_path, ["LLM checks"], kind="llm")
    worker.run(job)
    assert job.status == "done"
    assert worker.generation == 1
    assert job.issues == ["image: 1 not observable", "pattern matching: 1 kept"]
    restarted = Worker(worker.db_path, tmp_path)
    assert restarted.last_llm_run["results"]["image"]["read"] == 1
    assert restarted.last_llm_run["status"] == "done"


def test_llm_job_failure_retains_completed_stages(tmp_path, monkeypatch):
    from ftf.jobs import Job, Worker

    def run(store, on_stage, results, **controls):
        on_stage("image")
        results["image"] = {"read": 1}
        raise RuntimeError("interrupted")

    monkeypatch.setattr("ftf.llm_checks.run_llm_checks", run)
    worker = Worker(tmp_path / "ftf.sqlite", tmp_path)
    job = worker.run(Job("failed-run", tmp_path, ["LLM checks"], kind="llm"))
    assert job.status == "failed"
    assert "interrupted" in job.error
    assert worker.last_llm_run["results"]["image"]["read"] == 1


def test_llm_duplicate_submissions_are_refused(tmp_path, monkeypatch):
    from ftf.intake import IntakeError
    from ftf.jobs import Job, Worker

    monkeypatch.setattr("ftf.llm_checks.require_model_keys", lambda: None)
    worker = Worker(tmp_path / "ftf.sqlite", tmp_path)
    worker.jobs["running"] = Job("running", tmp_path, [], kind="llm", status="running")
    with pytest.raises(IntakeError, match="already queued or running"):
        worker.submit_llm()


def test_llm_orchestration_refreshes_groups_before_patterns_and_after_matching(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from ftf.layers._embedder import FakeEmbedder, get_embedder, set_embedder
    from ftf.llm_checks import run_llm_checks

    calls = []
    monkeypatch.setattr("ftf.llm_checks.require_model_keys", lambda: None)
    for module, function, name in (
        ("image", "run_perceive", "image"), ("page", "run_perceive_pages", "page"),
        ("video", "run_perceive_videos", "video"),
        ("expectations", "run_expectations", "expectations"),
        ("techniques", "run_techniques", "techniques"),
    ):
        def run(ctx, view_ids, *, settings, name=name):
            calls.append(name)
            return {"views": 1}
        monkeypatch.setattr(f"ftf.perception.{module}.{function}", run)
    monkeypatch.setattr("ftf.enrich.warning_lists.run_warning_lists", lambda *a: {})
    monkeypatch.setattr("ftf.pipeline.run_fingerprint", lambda *a: calls.append("fingerprint"))
    monkeypatch.setattr("ftf.pipeline.run_cloaking", lambda *a: None)
    monkeypatch.setattr("ftf.pipeline.run_compare_all", lambda *a: calls.append("compare"))

    def groups(*a):
        calls.append("groups")
        return SimpleNamespace(campaigns=[], families=[])

    monkeypatch.setattr("ftf.fusion.run_method", groups)
    monkeypatch.setattr("ftf.patterns.workflow.run_discover", lambda *a, **k: calls.append("patterns") or {})
    previous = FakeEmbedder()
    set_embedder(previous)
    store = Store(tmp_path / "pipeline.sqlite")
    store.put_incident(Incident(incident_id="7", root=str(tmp_path)), [], [
        View(view_id="image", incident_id="7", kind=ViewKind.IMAGE, label="image"),
        View(view_id="page", incident_id="7", kind=ViewKind.PAGE, label="page"),
        View(view_id="video", incident_id="7", kind=ViewKind.VIDEO, label="video"),
    ])
    try:
        results = {}
        stages = []
        run_llm_checks(store, stages.append, results)
        assert calls == ["image", "page", "video", "expectations", "techniques", "techniques", "techniques",
                         "fingerprint", "compare", "groups", "patterns",
                         "fingerprint", "compare", "groups"]
        assert stages[-3:] == ["rebuild", "patterns", "finalize"]
        assert get_embedder() is previous
    finally:
        store.close()
        set_embedder(None)


def test_skip_controls_are_saved_and_required_or_started_batches_are_protected(tmp_path):
    from ftf.intake import IntakeError
    from ftf.jobs import Job, Worker

    worker = Worker(tmp_path / "db.sqlite", tmp_path)
    job = Job("control-run", tmp_path, [], kind="llm", status="running")
    worker.jobs[job.job_id] = job
    worker.skip_llm_step(job.job_id, "video")
    assert next(s for s in job.steps if s["id"] == "video")["status"] == "skipped"
    active = next(s for s in job.steps if s["id"] == "image")
    active["status"] = "running"
    worker.skip_llm_step(job.job_id, "image")
    assert active["skipRequested"] and active["status"] == "running"
    with pytest.raises(IntakeError):
        worker.skip_llm_step(job.job_id, "finalize")
    next(s for s in job.steps if s["id"] == "patterns")["status"] = "running"
    with pytest.raises(IntakeError):
        worker.skip_llm_step(job.job_id, "patterns")
    assert Worker(worker.db_path, tmp_path).last_llm_run["status"] == "failed"


def test_reading_skip_stops_before_the_next_item_and_retains_progress(tmp_path, monkeypatch):
    from ftf.llm_checks import run_llm_checks

    monkeypatch.setattr("ftf.llm_checks.require_model_keys", lambda: None)
    store = Store(tmp_path / "skip.sqlite")
    store.put_incident(Incident(incident_id="test", root=str(tmp_path)), [], [
        View(view_id=f"image-{i}", incident_id="test", kind=ViewKind.IMAGE, label=f"Image {i}")
        for i in range(3)
    ])
    processed = []
    monkeypatch.setattr("ftf.perception.image.run_perceive",
                        lambda ctx, ids, **kw: processed.extend(ids) or {"views": 1, "read": 1})
    for name in ("run_fingerprint", "run_cloaking", "run_compare_all"):
        monkeypatch.setattr(f"ftf.pipeline.{name}", lambda *a: 0)
    from types import SimpleNamespace
    monkeypatch.setattr("ftf.fusion.run_method", lambda *a: SimpleNamespace(campaigns=[], families=[]))
    results, progress = {}, []
    try:
        run_llm_checks(store, lambda name: None, results,
                       should_skip=lambda name: name != "image" and name not in ("rebuild", "finalize") or (name == "image" and bool(processed)),
                       on_progress=lambda *args: progress.append(args))
        assert processed == ["image-0"]
        assert results["image"] == {"views": 1, "read": 1, "skipped": True}
        assert ("image", 1, 3, None) in progress
        assert results["patterns"]["skipped"] is True
    finally:
        store.close()


def test_skip_endpoint_updates_live_progress_and_refuses_required_steps(client):
    from ftf.config import get_settings
    from ftf.jobs import Job, worker_for

    settings = get_settings()
    worker = worker_for(settings.db_path, settings.data_dir)
    job = Job("api-skip", settings.data_dir, [], kind="llm", status="running")
    worker.jobs[job.job_id] = job
    response = client.post("/api/library/llm-checks/api-skip/skip/video")
    assert response.status_code == 200
    steps = response.json()["steps"]
    assert next(s for s in steps if s["id"] == "video")["status"] == "skipped"
    assert client.post("/api/library/llm-checks/api-skip/skip/finalize").status_code == 409
    assert client.get("/api/library").json()["llmRun"]["id"] == "api-skip"
    job.status = "done"


def test_table_and_item_detail_share_live_per_item_check_state(client):
    from ftf.config import get_settings
    from ftf.jobs import worker_for

    settings = get_settings()
    worker = worker_for(settings.db_path, settings.data_dir)
    worker.item_checks["7:shot"] = {"image": {
        "state": "running", "message": "Reading this image.", "runId": "live",
        "updatedAt": "2026-11-22T06:00:00Z",
    }}
    rows = {r["id"]: r for r in client.get("/api/library").json()["items"]}
    detail = client.get("/api/items/7:shot").json()
    assert rows["7:shot"]["processing"] == detail["processing"]
    assert detail["processing"]["state"] == "running"
    assert rows["7:page"]["processing"]["state"] == "not_run"
    assert detail["processing"]["checks"][0]["runId"] == "live"


def test_library_report_counts_the_whole_library():
    from ftf.library import UNCHECKED_PLAIN, Campaign, Case, Finding, Item, Library, Match, Risk
    from ftf.web.library_report import build_stats, render_html

    blank = list(UNCHECKED_PLAIN)
    pixel = Match(view_id="b", same_case=False, verdict="Same campaign", tiers=("identity",),
                  confidence="high", observed_groups=1, total_groups=1,
                  clue="Same pixel 1071875908635809", clue_ns="fb_pixel", clue_value="1071875908635809")  # fmt: skip
    back = Match(view_id="a", same_case=False, verdict="Same campaign", tiers=("identity",),
                 confidence="high", observed_groups=1, total_groups=1,
                 clue="Same pixel 1071875908635809", clue_ns="fb_pixel", clue_value="1071875908635809")  # fmt: skip
    timer = Finding("Persuasion", "countdown timer", "page.html", plain="A countdown <timer>")
    items = [
        Item("a", "1", ViewKind.PAGE, "Metabo RX", "a.top", [], Risk("warning", [timer]),
             matches=[pixel], campaign="c1"),
        Item("b", "2", ViewKind.PAGE, "Metabo RX", "a.top", [], Risk("none", unchecked=blank),
             matches=[back], campaign="c1"),
        Item("c", "2", ViewKind.IMAGE, "shot.png", None, [], Risk("none", unchecked=blank)),
    ]  # fmt: skip
    campaign = Campaign("c1", "Campaign 1071875908635809", ["a", "b"], ["1", "2"],
                        "2026-11-20T10:00:00+00:00", [], title="Metabo <RX>")  # fmt: skip
    lib = Library(
        cases=[Case("1", "Case 1", "2026-11-20T10:00:00+00:00", items[:1]),
               Case("2", "Case 2", "2026-11-21T10:00:00+00:00", items[1:])],
        journeys={}, links={}, campaigns={"c1": campaign},
    )  # fmt: skip
    stats = build_stats(lib, generated_at="2026-11-22T08:00:00+00:00")
    assert "by_type" not in stats and "link_status" not in stats
    assert stats["totals"] == {
        "items": 3, "cases": 2, "campaigns": 1, "kits": 0, "linked": 2, "linked_pct": 67,
        "with_signs": 1, "checked": 1, "websites": 1,
    }  # fmt: skip
    assert [(c["label"], c["items"], c["cases"]) for c in stats["campaigns"]] == [
        ("Metabo <RX>", 2, "1, 2")
    ]
    assert stats["kits"] == []
    for gone in ("risk", "signs", "strength", "linked_by", "hosts"):
        assert gone not in stats
    assert stats["latest"] == {"case": "Case 2", "day": "2026-11-21", "items": 2,
                               "linked_to_earlier": 1}  # fmt: skip
    together = Library(
        cases=[Case("1", "Case 1", "2026-11-21T10:00:00+00:00", items[:1]),
               Case("2", "Case 2", "2026-11-21T10:00:00+00:00", items[1:])],
        journeys={}, links={},
    )  # fmt: skip
    assert build_stats(together)["latest"]["case"] == "2 cases"  # one addition, two cases
    assert build_stats(together)["latest"]["items"] == 3
    assert stats["campaigns_across_cases"] == 1

    page = render_html(stats)
    assert "3 items in 2 cases" in page
    assert "Metabo &lt;RX&gt;" in page and "<RX>" not in page  # material stays text
    assert "<svg" not in page  # the overview is tables, no charts
    assert "<script" not in page and "http" not in page  # self-contained

    empty = render_html(build_stats(Library(cases=[], journeys={}, links={})))
    assert "The library is empty" in empty


def test_library_report_is_served_as_a_page_and_a_pdf(client, monkeypatch):
    stats = client.get("/api/library/report.json").json()
    assert stats["totals"]["items"] == 2 and stats["totals"]["cases"] == 1
    page = client.get("/api/library/report.html")
    assert page.status_code == 200 and "Library report" in page.text
    method = stats["method"]
    from ftf.registry import comparators, extractors, layers

    assert [f["id"] for f in method["fingerprints"]] and method["items"] == 2
    assert {f["id"] for f in method["fingerprints"]} == set(layers.names())  # all of them
    assert {e["id"] for e in method["extractors"]} == set(extractors.names())
    assert {c["id"] for c in method["comparators"]} == set(comparators.names())
    for row in method["extractors"] + method["enrichers"] + method["comparators"]:
        assert row["what"], row["id"]  # every method says what it does
    for heading in ("What happens to every added item", "Fingerprints", "Methods applied"):
        assert heading in page.text
    assert "1. Ingest" in page.text and "6. Group" in page.text
    assert "Items by type" not in page.text and "How items are linked" not in page.text
    assert client.get("/api/library/report.exe").status_code == 404

    monkeypatch.setattr("ftf.web.library_report.to_pdf", lambda html: b"%PDF-fake")
    pdf = client.get("/api/library/report.pdf")
    assert pdf.content == b"%PDF-fake" and pdf.headers["content-type"] == "application/pdf"
    assert pdf.headers["content-disposition"].startswith('attachment; filename="library-report-')

    def missing(html):
        from ftf.web.library_report import PdfUnavailable

        raise PdfUnavailable("no browser")

    monkeypatch.setattr("ftf.web.library_report.to_pdf", missing)
    assert client.get("/api/library/report.pdf").status_code == 503

    import threading

    from ftf.intake import ANALYSIS_LOCK

    held, done = threading.Event(), threading.Event()

    def analysis():
        with ANALYSIS_LOCK:
            held.set()
            done.wait(10)

    worker = threading.Thread(target=analysis)
    worker.start()
    held.wait(10)
    try:  # an analysis is running: half-written results are never reported
        assert client.get("/api/library/report.json").status_code == 409
    finally:
        done.set()
        worker.join()


def test_library_report_describes_a_new_plugin_from_its_own_docstring():
    from ftf.web.library_report import _described

    class NewReader:
        """Reads the favicon.

        Added after the report was written.
        """

        name = "new.reader"

    assert _described(NewReader(), {}) == "Reads the favicon"
    assert _described(NewReader(), {"new.reader": "Written by hand"}) == "Written by hand"


def test_library_report_counts_match_the_store(client):
    from ftf.config import get_settings
    from ftf.contracts import Fingerprint, FingerprintKind
    from ftf.web.library_report import build_method

    store = Store(get_settings().db_path)
    try:
        store.put_fingerprint(Fingerprint(view_id="7:page", layer_id="ids.domain",
                                          kind=FingerprintKind.SET, version="1", payload={}))  # fmt: skip
        method = build_method(store)
        assert method["items"] == len(store.views()) == 2
        by_id = {f["id"]: f["items"] for f in method["fingerprints"]}
        assert by_id["ids.domain"] == len(store.fingerprints(layer_id="ids.domain")) == 1
        assert method["counts"]["fingerprints"] == 1
        assert method["counts"]["evidence"] == len(store.evidence())
        assert method["counts"]["relations"] == len(store.relations())
    finally:
        store.close()


@pytest.mark.browser
def test_library_report_prints_to_a_real_pdf(chromium):
    from ftf.library import Library
    from ftf.web.library_report import build_stats, render_html, to_pdf

    pdf = to_pdf(render_html(build_stats(Library(cases=[], journeys={}, links={}))))
    assert pdf.startswith(b"%PDF-") and len(pdf) > 1000
