"""Comparators for the victim-run layers (src/ftf/layers/victim.py)."""

from __future__ import annotations

import math

from ..contracts import Evidence, Fingerprint
from ..registry import comparators


def _evidence(
    a: Fingerprint,
    b: Fingerprint,
    score: float,
    name: str,
    version: str,
    shared=None,
    differing=None,
    comparable: bool = True,
) -> Evidence:
    return Evidence(
        view_a=a.view_id,
        view_b=b.view_id,
        layer_id=a.layer_id,
        comparable=comparable,
        score=round(max(0.0, min(1.0, score)), 6),
        shared=shared or [],
        differing=differing or [],
        comparator=name,
        version=version,
    )


@comparators.register
class Jaccard:
    name = "victim.jaccard"
    version = "1"

    def compare(self, a: Fingerprint, b: Fingerprint) -> Evidence:
        sa = {(i["ns"], i["value"]) for i in a.payload}
        sb = {(i["ns"], i["value"]) for i in b.payload}
        union = sa | sb
        score = len(sa & sb) / len(union) if union else 0.0
        return _evidence(
            a,
            b,
            score,
            self.name,
            self.version,
            shared=[{"ns": n, "value": v} for n, v in sorted(sa & sb)],
            differing=[
                {
                    "ns": "victim.ask",
                    "a": sorted(v for _, v in sa - sb),
                    "b": sorted(v for _, v in sb - sa),
                }
            ]
            if sa ^ sb
            else [],
        )


@comparators.register
class Cosine:
    name = "victim.cosine"
    version = "1"

    def compare(self, a: Fingerprint, b: Fingerprint) -> Evidence:
        va, vb = a.payload.get("values", []), b.payload.get("values", [])
        if a.payload.get("model") != b.payload.get("model") or len(va) != len(vb) or not va:
            return _evidence(a, b, 0.0, self.name, self.version, comparable=False)
        dot = sum(x * y for x, y in zip(va, vb, strict=True))
        norm = math.sqrt(sum(x * x for x in va)) * math.sqrt(sum(y * y for y in vb))
        score = dot / norm if norm else 0.0
        labels = a.payload.get("labels")
        shared = (
            [
                {"ns": a.layer_id, "value": lab}
                for lab, x, y in zip(labels, va, vb, strict=True)
                if x and y
            ]
            if labels
            else []
        )
        return _evidence(a, b, score, self.name, self.version, shared=shared)


@comparators.register
class MoneyClose:
    """Per known dimension: 1 when equal, 0 when a factor of ~3 apart (0.5 in log10)."""

    name = "victim.money_close"
    version = "1"
    dims = ("entry", "promised_return", "ratio", "days")

    def compare(self, a: Fingerprint, b: Fingerprint) -> Evidence:
        va, vb = a.payload["values"], b.payload["values"]
        scores, shared = [], []
        for dim, x, y in zip(self.dims, va, vb, strict=False):
            if x and y:
                s = max(0.0, 1.0 - abs(x - y) / 0.5)
                scores.append(s)
                if s > 0.5:
                    shared.append({"ns": "victim.money", "value": dim, "score": round(s, 3)})
        if not scores:
            return _evidence(a, b, 0.0, self.name, self.version, comparable=False)
        return _evidence(a, b, sum(scores) / len(scores), self.name, self.version, shared=shared)


def edit_distance(a: list[str], b: list[str]) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


@comparators.register
class SequenceEdit:
    name = "victim.sequence_edit"
    version = "1"

    def compare(self, a: Fingerprint, b: Fingerprint) -> Evidence:
        sa, sb = a.payload["values"], b.payload["values"]
        longest = max(len(sa), len(sb))
        score = 1 - edit_distance(sa, sb) / longest if longest else 0.0
        return _evidence(
            a,
            b,
            score,
            self.name,
            self.version,
            shared=[{"ns": "funnel.shape", "value": " > ".join(sa)}] if sa == sb else [],
            differing=[] if sa == sb else [{"ns": "funnel.shape", "a": sa, "b": sb}],
        )
