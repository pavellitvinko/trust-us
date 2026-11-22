"""Visitor profiles for the cloaking differential (HAC-15). No browser here, so this is testable.

A profile is who the capture pretends to be: device, user agent, referrer, and which form of
the URL it opens. The *ad click* profile is a phone arriving from a social ad with the
original query string; *bare* is a desktop typing the address; *crawler* is a search bot.
A cloaker that shows the offer to one and a white page to another is caught by comparing them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit

# Parameters an ad network appends to a click. One of them is what a gating cloaker checks.
CLICK_IDS = ("fbclid", "gclid", "ttclid", "msclkid", "yclid", "wbraid", "gbraid")
# Synthetic, so it is recognisably ours in any log; Facebook click ids have this shape.
SYNTHETIC_FBCLID = "IwAR0ftfCloakingTest0000000000000000000000000000000000000000000"

DESKTOP_VIEWPORT = {"width": 1366, "height": 900}
MOBILE_VIEWPORT = {"width": 412, "height": 915}
SOCIAL_REFERER = "https://l.facebook.com/"
GOOGLEBOT_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"


@dataclass(frozen=True)
class Profile:
    name: str
    device: str  # desktop | mobile
    referer: str | None = None
    keep_query: bool = True
    add_click_id: bool = False  # append a synthetic fbclid when the URL has no click id
    user_agent: str | None = None  # None: a regular Chrome UA for the device

    @property
    def viewport(self) -> dict[str, int]:
        return dict(MOBILE_VIEWPORT if self.device == "mobile" else DESKTOP_VIEWPORT)

    @property
    def is_mobile(self) -> bool:
        return self.device == "mobile"

    def user_agent_for(self, browser_version: str) -> str:
        if self.user_agent:
            return self.user_agent
        # Headless Chromium announces itself as HeadlessChrome, which cloakers filter on.
        major = browser_version.split(".")[0] or "130"
        if self.is_mobile:
            return (
                "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
                f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Mobile Safari/537.36"
            )
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
        )

    def url_for(self, url: str) -> tuple[str, bool]:
        """(the URL this profile opens, whether a synthetic click id was added).

        The original query is kept byte for byte: a click id is appended to the raw string,
        never re-encoded, so a server checking a signature over it sees the real click.
        """
        parts = urlsplit(url)
        if not self.keep_query:
            return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", "", "")), False
        names = {k.lower() for k, _ in parse_qsl(parts.query, keep_blank_values=True)}
        if not self.add_click_id or names & set(CLICK_IDS):
            return url, False
        query = f"{parts.query}&" if parts.query else ""
        return urlunsplit(parts._replace(query=f"{query}fbclid={SYNTHETIC_FBCLID}")), True

    def ua_override(self, browser_version: str) -> dict[str, Any]:
        """`Emulation.setUserAgentOverride` parameters.

        Overriding the User-Agent header alone leaves `sec-ch-ua` and
        `navigator.userAgentData` saying HeadlessChrome and `navigator.platform` saying the
        host OS; a cloaker checking those serves every profile its white page.
        """
        major = browser_version.split(".")[0] or "130"
        full = browser_version or f"{major}.0.0.0"
        if self.user_agent:  # a crawler: no Chrome brand, a Linux host
            brands = [{"brand": "Chromium", "version": major}]
            platform, nav_platform, version, model, arch = "Linux", "Linux x86_64", "", "", "x86"
        elif self.is_mobile:
            brands = [
                {"brand": "Google Chrome", "version": major},
                {"brand": "Chromium", "version": major},
            ]
            platform, nav_platform, version, model, arch = (
                "Android",
                "Linux armv81",
                "10.0.0",
                "K",
                "",
            )
        else:
            brands = [
                {"brand": "Google Chrome", "version": major},
                {"brand": "Chromium", "version": major},
            ]
            platform, nav_platform, version, model, arch = "Windows", "Win32", "10.0.0", "", "x86"
        brands.append({"brand": "Not?A_Brand", "version": "99"})
        return {
            "userAgent": self.user_agent_for(browser_version),
            "acceptLanguage": "pl-PL,pl,en",  # Chrome adds the q weights itself
            "platform": nav_platform,
            "userAgentMetadata": {
                "brands": brands,
                "fullVersionList": [
                    {**b, "version": full} if b["version"] == major else b for b in brands
                ],
                "fullVersion": full,
                "platform": platform,
                "platformVersion": version,
                "architecture": arch,
                "model": model,
                "mobile": self.is_mobile,
                "bitness": "" if self.is_mobile else "64",
                "wow64": False,
            },
        }


DESKTOP = Profile("desktop", "desktop")  # the plain capture HAC-14 has always done
AD_CLICK = Profile("ad_click", "mobile", referer=SOCIAL_REFERER, add_click_id=True)
BARE = Profile("bare", "desktop", keep_query=False)
CRAWLER = Profile("crawler", "desktop", keep_query=False, user_agent=GOOGLEBOT_UA)

PROFILES = {p.name: p for p in (DESKTOP, AD_CLICK, BARE, CRAWLER)}
