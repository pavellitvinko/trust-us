"""Same post, shared advertiser and shared account links between ad screenshots.

The Sustain Studio fixtures are hand-read model answers for six phone screenshots: two of the
Ad Library (IMG_3631, IMG_3632) and four of the feed (IMG_3625-IMG_3628). They go through the
same `_observations` the model output does.
"""

import json
import time
from pathlib import Path

import pytest

from ftf.ad_links import build_ad_links
from ftf.contracts import Incident, Observation, Purpose, Source, State, View, ViewKind
from ftf.grouping.levels import Groups
from ftf.library import build_library
from ftf.perception.image import NAME, ImageReading, _observations

READINGS = Path(__file__).parent / "fixtures" / "sustain_studio" / "readings.json"
LIBRARY_ID = "2186769435230928"
NO_GROUPS = Groups(campaign_of={}, campaigns={}, family_of={}, families={}, funnel_edges={})
SUSTAIN = {f"IMG_{n}" for n in (3625, 3626, 3627, 3628, 3631, 3632)}


def shot(name, meta=None, case="s"):
    return View(
        view_id=Path(name).stem, incident_id=case, kind=ViewKind.IMAGE,
        label=f"{case} · {name}", primary=name, meta=meta or {},
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


def peers(ads, vid):
    shown, total = ads.possible_advertisers(vid)
    assert total == len(shown)
    return {link.other for link in shown}


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
    link = next(x for x in sustain.get("IMG_3628").ad_links if x.other == "IMG_3627")
    # The burned-in captions ("imieniu Maria…", "tym, jak zwykła Polka o") are on the creative
    # and the truncated caption at the top belongs to another post: neither counts.
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


def test_screenshots_without_a_stronger_link_are_possibly_the_same_advertiser(sustain):
    for vid in SUSTAIN:
        assert sustain.get(vid).advertiser == "Sustain Studio"
        linked = kinds(sustain.get(vid), "same_post")
        assert peers(sustain.ads, vid) == SUSTAIN - {vid} - linked
    shown, _ = sustain.ads.possible_advertisers("IMG_3625")
    by_other = {link.other: link.evidence[0] for link in shown}
    # IMG_3625 and IMG_3626 also both show the next post's advertiser.
    assert by_other["IMG_3626"] == "Advertiser “Fresh Perspectives”, “Sustain Studio” on both"


def test_ad_links_never_name_a_campaign(sustain):
    for case in sustain.cases:
        for item in case.items:
            assert item.campaign is None and item.kit is None
            assert all(label.kind != "campaign" for label in item.labels)


def test_the_item_page_shows_the_id_advertiser_and_links(sustain):
    from ftf.web.views import detail

    page = detail(sustain, sustain.get("IMG_3632"))
    assert page["ad"]["libraryId"] == LIBRARY_ID
    assert page["ad"]["librarySource"] == "Read on this screenshot"
    assert page["ad"]["libraryEvidence"][0]["text"] == f"Identyfikator biblioteki: {LIBRARY_ID}"
    assert page["ad"]["advertiser"] == "Sustain Studio"
    assert page["actions"][0]["label"] == "Find the Meta ad in Ad Library"
    assert page["actions"][0]["url"].endswith(f"&id={LIBRARY_ID}")
    links = detail(sustain, sustain.get("IMG_3628"))["ad"]["links"]
    names = [(link["id"], link["name"]) for link in links]
    assert ("IMG_3627", "Same post") in names
    assert ("IMG_3627", "Possibly the same advertiser") not in names  # no second, weaker row
    assert ("IMG_3631", "Possibly the same advertiser") in names


# -- negative, account and propagation cases -----------------------------------------------

BOX = "image:region[0.1,0.85,0.5,0.02]"  # below the button: post text, not the creative
HEAD = "image:region[0.1,0.05,0.3,0.02]"
BUTTON = "image:region[0.05,0.7,0.6,0.03]"
ON_CREATIVE = "image:region[0.1,0.6,0.8,0.03]"  # between the advertiser line and the button


def obs(view_id, namespace, raw, locator=BOX, state=State.PRESENT):
    return Observation(
        view_id=view_id, signal_id="E11", namespace=namespace, value=raw.casefold(), raw=raw,
        state=state, purpose=Purpose.BOTH, source=Source(file=f"{view_id}.png", locator=locator),
        extractor=NAME, version="2",
    )  # fmt: skip


def post(view_id, advertiser, *text, cta="Dowiedz się więcej"):
    rows = [
        obs(view_id, "image.advertiser", advertiser, HEAD),
        obs(view_id, "image.text", advertiser, HEAD),
    ]
    rows += [obs(view_id, "image.text", t) for t in text]
    if cta:
        rows += [obs(view_id, "image.cta", cta, BUTTON), obs(view_id, "image.text", cta, BUTTON)]
    return rows


CAPTION = ("Poznaj wszystkie szczegóły 👉", "Czas ucieka!")


def page(page_id):
    return {"saved_from": f"https://www.facebook.com/ads/library/?view_all_page_id={page_id}"}


def links(views, observations):
    return build_ad_links({v.view_id: v for v in views}, observations)


def link_kinds(out, vid):
    return sorted(x.kind for x in out.links.get(vid, []))


def test_one_account_with_different_creatives_is_not_one_ad():
    views = [shot("a.png", page("100064111111111")), shot("b.png", page("100064111111111"))]
    out = links(views, {
        "a": post("a", "Sustain Studio", "Inwestuj mądrze z nami już dziś"),
        "b": post("b", "Sustain Studio", "Nowa platforma dla każdego Polaka"),
    })  # fmt: skip
    assert link_kinds(out, "a") == ["same_account"]
    assert "Account “100064111111111” on both" in out.links["a"][0].evidence
    assert not out.posts


def test_the_same_display_name_on_different_accounts_is_not_linked():
    views = [shot("a.png", page("100064111111111")), shot("b.png", page("100064222222222"))]
    out = links(views, {"a": post("a", "Sustain Studio", *CAPTION),
                        "b": post("b", "Sustain Studio", *CAPTION)})  # fmt: skip
    assert link_kinds(out, "a") == ["different_account"]
    assert out.links["a"][0].evidence[1] == (
        "Different accounts: “100064111111111” and “100064222222222”"
    )
    assert not out.posts  # even an identical caption does not make it the same post
    assert out.possible_advertisers("a") == ([], 0)


def test_an_account_on_one_side_only_is_still_only_a_possible_advertiser():
    views = [shot("a.png", page("100064111111111")), shot("b.png")]
    out = links(views, {"a": post("a", "Sustain Studio"), "b": post("b", "Sustain Studio")})
    assert link_kinds(out, "a") == [] and peers(out, "a") == {"b"}


def test_a_page_id_outside_facebook_is_not_an_account():
    # WordPress pages use ?page_id= too: two copies of one post must stay the same post.
    views = [
        shot("a.png", {"saved_from": "https://shop.example/?page_id=123456"}),
        shot("b.png", {"saved_from": "https://shop.example/?page_id=654321"}),
    ]
    out = links(views, {"a": post("a", "Sustain Studio", *CAPTION),
                        "b": post("b", "Sustain Studio", *CAPTION)})  # fmt: skip
    assert link_kinds(out, "a") == ["same_post"]


@pytest.mark.parametrize(
    "generic",
    [
        ("Czas ucieka!", "2 dni"),  # a relative time
        ("Lubię to!", "Komentarz", "Udostępnij", "Zobacz tłumaczenie"),  # the action bar
        ("Sustain Studio · Obserwuj", "Czas ucieka!"),  # the name with a follow button
        ("Czas ucieka!", "1 wyświetlenie", "2", "Like", "Comment", "Share"),
        ("Nie będzie żadnej... więcej", "Czas ucieka!"),  # a neighbouring post, cut short
    ],
)
def test_generic_text_and_a_shared_name_are_not_the_same_post(generic):
    views = [shot("a.png"), shot("b.png")]
    out = links(views, {"a": post("a", "Sustain Studio", *generic, "Zarabiaj w domu"),
                        "b": post("b", "Sustain Studio", *generic, "Rząd ukrywa prawdę")})  # fmt: skip
    assert link_kinds(out, "a") == [] and peers(out, "a") == {"b"}


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
    assert not out.links and out.possible_advertisers("a") == ([], 0)


def test_the_same_news_template_with_a_different_caption_is_not_the_same_post():
    def ad(vid, caption):
        return post(vid, "Sustain Studio", "polsat news", "WYDARZENIA.INTERIA.PL", caption) + [
            obs(vid, "image.logo", "polsat news"),
            obs(vid, "image.domain", "WYDARZENIA.INTERIA.PL"),
        ]

    out = links([shot("a.png"), shot("b.png")], {
        "a": ad("a", "Zarabiaj w domu"), "b": ad("b", "Rząd ukrywa prawdę"),
    })  # fmt: skip
    assert link_kinds(out, "a") == []


def test_burned_in_creative_text_with_different_captions_is_not_the_same_post():
    def ad(vid, caption):
        burned = obs(vid, "image.text", "imieniu Maria opublikowała na swoim profilu", ON_CREATIVE)
        return post(vid, "Sustain Studio", caption) + [burned]

    out = links([shot("a.png"), shot("b.png")], {
        "a": ad("a", "Zarabiaj w domu"), "b": ad("b", "Rząd ukrywa prawdę"),
    })  # fmt: skip
    assert link_kinds(out, "a") == []


def test_screenshots_of_different_cases_are_not_linked():
    views = [shot("a.png", case="1"), shot("b.png", case="2")]
    out = links(views, {"a": post("a", "Sustain Studio", *CAPTION),
                        "b": post("b", "Sustain Studio", *CAPTION)})  # fmt: skip
    assert not out.links and out.possible_advertisers("a") == ([], 0)


def test_one_name_on_thousands_of_screenshots_stays_fast():
    views = [shot(f"s{i}.png") for i in range(3000)]
    observations = {
        v.view_id: post(v.view_id, "Facebook Ads", f"Oferta numer {i} tylko dzisiaj dla ciebie")
        for i, v in enumerate(views)
    }
    start = time.perf_counter()
    out = links(views, observations)
    assert time.perf_counter() - start < 5
    assert not out.links
    shown, total = out.possible_advertisers("s0", limit=20)
    assert len(shown) == 20 and total == 2999


def test_absent_advertiser_readings_are_not_shown(store, tmp_path):
    view = shot("a.png")
    store.put_incident(Incident(incident_id="s", root=str(tmp_path)), [], [view])
    store.put_observations("a", NAME, [obs("a", "image.advertiser", "Ghost", HEAD, State.ABSENT)])
    assert build_library(store, NO_GROUPS, tmp_path).get("a").advertiser is None


# -- the Library ID across screenshots of one post -----------------------------------------


def _library(store, tmp_path, rows, views=None):
    views = views or [shot(f"{vid}.png") for vid in rows]
    store.put_incident(Incident(incident_id="s", root=str(tmp_path)), [], views)
    for vid, observations in rows.items():
        store.put_observations(vid, NAME, observations)
    return build_library(store, NO_GROUPS, tmp_path)


def _with_id(vid, *text, lid=LIBRARY_ID):
    return post(vid, "Sustain Studio", *text) + [obs(vid, "image.text", f"Library ID: {lid}")]


def test_a_library_id_is_shown_on_the_other_screenshots_of_the_same_post(store, tmp_path):
    lib = _library(store, tmp_path, {
        "lib": _with_id("lib", *CAPTION),
        "feed": post("feed", "Sustain Studio", *CAPTION),
        "other": post("other", "Sustain Studio", "Inna reklama dziś rano"),
    })  # fmt: skip
    feed = lib.get("feed")
    assert feed.library_id == LIBRARY_ID
    assert feed.library_source == "same_post" and feed.library_via == "lib"
    assert feed.library_origin == "ocr"
    assert feed.library_evidence[0].raw == f"Library ID: {LIBRARY_ID}"
    assert lib.get("other").library_id is None  # same advertiser, different ad


def test_a_library_id_never_travels_along_a_chain(store, tmp_path):
    # lib and mid share one caption, mid and far another: lib and far share nothing.
    other = ("Zobacz nagranie zanim zniknie", "Tylko dziś")
    lib = _library(store, tmp_path, {
        "lib": _with_id("lib", *CAPTION),
        "mid": post("mid", "Sustain Studio", *CAPTION, *other),
        "far": post("far", "Sustain Studio", *other),
    })  # fmt: skip
    assert lib.get("mid").library_id == LIBRARY_ID
    assert lib.get("far").library_id is None
    assert "lib" not in kinds(lib.get("far"), "same_post")


def test_a_chain_does_not_cross_different_accounts(store, tmp_path):
    views = [
        shot("a.png", page("100064111111111")),
        shot("mid.png"),
        shot("c.png", page("100064222222222")),
    ]
    lib = _library(store, tmp_path, {
        "a": _with_id("a", *CAPTION),
        "mid": post("mid", "Sustain Studio", *CAPTION),
        "c": post("c", "Sustain Studio", *CAPTION),
    }, views)  # fmt: skip
    assert lib.get("c").library_id is None
    assert kinds(lib.get("c"), "different_account") == {"a"}


def test_a_post_whose_screenshots_disagree_on_the_id_shows_none(store, tmp_path):
    lib = _library(store, tmp_path, {
        "a": _with_id("a", *CAPTION, lid="2186769435230928"),
        "b": _with_id("b", *CAPTION, lid="4414624315417273"),
        "feed": post("feed", "Sustain Studio", *CAPTION),
    })  # fmt: skip
    assert lib.get("feed").library_id is None


def test_an_id_from_saved_material_keeps_its_origin(store, tmp_path):
    from ftf.web.views import detail

    views = [
        shot("lib.png", {"final_url": f"https://www.facebook.com/ads/library/?id={LIBRARY_ID}"}),
        shot("feed.png"),
    ]
    lib = _library(store, tmp_path, {
        "lib": post("lib", "Sustain Studio", *CAPTION),
        "feed": post("feed", "Sustain Studio", *CAPTION),
    }, views)  # fmt: skip
    feed = lib.get("feed")
    assert (feed.library_id, feed.library_origin) == (LIBRARY_ID, "metadata")
    assert detail(lib, feed)["ad"]["librarySource"] == (
        "From the saved material of another screenshot of the same post"
    )


def test_screenshots_of_a_missing_case_are_left_out(store, tmp_path):
    views = [shot("a.png"), shot("b.png", case="gone")]
    store.put_incident(Incident(incident_id="s", root=str(tmp_path)), [], views[:1])
    store.put_incident(Incident(incident_id="gone", root=str(tmp_path)), [], views[1:])
    store.put_observations("a", NAME, post("a", "Sustain Studio", *CAPTION))
    store.put_observations("b", NAME, _with_id("b", *CAPTION))
    store.db.execute("DELETE FROM incidents WHERE incident_id='gone'")
    lib = build_library(store, NO_GROUPS, tmp_path)
    assert lib.get("a").library_id is None and not lib.get("a").ad_links
