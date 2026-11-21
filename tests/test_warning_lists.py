"""Warning evidence, source provenance, offline operation and analyst integration."""

import json
from pathlib import Path

import pytest

from ftf.contracts import Incident, Observation, Purpose, Source, State, View, ViewKind
from ftf.enrich.warning_lists import (
    NAME,
    WarningLists,
    related_listed_views,
    run_warning_lists,
    warning_observations,
)
from ftf.enrich.warning_sources import (
    CERT_URL,
    KNF_URL,
    MF_REGISTER_URL,
    Sources,
    domain,
    download,
    load_sources,
    make_snapshot,
    parse_domains,
    parse_gambling_xml,
    parse_knf,
    read_snapshot,
    save_snapshot,
)
from ftf.registry import Context, layers, load_plugins
from ftf.store import Store

STAMP = "2026-11-21T22:50:00+00:00"
KNF = """<p>Liczba znalezionych <!-- -->ostrzeżeń: <!-- -->3</p><table><tr>
<th>Lp.</th><th>Nazwa podmiotu</th><th>Prokuratura</th></tr>
<tr><td>1.</td><td>Żółta Capital Ltd z siedzibą w Warszawie (zolte.example)</td>
<td>Unlisted Brand</td></tr>
<tr><td>2.</td><td>Alpha Markets (alpha.example)</td><td>Other Prosecutor</td></tr>
<tr><td>3.</td><td>1. First Broker Ltd 2. Second Broker Ltd</td><td>Other</td></tr>
</table>"""


@pytest.fixture
def sources():
    return Sources(
        {
            "cert": make_snapshot("cert", "phishing.example\nżółty.example\n", STAMP),
            "knf": make_snapshot("knf", KNF, STAMP),
        }
    )


def input_observation(view_id, ns, value, *, state=State.PRESENT):
    return Observation(
        view_id=view_id,
        signal_id="A14",
        namespace=ns,
        value=value,
        state=state,
        source=Source(file="page.html", locator=f"html:{ns}"),
        extractor="test.input",
        version="1",
    )


@pytest.fixture
def warning_store(tmp_path):
    store = Store(tmp_path / "data" / "ftf.sqlite")
    for vid in ("a", "b", "c"):
        view = View(
            view_id=vid, incident_id=vid, kind=ViewKind.PAGE, label=vid, primary="page.html"
        )
        store.put_incident(Incident(incident_id=vid, root=str(tmp_path / vid)), [], [view])
    for vid, ns, value in (
        ("a", "url.host", "child.phishing.example"),
        ("b", "url.host", "clean.example"),
        ("c", "image.brand_offer", "Alpha Marketz"),
    ):
        obs = [input_observation(vid, ns, value)]
        if vid != "c":
            obs.append(
                input_observation(vid, "page.entity.organisation", {"name": "Alpha Markets"})
            )
        store.put_observations(vid, "test.input", obs)
    yield store
    store.close()


def results(store, vid, source):
    return [o for o in warning_observations(store, vid) if o["value"]["source"] == source]


def test_snapshot_parsers_and_domain_boundaries(sources):
    records = parse_knf(KNF)
    assert len(records) == 4
    assert {r["name"] for r in records} >= {"First Broker Ltd", "Second Broker Ltd"}
    assert len({r["id"] for r in records}) == 4
    with pytest.raises(ValueError, match="count mismatch"):
        parse_knf(KNF.replace("<!-- -->3", "<!-- -->4"))
    with pytest.raises(ValueError):
        parse_knf("<html>bot check</html>")
    for invalid in ("<html>error</html>", "", "https://phishing.example/path", "bad_host.example"):
        with pytest.raises(ValueError):
            parse_domains(invalid)
    assert domain("HTTPS://ŻÓŁTY.example./path") == "xn--ty-4ja03asj.example"
    matcher = WarningLists(sources)
    for host, expected in (
        ("phishing.example", True),
        ("sub.phishing.example", True),
        ("notphishing.example", False),
        ("phishing.example.evil.test", False),
        ("example", False),
    ):
        assert bool(matcher.matches("cert", {"kind": "domain", "normalised": host})) is expected
    assert not matcher.matches("knf", {"kind": "entity", "normalised": "unlisted brand"})
    idn = domain("żółty.example")
    assert matcher.matches("cert", {"kind": "domain", "normalised": idn})


def test_dated_hits_misses_and_unavailable_provenance(warning_store, sources, tmp_path):
    run_warning_lists(warning_store, tmp_path, sources=sources)
    hit = results(warning_store, "a", "cert")[0]
    assert hit["state"] == "present" and hit["purpose"] == "risk"
    value = hit["value"]
    assert value["snapshot"]["sha256"] == sources.snapshots["cert"].sha256
    assert value["snapshot"]["source_url"] == CERT_URL
    assert value["matches"][0]["record"]["locator"] == "line[1]"
    assert value["input"]["source"]["file"] == "page.html"
    assert value["input"]["extractor"] == "test.input"
    miss = results(warning_store, "b", "cert")[0]
    assert miss["state"] == "absent"
    assert miss["value"]["message"] == "not listed as of 2026-11-21"
    assert miss["value"]["legitimacy_inference"] is False
    for vid in ("a", "b", "c"):
        assert results(warning_store, vid, "gambling")[0]["state"] == "not_observable"
    # A screenshot's offer name is supported; without a domain CERT is not observable.
    assert results(warning_store, "c", "cert")[0]["state"] == "not_observable"
    fuzzy = results(warning_store, "c", "knf")[0]
    assert fuzzy["state"] == "present"
    assert fuzzy["value"]["result"] == "possible_match" and fuzzy["value"]["uncertain"]
    assert fuzzy["value"]["matches"][0]["score"] < 1
    assert related_listed_views(warning_store, "c") == []


def test_name_normalisation_and_ambiguous_candidates(sources):
    matcher = WarningLists(sources)
    exact = matcher.matches("knf", {"kind": "entity", "normalised": "zolta capital"})
    assert exact[0]["method"] == "exact_name"
    assert not matcher.matches("knf", {"kind": "entity", "normalised": "capital"})
    duplicate = make_snapshot(
        "knf",
        KNF.replace("Alpha Markets (alpha.example)", "Alpha Markets (different.example)"),
        STAMP,
    )
    sources.snapshots["knf"].records += duplicate.records
    matcher = WarningLists(sources)
    view = View(view_id="x", incident_id="x", kind=ViewKind.IMAGE, label="x")
    out = matcher.run(view, [input_observation("x", "image.brand_offer", "Alpha Markets")], None)
    result = next(o for o in out if o.namespace == "warning.knf")
    assert result.value["uncertain"] and result.value["result"] == "possible_match"
    assert len(result.value["matches"]) == 2


def test_refresh_once_fallback_archive_and_integrity(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        return (KNF if url == KNF_URL else "phishing.example\n").encode()

    live = load_sources(tmp_path, refresh=True, fetch=fetch)
    assert calls == [CERT_URL, KNF_URL]  # no gambling request
    old = live.snapshots["cert"]
    assert Path(old.path).is_file() and "archive" in old.path
    assert read_snapshot(Path(old.path), "cert").sha256 == old.sha256
    repeated = load_sources(tmp_path, refresh=True, fetch=fetch)
    assert repeated.snapshots["cert"].path != old.path
    assert read_snapshot(Path(old.path), "cert").fetched_at == old.fetched_at

    def fail(url):
        raise OSError("source unavailable")

    fallback = load_sources(tmp_path, refresh=True, fetch=fail)
    assert fallback.snapshots["cert"].fetched_at == repeated.snapshots["cert"].fetched_at
    assert fallback.snapshots["cert"].sha256 == old.sha256
    assert fallback.errors["cert"].startswith("refresh failed")
    offline = load_sources(tmp_path, fetch=fail)
    assert not offline.errors.get("cert")
    body = json.loads((tmp_path / "cert.json").read_text())
    body["content"] = "tampered.example"
    (tmp_path / "cert.json").write_text(json.dumps(body))
    assert load_sources(tmp_path).snapshots["cert"].sha256 == old.sha256
    # HTML masquerading as a successful response never replaces the last valid snapshot.
    corrupt = load_sources(tmp_path, refresh=True, fetch=lambda _: b"<html>bot check</html>")
    assert corrupt.snapshots["knf"].sha256 == live.snapshots["knf"].sha256
    assert "refresh failed" in corrupt.errors["knf"]
    with pytest.raises(ValueError, match="not allowed"):
        download("https://hazard.mf.gov.pl/")


@pytest.mark.parametrize("body", [[], {"schema_version": 1}, {"schema_version": 1, "content": []}])
def test_malformed_snapshot_is_unavailable(tmp_path, body):
    (tmp_path / "cert.json").write_text(json.dumps(body))
    result = load_sources(tmp_path)
    assert "cert" not in result.snapshots and result.errors["cert"]


def test_failed_refresh_is_visible_on_cached_observations(warning_store, sources, tmp_path):
    sources.errors["cert"] = "refresh failed: OSError"
    run_warning_lists(warning_store, tmp_path, sources=sources)
    cached = results(warning_store, "b", "cert")[0]["value"]
    assert cached["refresh_error"] == "refresh failed: OSError"
    assert cached["snapshot"]["fetched_at"] == STAMP
    assert cached["message"] == "not listed as of 2026-11-21"


def test_multiple_cert_domain_rules_are_a_definite_hit(warning_store, sources, tmp_path):
    sources.snapshots["cert"] = make_snapshot(
        "cert",
        "phishing.example\nchild.phishing.example\n",
        STAMP,
    )
    run_warning_lists(warning_store, tmp_path, sources=sources)
    value = results(warning_store, "a", "cert")[0]["value"]
    assert value["result"] == "listed" and not value["uncertain"]
    assert len(value["matches"]) == 2


def test_replacement_selection_and_no_grouping_effect(warning_store, sources, tmp_path):
    load_plugins()
    run_warning_lists(warning_store, tmp_path, sources=sources)
    first = warning_observations(warning_store, "a")
    run_warning_lists(warning_store, tmp_path, sources=sources)
    assert warning_observations(warning_store, "a") == first
    related = related_listed_views(warning_store, "a")
    assert [r["view_id"] for r in related] == ["b"]
    assert related[0]["context_only"]
    assert related[0]["entity_name"] == "Alpha Markets (alpha.example)"
    # Warning evidence cannot create any fingerprint or campaign grouping.
    for view in warning_store.views():
        obs = [o for o in warning_store.observations(view.view_id) if o.extractor == NAME]
        assert all(layer.build(view, obs) is None for layer in layers.all())
    warning_store.put_observations("a", "test.input", [])
    run_warning_lists(warning_store, tmp_path, view_ids=["a"], sources=sources)
    assert all(o["state"] == "not_observable" for o in warning_observations(warning_store, "a"))
    assert related_listed_views(warning_store, "a") == []
    assert warning_observations(warning_store, "b")
    with pytest.raises(KeyError):
        run_warning_lists(warning_store, tmp_path, view_ids=["does-not-exist"], sources=sources)


def test_cli_manual_import_and_offline_command(warning_store, tmp_path, monkeypatch, capsys):
    from ftf.cli import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FTF_DATA_DIR", str(tmp_path / "data"))
    file = tmp_path / "manual.txt"
    file.write_text("phishing.example\n")
    with pytest.raises(SystemExit, match="snapshot-date"):
        main(["enrich", "--offline", "--import-gambling", str(file)])
    main(
        [
            "enrich",
            "--offline",
            "--import-gambling",
            str(file),
            "--snapshot-date",
            "2026-11-20",
            "--view",
            "a",
        ]
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["views"] == 1
    hit = results(warning_store, "a", "gambling")[0]
    assert hit["state"] == "present"
    assert hit["value"]["snapshot"]["access"] == "manual"
    assert hit["value"]["snapshot"]["snapshot_date"] == "2026-11-20"
    assert not warning_observations(warning_store, "b")


def test_bundled_knf_and_pair_report(warning_store, sources, tmp_path):
    from ftf.reporting import build_pair_report, render_pair_report

    bundled = load_sources(tmp_path)
    assert len(bundled.snapshots["knf"].records) >= 669
    assert (
        len(
            WarningLists(bundled).matches(
                "knf",
                {"kind": "entity", "normalised": "aforti finance"},
            )
        )
        == 1
    )
    run_warning_lists(warning_store, tmp_path, sources=sources)
    before = warning_store.db.total_changes
    report = build_pair_report(warning_store, "a", "b")
    assert warning_store.db.total_changes == before
    assert report["warning_lists"]["shared_listed_entities"]
    assert report["warning_lists"]["observations"]["a"] == warning_observations(warning_store, "a")
    rendered = render_pair_report(report)
    assert "Warning lists" in rendered and "not listed as of 2026-11-21" in rendered
    assert sources.snapshots["cert"].sha256 in rendered
    assert "table[1].row[3].cell[2]" in rendered


def test_material_warning_view_offline(warning_store, sources, tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("FTF_DATA_DIR", str(tmp_path / "data"))
    save_snapshot(sources.snapshots["cert"], tmp_path / "data/warning_lists/cert.json")
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app/streamlit_app.py")).run()
    assert not app.exception
    next(b for b in app.button if b.label == "Check warning lists").click().run()
    assert not app.exception
    assert any("legitimacy" in c.value for c in app.caption)
    assert results(warning_store, "a", "cert")[0]["state"] == "present"
    assert app.json


def test_registered_enricher_is_offline(warning_store):
    from ftf.registry import enrichers

    load_plugins()
    plugin = enrichers.get(NAME)
    view = warning_store.get_view("a")
    out = plugin.run(view, warning_store.observations("a"), Context(warning_store))
    assert all(o.state is State.NOT_OBSERVABLE and o.purpose is Purpose.RISK for o in out)


@pytest.mark.parametrize(
    "name",
    [
        "expresse finances banque",
        "expresse finances bank",
        "swisstrade exchange",
        "cento gx",
        "quantum market",
    ],
)
def test_bundled_knf_brand_aliases(name, tmp_path):
    matcher = WarningLists(load_sources(tmp_path))
    found = matcher.matches("knf", {"kind": "entity", "normalised": name})
    assert found and all(m["method"] == "exact_name" for m in found)


@pytest.mark.parametrize("separator", ["\n", "<br>"])
def test_unnumbered_knf_entities_do_not_link(warning_store, tmp_path, separator):
    content = (
        """<p>Liczba znalezionych ostrzeżeń: 1</p><table><tr>
    <th>Lp.</th><th>Nazwa podmiotu</th></tr><tr><td>1.</td><td>"""
        + separator.join(
            [
                "IGM Holdings - prowadzący działalność pod stroną www.igmholdings.com",
                "Cento GX - prowadzący działalność pod stroną www.centogx.com",
            ]
        )
        + "</td></tr></table>"
    )
    snapshot = make_snapshot("knf", content, STAMP)
    assert len(snapshot.records) == 2
    assert len({r["id"] for r in snapshot.records}) == 2
    for vid, name in (("a", "IGM Holdings"), ("b", "Cento GX"), ("c", "IGM Holdings")):
        warning_store.put_observations(
            vid,
            "test.input",
            [
                input_observation(vid, "offer.name", name),
            ],
        )
    run_warning_lists(warning_store, tmp_path, sources=Sources({"knf": snapshot}))
    assert [r["view_id"] for r in related_listed_views(warning_store, "a")] == ["c"]
    matcher = WarningLists(Sources({"knf": snapshot}))
    found = matcher.matches("knf", {"kind": "domain", "normalised": "www.centogx.com"})
    assert len(found) == 1 and found[0]["record"]["name"].startswith("Cento GX")


def test_wrapped_knf_name_remains_one_entity():
    content = KNF.replace(
        "Alpha Markets (alpha.example)", "Alpha Markets\nz siedzibą w Warszawie\n(alpha.example)"
    )
    snapshot = make_snapshot("knf", content, STAMP)
    assert len(snapshot.records) == 4
    matcher = WarningLists(Sources({"knf": snapshot}))
    assert matcher.matches("knf", {"kind": "entity", "normalised": "alpha markets"})


def test_interrupted_download_uses_dated_fallback(tmp_path):
    from http.client import IncompleteRead

    def fail(_):
        raise IncompleteRead(b"partial response", 100)

    result = load_sources(tmp_path, refresh=True, fetch=fail)
    assert result.snapshots["knf"].fetched_at == STAMP
    assert "cert" not in result.snapshots
    assert result.errors["cert"] == "refresh failed: IncompleteRead"


def test_corrupt_current_and_latest_archive_fall_back(tmp_path):
    from ftf.enrich.warning_sources import cache_snapshot

    old = make_snapshot("cert", "old.example\n", "2026-11-20T00:00:00+00:00")
    cache_snapshot(old, tmp_path)
    latest = make_snapshot("cert", "latest.example\n", STAMP)
    cache_snapshot(latest, tmp_path)
    Path(latest.path).write_text("damaged archive")
    (tmp_path / "cert.json").write_text("damaged pointer")
    result = load_sources(tmp_path)
    assert result.snapshots["cert"].sha256 == old.sha256
    assert result.snapshots["cert"].fetched_at == old.fetched_at


def test_risk_reasons_exclude_misses_and_preserve_uncertainty(warning_store, sources, tmp_path):
    from ftf.enrich.warning_lists import warning_risk_reasons
    from ftf.reporting import build_pair_report

    run_warning_lists(warning_store, tmp_path, sources=sources)
    observations = warning_observations(warning_store, "b")
    reasons = warning_risk_reasons(observations)
    assert (
        warning_risk_reasons(
            observations
            + [
                input_observation("b", "other.risk", "unrelated").model_dump(mode="json"),
            ]
        )
        == reasons
    )
    assert len(reasons) == 1 and reasons[0]["source"] == "knf"
    assert reasons[0]["observation"] in observations and reasons[0]["observation_ref"]
    assert reasons[0]["observation"]["value"]["snapshot"]["source_url"] == KNF_URL
    assert reasons[0]["kind"] == "domain" and not reasons[0]["uncertain"]
    fuzzy = warning_risk_reasons(warning_observations(warning_store, "c"))
    assert len(fuzzy) == 1 and fuzzy[0]["uncertain"]
    report = build_pair_report(warning_store, "a", "b")
    assert report["warning_lists"]["risk_reasons"]["b"] == reasons


def test_legacy_entity_links_require_rechecking(warning_store, sources, tmp_path):
    from ftf.enrich.warning_lists import listed_entities

    run_warning_lists(warning_store, tmp_path, sources=sources)
    observations = warning_observations(warning_store, "a")
    for observation in observations:
        observation["version"] = "1"
    assert not listed_entities(observations)


def test_live_cli_refreshes_once_before_matching(warning_store, monkeypatch, tmp_path, capsys):
    from contextlib import contextmanager
    from io import BytesIO

    from ftf.cli import main
    from ftf.enrich import warning_sources

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FTF_DATA_DIR", str(tmp_path / "data"))
    calls = []
    infrastructure_calls = []

    def infrastructure(store, view_ids):
        from collections import Counter

        infrastructure_calls.append(view_ids)
        return Counter({("dns.ns_pair", "present"): 1})

    from ftf import pipeline

    monkeypatch.setattr(pipeline, "run_enrich", infrastructure)

    @contextmanager
    def response(request, **kwargs):
        calls.append(request.full_url)
        yield BytesIO((KNF if request.full_url == KNF_URL else "phishing.example\n").encode())

    monkeypatch.setattr(warning_sources, "urlopen", response)
    main(["enrich"])
    assert calls == [CERT_URL, KNF_URL]
    summary = json.loads(capsys.readouterr().out)
    assert summary["views"] == 3
    assert infrastructure_calls == [None]
    assert summary["infrastructure"] == [
        {"namespace": "dns.ns_pair", "state": "present", "count": 1}
    ]
    assert results(warning_store, "a", "cert")[0]["value"]["result"] == "listed"


def test_domain_and_offer_cli_acceptance_with_network_and_models_blocked(tmp_path):
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    incident = tmp_path / "sample" / "1"
    incident.mkdir(parents=True)
    (incident / "page.html").write_text(
        "<!-- saved from url=(0028)https://phishing.example/ -->"
        '<form><input type="hidden" name="offer" value="Aforti Finance"></form>'
    )
    save_snapshot(
        make_snapshot("cert", "phishing.example\n", STAMP),
        tmp_path / "data/warning_lists/cert.json",
    )
    audit = tmp_path / "audit.json"
    env = {
        **os.environ,
        "FTF_DATA_DIR": str(tmp_path / "data"),
        "TLDEXTRACT_CACHE": str(tmp_path / "tld"),
        "FTF_EMBEDDER": "fake",
        "FTF_MODEL_OFF_AUDIT": str(audit),
        "FTF_ENRICH": "off",
        "OPENAI_API_KEY": "",
        "GEMINI_API_KEY": "",
        "PYTHONPATH": os.pathsep.join((str(root / "src"), str(root))),
    }
    for args in (("ingest", str(tmp_path / "sample")), ("enrich", "--offline")):
        result = subprocess.run(
            [sys.executable, "-m", "tests._model_off_cli", *args],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(audit.read_text()) == {"model": [], "network": []}
    store = Store(tmp_path / "data/ftf.sqlite")
    try:
        view = store.views()[0]
        for source in ("cert", "knf"):
            hits = [
                o for o in results(store, view.view_id, source) if o["value"]["result"] == "listed"
            ]
            assert hits, source
            assert hits[0]["value"]["snapshot"]["sha256"]
            assert hits[0]["value"]["matches"][0]["record"]["locator"]
    finally:
        store.close()


MF_XML = """<?xml version="1.0" encoding="utf-8"?>
<Rejestr xmlns="http://www.hazard.mf.gov.pl/2017/03/21/">
 <PozycjaRejestru Lp="60244">
  <AdresDomeny>www.casinolabcz.com</AdresDomeny>
  <DataWpisu>2026-11-20T13:56:55</DataWpisu>
 </PozycjaRejestru>
</Rejestr>"""


def test_mf_xml_retains_record_identity_and_rejects_wrong_responses():
    record = parse_gambling_xml(MF_XML)[0]
    assert record["id"] == "mf:60244"
    assert record["domain"] == "www.casinolabcz.com"
    assert record["listed_at"] == "2026-11-20T13:56:55"
    assert record["locator"] == "xml:Rejestr/PozycjaRejestru[@Lp='60244']/AdresDomeny"
    for invalid in (
        "<html>bot check</html>",
        MF_XML[:-10],
        MF_XML.replace(
            "</PozycjaRejestru>", "<DataWykreslenia>2026-11-21</DataWykreslenia></PozycjaRejestru>"
        ),
        MF_XML.replace("<PozycjaRejestru Lp", "<OtherRecord Lp"),
        MF_XML.replace("<Rejestr xmlns", "<!DOCTYPE Rejestr><Rejestr xmlns"),
    ):
        with pytest.raises(ValueError):
            parse_gambling_xml(invalid)
    with pytest.raises(ValueError, match="not allowed"):
        download(MF_REGISTER_URL)


def test_xml_unsupported_addresses_are_retained_without_entering_match_index():
    snapshot = make_snapshot(
        "gambling",
        MF_XML.replace("www.casinolabcz.com", "bad/host.example"),
        STAMP,
        access="manual",
    )
    assert len(snapshot.records) == 1 and snapshot.records[0]["domain"] is None
    assert snapshot.records[0]["raw"]["AdresDomeny"] == "bad/host.example"
    assert snapshot.provenance()["unmatchable_record_count"] == 1
    matcher = WarningLists(Sources({"gambling": snapshot}))
    assert not matcher.domain_indexes["gambling"]


def test_xml_manual_import_preserves_original_bytes(warning_store, tmp_path, monkeypatch, capsys):
    from hashlib import sha256

    from ftf.cli import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FTF_DATA_DIR", str(tmp_path / "data"))
    original = MF_XML.replace("\n", "\r\n").encode("utf-8")
    file = tmp_path / "Register.xml"
    file.write_bytes(original)
    main(["enrich", "--offline", "--import-gambling", str(file), "--snapshot-date", "2026-11-22"])
    summary = json.loads(capsys.readouterr().out)
    assert summary["snapshots"]["gambling"]["sha256"] == sha256(original).hexdigest()
    snapshot = read_snapshot(tmp_path / "data/warning_lists/gambling.json", "gambling")
    assert snapshot.content.encode("utf-8") == original
    assert snapshot.source_url == MF_REGISTER_URL


def test_bundled_gambling_snapshot_checks_offline_with_provenance(warning_store, tmp_path):
    from ftf.enrich.warning_lists import warning_risk_reasons
    from ftf.reporting import build_pair_report, render_pair_report

    sources = load_sources(tmp_path)
    snapshot = sources.snapshots["gambling"]
    assert snapshot.fetched_at.startswith("2026-11-22T")
    assert snapshot.sha256 == "854b94c7a3591882c756b904ec3af51a6465cc9eead91063c969bf5a6db4395f"
    assert len(snapshot.records) == 60231
    assert snapshot.provenance()["unmatchable_record_count"] == 0
    assert snapshot.source_url == MF_REGISTER_URL and snapshot.access == "manual"
    for vid, host in (("a", "www.casinolabcz.com"), ("b", "unlisted.example")):
        warning_store.put_observations(
            vid, "test.input", [input_observation(vid, "url.host", host)]
        )
    run_warning_lists(warning_store, tmp_path, sources=sources)
    hit = results(warning_store, "a", "gambling")[0]
    assert hit["state"] == "present" and hit["value"]["result"] == "listed"
    assert hit["value"]["matches"][0]["record"]["id"] == "mf:60244"
    assert hit["value"]["matches"][0]["record"]["raw"]["DataWpisu"] == "2026-11-20T13:56:55"
    miss = results(warning_store, "b", "gambling")[0]
    assert miss["state"] == "absent" and miss["value"]["message"] == "not listed as of 2026-11-22"
    assert not miss["value"]["legitimacy_inference"]
    assert any(
        r["source"] == "gambling"
        for r in warning_risk_reasons(warning_observations(warning_store, "a"))
    )
    report = build_pair_report(warning_store, "a", "b")
    assert any(
        o["value"]["source"] == "gambling" for o in report["warning_lists"]["observations"]["a"]
    )
    assert "mf:60244" in render_pair_report(report)
    warning_store.put_observations(
        "a",
        "test.input",
        [
            input_observation(
                "a",
                "url.host",
                "nomini02_but_w.lpmediastorage.com",
            )
        ],
    )
    run_warning_lists(warning_store, tmp_path, sources=sources, view_ids=["a"])
    underscore_hit = results(warning_store, "a", "gambling")[0]
    assert underscore_hit["value"]["result"] == "listed"
    assert underscore_hit["value"]["matches"][0]["record"]["id"] == "mf:20578"
