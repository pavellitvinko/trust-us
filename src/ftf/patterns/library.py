"""Pattern library: versioned storage with stable IDs, induction history and analyst control.

A pattern is a technique recurring across views, described by the model in its own words.
The library is versioned; pattern IDs are stable across versions; merges and splits are recorded.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger(__name__)


class PatternMetadata(BaseModel):
    """Metadata about a pattern's discovery and evolution."""

    model_config = ConfigDict(extra="forbid")

    created_in_version: str  # library version where it first appeared
    last_seen_in_version: str  # most recent version
    merged_from: list[str] = Field(default_factory=list)  # pattern IDs this was merged from
    split_into: list[str] = Field(default_factory=list)  # pattern IDs created from this split
    analyst_renamed_from: str | None = None  # original name before analyst edit
    analyst_retired_at: str | None = None  # ISO timestamp when analyst marked as inactive


class PatternDefinition(BaseModel):
    """A pattern written by the model from observed technique cards.

    Tier limit: pattern evidence is playbook-tier (T4) only. The model judges each matching
    view, but the pattern itself cannot drive campaign merging—only toolkit and identity can.
    """

    model_config = ConfigDict(extra="forbid")

    pattern_id: str  # stable across versions, e.g. "pattern.h1_celebrity_endorser"
    name: str  # written by the model, editable by analyst
    description: str  # what distinguishes it from neighbours
    positive_examples: list[dict[str, Any]] = Field(default_factory=list)  # {card_name, evidence}
    near_misses: list[dict[str, Any]] = Field(default_factory=list)  # similar but different
    related_patterns: list[str] = Field(default_factory=list)  # pattern IDs of near-neighbours
    centroid: list[float] = Field(default_factory=list)  # keeps IDs stable across versions
    metadata: PatternMetadata | None = None


class CompositePattern(BaseModel):
    """Techniques that appear together often enough to be a recognisable whole.

    E.g. celebrity endorser + fake news framing + profit screenshot; or health scare + discount
    + countdown timer. Composites help explain why views are related despite lacking shared IDs.
    """

    model_config = ConfigDict(extra="forbid")

    composite_id: str
    technique_ids: list[str]  # pattern IDs that make up this composite
    name: str
    description: str
    frequency: int  # number of views showing all these techniques together


class LibraryVersion(BaseModel):
    """Snapshot of the pattern library at one induction run."""

    model_config = ConfigDict(extra="forbid")

    version_id: str  # e.g. "v1.0.1-20261121-143025"
    created_at: str  # ISO timestamp
    patterns: list[PatternDefinition] = Field(default_factory=list)
    composites: list[CompositePattern] = Field(default_factory=list)
    input_hash: str = ""  # hash of technique cards + input order, for reproducibility checks
    model_name: str = ""
    model_config_snapshot: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""


class PatternLibraryStore:
    """Persistent storage for versioned pattern libraries and induction history."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.versions_dir = self.base_dir / "versions"
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.base_dir / "index.json"
        self._index = None

    def save_version(self, version: LibraryVersion) -> None:
        """Write a library version and update the index."""
        path = self.versions_dir / f"{version.version_id}.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(version.model_dump(), ensure_ascii=False), encoding="utf-8")
        temp.replace(path)
        self._index = None  # invalidate cache
        log.info(
            f"saved pattern library version {version.version_id} with {len(version.patterns)} patterns"
        )

    def load_version(self, version_id: str) -> LibraryVersion | None:
        """Load a specific library version."""
        path = self.versions_dir / f"{version_id}.json"
        if not path.exists():
            return None
        return LibraryVersion.model_validate_json(path.read_text(encoding="utf-8"))

    def latest_version(self) -> LibraryVersion | None:
        """Return the most recent library version, or None if empty."""
        versions = list(self.versions_dir.glob("v*.json"))
        if not versions:
            return None
        # Sort by stem (version ID), which works for semantic versions
        latest_stem = max(p.stem for p in versions)
        return self.load_version(latest_stem)

    def list_versions(self) -> list[str]:
        """List all version IDs in order."""
        return sorted(p.stem for p in self.versions_dir.glob("v*.json"))

    def pattern_by_id(
        self, pattern_id: str, version_id: str | None = None
    ) -> PatternDefinition | None:
        """Find a pattern in a specific version or the latest."""
        if version_id is None:
            v = self.latest_version()
            if v is None:
                return None
        else:
            v = self.load_version(version_id)
            if v is None:
                return None
        return next((p for p in v.patterns if p.pattern_id == pattern_id), None)


@dataclass
class TechniqueCardForClustering:
    """A technique card as prepared for embedding and clustering."""

    card_name: str
    card_description: str
    view_id: str
    incident_id: str
    source_text: str  # full text from which the card came
    card_id: str = ""  # stable hash of the view and card name
    campaign_id: str | None = None  # from the deterministic grouping; None counts for nothing
    evidence: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ClusterResult:
    """Result of clustering technique cards by embedding."""

    cluster_id: int
    cards: list[TechniqueCardForClustering] = field(default_factory=list)
    centroid: list[float] = field(default_factory=list)  # embedding of cluster centre

    def card_names(self) -> list[str]:
        """Return unique card names in this cluster."""
        seen = set()
        result = []
        for c in self.cards:
            if c.card_name not in seen:
                result.append(c.card_name)
                seen.add(c.card_name)
        return result
