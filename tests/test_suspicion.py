"""Suspicion summary over the namespaces the pipeline really writes."""

from ftf.contracts import Observation, Purpose, Source, State
from ftf.grouping.suspicion import reason_summary_for_view


def obs(namespace, value=None, state=State.PRESENT, raw=None):
    return Observation(
        view_id="v",
        signal_id="X",
        namespace=namespace,
        value=value,
        raw=raw,
        state=state,
        purpose=Purpose.RISK,
        source=Source(file="page.html", locator="loc"),
        extractor="test",
        version="1",
    )


def test_level_counts_independent_kinds_and_reasons_link_to_source():
    summary = reason_summary_for_view(
        "v",
        [
            obs("url.host", "gazeta-news.xyz"),
            obs("domain.tld_risk", "high"),
            obs("rdap.domain_age_days", 39),
            obs("image.claimed_publisher", "TVN24"),
            obs("expect.element", "terms", State.ABSENT, "expected element not found: Terms"),
            obs("dom.evasion_filter", "hue-rotate"),
            obs("pattern.score", {"pattern": "pattern.1_x", "score": 0.9}),
        ],
    )
    assert summary.level == 5
    assert len(summary.reasons_by_kind["domain"]) == 2
    reason = summary.reasons_by_kind["absent_elements"][0]
    assert reason.summary == "expected element not found: Terms"
    assert reason.source.file == "page.html"
    assert reason.observation_ref == "test/expect.element#4"


def test_clean_or_missing_signals_are_not_reasons():
    summary = reason_summary_for_view(
        "v",
        [
            obs("url.host", "tvn24.pl"),
            obs("domain.tld_risk", "low"),
            obs("rdap.domain_age_days", 4000),
            obs("image.claimed_publisher", "TVN24"),
            obs("warning.cert", {"result": "not_listed"}, State.ABSENT),
            obs("expect.element", "terms", State.NOT_OBSERVABLE, "timeout"),
            obs("pattern.score", {"pattern": "p", "score": 0.0}, State.ABSENT),
        ],
    )
    assert summary.level == 0
    assert "absent_elements" in summary.unchecked_kinds
    assert "domain" not in summary.unchecked_kinds
