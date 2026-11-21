"""Embedding behind one interface, so the vector layers do not care which model is behind it.

`FakeEmbedder` is deterministic hashed bag-of-words with mean pooling: no network, no key,
stable across machines. It exists so fusion, calibration and the tests run with the gateway
off. `GatewayEmbedder` calls the Perception track's Gemini client through the content-hash
cache, chunking long text and mean-pooling the chunks (8,192-token input limit).

Selection: `FTF_EMBEDDER=fake|gateway`; default is gateway when a Gemini key and the client
exist, else fake. Swapping the model changes the raw cosines; `compare.vector.calibrate`
absorbs that, thresholds stay put.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from collections.abc import Sequence
from itertools import pairwise
from typing import Protocol

from ..config import get_settings

_WORD = re.compile(r"\w+", re.UNICODE)


class Embedder(Protocol):
    model: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class FakeEmbedder:
    """Hashed word stems (5-character prefixes, which ride over Polish inflection) and stem
    bigrams, L2-normalised. Enough to order 'same story' above 'different story' on short
    Polish text so the mechanics can be tested; never the basis of a real score."""

    model = "fake-stem-256"
    dim = 256
    stem = 5

    def _vector(self, text: str) -> list[float]:
        words = [w.lower()[: self.stem] for w in _WORD.findall(text)]
        tokens = words + [f"{a} {b}" for a, b in pairwise(words)]
        vec = [0.0] * self.dim
        for t in tokens:
            h = int.from_bytes(hashlib.blake2b(t.encode(), digest_size=4).digest(), "big")
            vec[h % self.dim] += 1.0 if (h >> 31) == 0 else -1.0
        norm = math.sqrt(sum(x * x for x in vec))
        return [x / norm for x in vec] if norm else vec

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


class GatewayEmbedder:
    """Gemini embeddings via the model gateway. The client is the Perception track's
    `ftf.models.embeddings.embed_texts(texts, model) -> list[list[float]]`."""

    chunk_chars = 20_000  # ~8k tokens of Polish text, with margin

    def __init__(self, model: str):
        self.model = model

    def _call(self, texts: list[str]) -> list[list[float]]:
        from ..models import cached_call

        try:
            from ..models.embeddings import embed_texts  # provided by the Perception track
        except ImportError as exc:  # pragma: no cover - depends on another track
            raise RuntimeError(
                "gateway embedder requested but ftf.models.embeddings is not available; "
                "set FTF_EMBEDDER=fake or add the client"
            ) from exc
        settings = get_settings()
        out = []
        for text in texts:
            out.append(
                cached_call(
                    settings.model_cache_dir,
                    "embed",
                    "1",
                    self.model,
                    {"text": text},
                    lambda text=text: embed_texts([text], self.model)[0],
                )
            )
        return out

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            chunks = [
                text[i : i + self.chunk_chars] for i in range(0, len(text), self.chunk_chars)
            ] or [""]
            parts = self._call(chunks)
            dim = len(parts[0])
            pooled = [sum(p[i] for p in parts) / len(parts) for i in range(dim)]
            norm = math.sqrt(sum(x * x for x in pooled))
            vectors.append([x / norm for x in pooled] if norm else pooled)
        return vectors


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        choice = os.environ.get("FTF_EMBEDDER", "").lower()
        settings = get_settings()
        if choice == "gateway" or (not choice and settings.gemini_api_key):
            _embedder = GatewayEmbedder(settings.embedding_model)
        else:
            _embedder = FakeEmbedder()
    return _embedder


def set_embedder(embedder: Embedder | None) -> None:
    global _embedder
    _embedder = embedder
