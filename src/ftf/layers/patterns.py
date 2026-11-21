"""Pattern fingerprint layer for view comparison (pattern.mo).

Implements a fingerprint layer that represents a view's pattern vector
(presence/absence judgements across the pattern library).
Patterns are playbook-tier; they cannot drive campaign grouping directly.
"""

from __future__ import annotations

import logging
from typing import Any

from ..contracts import Evidence, Fingerprint, FingerprintKind, Observation, View
from ..layers import BaseLayer
from .library import PatternLibraryStore, ViewPatternVector
from .matching import judge_patterns_for_view

log = logging.getLogger(__name__)

PATTERN_LAYER_ID = "pattern.mo"
PATTERN_LAYER_VERSION = "1"


class PatternLayer(BaseLayer):
    """Fingerprinting layer for persuasion patterns.

    Judges each view against the pattern library and stores presence/absence vector.
    Two views with similar pattern vectors may follow the same playbook.
    Patterns alone cannot merge campaigns, but support playbook-tier evidence.
    """

    def __init__(self, library_store: PatternLibraryStore | None = None):
        self.library_store = library_store
        self.layer_id = PATTERN_LAYER_ID

    async def fingerprint(
        self,
        view: View,
        observations: list[Observation],
        store: Any,
    ) -> Fingerprint | None:
        """Generate pattern vector fingerprint for a view.

        Args:
            view: the view being fingerprinted
            observations: extracted facts about the view
            store: data store with technique card information

        Returns:
            A fingerprint containing the pattern vector, or None if patterns unavailable.
        """
        if not self.library_store:
            return None

        # Gather visible text from observations
        view_text = self._extract_view_text(observations)
        if not view_text:
            log.warning(f"no text available for view {view.view_id}")
            return None

        # Judge view against library patterns
        vector = await judge_patterns_for_view(
            view_id=view.view_id,
            view_text=view_text,
            library_store=self.library_store,
        )

        if not vector or not vector.judgements:
            return None

        # Convert to fingerprint payload: list of pattern states
        payload = [
            {
                "pattern_id": j.pattern_id,
                "state": j.state,
                "confidence": j.confidence,
            }
            for j in vector.judgements
        ]

        return Fingerprint(
            view_id=view.view_id,
            layer_id=PATTERN_LAYER_ID,
            kind=FingerprintKind.SET,
            payload=payload,
            version=PATTERN_LAYER_VERSION,
        )

    def compare(self, fp_a: Fingerprint, fp_b: Fingerprint) -> Evidence:
        """Compare pattern vectors from two views.

        Compares which patterns both views show, yield presence/absence.
        Low overlap in pattern signals suggests different persuasion approach (different playbook).
        High overlap suggests same technique set (same playbook).

        Args:
            fp_a: pattern vector from view A
            fp_b: pattern vector from view B

        Returns:
            Evidence of similarity.
        """
        if fp_a.kind != FingerprintKind.SET or fp_b.kind != FingerprintKind.SET:
            return Evidence(
                view_a=fp_a.view_id,
                view_b=fp_b.view_id,
                layer_id=PATTERN_LAYER_ID,
                comparable=False,
                comparator=f"{self.__class__.__name__}.compare",
                version=PATTERN_LAYER_VERSION,
            )

        payload_a = {p["pattern_id"]: p for p in fp_a.payload}
        payload_b = {p["pattern_id"]: p for p in fp_b.payload}

        # Shared pattern states: patterns both views show
        shared_patterns = []
        for pid in set(payload_a.keys()) & set(payload_b.keys()):
            if payload_a[pid]["state"] == payload_b[pid]["state"] == "present":
                shared_patterns.append({
                    "pattern_id": pid,
                    "state": "present",
                    "confidence_a": payload_a[pid]["confidence"],
                    "confidence_b": payload_b[pid]["confidence"],
                })

        # Differing: patterns one shows but not the other
        differing = []
        only_a = set(payload_a.keys()) - set(payload_b.keys())
        for pid in only_a:
            if payload_a[pid]["state"] == "present":
                differing.append({
                    "pattern_id": pid,
                    "in_view": "A",
                })

        only_b = set(payload_b.keys()) - set(payload_a.keys())
        for pid in only_b:
            if payload_b[pid]["state"] == "present":
                differing.append({
                    "pattern_id": pid,
                    "in_view": "B",
                })

        # Score: fraction of shared patterns out of common patterns
        common_count = len(payload_a) + len(payload_b) - len(only_a) - len(only_b)
        if common_count == 0:
            score = 0.0
        else:
            score = len(shared_patterns) / common_count

        return Evidence(
            view_a=fp_a.view_id,
            view_b=fp_b.view_id,
            layer_id=PATTERN_LAYER_ID,
            comparable=True,
            score=score,
            shared=shared_patterns,
            differing=differing,
            comparator=f"{self.__class__.__name__}.compare",
            version=PATTERN_LAYER_VERSION,
        )

    @staticmethod
    def _extract_view_text(observations: list[Observation]) -> str:
        """Extract visible text from observations."""
        # Gather text from various observation types
        text_parts = []

        # TODO: integrate with the actual perception models to get page text
        # For now, return a stub
        return "".join(text_parts)
