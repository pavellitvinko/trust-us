"""The fusion stage: fit weights, find demotions, compare every pair, store one Relation each.

Runs after `pipeline.run_fingerprint`. It recomputes evidence with the fitted weights and
replaces the stored evidence rows, since the skeleton's all-pairs run compares with base
weights only. `pairs` restricts the work to candidate pairs (grouping/candidates.py);
`campaigns` (view -> campaign id) switches rarity from stand-in domains to real campaigns.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from itertools import combinations
from typing import TYPE_CHECKING

from .. import pipeline
from ..compare.vector import calibrate
from ..compare.weights import weights
from ..contracts import FingerprintKind, Relation
from ..registry import layers, load_plugins
from .config import DEFAULT, FusionConfig
from .demotion import find_demotions
from .fuse import fuse, set_active_groups

if TYPE_CHECKING:
    from ..store import Store


def prepare(
    store: Store, config: FusionConfig = DEFAULT, campaigns: Mapping[str, str] | None = None
) -> None:
    """Fit the weight table and the demotion set for this corpus."""
    load_plugins()
    weights.fit(store, campaigns)
    weights.demoted, _ = find_demotions(store, config)
    set_active_groups({layers.get(f.layer_id).group for f in store.fingerprints()})
    for layer in layers.all():
        if layer.kind is FingerprintKind.VECTOR:
            calibrate(store, layer.name)


def fuse_pair(
    store: Store, view_a: str, view_b: str, config: FusionConfig = DEFAULT, persist: bool = True
) -> Relation:
    evidence = pipeline.compare_views(store, view_a, view_b)
    relation = fuse(view_a, view_b, evidence, config)
    if persist:
        for e in evidence:
            if e.comparable and e.score > 0:
                store.put_evidence(e)
        store.put_relation(relation)
    return relation


def run_method(store: Store, config: FusionConfig = DEFAULT, candidates: bool = False):
    """Fuse, group, then fuse again with rarity counted over the campaigns just found.
    With `candidates`, only indexed candidate pairs are compared in full. Returns the groups."""
    from ..grouping import build_groups

    if candidates:
        from ..grouping.candidates import run_fuse_with_candidates as _run

        def fuse_all(campaigns=None):
            _run(store, config, campaigns)
    else:

        def fuse_all(campaigns=None):
            run_fuse(store, config, campaigns=campaigns)

    fuse_all()
    groups = build_groups(store, config)
    fuse_all(groups.campaign_of)
    return build_groups(store, config)


def run_fuse(
    store: Store,
    config: FusionConfig = DEFAULT,
    pairs: Iterable[tuple[str, str]] | None = None,
    campaigns: Mapping[str, str] | None = None,
) -> int:
    prepare(store, config, campaigns)
    if pairs is None:
        pairs = combinations([v.view_id for v in store.views()], 2)
    count = 0
    for a, b in pairs:
        fuse_pair(store, a, b, config)
        count += 1
    return count
