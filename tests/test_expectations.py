"""Tests for expected-elements library and verification."""

from __future__ import annotations

import pytest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from src.ftf.expectations.library import (
    ElementBasis,
    ElementChecked,
    ElementProfile,
    ExpectedElement,
    KindLibraryStore,
    KindLibraryVersion,
    KindProfile,
    PageKind,
    TypicalWording,
)
from src.ftf.expectations.verification import (
    ElementCheckRecord,
    ProfileCheckResult,
    VerificationResult,
    verify_element,
    verify_profile_checks,
    verify_quote_in_text,
    search_for_element_in_text,
)
from src.ftf.expectations.matching import (
    ViewElementVector,
    judge_patterns_for_view,
    ElementJudgement,
)


class TestElementDataModels:
    """Test creation and serialization of element data structures."""

    def test_typical_wording_creation(self):
        """Test creating a typical wording."""
        w = TypicalWording(text="Oświadczenie o ryzyku", location_hint="at the top of the page")
        assert w.text == "Oświadczenie o ryzyku"
        assert w.location_hint == "at the top of the page"

    def test_expected_element_creation(self):
        """Test creating an expected element."""
        elem = ExpectedElement(
            name="risk_warning",
            description="Prominent warning about investment risks",
            basis=ElementBasis.LEGAL_REQUIREMENT,
            typical_wordings=[
                TypicalWording(text="Oświadczenie o ryzyku", location_hint="header"),
            ],
            typical_location="header",
        )
        assert elem.name == "risk_warning"
        assert elem.basis == ElementBasis.LEGAL_REQUIREMENT
        assert len(elem.typical_wordings) == 1

    def test_page_kind_creation(self):
        """Test creating a page kind."""
        kind = PageKind(
            kind_id="kind.1_investment_offer",
            name="investment offer",
            description="Solicits investment in a scheme",
            vertical="Investment / trading",
        )
        assert kind.kind_id == "kind.1_investment_offer"
        assert kind.vertical == "Investment / trading"

    def test_element_profile_with_grounding(self):
        """Test element profile with grounding metadata."""
        elem = ExpectedElement(name="privacy_policy", description="Privacy policy link")
        ep = ElementProfile(
            element=elem,
            seen_in_genuine=5,
            total_genuine_checked=5,
            false_alarm_rate=0.0,
            promoted=True,
        )
        assert ep.seen_in_genuine == 5
        assert ep.false_alarm_rate == 0.0

    def test_kind_profile_creation(self):
        """Test creating a kind profile."""
        kind = PageKind(kind_id="kind.1_test", name="test page")
        elem = ExpectedElement(name="element1", description="Test element")
        ep = ElementProfile(element=elem, promoted=True)

        profile = KindProfile(kind=kind, elements=[ep])
        assert profile.kind.name == "test page"
        assert len(profile.elements) == 1

    def test_kind_profile_serialization(self):
        """Test serialization of a kind profile to dict."""
        kind = PageKind(kind_id="kind.1_test", name="test")
        elem = ExpectedElement(name="e1", description="Element 1")
        ep = ElementProfile(element=elem)
        profile = KindProfile(kind=kind, elements=[ep])

        # Should be serializable (Pydantic handles this)
        data = profile.model_dump()
        assert data["kind"]["kind_id"] == "kind.1_test"
        assert len(data["elements"]) == 1


class TestKindLibraryStore:
    """Test versioned library storage."""

    def test_store_initialization(self):
        """Test creating a library store."""
        with TemporaryDirectory() as tmpdir:
            store = KindLibraryStore(Path(tmpdir))
            assert store.store_dir.exists()

    def test_save_and_load_version(self):
        """Test saving and loading a library version."""
        with TemporaryDirectory() as tmpdir:
            store = KindLibraryStore(Path(tmpdir))

            # Create a simple version
            kind = PageKind(kind_id="kind.1_test", name="test")
            elem = ExpectedElement(name="e1", description="Element 1")
            ep = ElementProfile(element=elem)
            profile = KindProfile(kind=kind, elements=[ep])

            lib = KindLibraryVersion(version_id="1.0.0", profiles=[profile])
            path = store.save_version(lib)

            # Load it back
            loaded = store.load_version("1.0.0")
            assert loaded.version_id == "1.0.0"
            assert len(loaded.profiles) == 1
            assert loaded.profiles[0].kind.kind_id == "kind.1_test"

    def test_save_duplicate_version_fails(self):
        """Test that saving the same version twice fails."""
        with TemporaryDirectory() as tmpdir:
            store = KindLibraryStore(Path(tmpdir))

            kind = PageKind(kind_id="kind.1_test", name="test")
            profile = KindProfile(kind=kind)
            lib = KindLibraryVersion(version_id="1.0.0", profiles=[profile])

            store.save_version(lib)
            with pytest.raises(ValueError, match="already exists"):
                store.save_version(lib)

    def test_latest_version(self):
        """Test retrieving the latest version."""
        with TemporaryDirectory() as tmpdir:
            store = KindLibraryStore(Path(tmpdir))

            kind = PageKind(kind_id="kind.1_test", name="test")
            profile = KindProfile(kind=kind)

            # Create versions 1.0.0 and 2.0.0
            lib1 = KindLibraryVersion(version_id="1.0.0", profiles=[profile])
            lib2 = KindLibraryVersion(version_id="2.0.0", profiles=[profile])

            store.save_version(lib1)
            store.save_version(lib2)

            latest = store.latest_version()
            assert latest.version_id == "2.0.0"

    def test_get_kind_profile_by_name(self):
        """Test retrieving a profile by kind name."""
        with TemporaryDirectory() as tmpdir:
            store = KindLibraryStore(Path(tmpdir))

            kind = PageKind(kind_id="kind.1_investment", name="investment offer")
            profile = KindProfile(kind=kind)
            lib = KindLibraryVersion(version_id="1.0.0", profiles=[profile])
            store.save_version(lib)

            found = store.get_kind_profile("investment offer")
            assert found is not None
            assert found.kind.kind_id == "kind.1_investment"

    def test_find_profile_by_id(self):
        """Test retrieving a profile by kind_id."""
        with TemporaryDirectory() as tmpdir:
            store = KindLibraryStore(Path(tmpdir))

            kind = PageKind(kind_id="kind.1_investment", name="investment offer")
            profile = KindProfile(kind=kind)
            lib = KindLibraryVersion(version_id="1.0.0", profiles=[profile])
            store.save_version(lib)

            found = store.find_profile_by_id("kind.1_investment")
            assert found is not None
            assert found.kind.name == "investment offer"


class TestVerification:
    """Test element verification against page text."""

    def test_verify_quote_in_text_exact_match(self):
        """Test verifying an exact quote in text."""
        page_text = "This is the legal entity name and registered address"
        quote = "legal entity name and registered address"
        assert verify_quote_in_text(quote, page_text)

    def test_verify_quote_in_text_missing(self):
        """Test that a missing quote fails verification."""
        page_text = "This is some page text"
        quote = "not found in page"
        assert not verify_quote_in_text(quote, page_text)

    def test_verify_quote_with_whitespace_normalization(self):
        """Test that quotes with extra whitespace are still found."""
        page_text = "This is   the   text"
        quote = "This is the text"
        # After normalization, should match
        assert verify_quote_in_text(quote, page_text)

    def test_search_for_element_wordings_found(self):
        """Test searching for typical wordings."""
        elem = ExpectedElement(
            name="privacy_policy",
            description="Privacy policy",
            basis=ElementBasis.CUSTOMARY,
            typical_wordings=[
                TypicalWording(text="Polityka prywatności"),
                TypicalWording(text="Privacy Policy"),
            ],
        )
        page_text = "Footer: Polityka prywatności | Terms of Service"
        found, matched = search_for_element_in_text(elem, page_text)
        assert found
        assert matched == "Polityka prywatności"

    def test_search_for_element_wordings_not_found(self):
        """Test that element wordings not in text are reported."""
        elem = ExpectedElement(
            name="privacy_policy",
            description="Privacy policy",
            basis=ElementBasis.CUSTOMARY,
            typical_wordings=[TypicalWording(text="Polityka prywatności")],
        )
        page_text = "This page has no privacy statement"
        found, matched = search_for_element_in_text(elem, page_text)
        assert not found
        assert matched is None

    def test_verify_element_present_valid(self):
        """Test verifying a present element with valid quote."""
        elem = ExpectedElement(name="test", description="Test element")
        page_text = "This page contains the required notice"
        quote = "required notice"

        result = verify_element(elem, ElementChecked.PRESENT, quote, page_text)
        assert result.verified
        assert result.quote == quote

    def test_verify_element_present_missing_quote(self):
        """Test that present without quote fails."""
        elem = ExpectedElement(name="test", description="Test")
        result = verify_element(elem, ElementChecked.PRESENT, None, "text")
        assert not result.verified
        assert "no quote" in result.reason

    def test_verify_element_present_quote_not_found(self):
        """Test that present with unfound quote fails."""
        elem = ExpectedElement(name="test", description="Test")
        page_text = "This is the page"
        quote = "not in page"
        result = verify_element(elem, ElementChecked.PRESENT, quote, page_text)
        assert not result.verified
        assert "not found" in result.reason

    def test_verify_element_absent_confirmed(self):
        """Test verifying an absent element that is truly absent."""
        elem = ExpectedElement(
            name="privacy",
            description="Privacy policy",
            basis=ElementBasis.CUSTOMARY,
            typical_wordings=[TypicalWording(text="Polityka prywatności")],
        )
        page_text = "No privacy statement on this page"
        result = verify_element(elem, ElementChecked.ABSENT, None, page_text)
        assert result.verified
        assert result.reason == "no typical wordings found in text"

    def test_verify_element_absent_found_in_text(self):
        """Test that absent fails when typical wordings are found."""
        elem = ExpectedElement(
            name="privacy",
            description="Privacy policy",
            basis=ElementBasis.CUSTOMARY,
            typical_wordings=[TypicalWording(text="Polityka prywatności")],
        )
        page_text = "Footer: Polityka prywatności"
        result = verify_element(elem, ElementChecked.ABSENT, None, page_text)
        assert not result.verified
        assert "typical wording found" in result.reason

    def test_verify_element_not_observable(self):
        """Test that not_observable always passes."""
        elem = ExpectedElement(name="test", description="Test")
        result = verify_element(elem, ElementChecked.NOT_OBSERVABLE, None, "text")
        assert result.verified


class TestProfileCheckResult:
    """Test aggregate check results."""

    def test_profile_check_result_counts(self):
        """Test that check result counts are accurate."""
        result = ProfileCheckResult("test_kind", "page text")

        elem1 = ExpectedElement(name="e1", description="Element 1")
        elem2 = ExpectedElement(name="e2", description="Element 2")

        result.add_check(elem1, ElementChecked.PRESENT, quote="text", verified=True)
        result.add_check(elem2, ElementChecked.ABSENT, verified=True)

        assert result.present_count() == 1
        assert result.absent_count() == 1
        assert len(result.passed_elements()) == 2


class TestElementVector:
    """Test element presence vectors for views."""

    def test_view_element_vector_creation(self):
        """Test creating a view element vector."""
        kind = PageKind(kind_id="kind.1_test", name="test")
        profile = KindProfile(kind=kind)
        vector = ViewElementVector("view.1", "test", profile)
        assert vector.view_id == "view.1"
        assert len(vector.judgements) == 0

    def test_view_element_vector_counts(self):
        """Test counting judgements by state."""
        kind = PageKind(kind_id="kind.1_test", name="test")
        profile = KindProfile(kind=kind)
        vector = ViewElementVector("view.1", "test", profile)

        vector.add_judgement("e1", ElementChecked.PRESENT, quote="text")
        vector.add_judgement("e2", ElementChecked.ABSENT)
        vector.add_judgement("e3", ElementChecked.NOT_OBSERVABLE)

        assert len(vector.present()) == 1
        assert len(vector.absent()) == 1
        assert len(vector.not_observable()) == 1

    def test_view_element_vector_fingerprint_payload(self):
        """Test fingerprint payload excludes NOT_OBSERVABLE."""
        kind = PageKind(kind_id="kind.1_test", name="test")
        profile = KindProfile(kind=kind)
        vector = ViewElementVector("view.1", "test", profile)

        vector.add_judgement("e1", ElementChecked.PRESENT)
        vector.add_judgement("e2", ElementChecked.ABSENT)
        vector.add_judgement("e3", ElementChecked.NOT_OBSERVABLE)

        payload = vector.as_fingerprint_payload()
        # Should have e1 and e2 but not e3
        assert len(payload) == 2
        states = {item["state"] for item in payload}
        assert "present" in states
        assert "absent" in states


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
