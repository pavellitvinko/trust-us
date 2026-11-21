"""The reference set: typed pairs from docs/baseline-report.md section 13."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..contracts import RelationType, ViewKind
from .fixtures import FIXTURES_DIR

if TYPE_CHECKING:
    from ..store import Store

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


# -- resolving fixture-style names against a real store -------------------------------------
#
# Reference pairs below folder level name views the way the fixtures do (`2:healthlinkq`,
# `61:article`). Real view ids are content hashes, so each alias is resolved from what the
# ingest recorded about the view: its origin, its kind and the folder its primary file sits in.

_ALIASES: dict[str, tuple[str, ...]] = {
    # alias: (kind, origin or "*", primary-path hint or "")
    "page": ("page", "saved_page", ""),
    "healthlinkq": ("page", "har_only", ""),
    "capture": ("page", "capture_bundle", ""),
    "article": ("page", "saved_page", "strona_1/"),
    "chat": ("page", "saved_page", "strona_2/"),
    "business": ("page", "saved_page", "strona_3/"),
    "video": ("video", "*", ""),
    "ad": ("image", "*", ""),
}


def resolve_view(store: Store, name: str) -> str | None:
    """Return the real view id for a fixture-style alias, or the name itself if it is a view id."""
    try:
        store.get_view(name)
        return name
    except KeyError:
        pass
    incident_id, _, alias = name.partition(":")
    rule = _ALIASES.get(alias)
    if rule is None:
        return None
    kind, origin, hint = rule
    candidates = [
        v
        for v in store.views(incident_id)
        if v.kind is ViewKind(kind)
        and (origin == "*" or v.meta.get("origin") == origin)
        and (not hint or hint in (v.primary or ""))
    ]
    if alias == "page" and len(candidates) > 1:  # several saved pages: "page" is ambiguous
        candidates = [v for v in candidates if "strona_" not in (v.primary or "")]
    return candidates[0].view_id if len(candidates) == 1 else None
