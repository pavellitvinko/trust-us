"""Artifact-tier layer: near-duplicate text (F1).

Word 5-gram MinHash on the cleaned article body (`text.body`: comments, navigation and
widgets already removed by the Evidence track). Two captures of one landing score about
1; a rewrite around a different celebrity scores near 0, which is what `narrative.skeleton`
is for.
"""

from __future__ import annotations

from ..compare.minhash import default_hasher, shingles
from ..contracts import Fingerprint, FingerprintKind, Observation, Tier, View
from ..registry import layers
from ._text import TEXT_NAMESPACES, body_text

MIN_WORDS = 20


@layers.register
class TextShingle:
    name = "text.shingle"
    version = "2"
    kind = FingerprintKind.HASH
    tier = Tier.ARTIFACT
    group = "text"
    comparator = "minhash"
    namespaces = TEXT_NAMESPACES

    def build(self, view: View, observations: list[Observation]) -> Fingerprint | None:
        body = body_text(observations)
        grams = shingles(body)
        if len(body.split()) < MIN_WORDS or not grams:
            return None
        signature = default_hasher.signature(grams)
        return Fingerprint(
            view_id=view.view_id,
            layer_id=self.name,
            kind=self.kind,
            payload={"algo": "minhash", "values": [str(x) for x in signature], "size": len(grams)},
            version=self.version,
        )
