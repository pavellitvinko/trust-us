"""Same post, shared advertiser and shared account links between ad screenshots.

The Sustain Studio fixtures are hand-read model answers for six phone screenshots: two of the
Ad Library (IMG_3631, IMG_3632) and four of the feed (IMG_3625-IMG_3628). They go through the
same `_observations` the model output does.
"""

import json
from pathlib import Path

import pytest

from ftf.ad_links import build_ad_links
from ftf.contracts import Incident, Observation, Purpose, Source, View, ViewKind
from ftf.grouping.levels import Groups
from ftf.library import build_library
from ftf.perception.image import NAME, ImageReading, _observations

READINGS = Path(__file__).parent / "fixtures" / "sustain_studio" / "readings.json"
LIBRARY_ID = "2186769435230928"
NO_GROUPS = Groups(campaign_of={}, campaigns={}, family_of={}, families={}, funnel_edges={})


def shot(name, meta=None):
    return View(
        view_id=Path(name).stem, incident_id="s", kind=ViewKind.IMAGE,
        label=f"s · {name}", primary=name, meta=meta or {},
    )  # fmt: skip


@pytest.fixture
def sustain(store, tmp_path):
    readings = json.loads(READINGS.read_text(encoding="utf-8"))
    views = [shot(name) for name in readings]
    store.put_incident(Incident(incident_id="s", root=str(tmp_path)), [], views)
    for view in views:
        reading = ImageReading.model_validate(readings[view.primary])
        store.put_observations(view.view_id, NAME, _observations(view, reading))
    return build_library(store, NO_GROUPS, tmp_path)


def kinds(item, kind):
    return {link.other for link in item.ad_links if link.kind == kind}


def test_library_screenshots_show_the_id_written_on_them(sustain):
    for vid in ("IMG_3631", "IMG_3632"):
        item = sustain.get(vid)
        assert item.library_id == LIBRARY_ID and item.library_source == "ocr"
    # IMG_3632 shows it twice: once on one line, once split over two lines.
    assert [e.raw for e in sustain.get("IMG_3632").library_evidence] == [
        f"Identyfikator biblioteki: {LIBRARY_ID}",
        f"Identyfikator biblioteki: {LIBRARY_ID}",
    ]
    assert len({e.locator for e in sustain.get("IMG_3632").library_evidence}) == 2


def test_both_video_frames_are_the_same_post(sustain):
    # The presenter frame and the Maria frame: same advertiser, caption and button.
    assert kinds(sustain.get("IMG_3627"), "same_post") == {"IMG_3626", "IMG_3628"}
    assert kinds(sustain.get("IMG_3628"), "same_post") == {"IMG_3626", "IMG_3627"}
    link = next(
        x
        for x in sustain.get("IMG_3628").ad_links
        if x.other == "IMG_3627" and x.kind == "same_post"
    )
    assert link.evidence == (
        "Advertiser “Sustain Studio” on both",
        "Same post text: “Czas ucieka!”, “Poznaj wszystkie szczegóły 👉”",
        "Same button: “Dowiedz się więcej”",
    )
    # IMG_3625 hides the caption under the tab bar: nothing but the name to go on.
    assert not kinds(sustain.get("IMG_3625"), "same_post")


def test_the_library_screenshots_are_not_the_same_post_on_generic_text(sustain):
    # The Library card shows only "Czas ucieka!" and no button: too little to call it the post.
    for vid in ("IMG_3631", "IMG_3632"):
        assert not kinds(sustain.get(vid), "same_post")
    for vid in ("IMG_3625", "IMG_3626", "IMG_3627", "IMG_3628"):
        assert sustain.get(vid).library_id is None


def test_every_screenshot_is_possibly_the_same_advertiser(sustain):
    names = {f"IMG_{n}" for n in (3625, 3626, 3627, 3628, 3631, 3632)}
    for vid in names:
        item = sustain.get(vid)
        assert item.advertiser == "Sustain Studio"
        assert kinds(item, "possible_advertiser") == names - {vid}
        assert not kinds(item, "same_account")


def test_ad_links_never_name_a_campaign(sustain):
    for case in sustain.cases:
        for item in case.items:
            assert item.campaign is None and item.kit is None
            assert all(label.kind != "campaign" for label in item.labels)


# -- negative and account cases ------------------------------------------------------------

BOX = "image:region[0.1,0.1,0.3,0.03]"


def obs(view_id, namespace, raw):
    return Observation(
        view_id=view_id, signal_id="E11", namespace=namespace, value=raw.casefold(), raw=raw,
        purpose=Purpose.BOTH, source=Source(file=f"{view_id}.png", locator=BOX),
        extractor=NAME, version="2",
    )  # fmt: skip


def post(view_id, advertiser, *text, cta="Dowiedz się więcej"):
    rows = [obs(view_id, "image.advertiser", advertiser), obs(view_id, "image.text", advertiser)]
    rows += [obs(view_id, "image.text", t) for t in text]
    if cta:
        rows += [obs(view_id, "image.cta", cta), obs(view_id, "image.text", cta)]
    return rows


CAPTION = ("Poznaj wszystkie szczegóły 👉", "Czas ucieka!")


def page(page_id):
    return {"saved_from": f"https://www.facebook.com/ads/library/?view_all_page_id={page_id}"}


def links(views, observations):
    return build_ad_links({v.view_id: v for v in views}, observations)


def test_one_account_with_different_creatives_is_not_one_ad():
    views = [shot("a.png", page("100064111111111")), shot("b.png", page("100064111111111"))]
    out = links(views, {
        "a": post("a", "Sustain Studio", "Inwestuj mądrze z nami już dziś"),
        "b": post("b", "Sustain Studio", "Nowa platforma dla każdego Polaka"),
    })  # fmt: skip
    assert [x.kind for x in out.links["a"]] == ["same_account"]
    assert "Account “100064111111111” on both" in out.links["a"][0].evidence
    assert not out.posts


def test_the_same_display_name_on_different_accounts_is_not_linked():
    views = [shot("a.png", page("100064111111111")), shot("b.png", page("100064222222222"))]
    out = links(views, {"a": post("a", "Sustain Studio", *CAPTION),
                        "b": post("b", "Sustain Studio", *CAPTION)})  # fmt: skip
    assert [x.kind for x in out.links["a"]] == ["different_account"]
    assert (
        out.links["a"][0].evidence[1]
        == "Different accounts: “100064111111111” and “100064222222222”"
    )
    assert not out.posts  # even an identical caption does not make it the same post


def test_an_account_on_one_side_only_is_still_only_a_possible_advertiser():
    views = [shot("a.png", page("100064111111111")), shot("b.png")]
    out = links(views, {"a": post("a", "Sustain Studio"), "b": post("b", "Sustain Studio")})
    assert [x.kind for x in out.links["a"]] == ["possible_advertiser"]


def test_generic_text_and_a_shared_name_are_not_the_same_post():
    views = [shot("a.png"), shot("b.png")]
    out = links(views, {"a": post("a", "Sustain Studio", "Czas ucieka!", "1 wyświetlenie", "2"),
                        "b": post("b", "Sustain Studio", "Czas ucieka!", "1 wyświetlenie", "2")})  # fmt: skip
    assert [x.kind for x in out.links["a"]] == ["possible_advertiser"]


def test_a_shared_caption_without_the_button_is_not_the_same_post():
    views = [shot("a.png"), shot("b.png")]
    out = links(views, {"a": post("a", "Sustain Studio", *CAPTION, cta=None),
                        "b": post("b", "Sustain Studio", *CAPTION)})  # fmt: skip
    assert not out.posts


def test_the_same_news_imagery_from_different_advertisers_is_not_linked():
    lower_third = ("polsat news", "WYDARZENIA.INTERIA.PL", "tym, jak zwykła Polka o")
    views = [shot("a.png"), shot("b.png")]
    out = links(views, {"a": post("a", "Sustain Studio", *lower_third, *CAPTION),
                        "b": post("b", "Fresh Perspectives", *lower_third, *CAPTION)})  # fmt: skip
    assert not out.links


def test_the_same_news_template_with_a_different_caption_is_not_the_same_post():
    def ad(vid, caption):
        return post(vid, "Sustain Studio", "polsat news", "WYDARZENIA.INTERIA.PL", caption) + [
            obs(vid, "image.logo", "polsat news"),
            obs(vid, "image.domain", "WYDARZENIA.INTERIA.PL"),
        ]

    out = links([shot("a.png"), shot("b.png")], {
        "a": ad("a", "Zarabiaj w domu"), "b": ad("b", "Rząd ukrywa prawdę"),
    })  # fmt: skip
    assert [x.kind for x in out.links["a"]] == ["possible_advertiser"]


def test_a_library_id_is_shown_on_the_other_screenshots_of_the_same_post(store, tmp_path):
    views = [shot("lib.png"), shot("feed.png"), shot("other.png")]
    store.put_incident(Incident(incident_id="s", root=str(tmp_path)), [], views)
    store.put_observations("lib", NAME, post("lib", "Sustain Studio", *CAPTION) + [
        obs("lib", "image.text", f"Identyfikator biblioteki: {LIBRARY_ID}")
    ])  # fmt: skip
    store.put_observations("feed", NAME, post("feed", "Sustain Studio", *CAPTION))
    store.put_observations("other", NAME, post("other", "Sustain Studio", "Inna reklama dziś"))
    lib = build_library(store, NO_GROUPS, tmp_path)
    feed = lib.get("feed")
    assert feed.library_id == LIBRARY_ID
    assert feed.library_source == "same_post" and feed.library_via == "lib"
    assert feed.library_evidence[0].raw == f"Identyfikator biblioteki: {LIBRARY_ID}"
    assert lib.get("other").library_id is None  # same advertiser, different ad


def test_a_post_whose_screenshots_disagree_on_the_id_shows_none(store, tmp_path):
    views = [shot("a.png"), shot("b.png"), shot("feed.png")]
    store.put_incident(Incident(incident_id="s", root=str(tmp_path)), [], views)
    for vid, lid in (("a", "2186769435230928"), ("b", "4414624315417273")):
        store.put_observations(vid, NAME, post(vid, "Sustain Studio", *CAPTION) + [
            obs(vid, "image.text", f"Library ID: {lid}")
        ])  # fmt: skip
    store.put_observations("feed", NAME, post("feed", "Sustain Studio", *CAPTION))
    assert build_library(store, NO_GROUPS, tmp_path).get("feed").library_id is None


def test_the_item_page_shows_the_id_advertiser_and_links(sustain):
    from ftf.web.views import detail

    page = detail(sustain, sustain.get("IMG_3632"))
    assert page["ad"]["libraryId"] == LIBRARY_ID
    assert page["ad"]["librarySource"] == "Read on this screenshot"
    assert page["ad"]["libraryEvidence"][0]["text"] == f"Identyfikator biblioteki: {LIBRARY_ID}"
    assert page["ad"]["advertiser"] == "Sustain Studio"
    assert page["actions"][0]["label"] == "Find the Meta ad in Ad Library"
    assert page["actions"][0]["url"].endswith(f"&id={LIBRARY_ID}")
    names = {link["name"] for link in detail(sustain, sustain.get("IMG_3628"))["ad"]["links"]}
    assert names == {"Same post", "Possibly the same advertiser"}
