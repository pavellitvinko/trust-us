"""Narrative verification. A fake model only."""

import pytest

from ftf import narrative as na
from ftf.contracts import Incident, Relation, RelationType, View, ViewKind
from ftf.models import ModelResult

FACTS = {
    "F1": "relation: same_campaign",
    "F2": "confidence label: high",
    "F3": "coverage: 7 of 9 groups",
    "F4": "layer ids.tracker shares fb_pixel=2012165449475163",
    "F5": "view A is 1:abc (civicdaybook.live), incident 1",
}


def sentence(text, *ids):
    return na.Sentence(text=text, fact_ids=list(ids))


def written(*sentences):
    return na.Narrative(paragraphs=[na.Paragraph(sentences=list(sentences))])


GOOD = sentence("The verdict is same_campaign with a high label.", "F1", "F2")
SECOND = sentence("They share fb_pixel 2012165449475163.", "F4")


def test_supported_sentences_are_kept():
    paragraphs, rejected = na.verify(written(GOOD, SECOND), FACTS)
    assert rejected == 0 and len(paragraphs[0]) == 2


@pytest.mark.parametrize(
    "bad",
    [
        sentence("The pixel 999888777 links them.", "F4"),
        sentence("Coverage was 8 of 9 groups.", "F3"),
        sentence("The site evil-domain.com is shared.", "F4"),
        sentence("The operators work for Gazprom.", "F1"),
        sentence("The verdict is same_campaign.", "F99"),
        sentence("The verdict is same_campaign."),
        sentence("Coverage was 7 of 9 groups.", "F1"),
    ],
)
def test_unsupported_sentence_is_removed(bad):
    paragraphs, rejected = na.verify(written(GOOD, bad), FACTS)
    assert rejected == 1 and len(paragraphs[0]) == 1


@pytest.fixture
def linked(store):
    views = [View(view_id=v, incident_id="i", kind=ViewKind.PAGE, label=f"label {v}") for v in "ab"]
    store.put_incident(Incident(incident_id="i", root="."), [], views)
    store.put_relation(
        Relation(
            view_a="a",
            view_b="b",
            type=RelationType.SAME_CAMPAIGN,
            confidence="high",
            tier_scores={"T1": 0.9},
            reasons=["identity 0.90 [tracker=1.00]"],
        )
    )
    return store


def fake(monkeypatch, answer):
    monkeypatch.setattr("ftf.models.read_structured", lambda *a, **k: answer)


@pytest.mark.parametrize("reason", ["no_key", "refusal", "timeout", "schema"])
def test_model_failure_is_unavailable(linked, monkeypatch, reason):
    fake(monkeypatch, ModelResult.not_observable(reason))
    result = na.pair_narrative(linked, "a", "b")
    assert (result["status"], result["reason"]) == ("unavailable", reason)
    assert "unavailable" in na.render_narrative(result)


def test_too_little_supported_text_is_unavailable(linked, monkeypatch):
    fake(monkeypatch, ModelResult.ok(written(sentence("Gazprom did it.", "F1"))))
    result = na.pair_narrative(linked, "a", "b")
    assert (result["status"], result["reason"]) == ("unavailable", "unsupported")


def test_campaign_facts_come_from_stored_relations(linked):
    from ftf.grouping import build_groups

    campaign = build_groups(linked, persist_funnel=False).campaign_of["a"]
    facts = na.campaign_facts(linked, campaign)
    assert any("same_campaign" in fact for fact in facts.values())
    with pytest.raises(ValueError):
        na.campaign_facts(linked, "missing")


def test_import_needs_no_model_client():
    import subprocess
    import sys

    code = "import sys, ftf.narrative; print('ftf.models' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
