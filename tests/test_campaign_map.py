"""Campaign map, method settings and analyst library edits."""

import pytest

from ftf.campaign_map import map_data, to_dot
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
