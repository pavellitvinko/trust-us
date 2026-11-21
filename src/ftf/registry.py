"""Plugin registries and the interfaces plugins implement.

Adding a signal, a layer or a comparator means writing one class and decorating it.
Nothing else in the pipeline changes.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypeVar, runtime_checkable

from .contracts import Evidence, Fingerprint, FingerprintKind, Observation, Tier, View

if TYPE_CHECKING:
    from .store import Store

T = TypeVar("T")


class Context:
    """What a plugin may touch: the store and the files of the view's incident."""

    def __init__(self, store: Store):
        self.store = store

    def path(self, view: View, relative: str) -> Path:
        incident = self.store.get_incident(view.incident_id)
        return Path(incident.root) / relative

    def read_text(self, view: View, relative: str, limit: int | None = None) -> str:
        with open(self.path(view, relative), encoding="utf-8", errors="replace") as fh:
            return fh.read() if limit is None else fh.read(limit)


@runtime_checkable
class Extractor(Protocol):
    """Reads a view's files and returns observations. Owned by the Evidence track."""

    name: str
    version: str

    def run(self, view: View, ctx: Context) -> list[Observation]: ...


@runtime_checkable
class Enricher(Protocol):
    """Adds observations from outside sources, given what is already known about a view."""

    name: str
    version: str

    def run(
        self, view: View, observations: list[Observation], ctx: Context
    ) -> list[Observation]: ...


@runtime_checkable
class Layer(Protocol):
    """Turns a view's observations into one fingerprint. Owned by the Method track."""

    name: str  # layer_id, e.g. "ids.domain"
    version: str
    kind: FingerprintKind
    tier: Tier
    group: str  # correlation group: layers in one group are not counted twice
    comparator: str  # name of a registered comparator

    def build(self, view: View, observations: list[Observation]) -> Fingerprint | None: ...


@runtime_checkable
class Comparator(Protocol):
    name: str
    version: str

    def compare(self, a: Fingerprint, b: Fingerprint) -> Evidence: ...


class Registry[T]:
    def __init__(self, kind: str):
        self.kind = kind
        self._items: dict[str, T] = {}

    def register(self, cls):
        """Class decorator: instantiates the plugin and stores it under its `name`."""
        instance = cls()
        name = instance.name
        if name in self._items:
            raise ValueError(f"duplicate {self.kind}: {name}")
        self._items[name] = instance
        return cls

    def get(self, name: str) -> T:
        try:
            return self._items[name]
        except KeyError:
            raise KeyError(f"unknown {self.kind}: {name!r}; known: {sorted(self._items)}") from None

    def all(self) -> list[T]:
        return [self._items[k] for k in sorted(self._items)]

    def names(self) -> list[str]:
        return sorted(self._items)


extractors: Registry[Extractor] = Registry("extractor")
enrichers: Registry[Enricher] = Registry("enricher")
layers: Registry[Layer] = Registry("layer")
comparators: Registry[Comparator] = Registry("comparator")

_PLUGIN_PACKAGES = ("ftf.extract", "ftf.enrich", "ftf.layers", "ftf.compare")
_loaded = False


def load_plugins() -> None:
    """Import every module in the plugin packages so their decorators run."""
    global _loaded
    if _loaded:
        return
    for pkg_name in _PLUGIN_PACKAGES:
        pkg = importlib.import_module(pkg_name)
        for mod in pkgutil.iter_modules(pkg.__path__):
            importlib.import_module(f"{pkg_name}.{mod.name}")
    _loaded = True
