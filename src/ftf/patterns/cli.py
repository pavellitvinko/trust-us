"""CLI commands for pattern library management and discovery.

Runs in perception track:
- ftf patterns induct: cluster technique cards and generate library
- ftf patterns match: judge views against library patterns
- ftf patterns list: show library versions and patterns
"""

from __future__ import annotations

import logging
from pathlib import Path

import click

from ftf.config import Settings, get_settings
from ftf.patterns import (
    PatternLibraryStore,
    cluster_cards_reproducible,
    gather_technique_cards,
)
from ftf.patterns.clustering import embed_text
from ftf.patterns.induction import run_induction
from ftf.patterns.novelty import NoveltyStore
from ftf.store import Store

log = logging.getLogger(__name__)


@click.group("patterns")
def patterns_group():
    """Manage and discover persuasion patterns."""
    pass


@patterns_group.command("induct")
@click.option("--sample", type=click.Path(exists=True), help="Path to data with technique cards")
@click.option("--output", type=click.Path(), default="./data/patterns", help="Output directory")
@click.option("--n-clusters", type=int, help="Number of clusters (auto-estimate if not set)")
@click.option("--seed", type=int, default=42, help="Random seed for reproducibility")
def induct_patterns(sample, output, n_clusters, seed):
    """Cluster technique cards and generate a pattern library.

    Reads technique card observations from the data store, clusters by embedding,
    and generates pattern definitions via the text model.
    """
    settings = get_settings()
    lib_store = PatternLibraryStore(Path(output))
    store = Store(settings.db_path)

    click.echo("Gathering technique cards...")
    cards = gather_technique_cards(store)
    if not cards:
        click.echo("Error: no technique cards found", err=True)
        return

    click.echo(f"Found {len(cards)} technique cards")
    click.echo(f"Clustering with seed {seed}...")

    clusters = cluster_cards_reproducible(cards, seed=seed, n_clusters=n_clusters)
    click.echo(f"Created {len(clusters)} clusters")

    for idx, cluster in enumerate(clusters[:5]):
        click.echo(f"  Cluster {idx}: {len(cluster.cards)} cards, names: {', '.join(cluster.card_names()[:3])}")

    click.echo("Running induction (generating pattern definitions)...")
    # TODO: integrate with actual model when available
    # For now, just show cluster analysis
    version = None  # await run_induction(settings, store, clusters)

    if version:
        lib_store.save_version(version)
        click.echo(f"✓ Saved library version {version.version_id} with {len(version.patterns)} patterns")
    else:
        click.echo("Induction not yet implemented in this version")


@patterns_group.command("list")
@click.option("--library", type=click.Path(exists=True), default="./data/patterns", help="Library directory")
def list_patterns(library):
    """List all pattern library versions and their patterns."""
    lib_store = PatternLibraryStore(Path(library))

    versions = lib_store.list_versions()
    if not versions:
        click.echo("No pattern library found")
        return

    for version_id in versions:
        version = lib_store.load_version(version_id)
        click.echo(f"\n{version_id} ({version.created_at})")
        click.echo(f"  Patterns: {len(version.patterns)}")
        for pattern in version.patterns[:5]:
            click.echo(f"    - {pattern.pattern_id}: {pattern.name}")
        if len(version.patterns) > 5:
            click.echo(f"    ... and {len(version.patterns) - 5} more")


@patterns_group.command("candidates")
@click.option("--novelty-dir", type=click.Path(exists=True), default="./data/novelty", help="Novelty tracking directory")
def show_candidates(novelty_dir):
    """Show candidate patterns waiting for promotion."""
    store = NoveltyStore(Path(novelty_dir))

    all_candidates = store._load_candidates()
    if not all_candidates:
        click.echo("No candidate patterns")
        return

    promoted = store.ready_for_promotion()
    click.echo(f"Candidate patterns: {len(all_candidates)}, ready for promotion: {len(promoted)}")

    click.echo("\nReady for promotion (seen 2+ times independently):")
    for candidate in promoted:
        click.echo(f"  {candidate.card_name}")
        click.echo(f"    Seen in {len(set(s['incident'] for s in candidate.seen_in))} incidents, {len(candidate.seen_in)} total sightings")

    click.echo("\nWaiting for second sighting:")
    for candidate in all_candidates.values():
        if not candidate.promotion_ready:
            incidents = set(s["incident"] for s in candidate.seen_in)
            click.echo(f"  {candidate.card_name} (seen in {list(incidents)})")


if __name__ == "__main__":
    patterns_group()
