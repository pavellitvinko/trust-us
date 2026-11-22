"""Seed stoplist: values shared by unrelated sites, which never count towards a link.

Four kinds of entry (docs/architecture.md section 9):
- CDN, platform and library hosts;
- common libraries by content hash (prefixes from docs/baseline-report.md appendix D);
- ad-platform standard URL parameters, which every Meta- or Google-sourced landing shares;
- unresolved template macros (`{{campaign}}`, `%CLICKID%`), which are placeholders, not values.

The rarity weighting in `weights.py` adds to this automatically: a value held by many
independent campaigns falls to weight 0 and is reported as stoplisted.
"""

from __future__ import annotations

import re

HOST_NAMESPACES = frozenset(
    {
        "domain.registrable",
        "outbound.domain",
        "redirect.domain",
        "tracker.host",
        "form_endpoint_host",
        "cdn.host",
        "video.hls_host",
        "http.server",
        "third_party.host",
        "url.host",
    }
)
HASH_NAMESPACES = frozenset(
    {
        "asset.sha256",
        "asset.sha256.content",
        "asset.sha256.avatar",
        "asset.sha256.code",
        "firstparty.sha256",
        "avatar.sha256",
    }
)
PARAM_NAMESPACES = frozenset({"url.param_name", "url.param_shape"})

SEED_HOSTS = frozenset(
    {
        # Public link shorteners and platform link wrappers: a redirect hop through one is
        # shared by unrelated campaigns (redirect.domain).
        "bit.ly",
        "t.co",
        "tinyurl.com",
        "goo.gl",
        "ow.ly",
        "lnkd.in",
        "is.gd",
        "buff.ly",
        "rebrand.ly",
        "cutt.ly",
        "shorturl.at",
        "linktr.ee",
        "cdnjs.cloudflare.com",
        "cdn.jsdelivr.net",
        "unpkg.com",
        "googleapis.com",
        "gstatic.com",
        "google.com",
        "google-analytics.com",
        "googletagmanager.com",
        "googleadservices.com",
        "googlesyndication.com",
        "doubleclick.net",
        "facebook.com",
        "facebook.net",
        "fbcdn.net",
        "instagram.com",
        "cloudflare.com",
        "cloudflareinsights.com",
        "jquery.com",
        "bootstrapcdn.com",
        "fontawesome.com",
        "fonts.googleapis.com",
        "youtube.com",
        "ytimg.com",
        "twitter.com",
        "tiktok.com",
        "bing.com",
        "yandex.ru",
        "mc.yandex.ru",
        "taboola.com",
        "quora.com",
        "localhost",
    }
)

# Content-hash prefixes of files every site may carry (baseline appendix D).
SEED_HASH_PREFIXES = (
    "ff1523fb7389539c",  # jquery-3.6.0.min.js
    "cbdf0712673ad947",  # Facebook fbevents.js
    "97f9b10039b05e1a",  # browser "saved_resource" support document
)

# Parameters the ad platforms append to every landing URL (catalogue B1).
SEED_URL_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_content",
        "utm_term",
        "utm_id",
        "fbclid",
        "gclid",
        "gbraid",
        "wbraid",
        "msclkid",
        "ttclid",
        "yclid",
        "ad_id",
        "adset_id",
        "adset_name",
        "ad_name",
        "campaign_id",
        "campaign_name",
        "placement",
        "site_source_name",
        "ref",
        "source",
        "lang",
        "hl",
    }
)

# The platforms' own value shapes for their standard parameters (catalogue B2). A *different*
# shape under a standard name (`utm_id:token20mixed`) is a kit signal and stays.
SEED_PARAM_SHAPES = frozenset(
    {
        "utm_id:digits18",
        "utm_campaign:digits18",
        "utm_source:word",
        "utm_medium:word",
        "utm_content:digits18",
        "fbclid:token",
        "gclid:token",
        "ad_id:digits18",
        "adset_id:digits18",
        "campaign_id:digits18",
    }
)

_MACRO = re.compile(
    r"^\s*(\{\{.*\}\}|\{[A-Za-z_][\w.]*\}|\[\[.*\]\]|%[A-Z_]{2,}%|\$\{.*\}|__[A-Z_]+__)\s*$"
)


def host_matches(value: str, hosts: frozenset[str] = SEED_HOSTS) -> bool:
    host = value.lower().rstrip(".")
    for h in hosts:
        if host == h or host.endswith("." + h):
            return True
    return False


def is_macro(value: str) -> bool:
    return bool(_MACRO.match(value))


def is_seed_stoplisted(namespace: str, value: object) -> bool:
    """True when the seed list says this value carries no information about a link."""
    if not isinstance(value, str):
        return False
    if is_macro(value):
        return True
    if namespace in HOST_NAMESPACES and host_matches(value):
        return True
    if namespace in HASH_NAMESPACES and value.lower().startswith(SEED_HASH_PREFIXES):
        return True
    if namespace == "url.param_shape":
        return value.lower() in SEED_PARAM_SHAPES
    return namespace in PARAM_NAMESPACES and value.lower() in SEED_URL_PARAMS
