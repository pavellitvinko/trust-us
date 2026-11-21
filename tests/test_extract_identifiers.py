"""HAC-8 extractors on synthetic material: one test per location an identifier can hide in."""

import json
from pathlib import Path

import pytest

from ftf.extract import _material
from ftf.extract.ad_params import AdParams
from ftf.extract.tracker_config import TrackerConfig
from ftf.extract.tracking_ids import TrackingPixels
from ftf.ingest import ingest_folder
from ftf.registry import Context

PIXEL = "2012165449475163"
CAMPAIGN = "0b9611d9-1111-4000-8000-000000000001"
TEMPLATE = "25c6c6fd-2222-4000-8000-000000000002"


@pytest.fixture(autouse=True)
def _fresh_cache():
    _material.clear_cache()


def page(
    root: Path,
    body: str = "",
    head: str = "",
    url: str = "https://www.shop.example.org/L1/",
    files: dict[str, str] | None = None,
    har: list[dict] | None = None,
) -> Path:
    root.mkdir(parents=True)
    (root / "page.html").write_text(
        f"<!-- saved from url=({len(url):04d}){url} -->\n"
        f"<html><head>{head}</head><body>{body}</body></html>",
        encoding="utf-8",
    )
    if files:
        (root / "page_files").mkdir()
        for name, text in files.items():
            (root / "page_files" / name).write_text(text)
    if har is not None:
        doc = {
            "request": {"url": url, "method": "GET"},
            "response": {"status": 200, "content": {"mimeType": "text/html"}},
            "_resourceType": "document",
        }
        host = url.split("/")[2]
        (root / f"{host}.har").write_text(json.dumps({"log": {"entries": [doc, *har]}}))
    return root


def run(extractor, store, root):
    _, views = ingest_folder(root, store)
    view = next(v for v in views if v.primary == "page.html")
    return extractor.run(view, Context(store))


def values(observations, namespace):
    return [o.value for o in observations if o.namespace == namespace]


def aio_script(config) -> str:
    literal = json.dumps(json.dumps(config))[1:-1].replace("'", "\\'")
    return f"<script>window.aio = JSON.parse('{literal}');</script>"


# --- tracker config ----------------------------------------------------------------------

AIO = {
    "campaign": {"uuid": CAMPAIGN, "owner_uuid": "own-1", "name": "1038 | PL - Test Crime 2"},
    "landing": {
        "uuid": "land-1",
        "name": "PL / Camp1",
        "lander_template_uuid": TEMPLATE,
        "lander_type_uuid": "type-1",
        "folder": [{"uuid": "fold-1", "name": "OPER"}],
    },
    "source": {"uuid": "src-1", "name": "AIO FB CAPI via Cloakerson"},
    "visit": {
        "ip": "10.1.2.3",
        "city": "Warszawa",
        "user_agent": "Mozilla/5.0",
        "campaign_human_id": 1038,
        "landing_human_ids": [17, 18],
        "fields": {"fb_pixel": PIXEL},
    },
    "session": {"id": "sess-should-not-appear"},
}


def test_tracker_config_whitelist(store, tmp_path):
    obs = run(TrackerConfig(), store, page(tmp_path / "1", head=aio_script(AIO)))
    got = {(o.namespace, o.value) for o in obs}
    assert ("aio.campaign_uuid", CAMPAIGN) in got
    assert ("aio.template_uuid", TEMPLATE) in got
    assert ("aio.folder_uuid", "fold-1") in got and ("aio.folder_name", "OPER") in got
    assert ("aio.campaign_human_id", "1038") in got
    assert {v for n, v in got if n == "aio.landing_human_id"} == {"17", "18"}
    assert ("aio.source_name", "AIO FB CAPI via Cloakerson") in got
    flat = json.dumps([o.model_dump() for o in obs])
    for leaked in ("10.1.2.3", "Warszawa", "Mozilla", "sess-should-not-appear"):
        assert leaked not in flat
    campaign = next(o for o in obs if o.namespace == "aio.campaign_uuid")
    assert campaign.signal_id == "A1"
    assert campaign.source.locator == "html:script[0]>aio:campaign.uuid"
    name = next(o for o in obs if o.namespace == "aio.campaign_name")
    assert name.purpose.value == "both"


def test_tracker_config_in_object_literal_and_script_file(store, tmp_path):
    files = {"app.js.pobrane": "window.aio = " + json.dumps(AIO) + ";"}
    obs = run(TrackerConfig(), store, page(tmp_path / "1", files=files))
    assert values(obs, "aio.campaign_uuid") == [CAMPAIGN]
    assert obs[0].source.file == "page_files/app.js.pobrane"


def test_tracker_config_absent_or_macro(store, tmp_path):
    assert run(TrackerConfig(), store, page(tmp_path / "1")) == []
    macro = {"campaign": {"uuid": "{{aio.campaign.uuid}}"}}
    assert run(TrackerConfig(), store, page(tmp_path / "2", head=aio_script(macro))) == []


# --- pixels: one location each -----------------------------------------------------------


def test_pixel_from_init_call(store, tmp_path):
    head = f"<script>fbq('init', '{PIXEL}'); fbq('track', 'PageView');</script>"
    obs = run(TrackingPixels(), store, page(tmp_path / "1", head=head))
    assert values(obs, "fb_pixel") == [PIXEL]
    assert obs[0].signal_id == "A3" and obs[0].source.locator == "html:script[0]"


def test_pixel_from_noscript_url(store, tmp_path):
    body = (
        f'<noscript><img src="https://www.facebook.com/tr?ev=PageView&amp;id={PIXEL}"></noscript>'
    )
    obs = run(TrackingPixels(), store, page(tmp_path / "1", body=body))
    assert values(obs, "fb_pixel") == [PIXEL]


def test_pixel_from_landing_url_parameter(store, tmp_path):
    url = f"https://shop.example.org/L1/?utm_id=120200000000000000&pxl={PIXEL}"
    obs = run(TrackingPixels(), store, page(tmp_path / "1", url=url))
    assert values(obs, "fb_pixel") == [PIXEL]
    assert obs[0].source.locator.endswith("url:query.pxl")


def test_pixel_from_form_action_parameter(store, tmp_path):
    body = f'<form action="/order.php?fbpx={PIXEL}"></form>'
    obs = run(TrackingPixels(), store, page(tmp_path / "1", body=body))
    assert values(obs, "fb_pixel") == [PIXEL]


def test_pixel_from_hidden_input(store, tmp_path):
    body = f'<form><input type="hidden" name="fb_pixel" value="{PIXEL}"></form>'
    obs = run(TrackingPixels(), store, page(tmp_path / "1", body=body))
    assert values(obs, "fb_pixel") == [PIXEL]
    assert obs[0].source.locator == "html:input[0][name=fb_pixel]"


def test_pixel_from_config_key(store, tmp_path):
    obs = run(TrackingPixels(), store, page(tmp_path / "1", head=aio_script(AIO)))
    assert values(obs, "fb_pixel") == [PIXEL]
    assert obs[0].source.locator.endswith("aio:visit.fields.fb_pixel")


def test_pixel_from_files_name(store, tmp_path):
    obs = run(TrackingPixels(), store, page(tmp_path / "1", files={PIXEL: "", "logo.png": ""}))
    assert values(obs, "fb_pixel") == [PIXEL]
    assert obs[0].source.file == f"page_files/{PIXEL}"


def test_pixel_from_har_url(store, tmp_path):
    har = [{"request": {"url": f"https://connect.facebook.net/signals/config/{PIXEL}?v=2"}}]
    obs = run(TrackingPixels(), store, page(tmp_path / "1", har=har))
    assert values(obs, "fb_pixel") == [PIXEL]
    assert obs[0].source.locator == "har:entries[1].request.url"


def test_pixel_needs_context(store, tmp_path):
    url = "https://shop.example.org/L1/?ad_id=120211111111111&utm_id=120222222222222"
    body = "<p>Order 1234567890123456 today</p>"
    obs = run(TrackingPixels(), store, page(tmp_path / "1", url=url, body=body))
    assert values(obs, "fb_pixel") == []


def test_one_pixel_many_locations_is_one_observation(store, tmp_path):
    head = aio_script(AIO) + f"<script>fbq('init','{PIXEL}')</script>"
    obs = run(TrackingPixels(), store, page(tmp_path / "1", head=head, files={PIXEL: ""}))
    assert values(obs, "fb_pixel") == [PIXEL]
    assert obs[0].source.locator.endswith("aio:visit.fields.fb_pixel")  # most trusted first


# --- Google and other vendors ------------------------------------------------------------


def test_google_ids(store, tmp_path):
    head = (
        '<script async src="https://www.googletagmanager.com/gtag/js?id=AW-11111111"></script>'
        "<script>gtag('config', 'AW-11111111'); gtag('config', 'G-ABC123XYZ9');"
        "gtag('event', 'conversion', {'send_to': 'AW-11111111/AbC-dEf_1'});</script>"
        "<script>(function(w,d,s,l,i){})(window,document,'script','dataLayer','GTM-K9ZXQ2');</script>"
    )
    obs = run(TrackingPixels(), store, page(tmp_path / "1", head=head))
    assert values(obs, "gads_id") == ["AW-11111111"]
    assert values(obs, "gads_conversion") == ["AW-11111111/AbC-dEf_1"]
    assert values(obs, "ga_id") == ["G-ABC123XYZ9"]
    assert values(obs, "gtm_id") == ["GTM-K9ZXQ2"]
    assert {o.signal_id for o in obs} == {"A4"}


def test_google_id_without_context_is_ignored(store, tmp_path):
    body = "<p>Model G-ABCDEF12 and AW-12345678 in prose</p>"
    assert run(TrackingPixels(), store, page(tmp_path / "1", body=body)) == []


def test_other_vendor_pixels(store, tmp_path):
    head = (
        "<script>qp('init', '0123456789abcdef0123456789abcdef');</script>"
        "<script>twq('config','o1abc');</script>"
        "<script>ttq.load('C1234567890ABCDEFGHI');</script>"
        "<script>var o={ti:'187000123', enableAutoSpaTracking: true}; window.uetq=[];</script>"
        "<script>window._tfa.push({notify: 'event', name: 'page_view', id: 1234567});</script>"
        "<script>ym(98765432, 'init', {clickmap:true});</script>"
    )
    obs = run(TrackingPixels(), store, page(tmp_path / "1", head=head))
    assert values(obs, "quora_pixel") == ["0123456789abcdef0123456789abcdef"]
    assert values(obs, "twitter_pixel") == ["o1abc"]
    assert values(obs, "tiktok_pixel") == ["C1234567890ABCDEFGHI"]
    assert values(obs, "bing_uet") == ["187000123"]
    assert values(obs, "taboola_id") == ["1234567"]
    assert values(obs, "yandex_metrica") == ["98765432"]


def test_vendor_presence_without_id_emits_nothing(store, tmp_path):
    head = (
        "<script>!function(q,e,v,n){q.qp=function(){}}(window);qp('track','ViewContent');</script>"
    )
    assert run(TrackingPixels(), store, page(tmp_path / "1", head=head)) == []


# --- ad parameters -----------------------------------------------------------------------


def test_ad_params_from_landing_url(store, tmp_path):
    url = (
        "https://shop.example.org/L1/?utm_source=fb&utm_id=120200000000000001"
        "&utm_campaign={{campaign.id}}&ad_id=120200000000000002&adset_name=hk77_tv6_PL"
        "&ad_name=video3&acc=PL78T&access_token=EAAB123secret&fbclid=IwAR0abc"
        "&clickid=abcdefghijklmnopqrst&utm_visit=6f1c1b0e-1111-4000-8000-000000000003"
    )
    obs = run(AdParams(), store, page(tmp_path / "1", url=url))
    got = {o.namespace: o.value for o in obs}
    assert got == {
        "ad.campaign_id": "120200000000000001",
        "ad.ad_id": "120200000000000002",
        "ad.adset_name": "hk77_tv6_PL",
        "ad.ad_name": "video3",
        "ad.account_label": "PL78T",
        "ad.access_token": "EAAB123secret",
    }
    token = next(o for o in obs if o.namespace == "ad.access_token")
    assert token.signal_id == "A11" and token.purpose.value == "both"


def test_ad_params_kit_utm_id_is_not_a_campaign_id(store, tmp_path):
    url = "https://shop.example.org/L1/?utm_id=AbCdEfGhIjKlMnOpQrSt"
    assert run(AdParams(), store, page(tmp_path / "1", url=url)) == []


# --- robustness --------------------------------------------------------------------------


@pytest.mark.parametrize("extractor", [TrackerConfig(), TrackingPixels(), AdParams()])
def test_extractors_never_raise(store, tmp_path, extractor):
    root = tmp_path / "bad"
    root.mkdir()
    (root / "page.html").write_bytes(
        b"<!-- saved from url=(0030)https://bad.example.org/?pxl= -->\n<script>window.aio="
        b"JSON.parse('{\\x');fbq('init',<form action='http://[::1'><input type=hidden"
    )
    (root / "bad.example.org.har").write_text(
        '{"log": {"entries": [null, 3, {"request": 5}, {"response": "x"}]}}'
    )
    _, views = ingest_folder(root, store)
    for view in views:
        assert isinstance(extractor.run(view, Context(store)), list)
