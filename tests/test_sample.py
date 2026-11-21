"""Checks against the organiser sample. Skipped where testing_sample/ is absent."""

import json
from collections import defaultdict
from pathlib import Path


def _views(store, incident, kind="page"):
    return [v for v in store.views(incident) if v.kind.value == kind]


def _linked(store, a, b):
    return any(e.score > 0 for e in store.evidence(view_a=a, view_b=b))


def test_sample_view_inventory(sample_store):
    s = sample_store
    assert sorted(i.incident_id for i in s.incidents()) == [
        "1",
        "10",
        "14",
        "2",
        "3",
        "36",
        "47",
        "48",
        "5",
        "61",
    ]
    assert (
        len(_views(s, "2")) == 2
    )  # the saved Brzoska page and the healthlinkq capture are separate views
    assert len(_views(s, "61")) == 3
    assert len(_views(s, "14")) == 2  # saved page + nested capture bundle, MHTML not counted twice
    assert len(_views(s, "5", "video")) == 1 and not _views(s, "5")
    assert all(v.har for v in _views(s, "61"))  # including the capture named just ".har"


def test_sample_domain_links(sample_store):
    s = sample_store
    health = next(v for v in _views(s, "2") if v.meta["origin"] == "har_only")
    olejnik = _views(s, "3")[0]
    assert _linked(s, health.view_id, olejnik.view_id)  # same landing on healthlinkq.pro

    brzoska_1, brzoska_2 = (
        _views(s, "1")[0],
        next(v for v in _views(s, "2") if v.meta["origin"] == "saved_page"),
    )
    assert not _linked(
        s, brzoska_1.view_id, brzoska_2.view_id
    )  # different domains: needs campaign IDs, not built yet

    a, b = _views(s, "14")
    assert _linked(s, a.view_id, b.view_id)


# --- identifier extractors (HAC-8, HAC-9) ------------------------------------------------

FIXTURE = Path(__file__).parent / "fixtures" / "observations.json"
# Namespaces no extractor in this PR produces yet (kit fingerprints, HAC-10).
NOT_YET = {"url.param_name"}


def _view(store, key):
    """Resolve a fixture view key (`2:healthlinkq`, `61:chat`) to a stored view."""
    incident, name = key.split(":")
    pages = _views(store, incident)
    if name == "healthlinkq":
        return next(v for v in pages if v.meta["origin"] == "har_only")
    if name in ("article", "chat"):
        folder = {"article": "strona_1/", "chat": "strona_2/"}[name]
        return next(v for v in pages if (v.primary or "").startswith(folder))
    saved = [v for v in pages if v.meta["origin"] == "saved_page"]
    assert len(saved) == 1, key
    return saved[0]


def _values(store, view, namespace):
    return {o.value for o in store.observations(view.view_id) if o.namespace == namespace}


def test_fixture_parity(sample_store):
    """Every value the Method track was promised in the fixture is produced on that view."""
    s = sample_store
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))["observations"]
    missing = [
        (r["view_id"], r["namespace"], r["value"])
        for r in rows
        if r["namespace"] not in NOT_YET
        and r["value"] not in _values(s, _view(s, r["view_id"]), r["namespace"])
    ]
    assert missing == []


def test_hac8_done_when(sample_store):
    s = sample_store
    v1, v2 = _view(s, "1:page"), _view(s, "2:page")
    v48, v61 = _view(s, "48:page"), _view(s, "61:article")
    camp, tmpl = "aio.campaign_uuid", "aio.template_uuid"
    assert _values(s, v1, camp) == _values(s, v2, camp) == {"0b9611d9-df5e-44e6-acea-ef85847dc3f1"}
    assert (
        _values(s, v48, tmpl) == _values(s, v61, tmpl) == {"25c6c6fd-73af-48cb-b5cf-752154ebc474"}
    )
    assert _values(s, v48, camp).isdisjoint(_values(s, v61, camp))
    pixels = {
        "1:page": "2012165449475163",
        "2:page": "2012165449475163",
        "10:page": "1549107073347817",
        "36:page": "1536860380829193",
        "47:page": "4322849547991901",
        "48:page": "2351106582387557",
    }
    for key, pixel in pixels.items():
        assert pixel in _values(s, _view(s, key), "fb_pixel"), key
    for v in _views(s, "14"):  # the saved page and the capture bundle
        assert "1071875908635809" in _values(s, v, "fb_pixel")


def test_hac8_never_emits_visitor_data(sample_store):
    """`visit.*` holds the analyst's IP, city and user agent; only record IDs may leave it."""
    s = sample_store
    for v in s.views():
        for o in s.observations(v.view_id):
            loc = o.source.locator or ""
            if "aio:visit." in loc:
                assert loc.split("aio:visit.")[1].split("[")[0] in (
                    "campaign_human_id",
                    "landing_human_ids",
                    "fields.fb_pixel",
                ), (o.namespace, loc)


def test_hac9_done_when(sample_store):
    s = sample_store
    for key in ("48:page", "61:article"):
        v = _view(s, key)
        assert "shadowtracecore.com" in _values(s, v, "form_endpoint_host"), key
        assert "APPA-165" in _values(s, v, "api_key_id"), key
        assert "/v1/landing/form-event" in _values(s, v, "form_endpoint_path"), key
    assert "crimsonbase.pro" in _values(s, _view(s, "3:page"), "outbound.domain")
    v47 = _view(s, "47:page")
    assert "/lander/order.php" in _values(s, v47, "form_endpoint_path")
    assert _values(s, v47, "aff.sub2") == {"Yosha"}
    assert _values(s, v47, "aff.sid") == {"1-48"}
    assert _values(s, v47, "aff.x_id") == {"4084"}
    # Catalogue A15/D11: the credential trap hidden in a file named like a library.
    assert "lerandbigdicker.com" in _values(s, _view(s, "36:page"), "outbound.domain")


def test_vendor_libraries_add_no_endpoints(sample_store):
    """fbevents.js and the browser's translate widget are not the operator's endpoints."""
    s = sample_store
    for v in s.views():
        paths = _values(s, v, "form_endpoint_path")
        assert not paths & {"/tr", "/privacy_sandbox/topics/registration", "/translate_voting"}


# Incidents the manual reading links (signal catalogue). Shared identifiers may only
# fall inside these. Kit-tier values may also span the whole 1, 2, 48, 61 family.
_LINKED = [{"1", "2"}, {"2", "3"}, {"48", "61"}]
_FAMILY = {"1", "2", "48", "61"}
_KIT_TIER = {
    "aio.template_uuid",
    "aio.landing_type_uuid",
    "aio.source_name",
    "form_endpoint_path",
    "api_key_id",
    "public_key",
    "push_public_key",
    "third_party.host",
}


def test_no_false_links_across_incidents(sample_store):
    """Any value two or more incidents share must be one the manual reading expects."""
    s = sample_store
    seen = defaultdict(set)
    for v in s.views():
        for o in s.observations(v.view_id):
            seen[(o.namespace, json.dumps(o.value, sort_keys=True))].add(v.incident_id)
    allowed = lambda ns, inc: any(inc <= g for g in _LINKED) or (ns in _KIT_TIER and inc <= _FAMILY)
    false_links = sorted(
        (ns, val, sorted(inc))
        for (ns, val), inc in seen.items()
        if len(inc) > 1 and not allowed(ns, inc)
    )
    assert false_links == []
