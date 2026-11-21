"""HAC-10 kit fingerprints on synthetic material: names and shapes, never per-click values."""

import json

import pytest

from ftf.extract import _material
from ftf.extract.infra_headers import InfraHeaders, product
from ftf.extract.kit_config import KitConfig, key_paths, kit_names
from ftf.extract.kit_url import KitUrl, api_route, path_convention, segment_shape, value_shape

from .test_extract_identifiers import aio_script, page, run, values

VISIT = "01a11f55-dea2-71c8-81fb-5bdfdd77fe0b"
KIT_URL = (
    "https://civic.example.live/?utm_id=WVVfDj0sSpk725CZefaO&utm_medium=7DJYmtt5h2"
    f"&utm_campaign=5DkJlRt5Wj&utm_content=tlJwInE50J&utm_visit={VISIT}"
)
META_URL = (
    "https://shop.example.org/?utm_source=fb&utm_medium=paid&utm_campaign=120254577452780651"
    "&utm_content=x&utm_term=y&utm_id=120254577452780651&ad_id=120254577452770651"
    "&adset_id=120254577452770652&adset_name=PL&ad_name=PL&campaign_id=120254577452780651"
    "&fbclid=PAYm9jbATq-XNwZG9mAmZkaWQWUMbgWlp5dSfdyB6Rn3TS"
)


@pytest.fixture(autouse=True)
def _fresh_cache():
    _material.clear_cache()


# --- shapes ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "shape"),
    [
        (VISIT, "uuid"),
        ("120254577452780651", "digits18"),
        ("WVVfDj0sSpk725CZefaO", "alnum20"),
        ("db4aoqm87rcs738c8m4g", "lower20"),
        ("05f1233dfd0a3f45a93c4d3b8874db3a0efdbd2611", "hex42"),
        ("PL", "upper2"),
        ("Others", "alpha6"),
        ("PL - test", "text"),
    ],
)
def test_value_shape(value, shape):
    assert value_shape(value) == shape


@pytest.mark.parametrize(
    ("path", "convention"),
    [
        ("/lander/prstt-pl-rmxlfrtm-svshnnk-x-tw/index.html", "/lander/<a-a-a-a-a-a>/"),
        ("/landers/wood_music_soda_treasure_reaction_skirt/", "/landers/<a_a_a_a_a_a>/"),
        ("/L41156846fl-923e569-fkf418Z/", "/<A9a-9a9-a9A>/"),
        (
            "/_cdn/production/landing-cdn/f141c307-7ca2-41d9-b311-5ed107964c8b/x/favicon.ico",
            "/_cdn/production/landing-cdn/<uuid>/",
        ),
        ("/_cdn/production/landing-cdn/666/abc/", "/_cdn/production/landing-cdn/<num>/"),
        ("/", None),
        ("/css/style.css", None),  # a lone literal folder says nothing
        ("/gKcXtVhV", None),  # a file at the root
        ("/_next/static/chunks/main.js", None),
        ("/api/v1/trigger/field/x/", None),  # API paths are B6
    ],
)
def test_path_convention(path, convention):
    assert path_convention(path) == convention


def test_segment_shape_literal_words():
    assert segment_shape("landing-cdn") == ("landing-cdn", True)
    assert segment_shape("12345") == ("<num>", False)


@pytest.mark.parametrize(
    ("path", "route"),
    [
        (f"/api/v1/trigger/field/{VISIT}/", "/api/v1/trigger/field/"),
        ("/api/v1/trigger/field/", "/api/v1/trigger/field/"),
        (f"/api/v1/agent/persist-session-record/{VISIT}/{VISIT}", "/api/v1/agent/persist-session-record/"),
        ("/api/v1/agent/pub-key-push-settings/", "/api/v1/agent/pub-key-push-settings/"),
        ("/assets/app.js", None),
        ("/api/", None),
    ],
)  # fmt: skip
def test_api_route(path, route):
    assert api_route(path) == route


# --- URL parameters (B1, B2) -------------------------------------------------------------


def test_kit_params_from_landing_url(store, tmp_path):
    obs = run(KitUrl(), store, page(tmp_path / "v", url=KIT_URL))
    assert values(obs, "url.param_name") == ["utm_visit"]
    assert set(values(obs, "url.param_shape")) == {"utm_visit:uuid", "utm_id:alnum20"}


def test_standard_params_produce_nothing(store, tmp_path):
    obs = run(KitUrl(), store, page(tmp_path / "v", url=META_URL))
    assert obs == []


def test_per_click_values_never_leave(store, tmp_path):
    url = KIT_URL + "&clickid=db4aoqm87rcs738c8m4g&fbclid=PAYm9jbATq-XNwZG9mAmZkaWQ"
    obs = run(KitUrl(), store, page(tmp_path / "v", url=url))
    emitted = json.dumps([o.value for o in obs])
    for secret in (VISIT, "db4aoqm87rcs738c8m4g", "PAYm9jbATq", "WVVfDj0sSpk725CZefaO"):
        assert secret not in emitted
    assert "clickid:lower20" in values(obs, "url.param_shape")


# --- paths and routes (B5, B6) -----------------------------------------------------------


def test_base_href_inside_a_script_is_found(store, tmp_path):
    body = (
        "<script>document.write('<base href=\"//x.example.life/landers/wood_music_soda_treasure_reaction_skirt/\">')"
        "</script><base href='.'>"
    )
    obs = run(KitUrl(), store, page(tmp_path / "v", body=body))
    assert "/landers/<a_a_a_a_a_a>/" in values(obs, "url.path_shape")


def test_api_routes_from_har_and_scripts_agree(store, tmp_path):
    url = "https://civic.example.live/"
    har = [
        {
            "request": {"url": f"{url}api/v1/trigger/field/{VISIT}/", "method": "POST"},
            "response": {"status": 200, "content": {"mimeType": "application/json"}},
        },
        {  # someone else's API is not the page's
            "request": {"url": "https://other.example.com/api/v2/collect/", "method": "POST"},
            "response": {"status": 200, "content": {}},
        },
    ]
    head = "<script>fetch('/api/v1/trigger/field/' + id); post(\"/api/v1/pwa/manifest/\")</script>"
    obs = run(KitUrl(), store, page(tmp_path / "v", url=url, head=head, har=har))
    assert sorted(values(obs, "api.route")) == ["/api/v1/pwa/manifest/", "/api/v1/trigger/field/"]


# --- config, forms, declared kit (B3, B4, B8) --------------------------------------------


def test_config_key_paths_skip_visitor_data():
    config = {
        "campaign": {"uuid": "x", "name": "n"},
        "landed_on_landings_uuids": {"91837506-95eb-4263-946d-73d5d4f62c73": 1},
        "landing": {"folder": [{"uuid": "a"}, {"uuid": "b", "name": "c"}]},
        "visit": {"ip": "1.2.3.4", "city": "Warsaw"},
        "session": {"ua": "Mozilla"},
    }
    assert key_paths(config) == [
        "campaign.name",
        "campaign.uuid",
        "landed_on_landings_uuids.*",
        "landing.folder[].name",
        "landing.folder[].uuid",
    ]


def test_kit_config_on_a_page(store, tmp_path):
    config = {
        "campaign": {"uuid": "x"},
        "source": {"name": "AIO FB CAPI via Cloakerson [Use This]"},
    }
    body = (
        aio_script(config)
        + '<form><input type="hidden" name="sub1" value="a"><input type="hidden" name="X_ID">'
        + '<input type="hidden" name="fd1a0e09e83e22558893e94cdd2481b2" value="1"></form>'
    )
    obs = run(KitConfig(), store, page(tmp_path / "v", body=body))
    assert set(values(obs, "config.key_path")) == {"campaign.uuid", "source.name"}
    assert sorted(values(obs, "form.hidden_name")) == ["sub1", "x_id"]  # the token is dropped
    assert values(obs, "kit.cloaker") == ["cloakerson"]


def test_kit_names():
    assert kit_names("PL | Keitaro flow") == {"keitaro"}
    assert kit_names("Polish news") == set()


# --- server headers (C10) ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "name"),
    [("1.1 Caddy", "caddy"), ("0.0 Caddy", "caddy"), ("nginx/1.25.3", "nginx"),
     ("Express", "express"), ("cloudflare", None), ("", None)],
)  # fmt: skip
def test_product(value, name):
    assert product(value) == name


def test_headers_from_own_domain_only(store, tmp_path):
    url = "https://healthlinkq.example.pro/L1/"
    headers = [{"name": "Server", "value": "cloudflare"}, {"name": "Via", "value": "1.1 Caddy"}]
    har = [
        {
            "request": {"url": url + "a.css", "method": "GET"},
            "response": {"status": 200, "headers": headers, "content": {}},
        },
        {
            "request": {"url": "https://cdn.other.com/x.js", "method": "GET"},
            "response": {"status": 200, "headers": [{"name": "Server", "value": "nginx"}]},
        },
    ]
    obs = run(InfraHeaders(), store, page(tmp_path / "v", url=url, har=har))
    assert values(obs, "http.via") == ["caddy"]
    assert values(obs, "http.server") == []


@pytest.mark.parametrize("extractor", [KitUrl(), KitConfig(), InfraHeaders()])
def test_broken_material_never_raises(store, tmp_path, extractor):
    root = page(tmp_path / "v", body="<script>window.aio = {", har=[])
    (root / "civic.example.live.har").write_text("{not json")
    (root / "www.shop.example.org.har").write_text("{not json")
    assert isinstance(run(extractor, store, root), list)
