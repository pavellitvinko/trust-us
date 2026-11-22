"""Level 4: read the bait mailbox and attribute each message to the site given that identity.

The mailbox is opened read-only. Links and attachments in messages are never opened.
A sender domain becomes linkage evidence only when the receiving server verified DKIM.
"""

from __future__ import annotations

import email
import email.policy
import imaplib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import parseaddr
from pathlib import Path

from ..extract._material import registrable_domain
from .persona import Ledger

# Shared senders say nothing about who runs a site.
SHARED_SENDERS = frozenset(
    {
        "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com",
        "proton.me", "protonmail.com", "interia.pl", "interia.eu", "wp.pl", "o2.pl", "onet.pl",
        "sendgrid.net", "mailgun.org", "amazonses.com", "mailchimp.com", "mandrillapp.com",
        "sparkpostmail.com", "postmarkapp.com", "brevo.com", "sendinblue.com",
    }
)  # fmt: skip
_DKIM_PASS = re.compile(r"\bdkim=pass\b[^;]*?header\.(?:d|i)=@?([\w.-]+)", re.IGNORECASE)


def _body_text(message) -> str:
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        return part.get_content()
    except (LookupError, ValueError):
        return ""


def parse_message(raw: bytes, entries: list[dict]) -> dict:
    """Facts of one message, and the site it belongs to if its bait name says so."""
    message = email.message_from_bytes(raw, policy=email.policy.default)
    sender = parseaddr(str(message.get("From", "")))[1].lower()
    sender_domain = registrable_domain(sender.rpartition("@")[2]) if "@" in sender else ""
    auth = " ".join(str(h) for h in message.get_all("Authentication-Results", []))
    verified = {registrable_domain(d.lower()) for d in _DKIM_PASS.findall(auth)}
    haystack = f"{message.get('To', '')} {message.get('Subject', '')} {_body_text(message)}"
    named = [e for e in entries if e.get("name") and e["name"].casefold() in haystack.casefold()]
    entry = named[0] if len(named) == 1 else None
    site = entry["site"] if entry else None
    return {
        "site": site,
        "bundle": entry.get("bundle") if entry else None,
        "name": entry.get("name") if entry else None,
        "sender_domain": sender_domain,
        "dkim_verified": sender_domain in verified,
        "subject": str(message.get("Subject", ""))[:160],
        "date": str(message.get("Date", "")),
        "resold": bool(site and sender_domain and sender_domain != site
                       and sender_domain in verified and sender_domain not in SHARED_SENDERS),
    }  # fmt: skip


def record_messages(parsed: list[dict]) -> int:
    """Add attributed messages to their site's probe.json. Returns how many were new."""
    added = 0
    by_bundle: dict[str, list[dict]] = {}
    for item in parsed:
        if item.get("bundle"):
            by_bundle.setdefault(item["bundle"], []).append(item)
    for bundle, items in by_bundle.items():
        path = Path(bundle) / "probe.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        known = {(m["sender_domain"], m["subject"], m["date"]) for m in data.get("mail", [])}
        have = {(f["ns"], f["value"]) for f in data.get("findings", [])}
        for item in items:
            key = (item["sender_domain"], item["subject"], item["date"])
            if key in known:
                continue
            known.add(key)
            added += 1
            data.setdefault("mail", []).append({k: v for k, v in item.items() if k != "bundle"})
            domain = item["sender_domain"]
            usable = item["dkim_verified"] and domain and domain not in SHARED_SENDERS
            if usable and ("mail.sender_domain", domain) not in have:
                have.add(("mail.sender_domain", domain))
                data.setdefault("findings", []).append(
                    {
                        "signal": "P2",
                        "ns": "mail.sender_domain",
                        "value": domain,
                        "where": "mailbox",
                    }
                )
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return added


def fetch_mailbox(user: str, password: str, host: str, port: int = 993) -> list[bytes]:
    with imaplib.IMAP4_SSL(host, port, timeout=30) as imap:
        imap.login(user, password)
        status, _ = imap.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError("could not open INBOX")
        _, ids = imap.search(None, "ALL")
        out = []
        for number in (ids[0] or b"").split():
            _, data = imap.fetch(number, "(BODY.PEEK[])")  # PEEK leaves messages unread
            out.extend(part[1] for part in data if isinstance(part, tuple))
        return out


def watch(user: str, password: str, host: str, ledger_path: Path, port: int = 993) -> dict:
    entries = Ledger(ledger_path).resolved()
    parsed = [parse_message(raw, entries) for raw in fetch_mailbox(user, password, host, port)]
    attributed = [p for p in parsed if p["site"]]
    return {
        "messages": len(parsed),
        "attributed": len(attributed),
        "new": record_messages(attributed),
        "resold": [p for p in attributed if p["resold"]],
    }


RESEND_API = "https://api.resend.com"


def _resend_get(api_key: str, path: str) -> dict:
    request = urllib.request.Request(
        f"{RESEND_API}{path}",
        headers={"Authorization": f"Bearer {api_key}", "User-Agent": "ftf-probe"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _addresses(*values) -> set[str]:
    found = set()
    for value in values:
        items = value if isinstance(value, list) else [value]
        for item in items:
            if item:
                found.add(parseaddr(str(item))[1].lower())
    return found - {""}


def parse_received(detail: dict, entries: list[dict]) -> dict:
    """Facts of one Resend received email, attributed by the exact bait address."""
    recipients = _addresses(
        detail.get("to"), detail.get("cc"), detail.get("bcc"), detail.get("received_for")
    )
    matched = [e for e in entries if str(e.get("email", "")).lower() in recipients]
    entry = matched[0] if len(matched) == 1 else None
    site = entry["site"] if entry else None
    sender = parseaddr(str(detail.get("from", "")))[1].lower()
    sender_domain = registrable_domain(sender.rpartition("@")[2]) if "@" in sender else ""
    # Resend reports "gray" for a signature from another domain, so "pass" means the From domain.
    verified = (detail.get("authentication") or {}).get("dkim") == "pass"
    return {
        "site": site,
        "bundle": entry.get("bundle") if entry else None,
        "name": entry.get("name") if entry else None,
        "sender_domain": sender_domain,
        "dkim_verified": verified,
        "subject": str(detail.get("subject") or "")[:160],
        "date": str(detail.get("created_at") or ""),
        "resold": bool(
            site
            and sender_domain
            and verified
            and sender_domain != site
            and sender_domain not in SHARED_SENDERS
        ),
    }


def watch_resend(api_key: str, ledger_path: Path, *, get=_resend_get, pause: float = 0.6) -> dict:
    """Read received mail through Resend. Only messages to our ledger addresses are opened."""
    entries = Ledger(ledger_path).resolved()
    ours = {str(e["email"]).lower() for e in entries if e.get("email")}
    total, parsed, after = 0, [], ""
    while True:
        query = urllib.parse.urlencode({"limit": 100, **({"after": after} if after else {})})
        page = get(api_key, f"/emails/receiving?{query}")
        items = page.get("data") or []
        total += len(items)
        for item in items:
            if not ours & _addresses(item.get("to"), item.get("cc"), item.get("bcc")):
                continue
            time.sleep(pause)
            message_id = urllib.parse.quote(str(item["id"]), safe="")
            parsed.append(parse_received(get(api_key, f"/emails/receiving/{message_id}"), entries))
        if not page.get("has_more") or not items:
            break
        after = str(items[-1]["id"])
        time.sleep(pause)
    attributed = [p for p in parsed if p["site"]]
    return {
        "messages": total,
        "attributed": len(attributed),
        "new": record_messages(attributed),
        "resold": [p for p in attributed if p["resold"]],
    }


def check_leaks(address: str, api_key: str) -> list[str]:
    """Breach names listing the bait address (Have I Been Pwned). Never downloads breach data."""
    url = (
        "https://haveibeenpwned.com/api/v3/breachedaccount/"
        f"{urllib.parse.quote(address, safe='')}?truncateResponse=true"
    )
    request = urllib.request.Request(
        url, headers={"hibp-api-key": api_key, "user-agent": "ftf-probe"}
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return [b["Name"] for b in json.load(response)]
    except urllib.error.HTTPError as exc:
        if exc.code == 404:  # the API's answer for "not in any breach"
            return []
        raise
