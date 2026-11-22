"""Suspicion summary: why one view is suspicious, as reasons grouped by kind (architecture §11).

The level is the number of independent kinds with a reason. It is not a classifier score.
Every reason names its observation and source. A kind whose inputs are missing is reported as
unchecked, so a missing stage never reads as a clean result.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..contracts import Observation, Source, State

KINDS = ("domain", "impersonation", "absent_elements", "evasion", "persuasion")
YOUNG_DOMAIN_DAYS = 365

# A kind counts as checked when one of these inputs was observed.
_INPUTS = {
    "domain": ("rdap.", "warning."),  # the host alone does not show age or listing
    "impersonation": ("page.read_text", "image.text"),
    "absent_elements": ("expect.element",),
    "evasion": ("page.clean_text", "page.body_text", "cloak."),
    "persuasion": ("pattern.score", "markup."),
}
_MARKUP = {
    "markup.countdown": "countdown timer",
    "markup.crossed_price": "crossed-out price",
    "markup.stock_counter": "low-stock counter",
}


class SuspicionReason(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    signal_id: str
    namespace: str
    summary: str
    value: Any = None
    quote: str | None = None
    observation_ref: str  # extractor/namespace#position among this view's observations
    source: Source = Field(default_factory=Source)


class SuspicionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    view_id: str
    level: int
    reasons_by_kind: dict[str, list[SuspicionReason]] = Field(default_factory=dict)
    unchecked_kinds: list[str] = Field(default_factory=list)
    # Shown as context and not counted in the level: a real news photo of a public figure is
    # not suspicious by itself.
    face_matches: list[SuspicionReason] = Field(default_factory=list)


def _compact(text: str) -> str:
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def _hosts(observations: list[Observation]) -> list[str]:
    return [
        _compact(str(o.value))
        for o in observations
        if o.namespace in ("url.host", "domain.registrable")
        and o.state is State.PRESENT
        and o.value
    ]


def _claim_differs(claimed: str, hosts: list[str]) -> bool:
    """True only when a host is known and does not carry the claimed name."""
    name = _compact(claimed)
    return bool(name and hosts) and not any(name in host for host in hosts)


def _describe(o: Observation, hosts: list[str]) -> tuple[str, str, str] | None:
    """(kind, signal, summary) when this observation is a reason, otherwise None."""
    ns, value = o.namespace, o.value
    if ns == "expect.element":
        if o.state is State.ABSENT:
            return "absent_elements", "K", o.raw or f"expected element not found: {value}"
        return None
    if o.state is not State.PRESENT:
        return None
    if ns == "domain.tld_risk" and value == "high":
        return "domain", "C4", f"high-risk TLD class: {o.raw or value}"
    is_age = ns == "rdap.domain_age_days" and isinstance(value, int | float)
    if is_age and 0 <= value < YOUNG_DOMAIN_DAYS:
        return "domain", "C1", f"domain registered {int(value)} days before capture"
    if ns.startswith("warning.") and isinstance(value, dict):
        result = value.get("result")
        source = ns.removeprefix("warning.")
        if result == "listed":
            return "domain", o.signal_id, f"listed in warning source: {source}"
        if result == "possible_match":
            return "domain", o.signal_id, f"possible warning-list match, verify: {source}"
    if ns == "image.claimed_publisher" and _claim_differs(str(value), hosts):
        return "impersonation", "G2", f"claimed publisher {value} is not the host"
    if ns == "image.imitated_interface":
        return "impersonation", "G2", f"imitates interface: {value}"
    is_org = ns == "page.entity.organisation" and isinstance(value, dict)
    if is_org and value.get("role") == "regulator_invoked":
        return "impersonation", "G3", f"invokes institution: {value.get('name')}"
    if ns == "dom.evasion_filter":
        return "evasion", "E13", f"visual-evasion filter: {value}"
    if ns == "kit.cloaker":
        return "evasion", "J1", f"cloaking kit marker: {value}"
    if ns == "cloak.differential" and isinstance(value, dict):
        return (
            "evasion",
            "J1",
            (
                f"different content for {value.get('reference')} and {value.get('against')} "
                f"(difference {value.get('difference')})"
            ),
        )
    if ns == "cloak.gating":
        return "evasion", "J5", o.raw or "offer shown only to the ad-click profile"
    if ns == "cloak.white_page":
        return "evasion", "J2", f"white-page traits: {o.raw or value}"
    if ns in _MARKUP:
        return "persuasion", o.signal_id, f"{_MARKUP[ns]}: {value}"
    if ns == "pattern.score" and isinstance(value, dict) and value.get("score", 0) >= 0.5:
        return "persuasion", "H", f"technique pattern: {value.get('pattern')}"
    return None


def _checked(kind: str, observations: list[Observation]) -> bool:
    return any(
        o.namespace.startswith(_INPUTS[kind]) and o.state is not State.NOT_OBSERVABLE
        for o in observations
    )


def reason_summary_for_view(view_id: str, observations: list[Observation]) -> SuspicionSummary:
    hosts = _hosts(observations)
    grouped: dict[str, list[SuspicionReason]] = {}
    seen: set[tuple[str, str]] = set()
    for position, o in enumerate(observations):
        found = _describe(o, hosts)
        if found is None or (o.namespace, found[2]) in seen:
            continue
        seen.add((o.namespace, found[2]))
        kind, signal, summary = found
        grouped.setdefault(kind, []).append(
            SuspicionReason(
                kind=kind,
                signal_id=signal,
                namespace=o.namespace,
                summary=summary,
                value=o.value,
                quote=o.raw if o.state is State.PRESENT else None,
                observation_ref=f"{o.extractor}/{o.namespace}#{position}",
                source=o.source,
            )
        )
    ordered = {kind: grouped[kind] for kind in KINDS if kind in grouped}
    faces = [
        SuspicionReason(
            kind="face_context",
            signal_id="G1",
            namespace=o.namespace,
            summary=(
                f"possible face match to {o.value.get('person')} "
                f"({float(o.value.get('similarity', 0)):.0%}), needs verification"
            ),
            value=o.value,
            observation_ref=f"{o.extractor}/{o.namespace}#{position}",
            source=o.source,
        )
        for position, o in enumerate(observations)
        if o.namespace == "face.reference"
        and o.state is State.PRESENT
        and isinstance(o.value, dict)
    ]
    return SuspicionSummary(
        view_id=view_id,
        level=len(ordered),
        reasons_by_kind=ordered,
        face_matches=faces,
        unchecked_kinds=[k for k in KINDS if k not in ordered and not _checked(k, observations)],
    )
