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


# second review (61811d2): contradictions beyond candidate pairs, component checks in assign,
# comparator compatibility in the novel fallback --------------------------------------------


def _abc_store() -> Store:
    """A=(campaign X, pixel P1), B=(pixels P1, P2), C=(campaign Y, pixel P2). A-C is never a
    candidate pair: they share no value, yet they contradict."""
    s = Store(":memory:")
    spec = {
        "a:page": [("aio.campaign_uuid", "X"), ("fb_pixel", "P1")],
        "b:page": [("fb_pixel", "P1"), ("fb_pixel", "P2")],
        "c:page": [("aio.campaign_uuid", "Y"), ("fb_pixel", "P2")],
    }
    for vid, rows in spec.items():
        iid = vid.split(":")[0]
        s.put_incident(
            Incident(incident_id=iid, root="/x"),
            [],
            [View(view_id=vid, incident_id=iid, kind=ViewKind.PAGE, label=vid)],
        )
        obs = [
            Observation(
                view_id=vid,
                signal_id="A14",
                namespace="domain.registrable",
                value=f"{iid}.example",
                extractor="t",
                version="1",
            )
        ]
        obs += [
            Observation(
                view_id=vid, signal_id="A1", namespace=ns, value=v, extractor="t", version="1"
            )
            for ns, v in rows
        ]
        s.put_observations(vid, "t", obs)
    pipeline.run_fingerprint(s)
    return s


@pytest.mark.parametrize("candidates", [False, True])
def test_contradiction_between_non_candidates_still_blocks_the_chain(candidates):
    s = _abc_store()
    g = run_method(s, candidates=candidates)
    assert g.campaign_of["a:page"] == g.campaign_of["b:page"]
    assert g.campaign_of["c:page"] != g.campaign_of["a:page"], g.campaigns
    assert any("contradiction between the components" in why for *_, why in g.rejected_merges)
    weights.reset()
    s.close()


def test_assign_refuses_a_campaign_containing_a_contradicting_member():
    s = _abc_store()
    g = run_method(s)  # all-pairs grouping: {A, B} is one campaign, C is apart
    assert g.campaign_of["a:page"] == g.campaign_of["b:page"]
    a = assign(s, "c:page", g, prepared=True)
    assert a.outcome is not Outcome.VARIANT_OF_CAMPAIGN
    assert a.outcome is Outcome.NEW_CAMPAIGN_IN_FAMILY  # capped at family by the contradiction
    assert any("refused: contradicts a:page on aio.campaign_uuid" in r for r in a.reasons)
    weights.reset()
    s.close()


def test_cheap_coverage_honours_comparator_compatibility():
    from ftf.grouping.candidates import cheap_relation

    s = Store(":memory:")
    for vid, kind in (("k1:page", "advertorial"), ("k2:page", "product_lander")):
        iid = vid.split(":")[0]
        s.put_incident(
            Incident(incident_id=iid, root="/x"),
            [],
            [View(view_id=vid, incident_id=iid, kind=ViewKind.PAGE, label=vid)],
        )
        obs = [
            Observation(
                view_id=vid,
                signal_id="A14",
                namespace="domain.registrable",
                value=f"{iid}.example",
                extractor="t",
                version="1",
            ),
            Observation(
                view_id=vid,
                signal_id="K",
                namespace="page.kind",
                value=kind,
                extractor="t",
                version="1",
            ),
        ]
        obs += [
            Observation(
                view_id=vid,
                signal_id="K",
                namespace="expect.element",
                value=el,
                extractor="t",
                version="1",
            )
            for el in ("imprint", "author", "contact", "terms")
        ]
        s.put_observations(vid, "t", obs)
    pipeline.run_fingerprint(s)
    prepare(s)
    r = cheap_relation(s, "k1:page", "k2:page")
    assert r.coverage == pytest.approx(0.5)  # domain yes; absence vector of another page kind no
    weights.reset()
    s.close()


# [third review, eba631a] -------------------------------------------------------------------


def _put(s: Store, vid: str, rows) -> None:
    iid = vid.split(":")[0]
    s.put_incident(
        Incident(incident_id=iid, root="/x"),
        [],
        [View(view_id=vid, incident_id=iid, kind=ViewKind.PAGE, label=vid)],
    )
    rows = [("domain.registrable", f"{iid}.example"), *rows]
    s.put_observations(
        vid,
        "t",
        [
            Observation(
                view_id=vid, signal_id="A1", namespace=ns, value=v, extractor="t", version="1"
            )
            for ns, v in rows
        ],
    )


def test_duplicate_assignment_is_refused_when_the_campaign_has_a_contradicting_member():
    """Campaign {A, B}: A declares campaign X, B has no UUID. C declares Y and carries B's
    creative. C-B alone is a duplicate, but C contradicts A, so it is not a duplicate of B."""
    creative = "f0f0f0f00f0f0f0f"
    s = Store(":memory:")
    _put(s, "a:page", [("aio.campaign_uuid", "X"), ("fb_pixel", "P1"), ("fb_pixel", "P2")])
    _put(s, "b:page", [("fb_pixel", "P1"), ("fb_pixel", "P2"), ("creative.phash", creative)])
    pipeline.run_fingerprint(s)
    g = run_method(s)
    assert g.campaign_of["a:page"] == g.campaign_of["b:page"], g.campaigns

    _put(s, "c:page", [("aio.campaign_uuid", "Y"), ("creative.phash", creative)])
    pipeline.run_fingerprint(s, ["c:page"])
    a = assign(s, "c:page", g)
    assert any(x.view_id == "b:page" for x in a.related[RelationType.DUPLICATE.value])
    assert a.outcome is not Outcome.DUPLICATE_OF, a.reasons
    assert a.outcome is Outcome.NEW_CAMPAIGN_IN_FAMILY
    assert any("refused: contradicts a:page on aio.campaign_uuid" in r for r in a.reasons)
    weights.reset()
    s.close()


def test_duplicate_assignment_still_works_without_a_contradiction():
    creative = "f0f0f0f00f0f0f0f"
    s = Store(":memory:")
    _put(s, "a:page", [("fb_pixel", "P1"), ("fb_pixel", "P2")])
    _put(s, "b:page", [("fb_pixel", "P1"), ("fb_pixel", "P2"), ("creative.phash", creative)])
    pipeline.run_fingerprint(s)
    g = run_method(s)
    _put(s, "c:page", [("creative.phash", creative)])
    pipeline.run_fingerprint(s, ["c:page"])
    a = assign(s, "c:page", g)
    assert a.outcome is Outcome.DUPLICATE_OF and a.target == "b:page", a.reasons
    weights.reset()
    s.close()


def test_standalone_image_and_its_reencode_are_duplicates(tmp_path):
    """A PNG screenshot and its JPEG re-encoding: the asset extractor's whole-image pHash
    reaches `ad.creative`, which anchors a duplicate. It does not also count in
    `asset.perceptual`, which stays for content images on pages."""
    from ftf.ingest import ingest_folder

    from .test_extract_assets import picture, reencode

    png = picture(7)
    for name, data in (("one", png), ("two", reencode(png))):
        (tmp_path / name).mkdir()
        (tmp_path / name / ("reklama.png" if name == "one" else "reklama.jpg")).write_bytes(data)
    s = Store(":memory:")
    ids = []
    for name in ("one", "two"):
        _, views = ingest_folder(tmp_path / name, s)
        ids += [v.view_id for v in views if v.kind is ViewKind.IMAGE]
    assert len(ids) == 2
    pipeline.run_extract(s)
    pipeline.run_fingerprint(s)
    layers_of = {v: {f.layer_id for f in s.fingerprints(view_id=v)} for v in ids}
    for v in ids:
        assert "ad.creative" in layers_of[v]
        assert "asset.perceptual" not in layers_of[v]
    g = run_method(s)
    assert g.campaign_of[ids[0]] == g.campaign_of[ids[1]], g.campaigns
    a = assign(s, ids[1], g, prepared=True)
    assert a.outcome is Outcome.DUPLICATE_OF and a.target == ids[0], a.reasons
    weights.reset()
    s.close()


# [P2] uncertainty preserved in open-set assignment ---------------------------------------------


def test_differing_screenshots_are_insufficient_not_novel(tmp_path):
    """Two standalone screenshots with different perceptual hashes: fusion calls the pair
    insufficient (the identity tier is not comparable), and `assign` must say the same
    rather than promote adequate coverage into "novel pattern"."""
    from ftf.fusion import fuse_pair
    from ftf.ingest import ingest_folder

    from .test_extract_assets import picture

    for name, seed in (("one", 1), ("two", 2)):
        (tmp_path / name).mkdir()
        (tmp_path / name / "reklama.png").write_bytes(picture(seed))
    s = Store(":memory:")
    ids = []
    for name in ("one", "two"):
        _, views = ingest_folder(tmp_path / name, s)
        ids += [v.view_id for v in views if v.kind is ViewKind.IMAGE]
    pipeline.run_extract(s)
    pipeline.run_fingerprint(s)
    g = run_method(s)
    r = fuse_pair(s, ids[0], ids[1], persist=False)
    assert r.type is RelationType.INSUFFICIENT and r.coverage >= 0.3, r  # seen, but not separable
    a = assign(s, ids[1], g, prepared=True)
    assert a.outcome is Outcome.INSUFFICIENT, a.reasons
    assert any("identity tier was not comparable" in x for x in a.reasons)
    weights.reset()
    s.close()


def test_novel_still_needs_only_one_separable_comparison():
    """The disjoint-set-layer scenario keeps its outcome: those pages carry identity layers,
    so fusion can call them unrelated and the view is novel."""
    s = _disjoint_store()
    prepare(s)
    a = assign(s, "x:page", prepared=True)
    assert a.outcome is Outcome.NOVEL and any("identity tier included" in r for r in a.reasons)
    weights.reset()
    s.close()
