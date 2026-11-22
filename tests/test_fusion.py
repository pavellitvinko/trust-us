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


# -- HAC-54: a shared domain alone, and confidence against what was actually seen -------------

DOMAIN = [{"ns": "domain.registrable", "value": "glowpixel.org", "weight": 0.9}]


def test_shared_domain_alone_is_not_a_campaign():
    r = fuse("a", "b", [ev("ids.domain", 0.9, DOMAIN), ev("text.shingle", 0.0)], demoted=set())
    assert r.type is RelationType.SAME_FAMILY and r.confidence == "low"
    assert any("shared domain only" in x for x in r.reasons)
    assert r.tier_scores["T1"] == pytest.approx(0.9)  # the score is kept; the rule decides


def test_shared_domain_with_a_second_independent_group_is_a_campaign():
    pixel = ev("ids.tracking", 0.9, [{"ns": "fb_pixel", "value": "P", "weight": 0.9}])
    text = ev("text.shingle", 0.95)
    for second in (pixel, text):
        r = fuse("a", "b", [ev("ids.domain", 0.9, DOMAIN), second], demoted=set())
        assert r.type is RelationType.SAME_CAMPAIGN, second.layer_id
    kit = [
        ev("kit.url", 1.0, [{"ns": "url.param_name", "value": "clickid", "weight": 0.5}]),
        ev("kit.form", 1.0, [{"ns": "form.field", "value": "fbtoken", "weight": 0.3}]),
    ]
    r = fuse("a", "b", [ev("ids.domain", 0.9, DOMAIN), *kit], demoted=set())
    assert r.type is RelationType.SAME_CAMPAIGN


def test_same_landing_url_still_anchors_a_duplicate():
    anchor = [{"ns": "url.host_path", "value": "healthlinkq.pro/L4", "weight": 0.95}]
    r = fuse("a", "b", [ev("ids.domain", 0.95, anchor), ev("text.shingle", 0.99)], demoted=set())
    assert r.type is RelationType.DUPLICATE


def test_solo_identity_groups_are_configurable():
    cfg = FusionConfig(identity_solo_groups=())
    r = fuse("a", "b", [ev("ids.domain", 0.9, DOMAIN)], cfg, demoted=set())
    assert r.type is RelationType.SAME_CAMPAIGN


def test_separation_needs_the_evidence_that_could_have_linked():
    """Missing evidence is unavailable: it never becomes a separation, let alone a confident one."""
    from ftf.fusion.fuse import set_active_groups

    try:
        set_active_groups({"domain", "text", "kit_url"})
        seen = [ev("ids.domain", 0.0), ev("text.shingle", 0.0), ev("kit.url", 0.0)]
        r = fuse("a", "b", seen, demoted=set())
        assert (r.type, r.confidence, r.coverage) == (RelationType.UNRELATED, "high", 1.0)
        assert any("3 of 3 groups comparable" in x for x in r.reasons)

        set_active_groups({"domain", "kit_url", "kit_form", "kit_dom"})
        no_artifact = [ev("ids.domain", 0.0), ev("kit.url", 0.0), ev("kit.form", 0.0)]
        r = fuse("a", "b", no_artifact, demoted=set())
        assert (r.type, r.confidence) == (RelationType.UNRELATED, "low")
        assert r.coverage >= DEFAULT.unrelated_high_coverage
        assert any("not comparable: artifact tier" in x for x in r.reasons)

        # two screenshots, both hashes seen and different: full coverage, still not "unrelated"
        set_active_groups({"asset", "creative"})
        r = fuse("a", "b", [ev("asset.exact", 0.0), ev("ad.creative", 0.0)], demoted=set())
        assert (r.type, r.confidence, r.coverage) == (RelationType.INSUFFICIENT, "", 1.0)
        assert any("identity tier not comparable" in x for x in r.reasons)
    finally:
        set_active_groups(None)


def test_weak_similarity_below_threshold_is_reported_not_used():
    from ftf.fusion.fuse import set_active_groups

    try:
        set_active_groups({"domain", "text", "kit_url"})
        weak = ev("kit.url", 0.33, [{"ns": "url.path_shape", "value": "/lander/", "weight": 0.33}])
        r = fuse("a", "b", [ev("ids.domain", 0.0), ev("text.shingle", 0.0), weak], demoted=set())
        assert r.type is RelationType.UNRELATED
        assert any(x.startswith("below threshold, not used: kit") for x in r.reasons)
    finally:
        set_active_groups(None)


def test_host_equal_to_its_registrable_domain_is_one_fact():
    from ftf.contracts import Observation, View, ViewKind
    from ftf.registry import layers, load_plugins

    load_plugins()
    view = View(view_id="v", incident_id="i", kind=ViewKind.PAGE, label="v")

    def obs(ns, value):
        return Observation(
            view_id="v", signal_id="A14", namespace=ns, value=value, extractor="t", version="t"
        )

    build = layers.get("ids.domain").build
    same = build(
        view, [obs("domain.registrable", "glowpixel.org"), obs("url.host", "glowpixel.org")]
    )
    assert same.payload == [{"ns": "domain.registrable", "value": "glowpixel.org"}]
    www = build(view, [obs("domain.registrable", "x.org"), obs("url.host", "www.x.org")])
    assert www.payload == [{"ns": "domain.registrable", "value": "x.org"}]
    sub = build(view, [obs("domain.registrable", "x.org"), obs("url.host", "shop.x.org")])
    assert {"ns": "url.host", "value": "shop.x.org"} in sub.payload  # a subdomain is its own fact


# -- HAC-47: the report's confidence basis is the decision table's own account ---------------

PATHS_BY_RELATION = {
    RelationType.DUPLICATE: {"duplicate"},
    RelationType.SAME_CAMPAIGN: {"identity", "artifact"},
    RelationType.SAME_FAMILY: {"kit", "identity_solo", "contradiction_cap"},
    RelationType.SAME_PLAYBOOK: {"playbook"},
    RelationType.UNRELATED: {"unrelated"},
    RelationType.INSUFFICIENT: {"insufficient"},
}


def _explain(evidence, config=DEFAULT):
    from ftf.fusion import explain, summarise

    return explain(summarise(evidence, config, demoted=set()), config)


def test_explain_and_decide_are_one_table(fused_store):
    from ftf.fusion import decide, explain

    for r in fused_store.relations():
        f = pipeline_summary(fused_store, r)
        rtype, confidence, reasons, basis = explain(f)
        assert (rtype, confidence, reasons) == decide(f)
        assert (rtype, confidence) == (r.type, r.confidence)
        assert basis["path"] in PATHS_BY_RELATION[rtype], (r.view_a, r.view_b, basis)
        assert basis["rule"] and "label_rule" in basis


def pipeline_summary(store, r):
    from ftf.fusion import summarise

    return summarise(pipeline.compare_views(store, r.view_a, r.view_b))


def test_basis_names_the_path_taken_not_the_strongest_tier():
    pixel = ev("ids.tracking", 0.9, [{"ns": "fb_pixel", "value": "P", "weight": 0.9}])
    # a shared domain alone: family by the solo rule, and the basis says so
    rtype, conf, _, basis = _explain([ev("ids.domain", 0.9, DOMAIN), ev("text.shingle", 0.0)])
    assert (rtype, conf, basis["path"]) == (RelationType.SAME_FAMILY, "low", "identity_solo")
    assert basis["tier"] == "T1" and basis["agreeing_groups"] == ["domain"]
    assert basis["kit_score"] < basis["kit_threshold"] and basis["label_rule"] == "fixed low"
    # the same domain with a second group: a campaign through the identity path
    rtype, _, _, basis = _explain([ev("ids.domain", 0.9, DOMAIN), pixel])
    assert (rtype, basis["path"]) == (RelationType.SAME_CAMPAIGN, "identity")
    assert set(basis["agreeing_groups"]) == {"domain", "tracking"}
    assert basis["label_rule"]["high"].startswith("score >= 0.9")
    # identity contradicted while tracking is strong: capped, and the basis names the cap
    contradiction = ev(
        "ids.campaign", 0.0, differing=[{"ns": "aio.campaign_uuid", "a": ["X"], "b": ["Y"]}]
    )
    rtype, conf, _, basis = _explain([contradiction, pixel])
    assert (rtype, conf, basis["path"]) == (RelationType.SAME_FAMILY, "medium", "contradiction_cap")
    assert basis["contradictions"] == ["aio.campaign_uuid"] and basis["tier"] == "T1"
    # kit evidence: the kit path, with the graded label rule at the kit threshold
    kit = [
        ev("kit.url", 1.0, [{"ns": "url.param_name", "value": "clickid", "weight": 0.5}]),
        ev("kit.form", 1.0, [{"ns": "form.field", "value": "fbtoken", "weight": 0.3}]),
        ev("kit.dom", 0.9),
    ]
    rtype, _, _, basis = _explain(kit)
    assert (rtype, basis["path"]) == (RelationType.SAME_FAMILY, "kit")
    assert (
        basis["threshold"] == DEFAULT.kit
        and f"score >= {DEFAULT.kit}" in basis["label_rule"]["medium"]
    )


def test_basis_separates_the_two_insufficient_causes_and_the_unrelated_label():
    from ftf.fusion.fuse import set_active_groups

    try:
        set_active_groups({"asset", "creative"})
        rtype, _, _, basis = _explain([ev("asset.exact", 0.0), ev("ad.creative", 0.0)])
        assert (rtype, basis["path"], basis["cause"]) == (
            RelationType.INSUFFICIENT,
            "insufficient",
            "identity_not_comparable",
        )
        assert basis["identity_seen"] is False and basis["coverage"] == 1.0

        set_active_groups({"domain", "text", "kit_url", "kit_form", "kit_dom", "asset"})
        rtype, _, _, basis = _explain([ev("ids.domain", 0.0)])
        assert (rtype, basis["cause"]) == (RelationType.INSUFFICIENT, "low_coverage")
        assert basis["coverage"] < basis["min_coverage"]

        set_active_groups({"domain", "kit_url", "kit_form", "kit_dom"})
        rtype, conf, _, basis = _explain(
            [ev("ids.domain", 0.0), ev("kit.url", 0.0), ev("kit.form", 0.0)]
        )
        assert (rtype, conf, basis["path"]) == (RelationType.UNRELATED, "low", "unrelated")
        assert basis["artifact_seen"] is False
        assert str(DEFAULT.unrelated_high_coverage) in basis["label_rule"]["high"]
    finally:
        set_active_groups(None)
