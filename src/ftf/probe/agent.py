"""The probe's browser loop. It chooses only among fixed actions; code supplies every value.

Writes are closed by default: any non-GET request is aborted, except during the moment the
agent itself submits a registration or login form.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlsplit

from ..config import get_settings
from .harvest import harvest
from .persona import Ledger, Persona
from .policy import (
    CONTINUE,
    FORBIDDEN_FIELDS,
    LOOPBACK,
    Level,
    check_target,
    classify_field,
    host_is_public,
    is_bait,
    is_dangerous,
    may_follow_link,
    require_sandbox,
    site_key,
)

MAX_PAGES = 15
MAX_QUIZ_CLICKS = 8
SETTLE_MS = 2500

_FORMS_JS = """() => [...document.forms].map((form) => ({
  text: (form.innerText || '').slice(0, 400),
  fields: [...form.querySelectorAll('input,select,textarea')].map((el) => ({
    tag: el.tagName.toLowerCase(), type: (el.type || '').toLowerCase(),
    name: el.name, id: el.id, placeholder: el.placeholder || '',
    autocomplete: el.autocomplete || '', required: el.required,
    label: (el.labels && el.labels[0] ? el.labels[0].innerText : '').slice(0, 120),
    visible: !!(el.offsetWidth || el.offsetHeight),
  })),
}))"""
_LINKS_JS = """() => [...document.querySelectorAll('a[href]')]
  .map((a) => ({href: a.href, text: (a.innerText || '').slice(0, 80)}))"""
_BUTTONS_JS = """() => [...document.querySelectorAll('button,[role=button]')]
  .map((b, i) => ({i, text: (b.innerText || '').slice(0, 60), type: b.type || '',
                   inForm: !!b.closest('form'), visible: !!(b.offsetWidth || b.offsetHeight)}))"""
_SKIP_TYPES = {"hidden", "submit", "button", "image", "reset", "file"}


@dataclass
class ProbeResult:
    url: str
    level: int
    pages: list[str] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    outbound: list[str] = field(default_factory=list)
    submitted_to: list[str] = field(default_factory=list)
    blocked: list[dict] = field(default_factory=list)
    registered: bool = False
    logged_in: bool = False
    checkout_seen: bool = False
    persona: str | None = None
    mail: list[dict] = field(default_factory=list)

    def dump(self) -> dict:
        return {"schema": "ftf.probe/1", **self.__dict__}

    def write(self, bundle: Path) -> Path:
        path = bundle / "probe.json"
        path.write_text(json.dumps(self.dump(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path


class _Probe:
    def __init__(
        self, url, level, bundle, *, email, mail_domain, ledger, local, timeout, on_step=None
    ):
        self.mail_domain = mail_domain
        self.on_step = on_step
        self.start, self.level, self.bundle = url, Level(level), bundle
        self.email, self.ledger, self.local = email, ledger, local
        self.timeout_ms = int(timeout * 1000)
        self.site = site_key(url)
        self.result = ProbeResult(url=url, level=int(level))
        self.persona: Persona | None = None
        self.writes_open = False
        self.seen: set[str] = set()
        self.queue: list[str] = [url]
        self._public: dict[str, bool] = {}

    def step(self, action: str, **detail) -> None:
        self.result.steps.append({"action": action, **detail})
        if self.on_step:
            self.on_step(self.result)

    def host_allowed(self, host: str) -> bool:
        if self.local:
            return host in LOOPBACK
        if host not in self._public:
            self._public[host] = host_is_public(host)
        return self._public[host]

    def guard(self, route, request) -> None:
        parts = urlsplit(request.url)
        if parts.scheme in ("data", "blob"):
            route.continue_()
            return
        if parts.scheme not in ("http", "https") or not self.host_allowed(parts.hostname or ""):
            self.result.blocked.append({"url": request.url, "why": "address not allowed"})
            route.abort("blockedbyclient")
            return
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if not self.writes_open:
                self.result.blocked.append({"url": request.url, "why": "write outside submit"})
                route.abort("blockedbyclient")
                return
            target = f"{parts.netloc}{parts.path}"
            if target not in self.result.submitted_to:
                self.result.submitted_to.append(target)
        route.continue_()

    def run(self) -> ProbeResult:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True, args=["--disable-blink-features=AutomationControlled"]
            )
            try:
                context = browser.new_context(
                    accept_downloads=False,
                    service_workers="block",
                    locale="pl-PL",
                    timezone_id="Europe/Warsaw",
                    viewport={"width": 1366, "height": 900},
                )
                context.route("**/*", self.guard)
                page = context.new_page()
                page.set_default_timeout(self.timeout_ms)
                # A page's new tabs are closed: the agent stays in the tab it controls.
                context.on("page", lambda extra: extra.close() if extra != page else None)
                page.on("dialog", lambda dialog: dialog.dismiss())
                while self.queue and len(self.result.pages) < MAX_PAGES:
                    url = self.queue.pop(0)
                    if url in self.seen:
                        continue
                    self.seen.add(url)
                    self.visit(page, url)
                context.close()
            finally:
                browser.close()
        self.result.outbound.sort()
        return self.result

    def visit(self, page, url: str) -> None:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            page.wait_for_timeout(600)
        except Exception as exc:  # noqa: BLE001 - one dead page never ends the probe
            self.step("visit_failed", url=url, error=str(exc).splitlines()[0])
            return
        if site_key(page.url) != self.site:
            self.step("left_site", url=url, landed=page.url)
            self.note_outbound(page.url)
            return
        self.read(page)
        self.walk_quiz(page)
        if self.level >= Level.REGISTER:
            self.try_account(page)

    def read(self, page) -> None:
        current = urldefrag(page.url)[0]
        if current not in self.result.pages:
            self.result.pages.append(current)
            self.step("read", url=current)
        self.seen.add(current)
        html = page.content()
        frames = [f.url for f in page.frames if f != page.main_frame]
        for item in harvest(html, current, frames):
            if self.level < Level.PAYMENT and item["ns"].startswith("payment."):
                continue
            own = self.persona.email if self.persona else self.email
            if is_bait(item["value"], own, self.mail_domain):
                continue  # our own bait must never become evidence
            key = (item["ns"], item["value"])
            if key not in {(f["ns"], f["value"]) for f in self.result.findings}:
                self.result.findings.append(item)
        forms = page.evaluate(_FORMS_JS)
        kinds = {classify_field(*_attrs(f)) for form in forms for f in form["fields"]}
        if "card" in kinds and self.level >= Level.PAYMENT:
            self.result.checkout_seen = True
            self.step("checkout_seen", url=current, filled=False)
        for link in page.evaluate(_LINKS_JS):
            self.consider_link(link["href"], link["text"], current)

    def consider_link(self, href: str, text: str, base: str) -> None:
        target = urldefrag(urljoin(base, href))[0]
        if urlsplit(target).scheme not in ("http", "https"):
            return
        if site_key(target) != self.site:
            self.note_outbound(target)
            return
        if not may_follow_link(self.level, href, text):
            return
        if target not in self.seen and target not in self.queue:
            self.queue.append(target)

    def note_outbound(self, url: str) -> None:
        key = site_key(url)
        if key and key != self.site and key not in self.result.outbound:
            self.result.outbound.append(key)

    def walk_quiz(self, page) -> None:
        for _ in range(MAX_QUIZ_CLICKS):
            buttons = [
                b
                for b in page.evaluate(_BUTTONS_JS)
                if b["visible"]
                and not b["inForm"]
                and b["type"] != "submit"
                and CONTINUE.search(b["text"])
                and not is_dangerous(b["text"])
            ]
            if not buttons:
                return
            label = buttons[0]["text"].strip()
            try:
                page.locator("button,[role=button]").nth(buttons[0]["i"]).click(timeout=2000)
                page.wait_for_timeout(500)
            except Exception:  # noqa: BLE001 - an unclickable step ends the walk
                return
            self.step("click", label=label[:60])
            if site_key(page.url) != self.site:
                self.note_outbound(page.url)
                return
            self.read(page)

    # --- level 3 ------------------------------------------------------------------------

    def try_account(self, page) -> None:
        for index, form in enumerate(page.evaluate(_FORMS_JS)):
            kinds = [classify_field(*_attrs(f)) for f in form["fields"] if _fillable(f)]
            if "password" not in kinds or not {"email", "username"} & set(kinds):
                continue
            is_register = kinds.count("password") > 1 or bool(_REGISTER_WORDS.search(form["text"]))
            if is_register and not self.result.registered:
                self.submit(page, index, form, "register")
                return
            if not is_register and self.result.registered and not self.result.logged_in:
                self.submit(page, index, form, "login")
                return

    def submit(self, page, index: int, form: dict, action: str) -> None:
        needed = [
            classify_field(*_attrs(f)) for f in form["fields"] if _fillable(f) and f["required"]
        ]
        stop = sorted(set(needed) & FORBIDDEN_FIELDS)
        if "other" in needed:
            stop.append("unrecognised required field")
        if stop:
            self.step(f"{action}_stopped", url=page.url, needs=stop)
            return
        if self.persona is None:
            self.persona = self.ledger.new_persona(
                self.email, self.site, self.bundle, int(self.level), self.mail_domain
            )
            self.result.persona = self.persona.full_name
        locator = page.locator("form").nth(index)
        controls = locator.locator("input,select,textarea")
        for position, spec in enumerate(form["fields"]):
            if not spec["visible"] or spec["type"] in _SKIP_TYPES:
                continue
            control = controls.nth(position)
            if spec["type"] == "checkbox":
                if spec["required"]:
                    control.check(timeout=2000)
                continue
            value = self.value_for(classify_field(*_attrs(spec)))
            if value is not None:
                control.fill(value, timeout=2000)
        button = locator.locator("button[type=submit],input[type=submit],button:not([type])")
        before = page.url
        self.writes_open = True
        try:
            button.first.click(timeout=3000)
            page.wait_for_timeout(SETTLE_MS)
        except Exception as exc:  # noqa: BLE001
            self.step(f"{action}_failed", error=str(exc).splitlines()[0])
            return
        finally:
            self.writes_open = False
        if action == "register":
            self.result.registered = True
        else:
            self.result.logged_in = page.url != before
        self.step(action, url=before, landed=page.url)
        if site_key(page.url) != self.site:
            self.note_outbound(page.url)
            return
        self.read(page)
        if action == "register":
            self.try_account(page)

    def value_for(self, kind: str) -> str | None:
        persona = self.persona
        return {
            "email": persona.email,
            "password": persona.password,
            "first_name": persona.first_name,
            "last_name": persona.last_name,
            "full_name": persona.full_name,
            "username": persona.username,
        }.get(kind)


def _attrs(field_spec: dict) -> tuple:
    return (
        field_spec["name"],
        field_spec["id"],
        field_spec["placeholder"],
        field_spec["autocomplete"],
        field_spec["label"],
        "password" if field_spec["type"] == "password" else "",
        "email" if field_spec["type"] == "email" else "",
        "phone" if field_spec["type"] == "tel" else "",
    )


def _fillable(field_spec: dict) -> bool:
    return field_spec["visible"] and field_spec["type"] not in _SKIP_TYPES | {"checkbox", "radio"}


_REGISTER_WORDS = re.compile(
    r"rejestr|za[łl]ó[żz]|utw[oó]rz konto|sign.?up|register|create", re.IGNORECASE
)


def run_probe(
    url: str,
    bundle: Path,
    *,
    level: int = 1,
    email: str = "",
    mail_domain: str = "",
    ledger_path: Path | None = None,
    live: bool = False,
    local: bool = False,
    timeout: float = 20.0,
    on_step=None,
) -> ProbeResult:
    """Probe `url` and write `<bundle>/probe.json`. Raises ProbeRefused outside policy."""
    from .policy import ProbeRefused

    wanted = Level(level)
    check_target(url, wanted, live=live, local=local)
    if not local:
        require_sandbox(get_settings().data_dir)
    if wanted >= Level.REGISTER and (not (email or mail_domain) or ledger_path is None):
        raise ProbeRefused("levels 3-4 need FTF_PROBE_MAIL_DOMAIN or FTF_PROBE_EMAIL")
    bundle.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(ledger_path or bundle / "ledger.json")
    result = _Probe(
        url,
        wanted,
        bundle,
        email=email,
        mail_domain=mail_domain,
        ledger=ledger,
        local=local,
        timeout=timeout,
        on_step=on_step,
    ).run()
    result.write(bundle)
    return result
