"""Typed fusion: per-layer Evidence in, one Relation out. Method track.

Correlation groups, tier scores, coverage, the contradiction rule and the demotion rule
(docs/architecture.md section 9). Entry points: `run_fuse(store)`, `fuse_pair(store, a, b)`.
"""

from .config import DEFAULT, FusionConfig
from .fuse import decide, fuse, summarise
from .run import fuse_pair, prepare, run_fuse

__all__ = [
    "DEFAULT",
    "FusionConfig",
    "decide",
    "fuse",
    "fuse_pair",
    "prepare",
    "run_fuse",
    "summarise",
]
