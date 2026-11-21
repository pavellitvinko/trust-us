"""Live capture: headless browser into the bundle layout ingestion already reads. Evidence track.

Playwright is imported only when a capture runs, so every other command works without a browser.
"""

from .browser import CaptureResult, archived_capture, capture_url
from .bundle import SCHEMA, Hop, HopKind, Manifest, Status, build_trace, read_manifest

__all__ = [
    "SCHEMA",
    "CaptureResult",
    "Hop",
    "HopKind",
    "Manifest",
    "Status",
    "archived_capture",
    "build_trace",
    "capture_url",
    "read_manifest",
]
