"""Which hosts a capture may reach: public internet only, never the server's own networks.

The interface lets anyone type a URL that the server's browser then opens, so a capture must
not reach loopback, private ranges, link-local (cloud metadata at 169.254.169.254) or other
reserved addresses. A host is checked by every address it resolves to. A name that does not
resolve is let through here: the browser will fail on it too, and a dead domain is evidence.

This is the early check that gives the analyst a plain message. The browser itself is held
to public addresses by `gate.EgressGate`, which also covers redirects and DNS rebinding.
"""

from __future__ import annotations

import ipaddress
import socket

_NAT64 = ipaddress.ip_network("64:ff9b::/96")

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def _address(text: str) -> IPAddress | None:
    try:
        return ipaddress.ip_address(text.split("%", 1)[0].strip("[]"))
    except ValueError:
        return None


def _embedded(ip: IPAddress) -> list[IPAddress]:
    """The address itself and any IPv4 address it carries (mapped, 6to4, NAT64, Teredo)."""
    found: list[IPAddress] = [ip]
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            found.append(ip.ipv4_mapped)
        if ip.sixtofour is not None:
            found.append(ip.sixtofour)
        if ip.teredo is not None:
            found.extend(ip.teredo)
        if ip in _NAT64:
            found.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    return found


def is_public(text: str) -> bool:
    """True for an address on the public internet, and for nothing that carries a private one."""
    ip = _address(text)
    if ip is None:
        return False
    return all(a.is_global and not a.is_multicast for a in _embedded(ip))


def resolve(host: str) -> list[str]:
    """Every address `host` stands for or resolves to; empty when it does not resolve."""
    if _address(host) is not None:
        return [host.strip("[]")]
    try:
        infos = socket.getaddrinfo(host.rstrip("."), None, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        return []
    return sorted({str(info[4][0]) for info in infos})


def blocked_address(host: str | None) -> str | None:
    """The first non-public address `host` stands for or resolves to, else None."""
    if not host:
        return None
    for text in resolve(host):
        if not is_public(text):
            return text.split("%", 1)[0]
    return None
