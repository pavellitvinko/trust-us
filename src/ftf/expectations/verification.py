"""Verification: check that quotes occur in page text; re-search for absent elements.

This module implements code-side verification of element presence/absence judgements:
1. Every "present" claim must have a quote that actually occurs in the visible page text.
2. Every "absent" claim is re-checked by searching for the element's typical wordings.

This ensures the model's judgements are grounded in the actual page content.
"""

from __future__ import annotations

import re
from typing import Optional

from .library import ElementChecked, ElementProfile, ExpectedElement, KindProfile


class VerificationResult:
    """Result of verifying one element against page text."""

    def __init__(
        self,
        element: ExpectedElement,
        claimed_state: ElementChecked,
        quote: Optional[str] = None,
        verified: bool = True,
        reason: str = "",
    ):
        """Initialize verification result.

        Args:
            element: The element being verified.
            claimed_state: What the model claimed (present/absent/not_observable).
            quote: The quoted span (if claimed_state is PRESENT).
            verified: Whether verification passed.
            reason: Why verification failed (if not verified).
        """
        self.element = element
        self.claimed_state = claimed_state
        self.quote = quote
        self.verified = verified
        self.reason = reason

    def __repr__(self) -> str:
        status = "✓" if self.verified else "✗"
        reason_str = f" ({self.reason})" if self.reason else ""
        quote_str = f': "{self.quote}"' if self.quote else ""
        return f"{status} {self.element.name} {self.claimed_state.value}{quote_str}{reason_str}"


def verify_quote_in_text(quote: str, page_text: str) -> bool:
    """Verify that a quote occurs in the page text.

    Args:
        quote: The claimed quoted span.
        page_text: The full page visible text.

    Returns:
        True if the quote is found in the page text.
    """
    # Exact match first
    if quote in page_text:
        return True

    # Normalize whitespace and try again (quotes might have been re-spaced)
    normalized_quote = " ".join(quote.split())
    normalized_text = " ".join(page_text.split())
    return normalized_quote in normalized_text


def search_for_element_in_text(element: ExpectedElement, page_text: str) -> tuple[bool, Optional[str]]:
    """Search for typical wordings of an element in page text.

    Args:
        element: The element with typical wordings.
        page_text: The page visible text.

    Returns:
        (found, matched_text) where found=True if any typical wording was found,
        and matched_text is the first matching wording (or None).
    """
    if not element.typical_wordings:
        return False, None

    # Try each typical wording as a regex pattern (case-insensitive)
    for wording in element.typical_wordings:
        try:
            # Escape special regex chars in the text
            pattern = re.escape(wording.text)
            if re.search(pattern, page_text, re.IGNORECASE):
                return True, wording.text
        except re.error:
            # Pattern error; skip this wording
            continue

    return False, None


def verify_element(
    element: ExpectedElement,
    claimed_state: ElementChecked,
    quote: Optional[str],
    page_text: str,
) -> VerificationResult:
    """Verify one element's claimed state against page text.

    Args:
        element: The expected element.
        claimed_state: What the model claimed (present/absent/not_observable).
        quote: The quote if claimed_state is PRESENT.
        page_text: The full page visible text.

    Returns:
        VerificationResult with verified=True if the claim is substantiated.

    If claimed_state is PRESENT:
        - Verification passes only if the quote is found in page text.
        - If quote is missing or not found, verification fails.

    If claimed_state is ABSENT:
        - Verification passes initially (no quote to verify).
        - But we re-search for typical wordings; if found, the result is flagged.

    If claimed_state is NOT_OBSERVABLE:
        - Verification passes (by definition, we can't check visibility).
    """
    if claimed_state == ElementChecked.PRESENT:
        if not quote:
            return VerificationResult(
                element,
                claimed_state,
                quote=None,
                verified=False,
                reason="present claimed but no quote provided",
            )

        if not verify_quote_in_text(quote, page_text):
            return VerificationResult(
                element,
                claimed_state,
                quote=quote,
                verified=False,
                reason=f"quote not found in page text: {quote[:50]}...",
            )

        return VerificationResult(
            element, claimed_state, quote=quote, verified=True, reason="quote found in text"
        )

    elif claimed_state == ElementChecked.ABSENT:
        # Re-check by searching for typical wordings
        found, matched = search_for_element_in_text(element, page_text)
        if found:
            # Element was claimed absent but typical wording found; flag for re-judgement
            return VerificationResult(
                element,
                claimed_state,
                quote=matched,
                verified=False,
                reason=f"absent claimed but typical wording found: '{matched}'",
            )

        return VerificationResult(
            element,
            claimed_state,
            quote=None,
            verified=True,
            reason="no typical wordings found in text",
        )

    elif claimed_state == ElementChecked.NOT_OBSERVABLE:
        # Can't verify visibility from text alone; pass by definition
        return VerificationResult(
            element,
            claimed_state,
            quote=None,
            verified=True,
            reason="not_observable: cannot verify from text",
        )

    else:
        return VerificationResult(
            element,
            claimed_state,
            quote=None,
            verified=False,
            reason=f"unknown state: {claimed_state}",
        )


class ElementCheckRecord:
    """One element's check result for a view."""

    def __init__(
        self,
        element: ExpectedElement,
        state: ElementChecked,
        quote: Optional[str] = None,
        verified: bool = True,
        verification_reason: str = "",
    ):
        self.element = element
        self.state = state
        self.quote = quote
        self.verified = verified
        self.verification_reason = verification_reason

    def to_dict(self) -> dict:
        """Serialize to dict for storage."""
        return {
            "element_name": self.element.name,
            "state": self.state.value,
            "quote": self.quote,
            "verified": self.verified,
            "verification_reason": self.verification_reason,
        }


class ProfileCheckResult:
    """Results of checking a view against a kind profile."""

    def __init__(self, kind_name: str, page_text: str):
        self.kind_name = kind_name
        self.page_text = page_text
        self.checks: list[ElementCheckRecord] = []

    def add_check(
        self,
        element: ExpectedElement,
        state: ElementChecked,
        quote: Optional[str] = None,
        verified: bool = True,
        verification_reason: str = "",
    ) -> None:
        """Add a check result for one element."""
        record = ElementCheckRecord(element, state, quote, verified, verification_reason)
        self.checks.append(record)

    def passed_elements(self) -> list[ElementCheckRecord]:
        """Elements where verification passed."""
        return [c for c in self.checks if c.verified]

    def failed_elements(self) -> list[ElementCheckRecord]:
        """Elements where verification failed."""
        return [c for c in self.checks if not c.verified]

    def present_count(self) -> int:
        """Count of verified PRESENT elements."""
        return sum(1 for c in self.checks if c.state == ElementChecked.PRESENT and c.verified)

    def absent_count(self) -> int:
        """Count of verified ABSENT elements."""
        return sum(1 for c in self.checks if c.state == ElementChecked.ABSENT and c.verified)

    def not_observable_count(self) -> int:
        """Count of verified NOT_OBSERVABLE elements."""
        return sum(1 for c in self.checks if c.state == ElementChecked.NOT_OBSERVABLE and c.verified)

    def to_dict(self) -> dict:
        """Serialize to dict."""
        return {
            "kind_name": self.kind_name,
            "total_checks": len(self.checks),
            "passed": len(self.passed_elements()),
            "failed": len(self.failed_elements()),
            "present": self.present_count(),
            "absent": self.absent_count(),
            "not_observable": self.not_observable_count(),
            "checks": [c.to_dict() for c in self.checks],
        }


def verify_profile_checks(
    profile: KindProfile, page_text: str, checks: list[tuple[str, ElementChecked, Optional[str]]]
) -> ProfileCheckResult:
    """Verify all element checks for a profile against page text.

    Args:
        profile: The kind profile being checked.
        page_text: The visible page text.
        checks: List of (element_name, state, quote) tuples from the model.

    Returns:
        ProfileCheckResult with all verifications completed.
    """
    result = ProfileCheckResult(profile.kind.name, page_text)

    # Build a map of element names to ElementProfile for easy lookup
    element_map = {ep.element.name: ep for ep in profile.elements}

    for element_name, state, quote in checks:
        if element_name not in element_map:
            # Element not in profile; skip
            continue

        ep = element_map[element_name]
        verification = verify_element(ep.element, state, quote, page_text)

        result.add_check(
            ep.element,
            state,
            quote=quote,
            verified=verification.verified,
            verification_reason=verification.reason,
        )

    return result
