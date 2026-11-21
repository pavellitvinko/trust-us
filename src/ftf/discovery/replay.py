"""Replay saved library responses through the same parser, with no network.

A replay directory holds response bodies (`*.txt`, `*.json`, `*.html`) as the live collector
saves them. Every query sees the ads that match it: keyword queries match the ad text,
page queries the page ID, ad queries the ad ID. Used by tests and for an offline demo.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from .models import LibraryAd, LibraryQuery, QueryKind
from .parse import ads_from_text
from .source import FetchResult, FetchStatus

_WORD = re.compile(r"\w+", re.UNICODE)


def _matches(query: LibraryQuery, ad: LibraryAd) -> bool:
    if query.kind is QueryKind.PAGE:
        return ad.page_id == query.terms.strip()
    if query.kind is QueryKind.AD:
        return ad.ad_id == query.terms.strip()
    text = (ad.text() + "\n" + "\n".join(ad.link_urls)).lower()
    terms = query.terms.strip().lower()
    if not terms:
        return False
    if query.exact_phrase:
        return terms in text
    words = set(_WORD.findall(text))
    return all(w in words or w in text for w in _WORD.findall(terms))


def _in_window(query: LibraryQuery, ad: LibraryAd) -> bool:
    if query.date_min and ad.end_date and ad.end_date < query.date_min:
        return False
    if query.date_max and ad.start_date and ad.start_date > query.date_max:
        return False
    if query.active_status == "active" and ad.is_active is False:
        return False
    return not (query.active_status == "inactive" and ad.is_active is True)


class ReplaySource:
    name = "replay"

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self._ads: dict[str, tuple[LibraryAd, dict]] = {}
        self._images: dict[str, Path] = {}
        for path in sorted(self.directory.rglob("*")):
            if path.suffix.lower() in {".txt", ".json", ".html"} and path.is_file():
                for ad, raw in ads_from_text(path.read_text(encoding="utf-8", errors="replace")):
                    self._ads.setdefault(ad.ad_id, (ad, raw))
            elif path.suffix.lower() == ".png" and path.stem.startswith("ad_"):
                self._images[path.stem.removeprefix("ad_")] = path

    def fetch(self, query: LibraryQuery, max_ads: int) -> FetchResult:
        hits = [
            (ad, raw)
            for ad, raw in self._ads.values()
            if _matches(query, ad) and _in_window(query, ad)
        ]
        return FetchResult(status=FetchStatus.OK, ads=hits[:max_ads])

    def screenshot_ad(self, ad_id: str, path: Path) -> bool:
        source = self._images.get(ad_id)
        if source is None:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, path)
        return True

    def close(self) -> None:
        return None
