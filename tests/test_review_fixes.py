"""Regression tests for the six findings of the first PR review (pavellitvinko, 2f76c65)."""

import json

import pytest

from ftf import pipeline
from ftf.compare.weights import WeightTable, weights
from ftf.contracts import Incident, Observation, Relation, RelationType, View, ViewKind
from ftf.eval.fixtures import store_from_fixtures
from ftf.fusion import prepare, run_method
from ftf.grouping import Outcome, assign, candidate_views
from ftf.grouping.candidates import candidate_pairs
from ftf.grouping.levels import build_campaigns
from ftf.store import Store


def _rel(a, b, t, t1=0.0, reasons=()):
    return Relation(
        view_a=a, view_b=b, type=t, tier_scores={"T1": t1, "T2": 0.0}, reasons=list(reasons)
    )


# [P1] contradictions across components -------------------------------------------------------


def test_transitive_merge_is_refused_when_the_components_contradict():
    rels = [
        _rel("a", "b", RelationType.SAME_CAMPAIGN, t1=0.95),
        _rel("b", "c", RelationType.SAME_CAMPAIGN, t1=0.9),
        _rel(
            "a", "c", RelationType.SAME_FAMILY, reasons=["contradiction: aio.campaign_uuid X vs Y"]
        ),
    ]
    _, of, rejected = build_campaigns(["a", "b", "c"], rels)
    assert of["a"] == of["b"] != of["c"]  # b joins the stronger side; c stays apart
    assert any("contradiction between the components" in why for _, _, why in rejected)


def test_funnel_union_respects_contradictions_too():
    rels = [
        _rel(
            "a", "c", RelationType.SAME_FAMILY, reasons=["contradiction: aio.campaign_uuid X vs Y"]
        )
    ]
    _, of, rejected = build_campaigns(["a", "c", "ad"], rels, funnel=[("ad", "a"), ("ad", "c")])
    assert of["ad"] == of["a"] != of["c"]
    assert rejected


# [P1] rarity bootstrap -------------------------------------------------------------------------


def _store_domains_sharing(uuid_holders: int, n_domains: int = 6) -> Store:
    s = Store(":memory:")
    for i in range(n_domains):
        vid = f"i{i}:page"
        s.put_incident(
            Incident(incident_id=f"i{i}", root="/x"),
            [],
            [View(view_id=vid, incident_id=f"i{i}", kind=ViewKind.PAGE, label=vid)],
        )
        obs = [
            Observation(
                view_id=vid,
                signal_id="A14",
                namespace="domain.registrable",
                value=f"site{i}.example",
                extractor="t",
                version="1",
            ),
            Observation(
                view_id=vid,
                signal_id="A3",
                namespace="fb_pixel",
                value="PIXEL",
                extractor="t",
                version="1",
            ),
        ]
        if i < uuid_holders:
            obs.append(
                Observation(
                    view_id=vid,
                    signal_id="A1",
                    namespace="aio.campaign_uuid",
                    value="CAMPAIGN",
                    extractor="t",
                    version="1",
                )
            )
        s.put_observations(vid, "t", obs)
    return s


def test_one_campaign_on_six_domains_keeps_its_uuid_and_pixel():
    table = WeightTable()
    table.fit(_store_domains_sharing(uuid_holders=6))
    assert table.holders[("aio.campaign_uuid", "CAMPAIGN")] == 1
    assert table.weight("aio.campaign_uuid", "CAMPAIGN") == pytest.approx(0.95)
    assert table.weight("fb_pixel", "PIXEL") == pytest.approx(0.9)  # one record holds it


def test_pixel_shared_by_six_unrelated_domains_is_still_suppressed():
    table = WeightTable()
    table.fit(_store_domains_sharing(uuid_holders=0))
    assert table.weight("fb_pixel", "PIXEL") == 0.0


# [P2] candidate index over fingerprint items ---------------------------------------------------


def _pattern_store() -> Store:
    s = Store(":memory:")
    for vid, score in (("p1:page", 0.95), ("p2:page", 0.90)):
        iid = vid.split(":")[0]
        s.put_incident(
            Incident(incident_id=iid, root="/x"),
            [],
            [View(view_id=vid, incident_id=iid, kind=ViewKind.PAGE, label=vid)],
        )
        s.put_observations(
            vid,
            "t",
            [
                Observation(
                    view_id=vid,
                    signal_id="H",
                    namespace="pattern.score",
                    value=json.dumps({"pattern": "H9_scarcity", "score": score}),
                    extractor="t",
                    version="1",
                ),
                Observation(
                    view_id=vid,
                    signal_id="H",
                    namespace="pattern.score",
                    value=json.dumps({"pattern": "H11_discount", "score": score}),
                    extractor="t",
                    version="1",
                ),
            ],
        )
    pipeline.run_fingerprint(s)
    return s


def test_pattern_scores_with_different_values_still_make_a_candidate_pair():
    s = _pattern_store()
    assert ("p1:page", "p2:page") in candidate_pairs(s)
    assert "p2:page" in candidate_views(s, "p1:page")
    g_all = run_method(s, candidates=False)
    rel_all = {(r.view_a, r.view_b): r.type for r in s.relations()}
    g_cand = run_method(s, candidates=True)
    rel_cand = {(r.view_a, r.view_b): r.type for r in s.relations()}
    assert rel_all == rel_cand and rel_all[("p1:page", "p2:page")] is RelationType.SAME_PLAYBOOK
    assert g_all.playbooks == g_cand.playbooks
    weights.reset()


# [P2] novel pattern for observable non-candidates -----------------------------------------------


SET_ROWS = (
    ("A14", "domain.registrable", "{v}.example"),
    ("A3", "fb_pixel", "{v}0001"),
    ("A8", "api_key_id", "KEY-{v}"),
    ("A13", "brand.offer", "Offer {v}"),
    ("E1", "asset.sha256", "{v}aaaa"),
    ("A2", "aio.template_uuid", "tpl-{v}"),
    ("B1", "url.param_name", "param_{v}"),
    ("B4", "form.field", "field_{v}"),
    ("D3", "firstparty.sha256", "{v}bbbb"),
    ("C3", "dns.ns_pair", "ns1.{v}|ns2.{v}"),
    ("G1", "entity.cast", '{{"entity": "Person {v}", "role": "role_{v}"}}'),
)


def _disjoint_store() -> Store:
    """Two pages observed on eleven set layers, sharing no value and carrying no text or hash."""
    s = Store(":memory:")
    for vid, tag in (("x:page", "x"), ("y:page", "y")):
        iid = vid.split(":")[0]
        s.put_incident(
            Incident(incident_id=iid, root="/x"),
            [],
            [View(view_id=vid, incident_id=iid, kind=ViewKind.PAGE, label=vid)],
        )
        s.put_observations(
            vid,
            "t",
            [
                Observation(
                    view_id=vid,
                    signal_id=sig,
                    namespace=ns,
                    value=val.format(v=tag),
                    extractor="t",
                    version="1",
                )
                for sig, ns, val in SET_ROWS
            ],
        )
    pipeline.run_fingerprint(s)
    return s


def test_well_observed_view_sharing_nothing_is_novel_not_insufficient():
    s = _disjoint_store()
    prepare(s)
    assert candidate_views(s, "x:page") == set()  # the reviewer's scenario: no candidate at all
    a = assign(s, "x:page", prepared=True)
    assert a.outcome is Outcome.NOVEL, a.reasons
    assert any("comparable with" in r for r in a.reasons)
    weights.reset()
    s.close()


def test_lone_video_is_still_insufficient():
    s = store_from_fixtures()
    pipeline.run_fingerprint(s)
    prepare(s)
    assert assign(s, "5:video", prepared=True).outcome is Outcome.INSUFFICIENT
    weights.reset()
    s.close()


# [P2] --candidates forwarded ---------------------------------------------------------------------


def test_cli_candidates_flag_skips_brute_force_and_reaches_run_method(monkeypatch, tmp_path):
    from ftf import cli

    calls = {"compare_all": 0, "run_method": []}
    monkeypatch.setenv("FTF_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        cli.pipeline,
        "run_compare_all",
        lambda store: calls.__setitem__("compare_all", calls["compare_all"] + 1) or 0,
    )

    real = cli.run_method

    def spy(store, **kw):
        calls["run_method"].append(kw)
        return real(store, **kw)

    monkeypatch.setattr(cli, "run_method", spy)
    cli.main(["load-fixtures", "--candidates"])
    assert calls["compare_all"] == 0
    assert calls["run_method"] == [{"candidates": True}]
    cli.main(["load-fixtures"])
    assert calls["compare_all"] == 1
    assert calls["run_method"][-1] == {"candidates": False}
    weights.reset()


# coverage over groups observed in the corpus ----------------------------------------------------


def test_coverage_counts_only_groups_the_corpus_can_build():
    """A layer no extractor produces for any view must not push every pair to insufficient."""
    from ftf.fusion.fuse import active_groups

    s = _disjoint_store()  # eleven set layers, no text, no hashes, no model output
    prepare(s)
    assert active_groups() is not None and len(active_groups()) == 11
    from ftf.fusion import fuse_pair

    r = fuse_pair(s, "x:page", "y:page", persist=False)
    assert r.type is RelationType.UNRELATED and r.coverage == 1.0
    weights.reset()
    assert active_groups() is None
    s.close()
