"""What the probe may do. No browser here, so every rule is unit-tested.

Pages are hostile input: nothing read from a page can widen these rules.
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
from enum import IntEnum
from pathlib import Path
from urllib.parse import urlsplit

from ..extract._material import registrable_domain


class Level(IntEnum):
    EXPLORE = 1  # follow links and quiz steps; submit nothing
    PAYMENT = 2  # also read checkout pages; never fill a payment field
    REGISTER = 3  # also register and log in with the bait identity
    WATCH = 4  # level 3, and the site is recorded for the mailbox watcher


class ProbeRefused(ValueError):
    """The target or the requested level is outside policy."""


LOOPBACK = {"127.0.0.1", "localhost", "::1"}
SECRET_SETTINGS = (
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "FTF_RESEND_API_KEY",
    "FTF_PROBE_IMAP_PASSWORD",
    "FTF_HIBP_API_KEY",
)

# Checked in this order, so a "card e-mail" field is a card field.
_FIELD_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "card",
        re.compile(r"\bcc[-_ ]|card|karty|karta|cvv|cvc|expir|ważnoś|waznosc", re.IGNORECASE),
    ),
    (
        "bank",
        re.compile(r"iban|bank|konto bankowe|account.?number|nr.?konta|przelew", re.IGNORECASE),
    ),
    ("national_id", re.compile(r"pesel|\bnip\b|tax.?id|ssn|national.?id", re.IGNORECASE)),
    ("document", re.compile(r"passport|paszport|dow[oó]d|document|dokument|selfie", re.IGNORECASE)),
    ("phone", re.compile(r"phone|\btel\b|telefon|mobile|kom[oó]rk", re.IGNORECASE)),
    ("birth", re.compile(r"birth|\bdob\b|urodzeni", re.IGNORECASE)),
    (
        "address",
        re.compile(
            r"address|adres(?!.*e-?mail)|street|ulica|\bcity\b|miasto|postal|\bzip\b|kod.?poczt",
            re.IGNORECASE,
        ),
    ),
    ("email", re.compile(r"e-?mail|poczt", re.IGNORECASE)),
    ("password", re.compile(r"pass(word|wd)?|has[łl]o", re.IGNORECASE)),
    ("first_name", re.compile(r"first.?name|given.?name|fname|\bimi[eę]\b", re.IGNORECASE)),
    ("last_name", re.compile(r"last.?name|family.?name|surname|lname|nazwisko", re.IGNORECASE)),
    ("full_name", re.compile(r"full.?name|\bname\b|imi[eę] i nazwisko", re.IGNORECASE)),
    ("username", re.compile(r"user.?name|\blogin\b|nick", re.IGNORECASE)),
)
# The agent registers only where these are all a form requires.
ACCOUNT_FIELDS = frozenset({"email", "password"})
# A site that asks for mail confirmation, in its page text.
CONFIRM_NOTICE = re.compile(
    r"potwierd[zź]\w* (?:\w+ )?(?:e-?mail|adres|konto|rejestracj)|aktywuj\w* konto|"
    r"link aktywacyjny|sprawd[zź] (?:swoj[aą] )?(?:skrzynk|e-?mail|poczt)|"
    r"(?:confirm|verify) your (?:e-?mail|account|address)|check your (?:e-?mail|inbox)|"
    r"activate your account",
    re.IGNORECASE,
)
CONFIRM_LINK = re.compile(r"confirm|verif|activat|potwierd|aktywuj|aktywac|weryfik", re.IGNORECASE)

# Never clicked or followed, whatever the page says.
NEVER_OPEN = re.compile(
    r"log.?out|wyloguj|sign.?out|delete|usu[nń]|zamknij konto|unsubscribe|wypisz|"
    r"download|pobierz|install|zainstaluj|admin|wp-login|phpmyadmin|\.env|\.git",
    re.IGNORECASE,
)
# Money controls are never clicked. From level 2 their links may be read, since writes stay closed.
MONEY = re.compile(
    r"\bpay\b|zap[łl]a[cć]|wp[łl]a[cć]|wyp[łl]a[cć]|deposit|withdraw|depozyt|"
    r"\bkup|\bbuy\b|\border\b|zam[oó]w|confirm.?payment",
    re.IGNORECASE,
)
CONTINUE = re.compile(
    r"dalej|kontynuuj|nast[eę]pn|sprawd[zź]|zacznij|rozpocznij|\bnext\b|continue|start|tak\b|yes\b",
    re.IGNORECASE,
)


def classify_field(*texts: str | None) -> str:
    """The kind of data a form field asks for, from its attributes and label."""
    text = " ".join(t for t in texts if t)
    for kind, rule in _FIELD_RULES:
        if rule.search(text):
            return kind
    return "other"


def is_dangerous(*texts: str | None) -> bool:
    """Never clicked: account-ending, download, admin and money controls."""
    text = " ".join(t for t in texts if t)
    return bool(NEVER_OPEN.search(text) or MONEY.search(text))


def may_follow_link(level: Level, *texts: str | None) -> bool:
    """A GET to a money page is allowed from level 2; the others never."""
    text = " ".join(t for t in texts if t)
    if NEVER_OPEN.search(text):
        return False
    return level >= Level.PAYMENT or not MONEY.search(text)


def is_bait(value: str, email: str = "", mail_domain: str = "") -> bool:
    """Our own bait address. It must never become evidence: it would link every probed site."""
    text = value.strip().lower()
    if email and text == email.strip().lower():
        return True
    domain = mail_domain.strip().lower()
    return bool(domain) and "@" in text and text.rpartition("@")[2] == domain


def site_key(url: str) -> str:
    """The registrable domain, or the host:port for loopback and IP addresses."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host in LOOPBACK or _is_ip(host):
        return parts.netloc.lower()
    return registrable_domain(host) or host


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def host_is_public(host: str) -> bool:
    """False for anything resolving to a private, loopback, link-local or reserved address."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    addresses = {info[4][0].split("%")[0] for info in infos}
    return bool(addresses) and all(ipaddress.ip_address(a).is_global for a in addresses)


def check_target(url: str, level: Level, *, live: bool, local: bool) -> None:
    """Refuse a start URL the policy does not cover."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ProbeRefused(f"not a web address: {url}")
    if local:
        if parts.hostname not in LOOPBACK:
            raise ProbeRefused("--local only works against this machine's fake site")
        return
    if not host_is_public(parts.hostname):
        raise ProbeRefused(f"{parts.hostname} is not a public address")
    if level >= Level.REGISTER and not live:
        raise ProbeRefused("levels 3-4 on a real site need --live")


def sandbox_problems(data_dir: Path, env=None, cwd: Path | None = None) -> list[str]:
    """Why this process must not open a real site. Empty inside the probe container."""
    env = os.environ if env is None else env
    problems = []
    if env.get("FTF_PROBE_SANDBOX") != "1":
        problems.append("not running in the probe container")
    problems += [f"{name} is visible" for name in SECRET_SETTINGS if env.get(name)]
    if ((cwd or Path.cwd()) / ".env").exists():
        problems.append(".env is visible")
    if (data_dir / "ftf.sqlite").exists():
        problems.append("the app database is visible")
    return problems


def require_sandbox(data_dir: Path) -> None:
    if problems := sandbox_problems(data_dir):
        raise ProbeRefused(
            "real sites run only in the sandbox: docker compose --profile probe run --rm probe "
            f"<url> ({'; '.join(problems)})"
        )
