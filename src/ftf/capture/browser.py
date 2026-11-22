"""Capture one URL with a headless browser into a bundle, live or replayed from an archived HAR.

Safeguards: a fresh browser context per capture (no cookies or storage carried over),
service workers blocked, downloads refused, and every non-GET navigation aborted, so no form
is ever submitted. Nothing is typed or clicked; the page is only scrolled.
"""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import shutil
import tempfile
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from ..ingest.har import document_redirects, load_har, main_document
from .bundle import (
    FailedRequest,
    Manifest,
    Resource,
    Server,
    Status,
    Tls,
    build_trace,
    classify_error,
    now_iso,
)
from .gate import EgressGate
from .profiles import DESKTOP, Profile

MAX_RESOURCE = 20 << 20  # bytes per saved resource
MAX_RESOURCES = 400
SCROLL_STEPS = 25

_SCROLL_JS = """async (steps) => {
  const pause = (ms) => new Promise((r) => setTimeout(r, ms));
  for (let i = 0; i < steps; i++) {
    const before = window.scrollY;
    window.scrollBy(0, window.innerHeight * 0.9);
    await pause(250);
    if (window.scrollY === before) break;
  }
  window.scrollTo(0, 0);
  await pause(200);
}"""

# Runs before any page script: a form can neither be submitted by script nor by an event.
# Attempts are noted so the manifest can say the page tried. The network guard below
# still aborts any non-GET navigation that gets past this.
_NO_SUBMIT_JS = """(() => {
  const tried = (window.__ftfBlockedSubmits = []);
  const note = (form) => {
    try { tried.push(String(form.action || location.href)); } catch (e) {}
  };
  HTMLFormElement.prototype.submit = function () { note(this); };
  HTMLFormElement.prototype.requestSubmit = function () { note(this); };
  window.addEventListener("submit", (e) => {
    note(e.target); e.preventDefault(); e.stopImmediatePropagation();
  }, true);
})();"""


@dataclass
class CaptureResult:
    bundle: Path
    manifest: Manifest
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """A page was captured (possibly with an error status or a failed onward hop)."""
        return self.manifest.status in (Status.OK, Status.HTTP_ERROR, Status.NAVIGATION_FAILED)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iso_from_epoch(value: Any) -> str | None:
    if not isinstance(value, int | float) or value <= 0:
        return None
    return datetime.fromtimestamp(value, UTC).isoformat(timespec="seconds")


def _extension(url: str, mime: str) -> str:
    suffix = PurePosixPath(urlsplit(url).path).suffix.lower()
    if 1 < len(suffix) <= 6 and suffix[1:].isalnum():
        return suffix
    return mimetypes.guess_extension((mime or "").split(";")[0].strip()) or ""


class NoDocumentError(ValueError):
    """An archived HAR holds no HTML document to start a replay from."""


def archived_capture(har: Path) -> tuple[str, str | None]:
    """(start URL, capture time) of an archived HAR.

    The start is where the archive's own HTTP redirects into its main document begin, so a
    replay walks the same hops the original capture did.
    """
    entries = load_har(har)
    doc = main_document(entries)
    if doc is None:
        raise NoDocumentError(
            f"{har.name}: no HTML document in the archive; pass the URL to replay"
        )
    hops = document_redirects(entries)
    first = hops[0][0] if hops else doc[0]
    start = hops[0][1] if hops else doc[1]
    return start, entries[first].get("startedDateTime") or entries[doc[0]].get("startedDateTime")


def capture_url(
    url: str | None,
    out_dir: Path,
    *,
    replay_har: Path | None = None,
    proxy: str | None = None,
    timeout: float = 30.0,
    settle: float = 5.0,
    profile: Profile = DESKTOP,
    differential: str | None = None,
    differential_run: str | None = None,
    public_only: bool = False,
) -> CaptureResult:
    """Capture `url` (or, in replay mode, the archive's own page) into `out_dir`.

    `profile` decides device, user agent, referrer and which form of the URL is opened.
    `differential` names the URL a cloaking differential is testing and `differential_run`
    the run; the variants of one run share both, which is how they are paired after ingestion.
    `public_only` sends the browser through an `EgressGate`, so no request (redirects,
    resources and WebSockets included) reaches a loopback, private, link-local or reserved
    address, as the web interface needs. A replay never leaves the archive anyway.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    archived_at = None
    if replay_har is not None:
        try:
            archived_url, archived_at = archived_capture(replay_har)
        except NoDocumentError:
            if not url:
                raise
            archived_at = None  # the given URL picks the page; the archive has no document
        else:
            url = url or archived_url
    if not url:
        raise ValueError("a URL is required for a live capture")
    if not urlsplit(url).scheme:
        url = "https://" + url
    url, click_id_added = profile.url_for(url)

    started = now_iso()
    manifest = Manifest(
        mode="replay" if replay_har else "live",
        page_url=url,
        captured_at=archived_at or started,
        replayed_at=started if replay_har else None,
        replay_source=(
            {"file": replay_har.name, "sha256": _sha256_file(replay_har)} if replay_har else None
        ),
        egress=proxy or "direct",
        profile=profile.name,
        referer=profile.referer,
        click_id_added=click_id_added or None,
        differential=differential,
        differential_run=differential_run,
        viewport=profile.viewport,
    )
    har_path = out_dir / "page.har"
    # The browser's own recording. In replay mode it only feeds resources and the DOM
    # fallback: the archive itself is kept as page.har, since a recording of the replay
    # would carry today's timestamps.
    work = Path(tempfile.mkdtemp(prefix="ftf-capture-"))
    try:
        record_path = work / "replay.har" if replay_har else har_path
        gate = EgressGate(timeout) if public_only and replay_har is None else None
        if gate is not None and proxy:
            raise ValueError("a public-only capture cannot go through another proxy")
        with gate or nullcontext():
            egress = gate.url if gate else proxy
            run = _Run(manifest, out_dir, record_path, replay_har, egress, timeout, settle, profile)
            run.gate = gate
            run.browse(url)
        if gate is not None:
            _gate_findings(gate, manifest, run)
        recorded = load_har(record_path)
        if run.dom_from_har:
            _dom_from_har(recorded, manifest, out_dir, run.notes)
        _save_resources(recorded, out_dir, manifest, run.notes)
        if replay_har is not None:
            shutil.copyfile(replay_har, har_path)
        if har_path.is_file():
            manifest.files["har"] = har_path.name
        manifest.redirects = build_trace(
            run.chains, load_har(har_path), har_path.name, final_url=manifest.final_url
        )
        if replay_har is not None:  # in replay a failed request is one the archive lacks
            for f in run.failed:
                f.error = f"not in archive ({f.error})" if f.error else "not in archive"
        manifest.failed_requests = run.failed[:200]
        manifest.blocked = run.blocked
    finally:
        shutil.rmtree(work, ignore_errors=True)
        manifest.write(out_dir)
    return CaptureResult(bundle=out_dir, manifest=manifest, notes=run.notes)


_FORM_BODY = ("application/x-www-form-urlencoded", "multipart/form-data")


class _Run:
    """One browser session: navigate, settle, scroll, snapshot. Collects what it saw."""

    def __init__(self, manifest, out_dir, record_path, replay_har, proxy, timeout, settle, profile):
        self.manifest, self.out_dir, self.record_path = manifest, out_dir, record_path
        self.profile: Profile = profile
        self.replay_har, self.proxy = replay_har, proxy
        self.timeout, self.settle = timeout, settle
        self.notes: list[str] = []
        self.chains: list[list[str]] = []  # main-frame navigations, each with its HTTP hops
        self.navigations: list[Any] = []  # the requests behind `chains`, in order
        self.responses: list[Any] = []  # main-frame document responses, in order
        self.failed: list[FailedRequest] = []
        self.blocked: list[FailedRequest] = []
        self.dom_from_har = False
        self.gate: EgressGate | None = None

    def guard(self, route, request) -> None:
        """No form is ever submitted: a non-GET navigation, or a request carrying a form
        body, is aborted. Registered after route_from_har, so it is consulted first."""
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            ctype = (request.headers.get("content-type") or "").lower()
            if request.is_navigation_request() or ctype.startswith(_FORM_BODY):
                self.blocked.append(
                    FailedRequest(url=request.url, method=request.method, error="form post")
                )
                route.abort("blockedbyclient")
                return
        route.fallback()

    def on_request(self, request) -> None:
        if not request.is_navigation_request() or request.frame != self.page.main_frame:
            return
        if request.method != "GET":
            return  # aborted by the guard; not a hop the page made
        self.navigations.append(request)
        if request.redirected_from is not None and self.chains:
            self.chains[-1].append(request.url)
        else:
            self.chains.append([request.url])

    def on_response(self, response) -> None:
        request = response.request
        if request.is_navigation_request() and request.frame == self.page.main_frame:
            self.responses.append(response)

    def browse(self, url: str) -> None:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        from playwright.sync_api import sync_playwright

        man = self.manifest
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                proxy=self.proxy_settings(),
                # Otherwise navigator.webdriver is true, the first thing a cloaker checks.
                args=[
                    "--disable-blink-features=AutomationControlled",
                    # WebRTC would otherwise send UDP around the gate.
                    *(
                        ["--force-webrtc-ip-handling-policy=disable_non_proxied_udp"]
                        if self.gate
                        else []
                    ),
                ],
            )
            try:
                man.user_agent = self.profile.user_agent_for(browser.version)
                context = browser.new_context(
                    accept_downloads=False,
                    service_workers="block",
                    user_agent=man.user_agent,
                    viewport=self.profile.viewport,
                    is_mobile=self.profile.is_mobile,
                    has_touch=self.profile.is_mobile,
                    locale="pl-PL",
                    timezone_id="Europe/Warsaw",
                    record_har_path=str(self.record_path),
                    record_har_content="embed",
                )
                try:
                    if self.replay_har is not None:
                        context.route_from_har(str(self.replay_har), not_found="abort")
                        # route_from_har leaves WebSockets alone: a replay stays offline.
                        context.route_web_socket("**/*", lambda ws: ws.close())
                    context.route("**/*", self.guard)
                    context.add_init_script(_NO_SUBMIT_JS)
                    self.page = page = context.new_page()
                    self.mask_headless(context, page, browser.version)
                    page.set_default_timeout(self.timeout * 1000)
                    page.on("request", self.on_request)
                    page.on("response", self.on_response)
                    page.on(
                        "requestfailed",
                        lambda r: self.failed.append(
                            FailedRequest(url=r.url, method=r.method, error=r.failure or "")
                        ),
                    )
                    page.on(
                        "download",
                        lambda d: self.blocked.append(
                            FailedRequest(url=d.url, error="download refused")
                        ),
                    )
                    loaded = False
                    try:
                        page.goto(
                            url,
                            wait_until="domcontentloaded",
                            timeout=self.timeout * 1000,
                            referer=self.profile.referer,
                        )
                        loaded = True
                    except PlaywrightTimeout as exc:
                        man.status, man.error = Status.TIMEOUT, str(exc).splitlines()[0]
                        loaded = page.url not in ("", "about:blank")
                    except PlaywrightError as exc:
                        message = str(exc).splitlines()[0]
                        status = classify_error(message)
                        if self.replay_har is not None and status is Status.FAILED:
                            status = Status.NOT_IN_ARCHIVE
                        man.status, man.error = status, message
                    if loaded:
                        self.snapshot(page, PlaywrightError)
                finally:
                    context.close()  # writes the HAR, also after an error
            finally:
                browser.close()

    def proxy_settings(self) -> dict[str, str] | None:
        if not self.proxy:
            return None
        if self.gate is not None:
            # Chromium sends loopback around a proxy unless told not to.
            return {"server": self.proxy, "bypass": "<-loopback>"}
        return {"server": self.proxy}

    def mask_headless(self, context, page, browser_version: str) -> None:
        """Client hints and navigator.platform matching the profile (see Profile.ua_override)."""
        try:
            cdp = context.new_cdp_session(page)
            cdp.send("Emulation.setUserAgentOverride", self.profile.ua_override(browser_version))
        except Exception as exc:  # noqa: BLE001 — the capture still runs, the note says how
            self.notes.append(f"client hints not overridden: {exc}")

    def snapshot(self, page, PlaywrightError) -> None:
        man = self.manifest
        _settle(page, self.settle, self.notes)
        try:
            page.evaluate(_SCROLL_JS, SCROLL_STEPS)
        except PlaywrightError as exc:
            self.notes.append(f"scroll: {exc}")
        _settle(page, self.settle, self.notes)
        try:
            for action in page.evaluate("() => window.__ftfBlockedSubmits || []"):
                self.blocked.append(
                    FailedRequest(url=str(action), method="SUBMIT", error="form submit")
                )
        except PlaywrightError as exc:
            self.notes.append(f"blocked submits: {exc}")

        last = self.navigations[-1] if self.navigations else None
        failure = last.failure if last is not None else None
        on_error_page = page.url.startswith("chrome-error:")
        if failure and len(self.chains) > 1:
            # Loaded, then navigated on to somewhere that failed: keep the page that loaded.
            man.status = Status.NAVIGATION_FAILED
            man.error = f"navigation to {last.url} failed: {failure}"
            man.final_url = self.chains[-2][-1]
        elif on_error_page:
            # A navigation the guard aborted leaves Chromium's error page behind.
            man.final_url = self.chains[-1][-1] if self.chains else None
        else:
            man.final_url = page.url
        if failure or on_error_page:
            self.dom_from_har = True  # the live DOM and screenshot show the error page
            self.notes.append("page left on an error page; DOM taken from the HAR")
        else:
            _write_page(page, self.out_dir, man, self.notes)
        self.server_facts()

    def server_facts(self) -> None:
        """IP, TLS and headers of the response that produced the final document."""
        man = self.manifest
        final = next(
            (
                r
                for r in reversed(self.responses)
                if man.final_url and _same_doc(r.url, man.final_url) and not 300 <= r.status < 400
            ),
            None,
        )
        if final is None:
            return
        server = Server(url=final.url, status=final.status)
        try:
            addr = final.server_addr()
            if self.gate is not None:  # the browser only saw the gate; the gate saw the server
                parts = urlsplit(final.url)
                port = parts.port or (443 if parts.scheme == "https" else 80)
                pinned = self.gate.pins.get(((parts.hostname or "").lower(), port))
                server.ip, server.port = (pinned, port) if pinned else (None, None)
            elif addr:
                server.ip, server.port = addr.get("ipAddress"), addr.get("port")
        except Exception as exc:  # noqa: BLE001
            self.notes.append(f"server address: {exc}")
        try:
            sec = final.security_details()
            if sec:
                server.tls = Tls(
                    protocol=sec.get("protocol"),
                    issuer=sec.get("issuer"),
                    subject=sec.get("subjectName"),
                    valid_from=_iso_from_epoch(sec.get("validFrom")),
                    valid_to=_iso_from_epoch(sec.get("validTo")),
                )
        except Exception as exc:  # noqa: BLE001
            self.notes.append(f"tls: {exc}")
        try:
            server.headers = dict(final.all_headers())
        except Exception as exc:  # noqa: BLE001
            self.notes.append(f"headers: {exc}")
        man.server = server
        if final.status >= 400 and man.status is Status.OK:
            man.status, man.error = Status.HTTP_ERROR, f"HTTP {final.status}"


def _gate_findings(gate: EgressGate, manifest: Manifest, run: _Run) -> None:
    """What the gate refused, in the manifest; a failed page says why it failed."""
    for host, address in sorted(gate.blocked.items()):
        run.blocked.append(FailedRequest(url=host, error=f"private address {address}"))
    manifest.blocked = run.blocked
    if manifest.status is Status.OK:
        return
    tried = [manifest.page_url, *(run.chains[-1] if run.chains else [])]
    hosts = [(urlsplit(u).hostname or "").lower() for u in tried]
    last = hosts[-1]
    if last in gate.blocked:
        manifest.status = Status.FAILED
        manifest.error = f"refused: {last} is a private address ({gate.blocked[last]})"
    elif last in gate.unresolved:
        manifest.status = Status.DNS_FAILED


def _same_doc(a: str, b: str) -> bool:
    return a.split("#")[0] == b.split("#")[0]


def _settle(page, seconds: float, notes: list[str]) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=seconds * 1000)
    except Exception as exc:  # noqa: BLE001 — a page that never idles is still captured
        notes.append(f"network idle: {type(exc).__name__}")


def _write_page(page, out_dir: Path, manifest: Manifest, notes: list[str]) -> None:
    try:
        (out_dir / "dom.html").write_text(page.content(), encoding="utf-8")
        manifest.files["dom"] = "dom.html"
    except Exception as exc:  # noqa: BLE001
        notes.append(f"dom: {exc}")
    shot = out_dir / "screenshot.png"
    for full_page in (True, False):  # a very tall page can fail full-page; keep the viewport
        try:
            page.screenshot(path=str(shot), full_page=full_page)
            manifest.files["screenshot"] = shot.name
            break
        except Exception as exc:  # noqa: BLE001
            notes.append(f"screenshot (full_page={full_page}): {exc}")


def _entry_bytes(entry: dict[str, Any]) -> bytes | None:
    content = (entry.get("response") or {}).get("content") or {}
    text = content.get("text")
    if not isinstance(text, str) or not text:
        return None
    if content.get("encoding") == "base64":
        try:
            return base64.b64decode(text)
        except ValueError:
            return None
    return text.encode("utf-8")


def _dom_from_har(entries: list, manifest: Manifest, out_dir: Path, notes: list[str]) -> None:
    """The final document as served, when the live page ended on an error page."""
    if not manifest.final_url:
        return
    for entry in reversed(entries):
        req, resp = entry.get("request") or {}, entry.get("response") or {}
        if not _same_doc(req.get("url", ""), manifest.final_url):
            continue
        if not 200 <= (resp.get("status") or 0) < 300:
            continue
        body = _entry_bytes(entry)
        if body:
            (out_dir / "dom.html").write_bytes(body)
            manifest.files["dom"] = "dom.html"
            notes.append("dom.html is the served document, not the rendered DOM")
            return


def _save_resources(entries: list, out_dir: Path, manifest: Manifest, notes: list[str]) -> None:
    """Every response body the browser recorded, stored once by content hash."""
    rdir = out_dir / "resources"
    for entry in entries:
        if len(manifest.resources) >= MAX_RESOURCES:
            notes.append(f"resources: stopped at {MAX_RESOURCES}")
            break
        url = (entry.get("request") or {}).get("url", "")
        resp = entry.get("response") or {}
        if url.startswith("data:") or not 200 <= (resp.get("status") or 0) < 300:
            continue
        body = _entry_bytes(entry)
        if not body or len(body) > MAX_RESOURCE:
            continue
        sha = hashlib.sha256(body).hexdigest()
        mime = str((resp.get("content") or {}).get("mimeType") or "")
        name = f"{sha}{_extension(url, mime)}"
        target = rdir / name
        if not target.exists():
            rdir.mkdir(exist_ok=True)
            target.write_bytes(body)
        manifest.resources.append(
            Resource(
                url=url,
                sha256=sha,
                mime=mime.split(";")[0].strip(),
                size=len(body),
                path=f"resources/{name}",
            )
        )
