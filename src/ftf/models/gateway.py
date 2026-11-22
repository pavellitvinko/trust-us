"""Content-hash cache in front of every model call.

Key = hash(task name + prompt version + model ID + input). A cached answer is returned
without touching the network, so re-runs are instant, free and repeatable. Provider
clients pass their call in as `compute`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from ..contracts import State

log = logging.getLogger(__name__)

_MISS = object()


@dataclass(frozen=True)
class ModelResult[T]:
    """A validated value, or `not_observable` with the reason. Never a guessed value."""

    state: State
    value: T | None = None
    reason: str | None = None  # refusal|timeout|schema|error|no_key|empty|unsupported
    # Calls a failed result still made (an embedding's chunks before the one that failed),
    # each of them cached, so a run record counts them as calls, not as cache hits.
    calls_made: int = 0

    @classmethod
    def ok(cls, value: T) -> ModelResult[T]:
        return cls(state=State.PRESENT, value=value)

    @classmethod
    def not_observable(cls, reason: str, calls_made: int = 0) -> ModelResult[T]:
        return cls(state=State.NOT_OBSERVABLE, reason=reason, calls_made=calls_made)


class TransientModelError(Exception):
    """A failure worth retrying on the next run. Raised inside `compute`, so it is never cached."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def is_timeout(exc: BaseException) -> bool:
    """Recognise timeouts from either SDK and from either HTTP library they may use."""
    names = {"TimeoutException", "APITimeoutError"}
    return isinstance(exc, TimeoutError) or any(c.__name__ in names for c in type(exc).__mro__)


def success_envelope(value: Any) -> dict[str, Any]:
    return {"state": State.PRESENT.value, "value": value}


def refusal_envelope() -> dict[str, Any]:
    """A real refusal is a stable answer for this input, so it is cached."""
    return {"state": State.NOT_OBSERVABLE.value, "reason": "refusal"}


def schema_parser[S: BaseModel](schema: type[S]) -> Callable[[Any], S]:
    """Validate cached JSON the same way as the model's answer, keeping aliases and strict mode."""

    def parse(value: Any) -> S:
        return schema.model_validate_json(json.dumps(value, ensure_ascii=False))

    return parse


def cache_key(task: str, prompt_version: str, model: str, payload: Any) -> str:
    blob = json.dumps(
        [task, prompt_version, model, payload], sort_keys=True, ensure_ascii=False, default=str
    )
    return hashlib.sha256(blob.encode("utf-8", "surrogatepass")).hexdigest()


def _read_cache(path: Path) -> Any:
    """Return the stored value, or `_MISS` when the entry is absent or unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _MISS
    except (OSError, ValueError):
        log.warning("ignoring unreadable cache entry %s", path)
        return _MISS


def _write_cache(path: Path, result: Any) -> None:
    """Write through a temp file, so a killed process cannot leave a partial entry."""
    temp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        blob = json.dumps(result, ensure_ascii=False, indent=1).encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        with temp.open("xb") as fh:  # honours the umask, so the shared cache stays readable
            fh.write(blob)
        os.replace(temp, path)
    except (OSError, UnicodeError):
        log.warning("could not write cache entry %s", path)
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def cached_call(
    cache_dir: Path,
    task: str,
    prompt_version: str,
    model: str,
    payload: Any,
    compute: Callable[[], Any],
) -> Any:
    """Return the cached JSON result for this exact input, computing and storing it on a miss."""
    key = cache_key(task, prompt_version, model, payload)
    path = Path(cache_dir) / task / f"{key}.json"
    cached = _read_cache(path)
    if cached is not _MISS:
        return cached
    result = compute()
    _write_cache(path, result)
    return result


def guarded_call[T](
    cache_dir: Path,
    task: str,
    prompt_version: str,
    model: str,
    payload: Any,
    compute: Callable[[], dict[str, Any]],
    parse: Callable[[Any], T],
) -> ModelResult[T]:
    """Run a call through the cache and turn every failure into `not_observable`."""
    try:
        envelope = cached_call(cache_dir, task, prompt_version, model, payload, compute)
    except TransientModelError as exc:
        return ModelResult.not_observable(exc.reason)
    if not isinstance(envelope, dict):
        return ModelResult.not_observable("schema")
    if envelope.get("state") != State.PRESENT.value:
        return ModelResult.not_observable(envelope.get("reason", "error"))
    try:
        return ModelResult.ok(parse(envelope["value"]))
    except (KeyError, TypeError, ValueError, ValidationError):
        return ModelResult.not_observable("schema")
