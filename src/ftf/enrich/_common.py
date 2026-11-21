"""Helpers shared by the enrichers: which domain a view lives on, and building observations."""

from __future__ import annotations

import json
from typing import Any

from ..contracts import Observation, Purpose, Source, State, View, ViewKind


def ascii_name(name: str) -> str:
    """A host or domain in its IDNA (punycode) form, as DNS and RDAP expect it."""
    name = name.strip().lower().rstrip(".")
    try:
        return name.encode("idna").decode("ascii")
    except UnicodeError:
        return name


def view_domain(view: View, observations: list[Observation]) -> str | None:
    """Registrable domain of a page view (IDNA form), from page.identity (A14) or the URL."""
    if view.kind is not ViewKind.PAGE:
        return None
    for o in observations:
        if o.namespace == "domain.registrable" and o.state is State.PRESENT and o.value:
            return ascii_name(str(o.value))
    from ..extract._material import own_domain

    domain = own_domain(view)
    return ascii_name(domain) if domain else None


def view_host(view: View, observations: list[Observation], domain: str) -> str:
    """The host the page was served from (IDNA form); the registrable domain when unknown."""
    for o in observations:
        if o.namespace == "url.host" and o.state is State.PRESENT and o.value:
            host = ascii_name(str(o.value))
            if host == domain or host.endswith("." + domain):
                return host
    return domain


def raw_json(info: dict[str, Any]) -> str:
    return json.dumps(info, ensure_ascii=False, sort_keys=True, default=str)


class Out:
    """Collects one enricher's observations for one view."""

    def __init__(self, view: View, enricher: str, version: str):
        self.view, self.enricher, self.version = view, enricher, version
        self.items: list[Observation] = []

    def add(
        self,
        signal_id: str,
        namespace: str,
        value: Any,
        locator: str,
        info: dict[str, Any],
        purpose: Purpose = Purpose.RISK,
        confidence: float = 1.0,
    ) -> None:
        self.items.append(
            Observation(
                view_id=self.view.view_id,
                signal_id=signal_id,
                namespace=namespace,
                value=value,
                raw=raw_json(info),
                purpose=purpose,
                source=Source(locator=locator),
                extractor=self.enricher,
                version=self.version,
                confidence=confidence,
            )
        )

    def missing(
        self,
        signal_id: str,
        namespace: str,
        locator: str,
        info: dict[str, Any],
        purpose: Purpose = Purpose.RISK,
    ) -> None:
        """A lookup that could not be made or failed: not observable, never a negative."""
        self.items.append(
            Observation(
                view_id=self.view.view_id,
                signal_id=signal_id,
                namespace=namespace,
                value=None,
                raw=raw_json(info),
                state=State.NOT_OBSERVABLE,
                purpose=purpose,
                source=Source(locator=locator),
                extractor=self.enricher,
                version=self.version,
                confidence=0.0,
            )
        )
