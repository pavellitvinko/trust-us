"""The Meta Ad Library ID written on a screenshot of the Ad Library.

Read from stored `image.text` observations, so existing screenshots gain it without a new
model call. Only an explicitly labelled Library ID counts: an Ads Manager ad ID, a campaign
ID or an unlabelled long number is a different thing, which the Ad Library cannot look up.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from ..contracts import Observation, State

NAMESPACE = "image.text"
MIN_DIGITS, MAX_DIGITS = 13, 17

# "Identyfikator biblioteki", "Identyfikator w bibliotece reklam", "Library ID", "Ad Library ID".
_LABEL = r"(?:identyfikator\s+(?:w\s+)?bibliote(?:ki|ce|ka)(?:\s+reklam)?|(?:ad\s+)?library\s+id)"
_SEP = r"\s*[:#\-\u2013]?\s*"
# Digits, possibly split by the spaces OCR sometimes inserts; never followed by a letter/digit.
_NUMBER = rf"(\d(?:[ \u00a0]?\d){{{MIN_DIGITS - 2},{MAX_DIGITS - 2}}})(?![\w])"
_LABELLED = re.compile(r"(?<![\w])" + _LABEL + _SEP + _NUMBER, re.IGNORECASE)
_LABEL_ONLY = re.compile(r"(?<![\w])" + _LABEL + _SEP + r"$", re.IGNORECASE)
_NUMBER_ONLY = re.compile(r"^\s*" + _NUMBER + r"\s*$")


@dataclass(frozen=True)
class Evidence:
    """Where the ID was read: the OCR text exactly as found and its region on the image."""

    raw: str
    locator: str | None
    file: str | None


@dataclass(frozen=True)
class LibraryId:
    value: str
    evidence: tuple[Evidence, ...]


def _digits(text: str) -> str | None:
    value = re.sub(r"\D", "", text)
    return value if MIN_DIGITS <= len(value) <= MAX_DIGITS else None


def _evidence(*observations: Observation) -> Evidence:
    first = observations[0]
    raw = " ".join((o.raw or str(o.value or "")).strip() for o in observations)
    return Evidence(raw=raw, locator=first.source.locator, file=first.source.file)


def find_library_ids(observations: Iterable[Observation]) -> dict[str, list[Evidence]]:
    """Every labelled Library ID in the OCR, with each place it was read."""
    lines = [o for o in observations if o.namespace == NAMESPACE and o.state is State.PRESENT]
    found: dict[str, list[Evidence]] = {}
    for i, o in enumerate(lines):
        text = o.raw or str(o.value or "")
        hits = [_digits(m.group(1)) for m in _LABELLED.finditer(text)]
        for value in filter(None, hits):
            found.setdefault(value, []).append(_evidence(o))
        # The label on one OCR line and the number alone on the next.
        if not hits and _LABEL_ONLY.search(text) and i + 1 < len(lines):
            nxt = lines[i + 1]
            match = _NUMBER_ONLY.match(nxt.raw or str(nxt.value or ""))
            if match and (value := _digits(match.group(1))):
                found.setdefault(value, []).append(_evidence(o, nxt))
    return found


def read_library_id(observations: Iterable[Observation]) -> LibraryId | None:
    """The one Library ID the screenshot shows, or None when there is none or they conflict."""
    found = find_library_ids(observations)
    if len(found) != 1:
        return None
    ((value, evidence),) = found.items()
    unique = tuple(dict.fromkeys(evidence))  # the same line read twice is one piece of evidence
    return LibraryId(value=value, evidence=unique)
