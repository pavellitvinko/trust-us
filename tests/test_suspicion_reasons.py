"""Tests for suspicion summary: reasons why views are suspicious."""

import pytest

from ftf.contracts import (
    Observation,
    Purpose,
    State,
    Source,
    SuspicionReason,
    SuspicionSummary,
)
from ftf.grouping.suspicion import reason_summary_for_view


class TestSuspicionReasonModel:
    """Test SuspicionReason data model."""

    def test_reason_creation(self):
        reason = SuspicionReason(
            kind="domain",
            signal_id="C1",
            namespace="domain.age",
            summary="domain registered 7 days ago",
            value=7,
            source=Source(file="test.html", locator="meta"),
        )
        assert reason.kind == "domain"
        assert reason.signal_id == "C1"
        assert reason.summary == "domain registered 7 days ago"
        assert reason.value == 7

    def test_reason_with_quote(self):
        reason = SuspicionReason(
            kind="impersonation",
            signal_id="G2",
            namespace="entity.impersonation",
            summary="impersonates Gazeta.pl",
            value="Gazeta.pl",
            quote="Gazeta.pl - artykuł",
            source=Source(file="page.html", locator="html:title"),
        )
        assert reason.quote == "Gazeta.pl - artykuł"


class TestSuspicionSummaryModel:
    """Test SuspicionSummary data model."""

    def test_summary_creation(self):
        summary = SuspicionSummary(
            view_id="1:page",
            level=2,
            reasons_by_kind={
                "domain": [
                    SuspicionReason(
                        kind="domain",
                        signal_id="C1",
                        namespace="domain.age",
                        summary="domain registered 7 days ago",
                        value=7,
                    )
                ],
                "impersonation": [
                    SuspicionReason(
                        kind="impersonation",
                        signal_id="G2",
                        namespace="entity.impersonation",
                        summary="impersonates Gazeta.pl",
                        value="Gazeta.pl",
                    )
                ],
            },
        )
        assert summary.view_id == "1:page"
        assert summary.level == 2
        assert len(summary.reasons_by_kind) == 2
        assert len(summary.reasons_by_kind["domain"]) == 1
        assert len(summary.reasons_by_kind["impersonation"]) == 1


class TestReasonSummaryGeneration:
    """Test reason_summary_for_view function."""

    def test_empty_observations(self):
        summary = reason_summary_for_view("view1", [])
        assert summary.view_id == "view1"
        assert summary.level == 0
        assert summary.reasons_by_kind == {}

    def test_linkage_only_ignored(self):
        """LINKAGE observations should be ignored; only RISK and BOTH are included."""
        obs = Observation(
            view_id="view1",
            signal_id="A1",
            namespace="aio.campaign_uuid",
            value="uuid-123",
            state=State.PRESENT,
            purpose=Purpose.LINKAGE,
            extractor="campaign_extractor",
            version="1.0",
        )
        summary = reason_summary_for_view("view1", [obs])
        assert summary.level == 0

    def test_domain_age_risk(self):
        """RISK observations with domain namespace should create domain reasons."""
        obs = Observation(
            view_id="view1",
            signal_id="C1",
            namespace="domain.age",
            value=7,
            state=State.PRESENT,
            purpose=Purpose.RISK,
            extractor="domain_extractor",
            version="1.0",
            source=Source(file="capture.html", locator="meta"),
        )
        summary = reason_summary_for_view("view1", [obs])
        assert summary.view_id == "view1"
        assert summary.level == 1
        assert "domain" in summary.reasons_by_kind
        assert len(summary.reasons_by_kind["domain"]) == 1
        reason = summary.reasons_by_kind["domain"][0]
        assert reason.kind == "domain"
        assert reason.signal_id == "C1"
        assert "7" in reason.summary
        assert reason.namespace == "domain.age"

    def test_impersonation_risk(self):
        """Impersonation observations should create impersonation reasons."""
        obs = Observation(
            view_id="view1",
            signal_id="G2",
            namespace="entity.impersonation",
            value="Gazeta.pl",
            state=State.PRESENT,
            purpose=Purpose.RISK,
            extractor="entity_extractor",
            version="1.0",
            source=Source(file="page.html", locator="html:title"),
        )
        summary = reason_summary_for_view("view1", [obs])
        assert summary.level == 1
        assert "impersonation" in summary.reasons_by_kind
        reason = summary.reasons_by_kind["impersonation"][0]
        assert reason.signal_id == "G2"
        assert "Gazeta.pl" in reason.summary

    def test_multiple_kinds(self):
        """Multiple risk kinds should increase the level."""
        observations = [
            Observation(
                view_id="view1",
                signal_id="C1",
                namespace="domain.age",
                value=7,
                state=State.PRESENT,
                purpose=Purpose.RISK,
                extractor="ext",
                version="1.0",
            ),
            Observation(
                view_id="view1",
                signal_id="G2",
                namespace="entity.impersonation",
                value="Gazeta.pl",
                state=State.PRESENT,
                purpose=Purpose.RISK,
                extractor="ext",
                version="1.0",
            ),
            Observation(
                view_id="view1",
                signal_id="E13",
                namespace="evasion.visual_filter",
                value=None,
                state=State.PRESENT,
                purpose=Purpose.RISK,
                extractor="ext",
                version="1.0",
            ),
        ]
        summary = reason_summary_for_view("view1", observations)
        assert summary.level == 3
        assert len(summary.reasons_by_kind) == 3
        assert "domain" in summary.reasons_by_kind
        assert "impersonation" in summary.reasons_by_kind
        assert "evasion" in summary.reasons_by_kind

    def test_multiple_reasons_same_kind(self):
        """Multiple RISK observations of the same kind should not increase level."""
        observations = [
            Observation(
                view_id="view1",
                signal_id="C1",
                namespace="domain.age",
                value=7,
                state=State.PRESENT,
                purpose=Purpose.RISK,
                extractor="ext",
                version="1.0",
            ),
            Observation(
                view_id="view1",
                signal_id="C4",
                namespace="domain.tld_risk",
                value=".pro",
                state=State.PRESENT,
                purpose=Purpose.RISK,
                extractor="ext",
                version="1.0",
            ),
        ]
        summary = reason_summary_for_view("view1", observations)
        assert summary.level == 1  # Still one kind (domain)
        assert len(summary.reasons_by_kind["domain"]) == 2

    def test_both_purpose_included(self):
        """BOTH purpose observations should be included."""
        obs = Observation(
            view_id="view1",
            signal_id="G2",
            namespace="entity.impersonation",
            value="Gazeta.pl",
            state=State.PRESENT,
            purpose=Purpose.BOTH,
            extractor="ext",
            version="1.0",
        )
        summary = reason_summary_for_view("view1", [obs])
        assert summary.level == 1

    def test_not_observable_ignored(self):
        """NOT_OBSERVABLE observations should be ignored."""
        obs = Observation(
            view_id="view1",
            signal_id="C1",
            namespace="domain.age",
            value=None,
            state=State.NOT_OBSERVABLE,
            purpose=Purpose.RISK,
            extractor="ext",
            version="1.0",
        )
        summary = reason_summary_for_view("view1", [obs])
        assert summary.level == 0

    def test_absent_elements_category(self):
        """Element.absent observations should create absent_elements reasons."""
        obs = Observation(
            view_id="view1",
            signal_id="K5",
            namespace="element.absent",
            value="privacy_policy_link",
            state=State.PRESENT,
            purpose=Purpose.RISK,
            extractor="expectations",
            version="1.0",
        )
        summary = reason_summary_for_view("view1", [obs])
        assert summary.level == 1
        assert "absent_elements" in summary.reasons_by_kind
        reason = summary.reasons_by_kind["absent_elements"][0]
        assert reason.signal_id == "K"

    def test_persuasion_patterns(self):
        """Pattern.* observations should create persuasion reasons."""
        observations = [
            Observation(
                view_id="view1",
                signal_id="H1",
                namespace="pattern.celebrity",
                value=None,
                state=State.PRESENT,
                purpose=Purpose.BOTH,
                extractor="techniques",
                version="1.0",
            ),
            Observation(
                view_id="view1",
                signal_id="H2",
                namespace="pattern.fake_news",
                value=None,
                state=State.PRESENT,
                purpose=Purpose.BOTH,
                extractor="techniques",
                version="1.0",
            ),
        ]
        summary = reason_summary_for_view("view1", observations)
        assert summary.level == 1
        assert "persuasion" in summary.reasons_by_kind
        assert len(summary.reasons_by_kind["persuasion"]) == 2

    def test_cloaking_evasion(self):
        """Cloaking observations should create evasion reasons."""
        obs = Observation(
            view_id="61:article",
            signal_id="J1",
            namespace="evasion.cloaking",
            value="article vs chat",
            state=State.PRESENT,
            purpose=Purpose.BOTH,
            extractor="cloaking_detector",
            version="1.0",
        )
        summary = reason_summary_for_view("61:article", [obs])
        assert summary.level == 1
        assert "evasion" in summary.reasons_by_kind
        reason = summary.reasons_by_kind["evasion"][0]
        assert "article vs chat" in reason.summary

    def test_all_five_kinds(self):
        """Test that all 5 suspicious kinds can be present."""
        observations = [
            Observation(
                view_id="view1",
                signal_id="C1",
                namespace="domain.age",
                value=7,
                state=State.PRESENT,
                purpose=Purpose.RISK,
                extractor="ext",
                version="1.0",
            ),
            Observation(
                view_id="view1",
                signal_id="G2",
                namespace="entity.impersonation",
                value="Gazeta.pl",
                state=State.PRESENT,
                purpose=Purpose.RISK,
                extractor="ext",
                version="1.0",
            ),
            Observation(
                view_id="view1",
                signal_id="K5",
                namespace="element.absent",
                value="privacy_policy",
                state=State.PRESENT,
                purpose=Purpose.RISK,
                extractor="ext",
                version="1.0",
            ),
            Observation(
                view_id="view1",
                signal_id="E13",
                namespace="evasion.visual_filter",
                value=None,
                state=State.PRESENT,
                purpose=Purpose.RISK,
                extractor="ext",
                version="1.0",
            ),
            Observation(
                view_id="view1",
                signal_id="H1",
                namespace="pattern.celebrity",
                value=None,
                state=State.PRESENT,
                purpose=Purpose.BOTH,
                extractor="ext",
                version="1.0",
            ),
        ]
        summary = reason_summary_for_view("view1", observations)
        assert summary.level == 5
        assert len(summary.reasons_by_kind) == 5
        expected_kinds = {"domain", "impersonation", "absent_elements", "evasion", "persuasion"}
        assert set(summary.reasons_by_kind.keys()) == expected_kinds


class TestReasonFormatting:
    """Test that reason summaries have readable human-facing format."""

    def test_domain_age_formatting(self):
        obs = Observation(
            view_id="view1",
            signal_id="C1",
            namespace="domain.age",
            value=10,
            state=State.PRESENT,
            purpose=Purpose.RISK,
            extractor="ext",
            version="1.0",
        )
        summary = reason_summary_for_view("view1", [obs])
        reason = summary.reasons_by_kind["domain"][0]
        assert reason.summary == "domain registered 10 days ago"

    def test_impersonation_formatting(self):
        obs = Observation(
            view_id="view1",
            signal_id="G2",
            namespace="entity.impersonation",
            value="TVN24",
            state=State.PRESENT,
            purpose=Purpose.RISK,
            extractor="ext",
            version="1.0",
        )
        summary = reason_summary_for_view("view1", [obs])
        reason = summary.reasons_by_kind["impersonation"][0]
        assert "TVN24" in reason.summary

    def test_evasion_filter_summary(self):
        obs = Observation(
            view_id="view1",
            signal_id="E13",
            namespace="evasion.visual_filter",
            value=None,
            state=State.PRESENT,
            purpose=Purpose.BOTH,
            extractor="ext",
            version="1.0",
        )
        summary = reason_summary_for_view("view1", [obs])
        reason = summary.reasons_by_kind["evasion"][0]
        assert "visual-hash evasion" in reason.summary or "filter" in reason.summary

    def test_pattern_celebrity_summary(self):
        obs = Observation(
            view_id="view1",
            signal_id="H1",
            namespace="pattern.celebrity",
            value=None,
            state=State.PRESENT,
            purpose=Purpose.BOTH,
            extractor="ext",
            version="1.0",
        )
        summary = reason_summary_for_view("view1", [obs])
        reason = summary.reasons_by_kind["persuasion"][0]
        assert "celebrity" in reason.summary.lower()
