"""Grouping at three levels (campaign, family, playbook), funnel edges, incident roll-up and
open-set assignment. Method track."""

from .assign import Assignment, Outcome, assign, assign_incident, candidate_views
from .funnel import FunnelEdge, funnel_edges, run_funnel
from .levels import Groups, build_groups, merge_guard
from .rollup import IncidentSummary, rollup

__all__ = [
    "Assignment",
    "FunnelEdge",
    "Groups",
    "IncidentSummary",
    "Outcome",
    "assign",
    "assign_incident",
    "build_groups",
    "candidate_views",
    "funnel_edges",
    "merge_guard",
    "rollup",
    "run_funnel",
]
