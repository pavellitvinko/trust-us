"""The victim's walk through a funnel (VV-10…VV-15, VV-24…VV-26, VV-40, VV-41).

One run: open the ad link in a visible window as the persona, then repeat
observe → choose → act → record until a stop condition. Every step is logged with its
screenshot; every move to a new page becomes a stage with the navigation that proves it.
Whatever happens (a stop, Ctrl-C, an error), the browser is closed through one `finally`,
because that is when the network record is written.
"""

from __future__ import annotations

import contextlib
import json
import secrets
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .. import __version__
from ..config import Settings, get_settings
from . import rules
from .browser import VictimBrowser, launch, referrer_for
from .contacts import (
    ContactBook,
    allowed_values,
    contacts_for,
    load_contacts,
    plan_form,
    value_allowed,
)
from .control import PanelServer, RunControl
from .decide import Decider, Decision, History, RuleDecider
from .observe import Candidate, PageState, observe
from .overlay import Overlay, action_caption
from .persona import Persona
from .record import Recorder, Stage, now_iso, stage_key

QUICK_STATE_JS = """() => [location.href, (document.body ? document.body.innerText.length : 0),
  document.querySelectorAll('button, a[href], input, .msg, .message').length].join('|')"""
FORM_ERRORS_JS = """(xpath) => {
  const form = document.evaluate(xpath, document, null, 9, null).singleNodeValue;
  if (!form) return { gone: true, invalid: [], errors: [] };
  const vis = (el) => el.getClientRects().length > 0;
  const invalid = Array.from(form.querySelectorAll('input, select, textarea'))
    .filter(el => vis(el) && el.willValidate && !el.checkValidity())
    .map(el => ({ name: el.name || el.id, message: el.validationMessage }));
  const errors = Array.from(form.querySelectorAll("[class*='error' i], [class*='invalid' i], [role='alert']"))
    .filter(vis).map(el => el.innerText.trim()).filter(Boolean).slice(0, 10);
  return { gone: !vis(form), invalid, errors };
}"""
READ_VALUES_JS = """(xpaths) => xpaths.map(x => { const el = document.evaluate(x, document, null, 9, null).singleNodeValue;
  return el ? (el.type === 'checkbox' || el.type === 'radio' ? String(el.checked) : el.value) : null; })"""


@dataclass
class RunOptions:
    max_steps: int = 40
    max_seconds: float = 600.0
    max_pages: int = 15
    no_progress_steps: int = 8
    human_wait_s: float = 600.0  # VV-24
    pace: float = 1.0  # 1 = the persona's own reading and typing speed; 0 = no waiting
    panel: bool = True
    record_video: bool = True  # the run's browser as .webm clips under run_files/recording/
    overlay: bool = True  # caption bar and highlight on the page (never in the evidence)
    demo: bool = False  # short reading waits and a pause on each highlight, for a video


@dataclass
class RunResult:
    run_id: str
    root: Path
    stop_reason: str
    stop_detail: str
    stages: list[Stage]
    steps: int
    manifest: dict[str, Any] = field(default_factory=dict)


class _Navigation:
    """Main-frame document requests, with the HTTP redirects that led to each."""

    def __init__(self, page: Any):
        self.records: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []  # every non-GET request, for the form log
        self.attach(page)

    def attach(self, page: Any) -> None:
        def on_request(request: Any) -> None:
            with contextlib.suppress(Exception):
                if request.method != "GET":
                    self.requests.append(
                        {
                            "at": now_iso(),
                            "method": request.method,
                            "url": request.url,
                            "post_data": (request.post_data or "")[:2000],
                        }
                    )
                if not request.is_navigation_request() or request.frame != page.main_frame:
                    return
                hops = []
                prev = request.redirected_from
                while prev is not None:
                    hops.insert(0, prev.url)
                    prev = prev.redirected_from
                self.records.append(
                    {
                        "at": now_iso(),
                        "url": request.url,
                        "method": request.method,
                        "referer": request.headers.get("referer", ""),
                        "http_redirects": hops,
                    }
                )

        page.on("request", on_request)

    def mark(self) -> tuple[int, int]:
        return len(self.records), len(self.requests)

    def since(self, mark: tuple[int, int]) -> list[dict[str, Any]]:
        return self.records[mark[0] :]

    def requests_since(self, mark: tuple[int, int]) -> list[dict[str, Any]]:
        return self.requests[mark[1] :]


class VictimRun:
    def __init__(
        self,
        url: str,
        persona: Persona,
        out_dir: Path,
        settings: Settings | None = None,
        decider: Decider | None = None,
        options: RunOptions | None = None,
        fallback: Any = None,
        launcher: Any = None,
        control: RunControl | None = None,
        notify: Any = print,
        replay: bool = False,
        contacts: ContactBook | None = None,
        mail_client: Any = None,
    ):
        self.url = url
        self.persona = persona
        self.settings = settings or get_settings()
        self.decider = decider or RuleDecider()
        self.options = options or RunOptions()
        self.fallback = fallback
        self.launcher = launcher
        self.notify = notify
        self.replay = replay
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.run_id = f"{stamp}-{secrets.token_hex(2)}"
        self.rec = Recorder(out_dir, self.run_id)
        self.control = control or RunControl(self.rec.files)
        self.control.run_dir = self.rec.files
        # Every address and number comes from the contacts file (docs/victim-contacts.md).
        book = contacts or load_contacts(self.settings.victim_contacts)
        self.contacts = contacts_for(book, persona, self.run_id, local=rules.is_local(url))
        self.email = self.contacts.email
        self.phone = self.contacts.phone
        self.mail_client = mail_client  # tests pass a fake mail.tm client
        self.history = History()
        self.stage: Stage | None = None
        self.step = 0
        self.forms_sent = 0
        self.chat_seen: set[tuple[int, str]] = set()
        self.started = time.monotonic()
        hold = 1.2 if self.options.demo else min(0.6 * self.options.pace, 0.6)
        self.overlay = Overlay(
            who=f"persona {persona.ref} (synthetic)",
            draw=self.options.overlay,
            hold_s=hold if self.options.overlay else 0.0,
        )
        self.rec.before_capture = self.overlay.clear
        self._cap: tuple[str, str, str] = ("", "", "")  # caption, reason, badge of this step
        self._clip_pages: list[Any] = []
        self._clip_paths: list[Path | None] = []
        self.recording: dict[str, Any] | None = None
        self.vb: VictimBrowser | None = None
        self.page: Any = None
        self.nav: _Navigation | None = None
        self.nav_mark = (0, 0)
        self._history_url = ""

    # -- small helpers -----------------------------------------------------------

    def say(self, line: str) -> None:
        self.control.note(line)
        if self.notify:
            self.notify(f"[victim {self.run_id}] {line}")

    def wait(self, seconds: float) -> None:
        """Wait in short slices, keeping page events flowing and obeying stop."""
        end = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < end and not self.control.stopped:
            self.page.wait_for_timeout(int(min(0.25, end - time.monotonic()) * 1000) + 1)

    def settle(self, before: str | None = None, timeout: float = 8.0, stable: float = 1.2) -> None:
        with contextlib.suppress(Exception):
            self.page.wait_for_load_state("domcontentloaded", timeout=int(timeout * 1000))
        with contextlib.suppress(Exception):
            self.page.wait_for_load_state("networkidle", timeout=2500)
        # Chat bots type their next message after a delay: wait until the page stops changing.
        deadline = time.monotonic() + timeout
        last, stable_since = self._quick(), time.monotonic()
        changed = before is None or last != before
        while time.monotonic() < deadline:
            self.page.wait_for_timeout(250)
            now = self._quick()
            if now != last:
                last, stable_since, changed = now, time.monotonic(), True
            elif (
                changed
                and time.monotonic() - stable_since >= stable
                or not changed
                and time.monotonic() - stable_since >= 3.0
            ):
                return

    def _quick(self) -> str:
        try:
            return self.page.evaluate(QUICK_STATE_JS)
        except Exception:  # noqa: BLE001 - navigating
            return ""

    def show(self, caption: str, sub: str = "", xpath: str = "", hold: float | None = None) -> None:
        """Caption (and highlight) on the page for the video and the person watching."""
        badge = self._cap[2] if xpath else ""
        self.overlay.show(self.page, caption, sub, badge, xpath=xpath, dot=bool(xpath), hold=hold)

    def _demo_hold(self, seconds: float) -> float:
        return seconds if self.options.demo else 0.0

    def observe(self) -> PageState:
        self.overlay.clear(self.page)
        for _ in range(3):
            try:
                return observe(self.page)
            except Exception:  # noqa: BLE001 - the page navigated while being read
                self.page.wait_for_timeout(500)
        return observe(self.page)

    def reading_time(self, state: PageState) -> float:
        words = min(len(state.text.split()), 1500)
        seconds = words / max(self.persona.digital.reading_wpm, 60) * 60
        if self.options.demo:  # enough to read the caption, not the page
            return min(max(seconds * 0.1, 1.5), 3.5)
        return min(max(seconds, 2.0), 25.0) * self.options.pace

    # -- stages ------------------------------------------------------------------

    def _new_or_known_stage(
        self, url: str, how: str, started: str, nav: dict[str, Any] | None, action: str
    ) -> bool:
        """Make `url` the current stage. True when it is a stage not seen before."""
        known = self.rec.stage_for(url)
        if known is not None:
            if self.stage is not None and known is not self.stage:
                self.rec.snapshot(self.page, self.stage)
                known.visits += 1
                self.say(f"back on stage {known.index}")
            self.stage = known
            return False
        if len(self.rec.stages) >= self.options.max_pages:
            return False
        state = self.observe()
        prev = self.stage  # its last snapshot was taken before the action that left it
        arrived = {
            "how": how,
            "step": self.step,
            "action": action,
            "from_stage": prev.index if prev else None,
            "from_url": prev.url if prev else (nav or {}).get("referer", ""),
            "to_url": url,
            "http_redirects": (nav or {}).get("http_redirects", []),
            "request_method": (nav or {}).get("method", "GET"),
            "referer_sent": (nav or {}).get("referer", ""),
            "requested_at": (nav or {}).get("at", started),
        }
        kind = rules.stage_kind(
            state, after_submit=how == "form_submit", sent_before=self.forms_sent > 0
        )
        stage = self.rec.new_stage(self.page, url, kind, started, arrived)
        line = self.rec.append(
            "navigation",
            {
                "stage": stage.index,
                **arrived,
                "proof": f"{how} at step {self.step}" + (f": {action}" if action else ""),
            },
        )
        stage.arrived_from = {**arrived, "navigation_line": line}
        self.rec.write_manifest(stage)
        self.stage = stage
        self.say(f"stage {stage.index}: {stage.kind} at {url[:100]} ({how})")
        reached = {
            "start": "opened from the ad link",
            "link_click": "reached by clicking a link",
            "chat_hand_off": "reached from the chat",
            "form_submit": "reached by sending the form",
            "button_click": "reached by clicking a button",
            "new_tab": "opened in a new tab",
        }.get(how, how)
        redirects = len(arrived["http_redirects"])
        kind_words = {
            "advertorial": "an article (advertorial)",
            "chat": "a chat",
            "quiz": "a quiz",
            "form": "a form",
            "thank_you": "a thank-you page",
            "decoy": "a harmless-looking decoy page",
        }
        self.show(
            f"Page {stage.index}: {kind_words.get(stage.kind, 'the next page')}",
            f"{reached}"
            + (f", through {redirects} redirect(s)" if redirects else "")
            + f" — {urlsplit(url).hostname or url[:60]}",
            hold=self._demo_hold(2.0),
        )
        return True

    def absorb_navigation(self, how: str, started: str, action: str = "") -> bool:
        """Turn document loads since the last mark into stages. True if a new stage appeared."""
        assert self.nav is not None
        loads = self.nav.since(self.nav_mark)
        self.nav_mark = self.nav.mark()
        new = False
        if loads:
            final = loads[-1]
            new = self._new_or_known_stage(final["url"], how, started, final, action)
        elif (
            self.stage is not None
            and stage_key(self.page.url) != stage_key(self.stage.url)
            and self.page.url != self._history_url
        ):
            # A history-API change keeps the same document: same stage, URL noted once.
            self._history_url = self.page.url
            self.rec.append(
                "navigation",
                {
                    "stage": self.stage.index,
                    "how": "history_api",
                    "to_url": self.page.url,
                    "step": self.step,
                },
            )
        return new

    def update_stage_kind(self, state: PageState) -> None:
        if self.stage is None:
            return
        kind = rules.stage_kind(state, sent_before=self.forms_sent > 0)
        if kind != "other" and kind not in self.stage.kinds:
            if self.stage.kinds == ["other"]:  # judged before the page had rendered
                self.stage.kinds = []
            self.stage.kinds.append(kind)
            if self.stage.kind == "other":
                self.stage.kind = kind
            self.rec.write_manifest(self.stage)

    def record_chat(self, state: PageState) -> int:
        """Log new bot messages; returns how many there were."""
        assert self.stage is not None
        new = 0
        for message in state.chat:
            if message.get("who") != "bot":
                continue
            key = (self.stage.index, message["text"])
            if key in self.chat_seen:
                continue
            self.chat_seen.add(key)
            new += 1
            self.rec.append(
                "chat",
                {
                    "stage": self.stage.index,
                    "step": self.step,
                    "who": "bot",
                    "text": message["text"],
                },
            )
        return new

    # -- waiting for a person (VV-24) and pauses (VV-26) --------------------------

    def log_user_actions(self) -> None:
        assert self.vb is not None
        for event in self.vb.events.user:
            if self.control.paused or self.control.state.get("status") == "waiting_for_person":
                self.rec.append(
                    "steps",
                    {
                        "actor": "user",
                        "step": self.step,
                        "event": event["detail"],
                        "url": event.get("url", ""),
                    },
                )
        self.vb.events.user.clear()

    def hold_while_paused(self) -> None:
        if not self.control.paused:
            return
        self.control.update(status="paused", notice="Paused: you may act in the window.")
        self.say("paused; actions in the window are logged as yours")
        start = now_iso()
        while self.control.paused and not self.control.stopped:
            self.page.wait_for_timeout(300)
            self.log_user_actions()
        self.log_user_actions()
        self.control.update(status="running", notice="")
        self.absorb_navigation("user", start, "navigation by the user while paused")
        self.say("resumed")

    def wait_for_person(self, what: str) -> bool:
        assert self.vb is not None
        limit = self.options.human_wait_s
        text = {"captcha": "a CAPTCHA or bot check", "login": "a login form"}.get(what, what)
        self.control.update(
            status="waiting_for_person",
            notice=f"Action needed in the window: {text}. Waiting up to {int(limit // 60)} min.",
        )
        self.say(f"the page shows {text}; waiting for a person (never solved by the agent)")
        self.rec.append(
            "blocks",
            {"step": self.step, "kind": what, "url": self.page.url, "status": "waiting_for_person"},
        )
        with contextlib.suppress(Exception):
            self.page.bring_to_front()
        start, started_iso = time.monotonic(), now_iso()
        while time.monotonic() - start < limit and not self.control.stopped:
            self.page.wait_for_timeout(1000)
            self.log_user_actions()
            if rules.obstacle(self.observe()) is None:
                self.control.update(status="running", notice="")
                self.rec.append(
                    "blocks", {"step": self.step, "kind": what, "status": "cleared by a person"}
                )
                self.absorb_navigation("user", started_iso, f"{what} cleared by a person")
                self.say("cleared; continuing")
                return True
        self.log_user_actions()
        return False

    # -- acting ------------------------------------------------------------------

    def locator(self, xpath: str) -> Any:
        return self.page.locator(f"xpath={xpath}").first

    def type_value(self, xpath: str, value: str, label: str = "") -> None:
        field = self.locator(xpath)
        field.scroll_into_view_if_needed(timeout=3000)
        if label:
            self.show(f"Types “{value}” into {label}", self._cap[1], xpath=xpath)
        field.click(timeout=3000)
        field.fill("")
        if self.options.pace > 0 or self.options.demo:
            delay = 1000 / max(self.persona.digital.typing_cps, 1.0) * max(self.options.pace, 0.3)
            field.press_sequentially(value, delay=min(delay, 120 if self.options.demo else 400))
        else:
            field.fill(value)

    def act_form(self, c: Candidate, state: PageState) -> dict[str, Any]:
        form = next(f for f in state.forms if f["index"] == c.detail.get("form_index"))
        plan = plan_form(form, self.persona, self.email, self.phone)
        if self.stage is not None and "form" not in self.stage.kinds:
            self.stage.kinds.append("form")
            self.rec.write_manifest(self.stage)
        assert self.nav is not None
        mark = self.nav.mark()
        typed: dict[str, str] = {}
        for xpath, value in plan.values.items():
            self.type_value(xpath, value, self._field_label(form, xpath))
            typed[xpath] = value
            self.wait(0.4 * self.options.pace)
        for xpath, option in plan.selects.items():
            self.locator(xpath).select_option(label=option, timeout=3000)
        for xpath in plan.checks:
            self.locator(xpath).check(timeout=3000)
        # Read every value back: nothing but persona data may be on the form (VV-21).
        xpaths = [
            f["xpath"]
            for f in form["fields"]
            if f.get("visible") and f.get("type") not in {"checkbox", "radio"}
        ]
        present = self.page.evaluate(READ_VALUES_JS, xpaths)
        allowed = allowed_values(self.persona, self.email, self.phone)
        foreign = [
            v for v in present if v is not None and not value_allowed(v, allowed, self.phone)
        ]
        if foreign:
            plan.refuse(
                "foreign_value", f"the form holds values the persona did not give: {foreign[:3]}"
            )
        record: dict[str, Any] = {
            "stage": self.stage.index if self.stage else None,
            "step": self.step,
            "form_action": form.get("action", ""),
            "method": form.get("method", ""),
            "fields": plan.fields,
            "hidden_fields": form.get("hidden", []),
            "values_given": {self._field_name(form, x): v for x, v in typed.items()},
            "email": self.email,
            "sent": False,
        }
        before_submit = self.nav.requests_since(mark)
        if before_submit:
            record["sent_while_typing"] = before_submit  # kits that post fields on blur
        if not plan.submit:
            record.update(
                stop_reason=plan.stop_reason, stop_detail=plan.stop_detail, refusals=plan.refusals
            )
            self.rec.append("forms", record)
            why = "; ".join(f"{r['reason']}: {r['detail']}" for r in plan.refusals)
            return {"stop": (rules.FORM_NOT_SENT, why)}
        mark = self.nav.mark()
        quick = self._quick()
        if form.get("submit_xpath"):
            self.show(
                f"Sends the form (“{form.get('submit_text') or 'submit'}”)",
                f"to {urlsplit(form.get('action') or self.page.url).hostname or 'the same page'}",
                xpath=form["submit_xpath"],
            )
            self.locator(form["submit_xpath"]).click(timeout=5000)
        else:
            self.page.evaluate(
                "(x) => { const f = document.evaluate(x, document, null, 9, null).singleNodeValue; f && f.requestSubmit(); }",
                form["xpath"],
            )
        self.settle(quick)
        sent = self.nav.requests_since(mark)
        loads = self.nav.since(self.nav_mark)
        errors = (
            self.page.evaluate(FORM_ERRORS_JS, form["xpath"])
            if not loads
            else {"gone": True, "invalid": [], "errors": []}
        )
        record.update(
            sent=True, sent_to=sent, navigation=[x["url"] for x in loads], response=errors
        )
        self.forms_sent += 1
        if not loads and not errors["gone"] and (errors["invalid"] or errors["errors"]):
            # VV-32: the form refused our data. That is evidence; the run stops at this form.
            record["rejected"] = True
            self.rec.append("forms", record)
            return {"stop": (rules.FORM_NOT_SENT, f"the form refused the data: {errors}")}
        self.rec.append("forms", record)
        return {"submitted": True}

    @staticmethod
    def _field_label(form: dict[str, Any], xpath: str) -> str:
        """What a viewer would call the field: its label, else its name."""
        f = next((x for x in form["fields"] if x.get("xpath") == xpath), {})
        return f"“{f.get('label') or f.get('name') or 'the field'}”"

    @staticmethod
    def _field_name(form: dict[str, Any], xpath: str) -> str:
        f = next((x for x in form["fields"] if x.get("xpath") == xpath), {})
        return f.get("name") or f.get("label") or xpath

    def act(self, c: Candidate, d: Decision, state: PageState) -> dict[str, Any]:
        if c.kind == "form":
            return self.act_form(c, state)
        if c.kind == "scroll":
            height = self.persona.digital.viewport.height
            for _ in range(3):
                self.page.mouse.wheel(0, height * 0.3)
                self.wait(0.5 * self.options.pace)
            return {}
        if c.kind == "media":
            self.show(self._cap[0], self._cap[1], xpath=c.xpath)
            self.locator(c.xpath).evaluate("v => { v.muted = true; return v.play && v.play(); }")
            self.wait(
                min(10 * self.options.pace, 4.0) if self.options.demo else 10 * self.options.pace
            )
            return {}
        if c.kind == "text_input":
            assert d.text is not None
            self.type_value(c.xpath, d.text, "the chat")
            self.page.keyboard.press("Enter")
            self.rec.append(
                "chat",
                {
                    "stage": self.stage.index,
                    "step": self.step,
                    "who": "persona",
                    "text": d.text,
                    "improvised": d.improvised,
                },
            )
            return {"typed": d.text}
        target = self.locator(c.xpath)
        target.scroll_into_view_if_needed(timeout=3000)
        self.show(self._cap[0], self._cap[1], xpath=c.xpath)
        if c.kind == "chat_option":
            self.rec.append(
                "chat",
                {
                    "stage": self.stage.index,
                    "step": self.step,
                    "who": "persona",
                    "text": c.text,
                    "question": d.question,
                    "topic": d.topic,
                    "improvised": d.improvised,
                },
            )
            if d.topic:
                self.history.answers.setdefault(d.topic, c.text)
            if d.question:
                self.history.asked.setdefault((self.page.url.split("#")[0], d.question), c.text)
        target.click(timeout=5000)
        return {}

    # -- the loop ----------------------------------------------------------------

    def stop_on(self, block: rules.Block, state: PageState | None = None) -> tuple[str, str]:
        record = {"step": self.step, **block.to_json(), "url": self.page.url}
        shot = self.rec.step_screenshot(self.page, self.step, "-stop")
        if shot:
            record["screenshot"] = shot
        self.rec.append("blocks", record)
        self.say(f"stopped: {block.reason}: {block.asked[:120]}")
        return block.reason, block.asked

    def provision_mailbox(self) -> None:
        """mail.tm scope: create this run's inbox before anything is typed (VV-30)."""
        c = self.contacts
        if c.provider != "mail.tm" or c.email_rule is None:
            return
        from .mailbox import MailboxError, MailTm

        try:
            box = (self.mail_client or MailTm(c.email_rule.api)).create(
                c.email_rule.local_part(self.persona.id, self.run_id), c.email_rule.domain
            )
        except MailboxError as exc:
            policy = f"{c.scope}: mail.tm inbox could not be created ({exc}); no e-mail given"
            self.contacts = replace(c, email_policy=policy)
            self.say(policy)
            return
        box.save(self.rec.files)
        self.email = box.address
        self.contacts = replace(
            c, email=box.address, email_policy=f"{c.scope}: mail.tm inbox {box.address}"
        )
        self.say(f"mail.tm inbox for this run: {box.address}")

    def run(self) -> RunResult:
        stop: tuple[str, str] = (rules.ERROR, "the run did not start")
        panel = PanelServer(self.control) if self.options.panel else None
        referer = referrer_for(self.persona)
        try:
            self.provision_mailbox()
            self.vb = launch(
                self.persona,
                self.rec.files / "session.har",
                self.url,
                fallback=self.fallback,
                panel_url=panel.url if panel else None,
                launcher=self.launcher,
                video_dir=self.rec.files / "recording" if self.options.record_video else None,
            )
            self.page = self.vb.page
            self._adopt(self.page)
            self.nav = _Navigation(self.page)
            self.control.update(status="running", persona=self.persona.ref, step=0, url=self.url)
            started = now_iso()
            self.say(f"opening the ad link as {self.persona.ref}")
            self.page.goto(self.url, referer=referer or None, wait_until="domcontentloaded")
            self.settle()
            platform = self.persona.digital.platform
            self.show(
                "Opens the ad link",
                f"as {self.persona.ref}, arriving from {platform}"
                if platform
                else self.persona.ref,
                hold=self._demo_hold(2.0),
            )
            self.absorb_navigation("start", started, "ad link opened with the persona's referrer")
            if self.stage is None:  # no document request seen (should not happen)
                self._new_or_known_stage(self.page.url, "start", started, None, "")
            stop = self.loop()
        except KeyboardInterrupt:
            stop = (rules.USER_STOP, "interrupted from the terminal")
        except Exception as exc:  # noqa: BLE001 - recorded; everything so far is kept
            stop = (rules.ERROR, f"{type(exc).__name__}: {exc}"[:500])
            with contextlib.suppress(Exception):
                self.rec.append(
                    "blocks", {"step": self.step, "reason": rules.ERROR, "asked": stop[1]}
                )
        finally:
            if self.stage is not None and self.page is not None:
                with contextlib.suppress(Exception):
                    self.rec.snapshot(self.page, self.stage)
            self.control.update(status="finished", notice=f"Stopped: {stop[0]} — {stop[1][:200]}")
            if self.page is not None and self.vb is not None:
                self.show(
                    f"Run ends: {rules.STOP_WORDS.get(stop[0], stop[0].replace('_', ' '))}",
                    stop[1][:160],
                    hold=self._demo_hold(3.0),
                )
            if self.vb is not None:
                self.vb.close()
                self.collect_recording()
            if panel is not None:
                panel.close()
        return self.finish(stop, referer)

    def loop(self) -> tuple[str, str]:
        assert self.vb is not None and self.nav is not None
        idle = waited_empty = 0
        while True:
            if self.control.stopped:
                return rules.USER_STOP, "stopped by the user"
            self.hold_while_paused()
            if self.control.stopped:
                return rules.USER_STOP, "stopped by the user"
            if self.step >= self.options.max_steps:
                return rules.BUDGET_STEPS, f"{self.options.max_steps} actions"
            if time.monotonic() - self.started > self.options.max_seconds:
                return rules.BUDGET_TIME, f"{int(self.options.max_seconds)} s"
            self.log_user_actions()
            if self.absorb_navigation("script_redirect", now_iso(), "navigation without an action"):
                idle = 0
            self.switch_to_popup()
            state = self.observe()
            assert self.stage is not None
            self.rec.snapshot(self.page, self.stage)
            if self.record_chat(state):
                idle = 0  # the bot moved on: that is progress inside the stage
            self.update_stage_kind(state)
            forbidden = self.vb.events.drain_forbidden()
            if forbidden:
                ev = forbidden[0]
                reason = (
                    rules.FORBIDDEN_DOWNLOAD
                    if ev["kind"] == "download"
                    else rules.FORBIDDEN_PERMISSION
                )
                return self.stop_on(rules.Block(reason, f"{ev['kind']}: {ev['detail']}"))
            what = rules.obstacle(state)
            if what is not None:
                if self.wait_for_person(what):
                    continue
                if self.control.stopped:
                    return rules.USER_STOP, "stopped by the user"
                return self.stop_on(
                    rules.Block(rules.BLOCKED, f"{what} not handled within the wait limit")
                )
            block = rules.page_block(state)
            if block is not None:
                return self.stop_on(block)
            if self.forms_sent:
                said = rules.handoff(state)
                if said:
                    return self.stop_on(rules.Block(rules.OFFSITE_HANDOFF, said))
            if len(self.rec.stages) >= self.options.max_pages and idle:
                return rules.BUDGET_PAGES, f"{self.options.max_pages} pages"
            allowed, refused = rules.split_candidates(state)
            decision = self.decider.choose(self.persona, state, allowed, self.history)
            if decision is None and waited_empty < 2:
                waited_empty += 1  # a bot may still be typing: look again before giving up
                self.page.wait_for_timeout(3000)
                continue
            waited_empty = 0
            if decision is None:
                onward = rules.only_way_on_is_forbidden(refused)
                if onward is not None:
                    return self.stop_on(onward)
                return rules.NO_ACTIONS, "nothing left to do on this page"
            chosen = next(c for c in allowed if c.n == decision.n)
            self.step += 1
            self.control.update(
                step=self.step,
                stage=f"{self.stage.index} ({'/'.join(self.stage.kinds)})",
                url=state.url,
                next=chosen.describe(),
                reason=decision.reason,
            )
            self.say(f"step {self.step}: {chosen.describe()[:120]} — {decision.reason}")
            self._cap = (
                action_caption(chosen, decision.text),
                decision.reason,
                f"Step {self.step}",
            )
            self.overlay.show(self.page, self._cap[0], self._cap[1], self._cap[2], hold=0)
            self.wait(
                self.reading_time(state) if chosen.kind != "scroll" else 1.0 * self.options.pace
            )
            if self.control.stopped or self.control.paused:
                self.step -= 1
                continue
            # Checked again right before acting, on the page as it is now.
            now = self.observe()
            block = rules.page_block(now)
            if block is not None:
                return self.stop_on(block)
            ok, why = rules.candidate_verdict(chosen, state)
            if not ok:
                return self.stop_on(rules.Block(why, chosen.describe()))
            # Pages re-render (a chat's "still there?" nudge): act on the element as it is now.
            fresh = next(
                (c for c in now.candidates if History.signature(c) == History.signature(chosen)),
                None,
            )
            if fresh is None or fresh.detail.get("covered"):
                self.say("the chosen action is gone or covered (a pop-up?); looking again")
                self.step -= 1
                idle += 1
                if idle >= self.options.no_progress_steps:
                    return rules.NO_PROGRESS, f"no new stage in {idle} steps"
                continue
            chosen = replace(chosen, xpath=fresh.xpath, detail=fresh.detail)
            if chosen.kind == "form":
                state = now  # fill the form as it is now
            before_shot = self.rec.step_screenshot(self.page, self.step)
            url_before, digest_before, started = self.page.url, state.digest(), now_iso()
            self.nav_mark = self.nav.mark()
            quick = self._quick()
            outcome: dict[str, Any] = {}
            error = ""
            try:
                outcome = self.act(chosen, decision, state)
            except Exception as exc:  # noqa: BLE001 - a failed click is recorded, not fatal
                error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
            self.overlay.unpoint(self.page)
            if not outcome.get("submitted") and not outcome.get("stop"):
                # A chat bot "types" its next message after a pause: wait for it longer.
                chatty = chosen.kind in {"chat_option", "text_input"}
                self.settle(quick, timeout=15.0 if chatty else 8.0, stable=3.0 if chatty else 1.2)
            how = {"link": "link_click", "chat_option": "chat_hand_off", "form": "form_submit"}.get(
                chosen.kind, "button_click"
            )
            self.switch_to_popup()
            new_stage = self.absorb_navigation(how, started, chosen.describe()[:200])
            after = self.observe()
            if not new_stage and outcome.get("submitted") and _text_changed(state.text, after.text):
                # A thank-you shown in place, without a new document: its own stage.
                key = f"{self.page.url.split('#')[0]}#after-submit-{self.forms_sent}"
                new_stage = self._new_or_known_stage(
                    key, "form_submit", started, None, chosen.describe()[:200]
                )
                if new_stage and self.stage is not None:
                    self.stage.kind, self.stage.kinds = "thank_you", ["thank_you"]
                    self.rec.write_manifest(self.stage)
            changed = new_stage or after.digest() != digest_before
            note = "new stage" if new_stage else ("page changed" if changed else "no change")
            if error:
                note = f"failed: {error}"
            self.history.record(state, chosen, changed and not error, note)
            if chosen.kind == "link":
                self.history.visited.add(chosen.href.split("#")[0])
            self.rec.append(
                "steps",
                {
                    "actor": "agent",
                    "step": self.step,
                    "stage": self.stage.index if self.stage else None,
                    "observation": {
                        "url": state.url,
                        "title": state.title,
                        "words": len(state.text.split()),
                        "chat_messages": len(state.chat),
                        "digest": digest_before,
                    },
                    "offered": [c.describe() for c in allowed],
                    "refused": refused,
                    "chosen": chosen.describe(),
                    "chosen_kind": chosen.kind,
                    "reason": decision.reason,
                    "decided_by": decision.by,
                    "improvised": decision.improvised,
                    "typed": outcome.get("typed"),
                    "url_before": url_before,
                    "url_after": self.page.url,
                    "outcome": note,
                    "screenshot": before_shot,
                    "screenshot_after": self.rec.step_screenshot(self.page, self.step, "-after"),
                },
            )
            if outcome.get("stop"):
                reason, detail = outcome["stop"]
                self.say(f"stopped at the form: {detail[:150]}")
                return reason, detail
            idle = 0 if new_stage else idle + 1
            if idle >= self.options.no_progress_steps:
                return rules.NO_PROGRESS, f"no new stage in {idle} steps"

    def _adopt(self, page: Any) -> None:
        """A page the run continues on: its video becomes the next clip."""
        if self.vb is None or any(p is page for p in self._clip_pages):
            return
        self._clip_pages.append(page)
        self.overlay.start_clip(page, self.vb.events.opened_at.get(id(page)))
        # The path is known from the start; the file is complete once the context closes,
        # after which Playwright can no longer be asked.
        video = None
        with contextlib.suppress(Exception):
            video = Path(page.video.path()) if page.video else None
        self._clip_paths.append(video)

    def collect_recording(self) -> None:
        """After the context is closed: name the clips in order, write their captions."""
        if self.vb is None or self.vb.video_dir is None:
            return
        out = self.vb.video_dir
        clips = []
        keep: set[Path] = set()
        for n, src in enumerate(self._clip_paths, 1):
            if src is None or not src.is_file():
                continue
            dest = out / f"clip-{n:02d}.webm"
            with contextlib.suppress(OSError):
                src.replace(dest)
            vtt = out / f"clip-{n:02d}.vtt"
            vtt.write_text(self.overlay.vtt(n), encoding="utf-8")
            keep |= {dest, vtt}
            clips.append(
                {
                    "video": f"{dest.relative_to(self.rec.root).as_posix()}",
                    "captions": f"{vtt.relative_to(self.rec.root).as_posix()}",
                    "cues": sum(1 for c in self.overlay.cues if c.clip == n),
                }
            )
        for stray in out.glob("*.webm"):  # pages the run did not follow (closed pop-ups)
            if stray not in keep:
                with contextlib.suppress(OSError):
                    stray.unlink()
        v = self.persona.digital.viewport
        self.recording = {
            "clips": clips,
            "size": {"width": v.width, "height": v.height},
            "overlay": self.options.overlay,
            "demo": self.options.demo,
            "note": "the caption bar and outline are drawn for viewers and removed before every "
            "screenshot and DOM snapshot",
        }
        (out / "recording.json").write_text(
            json.dumps(self.recording, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    def switch_to_popup(self) -> None:
        """A link that opens a new tab continues the funnel there (VV-22: only if in-funnel)."""
        assert self.vb is not None and self.nav is not None
        while self.vb.events.popups:
            popup = self.vb.events.popups.pop(0)
            with contextlib.suppress(Exception):
                popup.wait_for_load_state("domcontentloaded", timeout=10_000)
            url = popup.url
            if (
                url
                and not url.startswith("about:")
                and not rules.off_funnel_host(urlsplit(url).hostname or "")
            ):
                self.rec.append("navigation", {"how": "new_tab", "to_url": url, "step": self.step})
                if self.stage is not None:
                    self.rec.snapshot(self.page, self.stage)
                self.page = popup
                self._adopt(popup)
                self.nav.attach(popup)
                self.nav.records.append(
                    {
                        "at": now_iso(),
                        "url": url,
                        "method": "GET",
                        "referer": "",
                        "http_redirects": [],
                    }
                )
                with contextlib.suppress(Exception):
                    popup.bring_to_front()
            else:
                with contextlib.suppress(Exception):
                    popup.close()

    def finish(self, stop: tuple[str, str], referer: str) -> RunResult:
        self.rec.split_har(self.rec.files / "session.har")
        for stage in self.rec.stages:
            self.rec.write_manifest(stage)
        manifest = {
            "run_id": self.run_id,
            "persona": {"id": self.persona.id, "version": self.persona.version},
            "start_url": self.url,
            "referrer": referer,
            "device": self.persona.digital.model_dump(),
            "locale": self.persona.locale.model_dump(),
            "egress": "local replay, confined to loopback" if self.replay else "direct",
            "replay": self.replay,
            "started_at": self.rec.stages[0].started if self.rec.stages else None,
            "ended_at": now_iso(),
            "stop_reason": stop[0],
            "stop_detail": stop[1],
            "budgets": {
                "max_steps": self.options.max_steps,
                "max_seconds": self.options.max_seconds,
                "max_pages": self.options.max_pages,
                "human_wait_s": self.options.human_wait_s,
            },
            "steps": self.step,
            "decider": getattr(self.decider, "name", type(self.decider).__name__),
            "email": self.email,
            "phone": self.phone,
            "contacts": self.contacts.to_json(),
            "answers": self.history.answers,
            "blocked_requests": self.vb.events.blocked_requests[:500] if self.vb else [],
            "dialogs": self.vb.events.dialogs if self.vb else [],
            "stages": [
                {
                    "index": s.index,
                    "dir": s.name,
                    "url": s.url,
                    "kind": s.kind,
                    "kinds": s.kinds,
                    "arrived_from": s.arrived_from,
                }
                for s in self.rec.stages
            ],
            "funnel_shape": funnel_shape(self.rec.stages),
            "recording": self.recording,
            "tool": {"name": "ftf.victim", "version": __version__},
        }
        self.rec.write_json("run.json", manifest)
        return RunResult(
            self.run_id, self.rec.root, stop[0], stop[1], self.rec.stages, self.step, manifest
        )


def funnel_shape(stages: list[Stage]) -> list[str]:
    """Stage kinds in order, one entry per change of kind (advertorial → chat → form …)."""
    out: list[str] = []
    for stage in stages:
        for kind in stage.kinds:
            if kind != "other" and (not out or out[-1] != kind):
                out.append(kind)
    return out


def _text_changed(before: str, after: str) -> bool:
    a, b = set(before.split()), set(after.split())
    if not b:
        return False
    return len(b - a) / max(len(b), 1) > 0.3
