"""Analyst control of the pattern library. Every edit saves a new version."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from .library import LibraryVersion, PatternDefinition, PatternLibraryStore, PatternMetadata


class LibraryEditError(ValueError):
    """The edit cannot be applied; the message is safe to show."""


def _edit(
    library: PatternLibraryStore,
    note: str,
    change: Callable[[LibraryVersion, str], None],
) -> LibraryVersion:
    latest = library.latest_version()
    if latest is None:
        raise LibraryEditError("no pattern library yet")
    number = max((int(v[1:5]) for v in library.list_versions() if v[1:5].isdigit()), default=0) + 1
    version_id = f"v{number:04d}-analyst"
    updated = latest.model_copy(deep=True)
    updated.version_id = version_id
    updated.created_at = datetime.now(UTC).isoformat()
    updated.notes = note
    change(updated, version_id)
    library.save_version(updated)
    return updated


def _find(version: LibraryVersion, pattern_id: str) -> PatternDefinition:
    for pattern in version.patterns:
        if pattern.pattern_id == pattern_id:
            if pattern.metadata is None:
                pattern.metadata = PatternMetadata(
                    created_in_version=version.version_id,
                    last_seen_in_version=version.version_id,
                )
            return pattern
    raise LibraryEditError(f"unknown pattern: {pattern_id}")


def rename_pattern(library: PatternLibraryStore, pattern_id: str, name: str):
    name = " ".join(name.split())
    if not name:
        raise LibraryEditError("a pattern needs a name")

    def change(version: LibraryVersion, _: str) -> None:
        pattern = _find(version, pattern_id)
        pattern.metadata.analyst_renamed_from = (
            pattern.metadata.analyst_renamed_from or pattern.name
        )
        pattern.name = name

    return _edit(library, f"renamed {pattern_id}", change)


def retire_pattern(library: PatternLibraryStore, pattern_id: str):
    def change(version: LibraryVersion, _: str) -> None:
        _find(version, pattern_id).metadata.analyst_retired_at = datetime.now(UTC).isoformat()

    return _edit(library, f"retired {pattern_id}", change)


def merge_patterns(library: PatternLibraryStore, keep_id: str, merge_id: str):
    if keep_id == merge_id:
        raise LibraryEditError("choose two different patterns")

    def change(version: LibraryVersion, _: str) -> None:
        keep, gone = _find(version, keep_id), _find(version, merge_id)
        if keep.metadata.analyst_retired_at:
            raise LibraryEditError("cannot merge into a retired pattern")
        keep.merged_centroids += [c for c in (gone.centroid, *gone.merged_centroids) if c]
        known = {e.get("card_id") for e in keep.positive_examples}
        keep.positive_examples += [
            e for e in gone.positive_examples if e.get("card_id") not in known
        ]
        keep.metadata.merged_from = sorted({*keep.metadata.merged_from, merge_id})
        version.patterns = [p for p in version.patterns if p.pattern_id != merge_id]
        for composite in version.composites:
            composite.technique_ids = sorted(
                {keep_id if t == merge_id else t for t in composite.technique_ids}
            )
        version.composites = [c for c in version.composites if len(c.technique_ids) > 1]

    return _edit(library, f"merged {merge_id} into {keep_id}", change)
