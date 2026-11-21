"""The reference set: typed pairs from docs/baseline-report.md section 13."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..contracts import RelationType
from .fixtures import FIXTURES_DIR

REFERENCE = FIXTURES_DIR / "reference_pairs.json"

# strongest first; the roll-up of an incident pair is its strongest view pair
STRENGTH = {t: i for i, t in enumerate(RelationType)}
CAMPAIGN_LEVEL = {RelationType.DUPLICATE, RelationType.SAME_CAMPAIGN}


@dataclass(frozen=True)
class ReferencePair:
    a: str
    b: str
    expected: RelationType
    also_accept: frozenset[RelationType] = field(default_factory=frozenset)
    source: str = ""
    note: str = ""

    @property
    def level(self) -> str:
        return "view" if ":" in self.a else "incident"

    def agrees(self, predicted: RelationType) -> bool:
        return predicted == self.expected or predicted in self.also_accept

    def is_false_merge(self, predicted: RelationType) -> bool:
        """A campaign-level prediction where the reference allows nothing above family."""
        allowed = {self.expected} | set(self.also_accept)
        return predicted in CAMPAIGN_LEVEL and not (allowed & CAMPAIGN_LEVEL)


def load_reference(path: Path = REFERENCE) -> list[ReferencePair]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for p in data["pairs"]:
        a, b = sorted((p["a"], p["b"]))
        out.append(
            ReferencePair(
                a=a,
                b=b,
                expected=RelationType(p["expected"]),
                also_accept=frozenset(RelationType(t) for t in p.get("also_accept", [])),
                source=p.get("source", ""),
                note=p.get("note", ""),
            )
        )
    return out
