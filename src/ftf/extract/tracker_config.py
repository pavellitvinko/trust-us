"""Tracker-platform config: record IDs and internal labels (signals A1, A2, A9).

Reads `window.aio` (and its object-literal form) through the shared reader. Only a
whitelist of paths is emitted, never a flatten: `visit.*` and `session.*` hold the
capturing analyst's IP, city, ASN and user agent, and would link every page that one
person saved. The few `visit.*` paths below are record IDs, not visitor data.
"""

from __future__ import annotations

from typing import Any

from ..contracts import Observation, Purpose, View
from ..registry import Context, extractors
from . import _material as m

# (config path, namespace, signal, purpose). Paths ending in [] are lists.
_PATHS: tuple[tuple[str, str, str, Purpose], ...] = (
    ("campaign.uuid", "aio.campaign_uuid", "A1", Purpose.LINKAGE),
    ("campaign.owner_uuid", "aio.owner_uuid", "A1", Purpose.LINKAGE),
    ("landing.uuid", "aio.landing_uuid", "A1", Purpose.LINKAGE),
    ("source.uuid", "aio.source_uuid", "A1", Purpose.LINKAGE),
    ("flow.uuid", "aio.flow_uuid", "A1", Purpose.LINKAGE),
    ("landing.folder[].uuid", "aio.folder_uuid", "A1", Purpose.LINKAGE),
    ("visit.campaign_human_id", "aio.campaign_human_id", "A1", Purpose.LINKAGE),
    ("visit.landing_human_ids[]", "aio.landing_human_id", "A1", Purpose.LINKAGE),
    ("landing.lander_template_uuid", "aio.template_uuid", "A2", Purpose.LINKAGE),
    ("landing.lander_type_uuid", "aio.landing_type_uuid", "A2", Purpose.LINKAGE),
    ("campaign.name", "aio.campaign_name", "A9", Purpose.BOTH),
    ("landing.name", "aio.landing_name", "A9", Purpose.BOTH),
    ("landing.folder[].name", "aio.folder_name", "A9", Purpose.BOTH),
    ("source.name", "aio.source_name", "A9", Purpose.BOTH),
)


def values_at(config: dict[str, Any] | None, path: str) -> list[tuple[str, Any]]:
    """(concrete path, value) pairs for a whitelisted path; `[]` expands a list."""
    head, sep, tail = path.partition("[]")
    if not sep:
        return [(path, m.config_get(config, path))]
    items = m.config_get(config, head)
    if not isinstance(items, list):
        return []
    out = []
    for i, item in enumerate(items):
        concrete = f"{head}[{i}]"
        if tail:
            out.append((concrete + tail, m.config_get(item, tail.lstrip("."))))
        else:
            out.append((concrete, item))
    return out


def _scalar(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    return None


@extractors.register
class TrackerConfig:
    name = "tracker.config"
    version = "1"

    def run(self, view: View, ctx: Context) -> list[Observation]:
        mat = m.load(view, ctx)
        if mat.aio is None:
            return []
        emit = m.Emitter(view, self.name, self.version)
        for path, namespace, signal, purpose in _PATHS:
            for concrete, raw in values_at(mat.aio, path):
                value = _scalar(raw)
                source = m.at(mat.aio_source, f"aio:{concrete}")
                emit(signal, namespace, value, source, purpose=purpose)
        return emit.out
