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
