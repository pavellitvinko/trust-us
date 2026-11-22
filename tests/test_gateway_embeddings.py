"""The gateway embedding path and its run record (HAC-32), with a fake Gemini client: no
network, no key. Real-model numbers come only from `ftf validate --model gateway`."""

import hashlib
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from ftf import pipeline
from ftf.compare.vector import calibrate, get_calibration, set_calibration
from ftf.compare.weights import weights
from ftf.contracts import Fingerprint, FingerprintKind
from ftf.eval import sample
from ftf.eval.fixtures import store_from_fixtures
from ftf.fusion import prepare
from ftf.layers._embedder import FakeEmbedder, GatewayEmbedder, set_embedder
from ftf.layers.vectors import _BYLINE, mask
from ftf.models.gemini import EMBEDDING_DIMENSIONS
from ftf.registry import comparators

MODEL = "embed-test"


def _vector(text: str) -> list[float]:
    """Deterministic 3072-dim vector: hashed 5-character stems, like the fake embedder but at
    the gateway's size, so the two models are told apart by name and dimension alike."""
    vec = [0.0] * EMBEDDING_DIMENSIONS
    for word in text.lower().split():
        h = int.from_bytes(hashlib.blake2b(word[:5].encode(), digest_size=4).digest(), "big")
        vec[h % EMBEDDING_DIMENSIONS] += 1.0 if (h >> 31) == 0 else -1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class FakeGemini:
    """Stands in for `google.genai.Client`: `models.embed_content` answers from text."""

    def __init__(self, fail: Exception | None = None):
        self.calls = 0
        self.fail = fail
        self.models = SimpleNamespace(embed_content=self._embed)

    def _embed(self, model, contents, config):
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        return SimpleNamespace(embeddings=[SimpleNamespace(values=_vector(contents))])


@pytest.fixture
def gateway(settings):
    client = FakeGemini()
    emb = GatewayEmbedder(settings.embedding_model, settings=settings, client=client)
    set_embedder(emb)
    yield emb, client
    set_embedder(None)
    weights.reset()
    for layer in sample.VECTOR_LAYERS:
        set_calibration(layer, None)


def _fingerprinted(store):
    pipeline.run_fingerprint(store)
    prepare(store)
    return store


def test_gateway_vectors_carry_the_model_and_the_record_says_gateway(gateway):
    emb, client = gateway
    s = _fingerprinted(store_from_fixtures())
    try:
        vectors = list(s.fingerprints(layer_id="text.semantic"))
        assert vectors and all(f.payload["model"] == MODEL for f in vectors)
        assert all(len(f.payload["values"]) == EMBEDDING_DIMENSIONS for f in vectors)
        # identical fixture bodies hit the cache within one run, so calls <= chunk uses
        assert 0 < client.calls <= emb.chunks and not emb.failures
        line = sample.mode_line()
        assert line.startswith("gateway embeddings: model `embed-test`")
        assert "prompt v1" in line and "3072 dimensions" in line
        assert f"{emb.texts} texts embedded as {emb.chunks} chunk calls" in line
        cal = get_calibration("narrative.skeleton")
        assert cal and cal.model == MODEL and cal.pairs > 0 and cal.stale == 0
    finally:
        s.close()


def test_second_run_is_served_from_the_cache(gateway, settings):
    emb, client = gateway
    s = _fingerprinted(store_from_fixtures())
    s.close()
    first, used = client.calls, emb.chunks
    assert sample.cache_entries(settings) == first  # one entry per distinct chunk
    s = _fingerprinted(store_from_fixtures())
    s.close()
    assert client.calls == first  # nothing new reached the client
    assert emb.chunks == 2 * used  # yet every vector was built again, from the cache


def test_a_failing_gateway_leaves_the_layer_out_never_a_vector(settings):
    emb = GatewayEmbedder(
        settings.embedding_model, settings=settings, client=FakeGemini(TimeoutError())
    )
    set_embedder(emb)
    try:
        s = _fingerprinted(store_from_fixtures())
        assert not list(s.fingerprints(layer_id="text.semantic"))
        assert not list(s.fingerprints(layer_id="narrative.skeleton"))
        assert emb.failures and set(emb.failures) == {"timeout"}
        assert "left without a vector: " in emb.describe() and "timeout" in emb.describe()
        assert get_calibration("text.semantic") is None
        a, b = [v.view_id for v in s.views()][:2]
        for e in pipeline.compare_views(s, a, b):
            if e.layer_id in sample.VECTOR_LAYERS:
                assert not e.comparable and e.score == 0.0
        s.close()
    finally:
        set_embedder(None)
        weights.reset()


def test_no_key_means_no_gateway_run(settings, monkeypatch):
    with pytest.raises(ValueError):
        sample.validation_report(Path("testing_sample"), model="maybe")
    with pytest.raises(ValueError, match="model='gateway'"):
        sample.validation_report(Path("testing_sample"), embedder=FakeEmbedder())
    with pytest.raises(sample.MissingKeyError, match="GEMINI_API_KEY"):
        sample.gateway_embedder(settings)
    # the environment of the machine running the suite must not turn this into a paid run
    monkeypatch.setenv("GEMINI_API_KEY", "")
    with pytest.raises(sample.MissingKeyError):
        sample.validation_report(Path("testing_sample"), model="gateway")


def test_calibration_is_per_model_and_stale_vectors_are_not_comparable(store):
    def fp(v, model, values):
        return Fingerprint(
            view_id=v,
            layer_id="text.semantic",
            kind=FingerprintKind.VECTOR,
            payload={"model": model, "values": values},
            version="t",
        )

    from ftf.contracts import Incident, View, ViewKind

    for i, v in enumerate(("a", "b", "c", "d")):
        store.put_incident(
            Incident(incident_id=str(i), root=".", label=str(i)),
            [],
            [View(view_id=v, incident_id=str(i), kind=ViewKind.PAGE, label=v)],
        )
    store.put_fingerprint(fp("a", "old", [1, 0]))
    store.put_fingerprint(fp("b", "old", [0, 1]))
    store.put_fingerprint(fp("c", "new", [1, 0, 0]))
    store.put_fingerprint(fp("d", "new", [0.6, 0.8, 0]))
    cal = calibrate(store, "text.semantic", model="new")
    assert cal and cal.model == "new" and cal.pairs == 1 and cal.stale == 2
    assert cal.mu == pytest.approx(0.6)
    cmp = comparators.get("calibrated_cosine")
    assert not cmp.compare(fp("a", "old", [1, 0]), fp("c", "new", [1, 0, 0])).comparable
    # two stale vectors of the retired model: not scored against the new model's background
    assert not cmp.compare(fp("a", "old", [1, 0]), fp("b", "old", [1, 0])).comparable
    assert cmp.compare(fp("c", "new", [1, 0, 0]), fp("d", "new", [1, 0, 0])).score == 1.0
    # a model with fewer than two vectors clears the calibration instead of keeping the old fit
    assert calibrate(store, "text.semantic", model="other") is None
    assert get_calibration("text.semantic") is None
    # a store another embedder built (the shipped corpus under a key) keeps its own model
    set_embedder(GatewayEmbedder("gemini-elsewhere"))
    try:
        store.put_fingerprint(fp("d", "old", [0, 1]))
        cal = calibrate(store, "text.semantic")
        assert cal and cal.model == "old" and cal.stale == 1
    finally:
        set_embedder(None)


def test_a_missing_same_text_vector_makes_the_check_unmeasurable_not_lost():
    """One side of a same-text pair without a vector (a refused or timed-out embed) must read
    as not measurable, never as a lost ordering."""
    sem, ske = sample.VECTOR_LAYERS

    def pair(group, raw_sem, raw_ske, score_ske):
        return sample.VectorPair(
            "a",
            "b",
            group,
            group,
            raw={sem: raw_sem, ske: raw_ske},
            score={sem: 0.0, ske: score_ske},
        )

    good = [pair("same text", 0.9, 0.95, 0.9), pair("same story", 0.6, 0.7, 0.3)]
    good.append(pair("unrelated", 0.2, 0.3, 0.0))
    assert all(v is True for v in sample.narrative_checks(good).values())
    missing = [pair("same text", None, None, None), good[1], good[2]]
    assert all(v is None for v in sample.narrative_checks(missing).values())


def test_skeleton_closer_than_text_is_a_strict_check():
    """Closer on the masked skeleton means strictly closer: a story pair whose skeleton cosine
    sits a hair below the semantic one is a lost ordering, not a tolerated one."""
    sem, ske = sample.VECTOR_LAYERS

    def pair(group, raw_sem, raw_ske, score_ske):
        return sample.VectorPair(
            "a",
            "b",
            group,
            group,
            raw={sem: raw_sem, ske: raw_ske},
            score={sem: 0.0, ske: score_ske},
        )

    pairs = [
        pair("same text", 0.9, 0.95, 0.9),
        pair("same story", 0.70, 0.69, 0.3),
        pair("unrelated", 0.2, 0.3, 0.0),
    ]
    checks = sample.narrative_checks(pairs)
    assert checks["skeleton_above_text"] is False
    assert checks["story_above_unrelated_raw"] is True
    pairs[1] = pair("same story", 0.70, 0.70, 0.3)
    assert sample.narrative_checks(pairs)["skeleton_above_text"] is False
    pairs[1] = pair("same story", 0.70, 0.71, 0.3)
    assert sample.narrative_checks(pairs)["skeleton_above_text"] is True


def test_chunks_embedded_before_a_failure_count_as_calls(settings):
    """A multi-chunk text that fails on a later chunk still made (and cached) the earlier
    calls; the run record must count them, or "served from the cache" skews low."""

    class FailsOnSecondCall(FakeGemini):
        def _embed(self, model, contents, config):
            if self.calls == 1:
                self.calls += 1
                raise TimeoutError()
            return super()._embed(model, contents, config)

    client = FailsOnSecondCall()
    emb = GatewayEmbedder(settings.embedding_model, settings=settings, client=client)
    two_chunks = "zamach na kuriera " * 1500  # ~27,000 chars: two chunks of 7,000 tokens
    assert emb.embed([two_chunks]) == [None]
    assert emb.failures == {"timeout": 1} and client.calls == 2
    assert emb.chunks == 1  # the first chunk was a real call...
    assert sample.cache_entries(settings) == 1  # ...and is in the cache
    # so the report's arithmetic reads 0 served from the cache, not a clamped shortfall
    assert max(emb.chunks - sample.cache_entries(settings), 0) == 0


def test_active_model_is_the_installed_embedder():
    set_embedder(FakeEmbedder())
    try:
        s = _fingerprinted(store_from_fixtures())
        cal = get_calibration("text.semantic")
        assert cal and cal.model == FakeEmbedder.model and cal.stale == 0
        s.close()
    finally:
        set_embedder(None)
        weights.reset()


def test_mask_removes_inflected_first_names_and_bylines():
    text = (
        "Zamach na Rafała Brzoskę. oprac. Natalia Pacholczyk. Samochód Moniki Olejnik. "
        "Tomasza Lisa wyprowadzono w kajdankach."
    )
    m = mask(text)
    for leak in ("Rafał", "Brzosk", "Natalia", "Pacholczyk", "Monik", "Olejnik", "Tomasz", "Lis"):
        assert leak not in m, (leak, m)
    assert m.count("[CELEBRITY]") == 3 and "oprac. [PERSON]" in m
    # an agency credit and the sentence after it are story, not cast; so is a given name Lisa
    assert mask("fot. PAP Zamach na biznesmena.") == "fot. PAP Zamach na biznesmena."
    assert mask("oprac. Jan. Zamach trwa.") == "oprac. [PERSON]. Zamach trwa."
    assert "[CELEBRITY]" not in mask("Lisa z Krakowa schudła.")


def test_mask_catches_capitalised_bylines_and_stops_at_a_line_break():
    # the sample renders its credits capitalised, at the start of a line
    m = mask("Fot. Jan Kowalski. Autor: Anna Nowak\nTekst: Piotr Zieliński\nAutorka: Ewa Lis-Kot")
    for leak in ("Kowalski", "Nowak", "Zieliński", "Lis-Kot"):
        assert leak not in m, (leak, m)
    assert m.count("[PERSON]") == 4
    assert mask("Fot. PAP Zamach na biznesmena.") == "Fot. PAP Zamach na biznesmena."
    # a line break ends the byline: the next line's first word is story, not the author
    # (tested on the pattern, since mask() collapses whitespace on its way out)
    assert _BYLINE.sub(r"\1 [PERSON]", "oprac. Natalia\nPoseł zapytał") == (
        "oprac. [PERSON]\nPoseł zapytał"
    )
    assert _BYLINE.sub(r"\1 [PERSON]", "oprac. Natalia Pacholczyk\nPoseł") == (
        "oprac. [PERSON]\nPoseł"
    )
    # a keyword with no name on its line is not a byline: the next line is left alone
    assert mask("Fot.\nPremier zapytał") == "Fot. Premier zapytał"
    assert mask("Autor:\nPoseł zapytał") == "Autor: Poseł zapytał"


def test_mask_covers_the_inflections_the_sample_uses():
    m = mask("Słowa pana Rafała o uwolnieniu Tomasza. Rozmowa z Brzoską. Rafałowi i Tomaszem.")
    for leak in ("Rafał", "Tomasz", "Brzosk"):
        assert leak not in m, (leak, m)
    assert m.count("[CELEBRITY]") == 5


@pytest.mark.skipif(not Path("testing_sample").is_dir(), reason="testing_sample/ not present")
def test_gateway_report_runs_every_part_on_the_injected_embedder(settings):
    """The whole report, probes included, embeds with the gateway embedder: the run record
    names the model and the cache, and no vector in it comes from the hashed-word embedder."""
    client = FakeGemini()
    emb = GatewayEmbedder(settings.embedding_model, settings=settings, client=client)
    text = sample.validation_report(
        Path("testing_sample"), parts=("ablate",), model="gateway", embedder=emb
    )
    assert "with gateway embeddings (HAC-32)" in text
    assert "- Execution mode: gateway embeddings: model `embed-test`" in text
    assert "- Embedding cache: cold: 0 chunk entries before the run" in text
    assert "| `narrative.skeleton` | `embed-test` | calibrated |" in text
    assert (
        "fake-stem-256" not in text
        and "hashed-word" not in text.split("## Run record")[1].split("## Reference")[0]
    )
    # the ablation runs after the baseline reused the cache: no new client call, more chunk uses
    assert emb.chunks > client.calls > 0
    assert sample.cache_entries(settings) == client.calls
    # the cache is counted where this embedder wrote it, not in the environment's data dir
    cache_line = next(line for line in text.splitlines() if line.startswith("- Embedding cache:"))
    assert f"{client.calls} written by it" in cache_line
    assert str(settings.model_cache_dir / "embed") in cache_line
