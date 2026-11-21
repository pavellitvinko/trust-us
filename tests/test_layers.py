"""Identifier, asset, kit and text layers built from the fixtures (HAC-22, HAC-23, HAC-25)."""

import pytest

from ftf import pipeline
from ftf.compare.weights import weights
from ftf.contracts import Tier
from ftf.eval.fixtures import store_from_fixtures
from ftf.registry import layers, load_plugins

KIT_FAMILY = ("1:page", "2:page", "48:page", "61:article")
OTHERS = ("10:page", "14:page", "47:page", "36:page")


@pytest.fixture(scope="module")
def fixture_store():
    load_plugins()
    s = store_from_fixtures()
    pipeline.run_fingerprint(s)
    weights.fit(s)
    yield s
    weights.reset()
    s.close()


def scores(store, a, b, tier=None, prefix=None):
    out = {}
    for e in pipeline.compare_views(store, a, b):
        if not e.comparable:
            continue
        layer = layers.get(e.layer_id)
        if tier is not None and layer.tier is not tier:
            continue
        if prefix is not None and not e.layer_id.startswith(prefix):
            continue
        out[e.layer_id] = e.score
    return out


def test_every_layer_declares_a_registered_comparator_and_builds_somewhere(fixture_store):
    from ftf.registry import comparators

    built = {f.layer_id for f in fixture_store.fingerprints()}
    expected = {
        "ids.domain",
        "ids.campaign",
        "ids.tracking",
        "ids.conversion",
        "brand.offer",
        "asset.exact",
        "kit.config",
        "kit.url",
        "kit.form",
        "kit.dom",
        "kit.code",
        "infra.host",
        "text.shingle",
    }
    assert expected <= set(layers.names())
    assert expected <= built
    for layer in layers.all():
        assert layer.comparator in comparators.names()
        assert layer.group and layer.tier in Tier


def test_identity_layers_link_the_headline_pairs(fixture_store):
    s = fixture_store
    assert scores(s, "1:page", "2:page")["ids.campaign"] >= 0.99
    assert scores(s, "1:page", "2:page")["ids.tracking"] >= 0.85
    assert scores(s, "1:page", "2:page")["ids.domain"] == 0.0  # different domains, not a penalty
    assert scores(s, "2:healthlinkq", "3:page")["ids.domain"] >= 0.95  # same host + path
    assert scores(s, "61:article", "61:chat")["ids.campaign"] >= 0.95
    assert scores(s, "48:page", "61:article")["ids.campaign"] == 0.0  # different campaign records


def test_outbound_target_domains_count_in_ids_domain(fixture_store):
    e = next(
        e
        for e in pipeline.compare_views(fixture_store, "2:healthlinkq", "3:page")
        if e.layer_id == "ids.domain"
    )
    assert {s["ns"] for s in e.shared} >= {"domain.registrable", "url.host_path", "outbound.domain"}


def test_asset_exact_links_shared_images_only(fixture_store):
    assert scores(fixture_store, "1:page", "2:page")["asset.exact"] >= 0.99
    assert scores(fixture_store, "48:page", "61:article")["asset.exact"] >= 0.99
    assert scores(fixture_store, "1:page", "48:page")["asset.exact"] == 0.0


def test_kit_family_scores_high_within_and_low_across(fixture_store):
    s = fixture_store
    for i, a in enumerate(KIT_FAMILY):
        for b in KIT_FAMILY[i + 1 :]:
            kit = scores(s, a, b, tier=Tier.KIT)
            strong = [k for k, v in kit.items() if v >= 0.7]
            assert len(strong) >= 2, (a, b, kit)
    for a in KIT_FAMILY:
        for b in OTHERS:
            kit = scores(s, a, b, tier=Tier.KIT)
            strong = [k for k, v in kit.items() if v >= 0.7]
            assert len(strong) <= 1, (a, b, kit)


def test_unproven_kit_hypotheses_stay_below_family_strength(fixture_store):
    # 3 <-> 14 share clickid/key; 36 <-> 47 share media_type/utm_placement. One weak group each.
    assert scores(fixture_store, "3:page", "14:page")["kit.url"] < 0.7
    assert scores(fixture_store, "36:page", "47:page")["kit.url"] < 0.7
    assert all(v < 0.7 for v in scores(fixture_store, "3:page", "14:page", tier=Tier.KIT).values())


def test_common_library_hash_is_stoplisted(fixture_store):
    e = next(
        e
        for e in pipeline.compare_views(fixture_store, "14:page", "47:page")
        if e.layer_id == "kit.code"
    )
    assert e.score == 0.0 and e.shared == []  # shared jQuery bytes carry nothing


def test_platform_url_parameters_do_not_link(fixture_store):
    assert scores(fixture_store, "10:page", "36:page")["kit.url"] == 0.0


def test_text_shingle_near_duplicates(fixture_store):
    s = fixture_store
    assert scores(s, "1:page", "2:page")["text.shingle"] >= 0.9
    assert scores(s, "2:healthlinkq", "3:page")["text.shingle"] >= 0.9
    assert scores(s, "14:page", "14:capture")["text.shingle"] >= 0.7  # variant, not a copy
    assert scores(s, "1:page", "10:page")["text.shingle"] < 0.05
    assert scores(s, "48:page", "61:article")["text.shingle"] < 0.1  # same headline, new body


def test_missing_layers_are_left_out_not_penalised(fixture_store):
    evidence = pipeline.compare_views(fixture_store, "5:video", "1:page")
    assert evidence and all(not e.comparable for e in evidence)
    assert "kit.config" not in scores(fixture_store, "10:page", "1:page")
