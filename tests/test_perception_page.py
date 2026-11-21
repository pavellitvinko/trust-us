"""Page reading tests. Fake client only: nothing here touches the network."""

import json
import re
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from ftf.config import Settings
from ftf.contracts import Incident, Observation, Purpose, Source, State, View, ViewKind
from ftf.perception.page import (
    CLEAN_TEXT_NAMESPACE,
    NAME,
    NAMESPACES,
    READ_TEXT_NAMESPACE,
    STORY_PARTS,
    find_span,
    html_to_text,
    name_is_supported,
    numbers,
    organisation_is_supported,
    run_perceive_pages,
)
from ftf.registry import Context, extractors, load_plugins

ARTICLE = """<html><head><title>x</title><style>.a{}</style></head><body>
<div id="google_translate_element">Wybierz język</div>
<h1>Rafał Brzoska ujawnia sekret</h1>
<p>Rafała Brzoskę zapytano o platformę. Komisja KNF ostrzega.</p>
<p style="display:none">ukryty tekst</p>
<p>Wpłać 1 150 zł i odbierz 1 800 zł. Konsultantka Sara czeka.</p>
<script>var hidden = "skrypt";</script></body></html>"""


def spanned(value, quote):
    return {"value": value, "quote": quote}


def entity(name, quote, entity_type="person", role="endorser", prominence="protagonist"):
    return {
        "name": name,
        "entity_type": entity_type,
        "role": role,
        "prominence": prominence,
        "quote": quote,
    }


def reading(**fields):
    body = {
        "kind": None,
        "topic": None,
        "keywords": [],
        "entities": [],
        "story": dict.fromkeys(STORY_PARTS),
        "numeric_claims": [],
    }
    return {**body, **fields}


def message(body):
    part = NS(type="output_text", text=json.dumps(body))
    return NS(status="completed", output=[NS(type="message", phase=None, content=[part])])


class FakeOpenAI:
    def __init__(self, *responses):
        self.queue = list(responses)
        self.requests = []
        self.responses = NS(stream=self._stream)

    @contextmanager
    def _stream(self, **request):
        self.requests.append(request)
        yield NS(get_final_response=lambda: self.queue.pop(0))


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path / "data",
        openai_api_key="",
        gemini_api_key="",
        openai_model="openai-test",
        embedding_model="embed-test",
        video_model="video-test",
    )


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "incident"
    path.mkdir()
    (path / "page.html").write_text(ARTICLE, encoding="utf-8")
    return path


@pytest.fixture
def ctx(store, root):
    incident = Incident(incident_id="i1", root=str(root))
    view = View(view_id="v1", incident_id="i1", kind=ViewKind.PAGE, label="p", primary="page.html")
    store.put_incident(incident, [], [view])
    return Context(store)


def stored(store, namespace):
    return [o for o in store.observations("v1") if o.namespace == namespace]


def items(store):
    """Everything read from the page, without the stored copy of its text."""
    return [o for o in store.observations("v1") if o.namespace != READ_TEXT_NAMESPACE]


def test_reading_becomes_observations_with_spans(ctx, settings):
    story = dict.fromkeys(STORY_PARTS)
    story["revealed_secret"] = spanned("A platform", "ujawnia sekret")
    client = FakeOpenAI(
        message(
            reading(
                kind=spanned("Fake News Article", "Rafał Brzoska ujawnia"),
                keywords=[spanned("platforma", "platformę")],
                entities=[
                    entity("Rafał Brzoska", "Rafała Brzoskę"),
                    entity(
                        "KNF",
                        "Komisja KNF",
                        entity_type="organisation",
                        role="regulator_invoked",
                        prominence="mentioned",
                    ),
                    entity("Sara", "Konsultantka Sara", role="persona", prominence="supporting"),
                ],
                story=story,
                numeric_claims=[
                    {"kind": "entry_amount", "value": "1 150", "unit": "zł", "quote": "1 150 zł"}
                ],
            )
        )
    )
    summary = run_perceive_pages(ctx, settings=settings, client=client)
    assert summary["read"] == 1 and summary["not_observable"] == 0

    person = stored(ctx.store, "page.entity.person")[0]
    assert person.signal_id == "G1"
    assert person.value == {"name": "rafał brzoska", "role": "endorser"}
    assert stored(ctx.store, "page.entity.prominence")[0].value == {
        "name": "rafał brzoska",
        "role": "endorser",
        "prominence": "protagonist",
    }
    assert person.raw == "Rafała Brzoskę"
    assert person.source.file == "page.html"
    start, end = map(
        int, re.fullmatch(r"page\.read_text:span\[(\d+),(\d+)\]", person.source.locator).groups()
    )
    assert stored(ctx.store, READ_TEXT_NAMESPACE)[0].value[start:end] == person.raw
    assert stored(ctx.store, "page.entity.organisation")[0].signal_id == "G3"
    assert stored(ctx.store, "page.entity.persona")[0].signal_id == "G5"
    assert stored(ctx.store, "page.kind")[0].value == "fake news article"
    assert stored(ctx.store, "page.story.revealed_secret")[0].signal_id == "F4"
    claim = stored(ctx.store, "page.claim.entry_amount")[0]
    assert claim.signal_id == "F7" and claim.value == {"value": "1150", "unit": "zł"}


def test_supporting_endorser_is_distinct_from_protagonist(ctx, settings):
    client = FakeOpenAI(
        message(
            reading(entities=[entity("Rafał Brzoska", "Rafała Brzoskę", prominence="supporting")])
        )
    )
    run_perceive_pages(ctx, settings=settings, client=client)
    value = stored(ctx.store, "page.entity.prominence")[0].value
    assert value["role"] == "endorser" and value["prominence"] == "supporting"


def test_same_person_and_role_match_across_prominence(store, ctx, settings):
    client = FakeOpenAI(message(reading(entities=[entity("Rafał Brzoska", "Rafała Brzoskę")])))
    run_perceive_pages(ctx, settings=settings, client=client)
    cast = {"name": "rafał brzoska", "role": "endorser"}
    assert store.views_with_value("page.entity.person", cast) == ["v1"]


def test_item_whose_quote_is_not_in_the_text_is_dropped(ctx, settings):
    client = FakeOpenAI(
        message(
            reading(
                kind=spanned("news", "tego zdania nie ma na stronie"),
                entities=[entity("Jan Kowalski", "Jan Kowalski")],
            )
        )
    )
    run_perceive_pages(ctx, settings=settings, client=client)
    assert items(ctx.store) == []


def test_person_name_unsupported_by_its_quote_is_dropped(ctx, settings):
    client = FakeOpenAI(message(reading(entities=[entity("Jan Kowalski", "Rafała Brzoskę")])))
    run_perceive_pages(ctx, settings=settings, client=client)
    assert stored(ctx.store, "page.entity.person") == []


def test_number_not_in_its_quote_is_dropped(ctx, settings):
    claim = {"kind": "price", "value": "999", "unit": "zł", "quote": "1 150 zł"}
    client = FakeOpenAI(message(reading(numeric_claims=[claim])))
    run_perceive_pages(ctx, settings=settings, client=client)
    assert stored(ctx.store, "page.claim.price") == []


def test_part_of_a_larger_number_is_dropped(ctx, settings):
    claim = {"kind": "price", "value": "150", "unit": "zł", "quote": "1 150 zł"}
    client = FakeOpenAI(message(reading(numeric_claims=[claim])))
    run_perceive_pages(ctx, settings=settings, client=client)
    assert stored(ctx.store, "page.claim.price") == []


def test_organisation_unsupported_by_its_quote_is_dropped(ctx, settings):
    knf = entity("KNF", "Wpłać 1 150 zł", entity_type="organisation", role="regulator_invoked")
    client = FakeOpenAI(message(reading(entities=[knf])))
    run_perceive_pages(ctx, settings=settings, client=client)
    assert stored(ctx.store, "page.entity.organisation") == []


def test_clean_text_observation_is_preferred(ctx, settings):
    observation = Observation(
        view_id="v1",
        signal_id="F1",
        namespace=CLEAN_TEXT_NAMESPACE,
        value="Czysty tekst artykułu",
        source=Source(file="page.html", locator="html:body"),
        extractor="page.text",
        version="1",
    )
    ctx.store.put_observations("v1", "page.text", [observation])
    client = FakeOpenAI(message(reading()))
    run_perceive_pages(ctx, settings=settings, client=client)
    assert client.requests[0]["input"][0]["content"] == "Czysty tekst artykułu"


def test_markup_facts_are_sent_before_the_text(ctx, settings):
    def fact(namespace, value, purpose=Purpose.RISK):
        return Observation(
            view_id="v1",
            signal_id="F7",
            namespace=namespace,
            value=value,
            purpose=purpose,
            source=Source(file="page.html", locator="html"),
            extractor="page.structure",
            version="1",
        )

    facts = [fact("markup.countdown", "timer"), fact("markup.form_field", "tel:phone")]
    facts.append(fact("dom.class_set", ["a", "b"], Purpose.LINKAGE))  # not a fact
    ctx.store.put_observations("v1", "page.structure", facts)
    client = FakeOpenAI(message(reading(kind=spanned("news", "ujawnia sekret"))))
    run_perceive_pages(ctx, settings=settings, client=client)
    sent = client.requests[0]["input"][0]["content"]
    assert sent.startswith(
        "Facts found in the page markup (not page text):\n"
        "- markup.countdown: timer\n- markup.form_field: tel:phone\n\nPage text:\n"
    )
    assert "dom.class_set" not in sent
    read = stored(ctx.store, READ_TEXT_NAMESPACE)[0].value
    assert not read.startswith("Facts")  # spans index the page text, not the message
    kind = stored(ctx.store, "page.kind")[0]
    start, end = map(int, kind.source.locator.split("[")[1].rstrip("]").split(","))
    assert read[start:end] == "ujawnia sekret"


def test_fallback_sends_only_visible_text(ctx, settings):
    client = FakeOpenAI(message(reading()))
    run_perceive_pages(ctx, settings=settings, client=client)
    sent = client.requests[0]["input"][0]["content"]
    assert "Rafał Brzoska ujawnia sekret" in sent
    for hidden in ("Wybierz język", "ukryty tekst", "skrypt", ".a{}"):
        assert hidden not in sent


def test_second_run_uses_the_cache(ctx, settings):
    client = FakeOpenAI(message(reading(kind=spanned("news", "ujawnia sekret"))))
    run_perceive_pages(ctx, settings=settings, client=client)
    run_perceive_pages(ctx, settings=settings, client=client)
    assert len(client.requests) == 1
    assert len(items(ctx.store)) == 1


def test_refusal_is_not_observable_for_every_namespace(ctx, settings):
    refusal = NS(
        status="completed",
        output=[NS(type="message", phase=None, content=[NS(type="refusal", refusal="no")])],
    )
    summary = run_perceive_pages(ctx, settings=settings, client=FakeOpenAI(refusal))
    observations = ctx.store.observations("v1")
    assert summary["not_observable"] == 1
    assert len(observations) == len(NAMESPACES)
    assert all(o.state is State.NOT_OBSERVABLE and o.value is None for o in observations)
    assert {o.raw for o in observations} == {"refusal"}


def test_transient_failure_keeps_the_earlier_reading(ctx, settings):
    client = FakeOpenAI(message(reading(kind=spanned("news", "ujawnia sekret"))))
    run_perceive_pages(ctx, settings=settings, client=client)
    changed = replace(settings, openai_model="another-model")  # forces a cache miss, no key
    summary = run_perceive_pages(ctx, settings=changed)
    assert summary["kept"] == 1
    assert [o.value for o in items(ctx.store)] == ["news"]


def test_transient_failure_keeps_a_valid_empty_reading(ctx, settings):
    run_perceive_pages(ctx, settings=settings, client=FakeOpenAI(message(reading())))
    changed = replace(settings, openai_model="another-model")  # forces a cache miss, no key
    summary = run_perceive_pages(ctx, settings=changed)
    assert summary["kept"] == 1
    assert {o.state for o in ctx.store.observations("v1")} == {State.PRESENT}


def test_unreadable_file_keeps_the_earlier_reading(ctx, settings, root):
    client = FakeOpenAI(message(reading(kind=spanned("news", "ujawnia sekret"))))
    run_perceive_pages(ctx, settings=settings, client=client)
    (root / "page.html").unlink()
    summary = run_perceive_pages(ctx, settings=settings, client=client)
    assert summary["kept"] == 1
    assert [o.value for o in items(ctx.store)] == ["news"]


def test_page_without_text_makes_no_request(ctx, settings, root):
    (root / "page.html").write_text("<html><body><script>x</script></body></html>")
    client = FakeOpenAI()
    run_perceive_pages(ctx, settings=settings, client=client)
    assert client.requests == []
    assert {o.raw for o in ctx.store.observations("v1")} == {"no_text"}


def test_secret_in_page_text_reaches_neither_model_nor_cache(ctx, settings, root):
    (root / "page.html").write_text("<p>Link https://x.pl/?token=abc123secret tutaj</p>")
    client = FakeOpenAI(message(reading(kind=spanned("news", "Link"))))
    run_perceive_pages(ctx, settings=settings, client=client)
    assert "abc123secret" not in client.requests[0]["input"][0]["content"]
    cached = "".join(p.read_text() for p in settings.model_cache_dir.rglob("*.json"))
    assert cached and "abc123secret" not in cached


def test_perception_is_not_a_pipeline_extractor():
    load_plugins()
    assert NAME not in extractors.names()


def test_html_to_text_handles_nested_hidden_blocks():
    html = '<div hidden><div>a</div><div>b</div></div><p>widoczny</p><img src="x"><p>dalej</p>'
    assert html_to_text(html) == "widoczny\ndalej"


def test_html_to_text_survives_unclosed_head_and_hidden_paragraph():
    html = "<head><title>Tytuł</title><body><p hidden>ukryty<p>widoczny</p>"
    assert html_to_text(html) == "Tytuł\nwidoczny"


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("<ul><li hidden>a<ul><li>ukryty</li></ul></li><li>widoczny</li></ul>", "widoczny"),
        ("<div hidden/>ukryty</div><p>widoczny</p>", "widoczny"),
        ("<ul><li hidden>x</ul><p>Oferta</p>", "Oferta"),
        ("<p hidden>x<div>Oferta</div>", "Oferta"),
        ("<div><p hidden>x</div>Oferta", "Oferta"),
        ("<svg/><p>widoczny</p>", "widoczny"),
    ],
)
def test_html_to_text_follows_implied_closing(html, expected):
    assert html_to_text(html) == expected


@pytest.mark.parametrize(
    ("quote", "found"),
    [("RAFAŁ   brzoska", True), ("nie ma", False), ("...", False), ("", False)],
)
def test_find_span(quote, found):
    assert (find_span("Rafał\nBrzoska mówi", quote) is not None) is found


@pytest.mark.parametrize(
    ("name", "quote", "expected"),
    [
        ("Rafał Brzoska", "Rafała Brzoskę", True),
        ("Tomasz Lis", "Tomaszem Lisem", True),
        ("Rafał Brzoska", "Rafała", False),
        ("Jan Kowalski", "Rafała Brzoskę", False),
        ("Ewa Kowalska", "Ewy Kowalskiej", True),
        ("Marek Testowy", "Marka Testowego", True),
        ("Paweł Nowak", "Pawła Nowaka", True),
        ("Michał Lis", "Michale Lisie", True),
        ("Rafał Brzoska", "Rafale Brzosce", True),
        ("Jan Kowalski", "Janusz Kowalczyk", False),
        ("Rafał Brzoska", "Brzoska Rafał", False),
    ],
)
def test_name_is_supported(name, quote, expected):
    assert name_is_supported(name, quote) is expected


@pytest.mark.parametrize(
    ("name", "quote", "expected"),
    [
        ("KNF", "Komisja KNF", True),
        ("KNF", "Komisji Nadzoru Finansowego", True),
        ("Komisja Nadzoru Finansowego", "ostrzega KNF", True),
        ("Komisja Nadzoru Finansowego", "Komisji Nadzoru Finansowego", True),
        ("KNF", "Wpłać 1 150 zł", False),
        ("Bank Fikcyjny", "Komisja KNF", False),
    ],
)
def test_organisation_is_supported(name, quote, expected):
    assert organisation_is_supported(name, quote) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1 150 zł", ["1150"]),
        ("1.150 zł", ["1150"]),
        ("1,150 zł", ["1150"]),
        ("1 150,50 zł", ["1150.5"]),
        ("1.150,00 zł", ["1150"]),
        ("1,150.00 zł", ["1150"]),
        ("2,5%", ["2.5"]),
        ("0.125", ["0.125"]),
        ("1 000 000", ["1000000"]),
        ("w 7 dni 1 800 zł", ["7", "1800"]),
        ("siedem", []),
    ],
)
def test_numbers(text, expected):
    assert numbers(text) == expected
