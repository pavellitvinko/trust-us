"""Contract, store and ingestion tests that need no sample data."""

import json
from pathlib import Path

from ftf import pipeline
from ftf.contracts import Evidence, Fingerprint, FingerprintKind, Observation
from ftf.ingest import ingest_folder, main_document
from ftf.models import cached_call
from ftf.registry import comparators, extractors, layers, load_plugins

FIXTURES = Path(__file__).parent / "fixtures"


def _har(*entries):
    return {"log": {"entries": list(entries)}}


def _entry(url, mime="text/html", status=200, rtype="document"):
    return {
        "request": {"url": url},
        "response": {"status": status, "content": {"mimeType": mime}},
        "_resourceType": rtype,
    }


def make_incident(root: Path, host: str, path: str = "/") -> Path:
    root.mkdir(parents=True)
    (root / "page.html").write_text(
        f"<!-- saved from url=(0030)https://{host}{path} -->\n<html><title>x</title></html>"
    )
    (root / "page_files").mkdir()
    (root / "page_files" / "main.css").write_text("body{}")
    (root / f"{host}.har").write_text(
        json.dumps(
            _har(
                _entry("https://cdn.example.net/v.ts", "video/mp2t", rtype="media"),
                _entry(f"https://{host}{path}"),
            )
        )
    )
    (root / "ad.png").write_bytes(b"\x89PNG fake")
    (root / "clip.mp4").write_bytes(b"fake")
    (root / "clip.info.json").write_text("{}")
    return root


def test_plugins_load_and_satisfy_interfaces():
    load_plugins()
    assert "page.identity" in extractors.names()
    assert "ids.domain" in layers.names()
    assert layers.get("ids.domain").comparator in comparators.names()


def test_main_document_is_chosen_by_type_not_position():
    entries = [
        _entry("https://cdn.example.net/a.ts", "video/mp2t", rtype="media"),
        _entry("https://delivered/?id="),
        _entry("https://shop.example.org/x"),
    ]
    assert main_document(entries) == (2, "https://shop.example.org/x")
    assert main_document([_entry("https://a.example.org/", status=404)]) is None


def test_ingest_builds_views_and_roles(store, tmp_path):
    incident, views = ingest_folder(
        make_incident(tmp_path / "7", "shop.example.org", "/offer/"), store
    )
    kinds = sorted(v.kind.value for v in views)
    assert kinds == ["image", "page", "video"]
    page = next(v for v in views if v.kind.value == "page")
    assert page.har == "shop.example.org.har" and page.resources_dir == "page_files"
    video = next(v for v in views if v.kind.value == "video")
    assert video.attachments == ["clip.info.json"]
    roles = {a.path: a.role for a in store.artifacts(incident.incident_id)}
    assert roles["page_files/main.css"] == "resource" and roles["page.html"] == "primary"


def test_unmatched_har_becomes_its_own_page_view(store, tmp_path):
    root = make_incident(tmp_path / "8", "one.example.org")
    (root / "other.example.com.har").write_text(
        json.dumps(_har(_entry("https://other.example.com/landing")))
    )
    _, views = ingest_folder(root, store)
    assert sorted(v.meta["origin"] for v in views if v.kind.value == "page") == [
        "har_only",
        "saved_page",
    ]


def test_reingest_replaces_and_pipeline_links_same_domain(store, tmp_path):
    ingest_folder(make_incident(tmp_path / "a", "www.same-site.org", "/L1/"), store)
    ingest_folder(make_incident(tmp_path / "b", "same-site.org", "/L2/"), store)
    ingest_folder(make_incident(tmp_path / "c", "different-site.com"), store)
    ingest_folder(tmp_path / "a", store)  # idempotent
    assert len(store.views()) == 9
    pipeline.run_extract(store)
    pipeline.run_fingerprint(store)
    pipeline.run_compare_all(store)
    linked = [e for e in store.evidence(min_score=0.01) if e.layer_id == "ids.domain"]
    assert (
        len(linked) == 1
    )  # kit layers may also fire on these minimal pages; the domain link is the one
    assert linked[0].shared[0]["value"] == "same-site.org"
    assert {linked[0].view_a.split(":")[0], linked[0].view_b.split(":")[0]} == {"a", "b"}


def test_missing_layer_is_not_comparable_not_different(store, tmp_path):
    _, views = ingest_folder(make_incident(tmp_path / "d", "x.example.org"), store)
    pipeline.run_extract(store)
    pipeline.run_fingerprint(store)
    page = next(v for v in views if v.kind.value == "page")
    image = next(v for v in views if v.kind.value == "image")
    evidence = pipeline.compare_views(store, page.view_id, image.view_id)
    assert evidence and all(not e.comparable and e.score == 0 for e in evidence)


def test_model_cache_calls_once(tmp_path):
    calls = []
    compute = lambda: calls.append(1) or {"answer": 42}
    args = (tmp_path, "task", "v1", "model-x", {"text": "hello"})
    assert cached_call(*args, compute) == {"answer": 42}
    assert cached_call(*args, compute) == {"answer": 42}
    assert len(calls) == 1


def test_fixture_observations_are_valid_and_cover_the_key_cases():
    data = json.loads((FIXTURES / "observations.json").read_text(encoding="utf-8"))
    observations = [Observation.model_validate(o) for o in data["observations"]]
    by_view = {}
    for o in observations:
        by_view.setdefault(o.view_id, {}).setdefault(o.namespace, set()).add(o.value)
    camp = "aio.campaign_uuid"
    assert by_view["1:page"][camp] == by_view["2:page"][camp]  # same campaign, different domains
    assert by_view["1:page"]["domain.registrable"] != by_view["2:page"]["domain.registrable"]
    assert by_view["48:page"][camp] != by_view["61:article"][camp]  # contradiction
    assert by_view["48:page"]["aio.template_uuid"] == by_view["61:article"]["aio.template_uuid"]
    assert (
        by_view["48:page"]["api_key_id"] == by_view["61:article"]["api_key_id"]
    )  # must be demoted to kit tier


def test_contracts_roundtrip():
    fp = Fingerprint(
        view_id="v",
        layer_id="l",
        kind=FingerprintKind.SET,
        payload=[{"ns": "n", "value": "x"}],
        version="1",
    )
    assert Fingerprint.model_validate_json(fp.model_dump_json()) == fp
    ev = Evidence(view_a="a", view_b="b", layer_id="l", comparator="c", version="1")
    assert ev.comparable and ev.score == 0
