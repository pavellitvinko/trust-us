"""Page structure and markup facts (HAC-12).

Signals D1 (tag-path skeleton, text removed), D2/D8 (class-name set, translator classes
removed), D5/D7/D8 (developer comments and their language), E13 (a visual-evasion filter
on `<html>`: its presence, not the angle) and F7 markup facts for the model (countdown,
crossed-out price, stock counter, form fields). Markup facts are RISK: they never group.
"""

from __future__ import annotations

import re

from ..contracts import Observation, Purpose, Source, View, ViewKind
from ..registry import Context, extractors
from . import _material as m
from ._dom import Dom, parse, squash
from ._lang import detect, has_cyrillic

_FILTER_FUNCS = r"hue-rotate|invert|blur|saturate|grayscale|sepia|contrast|brightness"
# A literal call such as `hue-rotate(7deg)`; `var(--tw-hue-rotate)` has no `(` after the name.
_EVASION = re.compile(rf"(?<![\w-])({_FILTER_FUNCS})\(\s*[-\d.]", re.IGNORECASE)
_FILTER_DECL = re.compile(r"(?:-webkit-)?filter\s*:[^;}]*", re.IGNORECASE)
_STOCK = re.compile(
    r"pozostał[oa]?\D{0,30}\d+|zostało\s+(?:tylko\s+)?\d+|\d+\s*(?:sztuk\w*|szt\.|miejsc\w*)"
    r"|only\s+\d+\s+left",
    re.IGNORECASE,
)
_DIGITS = re.compile(r"\d+(?:[\s.,]\d+)*")
_HAS_DIGIT = re.compile(r"\d")
_MARKUP = re.compile(r"<[^>]*>|https?://\S+|[?&][\w=&]+")


def evasion_filters(dom: Dom) -> list[tuple[str, str]]:
    """(function name, declaration) for filters on the page root."""
    found: list[tuple[str, str]] = []
    for css in (dom.html_style, dom.body_style, *dom.root_css):
        for decl in _FILTER_DECL.findall(css):
            for name in _EVASION.findall(decl):
                found.append((name.lower(), decl.strip()))
    return found


def comment_langs(comments: tuple[str, ...]) -> list[str]:
    """Languages of developer comments, commented-out markup removed first.

    Two Cyrillic words already name the language; Latin text needs a sentence.
    """
    found: list[str] = []
    for comment in comments:
        text = _MARKUP.sub(" ", comment)
        lang = detect(text, min_words=2 if has_cyrillic(text) else 6)
        if lang and lang not in found:
            found.append(lang)
    return found


def stock_counters(text: str) -> list[tuple[str, str]]:
    """(shape with digits as `<n>`, original phrase)."""
    return [(_DIGITS.sub("<n>", squash(p)), squash(p)) for p in _STOCK.findall(text)]


@extractors.register
class PageStructure:
    name = "page.structure"
    version = "1"

    def run(self, view: View, ctx: Context) -> list[Observation]:
        if view.kind is not ViewKind.PAGE:
            return []
        html = m.load(view, ctx).html
        if html is None:
            return []
        dom = parse(html.text)
        emit = m.Emitter(view, self.name, self.version)

        def src(detail: str) -> Source:
            return m.at(html.source, detail)

        def skeleton() -> None:
            if dom.classes:
                emit("D2", "dom.class_set", sorted(dom.classes), src("class"), raw="")
            if dom.tag_paths:
                emit("D1", "dom.tag_paths", sorted(dom.tag_paths), src("tags"), raw="")

        def comments() -> None:
            if not dom.comments:
                return
            emit("D5", "dom.comments", list(dom.comments), src("comment"), raw="")
            for lang in comment_langs(dom.comments):
                if lang:
                    emit("D5", "dom.comment_lang", lang, src("comment"), purpose=Purpose.BOTH)

        def evasion() -> None:
            for name, decl in evasion_filters(dom):
                emit("E13", "dom.evasion_filter", name, src("html[style]"), raw=decl,
                     purpose=Purpose.BOTH)  # fmt: skip

        def facts() -> None:
            for token in dom.countdown:
                emit("F7", "markup.countdown", token, src("class"), purpose=Purpose.RISK)
            for text in dom.struck:
                if _HAS_DIGIT.search(text):
                    emit("F7", "markup.crossed_price", text, src("s"), purpose=Purpose.RISK)
            for shape, phrase in stock_counters(dom.text()):
                emit("F7", "markup.stock_counter", shape, src("text:all"), raw=phrase,
                     purpose=Purpose.RISK)  # fmt: skip
            for kind, name in dom.fields:
                emit("F7", "markup.form_field", f"{kind}:{name}", src("form"),
                     purpose=Purpose.RISK)  # fmt: skip

        m.run_steps([skeleton, comments, evasion, facts])
        return emit.out
