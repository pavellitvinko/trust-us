"""HAC-11 asset hashes on synthetic files: roles, exact and perceptual hashes."""

import base64
import hashlib
import io
import json
import random

import pytest
from PIL import Image, ImageDraw

from ftf.extract import _material
from ftf.extract.assets import AssetHashes, Role, classify, name_shape, phash
from ftf.ingest import ingest_folder
from ftf.registry import Context

from .test_extract_identifiers import page, values


@pytest.fixture(autouse=True)
def _fresh_cache():
    _material.clear_cache()


def picture(seed: int = 0, size=(400, 300), fmt="PNG", **save) -> bytes:
    """A deterministic picture with structure, so a perceptual hash has something to see."""
    rnd = random.Random(seed)
    w, h = size
    img = Image.new("RGB", size, tuple(rnd.randrange(256) for _ in range(3)))
    draw = ImageDraw.Draw(img)
    for _ in range(14):
        x0, y0 = rnd.randrange(w), rnd.randrange(h)
        box = [x0, y0, x0 + rnd.randrange(w // 8, w // 2), y0 + rnd.randrange(h // 8, h // 2)]
        fill = tuple(rnd.randrange(256) for _ in range(3))
        (draw.ellipse if rnd.random() < 0.5 else draw.rectangle)(box, fill=fill)
    buf = io.BytesIO()
    img.save(buf, fmt, **save)
    return buf.getvalue()


def hue_shift(data: bytes, degrees: int) -> bytes:
    img = Image.open(io.BytesIO(data)).convert("HSV")
    h, s, v = img.split()
    h = h.point(lambda x: (x + round(degrees / 360 * 255)) % 256)
    out = io.BytesIO()
    Image.merge("HSV", (h, s, v)).convert("RGB").save(out, "PNG")
    return out.getvalue()


def reencode(data: bytes, quality: int = 60) -> bytes:
    out = io.BytesIO()
    Image.open(io.BytesIO(data)).convert("RGB").save(out, "JPEG", quality=quality)
    return out.getvalue()


def distance(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


# --- roles -------------------------------------------------------------------------------

CODE = b"body { color: red; } " * 10


@pytest.mark.parametrize(
    ("name", "data", "role"),
    [
        ("jquery-3.6.0.min_ff1523fb.js", CODE, Role.NOISE),
        ("fbevents.js.pobrane", CODE, Role.NOISE),
        ("saved_resource(1).html", CODE, Role.NOISE),
        ("2012165449475163", picture(), Role.NOISE),  # a pixel saved under its ID
        ("tiny.gif", b"GIF89a", Role.NOISE),
        ("main.css", CODE, Role.CODE),
        ("main.js.pobrane", CODE, Role.CODE),
        ("logo.svg", b"<svg xmlns='http://www.w3.org/2000/svg'>" + b" " * 100 + b"</svg>", Role.ICON),
        ("thing.png", picture(size=(32, 32)), Role.ICON),
        ("brzoska-avatar.jpg", picture(fmt="JPEG"), Role.AVATAR),
        ("ava-coment10.jpeg", picture(fmt="JPEG"), Role.AVATAR),
        ("01-hero.webp", picture(fmt="WEBP"), Role.CONTENT),
        ("font.woff2", b"\x00" * 200, Role.NOISE),
    ],
)  # fmt: skip
def test_classify(name, data, role):
    assert classify(name, data) is role


def test_noise_host_is_noise():
    assert classify("x.png", picture(), host="www.facebook.com") is Role.NOISE


@pytest.mark.parametrize(
    ("name", "shape"),
    [
        ("01-hero.webp", "9-a.webp"),
        ("news1.webp", "a9.webp"),
        ("ava-coment10.jpeg", "a-a9.jpeg"),
        ("shot-pko.webp.pobrane", "a-a.webp"),
        ("photo.png", None),
        ("noext", None),
    ],
)
def test_name_shape(name, shape):
    assert name_shape(name) == shape


# --- perceptual hash ---------------------------------------------------------------------


@pytest.mark.parametrize("seed", [1, 2, 3, 7, 9])
def test_phash_survives_hue_shift_and_reencode(seed):
    """The sample's pages use `hue-rotate(6–8deg)`; 15° leaves a margin.

    Large rotations of flat saturated colours change luminance itself, which no grayscale
    hash can ignore; that is what the image-embedding layer (E3) is for.
    """
    base = picture(seed)
    h = phash(base)
    assert h is not None and len(h) == 16
    assert distance(h, phash(hue_shift(base, 8))) <= 8
    assert distance(h, phash(hue_shift(base, 15))) <= 10
    assert distance(h, phash(reencode(base))) <= 6
    assert distance(h, phash(reencode(hue_shift(base, 8), 50))) <= 10
    others = [distance(h, phash(picture(s))) for s in range(12) if s != seed]
    assert min(others) >= 16  # different pictures stay far apart


def test_phash_of_junk_is_none():
    assert phash(b"not an image") is None


# --- the extractor -----------------------------------------------------------------------


def run_page(store, root):
    _, views = ingest_folder(root, store)
    view = next(v for v in views if v.primary == "page.html")
    return AssetHashes().run(view, Context(store))


def write_files(root, files: dict[str, bytes]):
    folder = root / "page_files"
    folder.mkdir(exist_ok=True)
    for name, data in files.items():
        (folder / name).write_bytes(data)


def test_page_assets_by_role(store, tmp_path):
    root = page(tmp_path / "v")
    hero, ava = picture(1, fmt="WEBP"), picture(2, fmt="JPEG")
    write_files(
        root,
        {
            "01-hero.webp": hero,
            "brzoska-avatar.jpg": ava,
            "main.css": CODE,
            "jquery.min.js.pobrane": CODE + b"jq",
        },
    )
    obs = run_page(store, root)
    sha = lambda b: hashlib.sha256(b).hexdigest()
    assert values(obs, "asset.sha256.content") == [sha(hero)]
    assert values(obs, "asset.sha256.avatar") == [sha(ava)]
    assert values(obs, "asset.sha256.code") == [sha(CODE)]
    assert values(obs, "asset.sha256.noise") == [sha(CODE + b"jq")]
    assert set(values(obs, "asset.phash")) == {phash(hero), phash(ava)}
    assert "9-a.webp" in values(obs, "asset.name_shape")


def test_har_body_matches_the_saved_file(store, tmp_path):
    hero = picture(3, fmt="WEBP")
    har = [
        {
            "request": {"url": "https://www.shop.example.org/L1/assets/01_hero.webp?v=2"},
            "response": {
                "status": 200,
                "content": {
                    "mimeType": "image/webp",
                    "encoding": "base64",
                    "text": base64.b64encode(hero).decode(),
                },
            },
        }
    ]
    root = page(tmp_path / "v", har=har)
    obs = run_page(store, root)
    assert values(obs, "asset.sha256.content") == [hashlib.sha256(hero).hexdigest()]
    assert obs[0].source.locator.startswith("har:entries[")


def test_image_view_gets_a_phash(store, tmp_path):
    root = tmp_path / "v"
    root.mkdir()
    data = picture(5)
    (root / "reklama_1.png").write_bytes(data)
    _, views = ingest_folder(root, store)
    view = next(v for v in views if v.kind.value == "image")
    obs = AssetHashes().run(view, Context(store))
    assert values(obs, "asset.phash") == [phash(data)]
    assert values(obs, "asset.sha256.content") == [hashlib.sha256(data).hexdigest()]


def test_broken_inputs_never_raise(store, tmp_path):
    root = page(tmp_path / "v", har=[])
    (root / "www.shop.example.org.har").write_text("{not json")
    write_files(root, {"broken.png": b"\x89PNG" + b"\x00" * 200, "x.webp": b"RIFF" * 50})
    obs = run_page(store, root)
    assert isinstance(obs, list)
    assert not values(obs, "asset.phash")
    json.dumps([o.model_dump() for o in obs])  # serialisable
