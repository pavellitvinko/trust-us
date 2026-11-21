"""Model gateway: the only place that calls OpenAI or Gemini. Perception track."""

from .gateway import ModelResult, cache_key, cached_call
from .gemini import Embedding, EmbedTask, chunk_text, embed, read_video
from .openai_client import read_structured
from .redact import redact_json, redact_text

__all__ = [
    "EmbedTask",
    "Embedding",
    "ModelResult",
    "cache_key",
    "cached_call",
    "chunk_text",
    "embed",
    "read_structured",
    "read_video",
    "redact_json",
    "redact_text",
]
