"""Real-file validation of grouping, confidence and robustness (HAC-54).

Runs on the organiser sample, model off, and is skipped where the sample is absent. The
fixture versions of these tables are in test_ablation_candidates.py and stay separate."""

from pathlib import Path

import pytest

from ftf.compare.weights import weights
from ftf.contracts import RelationType
from ftf.eval import evaluate, sample
from ftf.eval.harness import Report
from ftf.eval.reference import load_reference, resolve_view
from ftf.grouping.assign import Outcome

SAMPLE = Path(__file__).resolve().parents[1] / "testing_sample"


@pytest.fixture(scope="module")
def base():
    if not SAMPLE.is_dir():
        pytest.skip("testing_sample/ not present")
    s = sample.extracted(SAMPLE)
    yield s
    s.close()


@pytest.fixture(scope="module")
def method(base):
    with sample.model_off():
        store, groups = sample.method_store(base)
        yield store, groups
        store.close()
    weights.reset()


def rel(store, a, b):
    r = sample._relation(store, a, b)
    assert r is not None, (a, b)
    return r


def test_case_61_business_page_is_not_in_the_article_campaign(method):
    store, groups = method
    business = resolve_view(store, "61:business")
    for other in ("61:article", "61:chat"):
        r = rel(store, "61:business", other)
        assert r.type is RelationType.SAME_FAMILY and r.confidence == "low", other
        assert any("shared domain only" in x for x in r.reasons)
        assert groups.campaign_of[business] != groups.campaign_of[resolve_view(store, other)]
    # shared infrastructure is still shown: one family, three campaigns' worth of evidence
    assert groups.family_of_view(business) == groups.family_of_view(resolve_view(store, "61:chat"))


def test_checkpoint_outcomes_all_hold(method):
    store, groups = method
    checks = sample.checkpoint(store, groups)
    assert len(checks) == 7 and all(v is True for v in checks.values()), checks


def test_unproven_similarities_stay_unlinked(method):
    store, _ = method
    for a, b in (("3:page", "14:page"), ("3:page", "14:capture"), ("36:page", "47:page")):
        r = rel(store, a, b)
        assert r.type is RelationType.UNRELATED, (a, b, r.type)
    assert rel(store, "2:page", "2:healthlinkq").type is RelationType.UNRELATED


def test_missing_data_is_unavailable_not_separation(method):
    store, _ = method
    for a, b in (("3:ad", "3:page"), ("5:video", "1:page"), ("47:video", "47:page")):
        r = rel(store, a, b)
        assert (r.type, r.confidence) == (RelationType.INSUFFICIENT, ""), (a, b)
    with_fp = {f.view_id for f in store.fingerprints()}
    bare = [r for r in store.relations() if r.view_a not in with_fp or r.view_b not in with_fp]
    assert bare and all(r.type is RelationType.INSUFFICIENT for r in bare)


def test_confidence_labels_match_the_supporting_groups(method):
    store, _ = method
    assert sample.confidence_problems(store) == []
    for s in sample.linked_support(store):
        if s.confidence == "high" and s.type is not RelationType.DUPLICATE:
            assert s.independent >= 2, s
        assert 0 < s.comparable <= s.total


def test_reference_report_counts_only_evaluated_pairs(method):
    store, _ = method
    report = evaluate(store)
    assert len(report.results) == 54 and report.not_evaluated == []
    assert report.false_merges == []
    counted = len(report.strict) + len(report.deviations) + len(report.disagreements)
    assert counted == len(report.evaluated)
    # the misses are playbook pairs and one stylesheet link, never a campaign-level error
    assert all(
        r.predicted in (RelationType.UNRELATED, RelationType.INSUFFICIENT)
        for r in report.disagreements
    )


def test_unresolved_reference_pairs_are_not_evaluated():
    from ftf import pipeline
    from ftf.eval.fixtures import store_from_fixtures
    from ftf.fusion import run_method

    s = store_from_fixtures()  # the fixtures hold no case-61 business page
    pipeline.run_fingerprint(s)
    run_method(s)
    report: Report = evaluate(s, load_reference())
    weights.reset()
    s.close()
    assert {(r.ref.a, r.ref.b) for r in report.not_evaluated} == {
        ("61:article", "61:business"),
        ("61:business", "61:chat"),
    }
    assert len(report.evaluated) == 52 and report.agreement == 1.0 and not report.false_merges


def test_degradation_on_real_files(base, tmp_path):
    runs = {r.name: r for r in sample.degrade_sample(SAMPLE, base, workdir=tmp_path)}
    full = runs.pop("full input")
    assert all(r.report.false_merges == [] for r in runs.values())
    assert list(tmp_path.iterdir()) == []  # damaged copies are removed

    no_har = runs["HAR files removed"]
    assert no_har.failed_checks == [] and no_har.views == full.views - 1  # the HAR-only page
    assert no_har.checks["1↔2 same campaign"] is True
    assert no_har.checks["2 healthlinkq↔3 duplicate"] is None  # unavailable, not lost

    swapped = runs["folder 2 on swapped domains"]
    assert swapped.checks["1↔2 same campaign"] is True  # the campaign record survives the move
    assert swapped.failed_checks == ["2 healthlinkq↔3 duplicate"]
    assert any(
        "2:healthlinkq↔3:page: duplicate → same_campaign" in f for f in swapped.flipped_from(full)
    )

    images = next(r for name, r in runs.items() if name.startswith("images re-encoded"))
    assert images.flipped_from(full) == [] and images.failed_checks == []

    shots = runs["screenshots only"]
    assert all(v is None for v in shots.checks.values())
    assert set(shots.types) == {RelationType.INSUFFICIENT.value}


def test_ablation_of_the_two_layers_that_matter(base):
    full = sample.run_sample("all layers", base)
    no_campaign = sample.run_sample("without ids.campaign", base, off=["ids.campaign"])
    # without the campaign record there is no contradiction: 48 and 61 merge
    assert len(no_campaign.report.false_merges) == 2
    assert no_campaign.failed_checks == ["48↔61 same family, two campaigns"]
    no_text = sample.run_sample("without text.shingle", base, off=["text.shingle"])
    assert no_text.flipped_from(full) == [] and no_text.failed_checks == []
    assert no_text.layers_on == full.layers_on - 1


def test_leave_one_incident_out_on_real_files(base):
    held = {r.incident_id: r for r in sample.loio_sample(base, incidents=["2", "3", "5", "61"])}
    assert held["3"].headline.outcome is Outcome.DUPLICATE_OF
    assert held["3"].labels[held["3"].headline.target].startswith("2 ·")
    assert {a.outcome for a in held["2"].per_view} == {
        Outcome.DUPLICATE_OF,
        Outcome.VARIANT_OF_CAMPAIGN,
    }
    assert held["5"].headline.outcome is Outcome.INSUFFICIENT
    assert held["61"].headline.outcome is Outcome.NEW_CAMPAIGN_IN_FAMILY
    assert held["61"].headline.target.startswith("f:48:")


def test_held_out_folder_does_not_shape_the_weights(base):
    """The weight table used to judge the held-out folder is the one fitted without it."""
    from ftf import pipeline
    from ftf.fusion import run_method
    from ftf.grouping.assign import assign_incident

    with sample.model_off():
        store = sample.clone(base, exclude_incidents=["3"])
        pipeline.run_fingerprint(store)
        groups = run_method(store)
        before = (dict(weights.holders), set(weights.demoted), set(weights.learned_stoplist))
        sample.clone(base, only_incidents=["3"], into=store)
        pipeline.run_fingerprint(store, view_ids=[v.view_id for v in store.views("3")])
        headline, _ = assign_incident(store, "3", groups, prepared=True)
        after = (dict(weights.holders), set(weights.demoted), set(weights.learned_stoplist))
        store.close()
    weights.reset()
    assert headline.outcome is Outcome.DUPLICATE_OF and after == before


# -- the file-level damage, on synthetic files (no sample needed) ----------------------------


def test_reencode_changes_bytes_and_keeps_the_picture(tmp_path):
    import imagehash
    from PIL import Image, ImageDraw

    src, dst = tmp_path / "a.png", tmp_path / "out" / "a.png"
    # blocks of different brightness, like a page screenshot: structure a hue shift leaves alone
    image = Image.new("RGB", (128, 128), (235, 232, 228))
    draw = ImageDraw.Draw(image)
    for box, colour in (
        ((8, 8, 120, 30), (40, 60, 90)),
        ((8, 40, 60, 110), (200, 80, 60)),
        ((70, 40, 120, 70), (90, 140, 100)),
        ((70, 80, 120, 110), (30, 30, 35)),
    ):
        draw.rectangle(box, fill=colour)
    image.save(src)
    assert sample.reencode_image(src, dst)
    assert dst.read_bytes() != src.read_bytes()
    assert imagehash.phash(Image.open(src)) - imagehash.phash(Image.open(dst)) <= 10
    assert not sample.reencode_image(tmp_path / "notes.txt", tmp_path / "out" / "notes.txt")
    (tmp_path / "broken.png").write_bytes(b"not an image")
    assert not sample.reencode_image(tmp_path / "broken.png", tmp_path / "out" / "broken.png")


def test_derive_tree_never_touches_the_originals(tmp_path):
    src = tmp_path / "sample"
    (src / "2").mkdir(parents=True)
    (src / "2" / "old.live.har").write_text('{"url": "https://old.live/x"}')
    (src / "2" / "shot.png").write_bytes(b"png")
    (src / "3").mkdir()
    (src / "3" / "page.html").write_text("<a href='https://old.live/'>")
    before = {p: p.read_bytes() for p in src.rglob("*") if p.is_file()}

    n = sample.derive_tree(
        src,
        tmp_path / "swapped",
        rewrite=lambda s, d: (
            s.relative_to(src).parts[0] == "2"
            and sample.swap_text({"old.live": "swapped-old.live"})(s, d)
        ),
        rename=lambda rel: Path(rel.as_posix().replace("old.live", "swapped-old.live")),
    )
    assert n == 3
    assert "swapped-old.live" in (tmp_path / "swapped/2/swapped-old.live.har").read_text()
    assert "swapped" not in (tmp_path / "swapped/3/page.html").read_text()  # folder 3 untouched
    sample.derive_tree(src, tmp_path / "nohar", keep=lambda rel: rel.suffix != ".har")
    assert sorted(p.name for p in (tmp_path / "nohar").rglob("*") if p.is_file()) == [
        "page.html",
        "shot.png",
    ]
    assert {p: p.read_bytes() for p in src.rglob("*") if p.is_file()} == before
