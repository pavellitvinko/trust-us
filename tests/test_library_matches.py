"""Read-only Library match rules and interaction state."""

from ftf.contracts import (
    Evidence,
    Fingerprint,
    FingerprintKind,
    Incident,
    Observation,
    Relation,
    RelationType,
    Source,
    View,
    ViewKind,
)
from ftf.grouping.levels import Groups
from ftf.library import build_library, build_matches, filter_matches, match_help, playbook_status


def add_views(store, *ids, case="1"):
    store.put_incident(
        Incident(incident_id=case, root="."),
        [],
        [View(view_id=v, incident_id=case, kind=ViewKind.PAGE, label=f"{case} · {v}") for v in ids],
    )


def groups(store):
    ids = [v.view_id for v in store.views()]
    return Groups(
        campaign_of={v: v for v in ids}, campaigns={v: [v] for v in ids}, family_of={}, families={}
    )


def evidence(store, a, b, score, layer="ids.tracking", ns="fb_pixel", value="123456789"):
    store.put_evidence(
        Evidence(
            view_a=a,
            view_b=b,
            layer_id=layer,
            score=score,
            shared=[{"ns": ns, "value": value, "weight": score}],
            comparator="shared-value",
            version="1",
        )
    )


def relation(store, a, b, kind, confidence="medium", **kwargs):
    store.put_relation(Relation(view_a=a, view_b=b, type=kind, confidence=confidence, **kwargs))


def test_count_types_thresholds_self_links_and_tier_filter(store, tmp_path):
    add_views(store, "a", "duplicate", "campaign", "kit", "weak", "clue", "playbook")
    relation(store, "a", "duplicate", RelationType.DUPLICATE)
    relation(store, "a", "campaign", RelationType.SAME_CAMPAIGN)
    relation(store, "a", "kit", RelationType.SAME_FAMILY)
    relation(store, "a", "playbook", RelationType.SAME_PLAYBOOK)  # no supporting layer
    relation(store, "a", "a", RelationType.DUPLICATE)
    evidence(store, "a", "weak", 0.01)
    evidence(store, "a", "clue", 0.8)  # inclusive threshold; not promoted to campaign
    lib = build_library(store, groups(store), tmp_path)
    item = lib.get("a")
    assert [m.verdict for m in item.matches] == [
        "Duplicate",
        "Same campaign",
        "Shared kit",
        "Clue only",
    ]
    assert len(item.matches) == 4
    assert len(lib.get("duplicate").matches) == 1
    assert match_help(item) == "4 items · identity, artifact, kit"
    assert [m.view_id for m in filter_matches(item, ["artifact"])] == ["duplicate"]
    assert filter_matches(item, []) == []
    assert not lib.get("weak").matches


def test_verdict_then_strength_order_and_same_case(store):
    add_views(store, "a", "b", "c", "d")
    add_views(store, "e", case="2")
    relation(store, "a", "b", RelationType.SAME_CAMPAIGN, "low")
    relation(store, "a", "c", RelationType.SAME_CAMPAIGN, "high")
    relation(store, "a", "d", RelationType.DUPLICATE, "low")
    relation(store, "a", "e", RelationType.SAME_CAMPAIGN, "medium")
    found = build_matches(store, groups(store))["a"]
    assert [m.view_id for m in found] == ["d", "c", "e", "b"]
    assert [m.strength for m in found] == ["Weak", "Strong", "Moderate", "Weak"]
    assert [m.same_case for m in found] == [True, True, False, True]


def test_demoted_and_stoplisted_values_do_not_create_clues(store):
    add_views(store, "a", "b", "c")
    for vid, campaign in (("a", "one"), ("b", "two")):
        store.put_fingerprint(
            Fingerprint(
                view_id=vid,
                layer_id="ids.campaign",
                kind=FingerprintKind.SET,
                version="1",
                payload=[{"ns": "aio.campaign_uuid", "value": campaign}],
            )
        )
        store.put_fingerprint(
            Fingerprint(
                view_id=vid,
                layer_id="ids.tracking",
                kind=FingerprintKind.SET,
                version="1",
                payload=[{"ns": "fb_pixel", "value": "123456789"}],
            )
        )
    evidence(store, "a", "b", 0.99)
    evidence(store, "a", "c", 0.99, layer="ids.domain", ns="domain.registrable", value="google.com")
    assert not any(build_matches(store, groups(store)).values())


def test_learned_stoplist_does_not_create_matches(store):
    for n in range(6):
        vid = str(n)
        add_views(store, vid, case=vid)
        store.put_observations(
            vid,
            "test",
            [
                Observation(
                    view_id=vid,
                    signal_id="A",
                    namespace="fb_pixel",
                    value="common",
                    source=Source(file="page.html"),
                    extractor="test",
                    version="1",
                )
            ],
        )
    evidence(store, "0", "1", 0.99, value="common")
    assert not any(build_matches(store, groups(store)).values())


def test_deciding_tiers_coverage_and_purity(store, tmp_path):
    from ftf.compare.weights import weights
    from ftf.fusion.fuse import active_groups, set_active_groups

    add_views(store, "a", "b")
    for vid in ("a", "b"):
        for layer in ("ids.tracking", "text.shingle"):
            store.put_fingerprint(
                Fingerprint(
                    view_id=vid, layer_id=layer, kind=FingerprintKind.SET, payload=[], version="1"
                )
            )
    relation(
        store,
        "a",
        "b",
        RelationType.SAME_CAMPAIGN,
        reasons=["artifact 0.95 [text=1]"],
        tier_scores={"T1": 0.99, "T2": 0.95, "T3": 0.8},
    )
    set_active_groups({"sentinel"})
    before = store.db.serialize(), dict(weights.holders), set(weights.demoted)
    try:
        lib = build_library(store, groups(store), tmp_path)
        match = lib.get("a").matches[0]
        assert match.tiers == ("artifact", "kit")  # rejected identity score is not a tier
        assert match.coverage == "2 of 2 evidence groups observed"  # includes zero-score text
        assert before == (store.db.serialize(), dict(weights.holders), set(weights.demoted))
        assert active_groups() == {"sentinel"}
    finally:
        set_active_groups(None)


def test_playbook_not_run_is_distinct_from_no_match(store):
    add_views(store, "a")
    assert not playbook_status(store)
    store.put_fingerprint(
        Fingerprint(
            view_id="a",
            layer_id="narrative.skeleton",
            kind=FingerprintKind.VECTOR,
            payload={"values": [1.0]},
            version="1",
        )
    )
    assert playbook_status(store)


def test_playbook_relation_uses_only_its_supported_tiers(store):
    add_views(store, "a", "b")
    relation(
        store,
        "a",
        "b",
        RelationType.SAME_PLAYBOOK,
        tier_scores={"T2": 0.99, "T4": 0.8},
        reasons=["playbook 0.80 [narrative=1]"],
    )
    evidence(store, "a", "b", 0.99, layer="text.shingle", ns="jaccard", value="0.99")
    assert not any(build_matches(store, groups(store)).values())
    evidence(store, "a", "b", 0.8, layer="narrative.skeleton", ns="cosine", value="0.8")
    match = build_matches(store, groups(store))["a"][0]
    assert match.verdict == "Clue only" and match.tiers == ("playbook",)


def test_match_filter_compare_close_and_previous_mode(tmp_path, monkeypatch):
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    from ftf.store import Store

    data = tmp_path / "data"
    store = Store(data / "ftf.sqlite")
    add_views(store, "a", "b", "alone")
    relation(store, "a", "b", RelationType.SAME_CAMPAIGN)
    store.close()
    monkeypatch.setenv("FTF_DATA_DIR", str(data))
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app/streamlit_app.py")).run()
    assert not app.exception
    assert app.button(key="matches-alone").disabled
    app.segmented_control(key="mode").set_value("By case").run()
    from ftf.intake import ANALYSIS_LOCK

    with ANALYSIS_LOCK:  # another thread holds analysis; the complete snapshot is still usable
        app.button(key="matches-a").click().run(timeout=2)
    assert not app.exception
    assert app.session_state["library_filter"] == {"matches_of": "a"}
    assert any(c.value == "Playbook: not run" for c in app.caption)
    assert not any(e.label == "What do these tiers mean?" for e in app.expander)
    identity_badge = next(m for m in app.markdown if '>identity</span>' in m.value)
    assert 'class="ftf-tier" title="' in identity_badge.value
    assert "tracking pixel ID" in identity_badge.value and "same campaign" in identity_badge.value
    assert not identity_badge.proto.help
    app.button(key="match-title-b").click().run()
    assert app.session_state["selected"] == "b"
    assert not [t for t in app.toggle if t.label in {"Identity", "Artifact", "Kit", "Playbook"}]
    assert "match-tiers" not in app.session_state
    app.session_state["match-tiers"] = []  # old filter state cannot hide matches
    app.run()
    app.button(key="match-compare-b").click().run()
    assert not app.exception
    assert app.session_state["library_compare"]["objects"]["a"]["view_id"] == "a"
    assert any(e.label == "Layer breakdown" for e in app.expander)
    assert len(app.get("download_button")) == 2
    next(b for b in app.button if b.label == "Close comparison").click().run()
    assert not app.exception
    assert app.session_state["selected"] == "b"
    app.button(key="close-matches").click().run()
    assert not app.exception
    assert "library_filter" not in app.session_state
    assert app.segmented_control(key="mode").value == "By case"
    assert app.session_state["selected"] == "a"
