"""The shared data contracts. Every track reads and writes these and nothing else.

FROZEN after the skeleton lands: a change here affects all three tracks, so it goes
through one owner and is announced. See docs/architecture.md section 3.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class State(StrEnum):
    """Three-state result. Only ABSENT is a signal; NOT_OBSERVABLE never counts against a link."""

    PRESENT = "present"
    ABSENT = "absent"
    NOT_OBSERVABLE = "not_observable"


class Purpose(StrEnum):
    LINKAGE = "linkage"  # helps decide two views are related; drives grouping
    RISK = "risk"  # helps decide one view is suspicious; never drives grouping
    BOTH = "both"


class Tier(StrEnum):
    """How strong a link a match implies. Maps to the relation a layer can support."""

    IDENTITY = "T1"  # same account or record the operator controls
    ARTIFACT = "T2"  # same or near-same piece of content
    KIT = "T3"  # same toolkit, template or tracker software
    PLAYBOOK = "T4"  # same way of persuading
    NOISE = "T0"  # shared by unrelated sites; stoplisted


class ViewKind(StrEnum):
    PAGE = "page"
    IMAGE = "image"  # screenshot or ad creative; its role is decided by perception
    VIDEO = "video"


class FingerprintKind(StrEnum):
    SET = "set"  # payload: list of {"ns": str, "value": str}
    HASH = "hash"  # payload: {"algo": str, "values": list[str]}
    VECTOR = "vector"  # payload: {"model": str, "values": list[float]}


class RelationType(StrEnum):
    DUPLICATE = "duplicate"
    SAME_CAMPAIGN = "same_campaign"
    SAME_FAMILY = "same_family"
    SAME_PLAYBOOK = "same_playbook"
    UNRELATED = "unrelated"
    INSUFFICIENT = "insufficient_evidence"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(_Model):
    """Where a value came from. `file` is relative to the incident root."""

    file: str | None = None
    locator: str | None = (
        None  # e.g. "har:entries[3].request.url", "html:script[2]", "url:query.pxl"
    )


class Incident(_Model):
    incident_id: str
    root: str  # absolute path of the folder as ingested
    label: str = ""


class Artifact(_Model):
    incident_id: str
    path: str  # relative to the incident root
    sha256: str
    size: int
    media_type: str
    role: str  # primary | har | resource | attachment | other


class View(_Model):
    """The unit of analysis: one captured page, one image, or one video."""

    view_id: str
    incident_id: str
    kind: ViewKind
    label: str
    primary: str | None = (
        None  # main file, relative to the incident root (None for a HAR-only page)
    )
    har: str | None = None
    resources_dir: str | None = None
    attachments: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class Observation(_Model):
    """One extracted fact about one view, with its provenance."""

    view_id: str
    signal_id: str  # row in docs/signal-catalogue.md, e.g. "A3"
    namespace: str  # values are only ever compared within one namespace, e.g. "fb_pixel"
    value: Any = None  # JSON-serialisable, normalised
    raw: str | None = None  # the value exactly as found
    state: State = State.PRESENT
    purpose: Purpose = Purpose.LINKAGE
    source: Source = Field(default_factory=Source)
    extractor: str
    version: str
    confidence: float = 1.0


class Fingerprint(_Model):
    view_id: str
    layer_id: str
    kind: FingerprintKind
    payload: Any
    version: str


class Evidence(_Model):
    """The result of comparing two views on one layer."""

    view_a: str
    view_b: str
    layer_id: str
    comparable: bool = True  # False when the layer is missing on either side
    score: float = 0.0  # 0..1
    shared: list[dict[str, Any]] = Field(default_factory=list)
    differing: list[dict[str, Any]] = Field(default_factory=list)
    comparator: str
    version: str


class Relation(_Model):
    """A typed link between two views, produced by fusion."""

    view_a: str
    view_b: str
    type: RelationType
    confidence: str = ""  # a label, not a probability
    coverage: float = 0.0  # share of comparable layer groups
    tier_scores: dict[str, float] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
