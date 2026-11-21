"""Funnel edges, incident roll-up and open-set assignment on the fixtures (HAC-29, HAC-31)."""

import pytest

from ftf import pipeline
from ftf.compare.weights import weights
from ftf.eval.fixtures import store_from_fixtures
from ftf.eval.loio import leave_one_incident_out
from ftf.fusion import run_method
from ftf.grouping import Outcome, assign, candidate_views, rollup
from ftf.grouping.funnel import MISMATCH_NS


@pytest.fixture(scope="module")
def corpus():
    s = store_from_fixtures()
    pipeline.run_fingerprint(s)
    groups = run_method(s)
    summaries = {x.incident_id: x for x in rollup(s, groups, groups.funnel_edges)}
    yield s, groups, summaries
    weights.reset()
    s.close()


# -- funnel edges and roll-up ---------------------------------------------------------------


def test_folder_2_rolls_up_to_two_campaigns(corpus):
    _, g, summaries = corpus
    two = summaries["2"]
    assert len(two.campaigns) == 2
    assert g.campaign_of["2:ad"] == g.campaign_of["2:page"] == g.campaign_of["1:page"]
    assert g.campaign_of["2:healthlinkq"] == g.campaign_of["3:page"]


def test_ad_joins_its_page_only_with_evidence(corpus):
    _, _, summaries = corpus
    edge = next(e for e in summaries["1"].funnel if e.source == "1:ad")
    assert edge.target == "1:page"
    assert any("domain shown in the ad = page host" in x for x in edge.evidence)
    sixty_one = summaries["61"]
    assert {(e.source, e.target) for e in sixty_one.funnel} >= {
        ("61:ad", "61:article"),
        ("61:article", "61:chat"),
    }
    assert any("stored navigation" in x for e in sixty_one.funnel for x in e.evidence)


def test_screenshot_only_incident_inherits_the_campaign_of_the_page_its_ad_led_to(corpus):
    _, g, summaries = corpus
    assert g.campaign_of["99:ad"] == g.campaign_of["1:page"]
    assert list(summaries["99"].shared_campaigns) == [g.campaign_of["1:page"]]


def test_cases_10_and_36_get_a_mismatch_observation_not_an_edge(corpus):
    s, g, summaries = corpus
    for incident, ad, page in (("10", "10:ad", "10:page"), ("36", "36:ad", "36:page")):
        assert summaries[incident].funnel == []
        assert g.campaign_of[ad] != g.campaign_of[page]  # co-capture never groups
        mism = [o for o in s.observations(ad) if o.namespace == MISMATCH_NS]
        assert len(mism) == 1 and mism[0].purpose.value == "risk"
        assert page in mism[0].value["pages"]
    assert summaries["36"].co_captured and summaries["36"].co_captured[0].kind == "co_captured"


def test_video_only_folder_has_no_mismatch_because_there_is_no_page(corpus):
    s, _, summaries = corpus
    assert summaries["5"].mismatches == []
    assert all(o.namespace != MISMATCH_NS for o in s.observations("5:video"))


# -- open-set assignment ---------------------------------------------------------------------


def test_candidates_come_from_the_indexes_and_exclude_the_own_folder(corpus):
    s, _, _ = corpus
    c = candidate_views(s, "1:page")
    assert "2:page" in c and "61:article" in c  # shared identifiers, shared kit values
    assert "1:ad" not in c
    assert candidate_views(s, "5:video") == set()  # nothing else carries a video layer


def test_assign_against_the_full_corpus(corpus):
    s, g, _ = corpus
    a = assign(s, "2:page", g, prepared=True)
    assert a.outcome is Outcome.VARIANT_OF_CAMPAIGN and a.target == g.campaign_of["1:page"]
    assert a.related["same_campaign"][0].view_id == "1:page"
    b = assign(s, "48:page", g, prepared=True)
    assert b.outcome is Outcome.NEW_CAMPAIGN_IN_FAMILY and b.target == g.family_of_view(
        "61:article"
    )


@pytest.mark.parametrize(
    "incident,view,outcome,target_contains",
    [
        ("2", "2:page", Outcome.VARIANT_OF_CAMPAIGN, "1:"),
        ("3", "3:page", Outcome.DUPLICATE_OF, "2:healthlinkq"),
        ("61", "61:article", Outcome.NEW_CAMPAIGN_IN_FAMILY, ""),
        ("5", "5:video", Outcome.INSUFFICIENT, None),
        ("36", "36:page", Outcome.NOVEL, None),
        ("99", "99:ad", Outcome.DUPLICATE_OF, "1:ad"),
    ],
)
def test_leave_one_incident_out(incident, view, outcome, target_contains):
    held = leave_one_incident_out(incident)
    a = next(x for x in held.per_view if x.view_id == view)
    assert a.outcome is outcome, (a.outcome, a.reasons)
    if target_contains is None:
        assert a.target is None
    else:
        assert a.target and target_contains in a.target
    weights.reset()


def test_held_out_61_lands_in_48s_family_not_its_campaign():
    held = leave_one_incident_out("61")
    article = next(x for x in held.per_view if x.view_id == "61:article")
    family_members = {x.view_id for x in article.related["same_family"]}
    assert "48:page" in family_members
    assert all(x.view_id != "48:page" for x in article.related["same_campaign"])
    assert held.headline.outcome is Outcome.NEW_CAMPAIGN_IN_FAMILY
    weights.reset()
