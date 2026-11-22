"""Candidates: technique clusters not yet seen in two independent campaigns."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .library import ClusterResult


class CandidatePattern(BaseModel):
    """A technique waiting for a sighting in a second independent campaign."""

    candidate_id: str
    card_name: str
    card_description: str
    first_seen_incident: str
    first_seen_view: str
    first_seen_at: str
    seen_in: list[dict[str, Any]] = Field(default_factory=list)
    promotion_ready: bool = False


def _campaigns(candidate: CandidatePattern) -> set[str]:
    return {s["campaign"] for s in candidate.seen_in if s.get("campaign")}


class NoveltyStore:
    """Active candidates and the log of those that reached the library."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.candidates_path = self.base_dir / "candidates.json"
        self.promoted_path = self.base_dir / "promoted.json"

    def candidates(self) -> dict[str, CandidatePattern]:
        if not self.candidates_path.exists():
            return {}
        data = json.loads(self.candidates_path.read_text(encoding="utf-8"))
        return {key: CandidatePattern.model_validate(value) for key, value in data.items()}

    def _write(self, path: Path, data: Any) -> None:
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        temp.replace(path)

    def sync(self, clusters: list[ClusterResult], library_card_ids: set[str]) -> dict[str, int]:
        """Replace active candidates. Earlier ones whose cards reached the library are logged."""
        previous = self.candidates()
        now = datetime.now(UTC).isoformat()
        current: dict[str, CandidatePattern] = {}
        for cluster in clusters:
            ids = sorted(c.card_id for c in cluster.cards)
            key = hashlib.sha256("|".join(ids).encode()).hexdigest()[:12]
            first = cluster.cards[0]
            earlier = previous.get(key)
            candidate = CandidatePattern(
                candidate_id=key,
                card_name=first.card_name,
                card_description=first.card_description,
                first_seen_incident=first.incident_id,
                first_seen_view=first.view_id,
                first_seen_at=earlier.first_seen_at if earlier else now,
                seen_in=[
                    {
                        "card": c.card_id,
                        "incident": c.incident_id,
                        "view": c.view_id,
                        "campaign": c.campaign_id,
                    }
                    for c in cluster.cards
                ],
            )
            candidate.promotion_ready = len(_campaigns(candidate)) >= 2
            current[key] = candidate
        promoted = []
        for key, candidate in previous.items():
            if key not in current and {s.get("card") for s in candidate.seen_in} & library_card_ids:
                promoted.append(
                    {"candidate_id": key, "promoted_at": now, "candidate": candidate.model_dump()}
                )
        if promoted:
            log = []
            if self.promoted_path.exists():
                log = json.loads(self.promoted_path.read_text(encoding="utf-8"))
            self._write(self.promoted_path, log + promoted)
        self._write(self.candidates_path, {k: v.model_dump() for k, v in current.items()})
        return {"candidates": len(current), "promoted": len(promoted)}
