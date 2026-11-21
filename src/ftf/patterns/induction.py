"""Pattern induction: embed and cluster technique cards, generate library.

Steps:
1. Embed all technique cards using a text model.
2. Cluster using k-means or similar.
3. For each cluster, call the model to write a pattern definition.
4. Identify composite patterns (techniques appearing together).
5. Write the library version and cache it.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..config import Settings
from ..contracts import Observation, View
from ..models import read_structured
from .library import (
    ClusterResult,
    CompositePattern,
    PatternDefinition,
    PatternLibraryStore,
    PatternMetadata,
    TechniqueCardForClustering,
    LibraryVersion,
)

log = logging.getLogger(__name__)

INDUCTION_NAME = "pattern.induction"
INDUCTION_VERSION = "1"
INDUCTION_PROMPT_VERSION = "1"


@dataclass
class PatternDefinitionOutput(BaseModel):
    """Model output for a pattern definition."""

    pattern_id: str
    name: str
    description: str
    positive_examples: list[dict[str, Any]]
    near_misses: list[dict[str, Any]]
    related_patterns: list[str] = []


def generate_pattern_id(cluster_index: int, cluster_names: list[str]) -> str:
    """Create a stable ID from cluster content.

    Format: pattern.<sequence>_<semantic_slug>
    E.g.: pattern.1_celebrity_endorser
    """
    if not cluster_names:
        name_part = "cluster"
    else:
        # Take first meaningful card name, slug it
        name = cluster_names[0].lower().replace(" ", "_")[:20]
        name_part = name
    return f"pattern.{cluster_index}_{name_part}"


def induction_system_prompt() -> str:
    """System prompt for the model's pattern-definition task."""
    return """You are a fraud analyst reading technique cards from suspicious ads and pages.

Each card is one persuasion technique, written by another model in its own words. The same
technique appears multiple times in different cards with different wordings.

Your task: read a cluster of similar cards from multiple views and unrelated domains, then write
a pattern definition that explains what they have in common, what distinguishes this pattern
from similar ones, and what the technique is meant to achieve.

Return a JSON object with:
- pattern_id: a stable identifier (e.g., "pattern.1_celebrity_endorser")
- name: a short name for this pattern in English
- description: one or two sentences explaining what makes this pattern distinct
- positive_examples: list of {card_name, evidence_summary} showing the pattern clearly
- near_misses: list of similar patterns from other clusters you should NOT confuse with this one
- related_patterns: list of pattern IDs that commonly appear together

Be specific. Do not over-generalise. If a cluster has only one or two distinct techniques,
say so—do not invent a pattern."""


def induction_prompt(cluster: ClusterResult, all_cluster_names: list[str]) -> str:
    """Create a prompt for the induction model on one cluster."""
    unique_names = cluster.card_names()
    examples = "\n".join(
        f"  - {name}: {next((c.card_description for c in cluster.cards if c.card_name == name), '')}"
        for name in unique_names[:5]
    )

    all_names_str = ", ".join(all_cluster_names[:10])
    if len(all_cluster_names) > 10:
        all_names_str += f", ... ({len(all_cluster_names)} total)"

    return f"""Cluster with {len(cluster.cards)} technique cards from {len(set(c.view_id for c in cluster.cards))} views:

{examples}

This cluster's pattern name and evidence suggest a technique that might be related to:
{all_names_str}

Describe the core technique this cluster represents. What would you name it? How does it differ
from the other techniques listed above?"""


async def run_induction(
    settings: Settings,
    store,
    clusters: list[ClusterResult],
    model_name: str = "text",
) -> LibraryVersion:
    """Generate a pattern library version from clustered technique cards.

    Args:
        settings: app config with cache and model settings
        store: data store with technique card observations
        clusters: list of technique card clusters from embedding
        model_name: which model to use for definitions ("text" uses settings.text_model_id)

    Returns:
        A new LibraryVersion with pattern definitions and composites.
    """
    version_id = f"v1.{len(Path(settings.cache_dir).glob('pattern.induction/**/*.json'))}._induction-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    all_cluster_names = []
    for cluster in clusters:
        all_cluster_names.extend(cluster.card_names())

    patterns = []
    for idx, cluster in enumerate(clusters):
        pattern_id = generate_pattern_id(idx, cluster.card_names())
        log.info(f"inducing pattern {pattern_id} from cluster {idx} with {len(cluster.cards)} cards")

        prompt = induction_prompt(cluster, all_cluster_names)

        try:
            result = await read_structured(
                cache_dir=settings.cache_dir,
                task=INDUCTION_NAME,
                prompt_version=INDUCTION_PROMPT_VERSION,
                model=settings.text_model_id,
                system_prompt=induction_system_prompt(),
                user_prompt=prompt,
                schema=PatternDefinitionOutput,
                instance_key=pattern_id,
            )

            if result.state != "present" or result.value is None:
                log.warning(f"pattern induction failed for {pattern_id}: {result.reason}")
                continue

            output = result.value
            patterns.append(
                PatternDefinition(
                    pattern_id=output.pattern_id,
                    name=output.name,
                    description=output.description,
                    positive_examples=output.positive_examples,
                    near_misses=output.near_misses,
                    related_patterns=output.related_patterns,
                    metadata=PatternMetadata(
                        created_in_version=version_id,
                        last_seen_in_version=version_id,
                    ),
                )
            )

        except Exception as e:
            log.error(f"error inducing pattern {pattern_id}: {e}")
            continue

    # Identify composite patterns: techniques that co-occur often
    composites = _identify_composites(patterns, clusters, store)

    # Compute input hash for reproducibility checks
    input_hash = _compute_input_hash(clusters)

    version = LibraryVersion(
        version_id=version_id,
        created_at=datetime.now().isoformat(),
        patterns=patterns,
        composites=composites,
        input_hash=input_hash,
        model_name=model_name,
    )

    return version


def _compute_input_hash(clusters: list[ClusterResult]) -> str:
    """Hash cluster composition and order for reproducibility checks."""
    blob = json.dumps(
        [
            {
                "centroid": cluster.centroid[:10],  # sample
                "card_names": cluster.card_names(),
            }
            for cluster in clusters
        ],
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _identify_composites(
    patterns: list[PatternDefinition],
    clusters: list[ClusterResult],
    store,
) -> list[CompositePattern]:
    """Identify patterns that frequently appear together.

    Heuristic: scan views that matched multiple patterns, record co-occurrence counts.
    When two or more patterns co-occur in >1 views, propose a composite.
    """
    composites = []
    # TODO: implement co-occurrence analysis when we have pattern matching working
    return composites
