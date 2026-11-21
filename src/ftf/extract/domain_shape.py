"""Domain name risk class and shape (signals C4, C5). Script only, no network.

C4: TLDs that dominate the sample's scam domains are `high`; `.pl` is `low`; the rest `other`.
C5: a coarse shape of the registrable label. Without a word list two-word names
(`civicdaybook`) stay `plain`; what is caught is digits with hyphens (`shop-77vault`),
strict consonant-vowel alternation (`meroporepufulo`) and runs of five or more consonants
(`shryxla-mynx`, counting `y` as a consonant).
The metrics go into `raw` so a reviewer can see why. Both are risk only: a shape class is shared
by unrelated domains and must not link views.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from itertools import pairwise

from ..contracts import Observation, Purpose, View, ViewKind
from ..registry import Context, extractors
from ._material import _tld, own_domain

HIGH_RISK_TLDS = frozenset({"live", "pro", "xyz", "top", "icu", "life"})
LOW_RISK_TLDS = frozenset({"pl"})
_VOWELS = set("aeiouy")


def tld_risk(suffix: str) -> str:
    last = suffix.rsplit(".", 1)[-1].lower()
    if last in HIGH_RISK_TLDS:
        return "high"
    if last in LOW_RISK_TLDS:
        return "low"
    return "other"


def entropy(text: str) -> float:
    counts = Counter(text)
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values()) if text else 0.0


def shape(label: str) -> tuple[str, dict]:
    """(shape, metrics) for the registrable label without its suffix."""
    letters = re.sub(r"[^a-z]", "", label.lower())
    kinds = ["v" if c in _VOWELS else "c" for c in letters]
    pairs = list(pairwise(kinds))
    alternation = sum(a != b for a, b in pairs) / len(pairs) if pairs else 0.0
    runs = re.findall(r"[^aeiou]+", letters)  # `y` counts as a consonant here
    longest_run = max((len(r) for r in runs), default=0)
    metrics = {
        "label": label,
        "length": len(label),
        "entropy": round(entropy(label), 3),
        "cv_alternation": round(alternation, 3),
        "longest_consonant_run": longest_run,
    }
    if "-" in label and re.search(r"\d", label):
        return "hyphen_digits", metrics
    if len(letters) >= 10 and alternation >= 0.9:
        return "generated_pronounceable", metrics
    if longest_run >= 5:
        return "generated", metrics
    return "plain", metrics


@extractors.register
class DomainShape:
    name = "domain.shape"
    version = "1"

    def run(self, view: View, ctx: Context) -> list[Observation]:
        if view.kind is not ViewKind.PAGE:
            return []
        domain = own_domain(view)
        if not domain:
            return []
        parts = _tld(domain)
        if not parts.domain or not parts.suffix:
            return []
        kind, metrics = shape(parts.domain)

        def obs(signal: str, namespace: str, value: str, raw: str, purpose: Purpose):
            return Observation(
                view_id=view.view_id,
                signal_id=signal,
                namespace=namespace,
                value=value,
                raw=raw,
                purpose=purpose,
                extractor=self.name,
                version=self.version,
            )

        return [
            obs("C4", "domain.tld_risk", tld_risk(parts.suffix), domain, Purpose.RISK),
            obs("C5", "domain.name_shape", kind, json.dumps(metrics), Purpose.RISK),
        ]
