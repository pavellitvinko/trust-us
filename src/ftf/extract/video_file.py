"""The automatic video pass: exact file identity and supplied text, with no decoding."""

import hashlib
import json
import mimetypes
from pathlib import Path

from ..contracts import Observation, Purpose, Source, State

NAME = "video.file"
VERSION = "1"
TEXT_KEYS = {
    "caption",
    "title",
    "description",
    "text",
    "message",
    "body",
    "ad_creative_bodies",
    "ad_creative_link_titles",
    "ad_creative_link_descriptions",
}


def read_file(view, ctx):
    rows = []

    def add(namespace, value, file, locator="file:bytes", purpose=Purpose.RISK):
        rows.append(
            Observation(
                view_id=view.view_id,
                signal_id="M1",
                namespace=namespace,
                value=value,
                raw=str(value),
                purpose=purpose,
                source=Source(file=file, locator=locator),
                extractor=NAME,
                version=VERSION,
            )
        )

    if not view.primary:
        return rows
    path = ctx.path(view, view.primary)
    try:
        with path.open("rb") as stream:
            value = hashlib.file_digest(stream, "sha256").hexdigest()
        add("asset.sha256.content", value, view.primary, purpose=Purpose.LINKAGE)
        add("video.file_size_bytes", path.stat().st_size, view.primary)
        add(
            "video.file_type",
            mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            view.primary,
        )
    except OSError:
        return [
            Observation(
                view_id=view.view_id,
                signal_id="M1",
                namespace="video.file_size_bytes",
                state=State.NOT_OBSERVABLE,
                raw="no_file",
                purpose=Purpose.RISK,
                source=Source(file=view.primary),
                extractor=NAME,
                version=VERSION,
            )
        ]

    def text_fields(value, key="", at="$"):
        if isinstance(value, dict):
            for name, child in value.items():
                yield from text_fields(child, name.casefold(), f"{at}.{name}")
        elif isinstance(value, list):
            for index, child in enumerate(value[:200]):
                yield from text_fields(child, key, f"{at}[{index}]")
        elif key in TEXT_KEYS and isinstance(value, str) and value.strip():
            yield at, value[:40_000]

    for name in view.attachments:
        sidecar = ctx.path(view, name)
        try:
            if sidecar.stat().st_size > 1_000_000:
                continue
            if Path(name).suffix.lower() == ".json":
                for locator, text in text_fields(json.loads(sidecar.read_text())):
                    add("video.sidecar_text", text, name, f"json:{locator}")
            elif Path(name).suffix.lower() in (".txt", ".srt", ".vtt"):
                text = sidecar.read_text(errors="replace")[:40_000]
                if text.strip():
                    add("video.sidecar_text", text, name, "text:0")
        except (OSError, ValueError, RecursionError):
            continue
    return rows
