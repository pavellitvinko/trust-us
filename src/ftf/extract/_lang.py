"""Language of a piece of text, by stopwords. Registers nothing; no extra dependency.

Coarse on purpose: the signals that use it (D5, D6, D14) only ask "which language is
this", for a page body or a developer comment. Too little text returns None.
"""

from __future__ import annotations

import re

_STOPWORD_TEXT = {
    "pl": (
        "i w na nie się z do że to jest jak po co ale od za o przez już tak dla czy jego "
        "był jej tym oraz który która które przy tylko także może są być ze"
    ),
    "en": (
        "the and of to in is that it for on with as was are this be by at from or have "
        "not but they you an which has his her their will"
    ),
    "de": (
        "der die das und ist nicht ein eine zu den mit von sich des auf für im dem auch "
        "es sie ich wir oder werden"
    ),
    "fr": (
        "le la les et des est un une du en que qui pour pas dans sur au avec ce il elle "
        "nous vous sont ou par"
    ),
    "nl": (
        "de het een en van is dat niet op te zijn met voor er maar ook als bij aan om "
        "dit wordt ik je hij"
    ),
    "es": (
        "el la los las y de que en un una es por con para no se del al lo como más pero su sus muy"
    ),
    "it": (
        "il lo la gli le e di che un una è per non con del della sono si come anche "
        "ma più nel alla questo"
    ),
}
_STOPWORDS = {lang: frozenset(text.split()) for lang, text in _STOPWORD_TEXT.items()}
# A word shared by two lists (`to`, `de`, `la`) tells nothing apart.
_ALL = {w for a in _STOPWORDS.values() for w in a}
_SHARED = {w for w in _ALL if sum(w in a for a in _STOPWORDS.values()) > 1}
_STOPWORDS = {lang: frozenset(stop - _SHARED) for lang, stop in _STOPWORDS.items()}
# Letters that occur in one language of the set only; a word holding one counts as a hit.
_LETTERS: dict[str, frozenset[str]] = {
    "pl": frozenset("ąęłńśźżć"),
    "de": frozenset("äß"),
    "fr": frozenset("çêëîïôûœ"),  # not `è`: Italian `è` is its most common word
    "es": frozenset("ñ"),
}
# Letters of languages outside the set (cs/sk, pt, ro). Text that holds them is not
# Polish, Spanish or Italian just because it shares stopwords with them: None.
_OUTSIDE_LETTERS = frozenset("ěřůťďňľĺŕãõășțşţ")
SUPPORTED = frozenset({"pl", "en", "de", "fr", "nl", "es", "it", "ru", "uk"})
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_UK_LETTERS = set("іїєґ")
_RU_LETTERS = set("ыэё")  # not `ъ`: rare in Russian, common in Bulgarian
_MIN_SHARE = 0.05  # share of words that are stopwords of the winning language
_MARGIN = 1.5  # the winner must beat the runner-up by this factor


def has_cyrillic(text: str) -> bool:
    return bool(_CYRILLIC.search(text))


def words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text)]


def detect(text: str, min_words: int = 20) -> str | None:
    """`pl`, `en`, `de`, `fr`, `nl`, `es`, `it`, `ru`, `uk`, or None when unsure."""
    tokens = words(text)
    if len(tokens) < min_words:
        return None
    cyrillic = [w for w in tokens if _CYRILLIC.search(w)]
    if len(cyrillic) * 2 > len(tokens):
        letters = set("".join(cyrillic))
        if letters & _UK_LETTERS and not letters & _RU_LETTERS:
            return "uk"
        if letters & _RU_LETTERS and not letters & _UK_LETTERS:
            return "ru"
        return None
    outside = sum(not _OUTSIDE_LETTERS.isdisjoint(w) for w in tokens)
    if outside >= max(2, 0.02 * len(tokens)):
        return None
    hits = {
        lang: sum(w in stop or not _LETTERS.get(lang, set()).isdisjoint(w) for w in tokens)
        for lang, stop in _STOPWORDS.items()
    }
    ranked = sorted(hits.items(), key=lambda kv: kv[1], reverse=True)
    (best, top), (_, second) = ranked[0], ranked[1]
    if top < max(2, _MIN_SHARE * len(tokens)) or top < _MARGIN * second:
        return None
    return best
