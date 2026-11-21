"""The victim's browser (VV-10, VV-21, VV-23, VV-26).

- Always a visible window. There is no headless mode: if no window can be shown, the run
  fails instead of carrying on unseen.
- One fresh context per run: no stored cookies, profile or service workers.
- Persona device, locale, time zone and referrer.
- Every permission denied. A script wrapped around the permission APIs records each request
  (notifications, camera, microphone, location, clipboard, file pickers, install prompts) and
  answers it with a refusal; the agent stops the run on it.
- Downloads refused (`accept_downloads=False`) and recorded.
- A run that starts on a local address is confined to loopback: any other request is answered
  from the replay's recorded responses or aborted, so a replay never reaches the live site.
- The control panel lives in a second context, so it is not in the run's network record.
"""

from __future__ import annotations

import contextlib
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .persona import Persona

REFERRERS = {
    "facebook": "https://l.facebook.com/",
    "instagram": "https://l.instagram.com/",
    "google": "https://www.google.com/",
    "tiktok": "https://www.tiktok.com/",
}
PHONE_DEVICE = "Pixel 7"
NAV_TIMEOUT_MS = 30_000

# Installed before any page script. `__BINDING__` is replaced with a per-run name.
GUARD_JS = r"""
(() => {
  const report = (kind, detail) => { try { window['__BINDING__']({ kind, detail: String(detail || ''), url: location.href }); } catch (e) {} };
  const deny = (name) => () => { report('permission', name); return Promise.reject(new DOMException('Permission denied', 'NotAllowedError')); };
  try {
    if (window.Notification) {
      Notification.requestPermission = function (cb) { report('permission', 'notifications'); if (cb) cb('denied'); return Promise.resolve('denied'); };
    }
    if (navigator.geolocation) {
      const fail = (name) => function (ok, err) { report('permission', name); if (err) err({ code: 1, message: 'User denied Geolocation' }); return 0; };
      navigator.geolocation.getCurrentPosition = fail('geolocation');
      navigator.geolocation.watchPosition = fail('geolocation');
    }
    if (navigator.mediaDevices) {
      navigator.mediaDevices.getUserMedia = deny('camera/microphone');
      navigator.mediaDevices.getDisplayMedia = deny('screen capture');
    }
    if (navigator.clipboard) {
      navigator.clipboard.readText = deny('clipboard');
      navigator.clipboard.read = deny('clipboard');
    }
    for (const name of ['showOpenFilePicker', 'showSaveFilePicker', 'showDirectoryPicker']) {
      if (window[name]) window[name] = deny('file system: ' + name);
    }
    if (window.PushManager && PushManager.prototype.subscribe) PushManager.prototype.subscribe = deny('push');
    if (navigator.serviceWorker) navigator.serviceWorker.register = deny('service worker');
    window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); report('install', 'web app install prompt'); }, true);
  } catch (e) {}
  // What a person does in the window. The agent keeps these only while the run is paused.
  const describe = (el) => { if (!el || !el.tagName) return ''; const t = (el.innerText || el.value || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim().slice(0, 80);
    return el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (t ? ' "' + t + '"' : ''); };
  for (const type of ['click', 'change', 'submit', 'keydown']) {
    window.addEventListener(type, (e) => { if (!e.isTrusted) return; if (type === 'keydown' && e.key.length === 1) return;
      report('user', type + ' ' + describe(e.target) + (type === 'keydown' ? ' ' + e.key : '')); }, true);
  }
})();
"""


@dataclass
class BrowserEvents:
    """What the browser reported during the run, drained by the agent after each step."""

    permissions: list[dict[str, str]] = field(default_factory=list)
    downloads: list[dict[str, str]] = field(default_factory=list)
    user: list[dict[str, str]] = field(default_factory=list)
    dialogs: list[dict[str, str]] = field(default_factory=list)
    blocked_requests: list[str] = field(default_factory=list)
    popups: list[Any] = field(default_factory=list)

    def drain_forbidden(self) -> list[dict[str, str]]:
        out = [*self.permissions, *self.downloads]
        self.permissions.clear()
        self.downloads.clear()
        return out


def _is_loopback(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".localhost")


@dataclass
class VictimBrowser:
    playwright: Any
    browser: Any
    context: Any
    page: Any
    panel_context: Any
    events: BrowserEvents
    har_path: Path

    def close(self) -> None:
        """Closing the context is what writes the HAR, so it always runs, in this order."""
        for closer in (self.context, self.panel_context, self.browser):
            if closer is not None:
                with contextlib.suppress(Exception):
                    closer.close()
        with contextlib.suppress(Exception):
            self.playwright.stop()


def context_options(persona: Persona, devices: dict[str, Any]) -> dict[str, Any]:
    """Device, locale and time zone of the persona (pure, for tests)."""
    d = persona.digital
    options: dict[str, Any] = {}
    if d.device == "phone":
        options.update(
            {k: v for k, v in devices.get(PHONE_DEVICE, {}).items() if k != "default_browser_type"}
        )
    options["viewport"] = {"width": d.viewport.width, "height": d.viewport.height}
    options.update(
        locale=persona.locale.locale,
        timezone_id=persona.locale.timezone,
        permissions=[],
        accept_downloads=False,
        service_workers="block",
        extra_http_headers={"Accept-Language": f"{persona.locale.locale},pl;q=0.9,en;q=0.5"},
    )
    return options


def launch(
    persona: Persona,
    har_path: Path,
    start_url: str,
    fallback: Callable[[str], Any] | None = None,
    panel_url: str | None = None,
    launcher: Callable[[Any], Any] | None = None,
) -> VictimBrowser:
    """Open the visible window. `launcher` exists for tests only; it never runs in a real run."""
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    try:
        browser = (launcher or (lambda p: p.chromium.launch(headless=False)))(pw)
    except Exception as exc:
        pw.stop()
        raise RuntimeError(
            "the victim visitor needs a visible browser window and none could be opened here "
            f"({type(exc).__name__}: {exc}). Run it on the host machine, not in the container."
        ) from exc
    events = BrowserEvents()
    har_path.parent.mkdir(parents=True, exist_ok=True)
    context = browser.new_context(
        **context_options(persona, pw.devices),
        record_har_path=str(har_path),
        record_har_content="embed",
    )
    context.set_default_navigation_timeout(NAV_TIMEOUT_MS)
    binding = "__ftf_" + secrets.token_hex(4)

    def on_report(_source: Any, data: dict[str, str]) -> None:
        kind = data.get("kind")
        if kind == "user":
            events.user.append(data)
        elif kind in {"permission", "install"}:
            events.permissions.append(data)

    context.expose_binding(binding, on_report)
    context.add_init_script(GUARD_JS.replace("__BINDING__", binding))

    if _is_loopback(start_url):

        def confine(route: Any) -> None:
            url = route.request.url
            if _is_loopback(url) or url.startswith(("data:", "blob:")):
                route.continue_()
                return
            recorded = fallback(url) if fallback else None
            if recorded is not None:
                route.fulfill(status=recorded.status, headers=recorded.headers, body=recorded.body)
                return
            events.blocked_requests.append(url[:300])
            route.abort()

        context.route("**/*", confine)

    def on_download(download: Any) -> None:
        events.downloads.append(
            {"kind": "download", "detail": download.suggested_filename, "url": download.url}
        )
        with contextlib.suppress(Exception):
            download.cancel()

    def on_dialog(dialog: Any) -> None:
        events.dialogs.append({"type": dialog.type, "message": dialog.message[:500]})
        with contextlib.suppress(Exception):
            dialog.dismiss()

    def on_page(new_page: Any) -> None:
        new_page.on("download", on_download)
        new_page.on("dialog", on_dialog)
        events.popups.append(new_page)

    context.on("page", on_page)
    page = context.new_page()
    panel_context = None
    if panel_url:
        panel_context = browser.new_context(viewport={"width": 460, "height": 640})
        panel = panel_context.new_page()
        with contextlib.suppress(Exception):
            panel.goto(panel_url)
        page.bring_to_front()
    events.popups.clear()  # the run's own first page is not a pop-up
    return VictimBrowser(pw, browser, context, page, panel_context, events, har_path)


def referrer_for(persona: Persona) -> str:
    return REFERRERS.get(persona.digital.platform, "")


def load_har(path: Path) -> list[dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))["log"]["entries"]
    except (OSError, ValueError, KeyError):
        return []
