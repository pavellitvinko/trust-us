"""The interface every ad-library source implements, and what a fetch returns."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from .models import LibraryAd, LibraryQuery


class FetchStatus(StrEnum):
    OK = "ok"
    BLOCKED = "blocked"  # CAPTCHA, login wall or failed bot check: the whole run must stop
    FAILED = "failed"  # this query failed for another reason; the run may continue


@dataclass
class FetchResult:
    status: FetchStatus
    ads: list[tuple[LibraryAd, dict[str, Any]]] = field(default_factory=list)  # (ad, raw node)
    raw_files: list[Path] = field(default_factory=list)
    screenshot: Path | None = None
    error: str | None = None


class AdLibrarySource(Protocol):
    """A place ads can be searched. The official API can be added later as another one."""

    name: str

    def fetch(self, query: LibraryQuery, max_ads: int) -> FetchResult: ...

    def screenshot_ad(self, ad_id: str, path: Path) -> bool:
        """Save an image of how the library shows one ad. False when it cannot."""
        ...

    def close(self) -> None: ...
