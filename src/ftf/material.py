"""What the Material screen shows for one view, with explicit unavailable states."""

from __future__ import annotations

from typing import Any

from .contracts import Observation, State
from .grouping.suspicion import reason_summary_for_view
from .store import Store


def _status(rows: list[Observation]) -> str:
    if not rows:
        return "not_run"
    return "unavailable" if all(o.state is State.NOT_OBSERVABLE for o in rows) else "available"


def _rows(rows: list[Observation], label) -> list[dict[str, Any]]:
    return [
        {
            "item": label(o),
            "state": o.state.value,
            "detail": o.raw,
            "source": f"{o.source.file or '-'}:{o.source.locator or '-'}",
        }
        for o in rows
        if o.value is not None
    ]


def material_summary(store: Store, view_id: str) -> dict[str, Any]:
    observations = store.observations(view_id)
    elements = [o for o in observations if o.namespace == "expect.element"]
    patterns = [o for o in observations if o.namespace == "pattern.score"]
    kind = next((o.value for o in observations if o.namespace == "page.kind" and o.value), None)
    return {
        "page_kind": kind,
        "suspicion": reason_summary_for_view(view_id, observations).model_dump(),
        "expectations": {
            "status": _status(elements),
            "rows": _rows(elements, lambda o: o.value),
        },
        "patterns": {
            "status": _status(patterns),
            "rows": _rows(patterns, lambda o: o.value.get("pattern")),
        },
    }


def expectation_profiles(store: Store) -> dict[str, list[dict[str, Any]]]:
    """Each page kind's checked elements, with how many views showed each state."""
    counts: dict[str, dict[str, dict[str, int]]] = {}
    for view in store.views():
        observations = store.observations(view.view_id)
        row = next(
            (o.value for o in observations if o.namespace == "expect.kind" and o.value), None
        )
        if not isinstance(row, dict):
            continue
        kind = counts.setdefault(row["canonical"], {})
        for o in observations:
            if o.namespace == "expect.element" and o.value is not None:
                states = kind.setdefault(str(o.value), {s.value: 0 for s in State})
                states[o.state.value] += 1
    return {
        name: [{"element": element, **states} for element, states in sorted(elements.items())]
        for name, elements in sorted(counts.items())
    }
