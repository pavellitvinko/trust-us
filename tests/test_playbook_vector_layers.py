"""Dense-vector layers with calibration, perceptual layers and the playbook layers from model
output (HAC-32, HAC-33, HAC-34). All on fixtures, with the fake embedder: no model call."""

import random

import pytest

from ftf import pipeline
from ftf.compare.vector import Calibration, calibrate, cosine, get_calibration, set_calibration
from ftf.compare.weights import weights
from ftf.contracts import Fingerprint, FingerprintKind, RelationType
from ftf.eval.fixtures import store_from_fixtures
from ftf.fusion import prepare, run_method
from ftf.layers._embedder import FakeEmbedder, get_embedder, set_embedder
from ftf.layers.vectors import mask
from ftf.registry import comparators

NEWS = ("1:page", "3:page", "48:page")


@pytest.fixture(scope="module")
def corpus():
    set_embedder(FakeEmbedder())
    s = store_from_fixtures()
    pipeline.run_fingerprint(s)
    prepare(s)
    yield s
    weights.reset()
    set_embedder(None)
    s.close()


def score(store, a, b, layer):
    e = next(e for e in pipeline.compare_views(store, a, b) if e.layer_id == layer)
    return e.score if e.comparable else None


# -- narrative skeleton and semantic text ----------------------------------------------------


def test_masking_replaces_cast_amounts_and_dates():
    text = (
        "W czwartek Rafał Brzoska wpłacił 1 000 zł do PKO, a Kacper z Wrocławia zarobił 12 400 zł."
    )
    m = mask(text)
    assert "[CELEBRITY]" in m and "[BANK]" in m and "[AMOUNT]" in m and "[DATE]" in m
    assert "[PERSON]" in m and "Brzoska" not in m and "12 400" not in m


def test_same_story_different_cast_is_closer_on_skeleton_than_on_text(corpus):
    """Brzoska, Olejnik and Lis: the skeleton must order them above unrelated pairs and above
    the name-keeping semantic layer. Absolute closeness needs the real embedding model."""
    s = corpus
    emb = get_embedder()
    fps = {f.view_id: f for f in s.fingerprints(layer_id="narrative.skeleton")}
    sem = {f.view_id: f for f in s.fingerprints(layer_id="text.semantic")}
    assert all(fps[v].payload["model"] == emb.model for v in NEWS)
    trio = [("1:page", "3:page"), ("1:page", "48:page"), ("3:page", "48:page")]
    unrelated = [("1:page", "10:page"), ("3:page", "36:page"), ("48:page", "14:page")]
    raw = lambda d, a, b: cosine(d[a].payload["values"], d[b].payload["values"])
    for a, b in trio:
        assert raw(fps, a, b) >= raw(sem, a, b) - 0.02, (a, b)
        assert raw(fps, a, b) > max(raw(fps, x, y) for x, y in unrelated)
    assert min(score(s, a, b, "narrative.skeleton") for a, b in trio) >= max(
        score(s, a, b, "narrative.skeleton") for a, b in unrelated
    )


def test_calibration_is_fitted_from_background_pairs(corpus):
    cal = get_calibration("narrative.skeleton")
    assert cal and cal.pairs > 20 and 0 < cal.mu < 0.6
    assert score(corpus, "1:page", "2:page", "text.semantic") >= 0.95  # near-identical text
    assert score(corpus, "1:page", "10:page", "text.semantic") == 0.0  # at or below background


def test_model_swap_needs_recalibration_not_new_thresholds():
    """Two toy models with different raw scales give the same calibrated picture."""
    cmp = comparators.get("calibrated_cosine")

    def fp(v, vec, model):
        return Fingerprint(
            view_id=v,
            layer_id="toy",
            kind=FingerprintKind.VECTOR,
            payload={"model": model, "values": vec},
            version="t",
        )

    # model A: unrelated pairs sit near cos 0.2; model B: near cos 0.7 (a common embedding trait)
    set_calibration("toy", Calibration(model="A", mu=0.2, sigma=0.05, pairs=10))
    a_related = cmp.compare(fp("x", [1, 0.2], "A"), fp("y", [1, 0.25], "A")).score
    a_unrelated = cmp.compare(fp("x", [1, 0], "A"), fp("y", [0.2, 1], "A")).score
    set_calibration("toy", Calibration(model="B", mu=0.7, sigma=0.05, pairs=10))
    b_related = cmp.compare(fp("x", [1, 0.2], "B"), fp("y", [1, 0.25], "B")).score
    b_unrelated = cmp.compare(fp("x", [1, 0], "B"), fp("y", [0.7, 0.714], "B")).score  # cos 0.7
    set_calibration("toy", None)
    assert a_related > 0.8 and b_related > 0.8
    assert a_unrelated == 0.0 and b_unrelated == 0.0


def test_vectors_of_different_models_are_not_comparable():
    cmp = comparators.get("calibrated_cosine")
    a = Fingerprint(
        view_id="a",
        layer_id="l",
        kind=FingerprintKind.VECTOR,
        payload={"model": "m1", "values": [1, 0]},
        version="t",
    )
    b = Fingerprint(
        view_id="b",
        layer_id="l",
        kind=FingerprintKind.VECTOR,
        payload={"model": "m2", "values": [1, 0]},
        version="t",
    )
    assert not cmp.compare(a, b).comparable


# -- perceptual layers -----------------------------------------------------------------------


def _ahash(pixels):
    """64-bit average hash of an 8x8 grayscale downscale: a stand-in for the Evidence track's pHash."""
    h, w = len(pixels), len(pixels[0])
    cells = []
    for by in range(8):
        for bx in range(8):
            ys = range(by * h // 8, (by + 1) * h // 8)
            xs = range(bx * w // 8, (bx + 1) * w // 8)
            cells.append(sum(pixels[y][x] for y in ys for x in xs) / (len(ys) * len(xs)))
    mean = sum(cells) / 64
    bits = "".join("1" if c > mean else "0" for c in cells)
    return f"{int(bits, 2):016x}"


def _gray(rgb):
    return [[0.299 * r + 0.587 * g + 0.114 * b for r, g, b in row] for row in rgb]


def _image(seed, size=64):
    rng = random.Random(seed)
    blobs = [
        (rng.randint(0, size), rng.randint(0, size), rng.randint(8, 24), rng.randint(60, 255))
        for _ in range(6)
    ]
    img = []
    for y in range(size):
        row = []
        for x in range(size):
            v = 30 + sum(
                val for cx, cy, rad, val in blobs if (x - cx) ** 2 + (y - cy) ** 2 < rad**2
            )
            v = min(255, v)
            row.append((v, int(v * 0.8), int(v * 0.6)))
        img.append(row)
    return img


def test_hue_shifted_reencoded_screenshot_matches_its_original_and_not_another():
    original = _image(1)
    hue_shifted = [
        [(int(b * 1.1) % 256, r, g) for r, g, b in row] for row in original
    ]  # rotate channels
    rng = random.Random(2)
    reencoded = [
        [tuple(max(0, min(255, c + rng.randint(-12, 12))) // 8 * 8 for c in px) for px in row]
        for row in hue_shifted
    ]
    other = _image(7)
    fp = lambda v, img: Fingerprint(
        view_id=v,
        layer_id="ad.creative",
        kind=FingerprintKind.HASH,
        payload={"algo": "phash", "values": [_ahash(_gray(img))]},
        version="t",
    )
    cmp = comparators.get("phash")
    same = cmp.compare(fp("orig", original), fp("copy", reencoded))
    diff = cmp.compare(fp("orig", original), fp("other", other))
    assert same.score == 1.0 and same.shared[0]["distance"] <= 10
    assert diff.score == 0.0


def test_perceptual_layers_build_for_images_and_video(corpus):
    built = {(f.view_id, f.layer_id) for f in corpus.fingerprints()}
    assert ("1:ad", "ad.creative") in built and ("5:video", "media.video") in built
    assert score(corpus, "1:ad", "99:ad", "ad.creative") == 1.0
    assert score(corpus, "1:ad", "2:ad", "ad.creative") == 0.0


# -- playbook layers from model output --------------------------------------------------------


def test_health_offers_are_close_on_pattern_mo(corpus):
    assert score(corpus, "14:page", "47:page", "pattern.mo") >= 0.8
    assert score(corpus, "14:page", "10:page", "pattern.mo") < 0.2
    assert (
        score(corpus, "36:page", "47:page", "pattern.mo") == 0.0
    )  # an uncertain pattern does not count


def test_news_stories_are_close_on_pattern_and_roles(corpus):
    s = corpus
    for a, b in (
        ("1:page", "2:page"),
        ("1:page", "3:page"),
        ("1:page", "48:page"),
        ("3:page", "48:page"),
    ):
        assert score(s, a, b, "pattern.mo") >= 0.8, (a, b)
        assert score(s, a, b, "entity.roles") >= 0.25, (a, b)
    assert score(s, "1:page", "14:page", "entity.roles") < 0.25
    assert score(s, "1:page", "48:page", "entity.cast") < 0.3  # different people in those roles


def test_absence_vector_compares_same_kind_of_page_only(corpus):
    assert score(corpus, "1:page", "3:page", "kit.absence") == 1.0
    assert (
        score(corpus, "1:page", "14:page", "kit.absence") is None
    )  # advertorial vs product lander
    assert score(corpus, "14:page", "47:page", "kit.absence") >= 0.9


def test_playbook_layers_never_merge_campaigns_and_14_47_share_a_playbook():
    set_embedder(FakeEmbedder())
    s = store_from_fixtures()
    pipeline.run_fingerprint(s)
    g = run_method(s)
    rel = {(r.view_a, r.view_b): r for r in s.relations()}
    assert rel[("14:page", "47:page")].type is RelationType.SAME_PLAYBOOK
    assert rel[("1:page", "3:page")].type is RelationType.SAME_PLAYBOOK
    assert rel[("10:page", "14:page")].type is RelationType.UNRELATED
    assert g.campaign_of["14:page"] != g.campaign_of["47:page"]
    assert g.family_of_view("14:page") != g.family_of_view("47:page")
    labels_14, labels_47 = g.playbooks_of.get("14:page", []), g.playbooks_of.get("47:page", [])
    assert labels_14 and set(labels_14) & set(labels_47)
    assert set(g.playbooks_of.get("1:page", [])) & set(g.playbooks_of.get("3:page", []))
    weights.reset()
    set_embedder(None)
    s.close()


def test_calibrate_returns_none_without_vectors():
    s = store_from_fixtures(only_incidents={"5"})
    pipeline.run_fingerprint(s)
    assert calibrate(s, "text.semantic") is None
    s.close()
