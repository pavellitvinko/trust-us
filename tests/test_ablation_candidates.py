"""Candidate generation, layer ablation and degradation runs (HAC-36, HAC-35)."""

from itertools import combinations

import pytest

from ftf import pipeline
from ftf.compare.weights import weights
from ftf.contracts import RelationType
from ftf.eval.ablation import (
    HEADLINE,
    disabled_layers,
    format_runs,
    model_off_namespaces,
    reencode_images,
    run_once,
    screenshot_only,
    swap_domains,
)
from ftf.eval.fixtures import store_from_fixtures
from ftf.fusion import run_method
from ftf.grouping.candidates import candidate_pairs, cheap_relation
from ftf.registry import layers


def _run(candidates: bool):
    s = store_from_fixtures()
    pipeline.run_fingerprint(s)
    g = run_method(s, candidates=candidates)
    rel = {(r.view_a, r.view_b): (r.type, r.coverage) for r in s.relations()}
    n_cand = len(candidate_pairs(s))
    n_all = len(list(combinations([v.view_id for v in s.views()], 2)))
    weights.reset()
    s.close()
    return rel, g, n_cand, n_all


def test_candidate_generation_matches_all_pairs_exactly():
    all_pairs, g_all, n_cand, n_all = _run(False)
    cand, g_cand, _, _ = _run(True)
    assert n_cand < n_all * 0.6  # fewer than 60% of pairs are compared in full
    assert set(all_pairs) == set(cand)  # a complete relation table either way
    assert {k: v[0] for k, v in all_pairs.items()} == {k: v[0] for k, v in cand.items()}
    assert all(abs(all_pairs[k][1] - cand[k][1]) < 1e-6 for k in all_pairs)
    assert (g_all.campaigns, g_all.families, g_all.playbooks) == (
        g_cand.campaigns,
        g_cand.families,
        g_cand.playbooks,
    )


def test_cheap_relation_for_non_candidates_uses_coverage_only():
    s = store_from_fixtures()
    pipeline.run_fingerprint(s)
    r = cheap_relation(s, "5:video", "10:page")
    assert r.type is RelationType.INSUFFICIENT and r.coverage == 0.0
    r2 = cheap_relation(s, "10:page", "36:page")
    assert r2.type is RelationType.UNRELATED and r2.coverage >= 0.3
    s.close()


def test_skipped_pairs_carry_the_fusion_reasons_and_a_marker():
    """HAC-36: a pair that was not compared reads like the all-pairs result, plus one line."""
    from ftf.fusion import fuse_pair, prepare
    from ftf.grouping.candidates import NOT_COMPARED, candidate_pairs

    s = store_from_fixtures()
    pipeline.run_fingerprint(s)
    prepare(s)
    proposed = candidate_pairs(s)
    views = sorted(v.view_id for v in s.views())
    skipped = [
        (a, b) for i, a in enumerate(views) for b in views[i + 1 :] if (a, b) not in proposed
    ]
    assert skipped
    for a, b in skipped:
        cheap = cheap_relation(s, a, b)
        full = fuse_pair(s, a, b, persist=False)
        assert (cheap.type, cheap.confidence, cheap.coverage) == (
            full.type,
            full.confidence,
            full.coverage,
        ), (a, b)
        assert cheap.reasons == [*full.reasons, NOT_COMPARED], (a, b)
        assert "groups comparable" in cheap.reasons[0]
    weights.reset()
    s.close()


def test_disabled_layers_restores_the_registry():
    before = layers.names()
    with disabled_layers(["ids.campaign", "kit.dom"]):
        assert "ids.campaign" not in layers.names()
    assert layers.names() == before


@pytest.fixture(scope="module")
def runs():
    out = {
        "base": run_once("base"),
        "no_campaign": run_once("no campaign ids", off=["ids.campaign"]),
        "no_domain": run_once("no domain", off=["ids.domain"]),
        "no_har": run_once(
            "no har",
            exclude_namespaces={
                "tracker.host",
                "form_endpoint_host",
                "form_endpoint_path",
                "http.server",
                "api.route_shape",
                "redirect.domain",
                "outbound.domain",
                "gads_id",
                "dns.ns_pair",
            },
        ),
        "screens": run_once("screens", transform=screenshot_only),
        "reencoded": run_once("reencoded", transform=reencode_images),
        "swapped": run_once("swapped", transform=swap_domains),
        "model_off": run_once(
            "model off",
            transform=model_off_namespaces,
            off=[
                "text.semantic",
                "narrative.skeleton",
                "pattern.mo",
                "kit.absence",
                "entity.cast",
                "entity.roles",
                "visual.layout",
            ],
        ),
    }
    yield out
    weights.reset()


def test_all_layers_reproduce_every_headline_pair(runs):
    base = runs["base"]
    assert base.headline_ok == len(HEADLINE)
    assert base.report.false_merges == []


def test_without_campaign_identifiers_the_contradiction_rule_is_lost(runs):
    r = runs["no_campaign"]
    assert len(r.report.false_merges) >= 1
    assert r.headline[("48:page", "61:article")] is RelationType.SAME_CAMPAIGN
    assert any("48" in x and "61" in x for x in r.flipped_from(runs["base"]))


def test_without_the_domain_layer_the_duplicate_loses_its_anchor(runs):
    assert runs["no_domain"].headline[("2:healthlinkq", "3:page")] is RelationType.SAME_CAMPAIGN


def test_degradation_no_har_keeps_the_campaign_links(runs):
    r = runs["no_har"]
    assert r.headline[("1:page", "2:page")] is RelationType.SAME_CAMPAIGN
    assert r.headline[("48:page", "61:article")] is RelationType.SAME_FAMILY
    assert r.report.false_merges == []


def test_degradation_screenshots_only_still_links_the_copied_ad(runs):
    r = runs["screens"]
    assert r.relations[("1:ad", "99:ad")] is RelationType.DUPLICATE
    assert r.relations[("1:page", "2:page")] is RelationType.INSUFFICIENT
    assert r.report.false_merges == []


def test_degradation_reencoded_images_keep_perceptual_matches(runs):
    r = runs["reencoded"]
    assert r.relations[("1:ad", "99:ad")] is RelationType.DUPLICATE  # 2-3 bits away still matches
    assert r.headline_ok == len(HEADLINE)


def test_degradation_swapped_domain_keeps_the_campaign_and_demotes_the_duplicate(runs):
    r = runs["swapped"]
    assert r.headline[("1:page", "2:page")] is RelationType.SAME_CAMPAIGN
    # the healthlinkq capture moved to another host: still the same campaign via two artifact groups
    assert r.headline[("2:healthlinkq", "3:page")] in (
        RelationType.SAME_CAMPAIGN,
        RelationType.DUPLICATE,
    )
    assert r.report.false_merges == []


def test_model_off_keeps_every_deterministic_link_and_loses_only_the_playbook(runs):
    r = runs["model_off"]
    assert r.report.false_merges == []
    for a, b, t in HEADLINE:
        if t is RelationType.SAME_PLAYBOOK:
            assert r.headline[(a, b)] is RelationType.UNRELATED
        else:
            assert r.headline[(a, b)] is t


def test_format_runs_renders(runs):
    text = format_runs([runs["base"], runs["no_campaign"]], "t")
    assert "no campaign ids" in text and "merges" in text
