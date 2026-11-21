"""Gather technique cards, embed them through the gateway and cluster them deterministically."""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np
from sklearn.cluster import AgglomerativeClustering

from ..config import Settings
from ..contracts import State
from .library import ClusterResult, TechniqueCardForClustering

CARD_NAMESPACE = "technique.card"
DISTANCE_THRESHOLD = 0.30  # cosine distance; average linkage


def card_id(view_id: str, name: str) -> str:
    return hashlib.sha256(f"{view_id}|{name}".encode()).hexdigest()[:12]


def gather_technique_cards(
    store, view_ids: list[str] | None = None, campaign_of: dict[str, str] | None = None
) -> list[TechniqueCardForClustering]:
    """Verified cards under the namespace written by `perception.techniques`, in stable order."""
    wanted = None if view_ids is None else set(view_ids)
    cards = []
    for view in store.views():
        if wanted is not None and view.view_id not in wanted:
            continue
        for o in store.observations(view.view_id):
            if o.namespace != CARD_NAMESPACE or o.state is not State.PRESENT:
                continue
            if not isinstance(o.value, dict):
                continue
            name, description = o.value.get("name"), o.value.get("description")
            if not name or not description:
                continue
            cards.append(
                TechniqueCardForClustering(
                    card_name=name,
                    card_description=description,
                    view_id=view.view_id,
                    incident_id=view.incident_id,
                    source_text=description,
                    card_id=card_id(view.view_id, name),
                    campaign_id=(campaign_of or {}).get(view.view_id),
                    evidence=list(o.value.get("evidence") or []),
                )
            )
    return sorted(cards, key=lambda c: c.card_id)


def embed_cards(
    cards: list[TechniqueCardForClustering],
    *,
    settings: Settings | None = None,
    client: Any = None,
) -> tuple[list[TechniqueCardForClustering], list[list[float]], dict[str, int]]:
    """Embedded cards and vectors, plus how many failed for each reason."""
    from ..models import EmbedTask, embed

    results = embed(
        [f"{c.card_name}: {c.card_description}" for c in cards],
        EmbedTask.CLUSTERING,
        settings=settings,
        client=client,
    )
    kept, vectors, failed = [], [], {}
    for card, result in zip(cards, results, strict=True):
        if result.state is State.PRESENT and result.value is not None:
            kept.append(card)
            vectors.append(result.value.vector)
        else:
            reason = result.reason or "error"
            failed[reason] = failed.get(reason, 0) + 1
    return kept, vectors, failed


def _unit(vector: np.ndarray) -> list[float]:
    norm = float(np.linalg.norm(vector))
    return (vector / norm).tolist() if norm else vector.tolist()


def cluster_cards(
    cards: list[TechniqueCardForClustering],
    embeddings: list[list[float]],
    threshold: float = DISTANCE_THRESHOLD,
) -> list[ClusterResult]:
    """Cluster by cosine distance. Input order does not change the result."""
    if not cards:
        return []
    if len(cards) != len(embeddings):
        raise ValueError("one embedding per card is required")
    order = sorted(range(len(cards)), key=lambda i: cards[i].card_id)
    ordered = [cards[i] for i in order]
    matrix = np.array([embeddings[i] for i in order], dtype=np.float64)
    if len(ordered) == 1:
        labels = np.zeros(1, dtype=int)
    else:
        labels = AgglomerativeClustering(
            n_clusters=None,
            metric="cosine",
            linkage="average",
            distance_threshold=threshold,
        ).fit_predict(matrix)
    groups: dict[int, list[int]] = {}
    for index, label in enumerate(labels):
        groups.setdefault(int(label), []).append(index)
    # The first card id names each group, so numbering does not depend on the algorithm's labels.
    members = sorted(groups.values(), key=lambda g: ordered[g[0]].card_id)
    return [
        ClusterResult(
            cluster_id=number,
            cards=[ordered[i] for i in group],
            centroid=_unit(matrix[group].mean(axis=0)),
        )
        for number, group in enumerate(members)
    ]
