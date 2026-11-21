"""Pattern discovery: observation, induction, matching, novelty (architecture section 6). Perception track.

This module implements architecture section 6:
- Step 1: Technique card observation (runs in perception.techniques module)
- Step 2: Induction — cluster cards, generate pattern library (induction.py)
- Step 3: Matching — judge each view against library patterns (matching.py)
- Step 4: Novelty — track candidates, promote when seen twice independently (novelty.py)

The library is versioned with stable IDs; merges and splits are recorded.
Pattern evidence is playbook-tier (T4) only; patterns cannot drive campaign grouping directly.
"""

from .library import (
    PatternDefinition,
    PatternLibraryStore,
    LibraryVersion,
    TechniqueCardForClustering,
    ClusterResult,
)
from .clustering import gather_technique_cards, cluster_cards, cluster_cards_reproducible
from .induction import run_induction
from .matching import judge_patterns_for_view, ViewPatternVector

__all__ = [
    "PatternDefinition",
    "PatternLibraryStore",
    "LibraryVersion",
    "TechniqueCardForClustering",
    "ClusterResult",
    "gather_technique_cards",
    "cluster_cards",
    "cluster_cards_reproducible",
    "run_induction",
    "judge_patterns_for_view",
    "ViewPatternVector",
]
