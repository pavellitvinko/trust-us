"""Set-overlap comparators: weighted Jaccard for schemas and class sets, MinHash for large sets.

Unlike shared-value, unshared items *do* count here: a kit is a whole schema, and two
pages that share half their parameter names are half alike. Stoplisted items (weight 0)
vanish from both sides.
"""

from __future__ import annotations

from ..contracts import Evidence, Fingerprint
from ..registry import comparators
from .minhash import estimate_jaccard
from .weights import weights


@comparators.register
class SetOverlap:
    name = "set_overlap"
    version = "1"

    def compare(self, a: Fingerprint, b: Fingerprint) -> Evidence:
        items_a = {(i["ns"], str(i["value"])) for i in a.payload}
        items_b = {(i["ns"], str(i["value"])) for i in b.payload}
        w = {k: weights.weight(*k) for k in items_a | items_b}
        shared = sorted(k for k in items_a & items_b if w[k] > 0)
        union = sum(w[k] for k in items_a | items_b)
        score = sum(w[k] for k in shared) / union if union > 0 else 0.0
        common_ns = {ns for ns, _ in items_a} & {ns for ns, _ in items_b}
        differing = [
            {
                "ns": ns,
                "a": sorted(v for n, v in items_a - items_b if n == ns and w[(n, v)] > 0),
                "b": sorted(v for n, v in items_b - items_a if n == ns and w[(n, v)] > 0),
            }
            for ns in sorted(common_ns)
        ]
        differing = [d for d in differing if d["a"] or d["b"]]
        return Evidence(
            view_a=a.view_id,
            view_b=b.view_id,
            layer_id=a.layer_id,
            score=round(score, 6),
            shared=[{"ns": ns, "value": v, "weight": round(w[(ns, v)], 4)} for ns, v in shared],
            differing=differing,
            comparator=self.name,
            version=self.version,
        )


@comparators.register
class MinHashJaccard:
    name = "minhash"
    version = "1"

    def compare(self, a: Fingerprint, b: Fingerprint) -> Evidence:
        score = estimate_jaccard(a.payload.get("values", []), b.payload.get("values", []))
        return Evidence(
            view_a=a.view_id,
            view_b=b.view_id,
            layer_id=a.layer_id,
            score=round(score, 6),
            shared=[{"ns": "jaccard", "value": f"{score:.3f}", "weight": 1.0}] if score else [],
            comparator=self.name,
            version=self.version,
        )
