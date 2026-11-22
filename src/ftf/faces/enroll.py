"""Load the closed reference list into the private face service.

Photos come from the licensed manifest, are sent to the service and are not kept on disk or in
the repository. The first photo anchors a person; later ones must match it.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .client import FaceClient, FaceServiceError, _read_state, write_state

MANIFEST = Path(__file__).with_name("reference_people.json")
MAX_PHOTO_BYTES = 5 * 1024 * 1024
SAME_PERSON_SIMILARITY = 0.85
USER_AGENT = "ftf-reference-builder/0.1 (non-commercial research)"


def load_manifest(path: Path = MANIFEST) -> dict[str, list[dict[str, str]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {name: entry["files"] for name, entry in data["people"].items()}


def download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise ValueError("photo is too large")
    return data


def _matches(client: FaceClient, person: str, photo: bytes) -> str | None:
    """Why this photo cannot be added for this person, or None when it can."""
    faces = client.recognize(photo)
    if len(faces) != 1:
        return f"{len(faces)} faces"
    subjects = faces[0].get("subjects") or []
    best = subjects[0] if subjects else {}
    if best.get("subject") != person:
        return f"closest to {best.get('subject')}"
    if float(best.get("similarity", 0.0)) < SAME_PERSON_SIMILARITY:
        return "does not match the first photo"
    return None


def enroll(
    client: FaceClient,
    people: dict[str, list[dict[str, str]]] | None = None,
    fetch: Callable[[str], bytes] = download,
) -> dict[str, Any]:
    """Add missing photos. Safe to run again: enrolled photos are remembered."""
    people = people if people is not None else load_manifest()
    state = _read_state()
    enrolled: dict[str, dict[str, str]] = state.get("enrolled", {})
    report: dict[str, Any] = {"added": 0, "already": 0, "skipped": []}
    for person, files in people.items():
        done = enrolled.setdefault(person, {})
        for file in files:
            title = file["title"]
            if title in done:
                report["already"] += 1
                continue
            try:
                photo = fetch(file["url"])
                # Only the first photo may define who this person is.
                problem = _matches(client, person, photo) if done else _single_face(client, photo)
                if problem:
                    report["skipped"].append({"person": person, "title": title, "why": problem})
                    continue
                done[title] = client.add_example(person, photo)
                report["added"] += 1
            except (FaceServiceError, urllib.error.URLError, OSError, ValueError, KeyError) as exc:
                why = getattr(exc, "reason", None) or type(exc).__name__
                report["skipped"].append({"person": person, "title": title, "why": str(why)[:120]})
            write_state({**state, "enrolled": enrolled})
    write_state({**state, "enrolled": enrolled})
    report["people_ready"] = sorted(p for p in people if enrolled.get(p))
    report["people_missing"] = sorted(p for p in people if not enrolled.get(p))
    return report


def _single_face(client: FaceClient, photo: bytes) -> str | None:
    faces = client.recognize(photo)
    return None if len(faces) == 1 else f"{len(faces)} faces"
