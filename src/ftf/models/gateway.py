"""Content-hash cache in front of every model call.

Key = hash(task name + prompt version + model ID + input). A cached answer is returned
without touching the network, so re-runs are instant, free and repeatable. Provider
clients are added by the Perception track; they pass their call in as `compute`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any


def cache_key(task: str, prompt_version: str, model: str, payload: Any) -> str:
    blob = json.dumps(
        [task, prompt_version, model, payload], sort_keys=True, ensure_ascii=False, default=str
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


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
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    result = compute()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result
