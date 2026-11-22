"""Key frames and container metadata of a video view (signals M1, M5).

M1: frames are picked where the scene changes, plus the first frame and one frame per slot
of a fixed time grid, so a video without hard cuts is still sampled. The grid is absolute
(every 2, 4, 8 or 16 s from the start, by length), so two copies of one video sample the
same moments, and grids of different steps share every other point. Each frame gets the same
grayscale pHash as page images (`asset.phash`), under `video.frame_phash`; the `media.video`
layer pairs them by Hamming distance, so a re-encoded or rescaled copy still matches. Near
duplicates are kept: a single-shot video (a talking head) samples to frames a few bits apart,
and collapsing them would leave it with one hash, too few for the layer. Only equal hashes
are merged. On the sample, frames of different videos are 20 bits or more apart.

M5: ffprobe's container facts: encoder and handler strings, codec, duration, resolution,
frame rate and creation time. Weak and often about the recording device rather than the
operator (a screen recording carries the analyst's phone and the moment of capture), so they
are written with purpose `risk`, which never drives grouping: evidence to read.

The file is untrusted. ffmpeg and ffprobe may only read local files, every call has a
timeout, and frames are capped in number and size. Without ffmpeg on the machine, or when
the frames cannot be read, the view gets a `not_observable` row saying why instead of
silence. Frames written before ffmpeg failed on a damaged file are kept.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..contracts import Observation, Purpose, Source, State, View, ViewKind
from ..registry import Context, extractors
from . import _material as m
from .assets import phash

NAME = "video.media"
VERSION = "1"
TIMEOUT_SECONDS = 180
SCENE_THRESHOLD = 0.3  # ffmpeg scene score: 0 same picture, 1 a different one
MAX_FRAMES = 120  # a safety cap for pathological input (a flashing video cuts every frame)
MIN_INTERVAL = 2.0  # seconds; the finest grid step
TARGET_FRAMES = 24  # the step doubles on long videos to stay near this many grid frames
FRAME_WIDTH = 320

# showinfo prints `n:<index> pts:<pts> pts_time:<seconds>`; a start before 0 (an MP4 edit
# list) gives a negative time, and the index pairs each time with its frame file.
_SHOWINFO = re.compile(r"\bn:\s*(\d+)\b.*?\bpts_time:\s*(-?[0-9.]+(?:e[-+]?\d+)?)")


class _NoTool(Exception):
    """ffmpeg or ffprobe is not installed."""


def _run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    if shutil.which(command[0]) is None:
        raise _NoTool(command[0])
    return subprocess.run(command, check=True, capture_output=True, timeout=TIMEOUT_SECONDS,
                          **kwargs)  # fmt: skip


def probe(path: Path) -> dict[str, Any] | None:
    """ffprobe's format and streams, or None when the file is not a readable video."""
    command = [
        "ffprobe", "-v", "error", "-protocol_whitelist", "file",
        "-print_format", "json", "-show_format", "-show_streams", f"file:{path.resolve()}",
    ]  # fmt: skip
    try:
        done = _run(command)
        # tags are not always UTF-8 (a Latin-1 title); they must not cost the frames
        data = json.loads(done.stdout.decode("utf-8", errors="replace"))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _interval(duration: float | None) -> float:
    """The grid step: MIN_INTERVAL doubled until the grid holds about TARGET_FRAMES."""
    step = MIN_INTERVAL
    while duration and duration / step > TARGET_FRAMES:
        step *= 2
    return step


def key_frames(path: Path, duration: float | None) -> list[tuple[float, bytes]]:
    """(seconds, JPEG bytes) of the frames picked by scene change or the interval."""
    every = _interval(duration)
    pick = (
        f"select='eq(n\\,0)+gt(scene\\,{SCENE_THRESHOLD})"
        f"+gt(floor(t/{every:g})\\,floor(prev_t/{every:g}))'"
    )
    with tempfile.TemporaryDirectory(prefix="ftf-frames-") as scratch:
        command = [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "info",
            "-protocol_whitelist", "file", "-i", f"file:{path.resolve()}",
            "-an", "-sn", "-dn",
            "-vf", f"{pick},scale='min({FRAME_WIDTH},iw)':-2,showinfo",
            "-fps_mode", "vfr", "-frames:v", str(MAX_FRAMES), "-q:v", "3",
            f"{scratch}/frame_%04d.jpg",
        ]  # fmt: skip
        try:
            log = _run(command).stderr
        except subprocess.CalledProcessError as exc:  # a damaged file: keep what was written
            log = exc.stderr
        except subprocess.TimeoutExpired as exc:
            log = exc.stderr
        text = (log or b"").decode("utf-8", errors="replace")
        times = {int(n): float(t) for n, t in _SHOWINFO.findall(text)}
        files = sorted(Path(scratch).glob("frame_*.jpg"))
        # frame_0001.jpg is showinfo's n:0
        return [(times.get(i, float("nan")), file.read_bytes()) for i, file in enumerate(files)]


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _rate(text: Any) -> float | None:
    if not isinstance(text, str) or "/" not in text:
        return _number(text)
    top, _, bottom = text.partition("/")
    top_n, bottom_n = _number(top), _number(bottom)
    return top_n / bottom_n if top_n and bottom_n else None


def container_facts(info: dict[str, Any]) -> list[tuple[str, Any, str]]:
    """(namespace, value, ffprobe locator) for M5, from ffprobe's JSON."""
    fmt = info.get("format") or {}
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None) or {}
    vtags = {k.lower(): v for k, v in (video.get("tags") or {}).items()}
    out: list[tuple[str, Any, str]] = []
    encoders = []
    for where, value in (("format.tags.encoder", tags.get("encoder")),
                         ("streams[video].tags.encoder", vtags.get("encoder"))):  # fmt: skip
        if isinstance(value, str) and value.strip() and value.strip() not in encoders:
            encoders.append(value.strip())
            out.append(("video.encoder", value.strip(), where))
    handler = vtags.get("handler_name")
    if isinstance(handler, str) and handler.strip():
        out.append(("video.handler", handler.strip(), "streams[video].tags.handler_name"))
    if video.get("codec_name"):
        out.append(("video.codec", str(video["codec_name"]).lower(), "streams[video].codec_name"))
    brand = tags.get("major_brand")
    if isinstance(brand, str) and brand.strip():
        out.append(("video.container", brand.strip().lower(), "format.tags.major_brand"))
    duration = _number(fmt.get("duration"))
    if duration and duration > 0:
        out.append(("video.duration", round(duration, 1), "format.duration"))
    width, height = video.get("width"), video.get("height")
    if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
        out.append(("video.resolution", f"{width}x{height}", "streams[video].width,height"))
    fps = _rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate"))
    if fps and 0 < fps < 1000:
        out.append(("video.fps", round(fps, 2), "streams[video].avg_frame_rate"))
    created = tags.get("creation_time") or vtags.get("creation_time")
    if isinstance(created, str) and created.strip():
        out.append(("video.created_at", created.strip(), "format.tags.creation_time"))
    return out


def _not_observable(view: View, signal: str, namespace: str, why: str) -> Observation:
    return Observation(
        view_id=view.view_id,
        signal_id=signal,
        namespace=namespace,
        raw=why,
        state=State.NOT_OBSERVABLE,
        source=Source(file=view.primary, locator="ffmpeg"),
        extractor=NAME,
        version=VERSION,
    )


@extractors.register
class VideoMedia:
    name = NAME
    version = VERSION

    def run(self, view: View, ctx: Context) -> list[Observation]:
        if view.kind is not ViewKind.VIDEO or not view.primary:
            return []
        path = ctx.path(view, view.primary)

        def unavailable(why: str) -> list[Observation]:
            return [
                _not_observable(view, "M5", "video.encoder", why),
                _not_observable(view, "M1", "video.frame_phash", why),
            ]

        try:
            info = probe(path)
            if info is None:
                return unavailable("not a readable video")
            duration = _number((info.get("format") or {}).get("duration"))
            frames = key_frames(path, duration)
        except _NoTool as exc:
            return unavailable(f"{exc} is not installed")
        except OSError as exc:  # ffmpeg not runnable, disk full: an extractor never raises
            return unavailable(f"ffmpeg could not run: {exc.strerror or exc}")
        emit = m.Emitter(view, self.name, self.version)
        for namespace, value, locator in container_facts(info):
            emit("M5", namespace, value, Source(file=view.primary, locator=f"ffprobe:{locator}"),
                 raw=str(value), purpose=Purpose.RISK)  # fmt: skip
        hashed = 0
        for seconds, data in frames:  # the emitter merges equal hashes, the first time wins
            value = phash(data)
            if not value:
                continue
            hashed += 1
            at = f"{seconds:.2f}s" if math.isfinite(seconds) else "?"
            emit("M1", "video.frame_phash", value,
                 Source(file=view.primary, locator=f"video:frame@{at}"), raw=at)  # fmt: skip
        if not hashed:
            emit.out.append(_not_observable(view, "M1", "video.frame_phash", "no frame decoded"))
        return emit.out
