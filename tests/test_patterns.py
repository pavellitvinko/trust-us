"""Pattern stage tests. A fake model and fake vectors: nothing here touches the network."""

import json
from dataclasses import dataclass

import pytest

from ftf.contracts import Incident, Observation, Source, State, View, ViewKind
from ftf.layers.playbook import PatternMo
from ftf.models import ModelResult
from ftf.patterns import induction as ind
from ftf.patterns import matching as mat
from ftf.patterns.clustering import cluster_cards, gather_technique_cards
from ftf.patterns.library import (
    ClusterResult,
    LibraryVersion,
    PatternDefinition,
    PatternLibraryStore,
    PatternMetadata,
    TechniqueCardForClustering,
)
from ftf.patterns.novelty import NoveltyStore
from ftf.registry import Context, load_plugins

TEXT = "Znany inwestor poleca platformę. Oferta kończy się za 10 minut."


def card(cid, campaign, view=None, name="Celebrity endorsement"):
    return TechniqueCardForClustering(
        card_name=name,
        card_description=f"{name} used to build trust",
        view_id=view or f"view-{cid}",
        incident_id=f"incident-{campaign}",
        source_text="",
        card_id=cid,
        campaign_id=campaign,
    )


def cluster(index, cards, centroid=(1.0, 0.0)):
    return ClusterResult(cluster_id=index, cards=cards, centroid=list(centroid))


def draft(ids, name="Celebrity endorsement", coherent=True):
    return ind.InducedPattern(
        coherent=coherent,
        name=name,
        description="A public figure is shown backing the offer.",
        positive_card_ids=ids,
        near_misses=["An anonymous testimonial"],
    )


@pytest.fixture
def library(tmp_path):
    return PatternLibraryStore(tmp_path / "library")


def fake_reader(monkeypatch, answer):
    calls = []

    def read(task, version, system, content, schema, **kwargs):
        calls.append(task)
        value = answer(task, content) if callable(answer) else answer
        return value if isinstance(value, ModelResult) else ModelResult.ok(value)

    monkeypatch.setattr("ftf.models.read_structured", read)
    return calls


def test_plugins_load_and_pattern_mo_is_the_only_pattern_layer():
    from ftf.registry import layers

    load_plugins()
    assert layers.get("pattern.mo").__class__ is PatternMo


def test_clustering_ignores_input_order():
    cards = [card("a", "c1"), card("b", "c2"), card("c", "c3", name="Countdown")]
    vectors = [[1.0, 0.0], [0.99, 0.05], [0.0, 1.0]]
    first = cluster_cards(cards, vectors)
    second = cluster_cards(cards[::-1], vectors[::-1])
    shape = [[c.card_id for c in x.cards] for x in first]
    assert shape == [[c.card_id for c in x.cards] for x in second]
    assert shape == [["a", "b"], ["c"]]


def test_gather_reads_the_real_card_namespace(store):
    view = View(view_id="v1", incident_id="i", kind=ViewKind.PAGE, label="p")
    store.put_incident(Incident(incident_id="i", root="."), [], [view])
    rows = [
        Observation(
            view_id="v1",
            signal_id="H",
            namespace="technique.card",
            value={"name": "Countdown", "description": "Timer", "evidence": [{"quote": "x"}]},
            extractor="perception.technique",
            version="1",
        ),
        Observation(
            view_id="v1",
            signal_id="H",
            namespace="technique.card",
            raw="refusal",
            state=State.NOT_OBSERVABLE,
            extractor="perception.technique",
            version="1",
        ),
    ]
    store.put_observations("v1", "perception.technique", rows)
    cards = gather_technique_cards(store, campaign_of={"v1": "c1"})
    assert [(c.card_name, c.campaign_id) for c in cards] == [("Countdown", "c1")]


def test_one_campaign_across_two_folders_is_only_a_candidate(library, settings, monkeypatch):
    calls = fake_reader(monkeypatch, draft(["a", "b"]))
    same = cluster(0, [card("a", "campaign-1"), card("b", "campaign-1")])
    result = ind.run_induction(library, [same], settings=settings)
    assert calls == []
    assert result.version is None
    assert result.reason == "no_patterns"
    assert result.candidates == [same]


def test_two_independent_campaigns_enter_the_library(library, settings, monkeypatch):
    fake_reader(monkeypatch, draft(["a", "b"]))
    found = cluster(0, [card("a", "c1"), card("b", "c2")])
    result = ind.run_induction(library, [found], settings=settings)
    assert [p.name for p in result.version.patterns] == ["Celebrity endorsement"]
    assert len(result.version.patterns[0].positive_examples) == 2


@pytest.mark.parametrize("ids", [["invented", "other"], ["a"]])
def test_uncited_or_one_campaign_evidence_is_rejected(library, settings, monkeypatch, ids):
    fake_reader(monkeypatch, draft(ids))
    found = cluster(0, [card("a", "c1"), card("b", "c2")])
    result = ind.run_induction(library, [found], settings=settings)
    assert result.version is None
    assert result.candidates == [found]


@pytest.mark.parametrize("reason", ["no_key", "timeout", "schema", "error"])
def test_model_failure_never_writes_a_partial_library(library, settings, monkeypatch, reason):
    fake_reader(monkeypatch, ModelResult.not_observable(reason))
    found = cluster(0, [card("a", "c1"), card("b", "c2")])
    result = ind.run_induction(library, [found], settings=settings)
    assert (result.version, result.reason) == (None, reason)
    assert library.latest_version() is None


def test_ids_stay_stable_and_splits_are_recorded(library, settings, monkeypatch):
    fake_reader(monkeypatch, draft(["a", "b"]))
    first = ind.run_induction(
        library, [cluster(0, [card("a", "c1"), card("b", "c2")])], settings=settings
    ).version
    library.save_version(first)
    original = first.patterns[0].pattern_id

    def answer(task, content):
        ids = [c["id"] for c in json.loads(content)["cards"]]
        return draft(ids, name=f"Variant {ids[0]}")

    fake_reader(monkeypatch, answer)
    big = cluster(0, [card("a", "c1"), card("b", "c2"), card("d", "c3")], (1.0, 0.0))
    small = cluster(1, [card("e", "c1"), card("f", "c2")], (0.99, 0.05))
    second = ind.run_induction(library, [big, small], settings=settings).version
    kept = next(p for p in second.patterns if p.pattern_id == original)
    assert len(second.patterns) == 2
    assert kept.metadata.created_in_version == first.version_id
    assert len(kept.metadata.split_into) == 1


def test_same_input_does_not_create_a_new_version(library, settings, monkeypatch):
    calls = fake_reader(monkeypatch, draft(["a", "b"]))
    clusters = [cluster(0, [card("a", "c1"), card("b", "c2")])]
    library.save_version(ind.run_induction(library, clusters, settings=settings).version)
    count = len(calls)
    assert ind.run_induction(library, clusters, settings=settings).unchanged
    assert len(calls) == count


def test_novelty_needs_two_campaigns_and_logs_promotion(tmp_path):
    novelty = NoveltyStore(tmp_path / "novelty")
    novelty.sync([cluster(0, [card("a", "c1"), card("b", "c1")])], set())
    assert [c.promotion_ready for c in novelty.candidates().values()] == [False]
    assert novelty.sync([], {"a"}) == {"candidates": 0, "promoted": 1}


@dataclass
class Embedded:
    vector: list


def pattern(pid="pattern.1_x", centroid=(1.0, 0.0)):
    return PatternDefinition(
        pattern_id=pid,
        name="Countdown",
        description="Time pressure",
        centroid=list(centroid),
        metadata=PatternMetadata(created_in_version="v1", last_seen_in_version="v1"),
    )


VERSION = LibraryVersion(version_id="v0001", created_at="now", patterns=[pattern()])


@pytest.fixture
def ctx(store):
    view = View(view_id="v1", incident_id="i", kind=ViewKind.PAGE, label="p", primary="p.html")
    store.put_incident(Incident(incident_id="i", root="."), [], [view])
    store.put_observations(
        "v1",
        "page.text",
        [
            Observation(
                view_id="v1",
                signal_id="F1",
                namespace="page.clean_text",
                value=TEXT,
                source=Source(file="p.html"),
                extractor="page.text",
                version="1",
            )
        ],
    )
    return Context(store)


def judged(state, quote=None, confidence=0.9):
    return mat.PatternJudgements(
        judgements=[
            mat.PatternJudgement(
                pattern_id="pattern.1_x", state=state, confidence=confidence, quote=quote
            )
        ]
    )


def fake_embed(monkeypatch, failure=None):
    def embed(texts, task, **kwargs):
        if failure:
            return [ModelResult.not_observable(failure) for _ in texts]
        return [ModelResult.ok(Embedded([1.0, 0.0])) for _ in texts]

    monkeypatch.setattr("ftf.models.embed", embed)


def scores(ctx):
    return [o for o in ctx.store.observations("v1") if o.namespace == "pattern.score"]


def test_verified_match_reaches_pattern_mo(ctx, monkeypatch):
    fake_embed(monkeypatch)
    fake_reader(monkeypatch, judged("present", "kończy się za 10 minut"))
    mat.run_matching(ctx, VERSION)
    load_plugins()
    fingerprint = PatternMo().build(ctx.store.get_view("v1"), ctx.store.observations("v1"))
    assert fingerprint.payload == [{"ns": "pattern", "value": "pattern.1_x", "score": 0.9}]


@pytest.mark.parametrize(
    "answer",
    [
        judged("present", "cytat, którego nie ma"),
        judged("uncertain"),
        mat.PatternJudgements(judgements=[]),
    ],
)
def test_unverified_judgement_is_not_observable(ctx, monkeypatch, answer):
    fake_embed(monkeypatch)
    fake_reader(monkeypatch, answer)
    mat.run_matching(ctx, VERSION)
    assert [o.state for o in scores(ctx)] == [State.NOT_OBSERVABLE]
    assert PatternMo().build(ctx.store.get_view("v1"), ctx.store.observations("v1")) is None


@pytest.mark.parametrize("step", ["embed", "read"])
@pytest.mark.parametrize("reason", ["no_key", "refusal", "timeout"])
def test_model_failure_never_becomes_absent(ctx, monkeypatch, step, reason):
    fake_embed(monkeypatch, reason if step == "embed" else None)
    fake_reader(
        monkeypatch,
        ModelResult.not_observable(reason) if step == "read" else judged("absent"),
    )
    summary = mat.run_matching(ctx, VERSION)
    assert summary["matched"] == 0
    assert State.ABSENT not in {o.state for o in scores(ctx)}


def test_passing_failure_keeps_the_earlier_vector(ctx, monkeypatch):
    fake_embed(monkeypatch)
    fake_reader(monkeypatch, judged("present", "kończy się za 10 minut"))
    mat.run_matching(ctx, VERSION)
    fake_reader(monkeypatch, ModelResult.not_observable("timeout"))
    assert mat.run_matching(ctx, VERSION)["kept"] == 1
    assert [o.state for o in scores(ctx)] == [State.PRESENT]


def test_view_without_text_is_not_observable(store, monkeypatch):
    view = View(view_id="v1", incident_id="i", kind=ViewKind.IMAGE, label="p")
    store.put_incident(Incident(incident_id="i", root="."), [], [view])
    fake_embed(monkeypatch)
    fake_reader(monkeypatch, judged("absent"))
    summary = mat.run_matching(Context(store), VERSION)
    assert summary["not_observable"] == 1


def test_pattern_evidence_alone_never_merges_a_campaign():
    assert PatternMo.tier.name == "PLAYBOOK"


def test_matching_reports_each_items_started_and_retained_outcome(ctx, monkeypatch):
    fake_embed(monkeypatch)
    fake_reader(monkeypatch, judged("present", "kończy się za 10 minut"))
    events = []
    mat.run_matching(ctx, VERSION, on_view=lambda vid, result: events.append((vid, result)))
    assert events == [("v1", None), ("v1", {"not_observable": 0, "matched": 1})]
    fake_reader(monkeypatch, ModelResult.not_observable("timeout"))
    events.clear()
    mat.run_matching(ctx, VERSION, on_view=lambda vid, result: events.append((vid, result)))
    assert events == [("v1", None), ("v1", {"kept": 1})]
