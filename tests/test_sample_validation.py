"""Real-file validation of grouping, confidence and robustness (HAC-54).

Runs on the organiser sample, model off, and is skipped where the sample is absent. The
fixture versions of these tables are in test_ablation_candidates.py and stay separate."""

import io
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
def base(sample_observations):
    s = sample.clone(sample_observations)
    yield s
    s.close()


@pytest.fixture(scope="module")
def method(base):
    with sample.run_embedder():
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
    # Missing data is a pair with no layer group comparable on both sides (a video and a
    # page): it is insufficient evidence, never unrelated.
    no_overlap = [r for r in store.relations() if r.coverage == 0]
    assert no_overlap and all(r.type is RelationType.INSUFFICIENT for r in no_overlap)
    # Since HAC-19 every sample view carries a fingerprint: videos get key-frame hashes.
    with_fp = {f.view_id for f in store.fingerprints()}
    assert {v.view_id for v in store.views()} <= with_fp


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
    # per-type rates follow the headline: the two unresolved family pairs are not recall misses
    family = report.by_type[RelationType.SAME_FAMILY]
    assert family.reference == family.exact and family.recall == 1.0
    assert sum(t.reference for t in report.by_type.values()) == len(report.evaluated)


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

    with sample.run_embedder():
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


def test_candidate_generation_matches_all_pairs_on_real_files(base):
    """HAC-36: the same relations and groups either way, and an honest count of the saving."""
    parity = sample.candidate_parity(base)
    assert parity.differences == [] and parity.groups_equal and parity.reasons_differ == 0
    assert parity.holds
    assert parity.all_pairs == parity.views * (parity.views - 1) // 2
    # no saving within a view kind: the catch-all buckets propose every page and image pair
    assert parity.by_kind["page-page"][0] == parity.by_kind["page-page"][1]
    assert parity.by_kind["image-page"][0] == 0
    assert sum(got for got, _ in parity.by_kind.values()) == parity.compared
    built, index_only = parity.variants
    assert built.pairs == parity.compared and built.scored_missed == []
    assert index_only.pairs < built.pairs / 2
    assert index_only.linked_found == index_only.linked_total > 0  # no link is lost
    assert all(score < 0.5 for _, _, score in index_only.scored_missed)  # only weak similarity
    text = "\n".join(sample.format_candidates(parity))
    assert "**holds**" in text and "not a saving" in text


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


def test_reencode_rewrites_the_image_bodies_inside_a_har(tmp_path):
    """The asset extractor hashes HAR response bodies too, so the re-encoding scenario must
    reach them: an original image kept inside the HAR would keep its exact hash alive."""
    import base64
    import json

    import imagehash
    from PIL import Image

    from .test_extract_assets import picture

    png = picture(7)
    har = {
        "log": {
            "entries": [
                {
                    "request": {"url": "https://x.example/hero.png"},
                    "response": {
                        "content": {
                            "mimeType": "image/png",
                            "encoding": "base64",
                            "text": base64.b64encode(png).decode(),
                        }
                    },
                },
                {
                    "request": {"url": "https://x.example/logo.svg"},
                    "response": {"content": {"mimeType": "image/svg+xml", "text": "<svg></svg>"}},
                },
                {
                    "request": {"url": "https://x.example/"},
                    "response": {"content": {"mimeType": "text/html", "text": "<html>same</html>"}},
                },
            ]
        }
    }
    src, dst = tmp_path / "x.har", tmp_path / "out" / "x.har"
    src.write_text(json.dumps(har))
    assert sample.reencode_image(src, dst)
    out = json.loads(dst.read_text())["log"]["entries"]
    body = base64.b64decode(out[0]["response"]["content"]["text"])
    assert body != png  # the exact hash is gone
    assert body == sample.reencode_bytes(png, "PNG")  # the same damage an image file gets
    original, damaged = Image.open(io.BytesIO(png)), Image.open(io.BytesIO(body))
    assert imagehash.phash(original) - imagehash.phash(damaged) <= 10  # the picture is not
    assert "text" not in out[1]["response"]["content"]  # undecodable image body dropped
    assert out[2]["response"]["content"]["text"] == "<html>same</html>"  # non-image untouched
    assert json.loads(src.read_text()) == har  # the original is never changed
    # a HAR without any image body is left to the plain copy
    (tmp_path / "plain.har").write_text(json.dumps({"log": {"entries": [out[2]]}}))
    assert not sample.reencode_image(tmp_path / "plain.har", tmp_path / "out" / "plain.har")


def test_reencoded_folders_keep_no_original_content_hash(base, tmp_path):
    """On the real sample: after the image scenario, no exact content hash of folders 2 and
    61 survives through any source, file or HAR body."""
    scenario = next(s for s in sample.scenarios(base) if s.name.startswith("images re-encoded"))
    damaged = tmp_path / "sample"
    scenario.build(SAMPLE, damaged)
    store = sample.extracted(damaged)

    def content_hashes(s, incidents):
        return {
            o.value
            for v in s.views()
            if v.incident_id in incidents
            for o in s.observations(v.view_id)
            if o.namespace == "asset.sha256.content"
        }

    before, after = content_hashes(base, {"2", "61"}), content_hashes(store, {"2", "61"})
    store.close()
    assert before and after
    assert before & after == set(), sorted(before & after)[:5]


def test_candidate_report_wording_follows_the_measurement():
    """HAC-36 review: the prose must change with the record, not restate the sample's result."""
    broken = sample.CandidateParity(
        views=20,
        all_pairs=190,
        compared=120,
        by_kind={"image-image": (45, 45), "image-page": (0, 100), "page-page": (40, 45)},
        by_key=[("perceptual (every holder)", 45), ("identity", 30)],
        differences=["a ~ b: same_campaign/high vs insufficient_evidence/-"],
        reasons_differ=2,
        groups_equal=False,
        variants=[
            sample.IndexVariant("as built: all index keys", 120, 40, 8, 8, []),
            sample.IndexVariant(
                "identifier, set and MinHash keys only", 30, 12, 6, 8, [("a", "b", 0.62)]
            ),
        ],
        skipped_types={"insufficient_evidence": 60, "same_campaign": 1, "unrelated": 9},
    )
    text = "\n".join(sample.format_candidates(broken))
    assert "**BROKEN**" in text and "equal for every pair" not in text
    assert "1 pairs differ in type" in text and "funnel edges differ" in text
    assert "2 pairs carry different reasons" in text
    assert "not a saving" not in text
    assert "5 of 90 pairs are skipped (image-image 45 of 45, page-page 40 of 45)" in text
    assert "No pair across view kinds is compared." in text
    assert "**1 skipped pairs are linked in the all-pairs run** (same_campaign 1)" in text
    assert "would miss 2 of 8 linked pairs" in text
    assert (
        "finds every linked pair" not in text
        and "Scaling is therefore not demonstrated" not in text
    )

    clean = sample.CandidateParity(
        views=5,
        all_pairs=7,
        compared=3,
        by_kind={"image-image": (3, 3), "image-page": (0, 3), "video-video": (0, 1)},
        by_key=[("perceptual (every holder)", 3)],
        differences=[],
        reasons_differ=0,
        groups_equal=True,
        variants=[
            sample.IndexVariant("as built: all index keys", 3, 0, 1, 1, []),
            sample.IndexVariant("identifier, set and MinHash keys only", 1, 0, 1, 1, []),
        ],
        skipped_types={"insufficient_evidence": 4},
    )
    text = "\n".join(sample.format_candidates(clean))
    assert "**holds**" in text and "equal for every pair" in text
    assert "not a saving" in text and "(image-image 3 of 3, video-video 0 of 1)" in text
    assert "No pair across view kinds is compared." in text
    assert "All 4 skipped pairs are unrelated or insufficient evidence" in text
    assert "find every linked pair here (1 of 1) while proposing 1 pairs instead of 3." in text
    assert "No skipped pair reaches a tier score of 0.1" in text
    assert "Scaling is therefore not demonstrated" in text


def test_scored_but_skipped_pairs_are_measured_not_counted_as_broken_parity():
    """Review of HAC-36: a pair the index did not propose but that scored in the all-pairs
    run is a recall measure of the index. Both runs store the same relation for it, so it
    must not flip the verdict; it gets its own sentence instead."""
    p = sample.CandidateParity(
        views=4,
        all_pairs=6,
        compared=4,
        by_kind={"page-page": (4, 6)},
        by_key=[("identity", 4)],
        differences=[],
        reasons_differ=0,
        groups_equal=True,
        variants=[sample.IndexVariant("as built: all index keys", 4, 4, 2, 2, [])],
        skipped_types={"unrelated": 2},
        scored_skipped=[("c ~ d", 0.3), ("a ~ b", 0.12)],
    )
    assert p.holds
    text = "\n".join(sample.format_candidates(p))
    assert "**holds**" in text
    assert "2 skipped pairs reach a tier score of 0.1 or more" in text
    assert "(highest 0.30, c ~ d)" in text and "same relation for them" in text


def test_group_comparison_covers_the_audit_trail_and_both_edge_lists():
    """Review of HAC-36: a candidate run that differs only in a rejected merge, a co-captured
    edge or an edge's evidence is not 'identical'."""
    from ftf.grouping.funnel import FunnelEdge
    from ftf.grouping.levels import Groups

    def groups(**over):
        base = {
            "campaign_of": {"a": "c1", "b": "c1"},
            "campaigns": {"c1": ["a", "b"]},
            "family_of": {"c1": "f1"},
            "families": {"f1": ["c1"]},
            "playbooks_of": {},
            "playbooks": {},
            "rejected_merges": [("a", "x", "merge guard")],
            "funnel_edges": {"1": ([FunnelEdge("a", "b", "funnel", ["e"])], [])},
        }
        base.update(over)
        return Groups(**base)

    same = sample._groups_as_tuples
    assert same(groups()) == same(groups())
    assert same(groups()) != same(groups(rejected_merges=[]))
    assert same(groups()) != same(groups(campaign_of={"a": "c1", "b": "c2"}))
    co = ([FunnelEdge("a", "b", "funnel", ["e"])], [FunnelEdge("a", "b", "co_captured", [])])
    assert same(groups()) != same(groups(funnel_edges={"1": co}))
    assert same(groups()) != same(
        groups(funnel_edges={"1": ([FunnelEdge("a", "b", "funnel", ["other"])], [])})
    )


def test_identifier_only_variant_drops_every_catch_all_bucket():
    """Review of HAC-36: the three-state pattern layers key on the page kind, a bucket like
    the perceptual and vector layers'. The 'identifier keys only' variant must drop it too,
    or a model-on run would credit identifiers with pairs the kind bucket proposed."""
    assert sample._is_bucket_key(("perceptual.image", "*"))
    assert sample._is_bucket_key(("playbook.pattern", "kind", "landing"))
    assert not sample._is_bucket_key(("ids.domain", "domain.registrable", "x.example"))
    assert not sample._is_bucket_key(("kit.text", "band", "0:abc"))
