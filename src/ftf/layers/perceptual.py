"""Perceptual image, creative and video layers (E2, L2, M1). Hashes come from the Evidence track.

All three compare with best-match pairing on Hamming distance, so a re-encoded or
hue-shifted copy of an image still matches while a different screenshot does not. A
screenshot-only item may carry nothing else, which is why these are must-have.
"""

from __future__ import annotations

from ..contracts import Fingerprint, FingerprintKind, Observation, Tier, View
from ..registry import layers
from ._set_layer import linkage_items


def _hash_layer(name: str, group: str, namespace, doc: str, min_values: int = 1):
    class HashLayer:
        def build(self, view: View, observations: list[Observation]) -> Fingerprint | None:
            values = sorted({v.lower() for _, v in linkage_items(observations, self.namespaces)})
            if len(values) < min_values:
                return None
            return Fingerprint(
                view_id=view.view_id,
                layer_id=self.name,
                kind=self.kind,
                payload={"algo": "phash", "values": values},
                version=self.version,
            )

    HashLayer.name = name
    HashLayer.version = "1"
    HashLayer.kind = FingerprintKind.HASH
    HashLayer.tier = Tier.ARTIFACT
    HashLayer.group = group
    HashLayer.comparator = "phash"
    HashLayer.namespaces = (namespace,) if isinstance(namespace, str) else tuple(namespace)
    HashLayer.__name__ = HashLayer.__qualname__ = "".join(p.title() for p in name.split("."))
    HashLayer.__doc__ = doc
    layers.register(HashLayer)
    return HashLayer


_hash_layer(
    "asset.perceptual",
    "perceptual",
    ("image.phash", "asset.phash"),
    "E2: pHash of a page's content images.",
)
_hash_layer(
    "ad.creative", "creative", "creative.phash", "L2: pHash of an ad creative or screenshot."
)
_hash_layer(
    "media.video", "video", "video.frame_phash", "M1: pHash of sampled key frames.", min_values=2
)
