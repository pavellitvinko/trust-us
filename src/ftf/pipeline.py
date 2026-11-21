"""Run the stages over stored views. Each stage is idempotent: re-running replaces its output."""

from __future__ import annotations

from itertools import combinations

from .contracts import Evidence
from .registry import Context, comparators, extractors, layers, load_plugins
from .store import Store


def run_extract(store: Store, view_ids: list[str] | None = None) -> int:
    load_plugins()
    ctx = Context(store)
    count = 0
    for view in store.views():
        if view_ids is not None and view.view_id not in view_ids:
            continue
        for extractor in extractors.all():
            observations = extractor.run(view, ctx)
            store.put_observations(view.view_id, extractor.name, observations)
            count += len(observations)
    return count


def run_fingerprint(store: Store, view_ids: list[str] | None = None) -> int:
    load_plugins()
    count = 0
    for view in store.views():
        if view_ids is not None and view.view_id not in view_ids:
            continue
        observations = store.observations(view.view_id)
        for layer in layers.all():
            fingerprint = layer.build(view, observations)
            if fingerprint is not None:
                store.put_fingerprint(fingerprint)
                count += 1
    return count


def compare_views(store: Store, view_a: str, view_b: str) -> list[Evidence]:
    """Compare two views on every layer. A layer missing on either side is reported as not comparable."""
    load_plugins()
    fps_a = {f.layer_id: f for f in store.fingerprints(view_id=view_a)}
    fps_b = {f.layer_id: f for f in store.fingerprints(view_id=view_b)}
    out = []
    for layer in layers.all():
        a, b = fps_a.get(layer.name), fps_b.get(layer.name)
        if a is None or b is None:
            out.append(
                Evidence(
                    view_a=view_a,
                    view_b=view_b,
                    layer_id=layer.name,
                    comparable=False,
                    comparator=layer.comparator,
                    version="-",
                )
            )
            continue
        out.append(comparators.get(layer.comparator).compare(a, b))
    return out


def run_compare_all(store: Store) -> int:
    """Brute-force all pairs. Fine at sample size; the Method track replaces it with candidate generation."""
    load_plugins()
    count = 0
    for layer in layers.all():
        comparator = comparators.get(layer.comparator)
        for a, b in combinations(store.fingerprints(layer_id=layer.name), 2):
            evidence = comparator.compare(a, b)
            if evidence.score > 0:
                store.put_evidence(evidence)
                count += 1
    return count
