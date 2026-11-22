"""Disposable mailboxes on mail.tm for victim runs (VV-30), and reading what arrives (VV-33).

With `"provider": "mail.tm"` in a contacts scope (docs/victim-contacts.md), each run creates
its own inbox, `<persona>.<run-id>@<a mail.tm domain>`, before the browser opens. The address
and password are kept in `run_files/mailbox.json`, so `ftf victim mail <run folder>` can read
the inbox later: each message is saved under `run_files/mail/`, and one line per message goes
to `run_files/mail.jsonl` with the sender, its domain, the links and phone numbers in it.

API: https://api.mail.tm (accounts, token, messages). No key is needed; it allows about eight
requests a second, so calls are spaced.
"""

from __future__ import annotations

import json
import re
import secrets
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .record import RUN_FILES, now_iso

DEFAULT_API = "https://api.mail.tm"
MIN_INTERVAL_S = 0.15  # mail.tm allows about 8 requests a second
MAX_PAGES = 10
_URL = re.compile(r"https?://[^\s\"'<>)\]]+", re.IGNORECASE)
_PHONE = re.compile(r"(?<!\d)(?:\+?48[\s-]?)?\d{3}[\s-]?\d{3}[\s-]?\d{3}(?!\d)")

# (method, url, headers, body) -> (status, body)
Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]


class MailboxError(Exception):
    pass


def _urllib(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise MailboxError(f"mail.tm unreachable: {exc}") from exc


@dataclass
class Mailbox:
    provider: str
    api: str
    address: str
    password: str
    account_id: str
    created_at: str

    def save(self, run_files: Path) -> Path:
        path = run_files / "mailbox.json"
        path.write_text(json.dumps(asdict(self), indent=1), encoding="utf-8")
        return path

    @classmethod
    def load(cls, run_files: Path) -> Mailbox:
        return cls(**json.loads((run_files / "mailbox.json").read_text(encoding="utf-8")))


def _members(data: Any) -> list[dict[str, Any]]:
    """mail.tm answers a plain list for `Accept: application/json`, or a Hydra collection."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("hydra:member", [])
    return []


class MailTm:
    def __init__(self, api: str = DEFAULT_API, transport: Transport | None = None):
        self.api = api.rstrip("/")
        self.transport = transport or _urllib
        self._last = 0.0

    def _call(
        self, method: str, path: str, body: dict | None = None, token: str | None = None
    ) -> Any:
        wait = MIN_INTERVAL_S - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        headers = {"Accept": "application/json", "User-Agent": "ftf-victim/0.1"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(3):
            status, raw = self.transport(method, f"{self.api}{path}", headers, data)
            self._last = time.monotonic()
            if status == 429 and attempt < 2:
                time.sleep(1.0 + attempt)
                continue
            break
        if status >= 400:
            detail = raw.decode("utf-8", "replace")[:300]
            raise MailboxError(f"mail.tm {method} {path}: HTTP {status} {detail}")
        return json.loads(raw) if raw else None

    def domains(self) -> list[str]:
        return [
            d["domain"]
            for d in _members(self._call("GET", "/domains"))
            if d.get("isActive", True) and not d.get("isPrivate", False)
        ]

    def create(self, local_part: str, domain: str = "") -> Mailbox:
        """A new inbox. `domain` must be one mail.tm offers; empty takes the first active one."""
        offered = self.domains()
        if not offered:
            raise MailboxError("mail.tm offers no active domain")
        if domain and domain not in offered:
            raise MailboxError(f"mail.tm does not offer {domain!r} (offers {offered})")
        address = f"{local_part}@{domain or offered[0]}".lower()
        password = secrets.token_urlsafe(18)
        account = self._call("POST", "/accounts", {"address": address, "password": password})
        return Mailbox(
            "mail.tm",
            self.api,
            account.get("address", address),
            password,
            str(account.get("id", "")),
            now_iso(),
        )

    def token(self, mailbox: Mailbox) -> str:
        answer = self._call(
            "POST", "/token", {"address": mailbox.address, "password": mailbox.password}
        )
        return answer["token"]

    def messages(self, mailbox: Mailbox, token: str | None = None) -> list[dict[str, Any]]:
        """Every message, full (sender, subject, text, html), oldest first."""
        token = token or self.token(mailbox)
        listed: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            batch = _members(self._call("GET", f"/messages?page={page}", token=token))
            listed += batch
            if len(batch) < 30:  # mail.tm pages hold 30
                break
        full = [self._call("GET", f"/messages/{m['id']}", token=token) for m in listed]
        return sorted(full, key=lambda m: m.get("createdAt", ""))


def summarise(message: dict[str, Any], address: str) -> dict[str, Any]:
    """One line for mail.jsonl: who sent it, from which domain, and what it points to."""
    sender = (message.get("from") or {}).get("address", "")
    html = message.get("html") or []
    html_text = " ".join(html) if isinstance(html, list) else str(html)
    body = f"{message.get('text') or ''} {unescape(html_text)}"
    links = sorted({u.rstrip(".,;") for u in _URL.findall(body)})
    return {
        "id": message.get("id"),
        "received_at": message.get("createdAt"),
        "to": address,
        "from": sender,
        "from_name": (message.get("from") or {}).get("name", ""),
        "sender_domain": sender.rsplit("@", 1)[-1].lower() if "@" in sender else "",
        "subject": message.get("subject", ""),
        "links": links,
        "link_hosts": sorted({(urlsplit(u).hostname or "").lower() for u in links} - {""}),
        "phones": sorted({re.sub(r"\D", "", p)[-9:] for p in _PHONE.findall(body)}),
        "file": f"{RUN_FILES}/mail/{message.get('id')}.json",
    }


def fetch_mail(root: Path, client: MailTm | None = None) -> list[dict[str, Any]]:
    """Read the run's inbox and record the messages not seen before. Returns the new ones."""
    files = Path(root) / RUN_FILES
    mailbox = Mailbox.load(files)
    client = client or MailTm(mailbox.api)
    out_dir = files / "mail"
    out_dir.mkdir(exist_ok=True)
    seen = {p.stem for p in out_dir.glob("*.json")}
    new = []
    for message in client.messages(mailbox):
        if str(message.get("id")) in seen:
            continue
        (out_dir / f"{message['id']}.json").write_text(
            json.dumps(message, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        line = summarise(message, mailbox.address)
        with open(files / "mail.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"fetched_at": now_iso(), **line}, ensure_ascii=False) + "\n")
        new.append(line)
    return new
