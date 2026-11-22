"""Comparators for the playbook layers that come from model output.

`pattern_overlap`: fuzzy weighted Jaccard over per-pattern scores, sum(min)/sum(max).
`absence_agreement`: agreement over co-observed expected elements, between views of the
same kind of page only. Only ABSENT is a signal; NOT_OBSERVABLE items never enter.
"""

from __future__ import annotations

from ..contracts import Evidence, Fingerprint
from ..registry import comparators


@comparators.register
class PatternOverlap:
    name = "pattern_overlap"
    version = "1"

    def compare(self, a: Fingerprint, b: Fingerprint) -> Evidence:
        sa = {i["value"]: float(i.get("score", 1.0)) for i in a.payload}
        sb = {i["value"]: float(i.get("score", 1.0)) for i in b.payload}
        keys = sorted(set(sa) | set(sb))
        num = sum(min(sa.get(k, 0.0), sb.get(k, 0.0)) for k in keys)
        den = sum(max(sa.get(k, 0.0), sb.get(k, 0.0)) for k in keys)
        score = num / den if den else 0.0
        shared = [
            {"ns": "pattern", "value": k, "weight": round(min(sa[k], sb[k]), 3)}
            for k in keys
            if k in sa and k in sb
        ]
        differing = [
            {
                "ns": "pattern",
                "a": sorted(k for k in sa if k not in sb),
                "b": sorted(k for k in sb if k not in sa),
            }
        ]
        return Evidence(
            view_a=a.view_id,
            view_b=b.view_id,
            layer_id=a.layer_id,
            score=round(score, 6),
            shared=shared,
            differing=[d for d in differing if d["a"] or d["b"]],
            comparator=self.name,
            version=self.version,
        )


@comparators.register
class AbsenceAgreement:
    name = "absence_agreement"
    version = "2"
    weak_weight = 0.5  # an absence that genuine pages also show
    min_co_observed = 3

    def compatible(self, a: Fingerprint, b: Fingerprint) -> bool:
        if a.payload.get("kind") != b.payload.get("kind"):
            return False
        co = {i["value"] for i in a.payload["items"]} & {i["value"] for i in b.payload["items"]}
        return len(co) >= self.min_co_observed

    def compare(self, a: Fingerprint, b: Fingerprint) -> Evidence:
        base = {
            "view_a": a.view_id,
            "view_b": b.view_id,
            "layer_id": a.layer_id,
            "comparator": self.name,
            "version": self.version,
        }
        if a.payload.get("kind") != b.payload.get("kind"):
            return Evidence(comparable=False, **base)
        sa = {i["value"]: i["state"] for i in a.payload["items"]}
        sb = {i["value"]: i["state"] for i in b.payload["items"]}
        co = sorted(set(sa) & set(sb))
        if len(co) < self.min_co_observed:
            return Evidence(comparable=False, **base)
        agree = [k for k in co if sa[k] == sb[k]]
        # both-absent agreements carry the signal; both-present ones are what any page has
        weak = {i["value"] for fp in (a, b) for i in fp.payload["items"] if i.get("weak")}
        absent = {k: self.weak_weight if k in weak else 1.0 for k in co}
        weight = {k: (absent[k] if sa[k] == "absent" else 0.5) for k in agree}
        total = sum(absent[k] if "absent" in (sa[k], sb[k]) else 0.5 for k in co)
        score = sum(weight.values()) / total if total else 0.0
        return Evidence(
            score=round(score, 6),
            shared=[{"ns": "element", "value": f"{k}:{sa[k]}", "weight": weight[k]} for k in agree],
            differing=[
                {
                    "ns": "element",
                    "a": [f"{k}:{sa[k]}" for k in co if k not in weight],
                    "b": [f"{k}:{sb[k]}" for k in co if k not in weight],
                }
            ]
            if len(agree) < len(co)
            else [],
            **base,
        )
