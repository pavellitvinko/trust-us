"""Video inspection on demand, using the normal request threads and a file cache."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException

from ..contracts import State, ViewKind
from ..models.redact import redact_text
from ..store import Store
from .inspection import artifact_path, artifact_ref, get_view


def video_path(store: Store, view_id: str) -> Path:
    view = get_view(store, view_id)
    if view.kind is not ViewKind.VIDEO or not view.primary:
        raise HTTPException(404, "This item has no original video")
    return artifact_path(store, view_id, artifact_ref(view.primary))[0]


def identity(path: Path) -> str:
    stat = path.stat()
    return hashlib.sha256(f"{path}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()[:24]


@lru_cache(maxsize=128)
def _probe(path: str, key: str) -> dict:
    try:
        done = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-protocol_whitelist",
                "file",
                "-show_entries",
                "format=duration:stream=codec_name,codec_type,pix_fmt",
                "-of",
                "json",
                f"file:{path}",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        data = json.loads(done.stdout)
    except FileNotFoundError:
        raise HTTPException(
            503, "FFmpeg/ffprobe is not installed. Download the original video."
        ) from None
    except (OSError, subprocess.SubprocessError, ValueError):
        raise HTTPException(422, "The video could not be read, or probing timed out.") from None
    streams = data.get("streams", [])
    videos = [s for s in streams if s.get("codec_type") == "video"]
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    if not videos:
        raise HTTPException(422, "No readable video track was found.")
    try:
        duration = float(data.get("format", {}).get("duration", "nan"))
    except (ValueError, TypeError):
        duration = float("nan")
    compatible = (
        Path(path).suffix.lower() == ".mp4"
        and videos[0].get("codec_name") == "h264"
        and videos[0].get("pix_fmt") in ("yuv420p", "yuvj420p")
        and all(s.get("codec_name") == "aac" for s in audios)
    )
    return {
        "duration": duration if math.isfinite(duration) and duration > 0 else None,
        "compatible": compatible,
    }


def converted_path(path: Path, data_dir: Path) -> Path:
    return data_dir / "media" / f"{identity(path)}.mp4"


def metadata(store: Store, view_id: str, data_dir: Path) -> dict:
    observations = store.observations(view_id)
    video_rows = [o for o in observations if o.namespace.startswith("video.")]
    available = [o for o in video_rows if o.state is State.PRESENT and o.value is not None]
    analysis = "not_run" if not video_rows else "available" if available else "unavailable"
    scenes = []
    for o in available:
        if o.namespace == "video.scene" and (
            match := re.fullmatch(r"video:t=(\d+(?:\.\d+)?)s", o.source.locator or "")
        ):
            seconds = float(match[1])
            if math.isfinite(seconds):
                scenes.append({"seconds": seconds, "text": redact_text(str(o.value))})
    scenes.sort(key=lambda s: s["seconds"])
    result = {
        "analysis": analysis,
        "analysisReason": next(
            (redact_text(o.raw or "") for o in video_rows if o.state is State.NOT_OBSERVABLE), ""
        ),
        "transcript": "\n\n".join(
            redact_text(str(o.value)) for o in available if o.namespace == "video.transcript"
        ),
        "onScreen": [
            redact_text(str(o.value)) for o in available if o.namespace == "video.on_screen_text"
        ],
        "scenes": scenes,
        "frames": [],
        "available": False,
        "error": None,
        "compatible": False,
        "prepared": False,
        "duration": None,
        "download": None,
    }
    try:
        path = video_path(store, view_id)
        view = get_view(store, view_id)
        result.update(
            available=True,
            download=(
                f"/api/items/{quote(view_id, safe='')}/artifacts/{artifact_ref(view.primary)}"
            ),
        )
        result.update(_probe(str(path), identity(path)))
        result["prepared"] = converted_path(path, data_dir).is_file()
    except HTTPException as exc:
        result["error"] = exc.detail
        return result
    duration = result["duration"]
    times = sorted({s["seconds"] for s in scenes if not duration or s["seconds"] < duration})
    if len(times) > 12:
        times = [times[round(i * (len(times) - 1) / 11)] for i in range(12)]
    if not times:
        times = (
            [(i + 0.1) * duration / 8 for i in range(8)]
            if duration
            else [0.0, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0]
        )
    result["frames"] = [{"seconds": round(t, 3), "sampled": not bool(scenes)} for t in times]
    return result


def prepare(path: Path, data_dir: Path) -> Path:
    from ..models.gateway import TransientModelError
    from ..models.gemini import _convert_to_mp4

    target = converted_path(path, data_dir)
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as folder:
        tmp = Path(folder) / "playback.mp4"
        try:
            _convert_to_mp4(path, tmp)
        except TransientModelError:
            raise HTTPException(
                422, "Compatible playback could not be prepared. Check FFmpeg and retry."
            ) from None
        os.replace(tmp, target)
    return target


def frame(path: Path, data_dir: Path, seconds: float) -> Path:
    from ..perception.techniques import _MediaError, _video_frame

    target = data_dir / "media" / f"{identity(path)}-{seconds:.3f}.jpg"
    if target.is_file():
        return target
    try:
        image = _video_frame(path, seconds)
    except _MediaError:
        raise HTTPException(422, "Frame unavailable. Check FFmpeg or retry.") from None
    if image is None:
        raise HTTPException(422, "No frame could be read at this time.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as tmp:
        temp = Path(tmp.name)
        try:
            tmp.write(image)
            tmp.close()
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
    return target
