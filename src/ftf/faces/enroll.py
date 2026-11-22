"""Load the closed reference list into the private face service.

Photos ship unmodified in `reference_photos/`, with author and licence in the manifest, so setup
needs no download. A photo missing from the package is fetched from its source. The first photo
anchors a person; later ones must match it.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .client import FaceClient, FaceServiceError, _read_state, write_state

MANIFEST = Path(__file__).with_name("reference_people.json")
PHOTOS = Path(__file__).with_name("reference_photos")
MAX_PHOTO_BYTES = 5 * 1024 * 1024
DOWNLOAD_ATTEMPTS = 4
DOWNLOAD_PAUSE_SECONDS = 2.0
SAME_PERSON_SIMILARITY = 0.85
USER_AGENT = "ftf-reference-builder/0.1 (non-commercial research)"


def load_manifest(path: Path = MANIFEST) -> dict[str, list[dict[str, str]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {name: entry["files"] for name, entry in data["people"].items()}


def download(url: str) -> bytes:
    """Fetch one photo, politely: Wikimedia rate-limits fast clients."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(DOWNLOAD_ATTEMPTS):
        time.sleep(DOWNLOAD_PAUSE_SECONDS)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read(MAX_PHOTO_BYTES + 1)
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == DOWNLOAD_ATTEMPTS - 1:
                raise
            time.sleep(min(int(exc.headers.get("Retry-After") or 30), 60))
    if len(data) > MAX_PHOTO_BYTES:
        raise ValueError("photo is too large")
    return data


def read_photo(file: dict[str, str], fetch: Callable[[str], bytes] = download) -> bytes:
    """The packaged photo when it is intact, otherwise the one at its source."""
    if file.get("photo"):
        try:
            data = (PHOTOS / Path(file["photo"]).name).read_bytes()
        except OSError:
            data = b""
        if hashlib.sha256(data).hexdigest() == file.get("sha256"):
            return data
    return fetch(file["url"])


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
    report: dict[str, Any] = {"added": 0, "already": 0, "removed": 0, "skipped": []}
    # The list is closed: anything no longer on it must stop matching.
    for person in list(enrolled):
        wanted = {file["title"] for file in people.get(person, [])}
        for title, image_id in list(enrolled[person].items()):
            if title not in wanted:
                client.delete_example(image_id)
                del enrolled[person][title]
                report["removed"] += 1
        if not enrolled[person]:
            del enrolled[person]
    write_state({**state, "enrolled": enrolled})
    for person, files in people.items():
        done = enrolled.setdefault(person, {})
        for file in files:
            title = file["title"]
            if title in done:
                report["already"] += 1
                continue
            try:
                photo = read_photo(file, fetch)
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
