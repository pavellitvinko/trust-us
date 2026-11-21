"""Artifact-tier layer: exact content hashes of images and commenter avatars (E1, E12)."""

from __future__ import annotations

from ..contracts import Tier
from ._set_layer import set_layer

set_layer(
    name="asset.exact",
    tier=Tier.ARTIFACT,
    group="asset",
    comparator="shared_value",
    namespaces=("asset.sha256", "asset.sha256.content", "avatar.sha256", "asset.sha256.avatar"),
    doc="SHA-256 of content images and avatars; logos, icons and library files belong to kit.code.",
)
