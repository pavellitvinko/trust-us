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


def test_check_url_reads_credentials_as_credentials():
    assert intake.check_url("user:pass@offer.live/x") == "https://user:pass@offer.live/x"


def test_check_url_keeps_a_url_inside_the_query():
    text = "offer.live/login?next=https://offer.live/home"
    assert intake.check_url(text) == f"https://{text}"


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "169.254.169.254",
        "::1",
        "::ffff:10.0.0.1",
        "64:ff9b::a9fe:a9fe",  # NAT64 of 169.254.169.254
        "2002:a9fe:a9fe::1",  # 6to4 of 169.254.169.254
        "224.0.0.1",
        "not an address",
    ],
)
def test_non_public_addresses(address):
    from ftf.capture.network import is_public

    assert not is_public(address)


def test_public_addresses():
    from ftf.capture.network import is_public

    assert is_public("93.184.216.34") and is_public("2606:2800:220:1:248:1893:25c8:1946")


@pytest.mark.parametrize(
    "text",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "ftp://x.org/",
        "https://a b.com",
        "mailto:x@y.pl",
        "http:/offer.live",
    ],
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


@pytest.fixture
def public_dns(monkeypatch):
    """Test hosts count as public without a real DNS lookup."""
    monkeypatch.setattr(intake, "blocked_address", lambda host: None)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8080/admin",
        "http://localhost/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/",
        "http://[::1]/",
        "http://[::ffff:192.168.1.1]/",
        "http://0.0.0.0/",
    ],
)
def test_local_addresses_are_not_captured(store, tmp_path, monkeypatch, url):
    monkeypatch.setenv("FTF_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv(intake.ALLOW_PRIVATE_ENV, raising=False)
    opened = []
    monkeypatch.setattr(intake, "_capture", lambda *args: opened.append(args))
    with pytest.raises(IntakeError, match="private network"):
        intake.capture_and_run(store, url, enrich=False)
    assert not opened and not (tmp_path / "data" / "captures").exists()


def test_local_addresses_can_be_allowed(monkeypatch):
    monkeypatch.setenv(intake.ALLOW_PRIVATE_ENV, "1")
    intake.check_public("http://127.0.0.1/")


def test_a_host_resolving_to_a_private_address_is_refused(monkeypatch):
    from ftf.capture import network

    monkeypatch.setattr(network, "resolve", lambda host: ["93.184.216.34", "192.168.0.10"])
    assert network.blocked_address("rebind.example") == "192.168.0.10"
    monkeypatch.setattr(network, "resolve", lambda host: ["93.184.216.34"])
    assert network.blocked_address("example.com") is None
    monkeypatch.setattr(network, "resolve", lambda host: [])
    assert network.blocked_address("gone.example") is None  # a dead domain is still captured


def test_url_capture_reaches_every_stage(store, tmp_path, monkeypatch, public_dns):
    monkeypatch.setenv("FTF_EMBEDDER", "fake")
    monkeypatch.setenv("FTF_DATA_DIR", str(tmp_path / "data"))
    opened = []

    def fake_capture(url, out_dir, replay_har, timeout, public_only):
        opened.append(url)
        assert public_only
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


def test_browser_failure_is_a_plain_error(store, tmp_path, monkeypatch, public_dns):
    monkeypatch.setenv("FTF_DATA_DIR", str(tmp_path / "data"))

    def broken(*_args):
        raise RuntimeError("Executable doesn't exist at /ms-playwright/chromium\nmore")

    monkeypatch.setattr(intake, "_capture", broken)
    with pytest.raises(IntakeError, match="playwright install chromium"):
        intake.capture_and_run(store, "https://offer.live/")
    assert not store.views()


def _half_capture(error):
    def capture(url, out_dir, *_args):
        out_dir.mkdir(parents=True)
        (out_dir / "manifest.json").write_text("{}")
        if error:
            raise error
        return _Result(out_dir)

    return capture


@pytest.mark.parametrize(
    ("error", "match"),
    [
        (ValueError("/srv/data/uploads/abc/x.har: no HTML document"), "no HTML document"),
        (PermissionError(13, "Permission denied"), "could not be saved: Permission denied"),
        (RuntimeError("Target closed"), "browser could not run"),
        (None, "no views"),  # captured, but nothing to ingest
    ],
)
def test_failed_capture_leaves_no_folder(store, tmp_path, monkeypatch, public_dns, error, match):
    monkeypatch.setenv("FTF_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(intake, "_capture", _half_capture(error))
    with pytest.raises(IntakeError, match=match):
        intake.capture_and_run(store, "https://offer.live/", enrich=False)
    assert not list((tmp_path / "data" / "captures").iterdir())


def test_replay_error_hides_the_server_path(store, tmp_path, monkeypatch):
    monkeypatch.setenv("FTF_DATA_DIR", str(tmp_path / "data"))
    har = tmp_path / "srv" / "x.har"

    def broken(url, out_dir, replay_har, *_args):
        raise ValueError(f"{replay_har}: not a HAR")

    monkeypatch.setattr(intake, "_capture", broken)
    with pytest.raises(IntakeError) as exc:
        intake.capture_and_run(store, None, replay_har=har)
    assert str(exc.value) == "x.har: not a HAR"


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
