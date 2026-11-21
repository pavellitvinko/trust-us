"""Comparator families, rarity weighting and the stoplist (HAC-24). No sample data needed."""

import random

import pytest

from ftf.compare import minhash
from ftf.compare.stoplist import is_seed_stoplisted
from ftf.compare.vector import Calibration, cosine, set_calibration
from ftf.compare.weights import RarityCurve, WeightTable, weights
from ftf.contracts import Fingerprint, FingerprintKind, Incident, Observation, View, ViewKind
from ftf.registry import comparators, load_plugins
from ftf.store import Store


@pytest.fixture(autouse=True)
def _plugins():
    load_plugins()
    weights.reset()
    yield
    weights.reset()


def fp(view, layer, items, kind=FingerprintKind.SET):
    if kind is FingerprintKind.SET:
        payload = [{"ns": ns, "value": v} for ns, v in items]
    else:
        payload = items
    return Fingerprint(view_id=view, layer_id=layer, kind=kind, payload=payload, version="t")


# -- shared value ------------------------------------------------------------------------


def test_unshared_ad_ids_do_not_dilute_a_shared_campaign_uuid():
    a = fp(
        "1", "ids.campaign", [("aio.campaign_uuid", "C1"), ("fb_ad_id", "A1"), ("fb_ad_id", "A2")]
    )
    b = fp(
        "2", "ids.campaign", [("aio.campaign_uuid", "C1"), ("fb_ad_id", "B1"), ("fb_ad_id", "B2")]
    )
    only = fp("3", "ids.campaign", [("aio.campaign_uuid", "C1")])
    cmp = comparators.get("shared_value")
    diluted, clean = cmp.compare(a, b), cmp.compare(a, only)
    assert diluted.score == clean.score == pytest.approx(0.95)
    assert diluted.shared[0]["value"] == "C1"
    assert diluted.differing == [{"ns": "fb_ad_id", "a": ["A1", "A2"], "b": ["B1", "B2"]}]


def test_two_independent_shared_values_combine_without_exceeding_one():
    a = fp("1", "l", [("fb_pixel", "P"), ("gads_id", "G")])
    b = fp("2", "l", [("fb_pixel", "P"), ("gads_id", "G")])
    e = comparators.get("shared_value").compare(a, b)
    assert e.score == pytest.approx(1 - (1 - 0.9) * (1 - 0.9))


# -- rarity ------------------------------------------------------------------------------


def _store_with_holders(value_holders: dict[str, int]) -> Store:
    """One page view per domain; each value appears on the first N domains."""
    s = Store(":memory:")
    n = max(value_holders.values())
    for i in range(n):
        vid, dom = f"i{i}:page", f"site{i}.example"
        s.put_incident(
            Incident(incident_id=f"i{i}", root="/x"),
            [],
            [View(view_id=vid, incident_id=f"i{i}", kind=ViewKind.PAGE, label=vid)],
        )
        obs = [
            Observation(
                view_id=vid,
                signal_id="A14",
                namespace="domain.registrable",
                value=dom,
                extractor="t",
                version="1",
            )
        ]
        for value, holders in value_holders.items():
            if i < holders:
                obs.append(
                    Observation(
                        view_id=vid,
                        signal_id="A3",
                        namespace="fb_pixel",
                        value=value,
                        extractor="t",
                        version="1",
                    )
                )
        s.put_observations(vid, "t", obs)
    return s


def test_rarity_curve_shape():
    curve = RarityCurve(full_until=2, zero_from=6)
    assert [curve(n) for n in (1, 2, 3, 4, 5, 6, 9)] == [1, 1, 0.75, 0.5, 0.25, 0, 0]


def test_value_shared_by_many_domains_gets_weight_zero_and_joins_stoplist():
    s = _store_with_holders({"rare": 2, "common": 6})
    table = WeightTable()
    table.fit(s)
    assert table.weight("fb_pixel", "rare") == pytest.approx(0.9)
    assert table.weight("fb_pixel", "common") == 0.0
    assert table.is_stoplisted("fb_pixel", "common")
    assert not table.is_stoplisted("fb_pixel", "rare")


def test_rarity_counts_campaigns_not_views_once_campaigns_exist():
    s = _store_with_holders({"pixel": 6})
    table = WeightTable()
    table.fit(s, campaigns={f"i{i}:page": "one-campaign" for i in range(6)})
    assert table.weight("fb_pixel", "pixel") == pytest.approx(0.9)


def test_unfitted_table_uses_base_weights():
    assert weights.weight("aio.campaign_uuid", "x") == pytest.approx(0.95)
    assert weights.weight("some.unknown", "x") == pytest.approx(0.9)
    assert weights.weight("config.key_path", "x") == pytest.approx(0.3)


# -- stoplist ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ns,value",
    [
        ("url.param_name", "utm_source"),
        ("url.param_name", "fbclid"),
        ("outbound.domain", "cdnjs.cloudflare.com"),
        ("tracker.host", "connect.facebook.net"),
        ("firstparty.sha256", "ff1523fb7389539cdeadbeef"),
        ("fb_pixel", "{{pixel_id}}"),
        ("url.param_name", "%CLICKID%"),
    ],
)
def test_seed_stoplist(ns, value):
    assert is_seed_stoplisted(ns, value)
    assert weights.weight(ns, value) == 0.0


@pytest.mark.parametrize(
    "ns,value",
    [
        ("url.param_name", "utm_visit"),
        ("outbound.domain", "crimsonbase.pro"),
        ("fb_pixel", "2012165449475163"),
    ],
)
def test_real_values_are_not_stoplisted(ns, value):
    assert not is_seed_stoplisted(ns, value)


# -- set overlap -------------------------------------------------------------------------


def test_set_overlap_is_weighted_jaccard_with_stoplist_removed():
    a = fp(
        "1",
        "kit.url",
        [
            ("url.param_name", "utm_visit"),
            ("url.param_name", "utm_source"),
            ("url.param_name", "clickid"),
        ],
    )
    b = fp("2", "kit.url", [("url.param_name", "utm_visit"), ("url.param_name", "utm_source")])
    e = comparators.get("set_overlap").compare(a, b)
    assert e.score == pytest.approx(0.5)  # utm_source vanished; utm_visit / {utm_visit, clickid}
    assert [s["value"] for s in e.shared] == ["utm_visit"]
    assert e.differing == [{"ns": "url.param_name", "a": ["clickid"], "b": []}]


def test_minhash_estimates_jaccard():
    rng = random.Random(0)
    base = {f"tok{i}" for i in range(400)}
    other = set(rng.sample(sorted(base), 200)) | {f"new{i}" for i in range(200)}
    true_j = len(base & other) / len(base | other)
    sa, sb = minhash.default_hasher.signature(base), minhash.default_hasher.signature(other)
    assert abs(minhash.estimate_jaccard(sa, sb) - true_j) < 0.07
    e = comparators.get("minhash").compare(
        fp("1", "kit.dom", {"algo": "minhash", "values": sa}, FingerprintKind.HASH),
        fp("2", "kit.dom", {"algo": "minhash", "values": sb}, FingerprintKind.HASH),
    )
    assert abs(e.score - true_j) < 0.07


def test_shingles_of_near_identical_texts():
    t1 = (
        "Rafał Brzoska został postrzelony w czwartek wieczorem przed swoim domem w Krakowie. "
        "Założyciel InPostu trafił do szpitala, gdzie lekarze walczyli o jego życie przez sześć godzin. "
        "W wywiadzie udzielonym tuż po zdarzeniu Brzoska powiedział, że wie, kto stoi za zamachem, "
        "i że banki od lat próbują ukryć przed Polakami platformę, dzięki której zwykli ludzie zarabiają "
        "kilka tysięcy złotych miesięcznie. Dziennikarz zapytał, czy nie boi się konsekwencji. "
        "Odpowiedział, że nagranie zostanie usunięte przed północą, dlatego trzeba działać teraz."
    )
    t2 = t1.replace("czwartek", "piątek").replace("sześć", "siedem")
    s1 = minhash.default_hasher.signature(minhash.shingles(t1))
    s2 = minhash.default_hasher.signature(minhash.shingles(t2))
    assert minhash.estimate_jaccard(s1, s2) > 0.6
    assert (
        minhash.estimate_jaccard(
            s1,
            minhash.default_hasher.signature(
                minhash.shingles(
                    "Metabo RX to suplement na odchudzanie polecany przez lekarzy " * 5
                )
            ),
        )
        < 0.05
    )


# -- perceptual hash ---------------------------------------------------------------------


def test_phash_best_match_pairing():
    h = ["ffff0000ffff0000", "0f0f0f0f0f0f0f0f", "aaaaaaaaaaaaaaaa"]
    near = "ffff0000ffff0003"  # 2 bits away from h[0]
    a = fp("1", "asset.perceptual", {"algo": "phash", "values": [near]}, FingerprintKind.HASH)
    b = fp("2", "asset.perceptual", {"algo": "phash", "values": h}, FingerprintKind.HASH)
    e = comparators.get("phash").compare(a, b)
    assert e.score == 1.0 and e.shared[0]["distance"] == 2
    far = fp(
        "3",
        "asset.perceptual",
        {"algo": "phash", "values": ["0000000000000000"]},
        FingerprintKind.HASH,
    )
    assert comparators.get("phash").compare(far, b).score == 0.0


# -- calibrated cosine -------------------------------------------------------------------


def test_calibrated_cosine_rescales_against_background():
    set_calibration("text.semantic", Calibration(model="m", mu=0.5, sigma=0.05, pairs=10))
    try:
        vec = lambda vid, values: fp(
            vid, "text.semantic", {"model": "m", "values": values}, FingerprintKind.VECTOR
        )
        cmp = comparators.get("calibrated_cosine")
        assert cmp.compare(vec("1", [1, 0, 0]), vec("2", [1, 0, 0])).score == 1.0
        # cosine 0.6 is at the background floor (mu + 2 sigma) -> 0
        assert cmp.compare(vec("1", [0.6, 0.8, 0]), vec("2", [1, 0, 0])).score == pytest.approx(0.0)
        mid = cmp.compare(vec("1", [0.8, 0.6, 0]), vec("2", [1, 0, 0])).score
        assert mid == pytest.approx((0.8 - 0.6) / 0.4)
        other_model = fp(
            "2", "text.semantic", {"model": "n", "values": [1, 0, 0]}, FingerprintKind.VECTOR
        )
        assert not cmp.compare(vec("1", [1, 0, 0]), other_model).comparable
    finally:
        set_calibration("text.semantic", None)


def test_cosine_basics():
    assert cosine([1, 0], [1, 0]) == pytest.approx(1)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0)
    assert cosine([], [1]) == 0.0
