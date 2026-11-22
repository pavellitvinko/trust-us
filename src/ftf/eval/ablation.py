"""Layer ablation and degradation runs on the fixtures (docs/architecture.md section 13).

Ablation switches one layer off at a time and records what changes against the reference:
agreement, false merges, and which reference pairs flipped. Degradation re-runs the whole
method on damaged input: no HAR-derived evidence, screenshot only, re-encoded and
hue-shifted images, swapped domains, and the gateway off (deterministic evidence only).
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .. import pipeline
from ..compare.weights import weights
from ..contracts import Observation, RelationType
from ..fusion import DEFAULT, FusionConfig, run_method
from ..registry import layers, load_plugins
from .fixtures import OBSERVATIONS, store_from_fixtures
from .harness import Report, evaluate

HEADLINE = (
    ("1:page", "2:page", RelationType.SAME_CAMPAIGN),
    ("2:healthlinkq", "3:page", RelationType.DUPLICATE),
    ("48:page", "61:article", RelationType.SAME_FAMILY),
    ("61:article", "61:chat", RelationType.SAME_CAMPAIGN),
    ("14:page", "47:page", RelationType.SAME_PLAYBOOK),
)


@dataclass
class RunResult:
    name: str
    report: Report
    headline: dict[tuple[str, str], RelationType | None]
    layers_on: int
    relations: dict[tuple[str, str], RelationType] = field(default_factory=dict)

    @property
    def headline_ok(self) -> int:
        return sum(self.headline.get((a, b)) is t for a, b, t in HEADLINE)

    def flipped_from(self, base: RunResult) -> list[str]:
        mine = {(r.ref.a, r.ref.b): r.predicted for r in self.report.results}
        theirs = {(r.ref.a, r.ref.b): r.predicted for r in base.report.results}
        return [
            f"{a}<->{b}: {theirs[(a, b)].value if theirs[(a, b)] else '-'} -> "
            f"{mine[(a, b)].value if mine[(a, b)] else '-'}"
            for (a, b) in mine
            if mine[(a, b)] != theirs.get((a, b))
        ]


@contextmanager
def disabled_layers(names: Iterable[str]):
    """Temporarily unregister layers. Restored on exit, whatever happens."""
    load_plugins()
    removed = {n: layers._items.pop(n) for n in names if n in layers._items}
    try:
        yield
    finally:
        layers._items.update(removed)


def run_once(
    name: str,
    path: Path = OBSERVATIONS,
    config: FusionConfig = DEFAULT,
    exclude_namespaces: set[str] = frozenset(),
    transform: Callable[[Observation], Observation | None] | None = None,
    off: Iterable[str] = (),
) -> RunResult:
    with disabled_layers(off):
        store = store_from_fixtures(
            path, exclude_namespaces=exclude_namespaces, transform=transform
        )
        pipeline.run_fingerprint(store)
        run_method(store, config)
        report = evaluate(store)
        rel = {(r.view_a, r.view_b): r.type for r in store.relations()}
        headline = {(a, b): rel.get(tuple(sorted((a, b)))) for a, b, _ in HEADLINE}
        n = len(layers.all())
        store.close()
    weights.reset()
    return RunResult(name, report, headline, n, rel)


def ablate(path: Path = OBSERVATIONS, config: FusionConfig = DEFAULT) -> list[RunResult]:
    load_plugins()
    base = run_once("all layers", path, config)
    out = [base]
    for layer in layers.names():
        out.append(run_once(f"without {layer}", path, config, off=[layer]))
    return out


# -- degradation scenarios -------------------------------------------------------------------

HAR_NAMESPACES = {
    "tracker.host",
    "form_endpoint_host",
    "form_endpoint_path",
    "http.server",
    "api.route_shape",
    "redirect.domain",
    "outbound.domain",
    "gads_id",
    "dns.ns_pair",
    # HAC-19: playlist hosts and CDN video libraries are read mostly from HAR requests
    "video.hls_host",
    "cdn.zone_id",
}
MODEL_NAMESPACES_PREFIX = ("ocr.", "pattern.", "expect.", "page.kind", "entity.", "layout.")
MODEL_LAYERS = (
    "text.semantic",
    "narrative.skeleton",
    "pattern.mo",
    "kit.absence",
    "entity.cast",
    "entity.roles",
    "visual.layout",
)
IMAGE_NAMESPACES = {
    "creative.phash",
    "image.phash",
    "video.frame_phash",
    "ocr.domain",
    "ocr.brand",
    "ocr.person",
    "ad.id",
    "ad.advertiser",
}


def _flip_bits(hex64: str, n: int, rng: random.Random) -> str:
    v = int(hex64, 16)
    for _ in range(n):
        v ^= 1 << rng.randrange(64)
    return f"{v:016x}"


def reencode_images(o: Observation) -> Observation | None:
    """Re-encoded and hue-shifted copies: exact hashes change, perceptual hashes move 2-3 bits."""
    if o.namespace in ("asset.sha256", "avatar.sha256"):
        return None if o.view_id.startswith(("2:", "61:", "99:")) else o
    if o.namespace in (
        "creative.phash",
        "image.phash",
        "video.frame_phash",
    ) and o.view_id.startswith(("2:", "61:", "99:")):
        rng = random.Random(f"{o.view_id}:{o.value}")
        return o.model_copy(update={"value": _flip_bits(str(o.value), rng.choice((2, 3)), rng)})
    return o


def swap_domains(o: Observation) -> Observation | None:
    """Every incident-2 view moves to a fresh domain."""
    if not o.view_id.startswith("2:"):
        return o
    if o.namespace == "domain.registrable":
        return o.model_copy(update={"value": f"swapped-{o.value}"})
    if o.namespace == "url.host_path":
        return o.model_copy(update={"value": f"swapped-{o.value}"})
    return o


def screenshot_only(o: Observation) -> Observation | None:
    return o if o.namespace in IMAGE_NAMESPACES else None


def model_off_namespaces(o: Observation) -> Observation | None:
    return None if o.namespace.startswith(MODEL_NAMESPACES_PREFIX) else o


def degrade(path: Path = OBSERVATIONS, config: FusionConfig = DEFAULT) -> list[RunResult]:
    return [
        run_once("full input", path, config),
        run_once("no HAR-derived evidence", path, config, exclude_namespaces=HAR_NAMESPACES),
        run_once("screenshots and video only", path, config, transform=screenshot_only),
        run_once(
            "images re-encoded + hue-shifted (2, 61, 99)", path, config, transform=reencode_images
        ),
        run_once("incident 2 on a swapped domain", path, config, transform=swap_domains),
        run_once(
            "model off: deterministic evidence only",
            path,
            config,
            transform=model_off_namespaces,
            off=MODEL_LAYERS,
        ),
    ]


def format_runs(runs: list[RunResult], title: str) -> str:
    base = runs[0]
    lines = [
        title,
        "",
        f"{'run':48} {'layers':>6} {'agree':>6} {'exact':>6} {'merges':>6} {'headline':>8}  changes",
    ]
    for r in runs:
        flips = r.flipped_from(base)
        change = (
            "-"
            if r is base
            else (
                f"{len(flips)} pair(s): " + "; ".join(flips[:3]) + (" …" if len(flips) > 3 else "")
            )
            if flips
            else "none"
        )
        lines.append(
            f"{r.name:48} {r.layers_on:6} {r.report.agreement:6.0%} {r.report.exact_agreement:6.0%} "
            f"{len(r.report.false_merges):6} {r.headline_ok:>5}/{len(HEADLINE)}  {change}"
        )
    lines += [
        "",
        "headline = 1<->2 campaign, healthlinkq<->3 duplicate, 48<->61 family, 61 article<->chat campaign, 14<->47 playbook",
    ]
    return "\n".join(lines)
