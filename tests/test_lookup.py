"""HAC-20: a bare identifier as a starting point (`ftf lookup`, `ftf start`)."""

import json

import pytest

from ftf.cli import main
from ftf.contracts import Incident, Observation, State, View, ViewKind
from ftf.lookup import is_address, lookup, registrable
from ftf.store import Store


def _obs(view_id, namespace, value, state=State.PRESENT):
    return Observation(
        view_id=view_id,
        signal_id="A3",
        namespace=namespace,
        value=value,
        state=state,
        extractor="test",
        version="1",
    )


def _fill(store: Store) -> None:
    for incident, views in {"1": ["1:page"], "2": ["2:page", "2:shot"]}.items():
        store.put_incident(
            Incident(incident_id=incident, root=f"/x/{incident}"),
            [],
            [
                View(view_id=v, incident_id=incident, kind=ViewKind.PAGE, label=f"label {v}")
                for v in views
            ],
        )
    store.put_observations(
        "1:page",
        "test",
        [
            _obs("1:page", "fb_pixel", "2012165449475163"),
            _obs("1:page", "brand.offer", "Świtowin Zyskarnia"),
            _obs("1:page", "domain.registrable", "civicdaybook.live"),
        ],
    )
    store.put_observations(
        "2:page",
        "test",
        [
            _obs("2:page", "fb_pixel", "2012165449475163"),
            _obs("2:page", "gtm_id", "GTM-ABC123"),
            _obs("2:page", "ad.ad_id", "2012165449475163"),  # same digits, other namespace
        ],
    )
    store.put_observations("2:shot", "test", [_obs("2:shot", "fb_pixel", None, State.ABSENT)])


@pytest.fixture
def filled(store):
    _fill(store)
    return store


@pytest.mark.parametrize(
    "text",
    [
        "example.com",
        "www.example.com",
        "https://x.pl/a?b=1",
        "http://h.example:8080/",
        "sub.offer.live/p",
        "Offer.Live",
        "shop.com.pl",
    ],
)
def test_addresses_are_recognised(text):
    assert is_address(text)


@pytest.mark.parametrize(
    "text",
    [
        "2012165449475163",
        "GTM-ABC123",
        "AW-18463917747",
        "Świtowin Zyskarnia",
        "",
        "v1.2",
        "Bitcoin.Era",  # dotted product names are not hosts
        "Dr.Max",
        "node.js",
        "index.html",
    ],
)
def test_identifiers_are_not_addresses(text):
    assert not is_address(text)


def test_lookup_returns_every_view_with_the_namespaces_it_matched(filled):
    hits = lookup(filled, "2012165449475163")
    assert [h.view_id for h in hits] == ["1:page", "2:page"]
    assert hits[1].namespaces == ["ad.ad_id", "fb_pixel"]
    assert hits[0].label == "label 1:page" and hits[0].incident_id == "1"
    assert all(h.exact for h in hits)


def test_lookup_narrows_to_namespace(filled):
    assert [h.view_id for h in lookup(filled, "2012165449475163", ["ad.ad_id"])] == ["2:page"]


def test_lookup_ignores_case_only_when_nothing_matches_exactly(filled):
    (hit,) = lookup(filled, "świtowin zyskarnia")  # non-ASCII case folding
    assert hit.view_id == "1:page" and not hit.exact
    (hit,) = lookup(filled, "gtm-abc123")
    assert hit.view_id == "2:page"


def test_lookup_skips_absent_observations_and_unknown_values(filled):
    assert lookup(filled, "null") == []
    assert lookup(filled, "nothing-here") == []
    assert lookup(filled, "   ") == []


def test_lookup_finds_values_stored_as_numbers(store):
    store.put_observations("9:page", "test", [_obs("9:page", "aff.user_id", 13)])
    (hit,) = lookup(store, "13")
    assert hit.view_id == "9:page" and hit.incident_id == "9"


@pytest.mark.parametrize(
    ("text", "domain"),
    [
        ("sub.offer.live", "offer.live"),
        ("https://WWW.Offer.Live/path", "offer.live"),
        ("m.shop.com.pl", "shop.com.pl"),
    ],
)
def test_registrable_matches_what_is_stored(text, domain):
    assert registrable(text) == domain


@pytest.fixture
def cli_store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FTF_DATA_DIR", str(tmp_path / "data"))
    store = Store(tmp_path / "data" / "ftf.sqlite")
    _fill(store)
    store.close()


def test_cli_lookup_prints_matches(cli_store, capsys):
    main(["lookup", "2012165449475163"])
    out = capsys.readouterr().out
    assert "1:page  [1]  label 1:page  via fb_pixel" in out
    assert "2:page  [2]  label 2:page  via ad.ad_id, fb_pixel" in out


def test_cli_lookup_json_and_miss(cli_store, capsys):
    main(["lookup", "GTM-ABC123", "--json"])
    (row,) = json.loads(capsys.readouterr().out)
    assert row["view_id"] == "2:page" and row["namespaces"] == ["gtm_id"]
    with pytest.raises(SystemExit) as exc:
        main(["lookup", "nope"])
    assert exc.value.code == 1
    assert "no stored view carries 'nope'" in capsys.readouterr().err


def test_cli_start_with_identifier_looks_it_up(cli_store, capsys):
    main(["start", "Świtowin Zyskarnia"])
    assert "1:page" in capsys.readouterr().out


class _Stop(Exception):
    pass


@pytest.fixture
def captured(monkeypatch):
    """Stop at the browser: record the URL `capture_url` would open."""
    from ftf import capture

    seen = {}

    def fake_capture_url(url, *a, **kw):
        seen["url"] = url
        raise _Stop

    monkeypatch.setattr(capture, "capture_url", fake_capture_url)
    return seen


def test_cli_start_with_bare_domain_captures(cli_store, tmp_path, captured, capsys):
    with pytest.raises(_Stop):
        main(["start", "sub.CivicDaybook.live", "--out", str(tmp_path / "inc"), "--no-enrich"])
    out = capsys.readouterr().out
    assert "already seen (1 views)" in out and "1:page" in out
    assert "(matched ignoring letter case)" not in out
    assert captured["url"] == "https://sub.CivicDaybook.live"


def test_cli_start_with_dotted_product_name_does_not_capture(cli_store, captured, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["start", "Bitcoin.Era"])
    assert exc.value.code == 1 and "url" not in captured
    assert "capturing" not in capsys.readouterr().out


def test_cli_start_refuses_other_schemes_before_anything_runs(cli_store, captured, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["start", "ftp://civicdaybook.live"])
    assert exc.value.code == 2 and "url" not in captured
    out, err = capsys.readouterr()
    assert out == "" and "only http and https" in err


def test_cli_lookup_json_miss_and_repeated_namespace(cli_store, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["lookup", "nope", "--json"])
    assert exc.value.code == 1 and json.loads(capsys.readouterr().out) == []
    main(["lookup", "2012165449475163", "--namespace", "fb_pixel", "--namespace", "gtm_id"])
    out = capsys.readouterr().out
    assert "via fb_pixel" in out and "ad.ad_id" not in out


def test_cli_capture_normalises_a_bare_domain(tmp_path, monkeypatch, captured):
    monkeypatch.setenv("FTF_DATA_DIR", str(tmp_path / "data"))
    with pytest.raises(_Stop):
        main(["capture", "example.com", "--out", str(tmp_path / "inc")])
    assert captured["url"] == "https://example.com"


def test_cli_capture_refuses_other_schemes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FTF_DATA_DIR", str(tmp_path / "data"))
    with pytest.raises(SystemExit) as exc:
        main(["capture", "file:///etc/passwd"])
    assert exc.value.code == 2
    assert "only http and https" in capsys.readouterr().err
