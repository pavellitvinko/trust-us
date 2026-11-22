"""Face matching with a fake service: nothing here touches the network."""

import json
import stat

import pytest

from ftf.contracts import Incident, Purpose, State, View, ViewKind
from ftf.faces import client as fc
from ftf.faces.client import FaceServiceError
from ftf.faces.enroll import enroll, load_manifest
from ftf.grouping.suspicion import reason_summary_for_view
from ftf.perception import faces as pf
from ftf.perception.techniques import _Image
from ftf.registry import Context


def face(person=None, similarity=0.0):
    subjects = [{"subject": person, "similarity": similarity}] if person else []
    return {"subjects": subjects, "box": {"x_min": 1, "y_min": 2, "x_max": 3, "y_max": 4}}


class FakeService:
    def __init__(self, answers=None, error=None):
        self.answers, self.error = answers or {}, error
        self.added = []

    def recognize(self, image, threshold=0.8):
        if self.error:
            raise FaceServiceError(self.error)
        return self.answers.get(image, [])

    def add_example(self, subject, image):
        self.added.append((subject, image))
        return f"id-{len(self.added)}"


@pytest.fixture
def ctx(store, monkeypatch):
    view = View(view_id="v", incident_id="i", kind=ViewKind.IMAGE, label="v", primary="ad.jpg")
    store.put_incident(Incident(incident_id="i", root="."), [], [view])
    monkeypatch.setattr(pf, "view_images", lambda *a: [_Image("image/jpeg", b"img", "ad.jpg", "")])
    return Context(store)


def rows(ctx):
    return [o for o in ctx.store.observations("v") if o.namespace == pf.NAMESPACE]


def test_match_is_risk_evidence_with_its_source(ctx):
    pf.run_perceive_faces(ctx, client=FakeService({b"img": [face("Donald Tusk", 0.99)]}))
    row = rows(ctx)[0]
    assert row.value == {"person": "Donald Tusk", "similarity": 0.99}
    assert row.purpose is Purpose.RISK
    assert row.raw == pf.NOTE and row.source.locator == "image:box[1,2,3,4]"
    summary = reason_summary_for_view("v", rows(ctx))
    assert "possible face match to Donald Tusk" in summary.face_matches[0].summary
    assert summary.level == 0  # context only: a real photo is not suspicious by itself


def test_weak_or_unknown_face_is_not_stored(ctx):
    answers = {b"img": [face("Donald Tusk", pf.MATCH_SIMILARITY - 0.01), face()]}
    pf.run_perceive_faces(ctx, client=FakeService(answers))
    assert [o.state for o in rows(ctx)] == [State.ABSENT]
    assert rows(ctx)[0].value is None


@pytest.mark.parametrize("reason", ["unreachable", "http_500: boom"])
def test_service_failure_is_not_observable_and_keeps_earlier(ctx, reason):
    assert pf.run_perceive_faces(ctx, client=FakeService(error=reason))["unavailable"] == 1
    assert rows(ctx)[0].state is State.NOT_OBSERVABLE
    pf.run_perceive_faces(ctx, client=FakeService({b"img": [face("Donald Tusk", 0.99)]}))
    assert pf.run_perceive_faces(ctx, client=FakeService(error=reason))["kept"] == 1
    assert rows(ctx)[0].state is State.PRESENT


def test_not_configured_makes_no_rows(ctx, monkeypatch):
    monkeypatch.delenv("FTF_COMPREFACE_URL", raising=False)
    assert pf.run_perceive_faces(ctx)["status"] == "not_configured"
    assert rows(ctx) == []


def test_shipped_views_are_left_alone(store):
    view = View(
        view_id="v", incident_id="i", kind=ViewKind.IMAGE, label="v", meta={"shipped": True}
    )
    store.put_incident(Incident(incident_id="i", root="."), [], [view])
    assert pf.run_perceive_faces(Context(store), client=FakeService())["views"] == 0


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("FTF_DATA_DIR", str(tmp_path / "data"))


PEOPLE = {
    "A B": [{"title": "1", "url": "u1"}, {"title": "2", "url": "u2"}, {"title": "3", "url": "u3"}]
}


def test_enrol_anchors_on_first_photo_and_rejects_others(data):
    service = FakeService(
        {
            b"u1": [face()],
            b"u2": [face("A B", 0.9)],
            b"u3": [face("C D", 0.9), face("A B", 0.9)],
        }
    )
    report = enroll(service, PEOPLE, fetch=str.encode)
    assert [image for _, image in service.added] == [b"u1", b"u2"]
    assert report["people_ready"] == ["A B"]
    assert report["skipped"][0]["why"] == "2 faces"
    again = enroll(service, {"A B": PEOPLE["A B"][:2]}, fetch=str.encode)
    assert (again["added"], again["already"]) == (0, 2)


def test_group_photo_cannot_define_a_person(data):
    service = FakeService({b"u1": [face(), face()]})
    report = enroll(service, {"A B": PEOPLE["A B"][:1]}, fetch=str.encode)
    assert service.added == [] and report["people_missing"] == ["A B"]


def test_state_file_is_private(data):
    fc.write_state({"api_key": "secret"})
    assert stat.S_IMODE(fc.state_path().stat().st_mode) == 0o600


def test_manifest_has_only_free_licences_and_named_sources():
    people = load_manifest()
    assert set(people) == {
        "Karol Nawrocki",
        "Donald Tusk",
        "Rafał Brzoska",
        "Monika Olejnik",
        "Tomasz Lis",
    }
    for files in people.values():
        assert files
        for file in files:
            assert file["licence"].startswith(("CC BY", "CC0", "Public domain"))
            assert file["page"].startswith("https://commons.wikimedia.org/")


def test_no_face_answer_is_an_empty_list(monkeypatch):
    def refuse(*args):
        raise FaceServiceError(json.dumps({"message": "No face is found"}).join(["http_400: ", ""]))

    monkeypatch.setattr(fc, "_send", refuse)
    assert fc.FaceClient("http://x", "k").recognize(b"x") == []


class RejectsFirst(FakeService):
    def recognize(self, image, threshold=0.8):
        if image == b"big":
            raise FaceServiceError("http_400: too large")
        return super().recognize(image, threshold)


def test_one_rejected_image_keeps_matches_from_others(ctx, monkeypatch):
    images = [_Image("image/jpeg", b"big", "b.jpg", ""), _Image("image/jpeg", b"img", "a.jpg", "")]
    monkeypatch.setattr(pf, "view_images", lambda *a: images)
    pf.run_perceive_faces(ctx, client=RejectsFirst({b"img": [face("Donald Tusk", 0.99)]}))
    assert rows(ctx)[0].state is State.PRESENT


def test_rejected_image_cannot_prove_no_match(ctx, monkeypatch):
    images = [_Image("image/jpeg", b"big", "b.jpg", ""), _Image("image/jpeg", b"img", "a.jpg", "")]
    monkeypatch.setattr(pf, "view_images", lambda *a: images)
    pf.run_perceive_faces(ctx, client=RejectsFirst({b"img": []}))
    assert rows(ctx)[0].state is State.NOT_OBSERVABLE


def test_unreadable_images_keep_the_earlier_match(ctx, monkeypatch):
    from ftf.perception.techniques import _MediaError

    pf.run_perceive_faces(ctx, client=FakeService({b"img": [face("Donald Tusk", 0.99)]}))

    def broken(*args):
        raise _MediaError("x")

    monkeypatch.setattr(pf, "view_images", broken)
    assert pf.run_perceive_faces(ctx, client=FakeService())["kept"] == 1
    assert rows(ctx)[0].state is State.PRESENT


def test_person_removed_from_the_list_stops_matching(data):
    class Deleting(FakeService):
        deleted = ()

        def delete_example(self, image_id):
            self.deleted = (*self.deleted, image_id)

    service = Deleting({b"u1": [face()]})
    enroll(service, {"A B": PEOPLE["A B"][:1]}, fetch=str.encode)
    report = enroll(service, {}, fetch=str.encode)
    assert report["removed"] == 1 and service.deleted == ("id-1",)
    assert fc._read_state()["enrolled"] == {}


def test_reset_service_is_set_up_again(data, monkeypatch):
    fc.write_state({"api_key": "old", "email": "e", "password": "p", "registered": True})
    calls = []

    def send(url, method, body, headers):
        calls.append(url.rsplit("/admin", 1)[-1])
        if "/api/v1" in url:
            raise FaceServiceError("http_401: bad key")
        if url.endswith(("/apps", "/models")):
            return []
        return {"id": "app", "apiKey": "new"}

    monkeypatch.setattr(fc, "_send", send)
    monkeypatch.setattr(fc, "_login", lambda *a: "c=1")
    assert fc.bootstrap("http://x").api_key == "new"
    assert any(c.endswith("/user/register") for c in calls)


def test_dropped_response_is_a_service_error(monkeypatch):
    import http.client

    def dropped(*args, **kwargs):
        raise http.client.IncompleteRead(b"")

    monkeypatch.setattr(fc.urllib.request, "urlopen", dropped)
    with pytest.raises(FaceServiceError):
        fc._send("http://x", "GET", None, {})
