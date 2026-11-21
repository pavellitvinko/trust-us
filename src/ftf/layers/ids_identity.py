"""Identity-tier layers: records in systems the operator controls (catalogue group A, I3, I4).

A match here supports *same campaign*. Two of the namespaces are exclusive — a page has
one campaign record and one owner — so differing values there are a contradiction
(fusion/config.py), never just an absence.
"""

from __future__ import annotations

import unicodedata

from ..contracts import Tier
from ._set_layer import set_layer

set_layer(
    name="ids.campaign",
    tier=Tier.IDENTITY,
    group="campaign",
    comparator="shared_value",
    namespaces=(
        "aio.campaign_uuid",
        "aio.owner_uuid",
        "aio.landing_uuid",
        "aio.flow_uuid",
        "aio.source_uuid",
        "aio.folder_uuid",
        "aio.campaign_human_id",
        "aio.landing_human_id",
        "aio.campaign_name",
        "aio.landing_name",
        "campaign.label",
        "landing.label",
        "nav.landing_uuid",
    ),
    doc="A1, A9, I4: tracker-platform campaign, owner, landing, source and flow records.",
)

set_layer(
    name="ids.tracking",
    tier=Tier.IDENTITY,
    group="tracking",
    comparator="shared_value",
    namespaces=(
        "fb_pixel",
        "gads_id",
        "gads_conversion",
        "gtm_id",
        "ga_id",
        "taboola_id",
        "uet_id",
        "bing_uet",
        "quora_pixel",
        "tiktok_pixel",
        "ym_id",
        "cdn.zone_id",
        "ad.ad_id",
        "ad.campaign_id",
        "ad.adset_id",
        "ad.account_label",
    ),
    doc="A3-A5, A10, C12: pixel and tag IDs of advertising and analytics accounts; ad-platform IDs in the landing URL.",
)

set_layer(
    name="ids.conversion",
    tier=Tier.IDENTITY,
    group="conversion",
    comparator="shared_value",
    namespaces=(
        "aff.network",
        "aff.user_id",
        "aff.offer_id",
        "aff.landing_id",
        "form_endpoint_host",
        "form_endpoint_path",
        "api_key_id",
        "public_key",
        "push_public_key",
        "tracker.host",
        "third_party.host",
    ),
    prefixes=("contact.", "aff."),
    doc="A6-A8, A12, I3, B7: where leads and clicks go — affiliate fields, form endpoints, keys, contacts.",
)


def _normalise_offer(namespace: str, value: str) -> str:
    value = unicodedata.normalize("NFC", value).casefold()
    return " ".join(value.replace("„", "").replace("”", "").replace('"', "").split())


set_layer(
    name="brand.offer",
    tier=Tier.IDENTITY,
    group="brand",
    comparator="shared_value",
    namespaces=("brand.offer", "offer.name"),
    normalise=_normalise_offer,
    doc="A13: invented product or offer names, case-folded; diacritics kept.",
)
