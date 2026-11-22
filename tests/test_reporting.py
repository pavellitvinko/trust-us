"""Completeness and provenance of the analyst-facing comparison report."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ftf import pipeline
from ftf.compare.weights import weights
from ftf.contracts import (
    Fingerprint,
    FingerprintKind,
    Incident,
    Observation,
    Source,
    State,
    View,
    ViewKind,
)
from ftf.eval.reference import resolve_view
from ftf.fusion import run_method
from ftf.layers._embedder import FakeEmbedder, set_embedder
from ftf.registry import layers, load_plugins
from ftf.reporting import build_pair_report, render_pair_report
from ftf.store import Store

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def report_store(tmp_path):
    s = Store(tmp_path / "data" / "ftf.sqlite")
    load_plugins()
    set_embedder(FakeEmbedder())
    for side in ("a", "b"):
        view = View(
            view_id=side,
            incident_id=side,
            kind=ViewKind.PAGE,
            label=f"Page {side}",
            primary="page.html",
        )
        s.put_incident(Incident(incident_id=side, root=str(tmp_path / side)), [], [view])
        values = [
            ("aio.campaign_uuid", "same-campaign"),
            ("fb_pixel", "123456789"),
            ("domain.registrable", f"{side}.example.org"),
            ("page.body_text", " ".join(f"word{i}" for i in range(50)) + f" tail {side}"),
            ("dom.class_set", ["special-heading", "custom-article", f"offer-{side}"]),
            ("url.param_name", "utm_source"),  # observed, but deliberately ignored
            ("asset.sha256.noise", "shared-library-hash"),
        ]
        if side == "a":
            values.append(("form.hidden_name", "tracking-field"))
        obs = [
            Observation(
                view_id=side,
                namespace=ns,
                value=value,
                signal_id="F1",
                extractor="synthetic",
                version="1",
                source=Source(file="page.html", locator=f"html:{ns}"),
            )
            for ns, value in values
        ]
        obs.append(
            Observation(
                view_id=side,
                namespace="pattern.score",
                state=State.NOT_OBSERVABLE,
                extractor="synthetic",
                version="1",
                signal_id="H1",
                source=Source(file="page.html", locator="model:disabled"),
            )
        )
        s.put_observations(side, "synthetic", obs)
    pipeline.run_fingerprint(s)
    run_method(s)
    yield s
    set_embedder(None)
    weights.reset()
    s.close()


def assert_complete(report):
    assert report["objects"]["a"]["view_id"] and report["objects"]["b"]["view_id"]
    assert (
        report["confidence"]["label"]
        and "not a calibrated probability" in report["confidence"]["meaning"]
    )
    coverage = report["coverage"]
    assert coverage["total_groups"] > 0
    assert coverage["value"] == pytest.approx(
        coverage["comparable_groups"] / coverage["total_groups"], abs=0.0001
    )
    assert report["reasons"] and report["tiers"] and report["rules"]
    assert report["confidence"]["basis"]["rule"]
    assert {row["layer_id"] for row in report["layers"]} == set(layers.names())
    for row in report["layers"]:
        assert row["tier"] and row["group"] and row["comparator"] and row["layer_version"]
        if row["status"] == "not_comparable":
            assert row["score"] is None and row["missing"]
            assert all(m["reason"] for m in row["missing"])
        for item in row["shared"]:
            assert item["provenance"] == "resolved", (row["layer_id"], item)
            for side in ("a", "b"):
                assert item["sources"][side]
                for ref in item["sources"][side]:
                    obs = report["sources"][ref]
                    assert obs["view_id"] == report["objects"][side]["view_id"]
                    assert obs["source"]["file"] and obs["source"]["locator"]
                    assert obs["extractor"] and obs["version"]
        for difference in row["differing"]:
            for side in ("a", "b"):
                for item in difference[side]:
                    assert item["sources"] and item["provenance"] == "resolved"
    rendered = render_pair_report(report)
    for heading in (
        "Objects",
        "Decision",
        "Tier scores",
        "Evidence, differences and missing features",
        "Source observations",
        "Scope and limitations",
    ):
        assert heading in rendered
    assert "A source:" in rendered and "B source:" in rendered
    assert "not_comparable" in rendered and "Differs" in rendered


def test_report_completeness_and_read_only_provenance(report_store):
    before = report_store.db.total_changes
    report = build_pair_report(report_store, "a", "b")
    assert report_store.db.total_changes == before
    assert_complete(report)
    rows = {r["layer_id"]: r for r in report["layers"]}
    assert rows["kit.form"]["missing"][0]["side"] == "b"
    assert rows["pattern.mo"]["missing"][0]["observed_states"] == ["not_observable"]
    assert rows["kit.url"]["ignored"][0]["value"] == "utm_source"
    assert any(o["ns"] == "asset.sha256.noise" for o in report["ignored_observations"])
    text = rows["text.shingle"]
    assert (
        text["details"]["shared_values"] and text["details"]["only_a"] and text["details"]["only_b"]
    )
    assert text["shared"][0]["evidence_kind"] == "derived_metric"
    assert all(
        report["sources"][ref]["namespace"] == "page.body_text"
        for ids in text["shared"][0]["sources"].values()
        for ref in ids
    )
    reverse = build_pair_report(report_store, "b", "a")
    diff = next(r for r in reverse["layers"] if r["layer_id"] == "ids.domain")["differing"][0]
    assert diff["a"][0]["value"] == "b.example.org"


def test_incompatible_vectors_and_unknown_provenance_are_explicit(report_store):
    for side in ("a", "b"):
        report_store.put_fingerprint(
            Fingerprint(
                view_id=side,
                layer_id="text.semantic",
                version="1",
                kind=FingerprintKind.VECTOR,
                payload={"model": f"model-{side}", "values": [0.5, 0.5]},
            )
        )
    report = build_pair_report(report_store, "a", "b")
    row = next(r for r in report["layers"] if r["layer_id"] == "text.semantic")
    assert row["status"] == "not_comparable" and row["missing"][0]["side"] == "pair"
    assert row["details"]["models"] == {"a": "model-a", "b": "model-b"}
    report_store.put_observations(
        "a",
        "untraceable",
        [
            Observation(
                view_id="a",
                namespace="aio.campaign_uuid",
                value="same-campaign",
                extractor="untraceable",
                version="1",
                signal_id="A1",
            )
        ],
    )
    report = build_pair_report(report_store, "a", "b")
    assert any(s["provenance"] == "unavailable" for s in report["sources"].values())
    campaign = next(row for row in report["layers"] if row["layer_id"] == "ids.campaign")
    assert campaign["shared"][0]["provenance"] == "partial"


def test_interface_uses_the_complete_report(report_store, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv(
        "FTF_DATA_DIR",
        str(Path(report_store.db.execute("PRAGMA database_list").fetchone()[2]).parent),
    )
    app = AppTest.from_file(str(ROOT / "app/streamlit_app.py")).run()
    assert not app.exception
    next(b for b in app.button if b.label == "Explain comparison").click().run()
    assert not app.exception
    assert_complete(app.session_state["comparison_report"])


@pytest.mark.skipif(not (ROOT / "testing_sample").is_dir(), reason="testing_sample/ not present")
def test_real_sample_report_completeness_without_models(tmp_path):
    env = {
        **os.environ,
        "FTF_DATA_DIR": str(tmp_path / "data"),
        "TLDEXTRACT_CACHE": str(tmp_path / "tld-cache"),
        "FTF_EMBEDDER": "fake",
        "FTF_ENRICH": "off",
        "OPENAI_API_KEY": "",
        "GEMINI_API_KEY": "",
        "PYTHONPATH": os.pathsep.join((str(ROOT / "src"), str(ROOT))),
    }

    def cli(*args):
        audit = tmp_path / "audit.json"
        result = subprocess.run(
            [sys.executable, "-m", "tests._model_off_cli", *args],
            env={**env, "FTF_MODEL_OFF_AUDIT": str(audit)},
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(audit.read_text()) == {"model": [], "network": []}
        return result.stdout

    cli("ingest", str(ROOT / "testing_sample"))
    load_plugins()
    s = Store(tmp_path / "data/ftf.sqlite")
    try:
        for a, b, relation in (
            ("1:page", "2:page", "same_campaign"),
            ("2:healthlinkq", "3:page", "duplicate"),
            ("48:page", "61:article", "same_family"),
            ("61:article", "61:chat", "same_campaign"),
        ):
            va, vb = resolve_view(s, a), resolve_view(s, b)
            report = json.loads(cli("explain", va, vb, "--format", "json"))
            assert_complete(report)
            assert report["relation"] == relation
            if a == "48:page":
                assert any(
                    d["exclusive_conflict"] for row in report["layers"] for d in row["differing"]
                )
                assert any(i["demoted_to_kit"] for row in report["layers"] for i in row["shared"])
            output = tmp_path / f"{a.replace(':', '-')}-{b.replace(':', '-')}.md"
            cli("explain", va, vb, "--output", str(output))
            assert "Source observations" in output.read_text()
    finally:
        s.close()


def test_report_does_not_write_funnel_rows(report_store):
    views = [v.view_id for v in report_store.views()][:2]
    count = "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM observations"
    before = report_store.db.execute(count).fetchone()
    build_pair_report(report_store, *views)
    assert report_store.db.execute(count).fetchone() == before
