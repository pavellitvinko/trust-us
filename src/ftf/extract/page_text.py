"""Clean page text, its zones and its language (HAC-12).

Signals D13 (capture pollution stripped, in `_dom`), F1 (body text for near-duplicates),
F6 (comment blocks), D7 (title, meta description, headline), D14 (`lang` attribute vs
detected language) and D6 (blocks in another language). `page.clean_text` is the text
the page reader (`perception/page.py`) sends to the model.
"""

from __future__ import annotations

from ..contracts import Observation, Purpose, Source, View, ViewKind
from ..registry import Context, extractors
from . import _material as m
from ._dom import parse
from ._lang import detect, words

MAX_TEXT = 200_000
FOREIGN_MIN_WORDS = 8


def primary_subtag(lang: str | None) -> str | None:
    """`pl-PL` -> `pl`; None for empty or malformed values."""
    if not lang:
        return None
    head = lang.strip().split("-")[0].split("_")[0].lower()
    return head if head.isalpha() and 2 <= len(head) <= 3 else None


@extractors.register
class PageText:
    name = "page.text"
    version = "1"

    def run(self, view: View, ctx: Context) -> list[Observation]:
        if view.kind is not ViewKind.PAGE:
            return []
        html = m.load(view, ctx).html
        if html is None:
            return []
        dom = parse(html.text)
        emit = m.Emitter(view, self.name, self.version)
        body = dom.text("body")

        def src(detail: str) -> Source:
            return m.at(html.source, detail)

        def texts() -> None:
            all_text = dom.text()[:MAX_TEXT]
            emit("F1", "page.clean_text", all_text, src("text:all"), raw="", whole=True)
            emit("F1", "page.body_text", body[:MAX_TEXT], src("text:body"), raw="", whole=True)
            comments = [b.text for b in dom.blocks if b.zone == "comments"]
            if comments:
                emit("F6", "page.comments", comments, src("text:comments"), raw="", whole=True)

        def template() -> None:
            emit("D7", "page.title", dom.title, src("title"))
            emit("D7", "page.meta_description", dom.meta_description, src("meta[description]"))
            if dom.h1:
                emit("D7", "page.headline", dom.h1[0], src("h1[0]"))
            elif dom.og_title:
                emit("D7", "page.headline", dom.og_title, src("meta[og:title]"))

        def language() -> None:
            declared = primary_subtag(dom.html_lang)
            detected = detect(body) or detect(dom.text())
            if declared:
                emit("D14", "page.lang_attr", declared, src("html[lang]"), raw=dom.html_lang)
            if detected:
                emit("D14", "page.lang_detected", detected, src("text:body"))
            if declared and detected and declared != detected:
                emit(
                    "D14",
                    "page.lang_mismatch",
                    f"{declared}→{detected}",
                    src("html[lang]"),
                    raw=dom.html_lang,
                    purpose=Purpose.RISK,
                )
            page_lang = detected or declared
            for i, block in enumerate(dom.blocks):
                if block.zone == "chrome" or len(words(block.text)) < FOREIGN_MIN_WORDS:
                    continue
                lang = detect(block.text, min_words=FOREIGN_MIN_WORDS)
                if lang and page_lang and lang != page_lang:
                    emit(
                        "D6",
                        "page.foreign_lang",
                        lang,
                        src(f"text:block[{i}]"),
                        raw=block.text[:120],
                        purpose=Purpose.BOTH,
                    )

        m.run_steps([texts, template, language])
        return emit.out
