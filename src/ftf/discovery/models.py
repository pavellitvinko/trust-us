"""Data shapes for ad-library discovery: what is asked, and what an ad looks like once read."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SOURCE_META = "meta"


class QueryKind(StrEnum):
    KEYWORDS = "keywords"  # free-text search
    PAGE = "page"  # every ad of one advertiser page
    AD = "ad"  # one ad by its Ad Library ID


class QueryOrigin(StrEnum):
    WATCHLIST = "watchlist"  # written by an analyst
    PIVOT = "pivot"  # derived from material already in the store
    MODEL = "model"  # proposed by the model from known campaigns
    MANUAL = "manual"  # given once on the command line or in the interface


class LibraryQuery(BaseModel):
    """One search against an ad library. `query_id` is a hash of what is searched, not why."""

    model_config = ConfigDict(extra="forbid")

    source: str = SOURCE_META
    kind: QueryKind = QueryKind.KEYWORDS
    terms: str = ""  # keywords, a page ID or an ad ID, depending on `kind`
    exact_phrase: bool = False
    country: str = "PL"
    active_status: Literal["all", "active", "inactive"] = "all"
    media_type: Literal["all", "image", "video", "meme", "none"] = "all"
    date_min: str | None = None  # YYYY-MM-DD; None means "since this query last ran"
    date_max: str | None = None
    origin: QueryOrigin = QueryOrigin.MANUAL
    reason: str = ""

    @property
    def query_id(self) -> str:
        key = [self.source, self.kind.value, self.terms.strip().lower(), self.exact_phrase,
               self.country, self.active_status, self.media_type]  # fmt: skip
        return hashlib.sha1(json.dumps(key).encode()).hexdigest()[:12]

    def label(self) -> str:
        if self.kind is QueryKind.PAGE:
            return f"page {self.terms}"
        if self.kind is QueryKind.AD:
            return f"ad {self.terms}"
        return f'"{self.terms}"' if self.exact_phrase else self.terms


class Media(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["image", "video", "video_preview"]
    url: str


class LibraryAd(BaseModel):
    """One ad as the library shows it. Every field is optional except the ID: ads differ a lot."""

    model_config = ConfigDict(extra="forbid")

    source: str = SOURCE_META
    ad_id: str
    page_id: str | None = None
    page_name: str | None = None
    page_url: str | None = None
    page_categories: list[str] = Field(default_factory=list)
    page_like_count: int | None = None
    is_active: bool | None = None
    start_date: str | None = None  # YYYY-MM-DD, UTC
    end_date: str | None = None
    platforms: list[str] = Field(default_factory=list)
    bodies: list[str] = Field(default_factory=list)
    titles: list[str] = Field(default_factory=list)
    link_descriptions: list[str] = Field(default_factory=list)
    link_urls: list[str] = Field(default_factory=list)
    captions: list[str] = Field(default_factory=list)  # the display domain under the creative
    cta_texts: list[str] = Field(default_factory=list)
    display_format: str | None = None
    media: list[Media] = Field(default_factory=list)
    collation_id: str | None = None  # Meta groups identical copies of one ad
    collation_count: int | None = None
    # Meta's enforcement state as the library shows it: ELIGIBLE, TAKEN_DOWN (ad removed for
    # breaking the ad standards), AD_ACCOUNT_DISABLED (the advertiser's account was shut down).
    enforcement: str | None = None
    is_deemed_finserv: bool | None = None
    library_url: str = ""

    @property
    def enforced(self) -> bool:
        """Meta itself removed the ad or shut down the advertiser's account."""
        return self.enforcement in {"TAKEN_DOWN", "AD_ACCOUNT_DISABLED"}

    def text(self) -> str:
        """All human-readable text of the ad, for screening and search."""
        parts = [self.page_name or "", *self.bodies, *self.titles, *self.link_descriptions,
                 *self.captions, *self.cta_texts]  # fmt: skip
        return "\n".join(dict.fromkeys(p.strip() for p in parts if p and p.strip()))


class Triage(BaseModel):
    """Why an ad was or was not picked for full analysis. Never a statement about links."""

    model_config = ConfigDict(extra="forbid")

    known_matches: list[dict[str, Any]] = Field(default_factory=list)  # {ns, value, views}
    verdict: Literal["yes", "no", "unsure", "not_screened"] = "not_screened"
    offer_kind: str | None = None
    reasons: list[str] = Field(default_factory=list)
    score: float = 0.0
