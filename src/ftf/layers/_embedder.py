"""Embedding behind one interface, so the vector layers do not care which model is behind it.

`FakeEmbedder` is deterministic hashed bag-of-words with mean pooling: no network, no key,
stable across machines. It exists so fusion, calibration and the tests run with the gateway
off. `GatewayEmbedder` calls the Perception track's Gemini client (`ftf.models.embed`), which
chunks long text, mean-pools the chunks and caches every call by content hash.

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

    def embed(self, texts: Sequence[str]) -> list[list[float] | None]: ...


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
    """Gemini embeddings through the Perception track's gateway (`ftf.models.embed`), which
    chunks long text, mean-pools the chunks and caches by content hash. A text the gateway
    cannot embed (no key, refusal, timeout) yields None, and the layer is left out for that
    view: a missing layer never counts against a link."""

    def __init__(self, model: str):
        self.model = model

    def embed(self, texts: Sequence[str]) -> list[list[float] | None]:
        from ..models import embed

        out: list[list[float] | None] = []
        for result in embed(list(texts)):
            if result.value is None:
                out.append(None)
            else:
                out.append(result.value.vector)
                self.model = result.value.model
        return out


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
