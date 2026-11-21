"""Evaluation harness: reference pairs, leave-one-out, ablations, degradation tests. Method track."""

from .harness import Report, evaluate, format_report
from .reference import load_reference

__all__ = ["Report", "evaluate", "format_report", "load_reference"]
