"""Agreement with the reference set: precision and recall per relation type, false merges,
and the list of disagreements for a human to judge (docs/architecture.md section 13).

Incident-level reference pairs are matched against the strongest relation between any two
views of the two incidents; view-level pairs are matched directly. `also_accept` outcomes
count as agreement but are reported as deviations, so tuning towards them is a choice
made in the open.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..contracts import Relation, RelationType
from .reference import STRENGTH, ReferencePair, load_reference, resolve_view

if TYPE_CHECKING:
    from ..store import Store


@dataclass
class PairResult:
    ref: ReferencePair
    predicted: RelationType | None
    via: tuple[str, str] | None  # the view pair that decided an incident-level roll-up

    @property
    def agrees(self) -> bool:
        return self.predicted is not None and self.ref.agrees(self.predicted)

    @property
    def deviates(self) -> bool:
        return self.agrees and self.predicted != self.ref.expected

    @property
    def false_merge(self) -> bool:
        return self.predicted is not None and self.ref.is_false_merge(self.predicted)


@dataclass
class TypeScore:
    """Strict per-type counts: `exact` is predicted == expected == this type. Accepted
    deviations are not credited here; they are listed separately."""

    reference: int = 0
    predicted: int = 0
    exact: int = 0

    @property
    def precision(self) -> float | None:
        return self.exact / self.predicted if self.predicted else None

    @property
    def recall(self) -> float | None:
        return self.exact / self.reference if self.reference else None


@dataclass
class Report:
    results: list[PairResult]
    by_type: dict[RelationType, TypeScore] = field(default_factory=dict)

    # A reference pair is evaluated when both of its sides exist in the corpus and a relation
    # was stored for it. Every rate below is over evaluated pairs only: a pair that could not
    # be evaluated is neither an agreement nor a miss, and "0 false merges" says nothing about it.

    @property
    def evaluated(self) -> list[PairResult]:
        return [r for r in self.results if r.predicted is not None]

    @property
    def not_evaluated(self) -> list[PairResult]:
        return [r for r in self.results if r.predicted is None]

    @property
    def strict(self) -> list[PairResult]:
        return [r for r in self.evaluated if r.predicted == r.ref.expected]

    @property
    def agreement(self) -> float:
        done = self.evaluated
        return sum(r.agrees for r in done) / len(done) if done else 0.0

    @property
    def exact_agreement(self) -> float:
        done = self.evaluated
        return len(self.strict) / len(done) if done else 0.0

    @property
    def false_merges(self) -> list[PairResult]:
        return [r for r in self.results if r.false_merge]

    @property
    def disagreements(self) -> list[PairResult]:
        """Misses: evaluated pairs whose relation is neither expected nor an accepted alternative."""
        return [r for r in self.evaluated if not r.agrees]

    @property
    def deviations(self) -> list[PairResult]:
        return [r for r in self.results if r.deviates]


def strongest_between(
    relations: list[Relation], incident_of: dict[str, str]
) -> dict[tuple[str, str], tuple[Relation, tuple[str, str]]]:
    best: dict[tuple[str, str], tuple[Relation, tuple[str, str]]] = {}
    for r in relations:
        ia, ib = incident_of.get(r.view_a), incident_of.get(r.view_b)
        if ia is None or ib is None or ia == ib:
            continue
        key = tuple(sorted((ia, ib)))
        if key not in best or STRENGTH[r.type] < STRENGTH[best[key][0].type]:
            best[key] = (r, (r.view_a, r.view_b))
    return best


def evaluate(store: Store, reference: list[ReferencePair] | None = None) -> Report:
    reference = load_reference() if reference is None else reference
    relations = store.relations()
    by_views = {(r.view_a, r.view_b): r for r in relations}
    incident_of = {v.view_id: v.incident_id for v in store.views()}
    by_incidents = strongest_between(relations, incident_of)

    results = []
    for ref in reference:
        if ref.level == "view":
            a, b = resolve_view(store, ref.a), resolve_view(store, ref.b)
            r = by_views.get(tuple(sorted((a, b)))) if a and b else None
            via = (a, b) if a and b and (a, b) != (ref.a, ref.b) else None
            results.append(PairResult(ref, r.type if r else None, via))
        else:
            hit = by_incidents.get((ref.a, ref.b))
            results.append(PairResult(ref, hit[0].type if hit else None, hit[1] if hit else None))

    # Per-type rates follow the same rule as the headline: evaluated pairs only. A reference
    # pair with a side missing from the corpus is reported under "not evaluated", not as a
    # recall miss of its type.
    by_type: dict[RelationType, TypeScore] = defaultdict(TypeScore)
    for res in results:
        if res.predicted is None:
            continue
        by_type[res.ref.expected].reference += 1
        by_type[res.predicted].predicted += 1
        if res.predicted == res.ref.expected:
            by_type[res.predicted].exact += 1
    return Report(results=results, by_type=dict(by_type))


def format_report(report: Report, show_agreed: bool = False) -> str:
    lines = []
    lines.append(
        f"reference pairs: {len(report.evaluated)}   agreement: {report.agreement:.0%} "
        f"(exact {report.exact_agreement:.0%}, {len(report.deviations)} accepted deviations)   "
        f"false merges: {len(report.false_merges)}"
    )
    lines.append(
        f"evaluated {len(report.evaluated)} of {len(report.results)}: "
        f"strict {len(report.strict)}, accepted alternatives {len(report.deviations)}, "
        f"misses {len(report.disagreements)}, false merges {len(report.false_merges)}; "
        f"not evaluated {len(report.not_evaluated)} (rates are over evaluated pairs only)"
    )
    lines.append("")
    lines.append(f"{'relation type':22} {'ref':>4} {'pred':>4} {'exact':>5} {'prec':>6} {'rec':>6}")
    for t in RelationType:
        s = report.by_type.get(t)
        if s is None:
            continue
        p = "-" if s.precision is None else f"{s.precision:.2f}"
        r = "-" if s.recall is None else f"{s.recall:.2f}"
        lines.append(f"{t.value:22} {s.reference:4} {s.predicted:4} {s.exact:5} {p:>6} {r:>6}")
    counts = Counter(res.predicted.value if res.predicted else "missing" for res in report.results)
    lines.append("")
    lines.append("predicted: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    def row(res: PairResult) -> str:
        via = f"  via {res.via[0]} <-> {res.via[1]}" if res.via else ""
        pred = res.predicted.value if res.predicted else "missing"
        return f"  {res.ref.a:14} {res.ref.b:14} expected {res.ref.expected.value:20} got {pred:20}{via}"

    if report.false_merges:
        lines += ["", "FALSE MERGES (campaign-level link the reference does not allow):"]
        lines += [row(r) for r in report.false_merges]
    if report.disagreements:
        lines += ["", "disagreements (judge by hand):"]
        for res in report.disagreements:
            lines.append(row(res) + (f"   [{res.ref.note}]" if res.ref.note else ""))
    if report.deviations:
        lines += ["", "accepted deviations from the baseline letter:"]
        lines += [row(r) for r in report.deviations]
    if report.not_evaluated:
        lines += ["", "not evaluated (a side is missing from this corpus):"]
        lines += [row(r) for r in report.not_evaluated]
    if show_agreed:
        lines += ["", "agreed:"]
        lines += [row(r) for r in report.results if r.agrees and not r.deviates]
    return "\n".join(lines)
