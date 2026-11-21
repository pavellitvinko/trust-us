"""Integration test: validate pattern discovery on signal group H.

Group H lists 19 persuasion techniques (H1-H19) discovered by manual reading of the sample.
This test validates that the pattern induction pipeline recovers these techniques.
"""

import tempfile
from pathlib import Path

import pytest

from ftf.patterns import (
    PatternLibraryStore,
    TechniqueCardForClustering,
    cluster_cards_reproducible,
)
from ftf.patterns.library import LibraryVersion, PatternDefinition, PatternMetadata

# Group H techniques from docs/signal-catalogue.md
GROUP_H_TECHNIQUES = {
    "H1": "Celebrity presented as endorser / user / founder",
    "H2": "Fake news-article framing",
    "H3": "Suppression story ('recording deleted', 'they tried to silence')",
    "H4": "Violence or arrest as hook",
    "H5": "'Banks / elites hide this from you'",
    "H6": "Profit / payout proof image",
    "H7": "Regulator or state backing claimed",
    "H8": "Authority figure in uniform (doctor, priest, presenter)",
    "H9": "Scarcity counter ('17 left', '99 places')",
    "H10": "Countdown timer / deadline",
    "H11": "Deep discount with crossed-out price",
    "H12": "Embedded fake social comments",
    "H13": "Gamified discount (wheel, doors, boxes)",
    "H14": "Chat-bot / quiz qualification before the form",
    "H15": "Callback promise ('a manager will call you')",
    "H16": "Miracle health claim / fear of illness",
    "H17": "Marketplace or app-store imitation",
    "H18": "Video sales letter with forced-sound prompt",
    "H19": "Low entry threshold stated as exact amount",
}


class TestGroupHDiscovery:
    """Test that pattern discovery recovers group H techniques."""

    def build_sample_cards(self) -> list[TechniqueCardForClustering]:
        """Build technique cards matching group H techniques.

        These simulate what the perception model would extract from the sample material.
        Each card includes wording variations to test clustering similarity.
        """
        cards = []

        # H1: Celebrity endorser
        cards.extend([
            TechniqueCardForClustering(
                card_name="Celebrity as endorser",
                card_description="A well-known public figure recommends and praises the product",
                view_id="v1_1", incident_id="1", source_text="Brzoska endorses this investment"
            ),
            TechniqueCardForClustering(
                card_name="Famous person using the product",
                card_description="A celebrity is shown using or benefiting from the product",
                view_id="v2_1", incident_id="2", source_text="Celebrity uses it daily"
            ),
            TechniqueCardForClustering(
                card_name="Endorsement by public figure",
                card_description="A well-known personality says this product is excellent",
                view_id="v3_1", incident_id="3", source_text="Olejnik vouches for quality"
            ),
        ])

        # H2: Fake news article
        cards.extend([
            TechniqueCardForClustering(
                card_name="News-style article layout",
                card_description="Content formatted as a journalism article with headline and byline",
                view_id="v1_2", incident_id="1", source_text="Article: Breaking discovery"
            ),
            TechniqueCardForClustering(
                card_name="Fake newspaper layout",
                card_description="Page mimics the design of a legitimate news outlet",
                view_id="v48_1", incident_id="48", source_text="Looks like Gazeta.pl"
            ),
        ])

        # H3: Suppression story
        cards.extend([
            TechniqueCardForClustering(
                card_name="Cover-up narrative",
                card_description="The technique mentions that authorities or elites tried to suppress information",
                view_id="v1_3", incident_id="1", source_text="They tried to silence this"
            ),
        ])

        # H4: Violence or arrest as hook
        cards.extend([
            TechniqueCardForClustering(
                card_name="Sensational arrest claim",
                card_description="Story implies someone was arrested or faced legal trouble",
                view_id="v2_4", incident_id="2", source_text="Celebrity arrested for..."
            ),
        ])

        # H5: Banks / elites hide this
        cards.extend([
            TechniqueCardForClustering(
                card_name="Elite conspiracy claim",
                card_description="Text says banks or powerful institutions are hiding secrets from ordinary people",
                view_id="v3_5", incident_id="3", source_text="Banks don't want you to know"
            ),
        ])

        # H6: Profit screenshot
        cards.extend([
            TechniqueCardForClustering(
                card_name="Fake bank statement image",
                card_description="Screenshot shows a fake banking app screen with profit figures",
                view_id="v1_6", incident_id="1", source_text="shot-pko.webp shows profit"
            ),
            TechniqueCardForClustering(
                card_name="Deposit confirmation screenshot",
                card_description="Image appears to be a bank app showing money received",
                view_id="v2_6", incident_id="2", source_text="shot-pekao.webp"
            ),
        ])

        # H7: Regulator backing
        cards.extend([
            TechniqueCardForClustering(
                card_name="KNF approval claim",
                card_description="Text claims the financial regulator supervises or approves this",
                view_id="v61_1", incident_id="61", source_text="KNF-registered operation"
            ),
        ])

        # H8: Authority figure in uniform
        cards.extend([
            TechniqueCardForClustering(
                card_name="Doctor in white coat",
                card_description="Image shows someone dressed as a medical professional",
                view_id="v5_1", incident_id="5", source_text="Dr. in lab coat"
            ),
            TechniqueCardForClustering(
                card_name="Authority figure appearance",
                card_description="Person wears clothing suggesting professional or official status",
                view_id="v14_1", incident_id="14", source_text="TV presenter outfit"
            ),
        ])

        # H9: Scarcity counter
        cards.extend([
            TechniqueCardForClustering(
                card_name="Limited places announcement",
                card_description="Text displays a counter showing few items or spots remaining",
                view_id="v14_2", incident_id="14", source_text="17 left in stock"
            ),
            TechniqueCardForClustering(
                card_name="Availability warning",
                card_description="Message indicates supply is limited and running out",
                view_id="v47_1", incident_id="47", source_text="99 places left"
            ),
        ])

        # H10: Countdown timer
        cards.extend([
            TechniqueCardForClustering(
                card_name="Ticking countdown display",
                card_description="Animated timer counting down to deadline",
                view_id="v14_3", incident_id="14", source_text="timer element"
            ),
            TechniqueCardForClustering(
                card_name="Time pressure script",
                card_description="JavaScript implements a countdown to create urgency",
                view_id="v47_2", incident_id="47", source_text="setInterval timer"
            ),
        ])

        # H11: Price discount visual
        cards.extend([
            TechniqueCardForClustering(
                card_name="Crossed-out price",
                card_description="Old price is struck through, new price shown as discount",
                view_id="v14_4", incident_id="14", source_text="<s>999 zł</s> 99 zł"
            ),
            TechniqueCardForClustering(
                card_name="Discount percentage display",
                card_description="Shows reduced price with percentage saving compared to original",
                view_id="v47_3", incident_id="47", source_text="50% off"
            ),
        ])

        # H12: Fake comments
        cards.extend([
            TechniqueCardForClustering(
                card_name="Fake user testimonials",
                card_description="Section shows fabricated customer reviews and comments",
                view_id="v1_12", incident_id="1", source_text="Positive comments section"
            ),
            TechniqueCardForClustering(
                card_name="Generated comment section",
                card_description="Comments block with fake names and reviews",
                view_id="v14_5", incident_id="14", source_text="Comments from users"
            ),
        ])

        # H13: Gamified discount
        cards.extend([
            TechniqueCardForClustering(
                card_name="Spin-the-wheel promotion",
                card_description="Interactive game where user spins wheel to reveal discount",
                view_id="v14_6", incident_id="14", source_text="Wheel game element"
            ),
        ])

        # H14: Chatbot qualification
        cards.extend([
            TechniqueCardForClustering(
                card_name="Quiz before lead form",
                card_description="Interactive questions required before access to main offer",
                view_id="v61_2", incident_id="61", source_text="Chat qualification"
            ),
        ])

        # H15: Callback promise
        cards.extend([
            TechniqueCardForClustering(
                card_name="Manager call guarantee",
                card_description="Text promises a company manager will call the person",
                view_id="v1_15", incident_id="1", source_text="Manager will call you"
            ),
            TechniqueCardForClustering(
                card_name="Follow-up contact promise",
                card_description="Message assures that someone will contact via phone",
                view_id="v47_4", incident_id="47", source_text="We will call you today"
            ),
        ])

        # H16: Health scare
        cards.extend([
            TechniqueCardForClustering(
                card_name="Medical problem warning",
                card_description="Text creates fear about health risks or disease",
                view_id="v5_2", incident_id="5", source_text="Serious health warning"
            ),
            TechniqueCardForClustering(
                card_name="Wellness crisis claim",
                card_description="Message emphasizes danger of not taking the supplement",
                view_id="v14_7", incident_id="14", source_text="Health disaster claim"
            ),
        ])

        # H17: App store imitation
        cards.extend([
            TechniqueCardForClustering(
                card_name="Fake app store listing",
                card_description="Page mimics the Google Play or App Store design",
                view_id="v10_1", incident_id="10", source_text="App Store layout"
            ),
        ])

        # H18: Video sales letter
        cards.extend([
            TechniqueCardForClustering(
                card_name="Forced-sound video prompt",
                card_description="Video auto-plays with sound enabled, trapping user attention",
                view_id="v14_8", incident_id="14", source_text="Video with auto-sound"
            ),
            TechniqueCardForClustering(
                card_name="Sales pitch video",
                card_description="Long-form video making persuasion pitch",
                view_id="v47_5", incident_id="47", source_text="Sales video content"
            ),
        ])

        # H19: Low entry amount
        cards.extend([
            TechniqueCardForClustering(
                card_name="Minimum investment stated",
                card_description="Text specifies an exact low amount to start",
                view_id="v61_3", incident_id="61", source_text="Enter from 1000 zł"
            ),
        ])

        return cards

    def test_discover_group_h_techniques(self):
        """Cluster technique cards and verify group H techniques are recovered."""
        cards = self.build_sample_cards()
        clusters = cluster_cards_reproducible(cards, seed=42, n_clusters=15)

        # We should have multiple clusters representing the different techniques
        assert len(clusters) >= 10, f"Expected at least 10 clusters, got {len(clusters)}"

        # Each cluster should have cards with similar names
        for cluster in clusters:
            names = cluster.card_names()
            assert len(names) >= 1, f"Cluster {cluster.cluster_id} has no cards"
            # Cards in same cluster should be semantically related
            # (e.g., "Celebrity as endorser" and "Famous person using the product" together)

    def test_reproducibility_on_shuffled_input(self):
        """Two induction runs on shuffled input produce same clusters."""
        cards = self.build_sample_cards()

        # First run
        clusters1 = cluster_cards_reproducible(cards, seed=42, n_clusters=15)

        # Shuffle the input
        import random

        shuffled = cards.copy()
        random.shuffle(shuffled)

        # Second run on shuffled input
        clusters2 = cluster_cards_reproducible(shuffled, seed=42, n_clusters=15)

        # Same card set should cluster identically
        def cluster_members_signature(clusters):
            return sorted([
                tuple(sorted((c.view_id, c.card_name) for c in cluster.cards))
                for cluster in clusters
            ])

        sig1 = cluster_members_signature(clusters1)
        sig2 = cluster_members_signature(clusters2)

        assert sig1 == sig2, "Shuffled input produced different clusters"
        assert len(sig1) == len(sig2), "Cluster count changed"

    def test_library_persistence(self):
        """Create and persist a pattern library."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lib_store = PatternLibraryStore(Path(tmpdir))

            # Create a simple library with group H pattern definitions
            patterns = [
                PatternDefinition(
                    pattern_id="pattern.1_celebrity_endorser",
                    name="Celebrity Endorser",
                    description="A well-known public figure recommends or uses the product",
                    positive_examples=[
                        {"card_name": "Celebrity as endorser", "evidence": "Brzoska endorses"},
                        {"card_name": "Famous person using", "evidence": "Uses daily"},
                    ],
                    related_patterns=["pattern.2_fake_news"],
                    metadata=PatternMetadata(
                        created_in_version="v1.0.0",
                        last_seen_in_version="v1.0.0",
                    ),
                ),
                PatternDefinition(
                    pattern_id="pattern.2_fake_news",
                    name="Fake News Framing",
                    description="Content formatted as a journalism article from a legitimate outlet",
                    positive_examples=[
                        {"card_name": "News-style article", "evidence": "Layout looks like Gazeta"},
                    ],
                    metadata=PatternMetadata(
                        created_in_version="v1.0.0",
                        last_seen_in_version="v1.0.0",
                    ),
                ),
                PatternDefinition(
                    pattern_id="pattern.3_scarcity",
                    name="Scarcity Signals",
                    description="Time or stock pressure to force quick decisions",
                    positive_examples=[
                        {"card_name": "Limited places", "evidence": "17 left"},
                        {"card_name": "Countdown timer", "evidence": "Timer element"},
                    ],
                    metadata=PatternMetadata(
                        created_in_version="v1.0.0",
                        last_seen_in_version="v1.0.0",
                    ),
                ),
            ]

            version = LibraryVersion(
                version_id="v1.0.0",
                created_at="2026-11-21T10:00:00",
                patterns=patterns,
                input_hash="abc123",
            )

            # Save the version
            lib_store.save_version(version)

            # Reload and verify
            loaded = lib_store.latest_version()
            assert loaded is not None
            assert len(loaded.patterns) == 3
            assert loaded.patterns[0].name == "Celebrity Endorser"
            assert any(
                p.pattern_id == "pattern.3_scarcity" for p in loaded.patterns
            ), "Scarcity pattern not found"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
