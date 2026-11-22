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
    bad = client.post("/api/material", data={"links": ["not a link"]})
    assert bad.status_code == 400 and "not a link" in bad.json()["detail"]
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
