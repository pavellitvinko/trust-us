"""Library screen data and background intake: saving uploads, capturing links, labels, journeys."""

import io
import json
from pathlib import Path

import pytest

from ftf.contracts import Incident, Observation, Source, View, ViewKind
from ftf.grouping.funnel import FunnelEdge
from ftf.grouping.levels import Groups
from ftf.intake import IntakeError
from ftf.jobs import Job, Worker, link_case, save_files
from ftf.library import (
    Match,
    Risk,
    build_library,
    campaign_title,
    campaign_tree,
    name_groups,
    plain_proof,
    read_meta,
    record_cases,
    short_id,
    short_value,
    view_title,
)

SAMPLE = Path(__file__).resolve().parents[1] / "testing_sample"
PAGE = "<html><head><title>Oferta</title></head><body><p>Treść strony.</p></body></html>"


def files(*entries):
    return [(name, io.BytesIO(body.encode())) for name, body in entries]


# -- saving uploads ----------------------------------------------------------------------


def test_a_chosen_folder_of_cases_stays_one_case_per_subfolder(tmp_path):
    root = save_files(
        files(("sample/a/page.html", PAGE), ("sample/b/shot.png", "x")), tmp_path / "up"
    )
    cases = sorted(p.name for p in root.iterdir())
    assert root.name == "sample" and len(cases) == 2
    assert all(name.startswith(("a-", "b-")) for name in cases)


def test_a_chosen_case_folder_and_loose_files_are_one_case(tmp_path):
    folder = save_files(
        files(("case/page.html", PAGE), ("case/page_files/x.css", "")), tmp_path / "up"
    )
    assert folder.name.startswith("case-") and (folder / "page_files/x.css").is_file()
    loose = save_files(files(("page.html", PAGE), ("shot.png", "x")), tmp_path / "up")
    assert loose.name.startswith("case-") and {p.name for p in loose.iterdir()} == {
        "page.html",
        "shot.png",
    }


@pytest.mark.parametrize("name", ["../evil.txt", "a/../../evil.txt", "/evil.txt", "C:/x.txt"])
def test_uploaded_names_cannot_escape(tmp_path, name):
    with pytest.raises(IntakeError):
        save_files(files((name, "x")), tmp_path / "up")
    assert not (tmp_path / "evil.txt").exists()
    assert not (tmp_path / "up").exists() or not list((tmp_path / "up").iterdir())


# -- the background job ------------------------------------------------------------------


def fake_capture(url, out_dir):
    out_dir.mkdir(parents=True)
    (out_dir / "dom.html").write_text(PAGE)
    manifest = {"schema": "ftf.capture/1", "page_url": url, "final_url": url, "status": "ok"}
    (out_dir / "manifest.json").write_text(json.dumps(manifest))


def test_links_without_files_become_one_named_case(tmp_path, monkeypatch):
    monkeypatch.setenv("FTF_EMBEDDER", "fake")
    data = tmp_path / "data"
    worker = Worker(data / "ftf.sqlite", data, capture=fake_capture)
    urls = ["https://ad.example/x", "https://page.example/y"]
    job = Job(job_id="j", path=link_case(urls[0], worker.uploads), names=urls, urls=urls)
    worker.run(job)
    assert job.status == "done", job.error
    assert len(job.incidents) == 1 and job.incidents[0].startswith("ad.example-")
    assert job.incidents[0] in read_meta(data)["cases"]
    assert worker.generation == 1


def test_a_failed_capture_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setenv("FTF_EMBEDDER", "fake")
    data = tmp_path / "data"

    def broken(url, out_dir):
        raise RuntimeError("no browser")

    worker = Worker(data / "ftf.sqlite", data, capture=broken)
    job = Job(job_id="j", path=link_case("https://x.example", worker.uploads), names=["x"],
              urls=["https://x.example"])  # fmt: skip
    worker.run(job)
    assert job.status == "failed" and "no browser" in job.error


# -- labels, risk and journeys -----------------------------------------------------------


def _store_with(store, views):
    store.put_incident(Incident(incident_id="7", root="."), [], views)


def test_only_groups_of_several_items_are_named(store, tmp_path):
    views = [
        View(view_id=v, incident_id="7", kind=ViewKind.PAGE, label=f"7 · {v}.example")
        for v in ("a", "b", "c")
    ]
    _store_with(store, views)
    groups = Groups(
        campaign_of={"a": "c1", "b": "c1", "c": "c2"},
        campaigns={"c1": ["a", "b"], "c2": ["c"]},
        family_of={"c1": "f1", "c2": "f2"},
        families={"f1": ["c1"], "f2": ["c2"]},
    )
    lib = build_library(store, groups, tmp_path)
    labels = {i.view_id: [label.text for label in i.labels] for i in lib.items()}
    # No shared clue: a short ID from the smallest member view, not a letter.
    assert labels["a"] == labels["b"] == [f"Campaign {short_id('a')}"]
    assert lib.get("a").labels[0].detail == "with 1 more item in this case"
    assert labels["c"] == ["Not enough evidence"]  # no fingerprints at all
    assert lib.links["a"] == {"b"} and lib.links["c"] == set()


def test_journey_follows_the_funnel_and_starts_from_the_ad(store, tmp_path):
    views = [
        View(view_id=v, incident_id="7", kind=ViewKind.PAGE, label=f"7 · {v}", primary=f"{v}.html")
        for v in ("landing", "chat", "about")
    ]
    _store_with(store, views)
    ad = Observation(
        view_id="landing", signal_id="A3", namespace="ad.id", value="123", raw="123",
        source=Source(file="landing.html"), extractor="ad_params", version="1",
    )  # fmt: skip
    store.put_observations("landing", "ad_params", [ad])
    edge = FunnelEdge("landing", "chat", "funnel", ["stored navigation names the landing: u1"])
    groups = Groups(
        campaign_of={v.view_id: v.view_id for v in views},
        campaigns={v.view_id: [v.view_id] for v in views},
        family_of={},
        families={},
        funnel_edges={"7": ([edge], [])},
    )
    lib = build_library(store, groups, tmp_path)
    journey = lib.journeys["chat"]
    assert [title for _, title in journey.steps] == ["landing", "chat"]
    assert journey.ads == ["123"]
    assert journey.proofs == ["Stored navigation names the earlier page: u1"]
    assert "about" not in lib.journeys
    order = [i.view_id for i in lib.cases[0].items]
    assert order[:2] == ["landing", "chat"]  # steps of one journey sit together, in order


def test_proofs_read_as_sentences():
    assert plain_proof("ad ID present in the landing URL: 99") == "Ad ID 99 is in the landing URL"
    assert plain_proof("something new") == "something new"


def test_saved_pages_are_named_after_their_title():
    view = View(
        view_id="v", incident_id="1", kind=ViewKind.PAGE, label="1 · x.example",
        primary="Oferta dnia.html", meta={"saved_from": "https://x.example/?a=1"},
    )  # fmt: skip
    assert view_title(view) == ("Oferta dnia", "x.example")


# -- the real sample, model off ----------------------------------------------------------


@pytest.fixture(scope="module")
def sample_library(tmp_path_factory):
    if not SAMPLE.is_dir():
        pytest.skip("testing_sample/ not present")
    mp = pytest.MonkeyPatch()
    for key, value in (("FTF_EMBEDDER", "fake"), ("FTF_ENRICH", "off"),
                       ("OPENAI_API_KEY", ""), ("GEMINI_API_KEY", "")):  # fmt: skip
        mp.setenv(key, value)
    from ftf import pipeline
    from ftf.fusion import run_method
    from ftf.grouping import build_groups
    from ftf.ingest import ingest_path
    from ftf.registry import load_plugins
    from ftf.store import Store

    load_plugins()
    s = Store(":memory:")
    ingest_path(SAMPLE, s)
    pipeline.run_extract(s)
    pipeline.run_fingerprint(s)
    pipeline.run_compare_all(s)
    run_method(s)
    lib = build_library(s, build_groups(s, persist_funnel=False), tmp_path_factory.mktemp("d"))
    yield lib
    s.close()
    mp.undo()


def test_sample_campaigns_kits_and_journeys(sample_library):
    def labels(case, host):
        case = next(c for c in sample_library.cases if c.case_id == case)
        return [[label.text for label in i.labels] for i in case.items if i.host == host]

    first = labels("1", "civicdaybook.live")[0]
    second = labels("2", "acrosscommunities.live")[0]
    assert first[0] == second[0] == "Campaign 0b9611d9…"  # its shared campaign record
    assert all(lab.kind != "kit" for i in sample_library.items() for lab in i.labels)
    tree = campaign_tree(sample_library)
    kit = next(r for r in tree.rows if r.kind == "kit")
    assert kit.name.startswith("Kit 25c6c6fd") and kit.cases == ["48", "61"]
    assert tree.unlinked.startswith("Not linked yet: ")
    assert any("Duplicate" in t for t in labels("2", "healthlinkq.pro")[0])
    case61 = next(c for c in sample_library.cases if c.case_id == "61")
    on_path = [i for i in case61.items if i.step]
    assert len(on_path) == 2 and on_path[0].step.position == 1
    decoy = next(i for i in case61.items if i.title.startswith("Glowpixel.org"))
    assert decoy.step is None and all(lab.kind != "campaign" for lab in decoy.labels)
    case14 = next(c for c in sample_library.cases if c.case_id == "14")
    journey = sample_library.journeys[next(i.view_id for i in case14.items if i.step)]
    assert journey.ads and "landing URL" in journey.proofs[0]


def test_sample_direct_matches_and_muted_screenshots(sample_library):
    lib = sample_library

    def page(case, host):
        return next(i for i in lib.items() if i.case_id == case and i.host == host)

    first, second = page("1", "civicdaybook.live"), page("2", "acrosscommunities.live")
    forward = next(m for m in first.matches if m.view_id == second.view_id)
    backward = next(m for m in second.matches if m.view_id == first.view_id)
    assert forward.verdict == backward.verdict == "Same campaign"
    assert "identity" in forward.tiers and "campaign record" in forward.clue
    health2, health3 = page("2", "healthlinkq.pro"), page("3", "healthlinkq.pro")
    assert next(m for m in health2.matches if m.view_id == health3.view_id).verdict == "Duplicate"
    kit = page("48", "novumfy.com")
    assert any(
        lib.get(m.view_id).case_id == "61" and m.verdict == "Shared kit" for m in kit.matches
    )
    assert any(i.kind is ViewKind.IMAGE and not i.matches for i in lib.items())


def test_a_flattened_folder_with_repeated_names_is_refused(tmp_path):
    with pytest.raises(IntakeError, match="choose the folder"):
        save_files(files(("main.js", "a"), ("main.js", "b")), tmp_path / "up")
    assert not (tmp_path / "up").exists()  # refused before anything is written


def test_missing_picture_files_give_no_thumbnail(store, tmp_path):
    shot = tmp_path / "case" / "shot.png"
    shot.parent.mkdir()
    shot.write_bytes(b"x")
    views = [
        View(view_id="here", incident_id="7", kind=ViewKind.IMAGE, label="7 · s", primary="shot.png"),
        View(view_id="gone", incident_id="7", kind=ViewKind.IMAGE, label="7 · g", primary="gone.png"),
    ]  # fmt: skip
    store.put_incident(Incident(incident_id="7", root=str(tmp_path / "case")), [], views)
    groups = Groups(campaign_of={}, campaigns={}, family_of={}, families={})
    lib = build_library(store, groups, tmp_path)
    assert lib.get("here").thumbnail == str(shot) and lib.get("gone").thumbnail is None


def test_an_add_that_links_an_older_item_leaves_a_note(store, tmp_path):
    views = [View(view_id="old", incident_id="7", kind=ViewKind.PAGE, label="7 · old.example")]
    _store_with(store, views)
    alone = Groups(campaign_of={"old": "c1"}, campaigns={"c1": ["old"]}, family_of={}, families={})
    before = build_library(store, alone, tmp_path)
    new = View(view_id="new", incident_id="8", kind=ViewKind.PAGE, label="8 · new.example")
    store.put_incident(Incident(incident_id="8", root="."), [], [new])
    linked = Groups(
        campaign_of={"old": "c1", "new": "c1"}, campaigns={"c1": ["old", "new"]},
        family_of={}, families={},
    )  # fmt: skip
    after = build_library(store, linked, tmp_path, previous=before)
    assert after.get("old").note == "New link to new.example"
    assert after.get("new").note is None  # it was not in the Library before


def test_library_rows_render_and_select(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    from ftf.store import Store

    data = tmp_path / "data"
    s = Store(data / "ftf.sqlite")
    views = [
        View(view_id=f"7:{n}", incident_id="7", kind=ViewKind.PAGE, label=f"7 · {n}.example")
        for n in ("first", "second")
    ]
    s.put_incident(Incident(incident_id="7", root=str(tmp_path)), [], views)
    s.close()
    monkeypatch.setenv("FTF_DATA_DIR", str(data))
    root = Path(__file__).resolve().parents[1]
    app = AppTest.from_file(str(root / "app/streamlit_app.py")).run()
    assert not app.exception
    titles = [b for b in app.button if b.label.endswith(".example")]
    assert [b.label for b in titles] == ["first.example", "second.example"]
    assert any("first.example" in m.value for m in app.markdown if m.value.startswith("#####"))
    titles[1].click().run()
    assert not app.exception
    assert app.session_state["selected"] == "7:second"
    assert any("second.example" in m.value for m in app.markdown if m.value.startswith("#####"))


def test_library_ids_come_only_from_the_ad_library(tmp_path):
    from ftf.library import library_id

    (tmp_path / "ad.info.json").write_text(
        json.dumps({"webpage_url": "https://www.facebook.com/ads/library/?id=1111222233334444"})
    )
    video = View(
        view_id="v", incident_id="1", kind=ViewKind.VIDEO, label="1 · ad.mp4",
        primary="ad.mp4", attachments=["ad.info.json"],
    )  # fmt: skip
    assert library_id(video, str(tmp_path)) == "1111222233334444"
    captured = View(
        view_id="p", incident_id="1", kind=ViewKind.PAGE, label="1 · facebook.com",
        meta={"final_url": "https://www.facebook.com/ads/library/?active_status=all&id=55"},
    )  # fmt: skip
    assert library_id(captured, str(tmp_path)) == "55"
    landing = View(
        view_id="l", incident_id="1", kind=ViewKind.PAGE, label="1 · shop.example",
        meta={"saved_from": "https://shop.example/?ad_id=120200000000000000"},
    )  # fmt: skip
    assert library_id(landing, str(tmp_path)) is None  # an Ads Manager ID is not a Library ID


def test_a_journey_knows_its_entry_host(store, tmp_path):
    views = [
        View(
            view_id=v,
            incident_id="7",
            kind=ViewKind.PAGE,
            label=f"7 · {v}",
            primary=f"{v}.html",
            meta={"saved_from": f"https://{v}.example/"},
        )
        for v in ("landing", "chat")
    ]
    _store_with(store, views)
    edge = FunnelEdge("landing", "chat", "funnel", ["stored navigation names the landing: u1"])
    groups = Groups(
        campaign_of={}, campaigns={}, family_of={}, families={},
        funnel_edges={"7": ([edge], [])},
    )  # fmt: skip
    assert build_library(store, groups, tmp_path).journeys["chat"].host == "landing.example"


# -- naming, stability and plurals ---------------------------------------------------------


def _match(other, verdict="Same campaign", ns="", value=""):
    return Match(
        view_id=other, same_case=False, verdict=verdict, tiers=("identity",), confidence="high",
        observed_groups=1, total_groups=1, clue="clue", clue_ns=ns, clue_value=value,
    )  # fmt: skip


def _views(*pairs):
    return {
        v: View(view_id=v, incident_id=case, kind=ViewKind.PAGE, label=f"{case} · {v}")
        for v, case in pairs
    }


def _groups(campaigns, families=None):
    families = families or {f"f:{c}": [c] for c in campaigns}
    return Groups(
        campaign_of={v: c for c, vs in campaigns.items() for v in vs},
        campaigns=campaigns,
        family_of={c: f for f, cs in families.items() for c in cs},
        families=families,
    )


def test_a_campaign_is_named_by_its_strongest_shared_clue():
    views = _views(("a", "1"), ("b", "2"), ("c", "2"))
    matches = {
        "a": [_match("b", ns="contact.phone", value="+48 22 307 45 67 wew. 12"),
              _match("c", ns="fb_pixel", value="1071875908635809")],
        "b": [_match("a", ns="contact.phone", value="+48 22 307 45 67 wew. 12")],
        "c": [_match("a", ns="fb_pixel", value="1071875908635809"),
              _match("b", verdict="Clue only", ns="aio.campaign_uuid", value="x")],
    }  # fmt: skip
    campaigns, _ = name_groups(_groups({"c1": ["a", "b", "c"]}), matches, views, {})
    assert campaigns["c1"].name == "Campaign 1071875908635809"  # a pixel outranks a phone
    del matches["c"]  # a clue-only pair never names a campaign
    matches["a"] = matches["a"][:1]
    campaigns, _ = name_groups(_groups({"c1": ["a", "b", "c"]}), matches, views, {})
    assert campaigns["c1"].name == "Campaign +48 22 307 45 67 …"


def test_long_values_are_shortened_for_a_chip():
    assert short_value("url.host_path", "shadowtracecore.com/") == "shadowtracecore.com"
    assert short_value("url.host_path", "shadowtracecore.com/a/b") == "shadowtracecore.com/…"
    assert short_value("aio.campaign_uuid", "0b9611d9-df5e-44e6-acea-ef85847dc3f1") == "0b9611d9…"


def test_names_do_not_depend_on_the_order_material_was_added(store, tmp_path):
    for case, vids in (("7", ("7:a", "7:b")), ("9", ("9:a",))):
        views = [View(view_id=v, incident_id=case, kind=ViewKind.PAGE, label=f"{case} · {v}")
                 for v in vids]  # fmt: skip
        store.put_incident(Incident(incident_id=case, root="."), [], views)
    record_cases(tmp_path, ["7"], when="2026-01-02T00:00:00+00:00")
    record_cases(tmp_path, ["9"], when="2026-01-03T00:00:00+00:00")
    before = build_library(store, _groups({"c1": ["7:a", "7:b"], "c2": ["9:a"]}), tmp_path)
    name = before.get("7:a").labels[0].text
    # An older case joins with a new campaign of its own: the first name stays as it was.
    store.put_incident(Incident(incident_id="5", root="."), [],
                       [View(view_id=v, incident_id="5", kind=ViewKind.PAGE, label=v)
                        for v in ("5:a", "5:b")])  # fmt: skip
    record_cases(tmp_path, ["5"], when="2025-01-01T00:00:00+00:00")
    groups = _groups({"c0": ["5:a", "5:b"], "c1": ["7:a", "7:b"], "c2": ["9:a"]})
    after = build_library(store, groups, tmp_path)
    assert after.get("7:a").labels[0].text == name
    assert after.get("5:a").labels[0].text != name


def test_campaigns_with_the_same_clue_are_told_apart():
    views = _views(("a", "1"), ("b", "1"), ("c", "2"), ("d", "2"))
    matches = {v: [_match(o, ns="fb_pixel", value="42")]
               for v, o in (("a", "b"), ("b", "a"), ("c", "d"), ("d", "c"))}  # fmt: skip
    campaigns, _ = name_groups(_groups({"c1": ["a", "b"], "c2": ["c", "d"]}), matches, views, {})
    assert campaigns["c1"].name == f"Campaign 42 {short_id('a')}"
    assert campaigns["c2"].name == f"Campaign 42 {short_id('c')}"


def test_a_kit_is_the_parent_of_its_campaigns_in_the_tree(store, tmp_path):
    views = [
        View(view_id=v, incident_id=case, kind=ViewKind.PAGE, label=f"{case} · {v}")
        for v, case in (("a", "1"), ("b", "2"), ("c", "3"), ("d", "4"), ("e", "4"))
    ]
    for case in ("1", "2", "3", "4"):
        store.put_incident(Incident(incident_id=case, root="."), [],
                           [v for v in views if v.incident_id == case])  # fmt: skip
    groups = _groups(
        {"c1": ["a", "b"], "c2": ["c"], "c3": ["d"], "c4": ["e"]},
        {"f1": ["c1", "c2"], "f3": ["c3"], "f4": ["c4"]},
    )
    lib = build_library(store, groups, tmp_path)
    assert lib.get("a").kit == "f1" and lib.get("c").kit == "f1"
    assert lib.get("c").labels[0].text == "No campaign link yet"
    tree = campaign_tree(lib)
    assert [(r.depth, r.kind) for r in tree.rows] == [(0, "kit"), (1, "campaign"), (1, "item")]
    assert tree.rows[0].name == f"Kit {short_id('a')}" and tree.rows[0].cases == ["1", "2", "3"]
    assert tree.unlinked == "Not linked yet: 2 items in 1 case"
    shown = sum(r.items for r in tree.rows if r.kind != "kit") + tree.unlinked_items
    assert shown == len(lib.items())  # every item exactly once


def test_cases_sort_by_number_in_the_tree(store, tmp_path):
    views = [View(view_id=v, incident_id=v, kind=ViewKind.PAGE, label=v) for v in ("14", "2")]
    for v in views:
        store.put_incident(Incident(incident_id=v.view_id, root="."), [], [v])
    lib = build_library(store, _groups({"c1": ["14", "2"]}), tmp_path)
    assert lib.campaigns["c1"].cases == ["2", "14"]
    assert lib.get("2").labels[0].detail == "with 1 more item in 1 case"


@pytest.mark.parametrize(
    ("level", "n", "text"),
    [("error", 1, "1 red flag"), ("error", 2, "2 red flags"), ("warning", 1, "1 warning"),
     ("warning", 3, "3 warnings"), ("none", 0, "")],
)  # fmt: skip
def test_risk_is_worded_with_the_right_plural(level, n, text):
    from ftf.library import Finding

    risk = Risk(level=level, findings=[Finding("Domain", "x", "s")] * n)
    assert risk.text == text


# -- the Campaigns screen: repeats, titles and why ------------------------------------------


def _page(view_id, case, title, host=None):
    meta = {"final_url": f"https://{host}/"} if host else {}
    return View(view_id=view_id, incident_id=case, kind=ViewKind.PAGE, label=f"{case} · {title}",
                primary=f"{title}.html", meta=meta)  # fmt: skip


def test_the_same_page_in_two_cases_is_not_a_campaign(store, tmp_path):
    for view in (_page("a", "47", "Uromexil Forte", "shryxla.icu"), _page("b", "47-copy", "Uromexil Forte", "shryxla.icu")):  # fmt: skip
        store.put_incident(Incident(incident_id=view.incident_id, root="."), [], [view])
    lib = build_library(store, _groups({"c1": ["a", "b"]}), tmp_path)
    repeat = lib.campaigns["c1"]
    assert repeat.repeat and repeat.name is None
    assert lib.get("a").campaign is None
    assert [lab.text for lab in lib.get("a").labels] == ["Duplicate · 47-copy"]
    assert not campaign_tree(lib).rows  # the classic tree agrees


def test_the_same_title_on_two_hosts_or_one_case_stays_a_campaign():
    views = {v.view_id: v for v in [_page("a", "1", "Wiem, kto strzelał", "civicdaybook.live"),
                                    _page("b", "2", "Wiem, kto strzelał", "acrosscommunities.live"),
                                    _page("c", "3", "Oferta", "x.pro"), _page("d", "3", "Oferta", "x.pro")]}  # fmt: skip
    campaigns, _ = name_groups(_groups({"c1": ["a", "b"], "c2": ["c", "d"]}), {}, views, {})
    assert not campaigns["c1"].repeat and campaigns["c1"].name
    assert not campaigns["c2"].repeat  # landing variants inside one case


def test_a_repeat_inside_a_kit_stays_in_the_kit():
    views = {v.view_id: v for v in [_page("a", "1", "Oferta", "x.pro"), _page("b", "2", "Oferta", "x.pro"),
                                    _page("c", "3", "Inna", "y.pro"), _page("d", "3", "Druga", "y.pro")]}  # fmt: skip
    groups = _groups({"c1": ["a", "b"], "c2": ["c", "d"]}, families={"f1": ["c1", "c2"]})
    campaigns, kits = name_groups(groups, {}, views, {})
    assert campaigns["c1"].repeat and campaigns["c1"].kit == "f1"
    assert kits["f1"].campaigns and set(kits["f1"].campaigns) == {"c1", "c2"}


def test_a_campaign_title_comes_from_its_pages_whatever_the_order():
    pages = [_page("a", "1", "ineptsolidoval.top", "ineptsolidoval.top"), _page("b", "1", "Metabo RX", "ineptsolidoval.top"),
             _page("c", "2", "Zamach_ kto strzelał", "a.live"), _page("d", "2", "Zamach_ kto strzelał", "b.live")]  # fmt: skip
    views = {v.view_id: v for v in pages}
    assert campaign_title(["a", "b"], views) == campaign_title(["b", "a"], views) == "Metabo RX"
    assert campaign_title(["a"], views) == "Pages on ineptsolidoval.top"  # only an address
    assert campaign_title(["c", "d", "b"], views) == "Zamach: kto strzelał"  # most common wins
    shots = {
        v: View(view_id=v, incident_id="5", kind=ViewKind.IMAGE, label=f"5 · {v}.png") for v in "xy"
    }
    assert campaign_title(["x", "y"], shots) == "2 pictures"
    long = {"l": _page("l", "1", "słowo " * 30)}
    assert len(campaign_title(["l"], long)) <= 81 and campaign_title(["l"], long).endswith("…")


def test_why_prefers_what_only_the_operator_controls():
    from dataclasses import replace

    from ftf.library import Campaign, Case, Item, Library, campaign_why

    pixel = replace(_match("b", ns="fb_pixel", value="1071875908635809"), clue="Same pixel 1071875908635809")  # fmt: skip
    files = replace(_match("b", verdict="Duplicate"), clue="3 identical files")
    items = [
        Item("a", "1", ViewKind.PAGE, "a", None, [], Risk("none"), matches=[files, pixel]),
        Item("b", "1", ViewKind.PAGE, "b", None, [], Risk("none")),
    ]
    campaign = Campaign("c1", "Campaign x", ["a", "b"], ["1"], "", [])
    lib = Library(cases=[Case("1", "Case 1", "", items)], journeys={}, links={}, campaigns={"c1": campaign})  # fmt: skip
    text, label, strength, note = campaign_why(lib, campaign)
    assert label == "same Facebook tracking pixel"
    assert text == "Same Facebook tracking pixel 1071875908635809" and strength == "Strong"
    assert note.startswith("Only whoever runs the pages")
    lib.get("a").matches = [files]
    assert campaign_why(lib, campaign)[0] == "3 identical files (images or scripts)"


def test_link_captures_of_two_addresses_on_one_host_are_not_a_repeat():
    def capture(view_id, case, url):
        return View(view_id=view_id, incident_id=case, kind=ViewKind.PAGE, label=f"{case} · x",
                    primary="dom.html", meta={"final_url": url})  # fmt: skip

    views = {v.view_id: v for v in [capture("a", "1", "https://shop.top/offer"),
                                    capture("b", "2", "https://shop.top/thanks"),
                                    capture("c", "3", "https://shop.top/offer"),
                                    capture("d", "4", "https://other.top/")]}  # fmt: skip
    campaigns, _ = name_groups(_groups({"c1": ["a", "b"], "c2": ["a", "c"]}), {}, views, {})
    assert not campaigns["c1"].repeat and campaigns["c2"].repeat
    assert campaign_title(["a", "d"], views) == "Pages on other.top and shop.top"


def test_why_falls_back_to_the_journey():
    from ftf.library import Campaign, Case, Item, Journey, Library, campaign_why

    items = [Item(v, "1", ViewKind.PAGE, v, None, [], Risk("none")) for v in "ab"]
    journey = Journey(
        ads=[], steps=[("a", "a"), ("b", "b")], proofs=["Ad ID 12 is in the landing URL"]
    )
    campaign = Campaign("c1", "Campaign x", ["a", "b"], ["1"], "", [])
    lib = Library(cases=[Case("1", "Case 1", "", items)], journeys={"a": journey, "b": journey},
                  links={}, campaigns={"c1": campaign})  # fmt: skip
    assert campaign_why(lib, campaign) == (
        "Ad ID 12 is in the landing URL",
        "the visitor's path",
        "",
        "A visitor is taken from one to the next.",
    )


def test_group_pages_are_titled_for_what_they_are():
    from ftf.library import campaign_heading, kit_heading

    assert campaign_heading("Metabo RX") == "Metabo RX campaign"  # never the page's own title
    assert campaign_heading("Pages on x.top") == "Campaign: Pages on x.top"
    assert campaign_heading("słowo " * 10).startswith("Campaign: ")
    views = {v: _page(v, "1", v) for v in "abcd"}
    assert kit_heading(list("abcd"), views) == "Page template used by 4 pages"
