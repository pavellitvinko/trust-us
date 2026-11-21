"""Shared-value comparator for identifier sets.

Each shared value contributes on its own: score = 1 - prod(1 - w(v)). Unshared values do
not dilute the score, because operators rotate domains and ad IDs between deployments.

This is the plain version. The Method track adds rarity weighting, the stoplist and the
demotion rule by supplying `weight`.
"""

from __future__ import annotations

from collections.abc import Callable

from ..contracts import Evidence, Fingerprint
from ..registry import comparators

Weight = Callable[[str, str], float]


def shared_value_evidence(
    a: Fingerprint, b: Fingerprint, weight: Weight, name: str, version: str
) -> Evidence:
    items_a = {(i["ns"], i["value"]) for i in a.payload}
    items_b = {(i["ns"], i["value"]) for i in b.payload}
    shared = sorted(items_a & items_b)
    miss = 1.0
    shared_out = []
    for ns, value in shared:
        w = max(0.0, min(1.0, weight(ns, value)))
        miss *= 1.0 - w
        shared_out.append({"ns": ns, "value": value, "weight": round(w, 4)})
    # "Differing" only makes sense within a namespace both sides have.
    common_ns = {ns for ns, _ in items_a} & {ns for ns, _ in items_b}
    differing = [
        {
            "ns": ns,
            "a": sorted(v for n, v in items_a - items_b if n == ns),
            "b": sorted(v for n, v in items_b - items_a if n == ns),
        }
        for ns in sorted(common_ns)
        if any(n == ns for n, _ in (items_a ^ items_b))
    ]
    return Evidence(
        view_a=a.view_id,
        view_b=b.view_id,
        layer_id=a.layer_id,
        score=round(1.0 - miss, 6),
        shared=shared_out,
        differing=differing,
        comparator=name,
        version=version,
    )


@comparators.register
class SharedValue:
    name = "shared_value"
    version = "1"
    default_weight = 0.9

    def compare(self, a: Fingerprint, b: Fingerprint) -> Evidence:
        return shared_value_evidence(
            a, b, lambda ns, value: self.default_weight, self.name, self.version
        )
