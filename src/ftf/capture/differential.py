"""Cloaking differential, capture side (HAC-15): one URL under several visitor profiles.

Each profile is captured into its own bundle, `<incident>/<profile>/`, so ingestion turns
every variant into its own view. The bundles share `differential` in their manifests; the
comparison itself runs after extraction (`grouping/cloaking.py`), with the pipeline's own
text and structure comparators.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .browser import CaptureResult, capture_url
from .bundle import now_iso
from .profiles import AD_CLICK, BARE, CRAWLER, Profile

DEFAULT_PROFILES = (AD_CLICK, BARE)


@dataclass
class DifferentialResult:
    url: str
    incident: Path
    captures: dict[str, CaptureResult] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """At least two variants loaded a page, so there is something to compare."""
        return sum(1 for r in self.captures.values() if r.ok) >= 2


def differential_profiles(crawler: bool = False) -> tuple[Profile, ...]:
    return (*DEFAULT_PROFILES, CRAWLER) if crawler else DEFAULT_PROFILES


def capture_differential(
    url: str,
    incident_dir: Path,
    *,
    profiles: tuple[Profile, ...] = DEFAULT_PROFILES,
    proxy: str | None = None,
    timeout: float = 30.0,
    settle: float = 5.0,
) -> DifferentialResult:
    """Capture `url` once per profile, each in a fresh browser, into `incident_dir/<profile>/`."""
    if not urlsplit(url).scheme:
        url = "https://" + url
    result = DifferentialResult(url=url, incident=incident_dir)
    started = now_iso()
    # Variants are paired by run, not by URL: a second run, or a bundle a previous run
    # left behind in the same folder, must never be compared with this one.
    run = f"{started}-{uuid.uuid4().hex[:8]}"
    for profile in profiles:
        result.captures[profile.name] = capture_url(
            url,
            incident_dir / profile.name,
            proxy=proxy,
            timeout=timeout,
            settle=settle,
            profile=profile,
            differential=url,
            differential_run=run,
        )
    summary = {
        "schema": "ftf.differential/1",
        "url": url,
        "run": run,
        "captured_at": started,
        "variants": {
            name: {
                "page_url": r.manifest.page_url,
                "final_url": r.manifest.final_url,
                "status": r.manifest.status.value,
                "bundle": name,
            }
            for name, r in result.captures.items()
        },
    }
    (incident_dir / "differential.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return result
