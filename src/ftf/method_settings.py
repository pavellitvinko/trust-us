"""Analyst-edited fusion weights and thresholds, saved beside the data."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .config import get_settings
from .fusion import DEFAULT, FusionConfig

log = logging.getLogger(__name__)

THRESHOLDS = (
    "duplicate_text",
    "identity",
    "artifact",
    "artifact_group_floor",
    "kit",
    "playbook",
    "min_coverage",
    "unrelated_high_coverage",
)
_FACTORY = FusionConfig()


def settings_path() -> Path:
    return get_settings().data_dir / "method_config.json"


def current_values() -> dict[str, Any]:
    return {
        "group_weights": dict(DEFAULT.group_weights),
        "tier_weights": dict(DEFAULT.tier_weights),
        "thresholds": {name: getattr(DEFAULT, name) for name in THRESHOLDS},
        "artifact_min_groups": DEFAULT.artifact_min_groups,
    }


def _unit(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and 0 <= value <= 1


def validate(values: dict[str, Any]) -> list[str]:
    if not isinstance(values, dict):
        return ["settings must be an object"]
    errors = []
    for section, known in (
        ("group_weights", _FACTORY.group_weights),
        ("tier_weights", _FACTORY.tier_weights),
        ("thresholds", dict.fromkeys(THRESHOLDS)),
    ):
        given = values.get(section)
        if not isinstance(given, dict) or set(given) != set(known):
            errors.append(f"{section}: expected exactly the known settings")
            continue
        errors += [f"{section}.{k}: must be from 0 to 1" for k, v in given.items() if not _unit(v)]
    minimum = values.get("artifact_min_groups")
    if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 1:
        errors.append("artifact_min_groups: must be a whole number of at least 1")
    return errors


def _apply(values: dict[str, Any]) -> None:
    # Every pipeline function takes DEFAULT as its default, so the CLI and UI stay in step.
    DEFAULT.group_weights = dict(values["group_weights"])
    DEFAULT.tier_weights = dict(values["tier_weights"])
    DEFAULT.artifact_min_groups = values["artifact_min_groups"]
    for name, value in values["thresholds"].items():
        setattr(DEFAULT, name, float(value))


def factory_values() -> dict[str, Any]:
    return {
        "group_weights": dict(_FACTORY.group_weights),
        "tier_weights": dict(_FACTORY.tier_weights),
        "thresholds": {name: getattr(_FACTORY, name) for name in THRESHOLDS},
        "artifact_min_groups": _FACTORY.artifact_min_groups,
    }


def apply_saved() -> None:
    """Use saved settings, or the shipped ones when none are saved or the file is invalid."""
    path = settings_path()
    values = factory_values()
    if path.is_file():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            errors = validate(saved)
            if errors:
                log.warning("ignoring %s: %s", path, "; ".join(errors))
            else:
                values = saved
        except (OSError, ValueError) as exc:
            log.warning("ignoring %s: %s", path, exc)
    _apply(values)


def save(values: dict[str, Any]) -> list[str]:
    errors = validate(values)
    if errors:
        return errors
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(values, indent=1), encoding="utf-8")
    temp.replace(path)
    _apply(values)
    return []


def reset() -> None:
    settings_path().unlink(missing_ok=True)
    _apply(factory_values())
