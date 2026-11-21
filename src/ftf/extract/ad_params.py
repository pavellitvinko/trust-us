"""Ad-platform IDs carried in the landing URL (signals A10, A11).

From the page's own URL only. Per-click values (`fbclid`, `clickid`, the `utm_visit`
value) identify one visit, not a campaign, and are never emitted. An access token in
the URL (A11) is stored as found, by team decision.
"""

from __future__ import annotations

import re

from ..contracts import Observation, Purpose, View
from ..registry import Context, extractors
from . import _material as m

_NUMERIC = re.compile(r"^\d{6,24}$")

# parameter -> (namespace, signal, purpose, numeric only)
_PARAMS: dict[str, tuple[str, str, Purpose, bool]] = {
    "ad_id": ("ad.ad_id", "A10", Purpose.LINKAGE, True),
    "adid": ("ad.ad_id", "A10", Purpose.LINKAGE, True),
    "adset_id": ("ad.adset_id", "A10", Purpose.LINKAGE, True),
    "adset_name": ("ad.adset_name", "A10", Purpose.LINKAGE, False),
    "ad_name": ("ad.ad_name", "A10", Purpose.LINKAGE, False),
    "campaign_id": ("ad.campaign_id", "A10", Purpose.LINKAGE, True),
    "utm_id": ("ad.campaign_id", "A10", Purpose.LINKAGE, True),
    "utm_campaign": ("ad.campaign_id", "A10", Purpose.LINKAGE, True),
    "acc": ("ad.account_label", "A10", Purpose.LINKAGE, False),
    "access_token": ("ad.access_token", "A11", Purpose.BOTH, False),
}


@extractors.register
class AdParams:
    name = "ids.ad_params"
    version = "1"

    def run(self, view: View, ctx: Context) -> list[Observation]:
        found = m.landing_url(view)
        if found is None or not found[2]:  # a first-party request is not the landing URL
            return []
        url, source, _ = found
        emit = m.Emitter(view, self.name, self.version)

        def harvest() -> None:
            for name, value in m.query_params(url):
                rule = _PARAMS.get(name)
                if rule is None:
                    continue
                namespace, signal, purpose, numeric = rule
                if numeric and not _NUMERIC.match(value):
                    continue
                emit(
                    signal,
                    namespace,
                    value,
                    m.at(source, f"url:query.{name}"),
                    raw=url,
                    purpose=purpose,
                )

        m.run_steps([harvest])
        return emit.out
