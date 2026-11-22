"""Possible matches between faces in the material and a closed list of public figures.

Runs from `ftf perceive` when a face service is configured. A match is risk evidence that an
analyst must verify. It never enters campaign linking, and unmatched faces are not stored.
"""

from __future__ import annotations

from typing import Any

from ..contracts import Observation, Purpose, Source, State, View, ViewKind
from ..extract import _material
from ..faces.client import FaceClient, FaceServiceError, service_client
from ..registry import Context
from .techniques import _Image, _MediaError, _page_images, _read_image, _video_images

NAME = "perception.faces"
VERSION = "1"
SIGNAL = "G1"
NAMESPACE = "face.reference"
MATCH_SIMILARITY = 0.95
MAX_IMAGES_PER_VIEW = 6
MAX_SERVICE_IMAGE_BYTES = 5 * 1024 * 1024  # the service's own upload limit
NOTE = "possible match, needs verification"


def _row(
    view: View, value: Any, state: State, raw: str, source: Source, conf: float
) -> Observation:
    return Observation(
        view_id=view.view_id,
        signal_id=SIGNAL,
        namespace=NAMESPACE,
        value=value,
        raw=raw,
        state=state,
        purpose=Purpose.RISK,  # risk-only observations never drive grouping
        source=source,
        extractor=NAME,
        version=VERSION,
        confidence=conf,
    )


def _unavailable(view: View, reason: str) -> list[Observation]:
    source = Source(file=view.primary or view.har, locator="faces")
    return [_row(view, None, State.NOT_OBSERVABLE, reason, source, 0.0)]


def view_images(view: View, ctx: Context) -> list[_Image]:
    """Images small enough for the service. A file that cannot be read raises `_MediaError`."""
    if view.kind is ViewKind.IMAGE:
        if view.primary is None:
            return []
        image = _read_image(ctx.path(view, view.primary), view.primary)
        images = [image] if image is not None else []
    elif view.kind is ViewKind.VIDEO:
        images = _video_images(view, ctx)
    else:
        images, failed = _page_images(view, ctx, _material.load(view, ctx))
        if failed:
            raise _MediaError("some page images could not be read")
    return [i for i in images if len(i.data) <= MAX_SERVICE_IMAGE_BYTES][:MAX_IMAGES_PER_VIEW]


def check_view(
    view: View, ctx: Context, client: FaceClient
) -> tuple[list[Observation], str | None]:
    try:
        images = view_images(view, ctx)
    except (_MediaError, OSError):
        return _unavailable(view, "media_error"), "media_error"
    if not images:
        return _unavailable(view, "no_image"), "no_image"
    best: dict[str, tuple[float, _Image, dict[str, Any]]] = {}
    rejected = 0
    for image in images:
        try:
            faces = client.recognize(image.data)
        except FaceServiceError as exc:
            reason = exc.reason.split(":", 1)[0]
            if reason in ("http_400", "http_413", "http_415"):  # this image only
                rejected += 1
                continue
            return _unavailable(view, reason), reason
        for face in faces:
            subjects = face.get("subjects") or []
            if not subjects:
                continue
            person, score = subjects[0].get("subject"), float(subjects[0].get("similarity", 0.0))
            if person and score >= MATCH_SIMILARITY and score > best.get(person, (0.0,))[0]:
                best[person] = (score, image, face.get("box") or {})
    if not best and rejected:  # an unread image cannot support "no match"
        return _unavailable(view, "image_rejected"), "image_rejected"
    if not best:
        source = Source(file=view.primary or view.har, locator="faces")
        checked = f"{len(images)} image(s) checked: no reference match"
        return [_row(view, None, State.ABSENT, checked, source, 1.0)], None
    rows = []
    for person, (score, image, box) in sorted(best.items()):
        corners = ",".join(str(box.get(k)) for k in ("x_min", "y_min", "x_max", "y_max"))
        source = Source(file=image.file, locator=f"{image.locator_prefix}image:box[{corners}]")
        value = {"person": person, "similarity": round(score, 4)}
        rows.append(_row(view, value, State.PRESENT, NOTE, source, round(score, 4)))
    return rows, None


def run_perceive_faces(
    ctx: Context, view_ids: list[str] | None = None, *, client: FaceClient | None = None
) -> dict[str, Any]:
    """Check views. A service failure never replaces an earlier verified result."""
    client = client or service_client()
    summary: dict[str, Any] = {"views": 0, "matched": 0, "no_match": 0, "unavailable": 0, "kept": 0}
    if client is None:
        return {**summary, "status": "not_configured"}
    for view in ctx.store.views():
        if view.meta.get("shipped") or (view_ids is not None and view.view_id not in view_ids):
            continue
        summary["views"] += 1
        rows, reason = check_view(view, ctx, client)
        earlier = any(
            o.extractor == NAME and o.state is not State.NOT_OBSERVABLE
            for o in ctx.store.observations(view.view_id)
        )
        if reason and reason != "no_image" and earlier:
            summary["kept"] += 1
            continue
        ctx.store.put_observations(view.view_id, NAME, rows)
        if reason:
            summary["unavailable"] += 1
        elif rows[0].state is State.PRESENT:
            summary["matched"] += 1
        else:
            summary["no_match"] += 1
    return {**summary, "status": "ok"}
