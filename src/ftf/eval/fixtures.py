"""Load hand-made observations (tests/fixtures/observations.json) into a Store.

The fixture file holds observations only; incidents and views are synthesised from the
view ids (`<incident>:<name>`). A view's kind comes from an optional `views` block in the
file, else from its name: `video` -> video, `ad`/`screenshot`/`creative` -> image, else page.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from ..contracts import Incident, Observation, View, ViewKind
from ..store import Store

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
OBSERVATIONS = FIXTURES_DIR / "observations.json"

_IMAGE_WORDS = ("ad", "screenshot", "creative", "image")


def _kind(name: str) -> ViewKind:
    if name.startswith("video"):
        return ViewKind.VIDEO
    if name.startswith(_IMAGE_WORDS):
        return ViewKind.IMAGE
    return ViewKind.PAGE


def load_observations(path: Path = OBSERVATIONS) -> tuple[dict, list[Observation]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data, [Observation.model_validate(o) for o in data["observations"]]


def store_from_fixtures(
    path: Path = OBSERVATIONS,
    store: Store | None = None,
    exclude_incidents: set[str] = frozenset(),
    exclude_namespaces: set[str] = frozenset(),
    only_incidents: set[str] | None = None,
    transform: Callable[[Observation], Observation | None] | None = None,
) -> Store:
    """A store holding the fixture incidents, views and observations. Nothing is run.
    `transform` may rewrite or drop (return None) each observation: degradation tests."""
    store = store or Store(":memory:")
    data, observations = load_observations(path)
    declared = data.get("views", {})
    by_incident: dict[str, dict[str, list[Observation]]] = defaultdict(lambda: defaultdict(list))
    for o in observations:
        if o.namespace in exclude_namespaces:
            continue
        incident_id, _, _ = o.view_id.partition(":")
        if incident_id in exclude_incidents:
            continue
        if only_incidents is not None and incident_id not in only_incidents:
            continue
        if transform is not None:
            o = transform(o)
            if o is None:
                continue
        by_incident[incident_id][o.view_id].append(o)
    for view_id, spec in declared.items():  # views with no observations still exist
        incident_id = view_id.partition(":")[0]
        if incident_id in exclude_incidents:
            continue
        if only_incidents is not None and incident_id not in only_incidents:
            continue
        by_incident[incident_id].setdefault(view_id, [])
    root = str(Path(path).resolve().parent / "incidents")
    for incident_id, views in sorted(by_incident.items()):
        view_objs = []
        for view_id in sorted(views):
            name = view_id.partition(":")[2]
            spec = declared.get(view_id, {})
            view_objs.append(
                View(
                    view_id=view_id,
                    incident_id=incident_id,
                    kind=ViewKind(spec.get("kind", _kind(name).value)),
                    label=spec.get("label", view_id),
                    meta={"origin": "fixture"},
                )
            )
        store.put_incident(
            Incident(
                incident_id=incident_id, root=f"{root}/{incident_id}", label=f"case {incident_id}"
            ),
            [],
            view_objs,
        )
        for view_id, obs in views.items():
            by_extractor: dict[str, list[Observation]] = defaultdict(list)
            for o in obs:
                by_extractor[o.extractor].append(o)
            for extractor, items in by_extractor.items():
                store.put_observations(view_id, extractor, items)
    return store
