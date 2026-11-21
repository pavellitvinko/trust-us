"""Replace secrets with their hash before anything reaches a model or the shipped cache.

Equal secrets get equal hashes, so two pages sharing a token can still be linked. Only values
under secret-like names are touched: ad pixel IDs and other linkage identifiers stay readable.
Text only: a secret visible inside a screenshot or a video is not removed.

Input is hostile web content, so every pattern uses possessive quantifiers and bounded lengths
to stay linear in the size of the page.
"""

from __future__ import annotations

import hashlib
import re
from itertools import pairwise
from typing import Any

_SECRET_PARTS = frozenset(
    {
        "token",
        "tokens",
        "jwt",
        "apikey",
        "accesskey",
        "privatekey",
        "secretkey",
        "secret",
        "secrets",
        "clientsecret",
        "session",
        "sessions",
        "sessionid",
        "sid",
        "phpsessid",
        "jsessionid",
        "cookie",
        "cookies",
        "csrf",
        "xsrf",
        "auth",
        "authorization",
        "password",
        "passwd",
        "passphrase",
        "pass",
        "pwd",
    }
)
_SECRET_SUFFIXES = ("token", "password")  # refreshtoken, idtoken, userpassword
_SECRET_PAIRS = frozenset(
    {("api", "key"), ("access", "key"), ("private", "key"), ("client", "secret")}
)

_MAX_TAG = 32_768
_KEY = r"[A-Za-z_$][\w$.\-]{0,127}+(?:\[[\w$.\-]{0,64}+\])?+"

_HEADER_LINE = re.compile(r"(?m)^([ \t]*+([A-Za-z][\w-]{0,63}+)[ \t]*+:[ \t]*+)([^\r\n]*+)")
_INLINE_HEADER = re.compile(
    r"(?i)(?<![\w-])((?:proxy-authorization|authorization|set-cookie|cookie)[ \t]*+:[ \t]*+)"
    r"""([^\r\n'"]++)"""
)
# A header inside a quoted curl argument or after a verbose-log `> ` marker.
_MARKED_HEADER = re.compile(
    r"""(?:(?<=['"])|(?<=[<>] )|(?<=[<>]\t))"""
    r"""(([A-Za-z][\w-]{0,63}+)[ \t]*+:[ \t]*+)([^\r\n'"<]++)"""
)
_BEARER = re.compile(r"(?i)\b(bearer[ \t]++)([A-Za-z0-9._~+/=-]{8,4096}+)")
_URL_PASSWORD = re.compile(
    r"(?i)\b([a-z][a-z0-9+.-]{0,31}+://[^/\s:@?#]{1,256}+:)([^/\s@?#]{1,256}+)@"
)
_TAG_OPEN = re.compile(r"<[A-Za-z][\w:-]{0,63}+")
_TAG_GAP = re.compile(r"[\s/]*+")
_ATTR = re.compile(
    r"""([^\s=<>"'/]{1,128}+)"""
    r"""(?:\s*+=\s*+(?:"([^"]*+)"|'([^']*+)'|([^\s"'<>]{1,8192}+)))?+"""
)
_STORAGE_CALL = re.compile(
    r"""(?i)\b(?:setItem|setRequestHeader)\(\s*+(["'])([\w$.\-]{1,128}+)\1\s*+,\s*+(["'])"""
    r"""((?:(?!\3)[^\\\r\n]|\\.){1,4096}+)\3"""
)
_ENCODED_PAIR = re.compile(
    r"""(?i)(%3F|%26)([\w.\-]{1,128}+)%3D((?:(?!%26)[^&\s"'<>;#]){1,4096}+)"""
)
_KEY_SEPARATOR = re.compile(
    rf"""(?<![\w$.\-])(?P<key>\\"{_KEY}\\"|"{_KEY}"|'{_KEY}'|{_KEY})"""
    r"""(?P<sep>\]?+[ \t]{0,8}+(?:=>|:=|:|=)[ \t]{0,8}+)"""
)
_VALUE = re.compile(
    r"""\\"(?:(?!\\").){0,4096}?\\\""""
    r"""|"(?:[^"\\\r\n]++|\\.)*+\""""
    r"""|'(?:[^'\\\r\n]++|\\.)*+'"""
    r"""|\[[^\]\r\n]{0,4096}+\]"""
    r"""|(?<==)[^&\s"'<>;#]{1,4096}+"""  # bare only right after `=`, so `https:` is not a pair
    r"""|-?\d[\d.]{0,63}+(?![\w.-])"""
)


def is_secret_name(name: str) -> bool:
    """True when a whole part of the name is secret-like: `auth_token` yes, `author` no."""
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", spaced).lower()
    parts = [p.rstrip("0123456789") for p in re.split(r"[^a-z0-9]+", spaced) if p]
    return any(p in _SECRET_PARTS or p.endswith(_SECRET_SUFFIXES) for p in parts) or any(
        pair in _SECRET_PAIRS for pair in pairwise(parts)
    )


def hash_secret(value: str) -> str:
    if value.startswith("[REDACTED:sha256:"):
        return value
    digest = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:12]
    return f"[REDACTED:sha256:{digest}]"


def _redact_trimmed(value: str) -> str:
    """Hash a header value without its trailing spaces, so equal values hash equally."""
    trimmed = value.rstrip(" \t")
    return hash_secret(trimmed) + value[len(trimmed) :] if trimmed else value


def _redact_header_line(match: re.Match[str]) -> str:
    prefix, name, value = match.groups()
    # A quoted value is a JS or JSON pair, handled with its quotes by the assignment pass.
    if not is_secret_name(name) or value[:1] in {'"', "'", "\\"}:
        return match.group(0)
    return prefix + _redact_trimmed(value)


def _redact_marked_header(match: re.Match[str]) -> str:
    prefix, name, value = match.groups()
    return prefix + _redact_trimmed(value) if is_secret_name(name) else match.group(0)


def _scan_tag(text: str, start: int) -> tuple[int, list[tuple[str, int, int]]]:
    """Return where scanning stopped and the attributes `(name, value_start, value_end)`.

    Attributes found before a malformed or oversized part are kept, so they are still redacted.
    """
    opened = _TAG_OPEN.match(text, start)
    if opened is None:
        return start + 1, []
    pos = opened.end()
    attrs: list[tuple[str, int, int]] = []
    while pos - start <= _MAX_TAG:
        pos = _TAG_GAP.match(text, pos).end()
        if pos >= len(text) or text[pos] == "<":
            break
        if text[pos] == ">":
            return pos + 1, attrs
        attr = _ATTR.match(text, pos)
        if attr is None:
            pos += 1
            continue
        group = next((g for g in (2, 3, 4) if attr.group(g) is not None), None)
        if group is not None:
            attrs.append((attr.group(1).lower(), *attr.span(group)))
        pos = attr.end()
    return pos, attrs


def _secret_spans(text: str, attrs: list[tuple[str, int, int]]) -> list[tuple[int, int]]:
    field_is_secret = any(
        name in {"name", "id", "type", "property", "http-equiv"} and is_secret_name(text[a:b])
        for name, a, b in attrs
    )
    return [
        (a, b)
        for name, a, b in attrs
        if b > a and (is_secret_name(name) or (field_is_secret and name in {"value", "content"}))
    ]


def _redact_tags(text: str) -> str:
    """Redact secret attributes and the values of secret-named inputs and meta tags."""
    out: list[str] = []
    last = pos = 0
    while (start := text.find("<", pos)) != -1:
        stopped, attrs = _scan_tag(text, start)
        pos = max(stopped, start + 1)
        for a, b in sorted(_secret_spans(text, attrs)):
            out += [text[last:a], hash_secret(text[a:b])]
            last = b
    out.append(text[last:])
    return "".join(out)


def _redact_storage_call(match: re.Match[str]) -> str:
    if not is_secret_name(match.group(2)):
        return match.group(0)
    head = match.group(0)[: match.start(4) - match.start()]
    return f"{head}{hash_secret(match.group(4))}{match.group(3)}"


def _redact_encoded_pair(match: re.Match[str]) -> str:
    opener, key, value = match.groups()
    return f"{opener}{key}%3D{hash_secret(value)}" if is_secret_name(key) else match.group(0)


def _hash_value(raw: str, after_equals: bool) -> str | None:
    if raw.startswith("[REDACTED:"):
        return None
    if raw.startswith('\\"'):
        quote = '\\"'
        inner = raw[2:-2]
    elif raw[0] in "\"'":
        quote = raw[0]
        inner = raw[1:-1]
    else:  # a bare value, an array or a number; a JSON value stays valid as a string
        quote = "" if after_equals else '"'
        inner = raw
    return f"{quote}{hash_secret(inner)}{quote}" if inner else None


def _redact_assignments(text: str) -> str:
    """Only secret keys read a value, so secrets nested in `href=...` or `url: ...` are found."""
    out: list[str] = []
    last = 0
    for pair in _KEY_SEPARATOR.finditer(text):
        if pair.start() < last or not is_secret_name(pair.group("key").strip("\\\"'")):
            continue
        value = _VALUE.match(text, pair.end())
        if value is None:
            continue
        replaced = _hash_value(value.group(0), text[value.start() - 1] == "=")
        if replaced is not None:
            out += [text[last : value.start()], replaced]
            last = value.end()
    out.append(text[last:])
    return "".join(out)


def redact_text(text: str) -> str:
    """Redact headers, tokens, URL passwords, HTML attributes, and key-value pairs."""
    text = text.encode("utf-8", "replace").decode("utf-8")  # drops lone surrogates
    text = _HEADER_LINE.sub(_redact_header_line, text)
    text = _INLINE_HEADER.sub(lambda m: m.group(1) + _redact_trimmed(m.group(2)), text)
    text = _MARKED_HEADER.sub(_redact_marked_header, text)
    text = _BEARER.sub(lambda m: m.group(1) + hash_secret(m.group(2)), text)
    text = _URL_PASSWORD.sub(lambda m: f"{m.group(1)}{hash_secret(m.group(2))}@", text)
    text = _redact_tags(text)
    text = _STORAGE_CALL.sub(_redact_storage_call, text)
    text = _ENCODED_PAIR.sub(_redact_encoded_pair, text)
    return _redact_assignments(text)


def _redact_key(key: Any) -> Any:
    return redact_text(key) if isinstance(key, str) else key


def _hash_leaves(value: Any) -> Any:
    if isinstance(value, dict):
        return {_redact_key(k): _hash_leaves(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_hash_leaves(v) for v in value]
    if value is None or isinstance(value, bool):
        return value
    return hash_secret(str(value))


def redact_json(value: Any) -> Any:
    """Redact a JSON-like value, including HAR-style `{"name": ..., "value": ...}` entries."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list | tuple):
        return [redact_json(v) for v in value]
    if not isinstance(value, dict):
        return value
    named_secret = isinstance(value.get("name"), str) and is_secret_name(value["name"])
    out = {}
    for key, item in value.items():
        if is_secret_name(str(key)) or (named_secret and key == "value"):
            out[_redact_key(key)] = _hash_leaves(item)
        else:
            out[_redact_key(key)] = redact_json(item)
    return out
