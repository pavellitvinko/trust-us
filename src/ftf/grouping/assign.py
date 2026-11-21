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

from ..contracts import Relation, RelationType
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
    """Views sharing an index key with this one: identifier values, MinHash bands, dense layers."""
    from .candidates import build_index, fingerprint_keys

    own = store.get_view(view_id)
    index = build_index(store)
    found: set[str] = set()
    for fp in store.fingerprints(view_id=view_id):
        for key in fingerprint_keys(fp):
            found |= index.get(key, set())
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

    from ..fusion.demotion import contradicts_any

    refused_family: str | None = None

    def compatible(match: str) -> str:
        """The campaign holding `match`, or "" if the view contradicts any of its members.

        A match with one member does not override a contradiction with another, whether
        the match is a duplicate or a same-campaign link."""
        nonlocal refused_family
        target = groups.campaign_of.get(match, f"c:{match}")
        members = [m for m in groups.campaigns.get(target, [match]) if m != view_id]
        clash = contradicts_any(store, view_id, members, config)
        if not clash:
            return target
        other, ns = clash[0]
        reasons.append(f"campaign {target} refused: contradicts {other} on {ns}")
        refused_family = refused_family or groups.family_of.get(target) or f"f:{target}"
        return ""

    for d in related[RelationType.DUPLICATE.value]:
        if not compatible(d.view_id):
            continue
        reasons += d.relation.reasons
        return Assignment(
            view_id, Outcome.DUPLICATE_OF, d.view_id, related, len(candidates), reasons
        )

    guarded = [
        x for x in related[RelationType.SAME_CAMPAIGN.value] if merge_guard(x.relation, config)
    ]
    for c in guarded:
        target = compatible(c.view_id)
        if not target:
            continue
        reasons += c.relation.reasons
        return Assignment(
            view_id, Outcome.VARIANT_OF_CAMPAIGN, target, related, len(candidates), reasons
        )
    f = best(RelationType.SAME_FAMILY)
    if f or refused_family:
        if f:
            reasons += f.relation.reasons
        target = (groups.family_of_view(f.view_id) or f"f:{f.view_id}") if f else refused_family
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
    # "Novel" is a separation claim about the whole corpus, so it rests on the same rule fusion
    # uses for one pair: at least one comparison fusion itself called `unrelated` (adequate
    # coverage *and* the identity tier seen on both sides). Coverage alone is not enough: two
    # screenshots whose hashes differ were compared, but nothing that defines a campaign was.
    separated = related[RelationType.UNRELATED.value]
    if separated:
        reasons.append(
            f"compared with {len(separated)} candidates fusion could call unrelated; "
            f"no tier reached its threshold"
        )
        return Assignment(view_id, Outcome.NOVEL, None, related, len(candidates), reasons)
    # No candidate separable. Well-observed material that shares nothing with the corpus is
    # still novel, not insufficient: apply the same rule to the non-candidates from layer
    # presence alone (the comparison would score 0 on every set layer anyway).
    from .candidates import cheap_relation

    own_incident = store.get_view(view_id).incident_id
    others = [
        v.view_id
        for v in store.views()
        if v.view_id != view_id
        and v.view_id not in candidates
        and (not exclude_incident or v.incident_id != own_incident)
    ]
    observable = [
        o
        for o in others
        if cheap_relation(store, view_id, o, config).type is RelationType.UNRELATED
    ]
    if observable:
        reasons.append(
            f"shares no indexed value with {len(observable)} views it is comparable with "
            f"at adequate coverage, identity tier included"
        )
        return Assignment(view_id, Outcome.NOVEL, None, related, len(candidates), reasons)
    if any(x.relation.coverage >= config.min_coverage for items in related.values() for x in items):
        reasons.append(
            "candidates were compared at adequate coverage, but the identity tier was not "
            "comparable on both sides for any of them: the material cannot be called novel"
        )
    else:
        reasons.append("no view could be compared at adequate coverage")
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
