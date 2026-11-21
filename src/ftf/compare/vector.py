"""Calibrated cosine for dense-vector layers.

Raw cosine depends on the embedding model: unrelated Polish articles may sit at 0.6 under
one model and 0.2 under another. The score is therefore rescaled against the layer's
background distribution (mean and spread of cosine over pairs assumed unrelated):

    score = clip((cos - mu - k*sigma) / (1 - mu - k*sigma), 0, 1)

Swapping the model means re-running `calibrate`, not moving thresholds. Uncalibrated
layers fall back to clip(cos, 0, 1), which is honest but model-dependent.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from itertools import combinations
from pathlib import Path
from typing import TYPE_CHECKING

from ..contracts import Evidence, Fingerprint
from ..registry import comparators

if TYPE_CHECKING:
    from ..store import Store


@dataclass
class Calibration:
    model: str
    mu: float
    sigma: float
    pairs: int
    k: float = 2.0

    def score(self, cos: float) -> float:
        floor = self.mu + self.k * self.sigma
        if floor >= 1.0:
            return 1.0 if cos >= 1.0 else 0.0
        return max(0.0, min(1.0, (cos - floor) / (1.0 - floor)))


_calibrations: dict[str, Calibration] = {}


def cosine(u: list[float], v: list[float]) -> float:
    if not u or not v or len(u) != len(v):
        return 0.0
    dot = sum(x * y for x, y in zip(u, v))
    nu = math.sqrt(sum(x * x for x in u))
    nv = math.sqrt(sum(y * y for y in v))
    return dot / (nu * nv) if nu and nv else 0.0


def set_calibration(layer_id: str, calibration: Calibration | None) -> None:
    if calibration is None:
        _calibrations.pop(layer_id, None)
    else:
        _calibrations[layer_id] = calibration


def get_calibration(layer_id: str) -> Calibration | None:
    return _calibrations.get(layer_id)


def calibration_path(data_dir: Path, layer_id: str, model: str) -> Path:
    safe = model.replace("/", "_")
    return Path(data_dir) / "calibration" / f"{layer_id}.{safe}.json"


def save_calibration(data_dir: Path, layer_id: str, calibration: Calibration) -> Path:
    path = calibration_path(data_dir, layer_id, calibration.model)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(calibration), indent=1), encoding="utf-8")
    return path


def load_calibration(data_dir: Path, layer_id: str, model: str) -> Calibration | None:
    path = calibration_path(data_dir, layer_id, model)
    if not path.is_file():
        return None
    cal = Calibration(**json.loads(path.read_text(encoding="utf-8")))
    set_calibration(layer_id, cal)
    return cal


def calibrate(
    store: Store, layer_id: str, background_pairs: set[tuple[str, str]] | None = None
) -> Calibration | None:
    """Fit mu and sigma of cosine over background pairs: by default, pairs of views from
    different incidents that share no identity-tier evidence."""
    fps = {f.view_id: f for f in store.fingerprints(layer_id=layer_id)}
    if len(fps) < 2:
        return None
    incident_of = {v.view_id: v.incident_id for v in store.views()}
    if background_pairs is None:
        from ..registry import layers, load_plugins

        load_plugins()
        identity = {layer.name for layer in layers.all() if layer.tier.value == "T1"}
        linked = {
            (e.view_a, e.view_b) for e in store.evidence(min_score=0.5) if e.layer_id in identity
        }
        background_pairs = {
            (a, b)
            for a, b in combinations(sorted(fps), 2)
            if incident_of.get(a) != incident_of.get(b) and (a, b) not in linked
        }
    cosines = [
        cosine(fps[a].payload["values"], fps[b].payload["values"]) for a, b in background_pairs
    ]
    if not cosines:
        return None
    mu = sum(cosines) / len(cosines)
    sigma = math.sqrt(sum((c - mu) ** 2 for c in cosines) / len(cosines))
    model = next(iter(fps.values())).payload.get("model", "?")
    cal = Calibration(model=model, mu=mu, sigma=sigma, pairs=len(cosines))
    set_calibration(layer_id, cal)
    return cal


@comparators.register
class CalibratedCosine:
    name = "calibrated_cosine"
    version = "1"

    def compare(self, a: Fingerprint, b: Fingerprint) -> Evidence:
        ma, mb = a.payload.get("model"), b.payload.get("model")
        if ma != mb:
            return Evidence(
                view_a=a.view_id,
                view_b=b.view_id,
                layer_id=a.layer_id,
                comparable=False,
                comparator=self.name,
                version=self.version,
            )
        cos = cosine(a.payload.get("values", []), b.payload.get("values", []))
        cal = _calibrations.get(a.layer_id)
        score = cal.score(cos) if cal else max(0.0, min(1.0, cos))
        return Evidence(
            view_a=a.view_id,
            view_b=b.view_id,
            layer_id=a.layer_id,
            score=round(score, 6),
            shared=[
                {
                    "ns": "cosine",
                    "value": f"{cos:.3f}",
                    "weight": round(score, 4),
                    "calibrated": cal is not None,
                }
            ]
            if score > 0
            else [],
            comparator=self.name,
            version=self.version,
        )
