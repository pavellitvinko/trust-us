"""Pattern discovery: embed technique cards and cluster them.

Uses the embedding model to vectorize technique card descriptions, then clusters
using k-means or similar. Clusters are then sent to the induction model.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

import numpy as np
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

from ..config import Settings
from ..contracts import Observation, State
from .library import ClusterResult, TechniqueCardForClustering

log = logging.getLogger(__name__)


def embed_text(text: str, model_name: str, settings: Settings | None = None) -> list[float] | None:
    """Embed a text string using the configured embedding model.

    Args:
        text: text to embed
        model_name: "embed" for the main embedder
        settings: app config (optional for testing)

    Returns:
        Embedding vector, or None if the model is not configured.
    """
    # TODO: integrate with the actual embedding API (Google Gemini or similar)
    # For now, return a mock embedding based on text hash for deterministic testing
    import hashlib

    h = hashlib.sha256(text.encode()).digest()
    seed = int.from_bytes(h[:8], "big")
    rng = np.random.RandomState(seed % 2**31)
    return rng.randn(768).tolist()


def gather_technique_cards(
    store,
    view_ids: list[str] | None = None,
) -> list[TechniqueCardForClustering]:
    """Gather all technique cards from observations.

    Args:
        store: data store
        view_ids: if provided, only cards from these views; else all cards

    Returns:
        List of cards ready for embedding.
    """
    cards = []

    if view_ids is None:
        # Get all views
        all_views = []
        for incident in store.incidents():
            all_views.extend(store.views(incident.incident_id))
        view_ids = [v.view_id for v in all_views]

    for view_id in view_ids:
        # Find all technique card observations for this view
        obs_list = store.observations(view_id)
        for obs in obs_list:
            if obs.namespace == "technique.card" and obs.state == State.PRESENT:
                # Parse the card JSON from the observation value
                try:
                    if isinstance(obs.value, dict):
                        card = obs.value
                    else:
                        import json

                        card = json.loads(obs.value) if isinstance(obs.value, str) else obs.value

                    card_name = card.get("name", "Unknown")
                    card_description = card.get("description", "")
                    # Get the view's incident_id
                    view = store.get_view(view_id)
                    incident = store.get_incident(view.incident_id)

                    cards.append(
                        TechniqueCardForClustering(
                            card_name=card_name,
                            card_description=card_description,
                            view_id=view_id,
                            incident_id=view.incident_id,
                            source_text=card_description,
                        )
                    )
                except Exception as e:
                    log.warning(f"error parsing technique card for {view_id}: {e}")
                    continue

    return cards


def cluster_cards(
    cards: list[TechniqueCardForClustering],
    embeddings: list[list[float]] | None = None,
    n_clusters: int | None = None,
) -> list[ClusterResult]:
    """Cluster technique cards by embedding similarity.

    Args:
        cards: technique cards to cluster
        embeddings: pre-computed embeddings (one per card); if None, computed here
        n_clusters: number of clusters; if None, estimated from card count

    Returns:
        List of ClusterResult objects with cards grouped by cluster.
    """
    if not cards:
        log.warning("no cards to cluster")
        return []

    if embeddings is None:
        embeddings = []
        for card in cards:
            # For testing, use a deterministic mock embedding
            embedding = embed_text(card.card_description, "embed", Settings("", "", "", "", "", ""))
            if embedding is None:
                log.error(f"failed to embed card: {card.card_name}")
                return []
            embeddings.append(embedding)

    embeddings_array = np.array(embeddings, dtype=np.float32)

    # Estimate cluster count if not provided
    if n_clusters is None:
        # Heuristic: aim for ~3-5 cards per cluster
        n_clusters = max(2, min(len(cards) // 4, 20))
        log.info(f"estimated {n_clusters} clusters for {len(cards)} cards")

    # Normalize embeddings
    scaler = StandardScaler()
    embeddings_scaled = scaler.fit_transform(embeddings_array)

    # K-means clustering
    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = kmeans.fit_predict(embeddings_scaled)

    # Group cards by cluster
    clusters = []
    for cluster_id in range(n_clusters):
        mask = labels == cluster_id
        cluster_cards = [cards[i] for i in range(len(cards)) if mask[i]]
        cluster_embeddings = embeddings_array[mask].tolist()

        if cluster_cards:
            # Centroid is the mean of cluster embeddings
            centroid = np.mean(cluster_embeddings, axis=0).tolist()
            clusters.append(
                ClusterResult(
                    cluster_id=cluster_id,
                    cards=cluster_cards,
                    centroid=centroid,
                )
            )

    log.info(f"clustered {len(cards)} cards into {len(clusters)} clusters")
    return sorted(clusters, key=lambda c: len(c.cards), reverse=True)


def cluster_cards_reproducible(
    cards: list[TechniqueCardForClustering],
    seed: int = 42,
    n_clusters: int | None = None,
) -> list[ClusterResult]:
    """Cluster with guaranteed reproducibility across runs.

    Uses deterministic embedding and k-means to ensure two runs on
    shuffled input (same card set) produce identical clusters.

    Args:
        cards: technique cards
        seed: random seed for k-means
        n_clusters: number of clusters

    Returns:
        Clustered results (order stable across runs with same input).
    """
    # Ensure consistent ordering despite possible shuffling
    sorted_cards = sorted(cards, key=lambda c: (c.incident_id, c.view_id, c.card_name))

    embeddings = []
    for card in sorted_cards:
        # Deterministic mock embedding based on card content
        import hashlib

        h = hashlib.sha256(
            (card.incident_id + "|" + card.card_name + "|" + card.card_description).encode()
        ).digest()
        seed_val = int.from_bytes(h[:8], "big")
        rng = np.random.RandomState(seed_val % 2**31)
        embeddings.append(rng.randn(768).tolist())

    clusters = cluster_cards(sorted_cards, embeddings=embeddings, n_clusters=n_clusters)

    # Sort clusters by size (descending) for reproducibility
    return sorted(clusters, key=lambda c: (len(c.cards), c.cluster_id), reverse=True)
