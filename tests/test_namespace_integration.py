"""The Method layers consume current extractor shapes, with legacy fallbacks."""

import pytest

from ftf.contracts import Observation, Purpose, State, View, ViewKind
from ftf.layers._embedder import FakeEmbedder, set_embedder
from ftf.layers._text import body_text
from ftf.registry import layers, load_plugins


def observation(namespace, value, **kwargs):
    return Observation(
        view_id="page",
        namespace=namespace,
        value=value,
        signal_id="F1",
        extractor="test",
        version="1",
        **kwargs,
    )


def test_body_layers_prefer_article_to_full_page_and_legacy_text():
    load_plugins()
    view = View(view_id="page", incident_id="1", kind=ViewKind.PAGE, label="page")
    body = " ".join(f"word{i}" for i in range(40))
    canonical = [observation("page.body_text", body)]
    polluted = [
        observation("page.clean_text", "menu footer comments " * 100),
        observation("text.body", "older body " * 40),
        *canonical,
    ]
    set_embedder(FakeEmbedder())
    try:
        for name in ("text.shingle", "text.semantic", "narrative.skeleton"):
            layer = layers.get(name)
            expected = layer.build(view, canonical)
            assert expected is not None
            assert layer.build(view, polluted) == expected
        assert body_text([observation("page.body_text", ""), *polluted[:2]]) == ""
        assert body_text([observation("text.body", body), polluted[0]]) == body
        assert body_text([polluted[0]]) == polluted[0].value.strip()
    finally:
        set_embedder(None)


@pytest.mark.parametrize("invalid", [{"purpose": Purpose.RISK}, {"state": State.ABSENT}])
def test_dom_list_expands_to_classes_without_admitting_risk_or_missing_values(invalid):
    load_plugins()
    view = View(view_id="page", incident_id="1", kind=ViewKind.PAGE, label="page")
    layer = layers.get("kit.dom")
    classes = ["article-special", "custom-heading", "offer-card"]
    expected = layer.build(view, [observation("dom.class", v) for v in classes])
    actual = layer.build(
        view,
        [
            observation(
                "dom.class_set", classes + ["btn-primary", "goog-test", "", None, 1, classes[0]]
            )
        ],
    )
    assert expected is not None and actual == expected
    assert layer.build(view, [observation("dom.class_set", classes, **invalid)]) is None


def test_code_layer_consumes_structural_signals_at_kit_weights():
    from ftf.compare.weights import BASE_WEIGHTS

    load_plugins()
    view = View(view_id="page", incident_id="1", kind=ViewKind.PAGE, label="page")
    fp = layers.get("kit.code").build(
        view,
        [
            observation("dom.comment_lang", "ru", purpose=Purpose.BOTH),
            observation("dom.evasion_filter", "hue-rotate", purpose=Purpose.BOTH),
        ],
    )
    assert fp is not None
    assert {i["ns"] for i in fp.payload} == {"dom.comment_lang", "dom.evasion_filter"}
    assert BASE_WEIGHTS["dom.comment_lang"] == BASE_WEIGHTS["code.comment_lang"]
    assert BASE_WEIGHTS["dom.evasion_filter"] == BASE_WEIGHTS["code.evasion_filter"]
