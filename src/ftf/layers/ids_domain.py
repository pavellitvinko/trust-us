"""Reference layer: the domains a page lives on and sends visitors to (identity tier, A14, A15)."""

from __future__ import annotations

from ..contracts import Fingerprint, FingerprintKind, Observation, State, Tier, View
from ..registry import layers


@layers.register
class IdsDomain:
    name = "ids.domain"
    version = "3"
    kind = FingerprintKind.SET
    tier = Tier.IDENTITY
    group = "domain"
    comparator = "shared_value"
    namespaces = (
        "domain.registrable",
        "url.host",
        "url.host_path",
        "outbound.domain",
        "redirect.domain",
        "ct.shared_cert",
    )

    def build(self, view: View, observations: list[Observation]) -> Fingerprint | None:
        found = {
            (o.namespace, str(o.value))
            for o in observations
            if o.namespace in self.namespaces and o.state is State.PRESENT
        }
        # A host equal to its registrable domain is the same fact: counted twice, one shared
        # domain scored 0.99 and looked like two agreeing identifiers (HAC-54).
        registrable = {value for ns, value in found if ns == "domain.registrable"}
        items = sorted(
            (ns, value)
            for ns, value in found
            if not (ns == "url.host" and value.removeprefix("www.") in registrable)
        )
        if not items:
            return None
        return Fingerprint(
            view_id=view.view_id,
            layer_id=self.name,
            kind=self.kind,
            payload=[{"ns": ns, "value": value} for ns, value in items],
            version=self.version,
        )
