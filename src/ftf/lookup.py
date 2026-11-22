"""A bare identifier as a starting point.

A web address or a bare domain starts a capture; anything else (a pixel ID, an ad-library ID,
a product name) is looked up in the index of processed material and returns the views that
carry it. Only scalar values can be typed; structured ones (`page.entity.person`) are not
found this way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .contracts import State
from .extract._material import _tld, registrable_domain
from .store import Store

# Dot-separated host labels, optionally with a port and a path.
_HOST = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}\.?(?::\d{1,5})?(?:[/?#].*)?$",
    re.IGNORECASE,
)
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


def host_of(text: str) -> str:
    """The lower-case host of a URL or bare domain, without a leading `www.`."""
    text = text.strip()
    host = urlsplit(text if "//" in text else f"//{text}").hostname or ""
    return host.lower().rstrip(".").removeprefix("www.")


def is_address(text: str) -> bool:
    """True for something to open in a browser: a URL with a scheme, or a bare host whose
    suffix is on the public-suffix list. `Bitcoin.Era` or `node.js` are names, not hosts."""
    text = text.strip()
    if not text or any(c.isspace() for c in text):
        return False
    if _SCHEME.match(text):
        return True
    return bool(_HOST.match(text)) and bool(_tld(host_of(text)).suffix)


def registrable(text: str) -> str:
    """The registrable domain of a URL or bare host, as `domain.registrable` stores it."""
    return registrable_domain(host_of(text))


@dataclass
class Hit:
    """One view carrying the value, with the namespaces it was seen under."""

    view_id: str
    incident_id: str
    label: str
    namespaces: list[str] = field(default_factory=list)
    exact: bool = True


def lookup(store: Store, value: str, namespaces: list[str] | None = None) -> list[Hit]:
    """Views whose observations carry `value`. Exact match first; when nothing matches, letter
    case is ignored. Observations recorded as absent or unobservable never count."""
    value = value.strip()
    if not value:
        return []
    for fold_case in (False, True):
        found = [
            o
            for o in store.observations_with_value(value, namespaces, fold_case=fold_case)
            if o.state == State.PRESENT
        ]
        if found:
            break
    hits: dict[str, Hit] = {}
    for o in found:
        hit = hits.get(o.view_id)
        if hit is None:
            try:
                view = store.get_view(o.view_id)
                incident, label = view.incident_id, view.label
            except KeyError:  # an observation without its view (fixtures, partial loads)
                incident, label = o.view_id.partition(":")[0], ""
            hit = hits[o.view_id] = Hit(o.view_id, incident, label, exact=not fold_case)
        if o.namespace not in hit.namespaces:
            hit.namespaces.append(o.namespace)
    return sorted(hits.values(), key=lambda h: (h.incident_id, h.view_id))
