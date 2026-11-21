"""HAC-12 clean text and structure on synthetic pages."""

import pytest

from ftf.contracts import Purpose
from ftf.extract import _material
from ftf.extract._dom import parse
from ftf.extract._lang import detect
from ftf.extract.page_structure import PageStructure, comment_langs, evasion_filters
from ftf.extract.page_text import PageText, primary_subtag

from .test_extract_identifiers import page, run

PL = (
    "Wczoraj wieczorem na Alejach Jerozolimskich w Warszawie motocyklista oddał serię strzałów "
    "w kierunku samochodu. Policja zabezpieczyła monitoring i nie ujawnia, kto jest sprawcą, "
    "ale już wiadomo, że to był zamach na założyciela firmy oraz jego rodzinę."
)
EN = (
    "Yesterday evening a motorcyclist fired a series of shots at the car on the main avenue. "
    "The police have secured the footage and they will not say who was behind it, but it is "
    "clear that this was an attack on the founder of the company and his family."
)


@pytest.fixture(autouse=True)
def _fresh_cache():
    _material.clear_cache()
    parse.cache_clear()


def values(observations, namespace):
    return [o.value for o in observations if o.namespace == namespace]


# --- language ----------------------------------------------------------------------------


def test_detect_languages():
    assert detect(PL) == "pl"
    assert detect(EN) == "en"
    assert detect("Обязательные поля", min_words=2) == "ru"
    assert detect("Обов'язкові поля для заповнення, їх треба ввести", min_words=2) == "uk"
    assert detect("Wczoraj wieczorem na Alejach Jerozolimskich") is None  # too short


def test_detect_polish_page_with_english_ui_strings():
    text = f"{PL}\nWould you like to continue?\nThe offer is not relevant to me"
    assert detect(text) == "pl"


def test_primary_subtag():
    assert primary_subtag("pl-PL") == "pl"
    assert primary_subtag(" EN ") == "en"
    assert primary_subtag("") is None
    assert primary_subtag("x-klingon-1") is None


# --- one DOM pass ------------------------------------------------------------------------


def test_translate_widget_is_stripped():
    dom = parse(
        '<html class="translated-ltr"><body><p>Treść strony</p>'
        '<div class="skiptranslate goog-te-gadget"><select class="goog-te-combo">'
        "<option>polski</option><option>kazachski</option></select></div>"
        '<div id="goog-gt-tt" class="VIpgJd-yAWNEb-L7lbkb"><span>Tekst oryginalny</span>'
        '<span class="inner">Oceń to tłumaczenie</span></div></body></html>'
    )
    assert dom.text() == "Treść strony"
    assert not {c for c in dom.classes if "VIpgJd" in c or "goog" in c or "translate" in c}
    assert "inner" not in dom.classes  # classes inside the widget are not the page's
    assert dom.fields == ()


def test_extension_markup_is_stripped():
    dom = parse(
        "<body><p>Treść</p><grammarly-desktop-integration>Grammarly</grammarly-desktop-integration>"
        '<div data-lastpass-root="">LastPass menu</div>'
        '<img src="chrome-extension://abc/icon.png"></body>'
    )
    assert dom.text() == "Treść"


def test_zones():
    dom = parse(
        "<body><nav><a>Wiadomości</a> <a>Sport</a></nav>"
        "<article><header><h1>Tytuł artykułu</h1></header><p>Treść artykułu.</p>"
        '<div class="fb-comment-bubble">Super, polecam!</div></article>'
        '<div class="footer-links">Kontakt</div><div class="fb-section">Dalej treść</div></body>'
    )
    zones = {b.text: b.zone for b in dom.blocks}
    assert zones["Wiadomości Sport"] == "chrome"
    assert zones["Tytuł artykułu"] == "body"  # an article's own header is not chrome
    assert zones["Treść artykułu."] == "body"
    assert zones["Super, polecam!"] == "comments"
    assert zones["Kontakt"] == "chrome"
    assert zones["Dalej treść"] == "body"
    assert dom.h1 == ("Tytuł artykułu",)


def test_header_wrapping_the_whole_lander_is_not_chrome():
    dom = parse('<body><header class="header-all"><h1>Nagłówek</h1><p>Treść</p></header></body>')
    assert {b.zone for b in dom.blocks} == {"body"}


def test_hidden_text_and_selects_are_not_visible_text():
    dom = parse(
        '<body><p>Widać</p><p style="display: none">Ukryte</p><div hidden>Też ukryte</div>'
        '<select name="country"><option>Poland</option></select><script>var x=1</script></body>'
    )
    assert dom.text() == "Widać"
    assert ("select", "country") in dom.fields


def test_root_filter_and_framework_variables():
    dom = parse(
        '<html lang="pl" style="filter: hue-rotate(7deg);"><head><style>'
        ".filter{filter:var(--tw-blur) var(--tw-hue-rotate)} html{font-family:Inter}"
        "</style></head><body></body></html>"
    )
    assert dom.html_style == "filter: hue-rotate(7deg);"
    assert dom.root_css == ("html{font-family:Inter}",)
    assert evasion_filters(dom) == [("hue-rotate", "filter: hue-rotate(7deg)")]
    tailwind = parse(
        "<html><head><style>:root{--tw-hue-rotate: ;} body{filter:var(--tw-hue-rotate)}"
        "</style></head></html>"
    )
    assert evasion_filters(tailwind) == []


def test_struck_countdown_and_comments():
    dom = parse(
        "<!-- saved from url=(0020)https://example.org/ -->"
        "<html><body><!-- Обязательные поля --><!-- $ --><!--[if IE]>x<![endif]-->"
        '<p>Cena: <del>198 zł</del> 99 zł</p><span style="text-decoration: line-through">'
        '300 PLN</span><div id="countdown-box" class="timer"></div></body></html>'
    )
    assert dom.struck == ("198 zł", "300 PLN")
    assert set(dom.countdown) == {"countdown-box", "timer"}
    assert dom.comments == ("Обязательные поля",)
    assert comment_langs(dom.comments) == ["ru"]


def test_commented_out_markup_does_not_pick_a_language():
    markup = '<div class="subscribe-bar"> <p>Prenumerata <strong>od 1 zł</strong></p> </div>'
    assert comment_langs((markup, "Taboola Pixel Code")) == []


@pytest.mark.parametrize(
    "html",
    [
        "<div><p>bez końca",
        "</div></span><p>tekst</p></div>",
        "<ul><li>a<li>b</ul><p>c<div>d</div>",
        "<<<>>><!-- niedomknięty",
    ],
)
def test_malformed_html_does_not_raise(html):
    parse(html)


# --- extractors --------------------------------------------------------------------------

ARTICLE = f"""
<nav class="main-navigation"><a>Sport.pl</a> <a>Poczta</a></nav>
<article><h1>Zamach na prezesa</h1><p>{PL}</p>
<p>Pozostało miejsc w programie: 185</p>
<div class="commentsApp__comment">Marek: Zarejestrowałem się i działa!</div></article>
<form><input type="text" name="first_name"><input type="tel" name="phone">
<input type="hidden" name="sub1" value="x"></form>
"""


def test_page_text_extractor(store, tmp_path):
    head = (
        '<title>Zamach — Gazeta</title><meta name="description" content="Opis  strony">'
        '<meta property="og:title" content="OG">'
    )
    obs = run(PageText(), store, page(tmp_path / "a", ARTICLE, head=head))
    body = values(obs, "page.body_text")[0]
    assert "Sport.pl" not in body and "Marek:" not in body and PL in body
    assert "Sport.pl" in values(obs, "page.clean_text")[0]
    assert values(obs, "page.comments") == [["Marek: Zarejestrowałem się i działa!"]]
    assert values(obs, "page.title") == ["Zamach — Gazeta"]
    assert values(obs, "page.meta_description") == ["Opis strony"]
    assert values(obs, "page.headline") == ["Zamach na prezesa"]
    assert values(obs, "page.lang_detected") == ["pl"]
    assert values(obs, "page.lang_attr") == []  # the test page has no `lang`
    assert all(o.source.file == "page.html" for o in obs)


def test_lang_mismatch_and_foreign_block(store, tmp_path):
    root = page(tmp_path / "a", f"<p>{PL}</p><p>{EN}</p><p>{PL}</p>")
    html = root / "page.html"
    html.write_text(html.read_text().replace("<html>", '<html lang="en-US">'))
    obs = run(PageText(), store, root)
    assert values(obs, "page.lang_attr") == ["en"]
    assert values(obs, "page.lang_detected") == ["pl"]
    mismatch = [o for o in obs if o.namespace == "page.lang_mismatch"]
    assert [o.value for o in mismatch] == ["en→pl"] and mismatch[0].purpose is Purpose.RISK
    assert values(obs, "page.foreign_lang") == ["en"]


def test_page_structure_extractor(store, tmp_path):
    root = page(tmp_path / "a", ARTICLE + '<s>198 zł</s><div class="countdown"></div>')
    html = root / "page.html"
    html.write_text(html.read_text().replace("<html>", '<html style="filter: hue-rotate(8deg)">'))
    obs = run(PageStructure(), store, root)
    assert values(obs, "dom.evasion_filter") == ["hue-rotate"]
    assert "main-navigation" in values(obs, "dom.class_set")[0]
    assert "html>body>article>h1" in values(obs, "dom.tag_paths")[0]
    assert values(obs, "markup.stock_counter") == ["Pozostało miejsc w programie: <n>"]
    assert values(obs, "markup.crossed_price") == ["198 zł"]
    assert values(obs, "markup.countdown") == ["countdown"]
    assert values(obs, "markup.form_field") == ["text:first_name", "tel:phone"]
    risk = {o.namespace for o in obs if o.purpose is Purpose.RISK}
    assert risk == {o.namespace for o in obs if o.namespace.startswith("markup.")}


def test_no_html_no_observations(store, tmp_path):
    root = tmp_path / "a"
    root.mkdir()
    (root / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    from ftf.ingest import ingest_folder
    from ftf.registry import Context

    _, views = ingest_folder(root, store)
    for view in views:
        assert PageText().run(view, Context(store)) == []
        assert PageStructure().run(view, Context(store)) == []


# --- review fixes (PR 10) -----------------------------------------------------------------


def test_unfilled_macro_keeps_the_page_text(store, tmp_path):
    root = page(tmp_path / "a", f"<p>Gratulacje {{firstname}}, wygrałeś!</p><p>{PL}</p>")
    obs = run(PageText(), store, root)
    assert "{firstname}" in values(obs, "page.clean_text")[0]
    assert PL in values(obs, "page.body_text")[0]


def test_svg_title_is_not_the_page_title():
    dom = parse(
        "<head><title>Zamach — Gazeta</title></head>"
        "<body><svg><title>Facebook</title></svg><p>Treść</p></body>"
    )
    assert dom.title == "Zamach — Gazeta"


@pytest.mark.parametrize("cls", ["single comments-open", "has-sidebar", "menu-open"])
def test_page_wide_class_sets_no_zone(cls):
    dom = parse(f'<body class="{cls}"><p>Treść artykułu</p></body>')
    assert {b.zone for b in dom.blocks} == {"body"}


def test_comments_wrapper_around_the_headline_is_not_comments():
    dom = parse('<body><div class="opinion-page"><h1>Nagłówek</h1><p>Treść</p></div></body>')
    assert {b.zone for b in dom.blocks} == {"body"}


def test_logo_h1_in_header_is_not_the_headline():
    dom = parse(
        '<body><header><h1 class="logo">Gazeta</h1><a href="/">Sport</a></header>'
        "<article><h1>Zamach na prezesa</h1><p>Treść</p></article></body>"
    )
    zones = {b.text: b.zone for b in dom.blocks}
    assert zones["Sport"] == "chrome" and zones["Gazeta"] == "chrome"
    assert dom.h1 == ("Zamach na prezesa", "Gazeta")


def test_backdrop_filter_is_not_evasion():
    assert evasion_filters(parse("<style>body{backdrop-filter:blur(4px)}</style>")) == []
    assert evasion_filters(parse("<style>:root{--x-filter:blur(4px)}</style>")) == []
    assert evasion_filters(parse("<style>body{filter:blur(4px)}</style>"))


def test_honey_in_a_class_is_page_content():
    dom = parse(
        '<body><div class="honey-offer">Miód manuka leczy</div>'
        "<honey-container>Honey coupons</honey-container></body>"
    )
    assert dom.text() == "Miód manuka leczy"
    assert "honey-offer" in dom.classes


def test_hidden_fields_are_not_visible_fields():
    dom = parse(
        '<form><div style="display:none"><input name="pesel"></div>'
        '<input name="token" hidden><template><input name="tpl"></template>'
        '<input name="phone" type="tel"></form>'
    )
    assert dom.fields == (("tel", "phone"),)


def test_italian_e_is_not_french():
    text = "Il prodotto è ottimo, è economico, è veloce, è sicuro, è facile da usare ogni giorno"
    assert detect(text, min_words=8) == "it"
