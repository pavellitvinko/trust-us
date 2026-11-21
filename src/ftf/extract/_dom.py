"""One structural pass over a page's HTML. Registers nothing.

Shared by the `page.text` and `page.structure` extractors (HAC-12) and cached on the HTML,
so a page is parsed once. Capture pollution (D13) is removed here, before anything else
sees the page: browser-translate widgets and extension markup lose their text, their
classes and their comments.

Every visible text block carries a zone: `body` (the article), `comments` (comment and
review blocks) or `chrome` (navigation, header, footer, widgets).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from html.parser import HTMLParser
from typing import Literal

Zone = Literal["body", "comments", "chrome"]

MAX_DEPTH = 6  # tag paths deeper than this are cut (D1)
# A "chrome" wrapper holding the headline or this much text is a page container
# (`<header class="header-all">` around a whole lander), not navigation.
CHROME_MAX_CHARS = 5000
# ... unless that text is mostly links: a mega-menu or a sitemap footer stays chrome.
CHROME_MAX_LINK_SHARE = 0.5
# A zone wrapper holding more than this share of the page's text, when the body zone is
# otherwise empty, is the page itself (`<div class="review-page">` around a lander).
PAGE_WRAPPER_SHARE = 0.5

_SKIPPED_TAGS = {
    "script", "style", "noscript", "template", "svg", "math", "head", "title", "select", "datalist",
}  # fmt: skip
_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
    "source", "track", "wbr",
}  # fmt: skip
_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt", "figcaption",
    "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li",
    "main", "nav", "ol", "option", "p", "section", "table", "td", "th", "tr", "ul", "button",
    "label", "select", "textarea",
}  # fmt: skip
_CLOSES_P = _BLOCK_TAGS - {"br", "button", "label", "select", "textarea", "option"}
_STRUCK_TAGS = {"s", "del", "strike"}
_NEVER_SKIPPED = {"html", "body"}
_NO_ZONE = {"html", "body", "main"}  # page-wide: a class here (`comments-open`) sets no zone
_FOREIGN_TITLE = {"svg", "math"}  # an icon's `<title>` is not the page title

TRANSLATE = re.compile(r"goog-te|goog-gt|skiptranslate|google_translate|VIpgJd", re.IGNORECASE)
_TRANSLATE_STATE = {"translated-ltr", "translated-rtl"}  # page-level class, no own text
_EXTENSION_VALUE = re.compile(r"(?:chrome|moz|safari-web)-extension://", re.IGNORECASE)
# Extension elements (`<grammarly-desktop-integration>`, `<honey-container>`). In class and id
# only the brand names that never occur as page words: `honey-offer` is page content.
_EXTENSION_TAG = re.compile(r"^(?:grammarly|lastpass|dashlane|honey)-", re.IGNORECASE)
_EXTENSION_MARK = re.compile(r"grammarly|lastpass|dashlane", re.IGNORECASE)
_EXTENSION_ATTR = re.compile(r"^data-(?:gr-|lastpass|dashlane|grammarly|honey)", re.IGNORECASE)
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE)
_LINE_THROUGH = re.compile(r"line-through", re.IGNORECASE)
_COUNTDOWN = re.compile(r"countdown|timer|odlicz", re.IGNORECASE)
_TOKEN_SPLIT = re.compile(r"[-_]+")
# State modifiers on a page wrapper (`has-sidebar`, `menu-open`, `no-comments`) describe
# the page around it, not the element: they set no zone.
_MODIFIER = re.compile(
    r"^(?:has|with|without|no|is|show|hide)[-_]|[-_](?:open|opened|closed|active|visible"
    r"|enabled|disabled|expanded|collapsed)$",
    re.IGNORECASE,
)
_COMMENT_PREFIXES = ("comment", "koment", "review", "opini", "testimonial")
_CHROME_PREFIXES = ("nav", "menu", "footer", "sidebar", "breadcrumb", "cookie", "widget")
_CHROME_WORDS = {"share", "related", "social", "topbar"}
_CHROME_TAGS = {"nav", "header", "footer", "aside"}
_ROOT_SELECTORS = {"html", "body", ":root"}
_CSS_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_CSS_STATEMENT = re.compile(r"@[\w-]+[^;{}]*;")
_LETTER = re.compile(r"[^\W\d_]")
_FIELD_TAGS = {"input", "select", "textarea"}
_NOT_FIELDS = {"hidden", "submit", "button", "image", "reset"}


@dataclass(frozen=True)
class Block:
    text: str
    zone: Zone


@dataclass(frozen=True)
class Dom:
    blocks: tuple[Block, ...] = ()
    title: str = ""
    meta_description: str = ""
    og_title: str = ""
    h1: tuple[str, ...] = ()
    html_lang: str | None = None
    html_style: str = ""
    body_style: str = ""
    root_css: tuple[str, ...] = ()
    classes: frozenset[str] = frozenset()
    tag_paths: frozenset[str] = frozenset()
    comments: tuple[str, ...] = ()
    struck: tuple[str, ...] = ()
    countdown: tuple[str, ...] = ()
    fields: tuple[tuple[str, str], ...] = ()  # (type, name) of visible form fields

    def text(self, *zones: Zone) -> str:
        """Blocks of the given zones (all when none given), one per line."""
        return "\n".join(b.text for b in self.blocks if not zones or b.zone in zones)


def squash(text: str) -> str:
    return " ".join(text.split())


def _tokens(attrs: dict[str, str]) -> list[str]:
    marks = f"{attrs.get('class', '')} {attrs.get('id', '')}".lower().split()
    return [t for m in marks if not _MODIFIER.search(m) for t in _TOKEN_SPLIT.split(m) if t]


def is_polluting(tag: str, attrs: dict[str, str]) -> bool:
    """Browser-translate or extension markup: never part of the page as served."""
    marks = f"{attrs.get('class', '')} {attrs.get('id', '')}"
    if TRANSLATE.search(marks) or _EXTENSION_TAG.search(tag) or _EXTENSION_MARK.search(marks):
        return True
    return any(
        _EXTENSION_ATTR.match(name) or _EXTENSION_VALUE.search(value)
        for name, value in attrs.items()
    )


@dataclass
class _Frame:
    tag: str
    skipped: bool  # no visible text inside
    foreign: bool  # capture pollution: no text, classes, paths or comments
    zone: Zone
    in_article: bool
    path: str
    owner: int = 0  # the nearest element that set a zone; 0 is the page itself
    owns: bool = False  # this element set that zone
    capture: list[str] | None = None  # text collected for h1 / struck elements
    capture_kind: str = ""


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        root = _Frame("#root", False, False, "body", False, "")
        self.stack: list[_Frame] = [root]
        self.lines: list[tuple[str, int]] = []  # (text, owner)
        self._line: list[str] = []
        self._line_owner = 0
        self._owners: list[tuple[Zone, int]] = [("body", 0)]  # (zone, parent owner)
        self._chars: dict[int, int] = {}
        self._link_chars: dict[int, int] = {}
        self._owner_tags: dict[int, str] = {}
        self._demoted: set[int] = set()
        self.title: list[str] = []
        self._in_title = False
        self._title_done = False
        self._style: list[str] | None = None
        self.meta_description = ""
        self.og_title = ""
        self.h1: list[tuple[str, int]] = []  # (text, owner)
        self.struck: list[str] = []
        self.html_lang: str | None = None
        self.html_style = ""
        self.body_style = ""
        self.root_css: list[str] = []
        self.classes: set[str] = set()
        self.tag_paths: set[str] = set()
        self.comments: list[str] = []
        self.countdown: list[str] = []
        self.fields: list[tuple[str, str]] = []

    # --- text lines --------------------------------------------------------------------

    def _flush(self) -> None:
        text = squash("".join(self._line))
        if text:
            self.lines.append((text, self._line_owner))
        self._line = []

    # --- stack -------------------------------------------------------------------------

    def _pop_to(self, index: int) -> None:
        while len(self.stack) > index:
            frame = self.stack.pop()
            if frame.owns and self._too_big_for_chrome(frame.owner):
                self._demoted.add(frame.owner)
            if frame.capture is not None:
                text = squash("".join(frame.capture))
                if text and frame.capture_kind == "h1":
                    self.h1.append((text, frame.owner))
                elif text:
                    self.struck.append(text)
            if frame.tag in _BLOCK_TAGS:
                self._flush()

    def _too_big_for_chrome(self, owner: int) -> bool:
        if self._owners[owner][0] != "chrome" or self._owner_tags.get(owner) == "nav":
            return False
        chars = self._chars.get(owner, 0)
        links = self._link_chars.get(owner, 0)
        return chars > CHROME_MAX_CHARS and links <= CHROME_MAX_LINK_SHARE * chars

    def _find(self, tag: str, stop: set[str] = frozenset()) -> int | None:
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                return i
            if self.stack[i].tag in stop:
                return None
        return None

    def _implied_close(self, tag: str) -> None:
        top = self.stack[-1].tag
        if top == "p" and tag in _CLOSES_P:
            self._pop_to(len(self.stack) - 1)
        elif tag == "li":
            i = self._find("li", {"ul", "ol"})
            if i is not None:
                self._pop_to(i)
        elif tag == "option" and top == "option":
            self._pop_to(len(self.stack) - 1)

    def _marker(self, tag: str, tokens: list[str], parent: _Frame) -> Zone | None:
        """The zone this element sets by itself, or None when it inherits its parent's."""
        if tag in _NO_ZONE:
            return None
        if any(t.startswith(_COMMENT_PREFIXES) for t in tokens):
            return "comments"
        if tag in _CHROME_TAGS and not (tag in {"header", "footer"} and parent.in_article):
            return "chrome"
        if any(t.startswith(_CHROME_PREFIXES) or t in _CHROME_WORDS for t in tokens):
            return "chrome"
        return None

    def _chain(self, owner: int):
        while owner:
            yield owner
            owner = self._owners[owner][1]

    def _resolve(self, owner: int) -> Zone:
        while owner in self._demoted:
            owner = self._owners[owner][1]
        return self._owners[owner][0]

    def _record(
        self, tag: str, a: dict[str, str], parent: _Frame, foreign: bool, invisible: bool
    ) -> str:
        path = f"{parent.path}>{tag}" if parent.path else tag
        if foreign:
            return path
        if path.count(">") < MAX_DEPTH:
            self.tag_paths.add(path)
        for cls in a.get("class", "").split():
            low = cls.lower()
            if low not in _TRANSLATE_STATE and not TRANSLATE.search(cls):
                self.classes.add(cls)
        for mark in (*a.get("class", "").split(), a.get("id", "")):
            if mark and _COUNTDOWN.search(mark) and mark.lower() not in self.countdown:
                self.countdown.append(mark.lower())
        if tag == "html":
            lang = a.get("lang", "").strip()
            self.html_lang = lang or None
            self.html_style = a.get("style", "")
        elif tag == "body":
            self.body_style = a.get("style", "")
        elif tag in _FIELD_TAGS and not invisible:
            kind = (a.get("type") or "text").lower() if tag == "input" else tag
            name = a.get("name") or a.get("id") or ""
            if name and kind not in _NOT_FIELDS and (kind, name) not in self.fields:
                self.fields.append((kind, name))
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key == "description" and not self.meta_description:
                self.meta_description = squash(a.get("content", ""))
            elif key == "og:title" and not self.og_title:
                self.og_title = squash(a.get("content", ""))
        return path

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        self._implied_close(tag)
        parent = self.stack[-1]
        foreign = parent.foreign or (tag not in _NEVER_SKIPPED and is_polluting(tag, a))
        hidden = "hidden" in a or bool(_HIDDEN_STYLE.search(a.get("style", "")))
        hidden = tag not in _NEVER_SKIPPED and hidden
        # A field that is hidden, or under a hidden, template or noscript element, is not visible.
        path = self._record(tag, a, parent, foreign, hidden or parent.skipped)
        if tag == "title" and not foreign and not self._title_done:
            self._in_title = not any(f.tag in _FOREIGN_TITLE for f in self.stack)
        if tag == "style" and not foreign:
            self._style = []
        if tag in _BLOCK_TAGS:
            self._flush()
        if tag in _VOID_TAGS:
            return
        skipped = foreign or parent.skipped or tag in _SKIPPED_TAGS or hidden
        marker = self._marker(tag, _tokens(a), parent)
        owner = parent.owner
        if marker is not None:
            self._owners.append((marker, parent.owner))
            owner = len(self._owners) - 1
            self._owner_tags[owner] = tag
        frame = _Frame(
            tag=tag,
            skipped=skipped,
            foreign=foreign,
            zone=marker or parent.zone,
            in_article=parent.in_article or tag == "article",
            path=path,
            owner=owner,
            owns=marker is not None,
        )
        if not skipped and tag == "h1":
            frame.capture, frame.capture_kind = [], "h1"
        elif not skipped and (tag in _STRUCK_TAGS or _LINE_THROUGH.search(a.get("style", ""))):
            frame.capture, frame.capture_kind = [], "struck"
        self.stack.append(frame)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_TAGS and self.stack[-1].tag == tag:
            self._pop_to(len(self.stack) - 1)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._in_title:
            self._in_title, self._title_done = False, True
        if tag == "style" and self._style is not None:
            self._css("".join(self._style))
            self._style = None
        if tag in _VOID_TAGS:
            if tag == "br":
                self._flush()
            return
        index = self._find(tag)
        if index is not None:
            self._pop_to(index)

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title.append(data)
        if self._style is not None:
            self._style.append(data)
        frame = self.stack[-1]
        if frame.skipped:
            return
        if frame.owner != self._line_owner:
            self._flush()
            self._line_owner = frame.owner
        self._line.append(data)
        size = len(data.strip())
        in_link = any(f.tag == "a" for f in self.stack)
        for o in self._chain(frame.owner):
            self._chars[o] = self._chars.get(o, 0) + size
            if in_link:
                self._link_chars[o] = self._link_chars.get(o, 0) + size
        for f in self.stack:
            if f.capture is not None:
                f.capture.append(data)

    def handle_comment(self, data: str) -> None:
        if self.stack[-1].foreign:
            return
        text = data.strip()
        if (
            not text
            or text.lower().startswith("saved from url")
            or text.startswith(("[if", "<![endif]", "[endif]"))
            or not _LETTER.search(text)
        ):
            return
        self.comments.append(squash(text))

    def _css(self, css: str) -> None:
        css = _CSS_COMMENT.sub(" ", css)
        css = _CSS_STATEMENT.sub(" ", css)  # `@import url(x.css);` before the first rule
        for selectors, body in _CSS_RULE.findall(css):
            selectors = selectors.rsplit(";", 1)[-1]
            names = {s.strip().lower() for s in selectors.split(",")}
            if names & _ROOT_SELECTORS:
                self.root_css.append(f"{selectors.strip()}{{{body.strip()}}}")

    def _headlines(self) -> tuple[str, ...]:
        """h1 texts, those in the body zone first.

        A logo `<h1>` in the site header leaves the header chrome when the article has its
        own h1. When no h1 is in the body zone, the wrappers around them (`<header>` or
        `<div class="opinion-page">` around a whole lander) are page containers: demoted.
        """
        in_body = [self._resolve(owner) == "body" for _, owner in self.h1]
        if not any(in_body):
            for _, owner in self.h1:
                self._demoted.update(self._chain(owner))
        ranked = sorted(zip(in_body, self.h1), key=lambda pair: not pair[0])  # stable
        return tuple(text for _, (text, _) in ranked)

    def _page_wrappers(self) -> None:
        """With no body text left, a class-marked wrapper holding most of the page is the page.

        Semantic `<nav>`, `<header>`, `<footer>` and `<aside>` keep their zone.
        """
        if any(self._resolve(owner) == "body" for _, owner in self.lines):
            return
        total = sum(len(text) for text, _ in self.lines)
        for owner, (_, parent) in enumerate(self._owners):
            if not owner or parent or self._owner_tags.get(owner) in _CHROME_TAGS:
                continue
            if self._chars.get(owner, 0) > PAGE_WRAPPER_SHARE * total:
                self._demoted.add(owner)

    def result(self) -> Dom:
        self._pop_to(1)
        self._flush()
        h1 = self._headlines()
        self._page_wrappers()
        return Dom(
            blocks=tuple(Block(text, self._resolve(owner)) for text, owner in self.lines),
            title=squash("".join(self.title)),
            meta_description=self.meta_description,
            og_title=self.og_title,
            h1=h1,
            html_lang=self.html_lang,
            html_style=self.html_style,
            body_style=self.body_style,
            root_css=tuple(self.root_css),
            classes=frozenset(self.classes),
            tag_paths=frozenset(self.tag_paths),
            comments=tuple(self.comments),
            struck=tuple(self.struck),
            countdown=tuple(self.countdown),
            fields=tuple(self.fields),
        )


@lru_cache(maxsize=16)
def parse(html: str) -> Dom:
    """Parse once per HTML text. Malformed markup keeps what was read before it."""
    parser = _Parser()
    try:
        parser.feed(html)
        parser.close()
    except (AssertionError, ValueError, RecursionError):
        pass
    return parser.result()
