"""Search the public Meta Ad Library in a browser a person can watch and step into.

Reads the JSON the page itself loads (its HTML and its own GraphQL responses) rather than
the rendered DOM. Meta's robots.txt and Terms do not permit automated collection; the team
accepted that for the hackathon, and these guardrails are fixed in code, not settings:

- the code never logs in, never types credentials and never solves a CAPTCHA;
- with a visible window (the default) a person may do those things: when the library asks
  for a CAPTCHA, a login or a security check, the run pauses, brings the window to the
  front, waits for the person (up to a limit), then continues where it stopped;
- without a visible window, or when the person does not act in time, the fetch reports
  BLOCKED and the run stops, as before;
- no imported cookies; a fresh browser context per run, so a login is not kept afterwards;
- default browser settings: no stealth plugins, fingerprint changes or proxy rotation;
- one page at a time, with pauses between page loads and scrolls.

A person who logs in does so with their own account, and the run's browsing is then tied to
that account; Meta may restrict accounts that browse automatically.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable
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
DEFAULT_HUMAN_WAIT_S = 600  # how long a run waits for a person to clear an obstacle
HUMAN_POLL_S = 3.0
CAPTCHA_RELOAD_S = 45.0  # with no CAPTCHA widget in sight, reload the search this often
MAX_HUMAN_WAITS_PER_PAGE = 3

LOGIN, CHECKPOINT, CAPTCHA = "login", "checkpoint", "captcha"
OBSTACLE_TEXT = {
    LOGIN: "the library asks you to log in",
    CHECKPOINT: "Facebook shows a security check",
    CAPTCHA: "the library asks for a CAPTCHA",
}
_LOGIN_INPUTS = "input[name='email'], input[name='pass']"
_CAPTCHA_WIDGET = (
    "iframe[src*='captcha' i], iframe[title*='captcha' i], "
    "[id*='captcha' i] iframe, form[action*='captcha' i]"
)


def _visible(page: Any, selector: str) -> bool:
    try:
        return bool(page.locator(selector).first.is_visible())
    except Exception:  # noqa: BLE001
        return False


def visible_obstacle(page: Any) -> str | None:
    """What the page currently shows in the way, judged from the page alone."""
    url = page.url or ""
    if "/checkpoint" in url:
        return CHECKPOINT
    if "/login" in url or _visible(page, _LOGIN_INPUTS):
        return LOGIN
    if _visible(page, _CAPTCHA_WIDGET):
        return CAPTCHA
    return None


def _has_ads_without_captcha(bodies: list[str]) -> bool:
    return any("ad_archive_id" in b and not captcha_required(b) for b in bodies)


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

    def __init__(
        self,
        raw_dir: Path,
        headed: bool = True,
        human: bool = True,
        human_wait_s: float = DEFAULT_HUMAN_WAIT_S,
        notify: Callable[[str], None] | None = None,
    ):
        self.raw_dir = Path(raw_dir)
        self.headed = headed
        self.human = human and headed  # a person can only help with a window to work in
        self.human_wait_s = human_wait_s
        self.notify = notify or (lambda msg: log.warning("%s", msg))
        self.human_waits = 0  # obstacles a person cleared during this run
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
            try:
                self._browser = self._pw.chromium.launch(headless=not self.headed)
            except Exception as exc:
                if not self.headed:
                    raise
                self.notify(
                    f"No window can be shown here ({type(exc).__name__}); running without one. "
                    "A CAPTCHA or login request will stop the run."
                )
                self.headed = self.human = False
                self._browser = self._pw.chromium.launch(headless=True)
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

    def wait_for_human(
        self, page: Any, obstacle: str, url: str, bodies: list[str],
        sleep: Callable[[float], None] | None = None, clock: Callable[[], float] = time.monotonic,
    ) -> bool:  # fmt: skip
        """Let a person clear `obstacle` in the window. True when the page can be read again.

        Resolved when the page loads ads without a CAPTCHA flag, or, for a login or security
        check, when the form is gone (the search is then reopened). A CAPTCHA with no widget
        in sight is retried by reopening the search now and then. Never types or clicks.
        """
        if not self.human:
            return False
        pause = sleep or (lambda s: page.wait_for_timeout(int(s * 1000)))  # keeps events flowing
        self.notify(
            f"Action needed in the browser window: {OBSTACLE_TEXT[obstacle]}. Please handle it "
            f"there; the run waits up to {int(self.human_wait_s // 60)} min and then continues."
        )
        with contextlib.suppress(Exception):
            page.bring_to_front()
        start = last_reload = clock()
        saw_widget = False  # a CAPTCHA the page shows, not only one its data asks for
        while clock() - start < self.human_wait_s:
            pause(HUMAN_POLL_S)
            if _has_ads_without_captcha(bodies):
                break
            now_showing = visible_obstacle(page)
            saw_widget = saw_widget or now_showing == CAPTCHA
            gone = now_showing is None
            if gone and (obstacle in (LOGIN, CHECKPOINT) or (obstacle == CAPTCHA and saw_widget)):
                self._reopen(page, url, pause)
                break
            if gone and obstacle == CAPTCHA and clock() - last_reload >= CAPTCHA_RELOAD_S:
                self._reopen(page, url, pause)
                last_reload = clock()
        else:
            self.notify(f"No change after {int(self.human_wait_s // 60)} min; stopping the run.")
            return False
        self.human_waits += 1
        self.notify("Thanks; continuing.")
        return True

    def _reopen(self, page: Any, url: str, pause: Callable[[float], None]) -> None:
        self._pace()
        page.goto(url, wait_until="domcontentloaded")
        pause(PAUSE_AFTER_LOAD_S)

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
        url = library_url(query)
        helped = 0
        try:
            self._pace()
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(int(PAUSE_AFTER_LOAD_S * 1000))
            idle = 0
            while True:
                new_bodies, bodies[:] = list(bodies), []
                obstacle = None
                for text in new_bodies:
                    result.raw_files.append(self._save(query.query_id, text))
                    if captcha_required(text):
                        obstacle = CAPTCHA
                        continue
                    for ad, raw in ads_from_text(text):
                        found.setdefault(ad.ad_id, (ad, raw))
                if obstacle is None and not found:
                    obstacle = visible_obstacle(page)
                if obstacle is not None:
                    if helped < MAX_HUMAN_WAITS_PER_PAGE and self.wait_for_human(
                        page, obstacle, url, bodies
                    ):
                        helped += 1
                        idle = 0
                        continue
                    return self._blocked(page, result, OBSTACLE_TEXT[obstacle])
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
            url = f"{BASE}?{urlencode({'id': ad_id})}"
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(int(PAUSE_AFTER_LOAD_S * 1000))
            obstacle = visible_obstacle(page)
            if obstacle is not None and not self.wait_for_human(page, obstacle, url, []):
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
