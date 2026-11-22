"""Grounding profiles on genuine pages. Fake capture and model only."""

import json

import pytest

from ftf.config import Settings
from ftf.contracts import Observation, Source, State
from ftf.models import ModelResult
from ftf.perception import expectations as ex
from ftf.perception import grounding as gr

TEXT = "Regulamin serwisu jest dostępny. Administratorem danych jest ACME. " * 12


def element(name, wording):
    return ex.ProfileElement(
        name=name,
        description=name,
        basis="legal_requirement",
        basis_detail="rule",
        polish_wordings=[wording],
        expected_location="footer",
    )


ELEMENTS = {
    "terms": element("Terms", "regulamin"),
    "controller": element("Controller", "administratorem"),
    "licence": element("Licence", "licencja"),
}


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path / "data",
        openai_api_key="",
        gemini_api_key="",
        openai_model="m",
        embedding_model="e",
        video_model="v",
    )


@pytest.mark.parametrize(
    ("present", "absent", "decision", "rate"),
    [
        (2, 0, "kept", 0.0),
        (1, 1, "downgraded", 0.5),
        (0, 2, "dropped", 1.0),
        (1, 0, "unverified", None),
        (0, 1, "unverified", None),
    ],
)
def test_decisions(present, absent, decision, rate):
    assert gr.decide(present, absent) == (decision, rate)


def entry(**decisions):
    return {
        "k": {
            "status": "grounded",
            "profile": "hash",
            "elements": {
                key: {"decision": value, "absent": 1, "checked": 2}
                for key, value in decisions.items()
            },
            "added": {"cookie": element("Cookie", "ciasteczka").model_dump()},
        }
    }


def test_apply_drops_downgrades_and_adds():
    kept, weak = gr.apply_grounding(
        "k", ELEMENTS, "hash", entry(terms="kept", controller="downgraded", licence="dropped")
    )
    assert set(kept) == {"terms", "controller", "cookie"}
    assert "1 of 2 genuine pages" in weak["controller"]


def test_results_for_another_profile_version_are_ignored():
    kept, weak = gr.apply_grounding("k", ELEMENTS, "new", entry(licence="dropped"))
    assert kept == ELEMENTS and weak == {}


def test_weak_absence_is_labelled():
    row = Observation(
        view_id="v",
        signal_id="K",
        namespace=ex.ELEMENT_NAMESPACE,
        value="controller",
        raw="expected element not found: Controller",
        state=State.ABSENT,
        source=Source(file="p"),
        extractor="x",
        version="1",
    )
    marked = ex._mark_weak(row, {"controller": "missing on 1 of 2 genuine pages"})
    assert marked.confidence == 0.5 and "genuine pages" in marked.raw


def page(url, **states):
    return {"url": url, "status": "checked", "states": states, "text": TEXT}


def test_ground_kind_scores_pages_and_verifies_additions(tmp_path, settings, monkeypatch):
    monkeypatch.setattr(gr, "_profile", lambda *a, **k: (ELEMENTS, "hash", None))
    pages = {
        "u1": page("u1", terms="present", controller="present", licence="absent"),
        "u2": page("u2", terms="present", controller="absent", licence="absent"),
    }
    monkeypatch.setattr(gr, "_check_page", lambda url, *a: pages[url])
    good = gr.AddedElement(
        name="Data controller note",
        description="d",
        polish_wordings=["administratorem"],
        expected_location="footer",
        quotes=[
            gr.PageQuote(page_index=0, quote="Administratorem danych"),
            gr.PageQuote(page_index=1, quote="Administratorem danych"),
        ],
    )
    invented = good.model_copy(
        update={
            "name": "Invented",
            "quotes": [
                gr.PageQuote(page_index=0, quote="Administratorem danych"),
                gr.PageQuote(page_index=1, quote="nie ma tego na stronie"),
            ],
        }
    )
    monkeypatch.setattr(
        gr,
        "read_structured",
        lambda *a, **k: ModelResult.ok(gr.AddedElements(elements=[good, invented])),
    )
    result = gr.ground_kind("k", ("u1", "u2"), tmp_path, settings=settings)
    assert result["elements"]["terms"]["decision"] == "kept"
    assert result["elements"]["controller"]["false_alarm_rate"] == 0.5
    assert result["elements"]["licence"]["decision"] == "dropped"
    assert set(result["added"]) == {"data_controller_note"}
    assert result["pages"][0]["control_present_share"] == 1.0
    assert result["pages"][1]["control_present_share"] == 0.5


def test_blocked_pages_do_not_count_as_grounding(tmp_path, settings, monkeypatch):
    monkeypatch.setattr(gr, "_profile", lambda *a, **k: (ELEMENTS, "hash", None))
    monkeypatch.setattr(gr, "GENUINE_PAGES", {"k": ("u1", "u2")})
    result = gr.run_grounding(
        ["k"], settings=settings, capture=lambda url, folder: (None, "http_error")
    )
    assert result["k"]["status"] == "too_few_pages"
    assert gr.apply_grounding("k", ELEMENTS, "hash", result)[0] == ELEMENTS


def test_failed_run_keeps_the_earlier_grounding(tmp_path, settings, monkeypatch):
    path = gr.grounding_path(settings)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema": gr.SCHEMA, "kinds": entry(licence="dropped")}))
    monkeypatch.setattr(gr, "_profile", lambda *a, **k: ({}, "", "timeout"))
    monkeypatch.setattr(gr, "GENUINE_PAGES", {"k": ("u1", "u2")})
    assert gr.run_grounding(["k"], settings=settings)["k"]["status"] == "grounded"


def test_damaged_file_is_ignored(settings):
    path = gr.grounding_path(settings)
    path.parent.mkdir(parents=True)
    path.write_text("{broken")
    assert gr.load_grounding(settings) == {}
