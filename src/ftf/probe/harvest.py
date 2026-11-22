"""Identifiers a probed page reveals. Pure functions over HTML and URLs."""

from __future__ import annotations

import hashlib
import html as html_lib
import re
from urllib.parse import urlsplit

from ..extract.conversion import IBAN, iban_valid

_HIDDEN = re.compile(r"<(script|style)\b.*?</\1\s*>|<!--.*?-->", re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"

_IBAN_START = re.compile(r"\b[A-Z]{2}\d{2}")
_ETH = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
_BTC_B58 = re.compile(r"\b[13][1-9A-HJ-NP-Za-km-z]{25,34}\b")
_BTC_BECH32 = re.compile(r"\bbc1[ac-hj-np-z02-9]{11,71}\b")
_TRON = re.compile(r"\bT[1-9A-HJ-NP-Za-km-z]{33}\b")
_EMAIL = re.compile(r"\b[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+\b")
_TELEGRAM = re.compile(r"(?:t\.me|telegram\.me)/([A-Za-z][\w]{3,31})\b")
_WHATSAPP = re.compile(r"wa\.me/\+?(\d{7,15})\b")
_NIP = re.compile(r"\bNIP\b[:\s]*((?:\d[\s-]?){10})(?!\d)", re.IGNORECASE)
_KRS = re.compile(r"\bKRS\b[:\s]*(\d{10})\b", re.IGNORECASE)
_STRIPE = re.compile(r"\bpk_live_[A-Za-z0-9]{16,}\b")
_MERCHANT = re.compile(
    r"""\b(?:p24_)?(?:merchant(?:_?id)?|pos_?id|shop_?id)["']?\s*[=:]\s*["']?([\w-]{3,64})""",
    re.IGNORECASE,
)
_URLS = re.compile(r"""https?://[^\s"'<>\\)]+""")

PROVIDERS = {
    "stripe.com": "stripe",
    "paypal.com": "paypal",
    "przelewy24.pl": "przelewy24",
    "payu.com": "payu",
    "tpay.com": "tpay",
    "nowpayments.io": "nowpayments",
    "coinbase.com": "coinbase",
    "checkout.com": "checkout.com",
    "adyen.com": "adyen",
    "revolut.com": "revolut",
}


def _b58_valid(text: str, versions: set[int]) -> bool:
    number = 0
    for ch in text:
        number = number * 58 + _B58.index(ch)
    pad = len(text) - len(text.lstrip("1"))
    try:
        raw = b"\0" * pad + number.to_bytes((number.bit_length() + 7) // 8, "big")
    except OverflowError:
        return False
    if len(raw) != 25 or raw[0] not in versions:
        return False
    return hashlib.sha256(hashlib.sha256(raw[:-4]).digest()).digest()[:4] == raw[-4:]


def _bech32_valid(text: str) -> bool:
    generators = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    hrp, data = text[:2], text[3:]
    values = [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]
    values += [_BECH32.index(c) for c in data]
    check = 1
    for value in values:
        top = check >> 25
        check = (check & 0x1FFFFFF) << 5 ^ value
        for i, gen in enumerate(generators):
            if (top >> i) & 1:
                check ^= gen
    return check in (1, 0x2BC830A3)  # bech32, bech32m


def nip_valid(digits: str) -> bool:
    if len(digits) != 10 or not digits.isdigit():
        return False
    weights = (6, 5, 7, 2, 3, 4, 5, 6, 7)
    return sum(int(d) * w for d, w in zip(digits, weights, strict=False)) % 11 == int(digits[9])


def _ibans(text: str) -> list[str]:
    """Valid IBANs. A match may run into the next word, so it is shortened until it validates."""
    out = []
    for start in _IBAN_START.finditer(text):
        match = IBAN.match(text, start.start())
        if not match:
            continue
        tokens = match.group(1).split(" ")
        for end in range(len(tokens), 0, -1):
            value = "".join(tokens[:end])
            if len(value) >= 15 and iban_valid(value):
                out.append(value)
                break
    return out


def visible_text(html: str) -> str:
    return html_lib.unescape(_TAGS.sub(" ", _HIDDEN.sub(" ", html or "")))


def harvest(html: str, url: str, frame_urls: list[str] | None = None) -> list[dict]:
    """Findings on one page: `{"signal", "ns", "value", "where"}`, deduplicated."""
    text = visible_text(html)
    urls = [url, *(frame_urls or []), *_URLS.findall(html or "")]
    found: dict[tuple[str, str], dict] = {}

    def add(signal: str, ns: str, value: str) -> None:
        found.setdefault((ns, value), {"signal": signal, "ns": ns, "value": value, "where": url})

    for value in _ibans(text):
        add("A12", "contact.iban", value)
    for value in _ETH.findall(text):
        add("A12", "contact.wallet", value.lower())
    for value in _BTC_B58.findall(text):
        if _b58_valid(value, {0, 5}):
            add("A12", "contact.wallet", value)
    for value in _BTC_BECH32.findall(text):
        if _bech32_valid(value):
            add("A12", "contact.wallet", value)
    for value in _TRON.findall(text):
        if _b58_valid(value, {0x41}):
            add("A12", "contact.wallet", value)
    for value in _EMAIL.findall(text):
        add("A12", "contact.email", value.lower())
    for value in _TELEGRAM.findall(html or ""):
        add("A12", "contact.telegram", value.lower())
    for value in _WHATSAPP.findall(html or ""):
        add("A12", "contact.whatsapp", value)
    for raw in _NIP.findall(text):
        digits = re.sub(r"\D", "", raw)
        if nip_valid(digits):
            add("P1", "company.nip", digits)
    for value in _KRS.findall(text):
        add("P1", "company.krs", value)
    for value in _STRIPE.findall(html or ""):
        add("I6", "payment.public_key", value)
    for value in _MERCHANT.findall(html or ""):
        add("I6", "payment.merchant_id", value)
    for candidate in urls:
        host = (urlsplit(candidate).hostname or "").lower()
        for domain, provider in PROVIDERS.items():
            if host == domain or host.endswith(f".{domain}"):
                add("I6", "payment.provider", provider)
    return list(found.values())
