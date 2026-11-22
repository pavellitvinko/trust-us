"""Library IDs read from a screenshot's OCR: only an explicitly labelled one counts."""

import pytest

from ftf.contracts import Observation, Purpose, Source, State
from ftf.perception.ad_library_id import find_library_ids, read_library_id

BOX = "image:region[0.1,0.2,0.6,0.03]"


def ocr(raw, locator=BOX, namespace="image.text", state=State.PRESENT):
    return Observation(
        view_id="v",
        signal_id="E11",
        namespace=namespace,
        value=" ".join(raw.split()).casefold(),
        raw=raw,
        state=state,
        purpose=Purpose.BOTH,
        source=Source(file="IMG_3632.webp", locator=locator),
        extractor="perception.image",
        version="2",
    )


@pytest.mark.parametrize(
    "text",
    [
        "Identyfikator biblioteki: 2186769435230928",  # the Sustain Studio screenshot
        "identyfikator biblioteki 2186769435230928",
        "Identyfikator w bibliotece reklam: 2186769435230928",
        "Library ID: 2186769435230928",
        "Ad Library ID 2186769435230928",
        "Library ID: 2186 7694 3523 0928",  # OCR split the digits
        "Aktywna Identyfikator biblioteki: 2186769435230928 Data zamieszczenia: 11 sie 2026",
    ],
)
def test_a_labelled_library_id_is_read(text):
    found = read_library_id([ocr(text)])
    assert found is not None
    assert found.value == "2186769435230928"
    assert found.evidence[0].raw == text
    assert found.evidence[0].locator == BOX
    assert found.evidence[0].file == "IMG_3632.webp"


def test_an_english_label_with_another_id():
    assert read_library_id([ocr("Library ID: 4414624315417273")]).value == "4414624315417273"


def test_the_label_and_the_number_on_separate_lines():
    lines = [ocr("Sustain Studio"), ocr("Identyfikator biblioteki:"), ocr("2186769435230928")]
    found = read_library_id(lines)
    assert found.value == "2186769435230928"
    assert found.evidence[0].raw == "Identyfikator biblioteki: 2186769435230928"


def test_the_same_id_read_twice_is_one_id_with_both_places():
    lines = [
        ocr("Identyfikator biblioteki: 2186769435230928", "image:region[0.08,0.29,0.6,0.02]"),
        ocr("Identyfikator biblioteki: 2186769435230928", "image:region[0.24,0.48,0.3,0.03]"),
        ocr("Identyfikator biblioteki: 2186769435230928", "image:region[0.24,0.48,0.3,0.03]"),
    ]
    found = read_library_id(lines)
    assert found.value == "2186769435230928"
    assert [e.locator for e in found.evidence] == [
        "image:region[0.08,0.29,0.6,0.02]",
        "image:region[0.24,0.48,0.3,0.03]",
    ]


@pytest.mark.parametrize(
    "text",
    [
        "2186769435230928",  # a number without a label
        "Liczba reklam wykorzystujących ten materiał reklamowy i tekst: 5",
        "Data zamieszczenia: 11 sie 2026 · Łączny czas aktywności: 8 godz.",
        "Ad ID: 120200000000000000",  # an Ads Manager ad ID
        "Campaign ID: 2186769435230928",
        "Identyfikator reklamy: 2186769435230928",
        "Identyfikator kampanii: 2186769435230928",
        "https://shop.example/?ad_id=120212345678901234",
        "tel. +48 600 700 800",
        "1 wyświetlenie",
    ],
)
def test_unrelated_numbers_are_not_library_ids(text):
    assert read_library_id([ocr(text)]) is None


@pytest.mark.parametrize(
    "text",
    [
        "Identyfikator biblioteki: 21867694352309O8",  # a letter O for a zero
        "Library ID: 2186769435230928x",
        "Library ID: 123456",  # too short
        "Library ID: 218676943523092812345",  # too long
        "Library ID:",
    ],
)
def test_malformed_ids_are_rejected(text):
    assert read_library_id([ocr(text)]) is None


def test_a_label_followed_by_a_line_that_is_not_only_a_number():
    lines = [ocr("Library ID:"), ocr("2186769435230928 Sponsored")]
    assert read_library_id(lines) is None


def test_two_different_ids_conflict():
    lines = [ocr("Library ID: 2186769435230928"), ocr("Library ID: 4414624315417273")]
    assert read_library_id(lines) is None
    assert set(find_library_ids(lines)) == {"2186769435230928", "4414624315417273"}


def test_only_present_image_text_is_read():
    assert read_library_id([ocr("Library ID: 2186769435230928", namespace="image.cta")]) is None
    gone = ocr("Library ID: 2186769435230928", state=State.NOT_OBSERVABLE)
    assert read_library_id([gone]) is None
