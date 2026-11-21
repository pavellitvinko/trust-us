"""Helpers shared by the enrichers: which domain a view lives on, and building observations."""

from __future__ import annotations

import json
from typing import Any

from ..contracts import Observation, Purpose, Source, State, View, ViewKind


def view_domain(view: View, observations: list[Observation]) -> str | None:
    """Registrable domain of a page view, from page.identity (A14) or the view's URL."""
    if view.kind is not ViewKind.PAGE:
        return None
    for o in observations:
        if o.namespace == "domain.registrable" and o.state is State.PRESENT and o.value:
            return str(o.value).lower()
    from ..extract._material import own_domain

    return own_domain(view)


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
