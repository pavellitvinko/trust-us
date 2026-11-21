"""Expected-but-absent elements: data model, versioning, storage.

This module defines the data structures for page-kind profiles and expected elements,
along with persistent versioned storage. Each page kind (e.g., 'investment offer',
'supplement sales page') has a profile listing expected elements. The model generates
profiles from knowledge; code grounds them against real pages and stores versions.

Profiles are cached and versioned like patterns. Element verification happens at check time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ElementBasis(StrEnum):
    """Why an element is expected on pages of this kind."""

    LEGAL_REQUIREMENT = "legal_requirement"
    REGULATOR_PRACTICE = "regulator_practice"
    CUSTOMARY = "customary"


class ElementChecked(StrEnum):
    """Result of checking whether an expected element is present in a view."""

    PRESENT = "present"
    ABSENT = "absent"
    NOT_OBSERVABLE = "not_observable"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TypicalWording(_Model):
    """Example text that commonly appears for this element in Polish, with its location hint."""

    text: str
    location_hint: str = ""


class ExpectedElement(_Model):
    """One expected element for a page kind.

    Attributes:
        name: Short name of the element (e.g., 'legal_entity_name').
        description: What the element is in English.
        basis: Why it is expected (legal, regulatory, or customary practice).
        typical_wordings: Polish example texts that might appear for this element.
        typical_location: Where on the page it usually sits (e.g., 'footer', 'form', 'header').
        notes: Optional notes on how to identify it.
    """

    name: str
    description: str
    basis: ElementBasis = ElementBasis.CUSTOMARY
    typical_wordings: list[TypicalWording] = Field(default_factory=list)
    typical_location: str = ""
    notes: str = ""


class PageKind(_Model):
    """A canonicalised kind of page, in the model's words.

    Attributes:
        kind_id: Stable identifier (e.g., 'kind.1_investment_offer').
        name: Short name, e.g., 'investment offer'.
        description: What pages of this kind do (e.g., 'solicits investment in a scheme').
        vertical: Categorisation for grouping (e.g., 'Investment / trading', 'Gambling').
    """

    kind_id: str
    name: str
    description: str = ""
    vertical: str = ""


@dataclass
class ElementProfile:
    """Profile entry for one expected element, with metadata."""

    element: ExpectedElement
    seen_in_genuine: int = 0  # count of genuine pages where this was present
    total_genuine_checked: int = 0  # count of genuine pages checked
    false_alarm_rate: float = 0.0  # (1 - seen_in_genuine / total_genuine_checked)
    promoted: bool = True  # kept in profile; False if seen in few genuine pages
    added_by: Optional[str] = None  # 'model' or analyst name
    added_at: Optional[datetime] = None


class KindProfile(_Model):
    """Profile for one page kind: list of expected elements, once generated and grounded.

    Attributes:
        kind: The page kind this profile describes.
        elements: List of expected elements, with grounding metadata.
        generated_from: 'model_knowledge' or 'patterns' (how the profile was created).
        grounded_on_genuine: Count of genuine pages used to ground this profile.
        created_at: When the profile was first generated.
        grounded_at: When grounding on genuine pages was completed.
        notes: Optional notes from analyst review.
    """

    kind: PageKind
    elements: list[ElementProfile] = Field(default_factory=list)
    generated_from: Literal["model_knowledge", "patterns"] = "model_knowledge"
    grounded_on_genuine: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    grounded_at: Optional[datetime] = None
    notes: str = ""


class KindLibraryVersion(_Model):
    """One version of the kind profiles library.

    Attributes:
        version_id: Semantic version (e.g., '1.0.0').
        profiles: List of page-kind profiles.
        created_at: When this version was created.
        input_hash: Hash of the input used to create this version (for reproducibility).
        merge_notes: Notes on any merges/splits from prior version.
    """

    version_id: str
    profiles: list[KindProfile] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    input_hash: Optional[str] = None
    merge_notes: str = ""


class KindLibraryStore:
    """Versioned storage for page-kind profiles.

    Stores profiles as JSON snapshots, with stable kind IDs. Each version is immutable.
    When a new induction run occurs, a new version is created.
    """

    def __init__(self, store_dir: Path):
        """Initialize the store.

        Args:
            store_dir: Directory where versions are stored (created if needed).
        """
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)

    def _version_path(self, version_id: str) -> Path:
        """Path to a version file."""
        return self.store_dir / f"kind-profiles-{version_id}.json"

    def save_version(self, library: KindLibraryVersion) -> Path:
        """Save a library version to disk.

        Args:
            library: The library version to save.

        Returns:
            Path to the saved file.

        Raises:
            ValueError: If the version already exists.
        """
        path = self._version_path(library.version_id)
        if path.exists():
            raise ValueError(f"version {library.version_id} already exists")

        with open(path, "w", encoding="utf-8") as fh:
            json.dump(library.model_dump(), fh, indent=2, default=str)
        return path

    def load_version(self, version_id: str) -> KindLibraryVersion:
        """Load a library version.

        Args:
            version_id: The version to load.

        Returns:
            The loaded library.

        Raises:
            FileNotFoundError: If the version does not exist.
        """
        path = self._version_path(version_id)
        if not path.exists():
            raise FileNotFoundError(f"version {version_id} not found")

        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        # Reconstruct nested models
        profiles = []
        for p_data in data.get("profiles", []):
            elements = []
            for e_data in p_data.get("elements", []):
                # Parse nested ExpectedElement and TypicalWording
                elem_dict = e_data.get("element", {})
                typicals = []
                for w in elem_dict.get("typical_wordings", []):
                    typicals.append(TypicalWording(**w))
                elem_dict["typical_wordings"] = typicals

                expected = ExpectedElement(**elem_dict)
                ep = ElementProfile(
                    element=expected,
                    seen_in_genuine=e_data.get("seen_in_genuine", 0),
                    total_genuine_checked=e_data.get("total_genuine_checked", 0),
                    false_alarm_rate=e_data.get("false_alarm_rate", 0.0),
                    promoted=e_data.get("promoted", True),
                    added_by=e_data.get("added_by"),
                    added_at=e_data.get("added_at"),
                )
                elements.append(ep)

            # Parse nested PageKind
            kind_dict = p_data.get("kind", {})
            kind = PageKind(**kind_dict)

            profile = KindProfile(
                kind=kind,
                elements=elements,
                generated_from=p_data.get("generated_from", "model_knowledge"),
                grounded_on_genuine=p_data.get("grounded_on_genuine", 0),
                created_at=p_data.get("created_at"),
                grounded_at=p_data.get("grounded_at"),
                notes=p_data.get("notes", ""),
            )
            profiles.append(profile)

        return KindLibraryVersion(
            version_id=data.get("version_id"),
            profiles=profiles,
            created_at=data.get("created_at"),
            input_hash=data.get("input_hash"),
            merge_notes=data.get("merge_notes", ""),
        )

    def latest_version(self) -> Optional[KindLibraryVersion]:
        """Get the latest version by lexicographic sort.

        Returns:
            The latest version, or None if no versions exist.
        """
        versions = sorted(p.stem.replace("kind-profiles-", "") for p in self.store_dir.glob("kind-profiles-*.json"))
        if not versions:
            return None
        return self.load_version(versions[-1])

    def list_versions(self) -> list[str]:
        """List all available version IDs."""
        return sorted(p.stem.replace("kind-profiles-", "") for p in self.store_dir.glob("kind-profiles-*.json"))

    def get_kind_profile(self, kind_name: str, version_id: Optional[str] = None) -> Optional[KindProfile]:
        """Get a profile for a page kind.

        Args:
            kind_name: The page kind name (e.g., 'investment offer').
            version_id: Which version to query; if None, uses the latest.

        Returns:
            The profile, or None if not found.
        """
        if version_id is None:
            lib = self.latest_version()
            if lib is None:
                return None
        else:
            lib = self.load_version(version_id)

        for profile in lib.profiles:
            if profile.kind.name == kind_name:
                return profile
        return None

    def find_profile_by_id(self, kind_id: str, version_id: Optional[str] = None) -> Optional[KindProfile]:
        """Get a profile by its stable kind_id.

        Args:
            kind_id: The kind_id (e.g., 'kind.1_investment_offer').
            version_id: Which version to query; if None, uses the latest.

        Returns:
            The profile, or None if not found.
        """
        if version_id is None:
            lib = self.latest_version()
            if lib is None:
                return None
        else:
            lib = self.load_version(version_id)

        for profile in lib.profiles:
            if profile.kind.kind_id == kind_id:
                return profile
        return None
