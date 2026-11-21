"""Fusion weights and thresholds. Hand-set on ten incidents; exposed so tuning edits a file.

Group weights say how much one correlation group may contribute to its tier:
tier score = 1 - prod(1 - weight_g * group_score_g). With a kit group weight of 0.6, one
kit group alone caps the kit tier at 0.6, below the family threshold: a family link needs
two independent kit signals, which is what keeps the unproven 3-14 and 36-47 similarities
from becoming links.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..contracts import Tier

DEFAULT_TIER_WEIGHT: dict[str, float] = {
    Tier.IDENTITY.value: 1.0,
    Tier.ARTIFACT.value: 0.9,
    Tier.KIT.value: 0.6,
    Tier.PLAYBOOK.value: 0.8,
    Tier.NOISE.value: 0.0,
}


@dataclass
class FusionConfig:
    # per-group overrides; groups not listed use their tier's default weight
    group_weights: dict[str, float] = field(
        default_factory=lambda: {
            "domain": 1.0,
            "campaign": 1.0,
            "tracking": 1.0,
            "conversion": 0.9,
            "brand": 0.7,  # an offer name alone is not an account
            "asset": 0.9,
            "text": 0.9,
            "infra": 0.3,
        }
    )
    tier_weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_TIER_WEIGHT))
    # thresholds, in decision order
    duplicate_text: float = 0.9
    identity: float = 0.8
    artifact: float = 0.8
    artifact_min_groups: int = 2
    artifact_group_floor: float = 0.5
    kit: float = 0.7
    playbook: float = 0.7
    min_coverage: float = 0.3
    # namespaces where two different values are a contradiction (one record per page)
    exclusive_namespaces: tuple[str, ...] = ("aio.campaign_uuid", "aio.owner_uuid")
    # a shared value here anchors a duplicate: same landing, or the same primary file
    duplicate_anchors: tuple[str, ...] = ("url.host_path", "primary.sha256")
    duplicate_layer: str = "text.shingle"
    # a perceptual match this close in these layers is the same creative re-encoded
    duplicate_anchor_layers: tuple[str, ...] = ("ad.creative",)
    duplicate_anchor_max_distance: int = 6

    def group_weight(self, group: str, tier: Tier) -> float:
        return self.group_weights.get(group, self.tier_weights.get(tier.value, 0.0))

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> FusionConfig:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for key in ("exclusive_namespaces", "duplicate_anchors", "duplicate_anchor_layers"):
            if key in data:
                data[key] = tuple(data[key])
        return cls(**data)


DEFAULT = FusionConfig()
