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
from ..contracts import FingerprintKind, Relation, RelationType, Tier
from ..fusion.config import DEFAULT, FusionConfig
from ..registry import layers, load_plugins

if TYPE_CHECKING:
    from ..store import Store

Pair = tuple[str, str]


def _pair(a: str, b: str) -> Pair:
    return (a, b) if a <= b else (b, a)


def fingerprint_keys(fp, band_size: int = 4) -> list[tuple]:
    """Index keys of one fingerprint: the items a comparator would actually match on.
    Set items (weight > 0), the page kind of a three-state vector, MinHash bands, and one
    bucket per dense layer. Indexing fingerprints rather than raw observations means a
    layer's transformation (pattern ids without their scores, case-folded offers) is
    indexed the way it is compared."""
    keys: list[tuple] = []
    if fp.kind is FingerprintKind.SET:
        if isinstance(fp.payload, list):
            for item in fp.payload:
                ns, value = item["ns"], str(item["value"])
                if weights.weight(ns, value) > 0:
                    keys.append((fp.layer_id, ns, value))
        elif isinstance(fp.payload, dict) and "kind" in fp.payload:
            keys.append((fp.layer_id, "kind", str(fp.payload["kind"])))
    elif fp.kind is FingerprintKind.HASH and fp.payload.get("algo") == "minhash":
        keys += [(fp.layer_id, "band", key) for key in bands(fp.payload["values"], band_size)]
    else:  # perceptual hashes and vectors: every holder of the layer is a candidate
        keys.append((fp.layer_id, "*"))
    return keys


def build_index(store: Store, band_size: int = 4) -> dict[tuple, set[str]]:
    load_plugins()
    index: dict[tuple, set[str]] = defaultdict(set)
    for fp in store.fingerprints():
        for key in fingerprint_keys(fp, band_size):
            index[key].add(fp.view_id)
    return index


def candidate_pairs(store: Store, band_size: int = 4) -> set[Pair]:
    found: set[Pair] = set()
    for members in build_index(store, band_size).values():
        found |= {_pair(a, b) for a, b in combinations(sorted(members), 2)}
    return found


def cheap_relation(store: Store, a: str, b: str, config: FusionConfig = DEFAULT) -> Relation:
    """The relation of a non-candidate pair, from layer presence alone."""
    groups_a = {layers.get(f.layer_id).group for f in store.fingerprints(view_id=a)}
    groups_b = {layers.get(f.layer_id).group for f in store.fingerprints(view_id=b)}
    from ..fusion.fuse import active_groups

    total = {layer.group for layer in layers.all()}
    if active_groups() is not None:
        total &= active_groups()
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
