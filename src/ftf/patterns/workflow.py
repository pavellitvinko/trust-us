"""The executable pattern stage: technique cards -> library -> `pattern.score` observations."""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any

from ..config import Settings, get_settings
from ..registry import Context
from .clustering import cluster_cards, embed_cards, gather_technique_cards
from .induction import run_induction
from .library import PatternLibraryStore
from .matching import run_matching
from .novelty import NoveltyStore


def _dirs(settings: Settings) -> tuple[PatternLibraryStore, NoveltyStore]:
    base = settings.data_dir / "patterns"
    return PatternLibraryStore(base / "library"), NoveltyStore(base / "novelty")


def _campaigns(ctx: Context) -> dict[str, str] | None:
    """Independent campaigns come from stored relations. Without them nothing can be promoted."""
    if not ctx.store.relations():
        return None
    from ..grouping import build_groups

    return build_groups(ctx.store).campaign_of


def run_discover(
    ctx: Context,
    view_ids: list[str] | None = None,
    *,
    settings: Settings | None = None,
    client: Any = None,
    on_match: Callable[[str, dict | None], None] | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    campaign_of = _campaigns(ctx)
    if campaign_of is None:
        return {"reason": "no_groups: run `ftf run` first"}
    cards = gather_technique_cards(ctx.store, view_ids, campaign_of=campaign_of)
    if not cards:
        return {"reason": "no_cards: run `ftf perceive` first"}
    kept, vectors, failed = embed_cards(cards, settings=settings, client=client)
    if failed:
        return {"reason": f"embedding_failed: {failed}", "cards": len(cards)}
    clusters = cluster_cards(kept, vectors)
    library, novelty = _dirs(settings)
    induced = run_induction(library, clusters, settings=settings, client=client)
    summary: dict[str, Any] = {"cards": len(cards), "clusters": len(clusters)}
    if induced.version is None:
        summary["reason"] = induced.reason
        return summary
    if not induced.unchanged:
        library.save_version(induced.version)
    summary |= novelty.sync(induced.candidates, induced.library_card_ids)
    summary |= {
        "version": induced.version.version_id,
        "patterns": len(induced.version.patterns),
        "composites": len(induced.version.composites),
    }
    match_options = {"on_view": on_match} if on_match else {}
    summary["matching"] = run_matching(
        ctx, induced.version, view_ids, settings=settings, client=client, **match_options
    )
    return summary


def run_match(
    ctx: Context,
    view_ids: list[str] | None = None,
    *,
    settings: Settings | None = None,
    client: Any = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    library, _ = _dirs(settings)
    version = library.latest_version()
    if version is None:
        return {"reason": "no_library: run `ftf patterns discover` first"}
    return {
        "version": version.version_id,
        "matching": run_matching(ctx, version, view_ids, settings=settings, client=client),
    }


def library_rows(settings: Settings | None = None) -> list[str]:
    library, novelty = _dirs(settings or get_settings())
    version = library.latest_version()
    if version is None:
        return ["no pattern library yet"]
    rows = [f"library {version.version_id}: {len(version.patterns)} patterns"]
    rows += [f"{p.pattern_id}  {p.name}: {p.description}" for p in version.patterns]
    rows += [f"{c.composite_id}  {c.name} ({c.frequency} views)" for c in version.composites]
    rows.append(f"candidates: {len(novelty.candidates())}")
    return rows


def run_stability(
    ctx: Context, *, seeds: tuple[int, int] = (1, 2), settings: Settings | None = None
) -> dict[str, Any]:
    """Cluster the same cards in two shuffled orders and compare the partitions."""
    from sklearn.metrics import adjusted_rand_score

    settings = settings or get_settings()
    campaign_of = _campaigns(ctx)
    if campaign_of is None:
        return {"reason": "no_groups"}
    cards = gather_technique_cards(ctx.store, campaign_of=campaign_of)
    kept, vectors, failed = embed_cards(cards, settings=settings) if cards else ([], [], {})
    if failed or len(kept) < 2:
        return {"reason": f"not_comparable: cards={len(kept)} failed={failed}"}

    def partition(seed: int) -> dict[str, int]:
        order = list(range(len(kept)))
        random.Random(seed).shuffle(order)
        clusters = cluster_cards([kept[i] for i in order], [vectors[i] for i in order])
        return {card.card_id: c.cluster_id for c in clusters for card in c.cards}

    first, second = partition(seeds[0]), partition(seeds[1])
    ids = sorted(first)
    return {
        "cards": len(ids),
        "clusters": [len(set(first.values())), len(set(second.values()))],
        "adjusted_rand_index": round(
            float(adjusted_rand_score([first[i] for i in ids], [second[i] for i in ids])), 6
        ),
    }
