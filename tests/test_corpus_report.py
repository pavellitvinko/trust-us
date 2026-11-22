"""Corpus report and export."""

import csv
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ftf import pipeline
from ftf.contracts import (
    Incident,
    Observation,
    Relation,
    RelationType,
    Source,
    State,
    View,
    ViewKind,
)
from ftf.corpus_report import (
    _cell,
    _cleaner,
    _commit,
    _safe_csv,
    build_corpus_report,
    export_files,
    render_corpus_report,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "testing_sample"
TOKEN = "EAAUrealsecrettokenvalue123"


def obs(view, namespace, value, state=State.PRESENT):
    return Observation(
        view_id=view,
        signal_id="X",
        namespace=namespace,
        value=value,
        raw=value if isinstance(value, str) else None,
        state=state,
        source=Source(file="p.html", locator="html"),
        extractor="test",
        version="1",
    )


@pytest.fixture
def filled(store, tmp_path, monkeypatch):
    monkeypatch.setenv("FTF_DATA_DIR", str(tmp_path / "data"))
    views = [
        View(view_id=v, incident_id="i", kind=ViewKind.PAGE, label=v, primary="p.html")
        for v in ("a", "b")
    ]
    store.put_incident(Incident(incident_id="i", root="/home/someone/case"), [], views)
    store.put_observations(
        "a",
        "test",
        [
            obs("a", "ad.access_token", TOKEN),
            obs("a", "url.full", f"https://x.pl/?access_token={TOKEN}"),
            obs("a", "page.clean_text", "Pełny tekst strony"),
            obs("a", "page.title", "=HYPERLINK('http://evil')"),
            obs("a", "expect.element", None, State.NOT_OBSERVABLE),
        ],
    )
    store.put_relation(
        Relation(view_a="a", view_b="b", type=RelationType.INSUFFICIENT, tier_scores={"T1": 0.0})
    )
    return store


def test_export_hides_secrets_text_and_local_paths(filled):
    files = export_files(filled)
    blob = "\n".join(files.values())
    assert TOKEN not in blob
    assert "Pełny tekst" not in blob
    assert "/home/someone" not in blob
    assert json.loads(files["report.json"])["export"]["whole_page_text_observations_omitted"] == 1


def test_csv_cannot_run_formulas(filled):
    rows = list(csv.DictReader(io.StringIO(export_files(filled)["observations.csv"])))
    assert next(r for r in rows if r["namespace"] == "page.title")["value"].startswith("'=")
    assert _safe_csv("-1+1") == "'-1+1"


def test_missing_model_stages_are_marked_not_absent(filled):
    report = build_corpus_report(filled)
    assert report["model_sections"]["patterns"]["status"] == "not_run"
    assert report["model_sections"]["expected_elements"]["status"] == "unavailable"
    text = render_corpus_report(report)
    assert "Unavailable: pattern discovery has not been run." in text
    assert any("expected elements" in q for q in report["open_questions"])


def test_report_does_not_change_the_store(filled):
    query = "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM observations"
    before = filled.db.execute(query).fetchone()
    export_files(filled)
    assert filled.db.execute(query).fetchone() == before


def test_markdown_cells_cannot_break_the_table(filled):
    filled.put_incident(
        Incident(incident_id="i", root="."),
        [],
        [View(view_id="a", incident_id="i", kind=ViewKind.PAGE, label="x | <script>", primary="p")],
    )
    text = render_corpus_report(build_corpus_report(filled))
    assert "x \\| &lt;script>" in text


@pytest.fixture
def linked_pair(store, tmp_path, monkeypatch):
    monkeypatch.setenv("FTF_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FTF_EMBEDDER", "fake")
    views = [
        View(view_id=v, incident_id="i", kind=ViewKind.PAGE, label=v, primary="p.html")
        for v in ("a", "b")
    ]
    store.put_incident(Incident(incident_id="i", root="."), [], views)
    page = "Zdanie jedyne w swoim rodzaju opisuje ofertę krok po kroku dla każdego czytelnika"
    for view in ("a", "b"):
        store.put_observations(
            view,
            "test",
            [
                obs(view, "page.clean_text", page),
                obs(view, "page.body_text", page),
                obs(view, "aff.sid", "1"),
                obs(view, "domain.registrable", "shared.example"),
            ],
        )
    pipeline.run_fingerprint(store)
    pipeline.run_compare_all(store)
    store.put_relation(
        Relation(
            view_a="a",
            view_b="b",
            type=RelationType.DUPLICATE,
            confidence="high",
            tier_scores={"T2": 1.0},
        )
    )
    return store, page


def test_pair_reports_hold_no_page_text_and_survive_short_secrets(linked_pair):
    store, page = linked_pair
    files = export_files(store)
    blob = "\n".join(files.values())
    assert page not in blob
    assert "jedyne w swoim rodzaju" not in blob
    assert json.loads(files["pairs.json"])[0]["layers"]


def test_not_comparable_layers_are_counted(linked_pair):
    store, _ = linked_pair
    assert build_corpus_report(store)["not_comparable_layers_in_linked_pairs"]


def test_cleaning_keeps_urls_and_hashes_every_secret_param(store):
    view = View(view_id="v", incident_id="i", kind=ViewKind.PAGE, label="p")
    store.put_incident(Incident(incident_id="i", root="."), [], [view])
    secret = "api-key-value-1234567890-abcdefgh"
    store.put_observations("v", "test", [obs("v", "kit.api_key_id", secret)])
    clean = _cleaner(store)
    url = clean("https://shop.example/l?refresh_token=abc&client_secret=def&id_token=ghi&q=ok")
    assert url.startswith("https://shop.example/l?refresh_token=[REDACTED")
    assert "abc" not in url and "def" not in url and "ghi" not in url and "q=ok" in url
    assert "author=Jan" in clean("https://x.pl/?author=Jan")
    assert secret[:23] not in clean(f"demoted to kit: api_key_id={secret[:23]}…")


def test_markdown_backslash_cannot_undo_escaping():
    assert _cell("\\[x](http://evil)") == "\\\\\\[x](http://evil)"
    assert _cell("a\\|b") == "a\\\\\\|b"


def test_commit_belongs_to_this_code(tmp_path, monkeypatch):
    expected = _commit()
    monkeypatch.chdir(tmp_path)
    assert _commit() == expected


@pytest.mark.skipif(not SAMPLE.is_dir(), reason="testing_sample/ not present")
def test_one_command_on_the_sample_without_models(tmp_path):
    env = {
        **os.environ,
        "FTF_DATA_DIR": str(tmp_path / "data"),
        "TLDEXTRACT_CACHE": str(tmp_path / "tld"),
        "FTF_EMBEDDER": "fake",
        "FTF_ENRICH": "off",
        "OPENAI_API_KEY": "",
        "GEMINI_API_KEY": "",
        "PYTHONPATH": os.pathsep.join((str(ROOT / "src"), str(ROOT))),
    }

    def cli(*args):
        audit = tmp_path / f"{args[0]}.json"
        result = subprocess.run(
            [sys.executable, "-m", "tests._model_off_cli", *args],
            cwd=ROOT,
            env={**env, "FTF_MODEL_OFF_AUDIT": str(audit)},
            text=True,
            capture_output=True,
            timeout=240,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(audit.read_text()) == {"model": [], "network": []}

    cli("ingest", str(SAMPLE))
    out = tmp_path / "export"
    cli("report", "--out", str(out))
    report = json.loads((out / "report.json").read_text())
    pairs = json.loads((out / "pairs.json").read_text())
    relations = json.loads((out / "relations.json").read_text())
    linked = [r for r in relations if r["type"] not in ("unrelated", "insufficient_evidence")]
    assert {(p["objects"]["a"]["view_id"], p["objects"]["b"]["view_id"]) for p in pairs} == {
        (r["view_a"], r["view_b"]) for r in linked
    }
    for pair in pairs:
        assert pair["confidence"]["basis"]["path"] and pair["layers"]
        assert pair["coverage"]["total_groups"] > 0
        assert pair["stored_relation"]["agrees"] is True
    assert report["recomputation_disagreements"] == []
    assert all(p["decision_path"] for p in report["linked_pairs"])
    paths = {(p["relation"], p["decision_path"]) for p in report["linked_pairs"]}
    assert ("same_family", "identity_solo") in paths  # the 61 business page, HAC-54
    assert report["inventory"]["views"] and report["pair_matrix"]
    kinds = {link["link"] for c in report["campaigns"] for link in c["links"]}
    assert "direct" in kinds
    assert kinds & {"funnel", "direct_and_funnel"}
    assert str(SAMPLE.resolve()) not in (out / "report.md").read_text()
