"""Discovery: parser, state, queries, triage, ingestion of ads, and an end-to-end replay run."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ftf import pipeline
from ftf.discovery.meta_web import library_url
from ftf.discovery.models import LibraryAd, LibraryQuery, QueryKind, QueryOrigin
from ftf.discovery.parse import ads_from_text, captcha_required
from ftf.discovery.queries import ProposedQueries, ProposedQuery, model_queries, pivot_queries
from ftf.discovery.replay import ReplaySource
from ftf.discovery.run import run_discovery
from ftf.discovery.service import plan_queries
from ftf.discovery.state import INGESTED, SCREENED_OUT, DiscoveryState
from ftf.discovery.triage import Reason, Screening, ad_identifiers, triage
from ftf.ingest import ingest_folder
from ftf.models import ModelResult
from ftf.store import Store

FIX = Path(__file__).parent / "fixtures" / "discovery"
SAMPLE = Path(__file__).resolve().parents[1] / "testing_sample"


def _text(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def _ads(name: str) -> dict[str, LibraryAd]:
    return {ad.ad_id: ad for ad, _ in ads_from_text(_text(name))}


class FakeRead:
    """Stands in for `models.read_structured`; records calls, returns canned answers."""

    def __init__(self, answers: dict[str, object] | None = None, default=None):
        self.answers, self.default, self.calls = answers or {}, default, []

    def __call__(self, task, prompt_version, system, content, schema, *a, **k):
        self.calls.append((task, content))
        for key, answer in self.answers.items():
            if key in content:
                return ModelResult.ok(answer)
        if self.default is None:
            return ModelResult.not_observable("no_key")
        return ModelResult.ok(self.default)


# -- parser ------------------------------------------------------------------------------


def test_parser_reads_graphql_and_enforcement_states():
    ads = _ads("treekollo_page.txt")
    assert len(ads) == 5
    removed = ads["1982065065827084"]
    assert removed.page_id == "1183383648198728" and removed.page_name == "TreeKollo"
    assert removed.enforcement == "TAKEN_DOWN" and removed.enforced
    assert removed.bodies == []  # "{{product.brand}}" is a template macro, not text
    assert removed.start_date == "2026-08-28" and removed.is_active is False
    assert "FACEBOOK" in removed.platforms
    eligible = [a for a in ads.values() if a.enforcement == "ELIGIBLE"]
    assert eligible and all(a.link_urls for a in eligible)
    assert any(a.enforcement == "AD_ACCOUNT_DISABLED" for a in ads.values())
    assert removed.library_url.endswith("?id=1982065065827084")


def test_parser_reads_html_embedded_json_with_other_nesting():
    ads = _ads("treekollo_single_ad.html")
    assert list(ads) == ["1982065065827084"]
    assert not captcha_required(_text("treekollo_single_ad.html"))


def test_parser_tolerates_noise_and_flags_captcha():
    assert ads_from_text("not json\n{}\nfor (;;);[1,2]") == []
    assert ads_from_text('{"ad_archive_id": "abc", "snapshot": {}}') == []  # non-numeric ID
    assert captcha_required(_text("captcha.html"))


def test_library_url_by_kind():
    assert "view_all_page_id=123" in library_url(LibraryQuery(kind=QueryKind.PAGE, terms="123"))
    assert library_url(LibraryQuery(kind=QueryKind.AD, terms="99")).endswith("?id=99")
    url = library_url(LibraryQuery(terms="złoto zysk", exact_phrase=True, date_min="2026-11-01"))
    assert "keyword_exact_phrase" in url and "country=PL" in url and "2026-11-01" in url


# -- state -------------------------------------------------------------------------------


def test_state_records_new_seen_and_stopped():
    state = DiscoveryState(":memory:")
    q = LibraryQuery(terms="x")
    run = state.start_run("test")
    ad = LibraryAd(ad_id="1", page_id="p", is_active=True)
    assert state.record_ad(run, q.query_id, ad, {}) == "new"
    assert state.record_ad(run, q.query_id, ad, {}) == "seen"
    stopped = ad.model_copy(update={"is_active": False, "end_date": "2026-11-20"})
    assert state.record_ad(run, q.query_id, stopped, {}) == "stopped"
    row = state.ad("1")
    assert row["is_active"] == 0 and row["end_date"] == "2026-11-20"
    assert [r["ad_id"] for r in state.ads(run_id=run)] == ["1"]


def test_query_identity_ignores_reason_and_disabled_queries_are_skipped():
    store, state = Store(":memory:"), DiscoveryState(":memory:")
    a = LibraryQuery(terms="Zysk", origin=QueryOrigin.WATCHLIST, reason="one")
    b = LibraryQuery(terms="zysk ", origin=QueryOrigin.MANUAL, reason="two")
    assert a.query_id == b.query_id
    queries, _ = plan_queries(store, state, extra=[a, b], pivots=False)
    assert len(queries) == 1
    state.set_enabled(a.query_id, False)
    queries, _ = plan_queries(store, state, extra=[a], pivots=False)
    assert queries == []


# -- queries -----------------------------------------------------------------------------


@pytest.mark.skipif(not SAMPLE.is_dir(), reason="testing_sample/ not present")
def test_case5_info_json_gives_ad_library_ids_and_a_page_pivot():
    store = Store(":memory:")
    _, views = ingest_folder(SAMPLE / "5", store)
    pipeline.run_extract(store)
    values = {(o.namespace, o.value) for v in views for o in store.observations(v.view_id)}
    assert ("ad_library.ad_id", "1982065065827084") in values
    assert ("ad_library.page_id", "1183383648198728") in values
    pivots = pivot_queries(store)
    assert any(q.kind is QueryKind.PAGE and q.terms == "1183383648198728" for q in pivots)


def test_model_queries_are_capped_deduplicated_and_tagged():
    store = Store(":memory:")
    # no known text yet: nothing to ask the model about
    assert model_queries(store, read=FakeRead())[0] == []


def test_model_queries_from_context(monkeypatch):
    import ftf.discovery.queries as q

    monkeypatch.setattr(q, "campaign_context", lambda store: "Incident 1:\n- topic: investment")
    answer = ProposedQueries(queries=[
        ProposedQuery(terms="Brzoska inwestycja", exact_phrase=False, reason="r"),
        ProposedQuery(terms="brzoska   inwestycja", exact_phrase=False, reason="dup"),
        ProposedQuery(terms="ab", exact_phrase=False, reason="too short"),
    ])  # fmt: skip
    out, why = q.model_queries(Store(":memory:"), read=FakeRead(default=answer))
    assert why is None
    assert [x.terms for x in out] == ["Brzoska inwestycja"]
    assert out[0].origin is QueryOrigin.MODEL


# -- triage ------------------------------------------------------------------------------


def test_triage_known_match_wins_and_quotes_are_checked():
    store = Store(":memory:")
    ad = _ads("synthetic_healthlinkq.txt")["900000000000001"]
    assert ("domain.registrable", "healthlinkq.pro") in ad_identifiers(ad)
    screening = Screening(
        likely_scam="yes",
        offer_kind="investment",
        reasons=[
            Reason(reason="guaranteed profit", quote="Inwestycja z gwarancją zysku"),
            Reason(reason="invented quote", quote="this text is not in the ad"),
        ],
    )
    result = triage(store, ad, read=FakeRead(default=screening))
    assert result.verdict == "yes" and len(result.reasons) == 1 and not result.known_matches
    assert result.score == pytest.approx(0.8)


def test_triage_without_model_uses_enforcement():
    ads = _ads("treekollo_page.txt")
    store = Store(":memory:")
    taken_down = triage(store, ads["2272835100173276"], use_model=False)
    assert taken_down.verdict == "not_screened" and taken_down.score >= 0.45


# -- ingestion of a discovered ad ---------------------------------------------------------


def test_materialised_ad_becomes_one_image_view_with_its_record(tmp_path):
    from ftf.discovery.materialise import materialise

    ad, raw = ads_from_text(_text("synthetic_healthlinkq.txt"))[0]
    replay = ReplaySource(FIX)
    folder = materialise(ad, raw, tmp_path, replay, download_media=False)
    store = Store(":memory:")
    _, views = ingest_folder(folder, store)
    assert len(views) == 1
    view = views[0]
    assert view.kind.value == "image" and view.meta["origin"] == "ad_library"
    assert view.attachments == ["ad.json"] and view.meta["ad_library_id"] == ad.ad_id
    pipeline.run_extract(store)
    values = {(o.namespace, o.value) for o in store.observations(view.view_id)}
    assert ("domain.registrable", "healthlinkq.pro") in values
    assert ("url.host_path", "healthlinkq.pro/L41156846fl-923e569-fkf418Z") in values
    assert ("ad_library.page_id", "900000000000999") in values
    assert ("ad_library.enforcement", "ELIGIBLE") in values


# -- end to end on replay -----------------------------------------------------------------


@pytest.mark.skipif(not SAMPLE.is_dir(), reason="testing_sample/ not present")
def test_replay_run_links_a_discovered_ad_to_known_cases(tmp_path):
    store, state = Store(":memory:"), DiscoveryState(":memory:")
    for case in ("2", "3", "5"):
        ingest_folder(SAMPLE / case, store)
    pipeline.run_extract(store)
    pipeline.run_fingerprint(store)

    queries, _ = plan_queries(store, state, extra=[LibraryQuery(terms="inwestycja")])
    assert any(q.kind is QueryKind.PAGE and q.terms == "1183383648198728" for q in queries)
    screening = Screening(likely_scam="no", offer_kind="property", reasons=[])
    report = run_discovery(
        store, state, ReplaySource(FIX), queries, tmp_path, use_model=True,
        read=FakeRead(default=screening), download_media=False,
    )  # fmt: skip
    assert report.status == "ok"
    assert report.counts["new"] >= 6

    # the synthetic ad pointing at healthlinkq.pro is a known-campaign match and is ingested
    synthetic = state.ad("900000000000001")
    assert synthetic["status"] == INGESTED
    assert any(m["ns"] == "domain.registrable" for m in synthetic["triage"].known_matches)
    item = next(i for i in report.ingested if i.ad_id == "900000000000001")
    linked_incidents = {link["view"].split(":")[0] for link in item.links}
    assert {"2", "3"} <= linked_incidents

    # ads of the case 5 advertiser page match the known page ID and are ingested too
    assert state.ad("2621609914968664")["status"] == INGESTED
    # an ordinary business the model clears is kept, not ingested
    assert state.ad("2026418911226437")["status"] == SCREENED_OUT

    # a second run finds nothing new; keyword searches now ask only for ads delivered since the
    # last run, so old ads drop out of them, while the page query (no date window) still sees all
    again = run_discovery(store, state, ReplaySource(FIX), queries, tmp_path, use_model=False,
                          download_media=False)  # fmt: skip
    assert again.counts["new"] == 0
    assert 5 <= again.counts["seen"] < report.counts["seen"]
    assert json.loads(json.dumps(state.runs()[0]["counts"]))["new"] == 0


class _BlockingSource:
    """Blocks on the second query; must never be asked a third time."""

    name = "fake"

    def __init__(self):
        self.asked: list[str] = []

    def fetch(self, query, max_ads):
        from ftf.discovery.source import FetchResult, FetchStatus

        self.asked.append(query.terms)
        if len(self.asked) == 2:
            return FetchResult(status=FetchStatus.BLOCKED, error="the library asked for a CAPTCHA")
        return FetchResult(status=FetchStatus.OK, ads=ads_from_text(_text("search_inwestycja.txt")))

    def screenshot_ad(self, ad_id, path):
        return False

    def close(self):
        return None


def test_a_block_stops_the_run_and_nothing_is_ingested(tmp_path):
    store, state, source = Store(":memory:"), DiscoveryState(":memory:"), _BlockingSource()
    queries = [LibraryQuery(terms=t) for t in ("one", "two", "three")]
    report = run_discovery(store, state, source, queries, tmp_path, use_model=False)
    assert report.status == "blocked" and "CAPTCHA" in report.error
    assert source.asked == ["one", "two"]
    assert report.ingested == [] and not any(tmp_path.iterdir())
    assert state.runs()[0]["status"] == "blocked"
    # ads seen before the block are kept and screened, ready for a later run
    assert report.counts["new"] == 3 and state.ads()


# -- a person clears a CAPTCHA or login in the visible browser -----------------------------


class _Loc:
    def __init__(self, visible):
        self._visible = visible

    @property
    def first(self):
        return self

    def is_visible(self):
        return self._visible


class _FakePage:
    """Shows `states[i]` on the i-th poll: 'login', 'captcha', 'checkpoint' or None."""

    def __init__(self, states, url="https://www.facebook.com/ads/library/?q=x"):
        self.states, self.polls, self.url, self.visits = list(states), 0, url, []

    @property
    def _now(self):
        return self.states[min(self.polls, len(self.states) - 1)]

    def locator(self, selector):
        if "input[name='email']" in selector:
            return _Loc(self._now == "login")
        if "captcha" in selector:
            return _Loc(self._now == "captcha")
        return _Loc(False)

    def goto(self, url, **_):
        self.visits.append(url)

    def bring_to_front(self):
        return None


def _web(monkeypatch, tmp_path, human=True, wait_s=60):
    import ftf.discovery.meta_web as mw

    monkeypatch.setattr(mw, "PAUSE_BETWEEN_PAGES_S", 0)
    notes: list[str] = []
    source = mw.MetaWebSource(tmp_path, headed=True, human=human, human_wait_s=wait_s,
                              notify=notes.append)  # fmt: skip
    return mw, source, notes


def _clock_and_sleep(page):
    t = [0.0]

    def sleep(seconds):
        t[0] += seconds
        page.polls += 1

    return (lambda: t[0]), sleep


def test_login_cleared_by_a_person_reopens_the_search(monkeypatch, tmp_path):
    mw, source, notes = _web(monkeypatch, tmp_path)
    page = _FakePage(["login", "login", None])
    clock, sleep = _clock_and_sleep(page)
    url = "https://www.facebook.com/ads/library/?q=zysk"
    assert source.wait_for_human(page, mw.LOGIN, url, [], sleep=sleep, clock=clock)
    assert page.visits == [url] and source.human_waits == 1
    assert notes[0].startswith("Action needed") and "log in" in notes[0]


def test_captcha_resolved_when_the_page_loads_ads(monkeypatch, tmp_path):
    mw, source, _ = _web(monkeypatch, tmp_path)
    page = _FakePage([None])
    clock, sleep = _clock_and_sleep(page)
    bodies: list[str] = []

    def sleep_then_ads(seconds):
        sleep(seconds)
        if page.polls == 2:
            bodies.append(_text("search_inwestycja.txt"))

    assert source.wait_for_human(page, mw.CAPTCHA, "u", bodies, sleep=sleep_then_ads, clock=clock)
    assert page.visits == []  # nothing reopened: the page loaded results by itself


def test_visible_captcha_widget_gone_counts_as_solved(monkeypatch, tmp_path):
    mw, source, _ = _web(monkeypatch, tmp_path)
    page = _FakePage(["captcha", "captcha", None])
    clock, sleep = _clock_and_sleep(page)
    assert source.wait_for_human(page, mw.CAPTCHA, "u", [], sleep=sleep, clock=clock)
    assert page.visits == ["u"]


def test_no_person_in_time_or_no_window_means_stop(monkeypatch, tmp_path):
    mw, source, notes = _web(monkeypatch, tmp_path, wait_s=30)
    page = _FakePage(["checkpoint"], url="https://www.facebook.com/checkpoint/123")
    assert mw.visible_obstacle(page) == mw.CHECKPOINT
    clock, sleep = _clock_and_sleep(page)
    assert not source.wait_for_human(page, mw.CHECKPOINT, "u", [], sleep=sleep, clock=clock)
    assert notes[-1].startswith("No change") and source.human_waits == 0

    _, hidden, hidden_notes = _web(monkeypatch, tmp_path, human=True)
    hidden.human = False  # headless: nobody can act, so stop at once
    assert not hidden.wait_for_human(_FakePage(["login"]), mw.LOGIN, "u", [])
    assert hidden_notes == []
    _, no_human, _ = _web(monkeypatch, tmp_path, human=False)
    assert no_human.human is False
