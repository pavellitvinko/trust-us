"""The shipped corpus: sanitising, loading and matching new material without raw files."""

import gzip
import json
from pathlib import Path

import pytest

from ftf.contracts import Incident, Observation, RelationType, Source, View, ViewKind
from ftf.corpus import (
    DATA_FILE,
    CorpusError,
    build_corpus,
    ensure_seeded,
    export_corpus,
    load_corpus,
)
from ftf.eval.reference import resolve_view
from ftf.fusion import run_method
from ftf.grouping import build_groups
from ftf.intake import run_stages
from ftf.store import Store

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "testing_sample"
TOKEN = "EAAUrealsecrettokenvalue123"


def seeded(store):
    view = View(view_id="v", incident_id="i", kind=ViewKind.PAGE, label="p", primary="p.html")
    store.put_incident(Incident(incident_id="i", root="/home/someone/secret"), [], [view])
    rows = [
        ("ad.access_token", TOKEN),
        ("url.full", f"https://x.pl/?access_token={TOKEN}"),
        ("page.clean_text", "Pełny tekst strony organizatora"),
    ]
    store.put_observations(
        "v",
        "test",
        [
            Observation(
                view_id="v",
                signal_id="X",
                namespace=ns,
                value=value,
                raw=value,
                source=Source(file="p.html"),
                extractor="test",
                version="1",
            )
            for ns, value in rows
        ],
    )


def text(folder):
    with gzip.open(folder / DATA_FILE, "rt", encoding="utf-8") as fh:
        return fh.read()


def test_export_removes_secrets_text_and_paths(store, tmp_path):
    seeded(store)
    export_corpus(store, tmp_path / "c", "test")
    blob = text(tmp_path / "c")
    assert TOKEN not in blob
    assert "Pełny tekst" not in blob
    assert "/home/someone" not in blob


def test_same_store_exports_identical_bytes(store, tmp_path):
    seeded(store)
    export_corpus(store, tmp_path / "a", "test")
    export_corpus(store, tmp_path / "b", "test")
    assert (tmp_path / "a" / DATA_FILE).read_bytes() == (tmp_path / "b" / DATA_FILE).read_bytes()


def test_load_needs_an_empty_store_and_a_known_schema(store, tmp_path):
    seeded(store)
    export_corpus(store, tmp_path / "c", "test")
    with pytest.raises(CorpusError, match="empty"):
        load_corpus(store, tmp_path / "c")
    manifest = tmp_path / "c" / "manifest.json"
    manifest.write_text(json.dumps({"schema": 999}))
    empty = Store(tmp_path / "empty.sqlite")
    with pytest.raises(CorpusError, match="schema"):
        load_corpus(empty, tmp_path / "c")


def test_seeding_is_opt_in(store, tmp_path, monkeypatch):
    source = Store(tmp_path / "source.sqlite")
    seeded(source)
    export_corpus(source, tmp_path / "c", "test")
    monkeypatch.setenv("FTF_CORPUS_DIR", str(tmp_path / "c"))
    monkeypatch.delenv("FTF_SEED_CORPUS", raising=False)
    assert not ensure_seeded(store)
    monkeypatch.setenv("FTF_SEED_CORPUS", "1")
    assert ensure_seeded(store)
    assert store.get_view("v").meta["shipped"] is True


def test_comments_url_keys_and_raw_secrets_do_not_ship(store, tmp_path):
    view = View(view_id="v", incident_id="i", kind=ViewKind.PAGE, label="p")
    store.put_incident(Incident(incident_id="i", root="."), [], [view])

    def row(namespace, value, raw=None):
        return Observation(
            view_id="v",
            signal_id="X",
            namespace=namespace,
            value=value,
            raw=raw,
            extractor="test",
            version="1",
        )

    store.put_observations(
        "v",
        "test",
        [
            row("page.comments", ["Marek Wiśniewski: wziąłem kredyt"]),
            row("url.full", "https://x.pl/?key=05f1233dfdgrsa3f45"),
            row("aff.api_key", "abc12", "abc12"),
            row("x.session", "normalisedvalue", "NormalisedValue"),
        ],
    )
    export_corpus(store, tmp_path / "c", "test")
    blob = text(tmp_path / "c")
    for leaked in ("Wiśniewski", "05f1233", "abc12", "NormalisedValue", "normalisedvalue"):
        assert leaked not in blob


def test_failed_load_leaves_the_store_empty(store, tmp_path):
    seeded(store)
    export_corpus(store, tmp_path / "c", "test")
    data = tmp_path / "c" / DATA_FILE
    lines = gzip.decompress(data.read_bytes()).decode().splitlines()
    data.write_bytes(gzip.compress("\n".join([*lines, '{"kind": "view", "body": {}}']).encode()))
    empty = Store(tmp_path / "empty.sqlite")
    with pytest.raises(CorpusError, match="damaged"):
        load_corpus(empty, tmp_path / "c")
    assert not empty.incidents()


def test_missing_manifest_is_a_clean_error_and_seed_failure_is_survivable(
    store, tmp_path, monkeypatch
):
    seeded(store)
    export_corpus(store, tmp_path / "c", "test")
    (tmp_path / "c" / "manifest.json").unlink()
    empty = Store(tmp_path / "empty.sqlite")
    with pytest.raises(CorpusError):
        load_corpus(empty, tmp_path / "c")
    monkeypatch.setenv("FTF_CORPUS_DIR", str(tmp_path / "c"))
    monkeypatch.setenv("FTF_SEED_CORPUS", "1")
    assert not ensure_seeded(empty)


def test_new_view_matches_a_shipped_hashed_identifier(store, tmp_path, monkeypatch):
    from ftf import pipeline
    from ftf.layers._embedder import set_embedder
    from ftf.registry import load_plugins

    load_plugins()
    monkeypatch.setenv("FTF_EMBEDDER", "fake")
    set_embedder(None)

    def add(view_id, incident):
        view = View(view_id=view_id, incident_id=incident, kind=ViewKind.PAGE, label=view_id)
        return view

    def obs(view_id):
        return Observation(
            view_id=view_id,
            signal_id="A",
            namespace="aff.api_key",
            value="shared-api-key-value-123",
            extractor="test",
            version="1",
        )

    source = Store(tmp_path / "source.sqlite")
    source.put_incident(Incident(incident_id="old", root="."), [], [add("known", "old")])
    source.put_observations("known", "test", [obs("known")])
    pipeline.run_fingerprint(source)
    export_corpus(source, tmp_path / "c", "test")
    load_corpus(store, tmp_path / "c")
    store.put_incident(Incident(incident_id="new", root="."), [], [add("fresh", "new")])
    store.put_observations("fresh", "test", [obs("fresh")])
    pipeline.run_fingerprint(store, ["fresh"])
    known = {f.layer_id: f.payload for f in store.fingerprints(view_id="known")}
    fresh = {f.layer_id: f.payload for f in store.fingerprints(view_id="fresh")}
    assert known and known == fresh
    assert "shared-api-key" not in str(fresh)


def test_folder_cannot_replace_a_known_incident(store, tmp_path):
    from ftf.intake import IntakeError, refuse_known_overwrite

    case = tmp_path / "i"
    case.mkdir()
    (case / "page.html").write_text("<html><body><p>Nowa</p></body></html>")
    source = Store(tmp_path / "source.sqlite")
    seeded(source)
    export_corpus(source, tmp_path / "c", "test")
    load_corpus(store, tmp_path / "c")
    with pytest.raises(IntakeError, match="would replace"):
        refuse_known_overwrite(store, case)


@pytest.mark.skipif(not SAMPLE.is_dir(), reason="testing_sample/ not present")
def test_clean_start_knows_campaigns_and_accepts_new_material(tmp_path, monkeypatch):
    monkeypatch.setenv("FTF_EMBEDDER", "fake")
    monkeypatch.setenv("FTF_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TLDEXTRACT_CACHE", str(tmp_path / "tld"))
    build_corpus(SAMPLE, tmp_path / "corpus")
    store = Store(tmp_path / "clean.sqlite")
    load_corpus(store, tmp_path / "corpus")

    def check():
        relations = {(r.view_a, r.view_b): r.type for r in store.relations()}
        for a, b, expected in (
            ("1:page", "2:page", RelationType.SAME_CAMPAIGN),
            ("2:healthlinkq", "3:page", RelationType.DUPLICATE),
            ("48:page", "61:article", RelationType.SAME_FAMILY),
            ("61:article", "61:chat", RelationType.SAME_CAMPAIGN),
        ):
            pair = tuple(sorted((resolve_view(store, a), resolve_view(store, b))))
            assert relations[pair] is expected, (a, b)
        groups = build_groups(store, persist_funnel=False)
        c48 = groups.campaign_of[resolve_view(store, "48:page")]
        c61 = groups.campaign_of[resolve_view(store, "61:article")]
        assert c48 != c61 and groups.family_of[c48] == groups.family_of[c61]

    check()
    known = resolve_view(store, "1:page")
    before = len(store.observations(known))
    new = tmp_path / "new-case"
    new.mkdir()
    (new / "page.html").write_text("<html><body><p>Nowa oferta</p></body></html>")
    assert run_stages(store, new, enrich=False)["views"] == 1
    assert len(store.observations(known)) == before
    run_method(store)
    check()
