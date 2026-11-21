"""Induction: generate and cache page-kind profiles.

This module implements Step 1 and 2 from architecture §6:
- Step 1: Model names the page kind in its own words.
- Step 2: Model generates what legitimate pages of that kind should contain.

Profiles are cached and versioned. The model's output is what code stores and grounds
against real pages in the next step.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from ..models import ModelResult, read_structured
from ..config import Settings
from .library import (
    ElementBasis,
    ElementProfile,
    ExpectedElement,
    KindLibraryVersion,
    KindLibraryStore,
    KindProfile,
    PageKind,
    TypicalWording,
)


def generate_kind_name_prompt(page_text: str, page_kind_hint: Optional[str] = None) -> str:
    """Generate a prompt for the model to name the page kind.

    Args:
        page_text: The page visible text.
        page_kind_hint: Optional hint about the kind (e.g., 'looks like a supplement listing').

    Returns:
        The prompt text.
    """
    return f"""You are a fraud analyst classifying web pages. Read this page and name its kind.

Page text:
---
{page_text[:2000]}
---

Respond with a short name in English (a few words) for what kind of page this is.
Examples: "investment offer", "online casino listing", "supplement sales page", "news article".

If provided, consider this hint: {page_kind_hint or '(no hint)'}

Respond with ONLY the page kind name, in lowercase, no explanation."""


def induction_system_prompt() -> str:
    """System prompt for profile generation."""
    return """You are a legal/regulatory compliance expert and fraud analyst. You understand what 
legitimate pages in various verticals must contain under Polish law, regulatory practice, and 
customary usage. You will generate profiles of expected elements for page kinds, grounded in 
actual compliance requirements and business practice.

For each element in the profile:
- Explain why it is expected (legal requirement, regulator practice, or customary).
- Provide 2-3 typical Polish wordings that might appear for this element.
- Specify where it usually appears on the page (header, footer, form, section, etc.).

Be specific, factual, and grounded in real Polish regulatory and business practices."""


def induction_prompt(page_kind: str, vertical: Optional[str] = None) -> str:
    """Generate a prompt for the model to generate a profile.

    Args:
        page_kind: The page kind name (e.g., 'investment offer').
        vertical: Optional vertical/category (e.g., 'Investment / trading').

    Returns:
        The prompt text.
    """
    vertical_hint = f" (vertical: {vertical})" if vertical else ""

    return f"""Generate a compliance profile for pages of this kind: "{page_kind}"{vertical_hint}

A profile lists expected elements that a legitimate page of this kind should contain.

For each element, provide:
1. name: short identifier (snake_case)
2. description: what the element is (English)
3. basis: "legal_requirement", "regulator_practice", or "customary"
4. typical_wordings: 2-3 example Polish texts that might appear for this element
5. typical_location: where on the page (e.g., "footer", "form", "section", "header")
6. notes: any notes on how to identify it

Output as JSON with key "elements" containing a list of element objects.

Focus on observable, verifiable elements. For pages with forms, include privacy/consent elements.
For regulated verticals (trading, gambling, pharmaceuticals), include required disclosures."""


async def generate_profile(
    page_kind: str,
    settings: Settings,
    cache_dir: Path,
    vertical: Optional[str] = None,
) -> KindProfile:
    """Generate a profile for a page kind using the model.

    Args:
        page_kind: The page kind name (e.g., 'investment offer').
        settings: Configuration with model API details.
        cache_dir: Directory for caching model responses.
        vertical: Optional vertical/category.

    Returns:
        A new KindProfile with generated elements.

    This calls the model once per page kind and caches the result.
    """
    # TODO: Integrate with real model API when gateway is available
    # For now, return a placeholder profile structure

    # Create a stable kind_id from the page_kind
    kind_id = _generate_kind_id(page_kind, vertical)

    kind = PageKind(
        kind_id=kind_id,
        name=page_kind,
        description=f"A page of kind: {page_kind}",
        vertical=vertical or "",
    )

    # TODO: Call model with induction_system_prompt() and induction_prompt()
    # Result should be JSON with "elements" list; parse and create ExpectedElement objects
    # Cache the response with the kind_id as key

    # Placeholder: empty profile
    return KindProfile(
        kind=kind,
        elements=[],
        generated_from="model_knowledge",
        created_at=datetime.utcnow(),
    )


def _generate_kind_id(page_kind: str, vertical: Optional[str] = None) -> str:
    """Generate a stable kind_id from the page kind name.

    Args:
        page_kind: The page kind name (e.g., 'investment offer').
        vertical: Optional vertical.

    Returns:
        Stable ID like 'kind.1_investment_offer'.

    The ID is deterministic based on the kind name, so the same kind
    always gets the same ID.
    """
    # Normalize to snake_case
    slug = page_kind.lower().replace(" ", "_").replace("/", "_")
    slug = "".join(c for c in slug if c.isalnum() or c == "_")

    # For now, use index 1; in a real system, this would be auto-incremented
    # or based on a registry of known kinds
    return f"kind.1_{slug}"


def cache_profile(
    profile: KindProfile, store: KindLibraryStore, version_id: str, input_hash: Optional[str] = None
) -> KindLibraryVersion:
    """Cache a generated profile by creating a new library version.

    Args:
        profile: The profile to cache.
        store: The library store to save to.
        version_id: Semantic version for this library (e.g., '1.0.0').
        input_hash: Optional hash of input cards for reproducibility.

    Returns:
        The new KindLibraryVersion that was saved.
    """
    library = KindLibraryVersion(
        version_id=version_id,
        profiles=[profile],
        created_at=datetime.utcnow(),
        input_hash=input_hash,
    )

    store.save_version(library)
    return library


def ground_profile_on_genuine_pages(
    profile: KindProfile, genuine_pages: list[tuple[str, str]]
) -> KindProfile:
    """Ground a profile by comparing it against genuine pages of the same kind.

    Step 3 from architecture §6: Check generated profile against real pages to adjust
    element presence expectations.

    Args:
        profile: The profile to ground.
        genuine_pages: List of (page_kind, page_text) tuples from real pages.

    Returns:
        Updated profile with false-alarm rates and promoted flags set.

    Elements that genuine pages consistently lack are downgraded or removed.
    Elements they consistently carry are marked as promoted.
    """
    # TODO: Implement grounding logic
    # For each element in profile:
    #   - For each genuine page, check if the element is present (using verification)
    #   - Track seen_in_genuine count
    #   - Calculate false_alarm_rate = (1 - seen_in_genuine / total)
    #   - If false_alarm_rate is too high, set promoted=False
    #   - If element missing from profile but all genuine pages have it, add it

    # For now, return profile as-is with grounding metadata
    profile.grounded_on_genuine = len(genuine_pages)
    profile.grounded_at = datetime.utcnow()
    return profile
