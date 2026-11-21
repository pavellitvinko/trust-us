"""HAC-9: where a page sends the visitor and their data. Synthetic material only."""

import base64
import json

import pytest

from ftf.extract import _material
from ftf.extract.conversion import ConversionTargets, iban_valid
from ftf.ingest import ingest_folder
from ftf.registry import Context

from .test_extract_identifiers import page, run, values


@pytest.fixture(autouse=True)
def _fresh_cache():
    _material.clear_cache()


def conv(store, root):
    return run(ConversionTargets(), store, root)


def test_form_endpoint_from_js_constant_and_concat(store, tmp_path):
    head = (
        "<script>const LANDING_FORM_EVENT_BACKEND_DOMAIN = 'https://api.shadowtracecore.com';"
        "const CDN_URL = 'https://img.example-cdn.net/';"
        "const APPA_KEY_ID = 'APPA-165';"
        "fetch(LANDING_FORM_EVENT_BACKEND_DOMAIN + '/v1/landing/form-event', {method:'POST'});"
        "</script>"
    )
    obs = conv(store, page(tmp_path / "1", head=head))
    assert values(obs, "form_endpoint_host") == ["api.shadowtracecore.com"]
    assert values(obs, "form_endpoint_path") == ["/v1/landing/form-event"]
    assert values(obs, "api_key_id") == ["APPA-165"]
    assert "img.example-cdn.net" not in json.dumps([o.value for o in obs])
    host = next(o for o in obs if o.namespace == "form_endpoint_host")
    assert host.signal_id == "A7" and host.source.locator == "html:script[0]"


def test_relative_form_action_gives_path_not_host(store, tmp_path):
    body = (
        '<form action="/lander/order.php" method="post">'
        '<input type="hidden" name="sid" value="1-48">'
        '<input type="hidden" name="x_id" value="4084">'
        '<input type="hidden" name="sub2" value="Yosha">'
        '<input type="hidden" name="sub_id_5" value="abc">'
        '<input type="hidden" name="fbtoken" value="{fbtoken}">'
        '<input type="hidden" name="country" value="PL">'
        "</form>"
    )
    obs = conv(store, page(tmp_path / "1", body=body))
    assert values(obs, "form_endpoint_path") == ["/lander/order.php"]
    assert values(obs, "form_endpoint_host") == [] and values(obs, "outbound.domain") == []
    got = {o.namespace: o.value for o in obs if o.namespace.startswith("aff.")}
    assert got == {"aff.sid": "1-48", "aff.x_id": "4084", "aff.sub2": "Yosha", "aff.sub5": "abc"}


def test_foreign_form_action(store, tmp_path):
    body = '<form action="https://crm.goods-crm.com/api/lead?user_id=13&_trk=bf1"></form>'
    obs = conv(store, page(tmp_path / "1", body=body))
    assert values(obs, "form_endpoint_host") == ["crm.goods-crm.com"]
    assert values(obs, "form_endpoint_path") == ["/api/lead"]
    assert values(obs, "outbound.domain") == ["goods-crm.com"]
    assert values(obs, "aff.user_id") == ["13"] and values(obs, "aff.tracker") == ["bf1"]


def test_outbound_from_links_beacons_and_har(store, tmp_path):
    body = (
        '<a href="https://go.offer-one.com/click">Buy</a>'
        '<a href="/about">About</a><a href="https://www.shop.example.org/x">Own</a>'
        '<a href="https://www.facebook.com/share">Share</a>'
        '<a href="#" onclick="window.location.href=\'https://redirect-two.net/r\'">Go</a>'
        "<script>navigator.sendBeacon('https://beacon-three.pro/c', data);</script>"
    )
    har = [
        {"request": {"url": "https://post-four.org/api", "method": "POST"}},
        {"request": {"url": "https://fetch-five.io/p", "method": "GET"}, "_resourceType": "fetch"},
        {
            "request": {"url": "https://img-six.com/a.png", "method": "GET"},
            "_resourceType": "image",
        },
    ]
    obs = conv(store, page(tmp_path / "1", body=body, har=har))
    assert set(values(obs, "outbound.domain")) == {
        "offer-one.com",
        "redirect-two.net",
        "beacon-three.pro",
        "post-four.org",
        "fetch-five.io",
    }
    assert {o.signal_id for o in obs if o.namespace == "outbound.domain"} == {"A15"}


def test_har_only_view_reaches_target_through_base64_body(store, tmp_path):
    root = tmp_path / "2"
    root.mkdir()
    body = "<html><script>navigator.sendBeacon('https://crimsonbase.pro/t');</script></html>"
    entries = [
        {
            "request": {"url": "https://healthlinkq.pro/a/", "method": "GET"},
            "response": {
                "status": 200,
                "content": {
                    "mimeType": "text/html",
                    "encoding": "base64",
                    "text": base64.b64encode(body.encode()).decode(),
                },
            },
            "_resourceType": "document",
        },
        {
            "request": {"url": "https://crimsonbase.pro/c?x=1", "method": "GET"},
            "_resourceType": "fetch",
        },
    ]
    (root / "landing.har").write_text(json.dumps({"log": {"entries": entries}}))
    _, views = ingest_folder(root, store)
    obs = ConversionTargets().run(views[0], Context(store))
    assert values(obs, "outbound.domain") == ["crimsonbase.pro"]
    assert obs[0].source.locator.startswith("har:entries[0].response.content>html:script[0]")


def test_third_party_hosts(store, tmp_path):
    head = (
        '<script src="https://cdn.puuuuuush-king.org/p.js"></script>'
        '<script src="https://code.jquery.com/jquery.js"></script>'
        '<script src="/local.js"></script>'
    )
    har = [
        {"request": {"url": "https://bf1.clickline.org/t.js"}, "_resourceType": "script"},
        {"request": {"url": "https://fonts.example-fonts.com/f.woff"}, "_resourceType": "font"},
    ]
    obs = conv(store, page(tmp_path / "1", head=head, har=har))
    assert set(values(obs, "third_party.host")) == {"cdn.puuuuuush-king.org", "bf1.clickline.org"}


def test_keys(store, tmp_path):
    pem = "-----BEGIN PUBLIC KEY-----\\nMIIBIjANBgkq\\nhkiG9w0BAQEF\\n-----END PUBLIC KEY-----"
    aio = json.dumps({"push": {"key": "BPushKey123"}})
    head = f"<script>var k = '{pem}'; var cfg = {{keyId: 'K-77'}};</script>"
    head += f"<script>window.aio = {aio};</script>"
    body = '<input type="hidden" name="api_key" value="aff-secret-1">'
    obs = conv(store, page(tmp_path / "1", head=head, body=body))
    assert values(obs, "api_key_id") == ["K-77"]
    [fp] = values(obs, "public_key")
    assert fp.startswith("sha256:") and len(fp) == 71
    assert values(obs, "push_public_key") == ["BPushKey123"]
    assert values(obs, "aff.api_key") == ["aff-secret-1"]


def test_same_pem_with_different_line_breaks_has_one_fingerprint(store, tmp_path):
    a = "-----BEGIN PUBLIC KEY-----\nAAAA\nBBBB\n-----END PUBLIC KEY-----"
    b = "-----BEGIN PUBLIC KEY-----\\nAAAABBBB\\n-----END PUBLIC KEY-----"
    obs_a = conv(store, page(tmp_path / "a", head=f"<script>var k=`{a}`</script>"))
    obs_b = conv(store, page(tmp_path / "b", head=f"<script>var k='{b}'</script>"))
    assert values(obs_a, "public_key") == values(obs_b, "public_key")


def test_offer_name_and_offer_id(store, tmp_path):
    body = (
        '<input type="hidden" name="offer" value="Metabo RX">'
        '<input type="hidden" name="product" value="9f1c1b0e-1111-4000-8000-000000000003">'
    )
    obs = conv(store, page(tmp_path / "1", body=body))
    assert values(obs, "offer.name") == ["Metabo RX"]
    assert values(obs, "aff.offer_uuid") == ["9f1c1b0e-1111-4000-8000-000000000003"]
    name = next(o for o in obs if o.namespace == "offer.name")
    assert name.signal_id == "A13" and name.purpose.value == "both"


def test_contacts(store, tmp_path):
    body = (
        '<a href="tel:+48 600-100-200">Call</a>'
        '<a href="mailto:Kontakt@Example-Shop.pl?subject=x">Mail</a>'
        '<a href="https://t.me/@InvestBot_PL">TG</a>'
        '<a href="https://wa.me/48600100200">WA</a>'
        '<a href="https://api.whatsapp.com/send?phone=48600100201">WA2</a>'
        "<p>Wpłać na konto PL61 1090 1014 0000 0712 1981 2874</p>"
        "<p>Nie konto: PL00 1234 5678 9012 3456 7890 1234</p>"
        "<script>var token='DE89370400440532013000';</script>"
        "<p>Tel. 600 100 200</p>"
    )
    obs = conv(store, page(tmp_path / "1", body=body))
    assert values(obs, "contact.phone") == ["+48600100200"]
    assert values(obs, "contact.email") == ["kontakt@example-shop.pl"]
    assert values(obs, "contact.telegram") == ["investbot_pl"]
    assert values(obs, "contact.whatsapp") == ["48600100200", "48600100201"]
    assert values(obs, "contact.iban") == ["PL61109010140000071219812874"]


def test_iban_checksum():
    assert iban_valid("PL61 1090 1014 0000 0712 1981 2874")
    assert iban_valid("DE89370400440532013000")
    assert not iban_valid("PL00123456789012345678901234")


def test_noise_and_own_domain_never_outbound(store, tmp_path):
    body = (
        '<a href="https://shop.example.org/other">own, other sub-domain</a>'
        '<a href="https://www.google.com/">noise</a>'
        '<form action="https://connect.facebook.net/x"></form>'
        '<form id="goog-gt-votingForm" action="https://translate.googleapis.com/translate_voting">'
        "</form>"
    )
    obs = conv(store, page(tmp_path / "1", body=body))
    assert values(obs, "outbound.domain") == [] and values(obs, "form_endpoint_host") == []
    assert values(obs, "form_endpoint_path") == []


def test_never_raises(store, tmp_path):
    root = tmp_path / "bad"
    root.mkdir()
    (root / "page.html").write_bytes(
        b"<!-- saved from url=(0030)https://bad.example.org/ -->\n<a href='http://[::1'>"
        b"<a href='tel:'><a href='mailto:@'><form action='https://[bad'>"
        b"<script>X_API_URL='//';fetch('http://');var k='-----BEGIN PUBLIC KEY-----"
    )
    (root / "bad.example.org.har").write_text('{"log": {"entries": [null, {"response": "x"}]}}')
    _, views = ingest_folder(root, store)
    for view in views:
        assert isinstance(ConversionTargets().run(view, Context(store)), list)


def test_onclick_beacon_is_outbound(store, tmp_path):
    # Case 3: the CTA's href stays on the page; the click is reported by a beacon.
    body = (
        '<a href="#order" onclick="navigator.sendBeacon('
        "'https://crimsonbase.pro/click?lp=1&amp;bcid=x')\">Zamów</a>"
    )
    obs = conv(store, page(tmp_path / "1", body=body))
    assert values(obs, "outbound.domain") == ["crimsonbase.pro"]
    assert obs[0].source.locator == "html:a[0].href>onclick"


def test_vendor_library_in_files_is_not_the_operator(store, tmp_path):
    files = {
        "fbevents.js.pobrane": "var TRACKING_API_PATH='/tr'; fetch('https://www.fbapi.example/x');",
        "app.js.pobrane": "const ORDER_API_URL='https://lead.example-crm.com/api/order';",
    }
    obs = conv(store, page(tmp_path / "1", files=files))
    assert values(obs, "form_endpoint_path") == ["/api/order"]
    assert values(obs, "form_endpoint_host") == ["lead.example-crm.com"]


# --- review of PR #2 ----------------------------------------------------------------------


def test_messengers_are_matched_by_hostname(store, tmp_path):
    body = (
        '<a href="https://www.visit.me/promo">not telegram</a>'
        '<a href="https://example.com/?next=https://t.me/hidden">telegram in a query</a>'
        '<a href="https://shop.example.com/t.me/path">telegram in a path</a>'
        '<a href="https://t.me/joinchat/AAAAAE">invite link, no handle</a>'
        '<a href="https://notwa.me/48600100200">not whatsapp</a>'
        '<a href="https://whatsapp.com/channel?phone=48600100299">not a send link</a>'
        '<a href="https://www.t.me/RealHandle">real</a>'
        '<a href="https://web.whatsapp.com/send?phone=48600100202">real</a>'
    )
    obs = conv(store, page(tmp_path / "1", body=body))
    assert values(obs, "contact.telegram") == ["realhandle"]
    assert values(obs, "contact.whatsapp") == ["48600100202"]


def test_relative_form_action_keeps_host_resolved_through_base(store, tmp_path):
    head = '<base href="https://crm.target.net/leads/">'
    body = '<form action="submit.php" method="post"></form>'
    obs = conv(store, page(tmp_path / "1", head=head, body=body))
    assert values(obs, "form_endpoint_host") == ["crm.target.net"]
    assert values(obs, "form_endpoint_path") == ["/leads/submit.php"]
    assert values(obs, "outbound.domain") == ["target.net"]
