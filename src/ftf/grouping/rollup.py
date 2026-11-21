"""Incident roll-up: results are computed per view and summarised per folder, because test
material arrives as folders. An incident may map to several campaigns (folder 2 does)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .funnel import MISMATCH_NS, FunnelEdge
from .levels import Groups

if TYPE_CHECKING:
    from ..store import Store


@dataclass
class IncidentSummary:
    incident_id: str
    campaigns: dict[str, list[str]]  # campaign id -> views of this incident in it
    families: dict[str, list[str]]  # family id -> campaign ids of this incident in it
    funnel: list[FunnelEdge] = field(default_factory=list)
    co_captured: list[FunnelEdge] = field(default_factory=list)
    mismatches: list[dict] = field(default_factory=list)  # funnel.mismatch observations

    @property
    def shared_campaigns(self) -> dict[str, list[str]]:
        """Campaigns of this incident that also hold views from other incidents."""
        return {c: vs for c, vs in self.campaigns.items() if self._foreign.get(c)}

    _foreign: dict[str, list[str]] = field(default_factory=dict, repr=False)


def rollup(
    store: Store,
    groups: Groups,
    edges: dict[str, tuple[list[FunnelEdge], list[FunnelEdge]]] | None = None,
) -> list[IncidentSummary]:
    incident_of = {v.view_id: v.incident_id for v in store.views()}
    out = []
    for incident in store.incidents():
        iid = incident.incident_id
        views = store.views(iid)
        campaigns: dict[str, list[str]] = defaultdict(list)
        for v in views:
            campaigns[groups.campaign_of[v.view_id]].append(v.view_id)
        families: dict[str, list[str]] = defaultdict(list)
        for c in campaigns:
            families[groups.family_of[c]].append(c)
        foreign = {c: [v for v in groups.campaigns[c] if incident_of[v] != iid] for c in campaigns}
        mismatches = [
            o.value
            for v in views
            for o in store.observations(v.view_id)
            if o.namespace == MISMATCH_NS
        ]
        funnel, co = edges.get(iid, ([], [])) if edges else ([], [])
        summary = IncidentSummary(
            incident_id=iid,
            campaigns=dict(campaigns),
            families=dict(families),
            funnel=funnel,
            co_captured=co,
            mismatches=mismatches,
        )
        summary._foreign = foreign
        out.append(summary)
    return out
