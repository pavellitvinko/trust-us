"""Gemini clients behind the cache: text embeddings and video reading."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from ..config import Settings, get_settings
from .gateway import (
    ModelResult,
    TransientModelError,
    guarded_call,
    is_timeout,
    schema_parser,
    success_envelope,
)
from .redact import redact_text

log = logging.getLogger(__name__)

EMBEDDING_DIMENSIONS = 3072
EMBED_PROMPT_VERSION = "v1"
CHUNK_TOKENS = 7000
CHUNK_OVERLAP_TOKENS = 200
CHARS_PER_TOKEN = 3  # conservative estimate for Polish text; needs no network call

EMBED_TIMEOUT_MS = 60_000
VIDEO_TIMEOUT_SECONDS = 300
RETRY_ATTEMPTS = 3  # the first try plus 2 retries
FILE_POLL_SECONDS = 5
FILE_POLL_TIMEOUT_MS = 30_000
# Ingested formats that Gemini's documentation lists. It does not list .mkv or .m4v.
VIDEO_MIME_TYPES = {".mp4": "video/mp4", ".mov": "video/mov", ".webm": "video/webm"}
CONVERT_TO_MP4 = {".mkv", ".m4v"}
CONVERT_TIMEOUT_SECONDS = 300


class EmbedTask(StrEnum):
    """Vectors made with different tasks must not be compared with each other."""

    SIMILARITY = "sentence similarity"  # comparing pages
    CLUSTERING = "clustering"  # grouping technique cards


class Embedding(BaseModel):
    vector: list[float]  # normalised mean of the chunk vectors
    chunks: list[list[float]]
    model: str


def _transient(exc: Exception) -> TransientModelError:
    if isinstance(exc, TransientModelError):
        return exc
    return TransientModelError("timeout" if is_timeout(exc) else "error")


def _make_client(settings: Settings, timeout_ms: int, attempts: int) -> Any:
    if not settings.gemini_api_key:
        raise TransientModelError("no_key")
    from google import genai
    from google.genai import types

    return genai.Client(
        api_key=settings.gemini_api_key,
        http_options=types.HttpOptions(
            timeout=timeout_ms, retry_options=types.HttpRetryOptions(attempts=attempts)
        ),
    )


def chunk_text(text: str) -> list[str]:
    """Split text to fit the 8,192-token limit, preferring to break on whitespace.

    Whitespace runs collapse first, so a padded page cannot produce an empty chunk.
    """
    text = " ".join(text.split())
    size = CHUNK_TOKENS * CHARS_PER_TOKEN
    overlap = CHUNK_OVERLAP_TOKENS * CHARS_PER_TOKEN
    if not text:
        return []
    chunks = []
    start = 0
    while len(text) - start > size:
        end = start + size
        space = text.rfind(" ", start + size - overlap, end)
        if space > start + overlap:
            end = space
        chunks.append(text[start:end].strip())
        start = end - overlap
    chunks.append(text[start:].strip())
    return chunks


def _with_prefix(task: EmbedTask, content: str) -> str:
    return f"task: {task.value} | query: {content}"


def _check_vector(values: Any) -> list[float]:
    if not isinstance(values, list) or len(values) != EMBEDDING_DIMENSIONS:
        raise ValueError("unexpected embedding size")
    return [float(v) for v in values]


def _mean_normalised(chunks: list[list[float]]) -> list[float]:
    mean = [sum(column) / len(chunks) for column in zip(*chunks, strict=True)]
    norm = math.sqrt(sum(v * v for v in mean))
    if norm == 0:
        raise ValueError("zero vector")
    return [v / norm for v in mean]


def _embed_chunk(prefixed: str, settings: Settings, client: Any) -> ModelResult[list[float]]:
    """One call and one cache entry per chunk, so a retry resumes where it failed."""

    def compute() -> dict[str, Any]:
        from google.genai import types

        try:
            active = client or _make_client(settings, EMBED_TIMEOUT_MS, RETRY_ATTEMPTS)
            response = active.models.embed_content(
                model=settings.embedding_model,
                contents=prefixed,
                config=types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIMENSIONS),
            )
        except Exception as exc:
            raise _transient(exc) from exc
        try:
            return success_envelope(_check_vector(response.embeddings[0].values))
        except (IndexError, TypeError, ValueError) as exc:
            raise TransientModelError("schema") from exc

    return guarded_call(
        settings.model_cache_dir,
        "embed",
        EMBED_PROMPT_VERSION,
        settings.embedding_model,
        {"text": prefixed, "dimensions": EMBEDDING_DIMENSIONS},
        compute,
        _check_vector,
    )


def _embed_one(
    text: str, task: EmbedTask, settings: Settings, client: Any
) -> ModelResult[Embedding]:
    pieces = chunk_text(redact_text(text))
    if not pieces:
        return ModelResult.not_observable("empty")

    vectors = []
    for piece in pieces:
        result = _embed_chunk(_with_prefix(task, piece), settings, client)
        if result.value is None:
            return ModelResult.not_observable(result.reason or "error")
        vectors.append(result.value)

    try:
        vector = _mean_normalised(vectors)
    except ValueError:
        return ModelResult.not_observable("schema")
    return ModelResult.ok(Embedding(vector=vector, chunks=vectors, model=settings.embedding_model))


def embed(
    texts: list[str],
    task: EmbedTask = EmbedTask.SIMILARITY,
    *,
    settings: Settings | None = None,
    client: Any = None,
) -> list[ModelResult[Embedding]]:
    """Embed each text separately, so one failure does not lose the others."""
    settings = settings or get_settings()
    return [_embed_one(text, task, settings, client) for text in texts]


def _wait_until_active(client: Any, uploaded: Any) -> Any:
    deadline = time.monotonic() + VIDEO_TIMEOUT_SECONDS
    while not uploaded.state or uploaded.state.name != "ACTIVE":
        if uploaded.state and uploaded.state.name == "FAILED":
            raise TransientModelError("error")
        if time.monotonic() >= deadline:
            raise TransientModelError("timeout")
        time.sleep(FILE_POLL_SECONDS)
        # A status check is free, so unlike generation it can be retried with a short timeout.
        uploaded = client.files.get(
            name=uploaded.name,
            config={
                "http_options": {
                    "timeout": FILE_POLL_TIMEOUT_MS,
                    "retry_options": {"attempts": RETRY_ATTEMPTS},
                }
            },
        )
    return uploaded


def _convert_to_mp4(source: Path, target: Path) -> None:
    """Re-encode one video and audio track. Input is untrusted, so ffmpeg may only read files."""
    command = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-protocol_whitelist",
        "file",
        "-i",
        f"file:{source.resolve()}",
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        "-y",
        f"file:{target}",
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=CONVERT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise TransientModelError("timeout") from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        raise TransientModelError("error") from exc


@contextmanager
def _as_uploadable(path: Path) -> Iterator[tuple[Path, str]]:
    """Yield a file Gemini accepts, converting to a temporary MP4 when needed."""
    mime_type = VIDEO_MIME_TYPES.get(path.suffix.lower())
    if mime_type is not None and path.name.isascii():
        yield path, mime_type
        return
    with tempfile.TemporaryDirectory(prefix="ftf-video-") as folder:
        if mime_type is not None:
            # The SDK sends the file name in an HTTP header, which must be ASCII.
            link = Path(folder) / f"video{path.suffix.lower()}"
            link.symlink_to(path.resolve())
            yield link, mime_type
            return
        target = Path(folder) / "video.mp4"
        _convert_to_mp4(path, target)
        yield target, "video/mp4"


def read_video[S: BaseModel](
    task: str,
    prompt_version: str,
    path: str | Path,
    prompt: str,
    schema: type[S],
    *,
    settings: Settings | None = None,
    client: Any = None,
) -> ModelResult[S]:
    """Read a video with its audio track into one `schema` object.

    The API has no clear refusal signal, so every failure here is left uncached and retried.
    """
    settings = settings or get_settings()
    path = Path(path)
    if not path.is_file():
        return ModelResult.not_observable("error")
    suffix = path.suffix.lower()
    if suffix not in VIDEO_MIME_TYPES and suffix not in CONVERT_TO_MP4:
        return ModelResult.not_observable("unsupported")
    with path.open("rb") as fh:
        file_hash = hashlib.file_digest(fh, "sha256").hexdigest()
    safe_prompt = redact_text(prompt)
    json_schema = schema.model_json_schema()
    payload = {"file_sha256": file_hash, "prompt": safe_prompt, "schema": json_schema}

    def compute() -> dict[str, Any]:
        uploaded = None
        active = None
        try:
            # One attempt: a retried video generation would be billed again.
            active = client or _make_client(settings, VIDEO_TIMEOUT_SECONDS * 1000, 1)
            with _as_uploadable(path) as (upload_path, mime_type):
                uploaded = active.files.upload(
                    file=str(upload_path), config={"mime_type": mime_type}
                )
            uploaded = _wait_until_active(active, uploaded)
            interaction = active.interactions.create(
                model=settings.video_model,
                input=[
                    {"type": "video", "uri": uploaded.uri, "mime_type": uploaded.mime_type},
                    {"type": "text", "text": safe_prompt},
                ],
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": json_schema,
                },
                timeout=VIDEO_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            raise _transient(exc) from exc
        finally:
            if active is not None and uploaded is not None:
                try:
                    active.files.delete(name=uploaded.name)
                except Exception:  # noqa: BLE001 - cleanup only; Gemini removes it after 48 h
                    log.warning("could not delete uploaded video %s", uploaded.name)
        if interaction.status != "completed":
            raise TransientModelError("error")
        text = interaction.output_text or ""
        try:
            schema.model_validate_json(text)
        except ValidationError as exc:
            raise TransientModelError("schema") from exc
        return success_envelope(json.loads(text))

    return guarded_call(
        settings.model_cache_dir,
        task,
        prompt_version,
        settings.video_model,
        payload,
        compute,
        schema_parser(schema),
    )
