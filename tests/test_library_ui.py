"""Library screen data and background intake: saving uploads, capturing links, labels, journeys."""

import io
import json
from pathlib import Path

import pytest

from ftf.contracts import Incident, Observation, Source, View, ViewKind
from ftf.grouping.funnel import FunnelEdge
from ftf.grouping.levels import Groups
from ftf.intake import IntakeError
from ftf.jobs import Job, Worker, link_case, save_files
from ftf.library import build_library, plain_proof, read_meta, view_title

SAMPLE = Path(__file__).resolve().parents[1] / "testing_sample"
PAGE = "<html><head><title>Oferta</title></head><body><p>Treść strony.</p></body></html>"


def files(*entries):
    return [(name, io.BytesIO(body.encode())) for name, body in entries]


# -- saving uploads ----------------------------------------------------------------------


def test_a_chosen_folder_of_cases_stays_one_case_per_subfolder(tmp_path):
    root = save_files(
        files(("sample/a/page.html", PAGE), ("sample/b/shot.png", "x")), tmp_path / "up"
    )
    cases = sorted(p.name for p in root.iterdir())
    assert root.name == "sample" and len(cases) == 2
    assert all(name.startswith(("a-", "b-")) for name in cases)


def test_a_chosen_case_folder_and_loose_files_are_one_case(tmp_path):
    folder = save_files(
        files(("case/page.html", PAGE), ("case/page_files/x.css", "")), tmp_path / "up"
    )
    assert folder.name.startswith("case-") and (folder / "page_files/x.css").is_file()
    loose = save_files(files(("page.html", PAGE), ("shot.png", "x")), tmp_path / "up")
    assert loose.name.startswith("case-") and {p.name for p in loose.iterdir()} == {
        "page.html",
        "shot.png",
    }


@pytest.mark.parametrize("name", ["../evil.txt", "a/../../evil.txt", "/evil.txt", "C:/x.txt"])
def test_uploaded_names_cannot_escape(tmp_path, name):
    with pytest.raises(IntakeError):
        save_files(files((name, "x")), tmp_path / "up")
    assert not (tmp_path / "evil.txt").exists()
    assert not (tmp_path / "up").exists() or not list((tmp_path / "up").iterdir())


# -- the background job ------------------------------------------------------------------


def fake_capture(url, out_dir):
    out_dir.mkdir(parents=True)
    (out_dir / "dom.html").write_text(PAGE)
    manifest = {"schema": "ftf.capture/1", "page_url": url, "final_url": url, "status": "ok"}
    (out_dir / "manifest.json").write_text(json.dumps(manifest))


def test_links_without_files_become_one_named_case(tmp_path, monkeypatch):
    monkeypatch.setenv("FTF_EMBEDDER", "fake")
    data = tmp_path / "data"
    worker = Worker(data / "ftf.sqlite", data, capture=fake_capture)
    urls = ["https://ad.example/x", "https://page.example/y"]
    job = Job(job_id="j", path=link_case(urls[0], worker.uploads), names=urls, urls=urls)
    worker.run(job)
    assert job.status == "done", job.error
    assert len(job.incidents) == 1 and job.incidents[0].startswith("ad.example-")
    assert job.incidents[0] in read_meta(data)["cases"]
    assert worker.generation == 1


def test_a_failed_capture_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setenv("FTF_EMBEDDER", "fake")
    data = tmp_path / "data"

    def broken(url, out_dir):
        raise RuntimeError("no browser")

    worker = Worker(data / "ftf.sqlite", data, capture=broken)
    job = Job(job_id="j", path=link_case("https://x.example", worker.uploads), names=["x"],
              urls=["https://x.example"])  # fmt: skip
    worker.run(job)
    assert job.status == "failed" and "no browser" in job.error


# -- labels, risk and journeys -----------------------------------------------------------


def _store_with(store, views):
    store.put_incident(Incident(incident_id="7", root="."), [], views)


def test_only_groups_of_several_items_are_named(store, tmp_path):
    views = [
        View(view_id=v, incident_id="7", kind=ViewKind.PAGE, label=f"7 · {v}.example")
        for v in ("a", "b", "c")
    ]
    _store_with(store, views)
    groups = Groups(
        campaign_of={"a": "c1", "b": "c1", "c": "c2"},
        campaigns={"c1": ["a", "b"], "c2": ["c"]},
        family_of={"c1": "f1", "c2": "f2"},
        families={"f1": ["c1"], "f2": ["c2"]},
    )
    lib = build_library(store, groups, tmp_path)
    labels = {i.view_id: [label.text for label in i.labels] for i in lib.items()}
    assert labels["a"] == labels["b"] == ["Campaign A"]
    assert labels["c"] == ["Not enough evidence"]  # no fingerprints at all
    assert lib.links["a"] == {"b"} and lib.links["c"] == set()


def test_journey_follows_the_funnel_and_starts_from_the_ad(store, tmp_path):
    views = [
        View(view_id=v, incident_id="7", kind=ViewKind.PAGE, label=f"7 · {v}", primary=f"{v}.html")
        for v in ("landing", "chat", "about")
    ]
    _store_with(store, views)
    ad = Observation(
        view_id="landing", signal_id="A3", namespace="ad.id", value="123", raw="123",
        source=Source(file="landing.html"), extractor="ad_params", version="1",
    )  # fmt: skip
    store.put_observations("landing", "ad_params", [ad])
    edge = FunnelEdge("landing", "chat", "funnel", ["stored navigation names the landing: u1"])
    groups = Groups(
        campaign_of={v.view_id: v.view_id for v in views},
        campaigns={v.view_id: [v.view_id] for v in views},
        family_of={},
        families={},
        funnel_edges={"7": ([edge], [])},
    )
    lib = build_library(store, groups, tmp_path)
    journey = lib.journeys["chat"]
    assert [title for _, title in journey.steps] == ["landing", "chat"]
    assert journey.ads == ["123"]
    assert journey.proofs == ["Stored navigation names the earlier page: u1"]
    assert "about" not in lib.journeys
    order = [i.view_id for i in lib.cases[0].items]
    assert order[:2] == ["landing", "chat"]  # steps of one journey sit together, in order


def test_proofs_read_as_sentences():
    assert plain_proof("ad ID present in the landing URL: 99") == "Ad ID 99 is in the landing URL"
    assert plain_proof("something new") == "something new"


def test_saved_pages_are_named_after_their_title():
    view = View(
        view_id="v", incident_id="1", kind=ViewKind.PAGE, label="1 · x.example",
        primary="Oferta dnia.html", meta={"saved_from": "https://x.example/?a=1"},
    )  # fmt: skip
    assert view_title(view) == ("Oferta dnia", "x.example")


# -- the real sample, model off ----------------------------------------------------------


@pytest.fixture(scope="module")
def sample_library(tmp_path_factory):
    if not SAMPLE.is_dir():
        pytest.skip("testing_sample/ not present")
    mp = pytest.MonkeyPatch()
    for key, value in (("FTF_EMBEDDER", "fake"), ("FTF_ENRICH", "off"),
                       ("OPENAI_API_KEY", ""), ("GEMINI_API_KEY", "")):  # fmt: skip
        mp.setenv(key, value)
    from ftf import pipeline
    from ftf.fusion import run_method
    from ftf.grouping import build_groups
    from ftf.ingest import ingest_path
    from ftf.registry import load_plugins
    from ftf.store import Store

    load_plugins()
    s = Store(":memory:")
    ingest_path(SAMPLE, s)
    pipeline.run_extract(s)
    pipeline.run_fingerprint(s)
    pipeline.run_compare_all(s)
    run_method(s)
    lib = build_library(s, build_groups(s, persist_funnel=False), tmp_path_factory.mktemp("d"))
    yield lib
    s.close()
    mp.undo()


def test_sample_campaigns_kits_and_journeys(sample_library):
    def labels(case, host):
        case = next(c for c in sample_library.cases if c.case_id == case)
        return [[label.text for label in i.labels] for i in case.items if i.host == host]

    first = labels("1", "civicdaybook.live")[0]
    second = labels("2", "acrosscommunities.live")[0]
    assert first[0].startswith("Campaign") and first[0] == second[0]
    assert any("Duplicate" in t for t in labels("2", "healthlinkq.pro")[0])
    case61 = next(c for c in sample_library.cases if c.case_id == "61")
    on_path = [i for i in case61.items if i.step]
    assert len(on_path) == 2 and on_path[0].step.position == 1
    decoy = next(i for i in case61.items if i.title.startswith("Glowpixel.org"))
    assert decoy.step is None and all(lab.kind != "campaign" for lab in decoy.labels)
    case14 = next(c for c in sample_library.cases if c.case_id == "14")
    journey = sample_library.journeys[next(i.view_id for i in case14.items if i.step)]
    assert journey.ads and "landing URL" in journey.proofs[0]


def test_a_flattened_folder_with_repeated_names_is_refused(tmp_path):
    with pytest.raises(IntakeError, match="choose the folder"):
        save_files(files(("main.js", "a"), ("main.js", "b")), tmp_path / "up")
    assert not (tmp_path / "up").exists()  # refused before anything is written


def test_missing_picture_files_give_no_thumbnail(store, tmp_path):
    shot = tmp_path / "case" / "shot.png"
    shot.parent.mkdir()
    shot.write_bytes(b"x")
    views = [
        View(view_id="here", incident_id="7", kind=ViewKind.IMAGE, label="7 · s", primary="shot.png"),
        View(view_id="gone", incident_id="7", kind=ViewKind.IMAGE, label="7 · g", primary="gone.png"),
    ]  # fmt: skip
    store.put_incident(Incident(incident_id="7", root=str(tmp_path / "case")), [], views)
    groups = Groups(campaign_of={}, campaigns={}, family_of={}, families={})
    lib = build_library(store, groups, tmp_path)
    assert lib.get("here").thumbnail == str(shot) and lib.get("gone").thumbnail is None


def test_an_add_that_links_an_older_item_leaves_a_note(store, tmp_path):
    views = [View(view_id="old", incident_id="7", kind=ViewKind.PAGE, label="7 · old.example")]
    _store_with(store, views)
    alone = Groups(campaign_of={"old": "c1"}, campaigns={"c1": ["old"]}, family_of={}, families={})
    before = build_library(store, alone, tmp_path)
    new = View(view_id="new", incident_id="8", kind=ViewKind.PAGE, label="8 · new.example")
    store.put_incident(Incident(incident_id="8", root="."), [], [new])
    linked = Groups(
        campaign_of={"old": "c1", "new": "c1"}, campaigns={"c1": ["old", "new"]},
        family_of={}, families={},
    )  # fmt: skip
    after = build_library(store, linked, tmp_path, previous=before)
    assert after.get("old").note == "New link to new.example"
    assert after.get("new").note is None  # it was not in the Library before


def test_library_rows_render_and_select(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    from ftf.store import Store

    data = tmp_path / "data"
    s = Store(data / "ftf.sqlite")
    views = [
        View(view_id=f"7:{n}", incident_id="7", kind=ViewKind.PAGE, label=f"7 · {n}.example")
        for n in ("first", "second")
    ]
    s.put_incident(Incident(incident_id="7", root=str(tmp_path)), [], views)
    s.close()
    monkeypatch.setenv("FTF_DATA_DIR", str(data))
    root = Path(__file__).resolve().parents[1]
    app = AppTest.from_file(str(root / "app/streamlit_app.py")).run()
    assert not app.exception
    titles = [b for b in app.button if b.label.endswith(".example")]
    assert [b.label for b in titles] == ["first.example", "second.example"]
    assert any("first.example" in m.value for m in app.markdown if m.value.startswith("#####"))
    titles[1].click().run()
    assert not app.exception
    assert app.session_state["selected"] == "7:second"
    assert any("second.example" in m.value for m in app.markdown if m.value.startswith("#####"))
