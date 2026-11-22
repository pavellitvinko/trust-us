"""Funnel evidence on the namespaces the Perception track writes (image.advertiser,
page.headline, aio.campaign_name, image.text), not only the fixture names."""

from ftf.contracts import Observation
from ftf.grouping.funnel import funnel_evidence, same_ad_evidence


def obs(namespace: str, value: str, view: str = "v") -> Observation:
    return Observation(view_id=view, signal_id="X", namespace=namespace, value=value,
                       extractor="test", version="1")


def test_ad_brand_matches_the_page_headline():
    ad = [obs("image.advertiser", "Sweet Bonanza")]
    page = [obs("page.headline", "Sweet Bonanza"), obs("page.title", "App Store – Słodka Bonanza")]
    assert funnel_evidence(ad, page) == ["shared brand or offer: sweet bonanza"]


def test_advertiser_named_in_the_operator_campaign_label():
    ad = [obs("image.advertiser", "Glowpixel")]
    page = [obs("aio.campaign_name", "Ari_3665-D5BE07D5_NO glowpixel.org")]
    assert funnel_evidence(ad, page) == ["brand named on the page or its campaign: glowpixel"]


def test_a_page_title_containing_the_brand_is_not_enough():
    # A site with the brand's domain in its title may be a shared host or white page.
    ad = [obs("image.advertiser", "Glowpixel")]
    page = [obs("page.title", "Glowpixel.org — Portal analiz produkcji medialnej")]
    assert funnel_evidence(ad, page) == []


def test_platform_and_imitated_outlet_names_link_nothing():
    ad = [obs("image.brand_offer", "Instagram"), obs("image.brand_offer", "TVN24")]
    page = [obs("page.title", "TVN24"), obs("aio.campaign_name", "PL | instagram | tvn24 lead")]
    assert funnel_evidence(ad, page) == []
    assert same_ad_evidence(ad, [obs("image.brand_offer", "tvn24")]) == []


def test_two_ad_library_screenshots_of_one_ad():
    a = [obs("image.text", "Identyfikator biblioteki: 4414624315417273")]
    b = [obs("image.text", "Identyfikator biblioteki: 4414624315417273")]
    assert same_ad_evidence(a, b) == ["same Ad Library ID: 4414624315417273"]


def test_same_advertiser_and_a_shortened_offer_name():
    a = [obs("image.advertiser", "Sustain Studio"),
         obs("image.brand_offer", "OLANLY Bathroom Rugs 30x20, Extra Soft")]
    b = [obs("image.advertiser", "sustain studio"), obs("image.brand_offer", "Olanly")]
    assert same_ad_evidence(a, b) == [
        "same advertiser: sustain studio",
        "same brand or offer in both ads: olanly",
    ]


def test_different_advertisers_are_not_one_ad():
    a = [obs("image.advertiser", "Sustain Studio")]
    b = [obs("image.advertiser", "Fresh Perspectives")]
    assert same_ad_evidence(a, b) == []
