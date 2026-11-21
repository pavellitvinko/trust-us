"""OpenAI client: structured reading behind the cache.

Streams the answer, so the timeout limits silence rather than the length of a long read.
"""

from __future__ import annotations

import json
import time
from typing import Any

from pydantic import BaseModel, ValidationError

from ..config import Settings, get_settings
from .gateway import (
    ModelResult,
    TransientModelError,
    guarded_call,
    is_timeout,
    refusal_envelope,
    schema_parser,
    success_envelope,
)
from .redact import redact_json, redact_text

SILENCE_TIMEOUT_SECONDS = 60.0
MAX_RETRIES = 2  # for rate limits, server and connection errors; never after a timeout
MAX_OUTPUT_TOKENS = 16000


def _make_client(settings: Settings) -> Any:
    if not settings.openai_api_key:
        raise TransientModelError("no_key")
    import openai

    # Retries are handled in `_send`: the SDK would also repeat a timed-out generation.
    return openai.OpenAI(
        api_key=settings.openai_api_key, timeout=SILENCE_TIMEOUT_SECONDS, max_retries=0
    )


def _send(client: Any, **request: Any) -> Any:
    import httpx2
    import openai

    retryable = (
        openai.RateLimitError,
        openai.InternalServerError,
        openai.APIConnectionError,
        httpx2.TransportError,
    )
    for attempt in range(MAX_RETRIES + 1):
        try:
            with client.responses.stream(**request) as stream:
                return stream.get_final_response()
        except ValidationError as exc:  # the SDK validates the text as it arrives
            raise TransientModelError("schema") from exc
        except (openai.APIError, httpx2.HTTPError) as exc:
            if is_timeout(exc):
                raise TransientModelError("timeout") from exc
            if not isinstance(exc, retryable) or attempt == MAX_RETRIES:
                raise TransientModelError("error") from exc
            time.sleep(2**attempt)
        except (AssertionError, RuntimeError, ValueError) as exc:  # failed or truncated stream
            raise TransientModelError("error") from exc
    raise TransientModelError("error")


def _final_parts(response: Any) -> list[Any]:
    """Message parts of the final answer, skipping any commentary phase."""
    return [
        part
        for item in response.output
        if item.type == "message" and getattr(item, "phase", None) in (None, "final_answer")
        for part in item.content
    ]


def _is_inline_image(part: Any) -> bool:
    if not isinstance(part, dict) or part.get("type") != "input_image":
        return False
    url = part.get("image_url")
    return isinstance(url, str) and url.startswith("data:image/")


def _redact_content(content: str | list[dict[str, Any]]) -> Any:
    """Redact text parts. Inline image data stays whole: base64 can look like `token=...`."""
    if not isinstance(content, list):
        return redact_json(content)
    out: list[Any] = []
    for part in content:
        if _is_inline_image(part):
            rest = {key: value for key, value in part.items() if key != "image_url"}
            out.append({**redact_json(rest), "image_url": part["image_url"]})
        else:
            out.append(redact_json(part))
    return out


def _redact_strings(value: Any) -> Any:
    """Redact every string in a model answer, keeping its keys and other value types."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [_redact_strings(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_strings(item) for key, item in value.items()}
    return value


def read_structured[S: BaseModel](
    task: str,
    prompt_version: str,
    system: str,
    content: str | list[dict[str, Any]],
    schema: type[S],
    effort: str | None = None,
    *,
    settings: Settings | None = None,
    client: Any = None,
) -> ModelResult[S]:
    """Ask the reading model for one `schema` object. Any failure gives `not_observable`.

    `content` is text, or Responses API parts such as `input_text` and `input_image`.
    """
    settings = settings or get_settings()
    safe_system = redact_text(system)
    safe_content = _redact_content(content)
    payload = {
        "system": safe_system,
        "content": safe_content,
        "schema": schema.model_json_schema(),
        "effort": effort,
    }

    def compute() -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": settings.openai_model,
            "instructions": safe_system,
            "input": [{"role": "user", "content": safe_content}],
            "text_format": schema,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "store": False,
        }
        if effort is not None:
            request["reasoning"] = {"effort": effort}
        response = _send(client or _make_client(settings), **request)
        parts = _final_parts(response)
        if any(part.type == "refusal" for part in parts):
            return refusal_envelope()
        if response.status != "completed":
            raise TransientModelError("schema")
        text = next((p.text for p in parts if p.type == "output_text"), None)
        if text is None:
            raise TransientModelError("schema")
        try:
            schema.model_validate_json(text)
            answer = _redact_strings(json.loads(text))  # the cache ships, so it holds no secrets
            schema_parser(schema)(answer)
        except ValidationError as exc:
            raise TransientModelError("schema") from exc
        return success_envelope(answer)

    return guarded_call(
        settings.model_cache_dir,
        task,
        prompt_version,
        settings.openai_model,
        payload,
        compute,
        schema_parser(schema),
    )
