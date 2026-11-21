"""Pattern novelty tracking: candidates and promotion to library.

Technique cards that match no library pattern are kept as candidates.
When the same candidate appears in a second independent campaign,
the next induction run promotes it to the library.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .library import TechniqueCardForClustering

log = logging.getLogger(__name__)


class CandidatePattern(BaseModel):
    """A technique card waiting for promotion to the library."""

    candidate_id: str  # hash of normalized card text
    card_name: str
    card_description: str
    first_seen_incident: str
    first_seen_view: str
    first_seen_at: str  # ISO timestamp
    seen_in: list[dict[str, Any]] = Field(default_factory=list)  # {incident, view, timestamp}
    promotion_ready: bool = False  # True when seen in 2+ independent incidents


class NoveltyStore:
    """Persistent storage for candidate patterns and promotion history."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.candidates_path = self.base_dir / "candidates.json"
        self.promoted_path = self.base_dir / "promoted.json"
        self._candidates = None

    def _load_candidates(self) -> dict[str, CandidatePattern]:
        """Load candidates from disk."""
        if self._candidates is not None:
            return self._candidates
        if not self.candidates_path.exists():
            return {}
        try:
            data = json.loads(self.candidates_path.read_text(encoding="utf-8"))
            return {k: CandidatePattern.model_validate(v) for k, v in data.items()}
        except Exception as e:
            log.warning(f"error loading candidates: {e}")
            return {}

    def _save_candidates(self, candidates: dict[str, CandidatePattern]) -> None:
        """Write candidates to disk."""
        data = {k: v.model_dump() for k, v in candidates.items()}
        self.candidates_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self._candidates = candidates

    def add_candidate(
        self,
        card: TechniqueCardForClustering,
        view_id: str,
        incident_id: str,
    ) -> CandidatePattern:
        """Record a technique card as a candidate pattern.

        Args:
            card: the technique card
            view_id: view where it appeared
            incident_id: incident ID

        Returns:
            The candidate pattern (new or existing).
        """
        import hashlib

        # Normalize for hashing: lowercase, remove extra whitespace
        normalized = " ".join(card.card_description.lower().split())
        candidate_id = hashlib.sha256(normalized.encode()).hexdigest()[:12]

        candidates = self._load_candidates()
        now = datetime.now().isoformat()

        if candidate_id not in candidates:
            candidates[candidate_id] = CandidatePattern(
                candidate_id=candidate_id,
                card_name=card.card_name,
                card_description=card.card_description,
                first_seen_incident=incident_id,
                first_seen_view=view_id,
                first_seen_at=now,
            )

        candidate = candidates[candidate_id]
        # Add this sighting if not already recorded
        if not any(
            s["incident"] == incident_id and s["view"] == view_id for s in candidate.seen_in
        ):
            candidate.seen_in.append({
                "incident": incident_id,
                "view": view_id,
                "timestamp": now,
            })

        # Check if promotion-ready: seen in 2+ different incidents
        incidents_seen = set(s["incident"] for s in candidate.seen_in)
        candidate.promotion_ready = len(incidents_seen) >= 2

        self._save_candidates(candidates)
        return candidate

    def ready_for_promotion(self) -> list[CandidatePattern]:
        """Return all candidates that have been seen twice independently."""
        candidates = self._load_candidates()
        return [c for c in candidates.values() if c.promotion_ready]

    def promote_candidate(self, candidate_id: str) -> None:
        """Move a candidate to the promoted list (before library insertion)."""
        candidates = self._load_candidates()
        if candidate_id not in candidates:
            return

        candidate = candidates.pop(candidate_id)
        self._save_candidates(candidates)

        # Append to promoted log
        promoted = []
        if self.promoted_path.exists():
            try:
                promoted = json.loads(self.promoted_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        promoted.append({
            "candidate_id": candidate_id,
            "promoted_at": datetime.now().isoformat(),
            "candidate": candidate.model_dump(),
        })
        self.promoted_path.write_text(json.dumps(promoted, indent=2), encoding="utf-8")
        log.info(f"promoted candidate {candidate_id}: {candidate.card_name}")
