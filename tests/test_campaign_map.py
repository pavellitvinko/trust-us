"""Campaign map, method settings and analyst library edits."""

import pytest

from ftf.campaign_map import link_tiers, map_data, to_dot
from ftf.contracts import Incident, Relation, RelationType, View, ViewKind
from ftf.fusion import DEFAULT
from ftf.method_settings import apply_saved, current_values, reset, save, settings_path
from ftf.patterns.analyst import LibraryEditError, merge_patterns, rename_pattern, retire_pattern
from ftf.patterns.library import LibraryVersion, PatternDefinition, PatternLibraryStore


@pytest.fixture
def linked(store):
    views = [
        View(view_id=v, incident_id="i", kind=ViewKind.PAGE, label=f'label "{v}"')
        for v in ("a", "b")
    ]
    store.put_incident(Incident(incident_id="i", root="."), [], views)
    store.put_relation(
        Relation(
            view_a="a",
            view_b="b",
            type=RelationType.SAME_PLAYBOOK,
            confidence="medium",
            tier_scores={"T1": 0.0, "T4": 0.9},
        )
    )
    return store


def test_tier_toggle_controls_links_and_dot_is_quoted(linked):
    assert map_data(linked, ["identity"])["edges"] == []
    data = map_data(linked, ["playbook"])
    assert [e["tiers"] for e in data["edges"]] == [["playbook"]]
    assert 'label \\"a\\"' in to_dot(data)


@pytest.fixture
def method(tmp_path, monkeypatch):
    monkeypatch.setenv("FTF_DATA_DIR", str(tmp_path / "data"))
    yield
    reset()


def test_saved_settings_apply_and_reset(method):
    values = current_values()
    values["thresholds"]["kit"] = 0.55
    assert save(values) == []
    assert DEFAULT.kit == 0.55
    DEFAULT.kit = 0.1
    apply_saved()
    assert DEFAULT.kit == 0.55
    reset()
    assert DEFAULT.kit == 0.7


@pytest.mark.parametrize("bad", [1.5, -0.1, True])
def test_invalid_settings_are_refused(method, bad):
    values = current_values()
    values["thresholds"]["kit"] = bad
    assert save(values)
    assert DEFAULT.kit == 0.7


def test_broken_file_falls_back_to_shipped_settings(method):
    settings_path().parent.mkdir(parents=True)
    settings_path().write_text("{broken")
    apply_saved()
    assert DEFAULT.kit == 0.7


@pytest.fixture
def library(tmp_path):
    store = PatternLibraryStore(tmp_path / "library")
    store.save_version(
        LibraryVersion(
            version_id="v0001-abc",
            created_at="now",
            patterns=[
                PatternDefinition(
                    pattern_id=p,
                    name=p,
                    description="d",
                    positive_examples=[{"card_id": p}],
                )
                for p in ("p1", "p2")
            ],
        )
    )
    return store


def test_analyst_edits_create_versions_and_keep_history(library):
    renamed = rename_pattern(library, "p1", "Countdown")
    assert renamed.patterns[0].metadata.analyst_renamed_from == "p1"
    retired = retire_pattern(library, "p2")
    assert retired.patterns[1].metadata.analyst_retired_at
    merged = merge_patterns(library, "p1", "p2")
    assert [p.pattern_id for p in merged.patterns] == ["p1"]
    assert merged.patterns[0].metadata.merged_from == ["p2"]
    assert len(merged.patterns[0].positive_examples) == 2
    assert len(library.list_versions()) == 4
    assert library.latest_version().version_id == merged.version_id


def test_bad_edits_are_refused(library):
    with pytest.raises(LibraryEditError):
        merge_patterns(library, "p1", "p1")
    with pytest.raises(LibraryEditError):
        rename_pattern(library, "missing", "x")
    assert len(library.list_versions()) == 1


@pytest.mark.parametrize("content", ["[]", "null", "1"])
def test_non_object_settings_file_is_ignored(method, content):
    settings_path().parent.mkdir(parents=True)
    settings_path().write_text(content)
    apply_saved()
    assert DEFAULT.kit == 0.7


def relation(kind, scores, reasons=()):
    return Relation(
        view_a="a",
        view_b="b",
        type=kind,
        confidence="medium",
        tier_scores=scores,
        reasons=list(reasons),
    )


def test_duplicate_stays_on_the_map_below_tier_thresholds():
    assert link_tiers(relation(RelationType.DUPLICATE, {"T2": 0.63}), DEFAULT) == ["artifact"]


def test_rejected_artifact_score_is_not_a_link_tier():
    found = relation(RelationType.SAME_PLAYBOOK, {"T2": 0.9, "T4": 0.8}, ["playbook 0.80 [x=1]"])
    assert link_tiers(found, DEFAULT) == ["playbook"]


def test_cannot_merge_into_a_retired_pattern(library):
    retire_pattern(library, "p1")
    with pytest.raises(LibraryEditError, match="retired"):
        merge_patterns(library, "p1", "p2")


def test_analyst_merge_survives_the_next_discovery(library, monkeypatch, tmp_path):
    from ftf.config import Settings
    from ftf.models import ModelResult
    from ftf.patterns import induction as ind
    from ftf.patterns.library import ClusterResult, TechniqueCardForClustering

    version = library.latest_version()
    version.patterns[0].centroid = [1.0, 0.0]
    version.patterns[1].centroid = [0.0, 1.0]
    version.version_id = "v0002-abc"
    library.save_version(version)
    merge_patterns(library, "p1", "p2")

    def card(cid, campaign):
        return TechniqueCardForClustering(
            card_name=cid,
            card_description=cid,
            view_id=cid,
            incident_id=campaign,
            source_text="",
            card_id=cid,
            campaign_id=campaign,
        )

    cluster = ClusterResult(0, [card("x", "c1"), card("y", "c2")], [0.0, 1.0])
    answer = ind.InducedPattern(
        coherent=True,
        name="New name",
        description="d",
        positive_card_ids=["x", "y"],
        near_misses=[],
    )
    monkeypatch.setattr("ftf.models.read_structured", lambda *a, **k: ModelResult.ok(answer))
    settings = Settings(tmp_path, "", "", "m", "e", "v")
    result = ind.run_induction(library, [cluster], settings=settings)
    assert [p.pattern_id for p in result.version.patterns] == ["p1"]
    assert {e["card_id"] for e in result.version.patterns[0].positive_examples} >= {"x", "y"}
