"""Grouping at three levels (campaign, family, playbook) and open-set assignment. Method track."""

from .levels import Groups, build_groups, merge_guard

__all__ = ["Groups", "build_groups", "merge_guard"]
