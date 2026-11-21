"""Open-set assignment: a new view gets exactly one headline outcome (docs/architecture.md section 10).

    duplicate of X · new variant of campaign C · new campaign in family F ·
    new campaign, known playbook P · novel pattern · insufficient evidence

Candidates come from the inverted index on identifier values (`store.views_with_value`)
plus every view holding a fingerprint in the same hash or vector layer (at sample size
that is a few lookups; MinHash bands and a vector index slot in behind the same
function). The view is compared in full only against that union. "Novel pattern" and
"insufficient evidence" are first-class outcomes: the hidden set contains material that
must not be attached to anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from ..compare.weights import weights
from ..contracts import FingerprintKind, Purpose, Relation, RelationType, State
from ..fusion import DEFAULT, FusionConfig, fuse_pair, prepare
from .levels import Groups, build_groups, merge_guard

if TYPE_CHECKING:
    from ..store import Store


class Outcome(StrEnum):
    DUPLICATE_OF = "duplicate_of"
    VARIANT_OF_CAMPAIGN = "new_variant_of_campaign"
    NEW_CAMPAIGN_IN_FAMILY = "new_campaign_in_family"
    NEW_CAMPAIGN_KNOWN_PLAYBOOK = "new_campaign_known_playbook"
    NOVEL = "novel_pattern"
    INSUFFICIENT = "insufficient_evidence"


ORDER = {o: i for i, o in enumerate(Outcome)}


@dataclass
class Related:
    view_id: str
    relation: Relation

    @property
    def strength(self) -> float:
        return max(self.relation.tier_scores.values(), default=0.0)


@dataclass
class Assignment:
    view_id: str
    outcome: Outcome
    target: str | None  # view id, campaign id, family id or playbook id
    related: dict[str, list[Related]] = field(default_factory=dict)  # relation type -> ranked
    candidates: int = 0
    reasons: list[str] = field(default_factory=list)

    def headline(self) -> str:
        return f"{self.outcome.value}" + (f" {self.target}" if self.target else "")


def candidate_views(store: Store, view_id: str, exclude_incident: bool = True) -> set[str]:
    own = store.get_view(view_id)
    found: set[str] = set()
    for o in store.observations(view_id):
        if o.state is not State.PRESENT or o.purpose is Purpose.RISK or o.value is None:
            continue
        if weights.weight(o.namespace, str(o.value)) <= 0:
            continue
        found |= set(store.views_with_value(o.namespace, o.value))
    for fp in store.fingerprints(view_id=view_id):
        if fp.kind is not FingerprintKind.SET:
            found |= {f.view_id for f in store.fingerprints(layer_id=fp.layer_id)}
    found.discard(view_id)
    if exclude_incident:
        same = {v.view_id for v in store.views(own.incident_id)}
        found -= same
    return found


def assign(
    store: Store,
    view_id: str,
    groups: Groups | None = None,
    config: FusionConfig = DEFAULT,
    exclude_incident: bool = True,
    prepared: bool = False,
) -> Assignment:
    if not prepared:
        prepare(store, config)
    groups = groups or build_groups(store, config, funnel=False)
    candidates = candidate_views(store, view_id, exclude_incident)
    related: dict[str, list[Related]] = {t.value: [] for t in RelationType}
    for c in sorted(candidates):
        r = fuse_pair(store, view_id, c, config, persist=False)
        related[r.type.value].append(Related(c, r))
    for items in related.values():
        items.sort(key=lambda x: -x.strength)
    reasons: list[str] = [f"{len(candidates)} candidates from the identifier and hash indexes"]

    def best(t: RelationType) -> Related | None:
        return related[t.value][0] if related[t.value] else None

    if d := best(RelationType.DUPLICATE):
        reasons += d.relation.reasons
        return Assignment(
            view_id, Outcome.DUPLICATE_OF, d.view_id, related, len(candidates), reasons
        )
    guarded = [
        x for x in related[RelationType.SAME_CAMPAIGN.value] if merge_guard(x.relation, config)
    ]
    if guarded:
        c = guarded[0]
        reasons += c.relation.reasons
        target = groups.campaign_of.get(c.view_id, f"c:{c.view_id}")
        return Assignment(
            view_id, Outcome.VARIANT_OF_CAMPAIGN, target, related, len(candidates), reasons
        )
    if f := best(RelationType.SAME_FAMILY):
        reasons += f.relation.reasons
        target = groups.family_of_view(f.view_id) or f"f:{f.view_id}"
        return Assignment(
            view_id, Outcome.NEW_CAMPAIGN_IN_FAMILY, target, related, len(candidates), reasons
        )
    if p := best(RelationType.SAME_PLAYBOOK):
        reasons += p.relation.reasons
        labels = groups.playbooks_of.get(p.view_id) or [f"p:{p.view_id}"]
        return Assignment(
            view_id,
            Outcome.NEW_CAMPAIGN_KNOWN_PLAYBOOK,
            labels[0],
            related,
            len(candidates),
            reasons,
        )
    seen = [
        x for items in related.values() for x in items if x.relation.coverage >= config.min_coverage
    ]
    if seen:
        reasons.append(
            f"compared with {len(seen)} candidates at adequate coverage; no tier reached its threshold"
        )
        return Assignment(view_id, Outcome.NOVEL, None, related, len(candidates), reasons)
    reasons.append("no candidate could be compared at adequate coverage")
    return Assignment(view_id, Outcome.INSUFFICIENT, None, related, len(candidates), reasons)


def assign_incident(
    store: Store,
    incident_id: str,
    groups: Groups | None = None,
    config: FusionConfig = DEFAULT,
    prepared: bool = False,
) -> tuple[Assignment | None, list[Assignment]]:
    """Per-view assignments and the folder's headline: its strongest outcome."""
    if not prepared:
        prepare(store, config)
    groups = groups or build_groups(store, config, funnel=False)
    per_view = [
        assign(store, v.view_id, groups, config, exclude_incident=True, prepared=True)
        for v in store.views(incident_id)
    ]
    headline = min(per_view, key=lambda a: ORDER[a.outcome], default=None)
    return headline, per_view
