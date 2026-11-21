"""Victim-run layers (docs/victim-visitor-requirements.md VV-52, VV-53). All playbook tier.

Built from the `victim.run` extractor's observations on a run's entry stage. Playbook tier
means they can never merge two campaigns on their own; the identifiers the run uncovers on
later stages (form endpoints, hand-off and redirect hosts) are what link campaigns.

- `victim.ask` (set): asked actions and asked data, normalised (`action:leave_contact`, `data:phone`).
- `victim.money` (vector): [entry, promised return, ratio, days], log-scaled.
- `victim.pitch` (vector): embedding of the masked pitch retelling.
- `victim.levers` (vector): multi-hot over the fixed lever list in `ftf.victim.perceive`.
  The model-discovered pattern library (architecture section 6) replaces it when it exists.
- `funnel.shape` (hash kind, ordered values): stage kinds in order, compared by edit distance.
"""

from __future__ import annotations

from ..contracts import Fingerprint, FingerprintKind, Observation, State, Tier, View
from ..extract.victim_run import money_vector
from ..registry import layers

GROUP = "victim.perception"


def _present(observations: list[Observation], namespace: str) -> list[Observation]:
    return [o for o in observations if o.namespace == namespace and o.state is State.PRESENT]


@layers.register
class VictimAsk:
    name = "victim.ask"
    version = "1"
    kind = FingerprintKind.SET
    tier = Tier.PLAYBOOK
    group = GROUP
    comparator = "victim.jaccard"

    def build(self, view: View, observations: list[Observation]) -> Fingerprint | None:
        values = sorted({str(o.value) for o in _present(observations, "victim.ask")})
        if not values:
            return None
        return Fingerprint(
            view_id=view.view_id,
            layer_id=self.name,
            kind=self.kind,
            payload=[{"ns": "victim.ask", "value": v} for v in values],
            version=self.version,
        )


@layers.register
class VictimMoney:
    name = "victim.money"
    version = "1"
    kind = FingerprintKind.VECTOR
    tier = Tier.PLAYBOOK
    group = GROUP
    comparator = "victim.money_close"

    def build(self, view: View, observations: list[Observation]) -> Fingerprint | None:
        items = [
            o.value for o in _present(observations, "victim.money") if isinstance(o.value, dict)
        ]
        vector = money_vector(items)
        if not any(vector):
            return None
        return Fingerprint(
            view_id=view.view_id,
            layer_id=self.name,
            kind=self.kind,
            payload={"model": "victim.money.v1", "values": vector},
            version=self.version,
        )


@layers.register
class VictimPitch:
    name = "victim.pitch"
    version = "1"
    kind = FingerprintKind.VECTOR
    tier = Tier.PLAYBOOK
    group = GROUP
    comparator = "victim.cosine"

    def build(self, view: View, observations: list[Observation]) -> Fingerprint | None:
        found = _present(observations, "victim.pitch.embedding")
        if not found or not isinstance(found[0].value, dict):
            return None
        return Fingerprint(
            view_id=view.view_id,
            layer_id=self.name,
            kind=self.kind,
            payload={"model": found[0].value["model"], "values": found[0].value["values"]},
            version=self.version,
        )


@layers.register
class VictimLevers:
    name = "victim.levers"
    version = "1"
    kind = FingerprintKind.VECTOR
    tier = Tier.PLAYBOOK
    group = GROUP
    comparator = "victim.cosine"

    def build(self, view: View, observations: list[Observation]) -> Fingerprint | None:
        from ..victim.perceive import LEVERS

        pressed = {str(o.value) for o in _present(observations, "victim.lever")}
        if not pressed:
            return None
        return Fingerprint(
            view_id=view.view_id,
            layer_id=self.name,
            kind=self.kind,
            payload={
                "model": "victim.levers.v1",
                "labels": list(LEVERS),
                "values": [1.0 if lever in pressed else 0.0 for lever in LEVERS],
            },
            version=self.version,
        )


@layers.register
class FunnelShape:
    name = "funnel.shape"
    version = "1"
    kind = FingerprintKind.HASH  # the contract has no sequence kind: values are kept in order
    tier = Tier.PLAYBOOK
    group = "funnel.shape"
    comparator = "victim.sequence_edit"

    def build(self, view: View, observations: list[Observation]) -> Fingerprint | None:
        found = _present(observations, "funnel.sequence")
        if not found or not found[0].value:
            return None
        return Fingerprint(
            view_id=view.view_id,
            layer_id=self.name,
            kind=self.kind,
            payload={"algo": "stage-sequence", "values": [str(v) for v in found[0].value]},
            version=self.version,
        )
