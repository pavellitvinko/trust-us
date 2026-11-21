"""Dense-vector layers: `text.semantic` (F2) and `narrative.skeleton` (F3).

The skeleton layer masks, by script, every person, organisation, product, amount and date
with a role placeholder before embedding, so the Brzoska, Olejnik and Lis stories land
near each other while `text.semantic`, which keeps the names, still separates them.
Masking uses what the corpus already knows (entity.person, entity.cast, brand.offer
observations) plus a small seed list; the model-written retelling is the upgrade path.
"""

from __future__ import annotations

import re

from ..contracts import Fingerprint, FingerprintKind, Observation, Tier, View
from ..registry import layers
from ._embedder import get_embedder
from ._set_layer import linkage_items

MIN_WORDS = 20

# Seed lists: enough for the sample; the Perception track's entity observations extend them.
CELEBRITIES = (
    "Rafał Brzoska",
    "Brzoska",
    "Brzoskę",
    "Monika Olejnik",
    "Olejnik",
    "Tomasz Lis",
    "Lis",
    "Donald Tusk",
    "Tusk",
    "Mateusz Morawiecki",
    "Morawiecki",
    "Morawieckiego",
    "Anita Werner",
    "Agnieszka Dziemianowicz-Bąk",
    "Dziemianowicz-Bąk",
    "Adam Glapiński",
    "Glapiński",
    "Glapińskiego",
    "Daniel Obajtek",
    "Obajtek",
)
BANKS = ("PKO", "Pekao", "mBank", "ING", "Santander", "Millennium", "Alior", "BNP")
INSTITUTIONS = (
    "NBP",
    "KNF",
    "ZUS",
    "UOKiK",
    "Ministerstwo Finansów",
    "Minister finansów",
    "Sejm",
    "InPost",
    "InPostu",
    "Orlen",
    "Gazeta.pl",
    "Gazety",
    "TVN24",
    "TVN",
    "Polsat",
    "Onet",
)
_ROLE_WORDS = {
    "dr",
    "doktor",
    "lekarz",
    "lekarze",
    "urolog",
    "urolodzy",
    "konsultant",
    "konsultantka",
    "menedżer",
    "dziennikarz",
    "dziennikarka",
    "prezes",
    "minister",
    "rzecznik",
    "prawnik",
}
_CAP_NAME = re.compile(
    r"\b([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)(\s+z\s+[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)?\b"
)
_AMOUNT = re.compile(r"\b\d[\d\s.,]*\s?(zł|złotych|PLN|%|kg|tys\.?|mln|miejsc|opakowań|osób)\b")
_NUMBER = re.compile(r"\b\d+([.,]\d+)?\b")
_DATE = re.compile(
    r"\b(w\s+)?(poniedziałek|wtorek|środ[ęa]|czwartek|piątek|sobot[ęa]|niedziel[ęa])\b|"
    r"\b\d{1,2}\s+(stycznia|lutego|marca|kwietnia|maja|czerwca|lipca|sierpnia|września|"
    r"października|listopada|grudnia)\b|\b\d{1,2}:\d{2}\b",
    re.IGNORECASE,
)


def _replace_all(text: str, names, placeholder: str) -> str:
    for name in sorted(set(names), key=len, reverse=True):
        if name:
            text = re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", placeholder, text)
    return text


def mask(text: str, observations: list[Observation] = ()) -> str:
    """Replace people, organisations, products, amounts and dates with role placeholders."""
    from .playbook import cast_names

    people = [
        v
        for _, v in linkage_items(
            observations, ("entity.person", "ocr.person", "image.person_name")
        )
    ]
    cast = cast_names(observations)
    products = [v for _, v in linkage_items(observations, ("brand.offer", "offer.name"))]
    text = _replace_all(text, products, "[PRODUCT]")
    text = _replace_all(text, list(CELEBRITIES) + people + cast, "[CELEBRITY]")
    text = _replace_all(text, BANKS, "[BANK]")
    text = _replace_all(text, INSTITUTIONS, "[INSTITUTION]")
    text = _DATE.sub("[DATE]", text)
    text = _AMOUNT.sub("[AMOUNT]", text)
    text = _NUMBER.sub("[NUMBER]", text)

    # remaining capitalised first names + "z <City>" (testimonials) -> [PERSON]
    def person(m: re.Match) -> str:
        word = m.group(1)
        if word.lower() in _ROLE_WORDS or word.startswith("["):
            return m.group(0)
        return "[PERSON]" if m.group(2) else m.group(0)

    text = _CAP_NAME.sub(person, text)
    return " ".join(text.split())


TEXT_NAMESPACES = ("text.body", "page.clean_text")


def _body(observations: list[Observation]) -> str:
    return "\n".join(v for _, v in linkage_items(observations, TEXT_NAMESPACES))


def _vector_fingerprint(view: View, layer_id: str, version: str, text: str) -> Fingerprint | None:
    if len(text.split()) < MIN_WORDS:
        return None
    embedder = get_embedder()
    values = embedder.embed([text])[0]
    if values is None:  # the gateway could not embed this text: leave the layer out
        return None
    return Fingerprint(
        view_id=view.view_id,
        layer_id=layer_id,
        kind=FingerprintKind.VECTOR,
        payload={"model": embedder.model, "values": [round(x, 6) for x in values]},
        version=version,
    )


@layers.register
class TextSemantic:
    name = "text.semantic"
    version = "1"
    kind = FingerprintKind.VECTOR
    tier = Tier.ARTIFACT
    group = "semantic"
    comparator = "calibrated_cosine"
    namespaces = TEXT_NAMESPACES

    def build(self, view: View, observations: list[Observation]) -> Fingerprint | None:
        return _vector_fingerprint(view, self.name, self.version, _body(observations))


@layers.register
class NarrativeSkeleton:
    name = "narrative.skeleton"
    version = "1"
    kind = FingerprintKind.VECTOR
    tier = Tier.PLAYBOOK
    group = "narrative"
    comparator = "calibrated_cosine"
    namespaces = TEXT_NAMESPACES + (
        "entity.person",
        "entity.cast",
        "page.entity.person",
        "brand.offer",
    )

    def build(self, view: View, observations: list[Observation]) -> Fingerprint | None:
        body = _body(observations)
        if not body:
            return None
        return _vector_fingerprint(view, self.name, self.version, mask(body, observations))
