"""Add-material and Material screen data."""

import io
import stat
import zipfile
from pathlib import Path

import pytest

from ftf.contracts import Incident, Observation, Source, State, View, ViewKind
from ftf.intake import IntakeError, add_zip, allowed_folder, run_stages, safe_extract
from ftf.material import material_summary

ROOT = Path(__file__).resolve().parents[1]


def archive(entries):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as bundle:
        for name, body in entries:
            bundle.writestr(name, body)
    data.seek(0)
    return data


@pytest.mark.parametrize("name", ["../evil.txt", "a/../../evil.txt", "/evil.txt"])
def test_zip_cannot_escape_its_folder(tmp_path, name):
    target = tmp_path / "out"
    target.mkdir()
    with pytest.raises(IntakeError):
        safe_extract(archive([(name, "x")]), target)
    assert not (tmp_path / "evil.txt").exists()


def test_zip_links_are_refused(tmp_path):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as bundle:
        link = zipfile.ZipInfo("link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        bundle.writestr(link, "/etc/passwd")
    data.seek(0)
    with pytest.raises(IntakeError):
        safe_extract(data, tmp_path)


def test_bad_upload_leaves_nothing_behind(tmp_path):
    with pytest.raises(IntakeError):
        add_zip(io.BytesIO(b"not a zip"), "x.zip", tmp_path / "uploads")
    assert not list((tmp_path / "uploads").iterdir())


def test_uploaded_folder_reaches_every_stage(store, tmp_path, monkeypatch):
    monkeypatch.setenv("FTF_EMBEDDER", "fake")
    html = "<html><head><title>Oferta</title></head><body><p>Treść strony.</p></body></html>"
    folder = add_zip(archive([("case/page.html", html)]), "case.zip", tmp_path / "uploads")
    seen = []
    monkeypatch.setenv("FTF_DATA_DIR", str(tmp_path / "data"))
    result = run_stages(store, folder, lambda name, *_: seen.append(name), enrich=False)
    assert seen == ["ingest", "extract", "enrich", "fingerprint", "compare", "group"]
    assert result["views"] == 1


def test_empty_material_is_an_error(store, tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(IntakeError):
        run_stages(store, tmp_path / "empty")


def test_rejected_material_keeps_the_stored_incident(store, tmp_path, monkeypatch):
    monkeypatch.setenv("FTF_EMBEDDER", "fake")
    monkeypatch.setenv("FTF_DATA_DIR", str(tmp_path / "data"))
    case = tmp_path / "case"
    case.mkdir()
    (case / "page.html").write_text("<html><body><p>Strona</p></body></html>")
    run_stages(store, case, enrich=False)
    before = [v.view_id for v in store.views()]
    with pytest.raises(IntakeError, match="already ingested"):
        run_stages(store, case, enrich=False)
    (case / "page.html").unlink()
    (case / "n.txt").write_text("x")
    with pytest.raises(IntakeError, match="no views"):
        run_stages(store, case, enrich=False)
    assert [v.view_id for v in store.views()] == before


def test_two_uploads_with_one_name_stay_separate(tmp_path):
    first = add_zip(archive([("case/p.html", "x")]), "case.zip", tmp_path / "u")
    second = add_zip(archive([("case/p.html", "y")]), "case.zip", tmp_path / "u")
    assert next(first.iterdir()).name != next(second.iterdir()).name


@pytest.mark.parametrize("entries", [[("a", "x"), ("a/b", "y")]])
def test_unpackable_zip_is_a_clean_error(tmp_path, entries):
    with pytest.raises(IntakeError):
        add_zip(archive(entries), "bad.zip", tmp_path / "u")
    assert not list((tmp_path / "u").iterdir())


def test_server_folder_needs_an_allowed_root(tmp_path):
    with pytest.raises(IntakeError):
        allowed_folder(str(tmp_path), None)
    with pytest.raises(IntakeError):
        allowed_folder("/etc", str(tmp_path))
    assert allowed_folder(str(tmp_path / "x"), str(tmp_path)) == (tmp_path / "x").resolve()


def test_missing_model_stages_are_explicit(store):
    view = View(view_id="v", incident_id="i", kind=ViewKind.PAGE, label="p")
    store.put_incident(Incident(incident_id="i", root="."), [], [view])
    summary = material_summary(store, "v")
    assert summary["expectations"]["status"] == "not_run"
    assert summary["patterns"]["status"] == "not_run"
    failed = Observation(
        view_id="v",
        signal_id="K",
        namespace="expect.element",
        raw="timeout",
        state=State.NOT_OBSERVABLE,
        source=Source(file="p.html"),
        extractor="perception.expectations",
        version="1",
    )
    store.put_observations("v", failed.extractor, [failed])
    assert material_summary(store, "v")["expectations"]["status"] == "unavailable"


def test_interface_opens_on_an_empty_store(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("FTF_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    app = AppTest.from_file(str(ROOT / "app/streamlit_app.py")).run()
    assert not app.exception
    assert any(b.label == "Analyse" for b in app.button)
