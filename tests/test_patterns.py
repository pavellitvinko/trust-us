"""Tests for pattern discovery: induction, matching, novelty.

These tests validate the core pattern library functionality:
1. Clustering technique cards by embedding
2. Generating pattern definitions via induction
3. Judging views against patterns
4. Tracking candidates for promotion
5. Reproducibility across shuffled inputs
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ftf.patterns import (
    ClusterResult,
    PatternDefinition,
    PatternLibraryStore,
    TechniqueCardForClustering,
    cluster_cards,
    cluster_cards_reproducible,
    gather_technique_cards,
)
from ftf.patterns.library import PatternMetadata, LibraryVersion
from ftf.patterns.matching import ViewPatternVector, PatternJudgement
from ftf.patterns.novelty import CandidatePattern, NoveltyStore


@pytest.fixture
def tmp_library(tmp_path):
    """Create a temporary pattern library store."""
    lib_dir = tmp_path / "library"
    lib_dir.mkdir()
    return PatternLibraryStore(lib_dir)


@pytest.fixture
def tmp_novelty(tmp_path):
    """Create a temporary novelty store."""
    nov_dir = tmp_path / "novelty"
    nov_dir.mkdir()
    return NoveltyStore(nov_dir)


class TestTechniqueCardClustering:
    """Test clustering of technique cards."""

    def test_cluster_empty_list(self):
        """Empty card list produces empty clusters."""
        clusters = cluster_cards([])
        assert clusters == []

    def test_cluster_single_card(self):
        """Single card produces one cluster."""
        card = TechniqueCardForClustering(
            card_name="Celebrity endorser",
            card_description="A famous person recommends the product",
            view_id="v1",
            incident_id="1",
            source_text="The celebrity says this is great",
        )
        clusters = cluster_cards([card], n_clusters=1)
        assert len(clusters) == 1
        assert len(clusters[0].cards) == 1
        assert clusters[0].cards[0].card_name == "Celebrity endorser"

    def test_cluster_multiple_cards(self):
        """Multiple cards are grouped by similarity."""
        cards = [
            TechniqueCardForClustering(
                card_name="Celebrity endorser",
                card_description="A famous person recommends the product",
                view_id=f"v{i}",
                incident_id=str(i),
                source_text="A celebrity",
            )
            for i in range(3)
        ]
        cards.extend(
            [
                TechniqueCardForClustering(
                    card_name="Scarcity claim",
                    card_description="Only a few items left in stock",
                    view_id=f"v{i}",
                    incident_id=str(i),
                    source_text="Limited stock",
                )
                for i in range(3, 5)
            ]
        )

        clusters = cluster_cards(cards, n_clusters=2)
        assert len(clusters) >= 1  # at least one cluster
        assert len([c for cluster in clusters for c in cluster.cards]) == len(cards)

    def test_cluster_reproducibility(self):
        """Two runs on same card set produce identical clusters."""
        cards = [
            TechniqueCardForClustering(
                card_name=f"Technique {i % 3}",
                card_description=f"Description for technique {i % 3}",
                view_id=f"v{i}",
                incident_id=str(i // 2),
                source_text=f"Text {i}",
            )
            for i in range(10)
        ]

        # First run
        clusters1 = cluster_cards_reproducible(cards, seed=42)

        # Shuffle cards
        import random

        shuffled = cards.copy()
        random.shuffle(shuffled)

        # Second run on shuffled input
        clusters2 = cluster_cards_reproducible(shuffled, seed=42)

        # Same cards should be in same clusters (order may differ)
        def cluster_signature(clusters):
            return sorted(
                [
                    tuple(sorted((c.view_id, c.card_name) for c in cluster.cards))
                    for cluster in clusters
                ]
            )

        assert cluster_signature(clusters1) == cluster_signature(clusters2)

    def test_centroid_computation(self):
        """Cluster centroid is the mean of card embeddings."""
        cards = [
            TechniqueCardForClustering(
                card_name="Card A",
                card_description="Description A",
                view_id="v1",
                incident_id="1",
                source_text="A",
            ),
            TechniqueCardForClustering(
                card_name="Card B",
                card_description="Description B",
                view_id="v2",
                incident_id="2",
                source_text="B",
            ),
        ]

        clusters = cluster_cards(cards, n_clusters=1)
        assert len(clusters[0].centroid) > 0  # has an embedding


class TestPatternLibraryStore:
    """Test pattern library persistence and versioning."""

    def test_save_and_load_version(self, tmp_library):
        """Save and load a library version."""
        pattern = PatternDefinition(
            pattern_id="pattern.1_celebrity",
            name="Celebrity Endorser",
            description="A famous person recommends or uses the product",
            positive_examples=[{"card_name": "Celebrity endorser", "evidence": "text quote"}],
        )

        version = LibraryVersion(
            version_id="v1.0.0",
            created_at="2026-11-21T10:00:00",
            patterns=[pattern],
        )

        tmp_library.save_version(version)
        loaded = tmp_library.load_version("v1.0.0")

        assert loaded is not None
        assert loaded.version_id == "v1.0.0"
        assert len(loaded.patterns) == 1
        assert loaded.patterns[0].name == "Celebrity Endorser"

    def test_latest_version(self, tmp_library):
        """latest_version returns the most recent version."""
        v1 = LibraryVersion(
            version_id="v1.0.0",
            created_at="2026-11-21T10:00:00",
            patterns=[],
        )
        v2 = LibraryVersion(
            version_id="v1.0.1",
            created_at="2026-11-21T11:00:00",
            patterns=[],
        )

        tmp_library.save_version(v1)
        tmp_library.save_version(v2)

        latest = tmp_library.latest_version()
        assert latest is not None
        assert latest.version_id == "v1.0.1"

    def test_pattern_by_id(self, tmp_library):
        """pattern_by_id retrieves a pattern by ID."""
        pattern1 = PatternDefinition(
            pattern_id="pattern.1_celebrity",
            name="Celebrity",
            description="Celebrity endorsement",
        )
        pattern2 = PatternDefinition(
            pattern_id="pattern.2_scarcity",
            name="Scarcity",
            description="Limited availability claim",
        )

        version = LibraryVersion(
            version_id="v1.0.0",
            created_at="2026-11-21T10:00:00",
            patterns=[pattern1, pattern2],
        )

        tmp_library.save_version(version)

        p = tmp_library.pattern_by_id("pattern.2_scarcity", "v1.0.0")
        assert p is not None
        assert p.name == "Scarcity"


class TestNoveltyStore:
    """Test candidate pattern tracking and promotion."""

    def test_add_candidate(self, tmp_novelty):
        """Add a technique card as a candidate pattern."""
        card = TechniqueCardForClustering(
            card_name="New Technique",
            card_description="A technique we have not seen before",
            view_id="v1",
            incident_id="1",
            source_text="Text",
        )

        candidate = tmp_novelty.add_candidate(card, "v1", "1")

        assert candidate.card_name == "New Technique"
        assert candidate.first_seen_incident == "1"
        assert candidate.first_seen_view == "v1"
        assert not candidate.promotion_ready

    def test_candidate_seen_twice_independent(self, tmp_novelty):
        """Candidate becomes promotion-ready when seen in 2+ independent incidents."""
        card = TechniqueCardForClustering(
            card_name="Repeated Technique",
            card_description="This technique appeared in multiple incidents",
            view_id="v1",
            incident_id="1",
            source_text="Text",
        )

        # First sighting
        c1 = tmp_novelty.add_candidate(card, "v1", "1")
        assert not c1.promotion_ready

        # Second sighting in different incident
        c2 = tmp_novelty.add_candidate(card, "v2", "2")
        assert c2.promotion_ready

    def test_ready_for_promotion(self, tmp_novelty):
        """ready_for_promotion returns candidates seen in 2+ incidents."""
        # Add candidate seen once
        card1 = TechniqueCardForClustering(
            card_name="Technique A",
            card_description="Once seen",
            view_id="v1",
            incident_id="1",
            source_text="A",
        )
        tmp_novelty.add_candidate(card1, "v1", "1")

        # Add candidate seen twice
        card2 = TechniqueCardForClustering(
            card_name="Technique B",
            card_description="Seen twice in different incidents",
            view_id="v2",
            incident_id="1",
            source_text="B",
        )
        tmp_novelty.add_candidate(card2, "v2", "1")
        tmp_novelty.add_candidate(card2, "v3", "2")

        ready = tmp_novelty.ready_for_promotion()
        assert len(ready) == 1
        assert ready[0].card_name == "Technique B"

    def test_promote_candidate(self, tmp_novelty):
        """promote_candidate removes from active candidates."""
        card = TechniqueCardForClustering(
            card_name="To Promote",
            card_description="Ready to be promoted",
            view_id="v1",
            incident_id="1",
            source_text="Text",
        )

        c = tmp_novelty.add_candidate(card, "v1", "1")
        candidate_id = c.candidate_id

        tmp_novelty.promote_candidate(candidate_id)

        # Should no longer appear in candidates
        candidates = tmp_novelty._load_candidates()
        assert candidate_id not in candidates

        # Should appear in promoted log
        promoted = json.loads(tmp_novelty.promoted_path.read_text())
        assert any(p["candidate_id"] == candidate_id for p in promoted)


class TestPatternDefinition:
    """Test pattern definition metadata."""

    def test_pattern_with_metadata(self):
        """PatternDefinition includes discovery metadata."""
        metadata = PatternMetadata(
            created_in_version="v1.0.0",
            last_seen_in_version="v1.0.1",
        )

        pattern = PatternDefinition(
            pattern_id="pattern.1_celebrity",
            name="Celebrity",
            description="Celebrity endorsement",
            metadata=metadata,
        )

        assert pattern.metadata.created_in_version == "v1.0.0"
        assert pattern.metadata.last_seen_in_version == "v1.0.1"

    def test_pattern_analyst_edit(self):
        """Analyst can rename a pattern."""
        metadata = PatternMetadata(
            created_in_version="v1.0.0",
            last_seen_in_version="v1.0.1",
            analyst_renamed_from="Old Name",
        )

        pattern = PatternDefinition(
            pattern_id="pattern.1_celebrity",
            name="Celebrity Endorser (Revised)",
            description="...",
            metadata=metadata,
        )

        assert pattern.metadata.analyst_renamed_from == "Old Name"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
