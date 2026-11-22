"""Typed fusion: per-layer Evidence in, one Relation out. Method track.

Correlation groups, tier scores, coverage, the contradiction rule and the demotion rule
(docs/architecture.md section 9). Entry points: `run_fuse(store)`, `fuse_pair(store, a, b)`.
"""

from .config import DEFAULT, FusionConfig
from .fuse import decide, explain, fuse, summarise
from .run import fuse_pair, prepare, run_fuse, run_method

__all__ = [
    "DEFAULT",
    "FusionConfig",
    "decide",
    "explain",
    "fuse",
    "fuse_pair",
    "prepare",
    "run_fuse",
    "run_method",
    "summarise",
]
