"""Turn a selected library ad into an incident folder the normal ingestion reads.

Layout of `incidents/meta-<ad_id>/`:

- `ad.json`  — the normalised record and the raw library object;
- `ad.png`   — how the library shows the ad (or a copy of its first image);
- `ad_files/` — the original creatives. The `_files` suffix makes ingestion treat them as
  resources of the ad, not as separate image views.
"""

from __future__ import annotations

import json
import logging
import shutil
import urllib.request
from pathlib import Path
from typing import Any

from .models import LibraryAd
from .source import AdLibrarySource

log = logging.getLogger(__name__)

MAX_MEDIA_FILES = 6
MAX_MEDIA_BYTES = 40 * 1024 * 1024
DOWNLOAD_TIMEOUT_S = 30
_EXT = {"image": ".jpg", "video": ".mp4", "video_preview": ".jpg"}


def incident_id(ad: LibraryAd) -> str:
    return f"{ad.source}-{ad.ad_id}"


def _download(url: str, path: Path) -> bool:
    request = urllib.request.Request(url, headers={"User-Agent": "follow-the-fake-research/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_S) as response:
            data = response.read(MAX_MEDIA_BYTES + 1)
    except Exception as exc:  # noqa: BLE001 — a missing creative must not stop the ad
        log.info("creative not downloaded (%s): %s", url[:80], exc)
        return False
    if not data or len(data) > MAX_MEDIA_BYTES:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True


def materialise(
    ad: LibraryAd, raw: dict[str, Any] | None, root: Path, source: AdLibrarySource | None,
    download_media: bool = True,
) -> Path:  # fmt: skip
    folder = Path(root) / incident_id(ad)
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    (folder / "ad.json").write_text(
        json.dumps({"ad": ad.model_dump(mode="json"), "raw": raw}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    saved: list[Path] = []
    if download_media:
        for i, item in enumerate(ad.media[:MAX_MEDIA_FILES]):
            path = folder / "ad_files" / f"{i:02d}_{item.kind}{_EXT[item.kind]}"
            if _download(item.url, path):
                saved.append(path)
    shot = folder / "ad.png"
    if source is None or not source.screenshot_ad(ad.ad_id, shot):
        first_image = next((p for p in saved if p.suffix == ".jpg"), None)
        if first_image is not None:
            shutil.copyfile(first_image, folder / f"ad{first_image.suffix}")
    return folder
