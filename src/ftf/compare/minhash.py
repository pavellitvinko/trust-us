"""Pure-Python MinHash for near-duplicate text and large class sets.

Fixed seeds, so signatures are reproducible across runs and machines. Used by the
`text.shingle` and `kit.dom` layers and by the `minhash` comparator; `bands` feeds
candidate generation.
"""

from __future__ import annotations

import hashlib
import random
import re
from collections.abc import Iterable

_MERSENNE = (1 << 61) - 1
_MAX_HASH = (1 << 32) - 1
NUM_PERM = 128

_WORD = re.compile(r"\w+", re.UNICODE)


def _token_hash(token: str) -> int:
    return int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest(), "big")


class MinHasher:
    def __init__(self, num_perm: int = NUM_PERM, seed: int = 1):
        rng = random.Random(seed)
        self.num_perm = num_perm
        self.params = [
            (rng.randint(1, _MERSENNE - 1), rng.randint(0, _MERSENNE - 1)) for _ in range(num_perm)
        ]

    def signature(self, tokens: Iterable[str]) -> list[int]:
        hashes = {_token_hash(t) for t in tokens}
        if not hashes:
            return []
        sig = []
        for a, b in self.params:
            sig.append(min(((a * h + b) % _MERSENNE) & _MAX_HASH for h in hashes))
        return sig


default_hasher = MinHasher()


def estimate_jaccard(sig_a: list, sig_b: list) -> float:
    if not sig_a or not sig_b or len(sig_a) != len(sig_b):
        return 0.0
    return sum(1 for x, y in zip(sig_a, sig_b) if str(x) == str(y)) / len(sig_a)


def normalise_text(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text)]


def shingles(text: str, n: int = 5) -> set[str]:
    words = normalise_text(text)
    if len(words) < n:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}


def bands(sig: list, band_size: int = 4) -> list[str]:
    """LSH band keys: two signatures sharing a band key are likely similar."""
    return [":".join(str(x) for x in sig[i : i + band_size]) for i in range(0, len(sig), band_size)]
