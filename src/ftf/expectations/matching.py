"""Matching: check a view against a profile, element by element.

This module implements Step 4 from architecture §6:
- Receive the profile and the view's visible text.
- For every element, return present, absent, or not_observable.
- Provide evidence: quoted span for present, places looked for absent.

The model makes the judgements; code verifies them.
"""

from __future__ import annotations

from typing import Optional

from ..contracts import State, Tier, Purpose
from ..models import ModelResult, read_structured
from ..config import Settings
from .library import ElementChecked, KindProfile
from .verification import verify_profile_checks, ElementCheckRecord


def check_system_prompt() -> str:
    """System prompt for element checking."""
    return """You are a fraud analyst checking a page against a compliance profile. For each 
expected element in the profile, determine if it is present, absent, or not observable in the 
page text.

Respond with ONLY a JSON object. Do not explain or discuss.

For each element, provide:
- element_name: the name from the profile
- state: "present", "absent", or "not_observable"
- quote: if state is "present", a short span from the page text that shows this element
        if state is "absent", null
        if state is "not_observable", null
- looked_in: if state is "absent", a brief description of where you looked (e.g., "footer, form, header")

A "not_observable" judgement means the element would usually sit below the visible area
(for a screenshot-only view), or the page format makes it unobservable."""


def check_prompt(profile: KindProfile, page_text: str, page_kind_observed: str) -> str:
    """Generate a prompt for the model to check a view against a profile.

    Args:
        profile: The kind profile with expected elements.
        page_text: The page visible text.
        page_kind_observed: What kind the analyst identified this page as.

    Returns:
        The prompt text.
    """
    # Build a list of expected elements from the profile
    elements_desc = []
    for ep in profile.elements:
        if ep.promoted:  # Only check promoted elements
            elem = ep.element
            elements_desc.append(f"- {elem.name}: {elem.description} ({elem.basis.value})")

    elements_list = "\n".join(elements_desc)

    return f"""Check this page against the compliance profile for "{profile.kind.name}" pages.

Observed page kind: {page_kind_observed}
Page text (first 3000 chars):
---
{page_text[:3000]}
---

Expected elements for this page kind:
{elements_list}

For each expected element, determine if it is present, absent, or not_observable in the page.

Respond with JSON: {{"checks": [{{"element_name": "...", "state": "...", "quote": null}}]}}"""


class ElementJudgement:
    """One element's judgement for a view."""

    def __init__(
        self,
        element_name: str,
        state: ElementChecked,
        quote: Optional[str] = None,
        looked_in: str = "",
    ):
        """Initialize a judgement.

        Args:
            element_name: Name of the expected element.
            state: The model's judgement (present/absent/not_observable).
            quote: Quoted span if state is PRESENT.
            looked_in: Where the model looked if state is ABSENT.
        """
        self.element_name = element_name
        self.state = state
        self.quote = quote
        self.looked_in = looked_in

    def to_dict(self) -> dict:
        """Serialize to dict."""
        return {
            "element_name": self.element_name,
            "state": self.state.value,
            "quote": self.quote,
            "looked_in": self.looked_in,
        }


class ViewElementVector:
    """Element presence vector for one view: one entry per profile element."""

    def __init__(self, view_id: str, page_kind: str, profile: KindProfile):
        """Initialize vector.

        Args:
            view_id: The view ID.
            page_kind: The page kind this profile applies to.
            profile: The kind profile.
        """
        self.view_id = view_id
        self.page_kind = page_kind
        self.profile = profile
        self.judgements: list[ElementJudgement] = []

    def add_judgement(
        self,
        element_name: str,
        state: ElementChecked,
        quote: Optional[str] = None,
        looked_in: str = "",
    ) -> None:
        """Add a judgement for one element."""
        judgement = ElementJudgement(element_name, state, quote, looked_in)
        self.judgements.append(judgement)

    def present(self) -> list[ElementJudgement]:
        """Elements judged PRESENT."""
        return [j for j in self.judgements if j.state == ElementChecked.PRESENT]

    def absent(self) -> list[ElementJudgement]:
        """Elements judged ABSENT."""
        return [j for j in self.judgements if j.state == ElementChecked.ABSENT]

    def not_observable(self) -> list[ElementJudgement]:
        """Elements judged NOT_OBSERVABLE."""
        return [j for j in self.judgements if j.state == ElementChecked.NOT_OBSERVABLE]

    def to_dict(self) -> dict:
        """Serialize to dict."""
        return {
            "view_id": self.view_id,
            "page_kind": self.page_kind,
            "total_elements": len(self.judgements),
            "present": len(self.present()),
            "absent": len(self.absent()),
            "not_observable": len(self.not_observable()),
            "judgements": [j.to_dict() for j in self.judgements],
        }

    def as_fingerprint_payload(self) -> list[dict]:
        """Convert to fingerprint payload (set of present elements).

        Only PRESENT and ABSENT elements are recorded in the fingerprint;
        NOT_OBSERVABLE elements are omitted since they don't tell us anything.

        Each item is a dict with keys: element_name, state.
        """
        payload = []
        for j in self.judgements:
            if j.state in (ElementChecked.PRESENT, ElementChecked.ABSENT):
                payload.append({"element": j.element_name, "state": j.state.value})
        return payload


async def check_view_against_profile(
    view_id: str,
    page_text: str,
    page_kind_observed: str,
    profile: KindProfile,
    settings: Settings,
) -> tuple[ViewElementVector, list[ElementCheckRecord]]:
    """Check a view against a kind profile.

    This runs the model's judgement and then verifies it against the page text.

    Args:
        view_id: The view ID.
        page_text: The visible page text.
        page_kind_observed: What kind of page the analyst identified it as.
        profile: The kind profile to check against.
        settings: Configuration with model API details.

    Returns:
        (vector, verified_checks) where vector is the full element vector and
        verified_checks is the list of elements that passed verification.

    Raises:
        RuntimeError: If the model fails to respond or responds with invalid JSON.
    """
    # TODO: Integrate with real model API when gateway is available
    # For now, return placeholder structures

    vector = ViewElementVector(view_id, page_kind_observed, profile)

    # TODO: Call model with check_system_prompt() and check_prompt()
    # Parse response as JSON with "checks" list
    # For each check, call verify_element() to verify against page_text
    # Only add checks that passed verification to the vector

    verified_checks = []
    return vector, verified_checks


def judge_patterns_for_view(
    view_id: str,
    page_text: str,
    page_kind: str,
    profile: KindProfile,
) -> ViewElementVector:
    """Fast path: judge view against profile without calling model (for testing/validation).

    Args:
        view_id: The view ID.
        page_text: The visible page text.
        page_kind: The page kind.
        profile: The kind profile.

    Returns:
        Element vector with judgements populated from profile defaults.
    """
    vector = ViewElementVector(view_id, page_kind, profile)

    # For testing: assume ABSENT for all elements (conservative)
    for ep in profile.elements:
        if ep.promoted:
            vector.add_judgement(ep.element.name, ElementChecked.ABSENT, looked_in="default")

    return vector
