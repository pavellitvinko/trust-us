"""Leave-one-incident-out on the fixtures: remove a folder, rebuild, re-submit it, read the
outcome. Exercises open-set assignment including the cases that must come back as novel or
insufficient (docs/architecture.md section 13)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .. import pipeline
from ..fusion import DEFAULT, FusionConfig, prepare, run_method
from ..grouping.assign import Assignment, assign_incident
from .fixtures import OBSERVATIONS, load_observations, store_from_fixtures


@dataclass
class HeldOut:
    incident_id: str
    headline: Assignment | None
    per_view: list[Assignment]


def leave_one_incident_out(
    incident_id: str,
    path: Path = OBSERVATIONS,
    config: FusionConfig = DEFAULT,
    exclude_namespaces: set[str] = frozenset(),
) -> HeldOut:
    store = store_from_fixtures(
        path, exclude_incidents={incident_id}, exclude_namespaces=exclude_namespaces
    )
    pipeline.run_fingerprint(store)
    groups = run_method(store, config)
    store_from_fixtures(
        path, store=store, only_incidents={incident_id}, exclude_namespaces=exclude_namespaces
    )
    new_views = [v.view_id for v in store.views(incident_id)]
    pipeline.run_fingerprint(store, view_ids=new_views)
    prepare(store, config)  # weights and demotions now see the new material
    headline, per_view = assign_incident(store, incident_id, groups, config, prepared=True)
    store.close()
    return HeldOut(incident_id, headline, per_view)


def all_incidents(path: Path = OBSERVATIONS) -> list[str]:
    _, observations = load_observations(path)
    return sorted({o.view_id.partition(":")[0] for o in observations}, key=lambda x: (len(x), x))


def loio_all(
    path: Path = OBSERVATIONS,
    config: FusionConfig = DEFAULT,
    incidents: list[str] | None = None,
    exclude_namespaces: set[str] = frozenset(),
) -> list[HeldOut]:
    return [
        leave_one_incident_out(i, path, config, exclude_namespaces)
        for i in (incidents or all_incidents(path))
    ]


def format_loio(results: list[HeldOut]) -> str:
    lines = [f"{'incident':9} {'view':16} {'outcome':28} target"]
    for r in results:
        for a in r.per_view:
            mark = "*" if r.headline is a else " "
            lines.append(
                f"{r.incident_id:9} {a.view_id:16} {a.outcome.value:28} {a.target or '-'} {mark}"
            )
    lines.append("")
    lines.append("* = the folder's headline outcome")
    return "\n".join(lines)
