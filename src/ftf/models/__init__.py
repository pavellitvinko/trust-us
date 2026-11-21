"""Model gateway: the only place that calls Claude or Gemini. Perception track."""

from .gateway import cache_key, cached_call

__all__ = ["cache_key", "cached_call"]
