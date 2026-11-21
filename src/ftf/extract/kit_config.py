"""Tracker-kit fingerprints from the config object and forms (signals B3, B4, B8).

B3 is the set of key paths in `window.aio`, never its values. The `visit.*` and
`session.*` subtrees describe the capturing analyst, not the kit, so they are skipped.
"""

from __future__ import annotations

import re
from typing import Any

from ..contracts import Observation, Purpose, View
from ..registry import Context, extractors
from . import _material as m
from .kit_url import value_shape

MAX_KEY_PATHS = 1000
_SKIP_ROOTS = frozenset({"visit", "session"})
_VARIABLE_KEY = re.compile(
    r"^(?:\d+|[0-9a-f]{8,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
    re.IGNORECASE,
)
_TOKEN_SHAPE = re.compile(r"^(?:hex|alnum|lower|upper|b64)(\d+)$")

# Names that can carry a declared traffic source or cloaker.
_NAME_PATHS = ("source.name", "campaign.name", "flow.name", "landing.name")
_VIA = re.compile(r"\bvia\s+([A-Za-z][\w\-]{2,40})", re.IGNORECASE)
KNOWN_KITS = frozenset({
    "cloakerson", "keitaro", "binom", "adspect", "magicchecker", "trafficarmor",
    "palladium", "imklo", "irax", "cloakit", "hoaxtech", "redtrack", "voluum",
})  # fmt: skip
_WORD = re.compile(r"[A-Za-z][A-Za-z\-]+")


def key_paths(config: Any, prefix: str = "") -> list[str]:
    """Every leaf key path; list items collapse to `[]`, ID-like keys to `*`."""
    out: list[str] = []

    def walk(node: Any, path: str) -> None:
        if len(out) >= MAX_KEY_PATHS:
            return
        if isinstance(node, dict) and node:
            for key, child in node.items():
                k = "*" if _VARIABLE_KEY.match(str(key)) else str(key)
                if not path and k in _SKIP_ROOTS:
                    continue
                walk(child, f"{path}.{k}" if path else k)
        elif isinstance(node, list) and node:
            for child in node:
                walk(child, f"{path}[]")
        elif path:
            out.append(path)

    walk(config, prefix)
    return sorted(set(out))


def _is_token(name: str) -> bool:
    shape = value_shape(name)
    if shape == "uuid":
        return True
    found = _TOKEN_SHAPE.match(shape)
    return bool(found) and int(found.group(1)) >= 16 and any(c.isdigit() for c in name)


def kit_names(text: str) -> set[str]:
    """Declared tracker or cloaker names in a config label."""
    names = {w.lower() for w in _WORD.findall(text) if w.lower() in KNOWN_KITS}
    names |= {v.lower() for v in _VIA.findall(text)}
    return names


@extractors.register
class KitConfig:
    name = "kit.config"
    version = "1"

    def run(self, view: View, ctx: Context) -> list[Observation]:
        mat = m.load(view, ctx)
        emit = m.Emitter(view, self.name, self.version)

        def config_keys() -> None:
            if mat.aio is None:
                return
            for path in key_paths(mat.aio):
                emit("B3", "config.key_path", path, m.at(mat.aio_source, f"aio:{path}"))

        def hidden_names() -> None:
            for tag in mat.hidden_inputs:
                name = (tag.attrs.get("name") or "").strip().lower()
                if name and not _is_token(name):
                    emit("B4", "form.hidden_name", name, tag.source)

        def declared_kit() -> None:
            if mat.aio is None:
                return
            for path in _NAME_PATHS:
                label = m.config_get(mat.aio, path)
                if not isinstance(label, str):
                    continue
                for name in sorted(kit_names(label)):
                    source = m.at(mat.aio_source, f"aio:{path}")
                    emit("B8", "kit.cloaker", name, source, raw=label, purpose=Purpose.BOTH)

        m.run_steps([config_keys, hidden_names, declared_kit])
        return emit.out
