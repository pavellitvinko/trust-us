"""What the victim may and may not do (VV-20…VV-24), enforced in code.

The decider only ever sees candidates these rules allow, and the agent checks again right
before it acts. A page that asks for something forbidden ends the run at that step, with
what was asked recorded (VV-21). Browser permissions and downloads are refused by the browser
setup (`browser.py`); requests for them are recorded and also stop the run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .contacts import classify_field, field_text
from .observe import Candidate, PageState
from .persona import fold

# Stop reasons, as recorded in the run manifest.
FORBIDDEN_PAYMENT = "forbidden_payment"
FORBIDDEN_UPLOAD = "forbidden_upload"
FORBIDDEN_DOWNLOAD = "forbidden_download"
FORBIDDEN_PERMISSION = "forbidden_permission"
FORBIDDEN_LOGIN = "forbidden_login"
OFFSITE_HANDOFF = "offsite_handoff"
FORM_NOT_SENT = "form_not_sent"  # phone required, no e-mail domain, form refused our data
NO_PROGRESS = "no_progress"
NO_ACTIONS = "no_actions"
BUDGET_STEPS = "budget_steps"
BUDGET_TIME = "budget_time"
BUDGET_PAGES = "budget_pages"
BLOCKED = "blocked"  # CAPTCHA or login nobody handled (VV-24)
USER_STOP = "user_stop"
ERROR = "error"

# Stop reasons in plain words, for the caption shown at the end of a run.
STOP_WORDS = {
    FORBIDDEN_PAYMENT: "a payment step, which the agent never fills in",
    FORBIDDEN_UPLOAD: "a document or photo upload, which the agent never does",
    FORBIDDEN_DOWNLOAD: "a download or app install, which the agent refuses",
    FORBIDDEN_PERMISSION: "a browser permission request, refused",
    FORBIDDEN_LOGIN: "a log-in with a real account, which the agent never does",
    OFFSITE_HANDOFF: "the next step happens off the site (a call-back)",
    FORM_NOT_SENT: "a form the agent would not send",
    NO_PROGRESS: "no new page after several steps",
    NO_ACTIONS: "nothing left to do on this page",
    BUDGET_STEPS: "step limit reached",
    BUDGET_TIME: "time limit reached",
    BUDGET_PAGES: "page limit reached",
    BLOCKED: "a check nobody handled",
    USER_STOP: "stopped by the person watching",
    ERROR: "an error",
}

_PAYMENT_FRAME = re.compile(
    r"stripe|adyen|checkout\.com|braintree|paypal|payu|przelewy24|tpay|dotpay|blik|"
    r"klarna|revolut|moonpay|coinbase|binance|/pay(ment)?\b|/checkout",
    re.IGNORECASE,
)
_PAYMENT_ACTION = re.compile(
    r"zapła[ćc]|płac[eę]|wpła[ćc]|wpłata|depozyt|deposit|\bpay\b|pay now|checkout|"
    r"doładuj|top.?up|blik|przelew|transfer funds|zasil konto",
    re.IGNORECASE,
)
_DOWNLOAD_HREF = re.compile(
    r"\.(apk|xapk|exe|msi|dmg|pkg|ipa|appx|msix|deb|rpm|zip|rar|7z|jar|bat|scr|iso)(\?|#|$)",
    re.IGNORECASE,
)
_STORE_HOSTS = ("play.google.com", "apps.apple.com", "itunes.apple.com", "appgallery.huawei.com")
_INSTALL_TEXT = re.compile(
    r"pobierz aplikacj|zainstaluj|install( the)? app|download( the)? app|pobierz apk|"
    r"get it on google play|app store|instaluj",
    re.IGNORECASE,
)
_OAUTH_TEXT = re.compile(
    r"(zaloguj|kontynuuj|continue|sign in|log in|login|połącz|connect|register|zarejestruj)"
    r".{0,25}(facebook|google|apple|microsoft|linkedin|twitter|\bx\b)",
    re.IGNORECASE,
)
_OAUTH_HOSTS = (
    "accounts.google.com",
    "facebook.com/login",
    "facebook.com/dialog/oauth",
    "m.facebook.com/login",
    "appleid.apple.com",
    "login.microsoftonline.com",
    "login.live.com",
)
_UPLOAD_TEXT = re.compile(
    r"prze[śs]lij (zdj[eę]cie|skan|dow[oó]d)|zdj[eę]cie dowodu|skan dowodu|selfie|"
    r"upload (your )?(id|passport|document|photo)|weryfikacja to[żz]samo",
    re.IGNORECASE,
)
# Share buttons, real publishers and company legal pages: never part of a funnel (VV-22).
_OFF_FUNNEL_HOSTS = (
    "facebook.com",
    "fb.com",
    "messenger.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "t.co",
    "linkedin.com",
    "pinterest.",
    "reddit.com",
    "tiktok.com",
    "youtube.com",
    "google.",
    "wikipedia.org",
    "gov.pl",
    "knf.gov.pl",
    "uokik.gov.pl",
    "apple.com",
    "microsoft.com",
    "whatsapp.com",
    "wa.me",
    "t.me",
    "telegram.",
)
_SHARE = re.compile(r"share|sharer|udostępnij|udostepnij|intent/tweet|pin/create", re.IGNORECASE)
_LEGAL = re.compile(
    r"/(privacy|polityka|prywatno|cookie|terms|regulamin|rodo|gdpr|legal|imprint|impressum)",
    re.IGNORECASE,
)
_HANDOFF = re.compile(
    r"(konsultant|doradc|menad[żz]er|manager|specjalist|opiekun|agent|ekspert)\w*.{0,60}"
    r"(zadzwoni|skontaktuje|oddzwoni|will call|will contact|call you|kontakt telefon)|"
    r"(zadzwonimy|oddzwonimy|skontaktujemy si[eę]|we will call|we'll call|we will contact)",
    re.IGNORECASE,
)
_CAPTCHA_TEXT = re.compile(
    r"captcha|nie jestem robotem|i'?m not a robot|verify you are human", re.IGNORECASE
)


@dataclass(frozen=True)
class Block:
    """A forbidden step reached: why the run stops and what the page asked for."""

    reason: str
    asked: str

    def to_json(self) -> dict[str, str]:
        return {"reason": self.reason, "asked": self.asked}


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def is_local(url: str) -> bool:
    host = _host(url)
    return host in {"localhost", "127.0.0.1", "::1"} or host.endswith((".localhost", ".test"))


def off_funnel_host(host: str) -> bool:
    """Social networks, search engines, regulators and big platforms (VV-22)."""
    for h in _OFF_FUNNEL_HOSTS:
        if h.endswith("."):
            if host.startswith(h) or ("." + h) in host:
                return True
        elif host == h or host.endswith("." + h):
            return True
    return False


def page_block(state: PageState) -> Block | None:
    """A forbidden step the page itself presents (VV-21). Checked before every action."""
    fields = state.all_fields()
    for f in fields:
        if f.get("type") == "file" or f.get("capture"):
            return Block(FORBIDDEN_UPLOAD, f"a file upload field: {field_text(f).strip()!r}")
    for f in fields:
        if f.get("type") != "password" and classify_field(f) == "forbidden":
            return Block(
                FORBIDDEN_PAYMENT, f"a payment or identity field: {field_text(f).strip()!r}"
            )
    for frame in [*state.frames, *(f.get("url", "") for f in state.frame_flags)]:
        if frame and _PAYMENT_FRAME.search(frame):
            return Block(FORBIDDEN_PAYMENT, f"a payment widget frame: {frame[:160]}")
    match = _UPLOAD_TEXT.search(state.text)
    if (
        match and len(state.text.split()) < 400
    ):  # a short page that asks; not an article mentioning it
        return Block(FORBIDDEN_UPLOAD, f"the page asks for a document: {match.group(0)!r}")
    if _PAYMENT_FRAME.search(urlsplit(state.url).path):
        return Block(FORBIDDEN_PAYMENT, f"a payment page: {state.url[:160]}")
    return None


def obstacle(state: PageState) -> str | None:
    """A CAPTCHA or login wall a person may clear in the window (VV-24), else None."""
    if state.flags.get("captcha") or _CAPTCHA_TEXT.search(state.text[:3000]):
        return "captcha"
    passwords = state.flags.get("password", 0) + sum(
        f.get("password", 0) for f in state.frame_flags
    )
    if passwords:
        return "login"
    return None


def handoff(state: PageState) -> str | None:
    """The funnel says the next step happens off-site ("a consultant will call you")."""
    match = _HANDOFF.search(state.text)
    return match.group(0) if match else None


def candidate_verdict(c: Candidate, state: PageState) -> tuple[bool, str]:
    """(allowed, reason) for one candidate. The reason is recorded either way."""
    text = c.text or ""
    if c.kind == "link":
        href = c.href
        host = _host(href)
        if c.detail.get("download") or _DOWNLOAD_HREF.search(href):
            return False, FORBIDDEN_DOWNLOAD
        if any(host == h or host.endswith("." + h) for h in _STORE_HOSTS) or _INSTALL_TEXT.search(
            text
        ):
            return False, FORBIDDEN_DOWNLOAD
        if _OAUTH_TEXT.search(text) or any(h in href for h in _OAUTH_HOSTS):
            return False, FORBIDDEN_LOGIN
        if _PAYMENT_ACTION.search(text):
            return False, FORBIDDEN_PAYMENT
        if href.split("#")[0] == state.url.split("#")[0] and "#" in href:
            return False, "same-page anchor"
        if _SHARE.search(href) or _SHARE.search(text):
            return False, "share button (not the funnel)"
        if host and host != state.host and off_funnel_host(host):
            return False, "unrelated outbound site (not the funnel)"
        if _LEGAL.search(urlsplit(href).path):
            return False, "legal or privacy page (not the funnel)"
        if is_local(state.url) and host and not is_local(href):
            return False, "replay runs stay on the local replay"
        return True, "funnel link"
    if c.kind in {"button", "chat_option", "close"}:
        if _OAUTH_TEXT.search(text):
            return False, FORBIDDEN_LOGIN
        if _INSTALL_TEXT.search(text):
            return False, FORBIDDEN_DOWNLOAD
        if _PAYMENT_ACTION.search(text) and c.kind != "chat_option":
            return False, FORBIDDEN_PAYMENT
        if c.kind == "button" and c.detail.get("form_index") is not None:
            return False, "part of a form: sent through the form action"
        if _SHARE.search(text):
            return False, "share button (not the funnel)"
        return True, "page control"
    if c.kind == "text_input":
        field = c.detail.get("field") or {}
        if classify_field(field) in {"forbidden", "email", "phone"}:
            return False, "contact or forbidden field outside a form"
        return True, "free-text answer"
    if c.kind == "form":
        form = next((f for f in state.forms if f["index"] == c.detail.get("form_index")), None)
        if form is None:
            return False, "form gone"
        kinds = {classify_field(f) for f in form["fields"] if f.get("visible")}
        if "forbidden" in kinds:
            return False, FORBIDDEN_PAYMENT
        if not kinds & {"email", "phone", "first_name", "last_name", "full_name"}:
            return False, "not a lead form (comment or search box)"
        if _PAYMENT_ACTION.search(form.get("submit_text", "")):
            return False, FORBIDDEN_PAYMENT
        return True, "lead form"
    if c.kind in {"media", "scroll"}:
        return True, "reading"
    return False, f"unknown action kind {c.kind}"


def split_candidates(state: PageState) -> tuple[list[Candidate], list[dict[str, str]]]:
    """(allowed candidates, refused ones with their reason)."""
    allowed, refused = [], []
    for c in state.candidates:
        ok, why = candidate_verdict(c, state)
        if ok:
            allowed.append(c)
        else:
            refused.append({"n": str(c.n), "action": c.describe(), "reason": why})
    return allowed, refused


def only_way_on_is_forbidden(refused: list[dict[str, str]]) -> Block | None:
    """When the page's prominent onward step is forbidden, the run stops there (VV-21)."""
    for r in refused:
        if r["reason"].startswith("forbidden_"):
            return Block(r["reason"], r["action"])
    return None


_THANKS = re.compile(
    r"dzi[eę]kujemy|dziękuję|thank you|gratulacje|zgłoszenie (zostało )?przyj", re.IGNORECASE
)


def stage_kind(state: PageState, after_submit: bool = False, sent_before: bool = False) -> str:
    """advertorial | chat | quiz | form | thank_you | payment | other (for `funnel.shape`).

    A thank-you page is one reached by sending a form, or a short "thank you" page after one.
    """
    words = len(state.text.split())
    block = page_block(state)
    if block is not None and block.reason == FORBIDDEN_PAYMENT:
        return "payment"
    if after_submit or (sent_before and _THANKS.search(state.text[:1500]) and words < 400):
        return "thank_you"
    chat_options = [c for c in state.candidates if c.kind == "chat_option"]
    if len(state.chat) >= 2 or (state.chat and chat_options):
        return "chat"
    if chat_options and len(chat_options) >= 2:
        return "quiz"
    lead_forms = [
        f
        for f in state.forms
        if f.get("visible")
        and {classify_field(x) for x in f["fields"] if x.get("visible")}
        & {"email", "phone", "first_name", "last_name", "full_name"}
    ]
    if words >= 600:
        return "advertorial"
    if lead_forms:
        return "form"
    return "other"


def normalise_text(text: str) -> str:
    return " ".join(fold(text).split())
