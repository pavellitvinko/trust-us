"""Add-material and Material screen data."""

import io
import stat
import zipfile
from pathlib import Path

import pytest

from ftf import intake
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


# --- URL input (HAC-45 step 3) ------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://offer.live/a?b=1", "https://offer.live/a?b=1"),
        ("offer.live/landing", "https://offer.live/landing"),
        ("  http://offer.live  ", "http://offer.live"),
        ("", ""),
    ],
)
def test_check_url(text, expected):
    assert intake.check_url(text) == expected


@pytest.mark.parametrize(
    "text", ["file:///etc/passwd", "javascript:alert(1)", "ftp://x.org/", "https://a b.com"]
)
def test_check_url_refuses_other_schemes(text):
    with pytest.raises(IntakeError):
        intake.check_url(text)


class _Manifest:
    mode, page_url, final_url, error, redirects = "live", "https://offer.live/", None, None, []

    class status:
        value = "ok"


class _Result:
    def __init__(self, bundle):
        self.bundle, self.manifest, self.ok = bundle, _Manifest(), True


def test_url_capture_reaches_every_stage(store, tmp_path, monkeypatch):
    monkeypatch.setenv("FTF_EMBEDDER", "fake")
    monkeypatch.setenv("FTF_DATA_DIR", str(tmp_path / "data"))
    opened = []

    def fake_capture(url, out_dir, replay_har, timeout):
        opened.append(url)
        out_dir.mkdir(parents=True)
        (out_dir / "page.html").write_text(
            "<html><head><title>Oferta</title></head><body><p>Treść.</p></body></html>",
            encoding="utf-8",
        )
        return _Result(out_dir)

    monkeypatch.setattr(intake, "_capture", fake_capture)
    seen = []
    result = intake.capture_and_run(
        store, "offer.live", lambda name, i, n: seen.append((name, i, n)), enrich=False
    )
    assert opened == ["https://offer.live"]
    assert [name for name, *_ in seen] == list(intake.CAPTURE_STAGES)
    assert [i for _, i, _ in seen] == list(range(len(intake.CAPTURE_STAGES)))
    assert result["views"] == 1 and result["capture"]["status"] == "ok"
    assert result["incidents"][0].startswith("offer.live-")
    again = intake.capture_and_run(store, "https://offer.live", enrich=False)  # a second look
    assert again["views"] == 1 and again["incidents"] != result["incidents"]


def test_url_input_needs_a_url_or_a_har(store):
    with pytest.raises(IntakeError, match="URL"):
        intake.capture_and_run(store, "  ")


def test_browser_failure_is_a_plain_error(store, tmp_path, monkeypatch):
    monkeypatch.setenv("FTF_DATA_DIR", str(tmp_path / "data"))

    def broken(*_args):
        raise RuntimeError("Executable doesn't exist at /ms-playwright/chromium\nmore")

    monkeypatch.setattr(intake, "_capture", broken)
    with pytest.raises(IntakeError, match="playwright install chromium"):
        intake.capture_and_run(store, "https://offer.live/")
    assert not store.views()


def test_save_upload_keeps_only_the_file_name(tmp_path):
    path = intake.save_upload(io.BytesIO(b"{}"), "../../evil.har", tmp_path / "uploads")
    assert path.name == "evil.har" and path.is_relative_to(tmp_path / "uploads")


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
    assert any(b.label == "Analyse material" for b in app.button)
