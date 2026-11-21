"""Three-level grouping and the evaluation harness on the fixtures (HAC-30, HAC-27)."""

import pytest

from ftf import pipeline
from ftf.compare.weights import weights
from ftf.contracts import Relation, RelationType
from ftf.eval import evaluate, format_report, load_reference
from ftf.eval.fixtures import store_from_fixtures
from ftf.fusion import run_method
from ftf.grouping import merge_guard
from ftf.grouping.levels import build_campaigns, build_playbooks


@pytest.fixture(scope="module")
def grouped():
    s = store_from_fixtures()
    pipeline.run_fingerprint(s)
    groups = run_method(s)
    yield s, groups
    weights.reset()
    s.close()


def test_cases_1_and_2_form_one_campaign(grouped):
    _, g = grouped
    assert g.campaign_of["1:page"] == g.campaign_of["2:page"]
    assert g.campaign_of["2:page"] != g.campaign_of["2:healthlinkq"]  # folder 2 holds two campaigns


def test_48_and_61_are_separate_campaigns_in_one_family(grouped):
    _, g = grouped
    assert g.campaign_of["48:page"] != g.campaign_of["61:article"]
    assert g.campaign_of["61:article"] == g.campaign_of["61:chat"]
    assert g.family_of_view("48:page") == g.family_of_view("61:article")


def test_health_offers_14_and_47_are_separate_campaigns(grouped):
    _, g = grouped
    assert g.campaign_of["14:page"] == g.campaign_of["14:capture"]
    assert g.campaign_of["14:page"] != g.campaign_of["47:page"]
    assert g.family_of_view("14:page") != g.family_of_view("47:page")


def test_duplicate_capture_joins_its_landing(grouped):
    _, g = grouped
    assert g.campaign_of["2:healthlinkq"] == g.campaign_of["3:page"]


def test_video_only_incident_stays_alone(grouped):
    _, g = grouped
    assert g.campaigns[g.campaign_of["5:video"]] == ["5:video"]
    assert "5:video" not in g.playbooks_of


def test_second_pass_counts_rarity_over_campaigns(grouped):
    # after grouping, 1:page and 2:page are one holder, so their shared pixel stays rare
    assert weights.holders[("fb_pixel", "2012165449475163")] == 1
    assert weights.weight("fb_pixel", "2012165449475163") == pytest.approx(0.9)


def test_merge_guard_rejects_a_bare_same_campaign_edge():
    weak = Relation(
        view_a="a", view_b="b", type=RelationType.SAME_CAMPAIGN, tier_scores={"T1": 0.3}
    )
    assert not merge_guard(weak)
    strong = Relation(
        view_a="a", view_b="b", type=RelationType.SAME_CAMPAIGN, tier_scores={"T1": 0.95}
    )
    assert merge_guard(strong)
    campaigns, _, rejected = build_campaigns(["a", "b"], [weak])
    assert len(campaigns) == 2 and rejected


def test_playbook_clustering_cuts_at_the_threshold():
    rel = lambda a, b, s: Relation(
        view_a=a, view_b=b, type=RelationType.SAME_PLAYBOOK, tier_scores={"T4": s}
    )
    playbooks, of = build_playbooks(
        ["a", "b", "c", "d"],
        [rel("a", "b", 0.9), rel("b", "c", 0.8), rel("a", "c", 0.85), rel("c", "d", 0.2)],
    )
    assert list(playbooks.values()) == [["a", "b", "c"]]
    assert "d" not in of


# -- evaluation harness ------------------------------------------------------------------


def test_reference_set_covers_the_matrix_and_sub_folder_edges():
    ref = load_reference()
    incident_pairs = [p for p in ref if p.level == "incident"]
    assert len(incident_pairs) == 45
    assert len(ref) > 45


def test_eval_reports_no_false_merges_and_the_headline_pairs(grouped):
    s, _ = grouped
    report = evaluate(s)
    assert report.false_merges == []
    got = {(r.ref.a, r.ref.b): r.predicted for r in report.results}
    assert got[("1", "2")] is RelationType.SAME_CAMPAIGN
    assert got[("2", "3")] is RelationType.DUPLICATE
    assert got[("48", "61")] is RelationType.SAME_FAMILY
    assert got[("61:article", "61:chat")] is RelationType.SAME_CAMPAIGN
    assert got[("14", "47")] is not RelationType.SAME_CAMPAIGN
    assert report.by_type[RelationType.SAME_CAMPAIGN].precision == 1.0
    assert report.by_type[RelationType.DUPLICATE].recall == 1.0
    text = format_report(report)
    assert "false merges: 0" in text and "same_family" in text


def test_false_merge_is_counted(grouped):
    s, _ = grouped
    from ftf.eval.reference import ReferencePair

    bad = [ReferencePair(a="14", b="47", expected=RelationType.SAME_PLAYBOOK)]
    report = evaluate(s, bad)
    assert not report.false_merges  # we say unrelated, which is a disagreement, not a merge
    fake = ReferencePair(a="1", b="2", expected=RelationType.SAME_PLAYBOOK)
    assert evaluate(s, [fake]).false_merges  # a campaign link where only playbook is allowed


def test_reference_aliases_resolve_against_real_view_ids():
    from ftf.contracts import Incident, View, ViewKind
    from ftf.eval.reference import resolve_view
    from ftf.store import Store

    s = Store(":memory:")
    s.put_incident(
        Incident(incident_id="61", root="/x"),
        [],
        [
            View(
                view_id="61:aaa",
                incident_id="61",
                kind=ViewKind.PAGE,
                label="a",
                primary="strona_1/art.html",
                meta={"origin": "saved_page"},
            ),
            View(
                view_id="61:bbb",
                incident_id="61",
                kind=ViewKind.PAGE,
                label="b",
                primary="strona_2/chat.html",
                meta={"origin": "saved_page"},
            ),
            View(view_id="61:ccc", incident_id="61", kind=ViewKind.IMAGE, label="c"),
        ],
    )
    s.put_incident(
        Incident(incident_id="2", root="/x"),
        [],
        [
            View(
                view_id="2:ppp",
                incident_id="2",
                kind=ViewKind.PAGE,
                label="p",
                primary="page.html",
                meta={"origin": "saved_page"},
            ),
            View(
                view_id="2:hhh",
                incident_id="2",
                kind=ViewKind.PAGE,
                label="h",
                meta={"origin": "har_only"},
            ),
        ],
    )
    assert resolve_view(s, "61:article") == "61:aaa"
    assert resolve_view(s, "61:chat") == "61:bbb"
    assert resolve_view(s, "61:ad") == "61:ccc"
    assert resolve_view(s, "61:page") is None  # ambiguous: two saved pages, none plain
    assert resolve_view(s, "2:page") == "2:ppp"
    assert resolve_view(s, "2:healthlinkq") == "2:hhh"
    assert resolve_view(s, "2:hhh") == "2:hhh"  # a real id resolves to itself
    assert resolve_view(s, "9:page") is None
    s.close()
