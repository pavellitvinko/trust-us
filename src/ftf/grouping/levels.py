"""Grouping at three levels from stored relations (docs/architecture.md section 10).

Campaign: connected components over duplicate and same-campaign edges, with a merge guard —
an edge merges two components only if it rests on an identity group or on two independent
artifact groups (fusion already requires this; the guard makes it explicit and survives a
looser config). Family: connected components over campaigns joined by same-family edges.
Playbook: average-linkage agglomerative clustering on 1 - playbook tier score, cut at
1 - threshold; a view may carry several labels.

Groups are recomputed from relations on demand; nothing is stored (the frozen store has no
groups table yet).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from typing import TYPE_CHECKING

from ..contracts import Relation, RelationType, Tier
from ..fusion.config import DEFAULT, FusionConfig

if TYPE_CHECKING:
    from ..store import Store


class UnionFind:
    def __init__(self, items):
        self.parent = {i: i for i in items}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if rb < ra:
            ra, rb = rb, ra
        self.parent[rb] = ra
        return True

    def groups(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = defaultdict(list)
        for x in self.parent:
            out[self.find(x)].append(x)
        return {k: sorted(v) for k, v in out.items()}


@dataclass
class Groups:
    campaign_of: dict[str, str]  # view -> campaign id
    campaigns: dict[str, list[str]]  # campaign id -> views
    family_of: dict[str, str]  # campaign id -> family id
    families: dict[str, list[str]]  # family id -> campaign ids
    playbooks_of: dict[str, list[str]] = field(default_factory=dict)  # view -> playbook ids
    playbooks: dict[str, list[str]] = field(default_factory=dict)  # playbook id -> views
    rejected_merges: list[tuple[str, str, str]] = field(default_factory=list)
    funnel_edges: dict = field(default_factory=dict)  # incident -> (funnel, co_captured)

    def family_of_view(self, view_id: str) -> str | None:
        c = self.campaign_of.get(view_id)
        return self.family_of.get(c) if c else None

    def views_in_family(self, family_id: str) -> list[str]:
        return sorted(v for c in self.families.get(family_id, []) for v in self.campaigns[c])


def merge_guard(r: Relation, config: FusionConfig = DEFAULT) -> bool:
    """One identity group, or two independent artifact groups, or an anchored duplicate."""
    if r.type is RelationType.DUPLICATE:
        return True
    if r.type is not RelationType.SAME_CAMPAIGN:
        return False
    if r.tier_scores.get(Tier.IDENTITY.value, 0.0) >= config.identity:
        return True
    return r.tier_scores.get(Tier.ARTIFACT.value, 0.0) >= config.artifact and any(
        x.startswith("artifact") for x in r.reasons
    )


def build_campaigns(
    view_ids: list[str],
    relations: list[Relation],
    config: FusionConfig = DEFAULT,
    funnel: list[tuple[str, str]] = (),
):
    """Union-find over duplicate and same-campaign edges, strongest first, with two guards:
    the edge itself must rest on identity or two artifact groups, and the two components
    being joined must not contain a contradicting pair (A-B and B-C may each look like one
    campaign while A and C declare different campaign records; then B joins the stronger
    side and the other merge is refused). Funnel unions go through the same check."""
    uf = UnionFind(view_ids)
    rejected: list[tuple[str, str, str]] = []
    contradicting = {
        (r.view_a, r.view_b)
        for r in relations
        if any(x.startswith("contradiction") for x in r.reasons)
    }

    def members(root: str) -> list[str]:
        return [v for v in uf.parent if uf.find(v) == root]

    def safe_union(a: str, b: str) -> bool:
        ra, rb = uf.find(a), uf.find(b)
        if ra == rb:
            return True
        if contradicting:
            for x in members(ra):
                for y in members(rb):
                    if ((x, y) if x <= y else (y, x)) in contradicting:
                        rejected.append((a, b, f"contradiction between the components: {x} vs {y}"))
                        return False
        uf.union(a, b)
        return True

    def strength(r: Relation) -> float:
        bonus = 1.0 if r.type is RelationType.DUPLICATE else 0.0
        return (
            bonus
            + r.tier_scores.get(Tier.IDENTITY.value, 0.0)
            + r.tier_scores.get(Tier.ARTIFACT.value, 0.0)
        )

    edges = [r for r in relations if r.type in (RelationType.DUPLICATE, RelationType.SAME_CAMPAIGN)]
    for r in sorted(edges, key=strength, reverse=True):
        if merge_guard(r, config):
            safe_union(r.view_a, r.view_b)
        else:
            rejected.append((r.view_a, r.view_b, "merge guard: no identity or two artifact groups"))
    for a, b in funnel:  # campaign membership propagates along funnel edges, never co-capture
        if a in uf.parent and b in uf.parent:
            safe_union(a, b)
    comps = uf.groups()
    campaigns = {f"c:{root}": views for root, views in comps.items()}
    campaign_of = {v: cid for cid, views in campaigns.items() for v in views}
    return campaigns, campaign_of, rejected


def build_families(campaigns: dict[str, list[str]], campaign_of: dict[str, str], relations):
    uf = UnionFind(list(campaigns))
    for r in relations:
        if r.type is RelationType.SAME_FAMILY:
            ca, cb = campaign_of.get(r.view_a), campaign_of.get(r.view_b)
            if ca and cb and ca != cb:
                uf.union(ca, cb)
    comps = uf.groups()
    families = {f"f:{root.removeprefix('c:')}": cs for root, cs in comps.items()}
    family_of = {c: fid for fid, cs in families.items() for c in cs}
    return families, family_of


def build_playbooks(
    view_ids: list[str], relations: list[Relation], config: FusionConfig = DEFAULT
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Average-linkage agglomerative clustering on d = 1 - T4 score, cut at 1 - threshold.
    Views with no playbook evidence at all stay unclustered (no label)."""
    t4 = {}
    for r in relations:
        s = r.tier_scores.get(Tier.PLAYBOOK.value, 0.0)
        if s > 0:
            t4[(r.view_a, r.view_b)] = s
    active = sorted({v for pair in t4 for v in pair})
    if not active:
        return {}, {}

    def dist(a: str, b: str) -> float:
        return 1.0 - t4.get(tuple(sorted((a, b))), 0.0)

    clusters: dict[int, list[str]] = {i: [v] for i, v in enumerate(active)}
    cut = 1.0 - config.playbook
    while len(clusters) > 1:
        best = None
        for (i, ci), (j, cj) in combinations(clusters.items(), 2):
            d = sum(dist(a, b) for a in ci for b in cj) / (len(ci) * len(cj))
            if best is None or d < best[0]:
                best = (d, i, j)
        if best is None or best[0] > cut:
            break
        _, i, j = best
        clusters[i] = sorted(clusters[i] + clusters.pop(j))
    playbooks = {f"p:{members[0]}": members for members in clusters.values() if len(members) > 1}
    playbooks_of: dict[str, list[str]] = defaultdict(list)
    for pid, members in playbooks.items():
        for v in members:
            playbooks_of[v].append(pid)
    return playbooks, dict(playbooks_of)


def build_groups(store: Store, config: FusionConfig = DEFAULT, funnel: bool = True) -> Groups:
    from .funnel import run_funnel

    view_ids = [v.view_id for v in store.views()]
    relations = store.relations()
    edges = run_funnel(store) if funnel else {}
    funnel_pairs = [(e.source, e.target) for f, _ in edges.values() for e in f]
    campaigns, campaign_of, rejected = build_campaigns(view_ids, relations, config, funnel_pairs)
    families, family_of = build_families(campaigns, campaign_of, relations)
    playbooks, playbooks_of = build_playbooks(view_ids, relations, config)
    return Groups(
        campaign_of=campaign_of,
        campaigns=campaigns,
        family_of=family_of,
        families=families,
        playbooks_of=playbooks_of,
        playbooks=playbooks,
        rejected_merges=rejected,
        funnel_edges=edges,
    )
