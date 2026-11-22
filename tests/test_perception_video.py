"""Video reading. A fake model only: nothing here touches the network."""

import pytest

from ftf.contracts import Incident, State, View, ViewKind
from ftf.layers import _text
from ftf.models import ModelResult
from ftf.patterns.matching import view_text
from ftf.perception import video as vi
from ftf.registry import Context

SPEECH = "Rafał Brzoska mówi, że Komisja KNF ostrzega. Wpłać tysiąc złotych."


def reading(**fields):
    body = {
        "language": "pl",
        "transcript": SPEECH,
        "on_screen_text": ["Zarabiaj dziś"],
        "scenes": [vi.Scene(start_seconds=1.0, description="A man speaks to camera")],
        "entities": [
            vi.VideoEntity(
                name="Rafał Brzoska", entity_type="person", role="endorser", quote="Rafał Brzoska"
            ),
            vi.VideoEntity(
                name="KNF",
                entity_type="organisation",
                role="regulator_invoked",
                quote="Komisja KNF",
            ),
        ],
    }
    return vi.VideoReading(**{**body, **fields})


@pytest.fixture
def ctx(store, tmp_path):
    (tmp_path / "ad.mp4").write_bytes(b"video")
    view = View(view_id="v", incident_id="i", kind=ViewKind.VIDEO, label="v", primary="ad.mp4")
    store.put_incident(Incident(incident_id="i", root=str(tmp_path)), [], [view])
    return Context(store)


def fake(monkeypatch, answer):
    calls = []

    def read(*args, **kwargs):
        calls.append(args)
        return answer

    monkeypatch.setattr(vi, "read_video", read)
    return calls


def values(ctx, namespace):
    return [o.value for o in ctx.store.observations("v") if o.namespace == namespace]


def test_reading_reaches_text_and_entities(ctx, monkeypatch):
    fake(monkeypatch, ModelResult.ok(reading()))
    assert vi.run_perceive_videos(ctx)["read"] == 1
    assert values(ctx, "video.transcript") == [SPEECH]
    rows = ctx.store.observations("v")
    assert _text.body_text(rows) == SPEECH
    assert values(ctx, "page.entity.person")[0]["role"] == "endorser"
    assert values(ctx, "page.entity.organisation")[0]["role"] == "regulator_invoked"
    assert view_text(rows) == f"{SPEECH}\nZarabiaj dziś"


def test_name_without_a_real_quote_is_dropped(ctx, monkeypatch):
    invented = vi.VideoEntity(
        name="Jan Kowalski", entity_type="person", role="endorser", quote="Jan Kowalski poleca"
    )
    fake(monkeypatch, ModelResult.ok(reading(entities=[invented])))
    vi.run_perceive_videos(ctx)
    assert values(ctx, "page.entity.person") == []


@pytest.mark.parametrize("reason", ["no_key", "timeout", "schema", "error"])
def test_failure_is_not_observable(ctx, monkeypatch, reason):
    fake(monkeypatch, ModelResult.not_observable(reason))
    assert vi.run_perceive_videos(ctx)["not_observable"] == 1
    states = {o.state for o in ctx.store.observations("v")}
    assert states == {State.NOT_OBSERVABLE}
    assert _text.body_text(ctx.store.observations("v")) in ("", None)


def test_passing_failure_keeps_the_earlier_reading(ctx, monkeypatch):
    fake(monkeypatch, ModelResult.ok(reading()))
    vi.run_perceive_videos(ctx)
    fake(monkeypatch, ModelResult.not_observable("timeout"))
    assert vi.run_perceive_videos(ctx)["kept"] == 1
    assert values(ctx, "video.transcript") == [SPEECH]


def test_empty_video_is_not_a_successful_empty_result(ctx, monkeypatch):
    silent = reading(transcript="", on_screen_text=[], scenes=[], entities=[])
    fake(monkeypatch, ModelResult.ok(silent))
    assert vi.run_perceive_videos(ctx)["not_observable"] == 1


def test_missing_file_makes_no_model_call(ctx, monkeypatch, tmp_path):
    (tmp_path / "ad.mp4").unlink()
    calls = fake(monkeypatch, ModelResult.ok(reading()))
    assert vi.run_perceive_videos(ctx)["not_observable"] == 1
    assert calls == []


def test_caption_only_video_can_be_matched(ctx, monkeypatch):
    fake(monkeypatch, ModelResult.ok(reading(transcript="", entities=[])))
    vi.run_perceive_videos(ctx)
    assert view_text(ctx.store.observations("v")) == "Zarabiaj dziś"


def test_empty_reading_replaces_the_earlier_one(ctx, monkeypatch):
    fake(monkeypatch, ModelResult.ok(reading()))
    vi.run_perceive_videos(ctx)
    silent = reading(transcript="", on_screen_text=[], scenes=[], entities=[])
    fake(monkeypatch, ModelResult.ok(silent))
    assert vi.run_perceive_videos(ctx)["kept"] == 0
    assert values(ctx, "video.transcript") == [None]


def test_missing_file_keeps_the_earlier_reading(ctx, monkeypatch, tmp_path):
    fake(monkeypatch, ModelResult.ok(reading()))
    vi.run_perceive_videos(ctx)
    (tmp_path / "ad.mp4").unlink()
    assert vi.run_perceive_videos(ctx)["kept"] == 1


def test_unreadable_file_does_not_stop_the_run(ctx, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError

    monkeypatch.setattr(vi, "read_video", denied)
    assert vi.run_perceive_videos(ctx)["not_observable"] == 1


def test_video_text_is_not_a_script_fact_and_stays_out_of_exports():
    from ftf.corpus_report import TEXT_NAMESPACES
    from ftf.perception.techniques import _NOT_SCRIPT_FACTS

    assert vi.NAME in _NOT_SCRIPT_FACTS
    assert {"video.transcript", "video.on_screen_text", "video.scene"} <= TEXT_NAMESPACES
