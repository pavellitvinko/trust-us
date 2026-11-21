"""Perceptual-hash comparator: Hamming distance with best-match pairing.

Each view carries a set of 64-bit hashes (one per image or key frame). Every hash on the
smaller side looks for its nearest hash on the other side; a pair within `max_distance`
bits is a match. The score is the matched share of the smaller side, so a single
screenshot that matches one of twenty images on a page scores 1.
"""

from __future__ import annotations

from ..contracts import Evidence, Fingerprint
from ..registry import comparators


def hamming(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


@comparators.register
class PerceptualHash:
    name = "phash"
    version = "1"
    max_distance = 10

    def compatible(self, a: Fingerprint, b: Fingerprint) -> bool:
        return bool(a.payload.get("values")) and bool(b.payload.get("values"))

    def compare(self, a: Fingerprint, b: Fingerprint) -> Evidence:
        ha = list(dict.fromkeys(a.payload.get("values", [])))
        hb = list(dict.fromkeys(b.payload.get("values", [])))
        if not ha or not hb:
            return Evidence(
                view_a=a.view_id,
                view_b=b.view_id,
                layer_id=a.layer_id,
                comparator=self.name,
                version=self.version,
            )
        small, large, flipped = (ha, hb, False) if len(ha) <= len(hb) else (hb, ha, True)
        shared, taken = [], set()
        for h in small:
            best = min(
                ((hamming(h, g), j) for j, g in enumerate(large) if j not in taken),
                default=None,
            )
            if best is not None and best[0] <= self.max_distance:
                d, j = best
                taken.add(j)
                pair = (large[j], h) if flipped else (h, large[j])
                shared.append(
                    {
                        "ns": "phash",
                        "value": pair[0],
                        "other": pair[1],
                        "distance": d,
                        "weight": round(1 - d / 64, 4),
                    }
                )
        score = len(shared) / len(small)
        return Evidence(
            view_a=a.view_id,
            view_b=b.view_id,
            layer_id=a.layer_id,
            score=round(score, 6),
            shared=shared,
            differing=[{"ns": "phash", "a": len(ha) - len(shared), "b": len(hb) - len(shared)}]
            if len(shared) < max(len(ha), len(hb))
            else [],
            comparator=self.name,
            version=self.version,
        )
