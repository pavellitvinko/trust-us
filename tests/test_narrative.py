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
        sentence("Gazprom runs both pages.", "F1"),
        sentence("The operators work for gazprom.", "F1"),
        sentence("The operator is: Gazprom.", "F1"),
        sentence("Coverage was 9 of 7 groups.", "F3"),
        sentence("The pixel 2012 links them.", "F4"),
        sentence("The site book.live is shared.", "F5"),
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


def _report(**extra):
    side = {"view_id": "a", "label": "l", "incident_id": "i"}
    return {
        "objects": {"a": side, "b": {**side, "view_id": "b"}},
        "relation": "same_campaign",
        "confidence": {"label": "high"},
        "coverage": {"comparable_groups": 7, "total_groups": 9},
        "reasons": [],
        "layers": [],
        "limitations": [],
        **extra,
    }


def test_facts_keep_contradictions_and_qualifiers():
    layer = {
        "layer_id": "ids.tracker",
        "status": "compared",
        "score": 0.9,
        "missing": [],
        "shared": [{"ns": "fb_pixel", "value": "1", "demoted_to_kit": True}],
        "differing": [
            {
                "ns": "aio.campaign_uuid",
                "exclusive_conflict": True,
                "a": [{"value": "x"}],
                "b": [{"value": "y"}],
            }
        ],
    }
    membership = {
        "same_campaign": True,
        "same_family": True,
        "funnel_edges": [],
    }
    facts = " ".join(na.pair_facts(_report(layers=[layer], membership=membership)).values())
    assert "demoted to kit" in facts
    assert "exclusive contradiction" in facts
    assert "not direct evidence" in facts


def test_a_long_table_keeps_decisive_facts_and_says_what_was_cut():
    layers = [
        {
            "layer_id": f"l{i}",
            "status": "compared",
            "score": 0.5,
            "missing": [],
            "differing": [],
            "shared": [{"ns": "n", "value": str(j)} for j in range(6)],
        }
        for i in range(40)
    ]
    membership = {"same_campaign": False, "same_family": True, "funnel_edges": []}
    facts = na.pair_facts(_report(layers=layers, membership=membership))
    assert len(facts) <= na.MAX_FACTS
    text = " ".join(facts.values())
    assert "same family: True" in text and "more evidence rows are not listed" in text


def test_a_faithful_copy_of_a_redacted_fact_is_kept(linked, monkeypatch):
    from ftf.models import redact_text

    value = redact_text("url=https://x.test/a?token=abcdef123456")
    facts = {"F1": f"relation: same_campaign; {value}"}
    good = sentence(f"The relation is same_campaign with {value}.", "F1")
    assert na.verify(written(good), facts)[1] == 0
