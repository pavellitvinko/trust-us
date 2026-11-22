"""Turn a bundle's `probe.json` into observations (docs/probe-agent.md)."""

from __future__ import annotations

import json
import os
from pathlib import PurePosixPath

from ..contracts import Observation, Purpose, Source, View
from ..probe.policy import is_bait
from ..registry import Context, extractors

# Which payment service a site uses is shared by unrelated sites: context, never a link.
_RISK_ONLY = {"payment.provider"}


@extractors.register
class ProbeFindings:
    name = "probe.findings"
    version = "1"

    def run(self, view: View, ctx: Context) -> list[Observation]:
        anchor = view.primary or (view.attachments[0] if view.attachments else None)
        if anchor is None:
            return []
        relative = (PurePosixPath(anchor).parent / "probe.json").as_posix()
        try:
            data = json.loads(ctx.read_text(view, relative))
        except (OSError, ValueError):
            return []
        if not str(data.get("schema", "")).startswith("ftf.probe/"):
            return []
        bait = os.environ.get("FTF_PROBE_EMAIL", "")
        bait_domain = os.environ.get("FTF_PROBE_MAIL_DOMAIN", "")
        out = []
        for index, item in enumerate(data.get("findings", [])):
            value = str(item.get("value", ""))
            if is_bait(value, bait, bait_domain):  # it would link every probed site
                continue
            out.append(
                Observation(
                    view_id=view.view_id,
                    signal_id=str(item.get("signal", "P0")),
                    namespace=str(item["ns"]),
                    value=value,
                    raw=value,
                    purpose=Purpose.RISK if item["ns"] in _RISK_ONLY else Purpose.LINKAGE,
                    source=Source(
                        file=relative,
                        locator=f"probe:findings[{index}] {item.get('where', '')}".strip(),
                    ),
                    extractor=self.name,
                    version=self.version,
                )
            )
        return out
