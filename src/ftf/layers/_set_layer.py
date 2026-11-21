"""Factory for set-valued layers: pick observations by namespace, dedupe, emit a SET fingerprint.

Most layers differ only in their namespaces, tier, correlation group and comparator, so
they are declared, not written. RISK-only observations never enter a linkage fingerprint,
and only PRESENT values are kept: a missing value leaves the layer out, it never counts
against a link.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from ..contracts import Fingerprint, FingerprintKind, Observation, Purpose, State, Tier, View
from ..registry import layers

Normalise = Callable[[str, str], str]


def linkage_items(
    observations: Sequence[Observation],
    namespaces: Sequence[str] = (),
    prefixes: Sequence[str] = (),
    normalise: Normalise | None = None,
) -> list[tuple[str, str]]:
    items = set()
    for o in observations:
        if o.state is not State.PRESENT or o.purpose is Purpose.RISK or o.value is None:
            continue
        if o.namespace in namespaces or (prefixes and o.namespace.startswith(tuple(prefixes))):
            value = str(o.value).strip()
            if normalise:
                value = normalise(o.namespace, value)
            if value:
                items.add((o.namespace, value))
    return sorted(items)


def set_fingerprint(view: View, layer_id: str, version: str, items) -> Fingerprint | None:
    if not items:
        return None
    return Fingerprint(
        view_id=view.view_id,
        layer_id=layer_id,
        kind=FingerprintKind.SET,
        payload=[{"ns": ns, "value": value} for ns, value in items],
        version=version,
    )


def set_layer(
    *,
    name: str,
    tier: Tier,
    group: str,
    comparator: str,
    namespaces: Sequence[str] = (),
    prefixes: Sequence[str] = (),
    version: str = "1",
    normalise: Normalise | None = None,
    doc: str = "",
):
    """Declare and register a set layer. Returns the class, for introspection in tests."""

    class SetLayer:
        def build(self, view: View, observations: list[Observation]) -> Fingerprint | None:
            items = linkage_items(observations, self.namespaces, self.prefixes, self.normalise)
            return set_fingerprint(view, self.name, self.version, items)

    SetLayer.name = name
    SetLayer.version = version
    SetLayer.kind = FingerprintKind.SET
    SetLayer.tier = tier
    SetLayer.group = group
    SetLayer.comparator = comparator
    SetLayer.namespaces = tuple(namespaces)
    SetLayer.prefixes = tuple(prefixes)
    SetLayer.normalise = staticmethod(normalise) if normalise else None
    SetLayer.__name__ = SetLayer.__qualname__ = "".join(p.title() for p in name.split("."))
    SetLayer.__doc__ = doc
    layers.register(SetLayer)
    return SetLayer
