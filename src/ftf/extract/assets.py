"""Exact and perceptual hashes of page assets (signals D3, E1, E2, E5, E12).

Every asset gets a role, and the role is part of the namespace: `asset.sha256.content`,
`.avatar`, `.icon`, `.code` and `.noise`. Common libraries, browser "saved resource"
stubs and tracking pixels land in `.noise`: recorded, never linked on.

The perceptual hash is computed on grayscale, so a `hue-rotate` filter or a re-encode
does not break it. Sources: the page's `_files/` folder, image, script and style bodies
in its HAR, and the file of an image view (screenshot or ad creative).
"""

from __future__ import annotations

import base64
import hashlib
import io
import re
import warnings
from enum import StrEnum
from pathlib import Path

import imagehash
from PIL import Image

from ..contracts import Observation, Source, View, ViewKind
from ..ingest.har import is_noise_host, load_har
from ..registry import Context, extractors
from . import _material as m

MAX_BYTES = 20 << 20
MAX_PIXELS = 50_000_000
MIN_BYTES = 64  # smaller files are pixels and placeholders
ICON_SIDE = 64
PIXEL_SIDE = 2  # tracking pixels are 1x1, sometimes 2x2
PLATFORM_ID_DIGITS = 12  # a file named by an ad-platform ID (pixel config), not `1.jpg`

_RASTER = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif")
_VECTOR_ICON = (".svg", ".ico")
_CODE = (".css", ".js", ".mjs")
_LIBRARY = re.compile(  # whole words only: `reaction-2020.png` is not React
    r"(?<![a-z])(?:jquery|bootstrap|swiper|slick|lodash|react|vue|gsap|font-?awesome|popper|"
    r"fbevents|gtag|gtm|analytics|recaptcha|sdk|polyfill)(?![a-z])",
    re.IGNORECASE,
)
_STUB = re.compile(r"^saved_resource(\(\d+\))?\.html?$", re.IGNORECASE)
_ICON_NAME = re.compile(r"logo|icon|favicon|nav-|arrow|sprite|flag|badge|star", re.IGNORECASE)
_AVATAR_NAME = re.compile(r"\bava\b|ava[-_\d]|avatar|user|comment|author", re.IGNORECASE)


class Role(StrEnum):
    CONTENT = "content"
    AVATAR = "avatar"
    ICON = "icon"
    CODE = "code"
    NOISE = "noise"


# When sources disagree about one file, the least linkable role wins.
_PRECEDENCE = {Role.NOISE: 0, Role.ICON: 1, Role.CODE: 2, Role.AVATAR: 3, Role.CONTENT: 4}


def clean_name(name: str) -> str:
    """File name without the browser's save suffix (`main.js.pobrane` -> `main.js`)."""
    return m._strip_save_suffix(Path(name).name)


def _image(data: bytes) -> Image.Image | None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(data))
            if img.width * img.height > MAX_PIXELS:
                return None
            img.load()
        return img
    except Exception:  # noqa: BLE001 — not an image we can read
        return None


def classify(name: str, data: bytes, host: str = "", mime: str = "") -> Role:
    """The role of one asset, from its name, bytes, host and declared type."""
    clean = clean_name(name).lower()
    stem, ext = Path(clean).stem, Path(clean).suffix
    if (
        len(data) < MIN_BYTES
        or (host and is_noise_host(host))
        or _STUB.match(clean)
        or ext in (".html", ".htm")
        or (stem.isdigit() and len(stem) >= PLATFORM_ID_DIGITS)
        or _LIBRARY.search(clean)
    ):
        return Role.NOISE
    if ext in _CODE or "css" in mime or "javascript" in mime:
        return Role.CODE
    if ext in _VECTOR_ICON or "svg" in mime or _ICON_NAME.search(stem):
        return Role.ICON
    img = _image(data) if ext in _RASTER or mime.startswith("image/") or not ext else None
    if img is None:
        return Role.NOISE  # neither code nor a readable image: fonts, data, junk
    if img.width <= PIXEL_SIDE and img.height <= PIXEL_SIDE:
        return Role.NOISE
    if img.width <= ICON_SIDE and img.height <= ICON_SIDE:
        return Role.ICON
    if _AVATAR_NAME.search(stem):
        return Role.AVATAR
    return Role.CONTENT


def phash(data: bytes) -> str | None:
    """64-bit DCT hash of the grayscale image, as 16 hex characters."""
    img = _image(data)
    if img is None:
        return None
    try:
        img.seek(0)  # first frame of an animation
    except EOFError:
        pass
    if img.mode in ("P", "PA"):  # palette transparency must go through RGBA first
        img = img.convert("RGBA")
    return str(imagehash.phash(img.convert("L")))


def name_shape(name: str) -> str | None:
    """The naming convention: `01-hero.webp` -> `9-a.webp`, `news1.webp` -> `a9.webp`.

    None for a single-token name (`a.png`), which every site has.
    """
    clean = clean_name(name).lower()
    stem, ext = Path(clean).stem, Path(clean).suffix
    if not stem or not ext:
        return None
    out: list[str] = []
    for c in stem:
        k = "9" if c.isdigit() else "a" if c.isalpha() else c
        if not out or out[-1] != k:
            out.append(k)
    shape = "".join(out)
    if len(shape) < 2:
        return None
    return shape + ext


def _har_bytes(entry: dict) -> tuple[bytes, str] | None:
    content = (entry.get("response") or {}).get("content") or {}
    text = content.get("text")
    if not isinstance(text, str) or not text:
        return None
    mime = str(content.get("mimeType") or "").lower()
    if not (mime.startswith("image/") or "css" in mime or "javascript" in mime):
        return None
    if content.get("encoding") == "base64":
        try:
            return base64.b64decode(text), mime
        except ValueError:
            return None
    return text.encode("utf-8"), mime


@extractors.register
class AssetHashes:
    name = "asset.hash"
    version = "1"

    def run(self, view: View, ctx: Context) -> list[Observation]:
        emit = m.Emitter(view, self.name, self.version)
        found: list[tuple[str, bytes, Source, Role]] = []

        def add(name: str, data: bytes, source: Source, host: str = "", mime: str = "") -> None:
            if data and len(data) <= MAX_BYTES:
                found.append((name, data, source, classify(name, data, host, mime)))

        def image_view() -> None:
            if view.kind is not ViewKind.IMAGE or not view.primary:
                return
            data = ctx.path(view, view.primary).read_bytes()
            if len(data) <= MAX_BYTES:  # a screenshot or creative is content whatever its name
                found.append((view.primary, data, Source(file=view.primary, locator="file:bytes"),
                              Role.CONTENT))  # fmt: skip

        def resources() -> None:
            if view.kind is not ViewKind.PAGE or not view.resources_dir:
                return
            folder = ctx.path(view, view.resources_dir)
            root = ctx.path(view, "")
            for p in sorted(folder.rglob("*")):
                try:
                    if not p.is_file() or p.stat().st_size > MAX_BYTES:
                        continue
                    rel = p.relative_to(root).as_posix()
                    add(p.name, p.read_bytes(), Source(file=rel, locator="file:bytes"))
                except OSError:
                    continue

        def har_bodies() -> None:
            if view.kind is not ViewKind.PAGE or not view.har:
                return
            for i, entry in enumerate(load_har(ctx.path(view, view.har))):
                try:
                    body = _har_bytes(entry)
                    if body is None:
                        continue
                    url = str((entry.get("request") or {}).get("url") or "")
                    name = Path(url.split("?")[0].rstrip("/")).name or "index"
                    loc = f"har:entries[{i}].response.content"
                    add(name, body[0], Source(file=view.har, locator=loc), m.host_of(url), body[1])
                except Exception:  # noqa: BLE001, S112 — one bad entry must not cost the others
                    continue

        m.run_steps([image_view, resources, har_bodies])

        # One role per file, whatever name each source gives it. A library saved into
        # _files/ is still a library if the HAR shows a public CDN served it, and a file
        # one source names as a logo is not linkable content under another name.
        role_of: dict[str, Role] = {}
        for _, data, _, role in found:
            digest = hashlib.sha256(data).hexdigest()
            current = role_of.get(digest)
            if current is None or _PRECEDENCE[role] < _PRECEDENCE[current]:
                role_of[digest] = role
        hashes: dict[str, str | None] = {}
        for name, data, source, _ in found:
            digest = hashlib.sha256(data).hexdigest()
            role = role_of[digest]
            label = clean_name(name)
            signal = {Role.CODE: "D3", Role.AVATAR: "E12"}.get(role, "E1")
            emit(signal, f"asset.sha256.{role}", digest, source, raw=label)
            if role in (Role.CONTENT, Role.AVATAR):
                if digest not in hashes:
                    hashes[digest] = phash(data)
                if hashes[digest]:
                    emit("E2", "asset.phash", hashes[digest], source, raw=label)
            if role is not Role.NOISE and view.kind is ViewKind.PAGE:
                shape = name_shape(name)
                if shape:
                    emit("E5", "asset.name_shape", shape, source, raw=label)
        return emit.out
