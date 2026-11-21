"""Contradictions and the demotion rule (docs/architecture.md section 9).

A contradiction is two different values in an exclusive namespace: two pages that each
declare one campaign record, and the records differ. Any identity-tier value found on both
sides of a contradiction is, by that fact, shared between distinct campaigns, so it is
reclassified to kit tier for the whole corpus. Without this, a new item carrying the
shared key but no visible campaign UUID would attach to both campaigns and fuse them.

Computed from fingerprints, not stored evidence: the all-pairs run keeps only scored rows,
and a contradicting pair may share nothing in its campaign layer.
"""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from typing import TYPE_CHECKING

from ..contracts import Tier
from ..registry import layers, load_plugins
from .config import DEFAULT, FusionConfig

if TYPE_CHECKING:
    from ..store import Store

Key = tuple[str, str]


def identity_items(store: Store) -> dict[str, set[Key]]:
    """Every (namespace, value) in an identity-tier fingerprint, per view."""
    load_plugins()
    identity_layers = {layer.name for layer in layers.all() if layer.tier is Tier.IDENTITY}
    items: dict[str, set[Key]] = defaultdict(set)
    for fp in store.fingerprints():
        if fp.layer_id in identity_layers and isinstance(fp.payload, list):
            items[fp.view_id] |= {(i["ns"], str(i["value"])) for i in fp.payload}
    return items


def exclusive_values(
    items: dict[str, set[Key]], config: FusionConfig = DEFAULT
) -> dict[str, dict[str, set[str]]]:
    """view -> namespace -> values, for exclusive namespaces only."""
    out: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for view_id, keys in items.items():
        for ns, value in keys:
            if ns in config.exclusive_namespaces:
                out[view_id][ns].add(value)
    return out


def contradictions_between(
    a: dict[str, set[str]], b: dict[str, set[str]]
) -> list[tuple[str, set[str], set[str]]]:
    """Namespaces where both sides have values and none agree."""
    out = []
    for ns in a.keys() & b.keys():
        if a[ns] and b[ns] and not (a[ns] & b[ns]):
            out.append((ns, a[ns], b[ns]))
    return out


def find_demotions(
    store: Store, config: FusionConfig = DEFAULT
) -> tuple[set[Key], list[tuple[str, str, str]]]:
    """Return (demoted keys, contradicting pairs as (view_a, view_b, namespace))."""
    items = identity_items(store)
    exclusive = exclusive_values(items, config)
    demoted: set[Key] = set()
    pairs: list[tuple[str, str, str]] = []
    for a, b in combinations(sorted(exclusive), 2):
        found = contradictions_between(exclusive[a], exclusive[b])
        if not found:
            continue
        for ns, _, _ in found:
            pairs.append((a, b, ns))
        shared = items[a] & items[b]
        demoted |= {k for k in shared if k[0] not in config.exclusive_namespaces}
    return demoted, pairs
