"""Reference layer: the domains a page lives on and sends visitors to (identity tier, A14, A15)."""

from __future__ import annotations

from ..contracts import Fingerprint, FingerprintKind, Observation, State, Tier, View
from ..registry import layers


@layers.register
class IdsDomain:
    name = "ids.domain"
    version = "1"
    kind = FingerprintKind.SET
    tier = Tier.IDENTITY
    group = "domain"
    comparator = "shared_value"
    namespaces = ("domain.registrable", "url.host_path", "outbound.domain", "redirect.domain")

    def build(self, view: View, observations: list[Observation]) -> Fingerprint | None:
        items = sorted(
            {
                (o.namespace, str(o.value))
                for o in observations
                if o.namespace in self.namespaces and o.state is State.PRESENT
            }
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
