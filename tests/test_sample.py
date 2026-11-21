"""Checks against the organiser sample. Skipped where testing_sample/ is absent."""


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
