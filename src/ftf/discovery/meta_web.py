"""Search the public Meta Ad Library with a headless browser, logged out.

Reads the JSON the page itself loads (its HTML and its own GraphQL responses) rather than
the rendered DOM. Meta's robots.txt and Terms do not permit automated collection; the team
accepted that for the hackathon, and these guardrails are fixed in code, not settings:

- no login and no imported cookies; a fresh browser context per run;
- default browser settings: no stealth plugins, fingerprint changes or proxy rotation;
- one page at a time, with pauses between page loads and scrolls;
- on a CAPTCHA, a login wall or a failed bot check the fetch reports BLOCKED, the run
  stops, and nothing is retried or solved.
"""

from __future__ import annotations

import contextlib
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from .models import LibraryAd, LibraryQuery, QueryKind
from .parse import ads_from_text, captcha_required
from .source import FetchResult, FetchStatus

log = logging.getLogger(__name__)

BASE = "https://www.facebook.com/ads/library/"
PAUSE_AFTER_LOAD_S = 6.0  # the page runs its own bot check, then loads results
PAUSE_BETWEEN_SCROLLS_S = 3.5
PAUSE_BETWEEN_PAGES_S = 5.0
MAX_IDLE_SCROLLS = 2  # stop scrolling after this many scrolls bring nothing new
NAV_TIMEOUT_MS = 45_000


def library_url(query: LibraryQuery) -> str:
    if query.kind is QueryKind.AD:
        return f"{BASE}?{urlencode({'id': query.terms.strip()})}"
    params: dict[str, str] = {
        "active_status": query.active_status,
        "ad_type": "all",
        "country": query.country,
        "media_type": query.media_type,
    }
    if query.kind is QueryKind.PAGE:
        params["view_all_page_id"] = query.terms.strip()
        params["search_type"] = "page"
    else:
        params["q"] = query.terms.strip()
        params["search_type"] = (
            "keyword_exact_phrase" if query.exact_phrase else "keyword_unordered"
        )
    if query.date_min:
        params["start_date[min]"] = query.date_min
    if query.date_max:
        params["start_date[max]"] = query.date_max
    return f"{BASE}?{urlencode(params)}"


class MetaWebSource:
    name = "meta_web"

    def __init__(self, raw_dir: Path, headed: bool = False):
        self.raw_dir = Path(raw_dir)
        self.headed = headed
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._last_load = 0.0
        self._saved = 0

    # -- browser lifecycle -------------------------------------------------------------

    def _ensure(self) -> Any:
        if self._context is None:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=not self.headed)
            self._context = self._browser.new_context(locale="pl-PL")
            self._context.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        return self._context

    def close(self) -> None:
        for closer in (self._context, self._browser):
            if closer is not None:
                with contextlib.suppress(Exception):  # closing must never fail a run
                    closer.close()
        if self._pw is not None:
            with contextlib.suppress(Exception):
                self._pw.stop()
        self._pw = self._browser = self._context = None

    def _pace(self) -> None:
        wait = PAUSE_BETWEEN_PAGES_S - (time.monotonic() - self._last_load)
        if wait > 0:
            time.sleep(wait)
        self._last_load = time.monotonic()

    def _save(self, prefix: str, text: str) -> Path:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self._saved += 1
        path = self.raw_dir / f"{prefix}_{self._saved:04d}.txt"
        path.write_text(text, encoding="utf-8")
        return path

    @staticmethod
    def _login_wall(page: Any) -> bool:
        url = page.url
        if "/login" in url or "/checkpoint" in url:
            return True
        try:
            return page.locator("input[name='email'], input[name='pass']").first.is_visible()
        except Exception:  # noqa: BLE001
            return False

    # -- fetch -------------------------------------------------------------------------

    def fetch(self, query: LibraryQuery, max_ads: int) -> FetchResult:
        context = self._ensure()
        page = context.new_page()
        result = FetchResult(status=FetchStatus.OK)
        found: dict[str, tuple[LibraryAd, dict[str, Any]]] = {}
        bodies: list[str] = []

        def on_response(response: Any) -> None:
            if response.request.resource_type not in ("document", "xhr", "fetch"):
                return
            try:
                text = response.text()
            except Exception:  # noqa: BLE001 — redirects and aborted requests have no body
                return
            if "ad_archive_id" in text or "captcha_required" in text:
                bodies.append(text)

        page.on("response", on_response)
        try:
            self._pace()
            page.goto(library_url(query), wait_until="domcontentloaded")
            page.wait_for_timeout(int(PAUSE_AFTER_LOAD_S * 1000))
            idle = 0
            while True:
                new_bodies, bodies[:] = list(bodies), []
                for text in new_bodies:
                    result.raw_files.append(self._save(query.query_id, text))
                    if captcha_required(text):
                        return self._blocked(page, result, "the library asked for a CAPTCHA")
                    for ad, raw in ads_from_text(text):
                        found.setdefault(ad.ad_id, (ad, raw))
                if not found and self._login_wall(page):
                    return self._blocked(page, result, "the library showed a login wall")
                if len(found) >= max_ads or query.kind is QueryKind.AD:
                    break
                before = len(found)
                page.mouse.wheel(0, 6000)
                page.wait_for_timeout(int(PAUSE_BETWEEN_SCROLLS_S * 1000))
                if not bodies and len(found) == before:
                    idle += 1
                    if idle >= MAX_IDLE_SCROLLS:
                        break
                else:
                    idle = 0
        except Exception as exc:  # noqa: BLE001 — one failed query must not end the run
            log.warning("ad library fetch failed for %s: %s", query.label(), exc)
            result.status, result.error = FetchStatus.FAILED, f"{type(exc).__name__}: {exc}"[:300]
        finally:
            with contextlib.suppress(Exception):
                page.close()
        result.ads = list(found.values())[:max_ads]
        return result

    def _blocked(self, page: Any, result: FetchResult, reason: str) -> FetchResult:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        shot = self.raw_dir / "blocked.png"
        with contextlib.suppress(Exception):
            page.screenshot(path=str(shot))
            result.screenshot = shot
        result.status, result.error = FetchStatus.BLOCKED, reason
        return result

    def screenshot_ad(self, ad_id: str, path: Path) -> bool:
        """Open the ad's own library page and save how the library shows it."""
        context = self._ensure()
        page = context.new_page()
        try:
            self._pace()
            page.goto(f"{BASE}?{urlencode({'id': ad_id})}", wait_until="domcontentloaded")
            page.wait_for_timeout(int(PAUSE_AFTER_LOAD_S * 1000))
            if self._login_wall(page):
                return False
            path.parent.mkdir(parents=True, exist_ok=True)
            # The ad opens in a dialog that shows its Library ID; other dialogs may exist hidden.
            dialog = page.locator("div[role='dialog']").filter(has_text=ad_id).last
            try:
                dialog.wait_for(state="visible", timeout=15_000)
                with contextlib.suppress(Exception):  # creatives may keep loading; good enough
                    page.wait_for_load_state("networkidle", timeout=8_000)
                dialog.screenshot(path=str(path))
            except Exception:  # noqa: BLE001 — no dialog: keep what the page shows
                page.screenshot(path=str(path))
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("could not capture ad %s: %s", ad_id, exc)
            return False
        finally:
            with contextlib.suppress(Exception):
                page.close()
