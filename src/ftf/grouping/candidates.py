"""Candidate generation instead of all-pairs comparison (docs/architecture.md section 9).

Two indexes decide which pairs are worth a full comparison: the inverted index from each
identifier value to the views holding it (`store.views_with_value`, weight > 0 only, so
stoplisted values generate nothing) and LSH bands over MinHash signatures. Perceptual-hash
and vector layers are small enough at this scale that every pair holding them is a
candidate; a vector index and hash-prefix buckets slot in here later without touching
the callers.

A pair that is not a candidate shares no indexed value, so its set layers score 0 and its
relation can only be *unrelated* or *insufficient evidence*; that relation is written
without a comparison, from layer presence alone, so downstream code sees a complete table.
"""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from typing import TYPE_CHECKING

from ..compare.minhash import bands
from ..compare.weights import weights
from ..contracts import FingerprintKind, Purpose, Relation, RelationType, State, Tier
from ..fusion.config import DEFAULT, FusionConfig
from ..registry import layers, load_plugins

if TYPE_CHECKING:
    from ..store import Store

Pair = tuple[str, str]


def _pair(a: str, b: str) -> Pair:
    return (a, b) if a <= b else (b, a)


def candidate_pairs(store: Store, band_size: int = 4) -> set[Pair]:
    load_plugins()
    views = store.views()
    found: set[Pair] = set()

    # identifier index
    holders: dict[tuple[str, str], set[str]] = defaultdict(set)
    for v in views:
        for o in store.observations(v.view_id):
            if o.state is not State.PRESENT or o.purpose is Purpose.RISK or o.value is None:
                continue
            key = (o.namespace, str(o.value))
            if weights.weight(*key) > 0:
                holders[key].add(v.view_id)
    for members in holders.values():
        found |= {_pair(a, b) for a, b in combinations(sorted(members), 2)}

    # MinHash bands, perceptual hashes, vectors
    by_band: dict[tuple[str, str], set[str]] = defaultdict(set)
    dense: dict[str, set[str]] = defaultdict(set)
    for fp in store.fingerprints():
        if fp.kind is FingerprintKind.HASH and fp.payload.get("algo") == "minhash":
            for key in bands(fp.payload["values"], band_size):
                by_band[(fp.layer_id, key)].add(fp.view_id)
        elif fp.kind in (FingerprintKind.HASH, FingerprintKind.VECTOR):
            dense[fp.layer_id].add(fp.view_id)
    for members in list(by_band.values()) + list(dense.values()):
        found |= {_pair(a, b) for a, b in combinations(sorted(members), 2)}
    return found


def cheap_relation(store: Store, a: str, b: str, config: FusionConfig = DEFAULT) -> Relation:
    """The relation of a non-candidate pair, from layer presence alone."""
    groups_a = {layers.get(f.layer_id).group for f in store.fingerprints(view_id=a)}
    groups_b = {layers.get(f.layer_id).group for f in store.fingerprints(view_id=b)}
    total = {layer.group for layer in layers.all()}
    coverage = len(groups_a & groups_b) / len(total) if total else 0.0
    rtype = RelationType.UNRELATED if coverage >= config.min_coverage else RelationType.INSUFFICIENT
    return Relation(
        view_a=a,
        view_b=b,
        type=rtype,
        confidence="high"
        if rtype is RelationType.UNRELATED and coverage >= 0.6
        else ("low" if rtype is RelationType.UNRELATED else ""),
        coverage=round(coverage, 4),
        tier_scores={t.value: 0.0 for t in (Tier.IDENTITY, Tier.ARTIFACT, Tier.KIT, Tier.PLAYBOOK)},
        reasons=["not a candidate: the two views share no indexed value, band or dense layer"],
    )


def run_fuse_with_candidates(
    store: Store, config: FusionConfig = DEFAULT, campaigns=None
) -> tuple[int, int]:
    """Full comparison for candidates, cheap relation for the rest. Returns (compared, skipped)."""
    from ..fusion.run import prepare, run_fuse

    prepare(store, config, campaigns)
    cands = candidate_pairs(store)
    compared = run_fuse(store, config, pairs=sorted(cands), campaigns=campaigns)
    skipped = 0
    for a, b in combinations([v.view_id for v in store.views()], 2):
        if _pair(a, b) not in cands:
            store.put_relation(cheap_relation(store, a, b, config))
            skipped += 1
    return compared, skipped
