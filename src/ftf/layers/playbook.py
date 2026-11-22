"""Playbook-tier layers from model output (docs/architecture.md section 6, 8).

`pattern.mo`: one score per pattern of the model-discovered library. `kit.absence`: the
three-state vector over the expectation profile, compared only between views of the same
kind of page. `entity.cast`: (entity, role) pairs, plus the roles alone so that a different
celebrity in the same role still counts. All playbook tier: they can never merge campaigns.
Inputs come from the Perception track; nothing here calls a model.
"""

from __future__ import annotations

import json

from ..contracts import Fingerprint, FingerprintKind, Observation, Purpose, State, Tier, View
from ..registry import layers
from ._set_layer import set_layer


def _parse(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}


@layers.register
class PatternMo:
    name = "pattern.mo"
    version = "1"
    kind = FingerprintKind.SET
    tier = Tier.PLAYBOOK
    group = "pattern"
    comparator = "pattern_overlap"
    namespaces = ("pattern.score",)
    min_score = 0.5  # "uncertain" and "absent" patterns do not enter the fingerprint

    def build(self, view: View, observations: list[Observation]) -> Fingerprint | None:
        scores: dict[str, float] = {}
        for o in observations:
            if o.namespace not in self.namespaces or o.state is not State.PRESENT:
                continue
            d = _parse(o.value)
            pattern, score = d.get("pattern"), float(d.get("score", 0.0))
            if pattern and score >= self.min_score:
                scores[pattern] = max(scores.get(pattern, 0.0), score)
        if not scores:
            return None
        return Fingerprint(
            view_id=view.view_id,
            layer_id=self.name,
            kind=self.kind,
            payload=[
                {"ns": "pattern", "value": p, "score": round(s, 3)}
                for p, s in sorted(scores.items())
            ],
            version=self.version,
        )


@layers.register
class KitAbsence:
    name = "kit.absence"
    version = "2"
    kind = FingerprintKind.SET
    tier = Tier.PLAYBOOK
    group = "absence"
    comparator = "absence_agreement"
    namespaces = ("expect.element", "page.kind")

    def build(self, view: View, observations: list[Observation]) -> Fingerprint | None:
        kind = next(
            (str(o.value) for o in observations if o.namespace == "page.kind" and o.value), None
        )
        items = sorted(
            {
                (str(o.value), o.state.value, o.state is State.ABSENT and o.confidence < 1.0)
                for o in observations
                if o.namespace == "expect.element" and o.state is not State.NOT_OBSERVABLE
            }
        )
        if kind is None or not items:
            return None
        return Fingerprint(
            view_id=view.view_id,
            layer_id=self.name,
            kind=self.kind,
            payload={
                "kind": kind,
                "items": [
                    {"ns": "element", "value": v, "state": s, **({"weak": True} if w else {})}
                    for v, s, w in items
                ],
            },
            version=self.version,
        )


# Fixture form `entity.cast` {"entity", "role"} and the Perception track's
# `page.entity.person` / `page.entity.organisation` / `page.entity.persona` {"name", "role"}.
CAST_NAMESPACES = (
    "entity.cast",
    "page.entity.person",
    "page.entity.organisation",
    "page.entity.persona",
)


def _cast_items(observations: list[Observation]):
    for o in observations:
        if o.namespace not in CAST_NAMESPACES or o.state is not State.PRESENT:
            continue
        if o.purpose is Purpose.RISK:
            continue
        d = _parse(o.value)
        if isinstance(o.value, str) and "|" in o.value and not d:
            entity, _, role = o.value.partition("|")
            d = {"entity": entity, "role": role}
        entity = d.get("entity") or d.get("name")
        role = d.get("role") or ("persona" if o.namespace.endswith("persona") else None)
        if entity and role:
            yield str(entity).strip(), str(role).strip().lower()


def cast_names(observations: list[Observation]) -> list[str]:
    return sorted({entity for entity, _ in _cast_items(observations)})


@layers.register
class EntityCast:
    name = "entity.cast"
    version = "1"
    kind = FingerprintKind.SET
    tier = Tier.PLAYBOOK
    group = "cast"
    comparator = "set_overlap"
    namespaces = CAST_NAMESPACES

    def build(self, view: View, observations: list[Observation]) -> Fingerprint | None:
        pairs = sorted(set(_cast_items(observations)))
        if not pairs:
            return None
        items = [{"ns": "entity.cast", "value": f"{e}|{r}"} for e, r in pairs]
        return Fingerprint(
            view_id=view.view_id,
            layer_id=self.name,
            kind=self.kind,
            payload=items,
            version=self.version,
        )


@layers.register
class EntityRoles:
    """The cast's roles alone: a different celebrity in the same role still counts. Same
    correlation group as entity.cast, so the two are never counted twice."""

    name = "entity.roles"
    version = "1"
    kind = FingerprintKind.SET
    tier = Tier.PLAYBOOK
    group = "cast"
    comparator = "set_overlap"
    namespaces = CAST_NAMESPACES

    def build(self, view: View, observations: list[Observation]) -> Fingerprint | None:
        roles = sorted({r for _, r in _cast_items(observations)})
        if not roles:
            return None
        return Fingerprint(
            view_id=view.view_id,
            layer_id=self.name,
            kind=self.kind,
            payload=[{"ns": "entity.role", "value": r} for r in roles],
            version=self.version,
        )


set_layer(
    name="visual.layout",
    tier=Tier.PLAYBOOK,
    group="layout",
    comparator="set_overlap",
    namespaces=("layout.block",),
    doc="E4 stand-in: coarse layout blocks named by the screenshot reader; the embedding is the upgrade.",
)
