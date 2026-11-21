"""Typed fusion with the contradiction and demotion rules, on the fixtures (HAC-26)."""

import pytest

from ftf import pipeline
from ftf.compare.weights import weights
from ftf.contracts import Evidence, RelationType
from ftf.eval.fixtures import store_from_fixtures
from ftf.fusion import DEFAULT, FusionConfig, fuse, run_fuse
from ftf.fusion.demotion import find_demotions


@pytest.fixture(scope="module")
def fused_store():
    s = store_from_fixtures()
    pipeline.run_fingerprint(s)
    run_fuse(s)
    yield s
    weights.reset()
    s.close()


def relation(store, a, b):
    a, b = sorted((a, b))
    return next(r for r in store.relations() if (r.view_a, r.view_b) == (a, b))


def test_same_campaign_across_different_domains(fused_store):
    r = relation(fused_store, "1:page", "2:page")
    assert r.type is RelationType.SAME_CAMPAIGN and r.confidence == "high"
    assert r.tier_scores["T1"] >= 0.99
    assert not any(x.startswith("contradiction") for x in r.reasons)


def test_duplicate_capture_of_one_landing(fused_store):
    r = relation(fused_store, "2:healthlinkq", "3:page")
    assert r.type is RelationType.DUPLICATE
    assert any("url.host_path=healthlinkq.pro" in x for x in r.reasons)


def test_shared_template_with_contradicting_campaigns_is_family_not_campaign(fused_store):
    r = relation(fused_store, "48:page", "61:article")
    assert r.type is RelationType.SAME_FAMILY
    assert any(x.startswith("contradiction: aio.campaign_uuid") for x in r.reasons)
    assert r.tier_scores["T1"] == 0.0  # everything shared at identity level was demoted


def test_demotion_reclassifies_shared_integration_values(fused_store):
    assert {
        ("api_key_id", "APPA-165"),
        ("form_endpoint_host", "shadowtracecore.com"),
    } <= weights.demoted
    demoted, pairs = find_demotions(fused_store)
    assert ("aio.campaign_uuid", "ad8555cd-ad11-493f-8c31-c8f0d4799975") not in demoted
    assert ("48:page", "61:article", "aio.campaign_uuid") in pairs


def test_article_and_chat_of_one_funnel_are_one_campaign(fused_store):
    r = relation(fused_store, "61:article", "61:chat")
    assert r.type is RelationType.SAME_CAMPAIGN
    assert (
        r.tier_scores["T1"] >= 0.95
    )  # campaign UUID, domain and Ads ID still agree after demotion


def test_demoted_values_alone_cannot_merge_campaigns(fused_store):
    # 61 chat and 48 share the demoted key and host but declare different campaigns.
    r = relation(fused_store, "61:chat", "48:page")
    assert r.type is RelationType.SAME_FAMILY


def test_unrelated_and_insufficient(fused_store):
    assert relation(fused_store, "10:page", "14:page").type is RelationType.UNRELATED
    r = relation(fused_store, "5:video", "1:page")
    assert r.type is RelationType.INSUFFICIENT and r.coverage == 0.0


def test_kit_family_of_the_aio_runtime(fused_store):
    r = relation(fused_store, "1:page", "48:page")
    assert r.type is RelationType.SAME_FAMILY
    assert r.tier_scores["T3"] >= 0.7


def test_unproven_kit_hypotheses_do_not_link(fused_store):
    assert relation(fused_store, "3:page", "14:page").type is RelationType.UNRELATED
    assert relation(fused_store, "36:page", "47:page").type is RelationType.UNRELATED
    assert relation(fused_store, "14:page", "47:page").type is not RelationType.SAME_CAMPAIGN


def test_landing_variants_in_one_folder_are_one_campaign(fused_store):
    assert relation(fused_store, "14:page", "14:capture").type is RelationType.SAME_CAMPAIGN


def test_co_captured_views_are_not_linked_by_the_folder(fused_store):
    # the Brzoska page and the healthlinkq capture share folder 2 and a narrative, not a campaign
    r = relation(fused_store, "2:page", "2:healthlinkq")
    assert r.type in (RelationType.SAME_PLAYBOOK, RelationType.UNRELATED)
    assert r.tier_scores["T1"] == 0.0 and r.tier_scores["T2"] == 0.0


# -- unit behaviour of fuse() ---------------------------------------------------------------


def ev(layer, score, shared=(), differing=(), comparable=True):
    return Evidence(
        view_a="a",
        view_b="b",
        layer_id=layer,
        comparable=comparable,
        score=score,
        shared=list(shared),
        differing=list(differing),
        comparator="t",
        version="t",
    )


def test_missing_layer_lowers_coverage_never_the_score():
    full = fuse(
        "a",
        "b",
        [ev("ids.campaign", 1.0, [{"ns": "aio.campaign_uuid", "value": "X", "weight": 0.95}])],
        demoted=set(),
    )
    with_missing = fuse(
        "a",
        "b",
        [
            ev("ids.campaign", 1.0, [{"ns": "aio.campaign_uuid", "value": "X", "weight": 0.95}]),
            ev("asset.exact", 0.0, comparable=False),
            ev("kit.dom", 0.0, comparable=False),
        ],
        demoted=set(),
    )
    assert full.tier_scores == with_missing.tier_scores
    assert full.type is with_missing.type is RelationType.SAME_CAMPAIGN


def test_playbook_evidence_never_merges_campaigns():
    """A perfect playbook score reaches same_playbook at most, whatever the config."""
    from ftf.contracts import FingerprintKind, Tier
    from ftf.registry import layers

    class FakePlaybook:
        name, version, kind, tier, group, comparator = (
            "test.playbook",
            "t",
            FingerprintKind.SET,
            Tier.PLAYBOOK,
            "test_pb",
            "set_overlap",
        )

        def build(self, view, observations):
            return None

    layers.register(FakePlaybook)
    try:
        r = fuse("a", "b", [ev("test.playbook", 1.0)], demoted=set())
        assert r.type is RelationType.SAME_PLAYBOOK
        assert r.tier_scores["T1"] == r.tier_scores["T2"] == r.tier_scores["T3"] == 0.0
    finally:
        layers._items.pop("test.playbook")


def test_one_kit_group_alone_is_below_the_family_threshold():
    r = fuse(
        "a",
        "b",
        [ev("kit.url", 1.0, [{"ns": "url.param_name", "value": "clickid", "weight": 0.5}])],
        demoted=set(),
    )
    assert r.tier_scores["T3"] == pytest.approx(0.6)
    assert r.type is not RelationType.SAME_FAMILY


def test_contradiction_caps_strong_identity_at_family():
    e = [
        ev("ids.campaign", 0.0, differing=[{"ns": "aio.campaign_uuid", "a": ["X"], "b": ["Y"]}]),
        ev("ids.tracking", 0.9, [{"ns": "fb_pixel", "value": "P", "weight": 0.9}]),
    ]
    r = fuse("a", "b", e, demoted=set())
    assert r.type is RelationType.SAME_FAMILY
    assert any("capped at family" in x for x in r.reasons)


def test_config_roundtrip(tmp_path):
    cfg = FusionConfig(kit=0.75)
    cfg.save(tmp_path / "f.json")
    loaded = FusionConfig.load(tmp_path / "f.json")
    assert loaded == cfg and loaded != DEFAULT
