"""Shared reader for the identifier extractors. Registers nothing.

"Harvest from every location" is implemented once, here: the landing URL, the page HTML
(or the HAR's main document body for a HAR-only view), inline scripts, hidden inputs,
forms, anchors, first-party script files, `_files/` names, HAR requests and the tracker
config object. Extractors read a `Material` and never open files themselves.

Every reading step runs in its own guard: a malformed file drops that step only.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import tldextract

from ..contracts import Observation, Purpose, Source, View, ViewKind
from ..ingest.har import load_har, main_document
from ..registry import Context

# Use the suffix list bundled with the library: no network call at run time.
_tld = tldextract.TLDExtract(suffix_list_urls=())

MAX_HTML = 10 << 20  # characters read from a page
MAX_SCRIPT = 2 << 20  # bytes per first-party script file
_SAVE_SUFFIXES = (".pobrane", ".pobierz", ".download")
_JS_EXT = (".js", ".mjs")


# --- domains and the landing URL -------------------------------------------------------


def registrable_domain(host: str) -> str:
    parts = _tld(host)
    return f"{parts.domain}.{parts.suffix}" if parts.domain and parts.suffix else host


def landing_url(view: View) -> tuple[str, Source, bool] | None:
    """The page's URL in order of trust: (url, source, is_page_url).

    A first-party request is not the page itself, so `is_page_url` is False for it and
    callers should use its host only.
    """
    choices = (
        ("saved_from", view.primary, "html:comment[saved from url]", True),
        ("manifest_url", view.primary, "manifest.json:page_url", True),
        ("har_document", view.har, "har:main document request.url", True),
        ("har_first_party", view.har, "har:busiest first-party host", False),
    )
    for key, file, locator, is_page_url in choices:
        url = view.meta.get(key)
        if isinstance(url, str) and url:
            return url, Source(file=file, locator=locator), is_page_url
    return None


def own_domain(view: View) -> str | None:
    """Registrable domain of the page, or None when the page's URL is unknown."""
    found = landing_url(view)
    if found is None:
        return None
    host = (urlsplit(found[0]).hostname or "").lower()
    return registrable_domain(host) if host else None


# --- values ------------------------------------------------------------------------------

_MACRO = re.compile(r"\{\{.*?\}\}|\{[A-Za-z_][\w.\-]*\}|%[A-Za-z_][\w.\-]*%|__[A-Z_]+__")
_PLACEHOLDER_WORDS = {"null", "none", "undefined", "nan", "false", "true", "test", "xxx"}


def is_placeholder(value: Any) -> bool:
    """True for empty values and unfilled template macros (`{fbtoken}`, `{{aio.visit…}}`)."""
    if value is None:
        return True
    text = str(value).strip()
    if not text or text.lower() in _PLACEHOLDER_WORDS:
        return True
    if _MACRO.search(text):
        return True
    return set(text) <= set("0-_. ")


# --- what one view's material looks like ------------------------------------------------


@dataclass(frozen=True)
class Text:
    """A piece of text with where it came from."""

    text: str
    source: Source


@dataclass(frozen=True)
class Tag:
    """One HTML element's attributes, lower-cased names, with where it was found."""

    attrs: dict[str, str]
    source: Source


@dataclass(frozen=True)
class HarRequest:
    index: int
    url: str
    method: str
    post_text: str | None
    resource_type: str
    mime: str
    file: str

    @property
    def source(self) -> Source:
        return Source(file=self.file, locator=f"har:entries[{self.index}].request.url")

    @property
    def post_source(self) -> Source:
        return Source(file=self.file, locator=f"har:entries[{self.index}].request.postData")


@dataclass(frozen=True)
class Material:
    html: Text | None = None
    inline_scripts: tuple[Text, ...] = ()
    script_srcs: tuple[Tag, ...] = ()
    hidden_inputs: tuple[Tag, ...] = ()
    inputs: tuple[Tag, ...] = ()
    forms: tuple[Tag, ...] = ()
    anchors: tuple[Tag, ...] = ()
    iframes: tuple[Tag, ...] = ()
    base_href: str | None = None
    first_party_scripts: tuple[Text, ...] = ()
    resource_names: tuple[Text, ...] = ()
    har_requests: tuple[HarRequest, ...] = ()
    aio: dict[str, Any] | None = None
    aio_source: Source | None = None
    errors: tuple[str, ...] = field(default=())

    def script_texts(self) -> list[Text]:
        """Inline scripts first, then first-party script files."""
        return [*self.inline_scripts, *self.first_party_scripts]


# --- HTML --------------------------------------------------------------------------------


class _Collector(HTMLParser):
    def __init__(self, file: str | None):
        super().__init__(convert_charrefs=True)
        self.file = file
        self.counts: dict[str, int] = {}
        self.inline_scripts: list[Text] = []
        self.script_srcs: list[Tag] = []
        self.hidden_inputs: list[Tag] = []
        self.inputs: list[Tag] = []
        self.forms: list[Tag] = []
        self.anchors: list[Tag] = []
        self.iframes: list[Tag] = []
        self.base_href: str | None = None
        self._script: list[str] | None = None
        self._script_index = 0

    def _source(self, tag: str, index: int, detail: str = "") -> Source:
        return Source(file=self.file, locator=f"html:{tag}[{index}]{detail}")

    def _next(self, tag: str) -> int:
        index = self.counts.get(tag, 0)
        self.counts[tag] = index + 1
        return index

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "script":
            index = self._next("script")
            if a.get("src"):
                self.script_srcs.append(Tag(a, self._source("script", index, ".src")))
            else:
                self._script, self._script_index = [], index
        elif tag == "input":
            index = self._next("input")
            if a.get("type", "").lower() == "hidden":
                name = a.get("name") or a.get("id") or ""
                self.hidden_inputs.append(Tag(a, self._source("input", index, f"[name={name}]")))
            self.inputs.append(Tag(a, self._source("input", index)))
        elif tag == "form":
            self.forms.append(Tag(a, self._source("form", self._next("form"), ".action")))
        elif tag == "a":
            self.anchors.append(Tag(a, self._source("a", self._next("a"), ".href")))
        elif tag == "iframe":
            self.iframes.append(Tag(a, self._source("iframe", self._next("iframe"), ".src")))
        elif tag == "base" and a.get("href") and self.base_href is None:
            self.base_href = a["href"]

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag == "script":
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script is not None:
            text = "".join(self._script)
            if text.strip():
                self.inline_scripts.append(Text(text, self._source("script", self._script_index)))
            self._script = None


# --- tracker config ----------------------------------------------------------------------

_AIO_START = re.compile(r"(?<![\w.$])(?:window\.)?aio\s*=\s*")
_JS_ESCAPE = re.compile(r"\\(u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)", re.DOTALL)
_JS_SIMPLE = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}


def js_unescape(text: str) -> str:
    """Decode a JavaScript string literal's body (without its quotes)."""

    def sub(m: re.Match[str]) -> str:
        esc = m.group(1)
        if esc[0] in "ux" and len(esc) > 1:
            return chr(int(esc[1:], 16))
        return _JS_SIMPLE.get(esc, esc)

    return _JS_ESCAPE.sub(sub, text)


def _string_literal(text: str, start: int) -> tuple[str, int] | None:
    """Read a quoted JS string starting at `start` (the quote). Returns (body, end index)."""
    if start >= len(text) or text[start] not in "'\"`":
        return None
    quote, i = text[start], start + 1
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == quote:
            return text[start + 1 : i], i + 1
        i += 1
    return None


def _balanced(text: str, start: int) -> str | None:
    """The `{…}` block starting at `start`, respecting strings."""
    depth, i = 0, start
    while i < len(text):
        ch = text[i]
        if ch in "'\"`":
            lit = _string_literal(text, i)
            if lit is None:
                return None
            i = lit[1]
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    return None


def aio_config(script: str) -> dict[str, Any] | None:
    """Parse `window.aio = JSON.parse('…')` or `window.aio = {…}` from one script."""
    for m in _AIO_START.finditer(script):
        rest = m.end()
        head = script[rest : rest + 16]
        try:
            if head.startswith("JSON.parse("):
                i = rest + len("JSON.parse(")
                while i < len(script) and script[i].isspace():
                    i += 1
                lit = _string_literal(script, i)
                if lit is not None:
                    data = json.loads(js_unescape(lit[0]))
                    if isinstance(data, dict):
                        return data
            elif head.startswith("{"):
                block = _balanced(script, rest)
                if block is not None:
                    data = json.loads(block)
                    if isinstance(data, dict):
                        return data
        except (ValueError, RecursionError):
            continue
    return None


def config_get(config: dict[str, Any] | None, path: str) -> Any:
    """Walk a dotted path (`campaign.uuid`). Returns None when any step is missing."""
    node: Any = config
    for key in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


# --- loading ----------------------------------------------------------------------------


def _strip_save_suffix(name: str) -> str:
    lower = name.lower()
    for suffix in _SAVE_SUFFIXES:
        if lower.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _body_text(entry: dict[str, Any]) -> str | None:
    content = (entry.get("response") or {}).get("content") or {}
    text = content.get("text")
    if not isinstance(text, str) or not text:
        return None
    if content.get("encoding") == "base64":
        try:
            return base64.b64decode(text).decode("utf-8", errors="replace")
        except ValueError:
            return None
    return text


def _post_text(request: dict[str, Any]) -> str | None:
    post = request.get("postData")
    if not isinstance(post, dict):
        return None
    text = post.get("text")
    if isinstance(text, str) and text:
        return text
    params = post.get("params")
    if isinstance(params, list) and params:
        return "&".join(
            f"{p.get('name', '')}={p.get('value', '')}" for p in params if isinstance(p, dict)
        )
    return None


def _har_request(index: int, entry: dict[str, Any], har: str) -> HarRequest | None:
    req = entry.get("request") or {}
    url = req.get("url")
    if not isinstance(url, str) or not url:
        return None
    response = entry.get("response") or {}
    content = response.get("content") or {} if isinstance(response, dict) else {}
    return HarRequest(
        index=index,
        url=url,
        method=str(req.get("method") or "GET").upper(),
        post_text=_post_text(req),
        resource_type=str(entry.get("_resourceType") or "").lower(),
        mime=str(content.get("mimeType") or "") if isinstance(content, dict) else "",
        file=har,
    )


def _guard(errors: list[str], step: str, fn: Callable[[], None]) -> None:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 — an extractor never raises on bad input
        errors.append(f"{step}: {type(exc).__name__}: {exc}")


@lru_cache(maxsize=64)
def _load(
    root: str,
    primary: str | None,
    har: str | None,
    resources_dir: str | None,
    page_host: str | None,
    stamp: tuple[int, ...],  # file state: a changed file is read again
) -> Material:
    base = Path(root)
    errors: list[str] = []
    state: dict[str, Any] = {}

    def read_har() -> None:
        if not har:
            return
        entries = load_har(base / har)
        state["entries"] = entries
        requests = []
        for i, entry in enumerate(entries):
            try:  # one malformed entry must not cost the others
                requests.append(_har_request(i, entry, har))
            except Exception:  # noqa: BLE001, S112
                continue
        state["har_requests"] = tuple(r for r in requests if r is not None)

    def read_html() -> None:
        if primary and Path(primary).suffix.lower() in (".html", ".htm"):
            with open(base / primary, encoding="utf-8", errors="replace") as fh:
                state["html"] = Text(fh.read(MAX_HTML), Source(file=primary, locator="html"))
            return
        entries = state.get("entries") or []
        if primary is None and entries:
            doc = main_document(entries, prefer_host=page_host)
            if doc is not None:
                body = _body_text(entries[doc[0]])
                if body:
                    state["html"] = Text(
                        body[:MAX_HTML],
                        Source(file=har, locator=f"har:entries[{doc[0]}].response.content"),
                    )

    def parse_html() -> None:
        html = state.get("html")
        if html is None:
            return
        collector = _Collector(html.source.file)
        collector.feed(html.text)
        collector.close()
        if html.source.locator != "html":  # a HAR body: point locators at the HAR entry
            prefix = html.source.locator

            def retarget(t: Any) -> Any:
                loc = f"{prefix}>{t.source.locator}"
                src = Source(file=html.source.file, locator=loc)
                return Text(t.text, src) if isinstance(t, Text) else Tag(t.attrs, src)

        else:
            retarget = lambda t: t
        for name in (
            "inline_scripts",
            "script_srcs",
            "hidden_inputs",
            "inputs",
            "forms",
            "anchors",
            "iframes",
        ):
            state[name] = tuple(retarget(t) for t in getattr(collector, name))
        state["base_href"] = collector.base_href

    def read_resources() -> None:
        if not resources_dir:
            return
        rdir = base / resources_dir
        names, scripts = [], []
        for p in sorted(rdir.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(base).as_posix()
            names.append(Text(p.name, Source(file=rel, locator="file:name")))
            clean = _strip_save_suffix(p.name).lower()
            if clean.endswith(_JS_EXT) and p.stat().st_size <= MAX_SCRIPT:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    scripts.append(Text(fh.read(), Source(file=rel, locator="file:text")))
        state["resource_names"] = tuple(names)
        state["first_party_scripts"] = tuple(scripts)

    def read_har_scripts() -> None:
        # Only for HAR-only views: a saved page carries its scripts in its _files/ folder.
        if primary is not None or not page_host:
            return
        own = registrable_domain(page_host)
        entries = state.get("entries") or []
        scripts = []
        for req in state.get("har_requests", ()):
            host = host_of(req.url)
            is_js = req.resource_type == "script" or "javascript" in req.mime.lower()
            if not is_js or registrable_domain(host) != own:
                continue
            body = _body_text(entries[req.index])
            if body and len(body) <= MAX_SCRIPT:
                scripts.append(
                    Text(
                        body, Source(file=har, locator=f"har:entries[{req.index}].response.content")
                    )
                )
        state["first_party_scripts"] = (*state.get("first_party_scripts", ()), *scripts)

    def read_aio() -> None:
        for script in (*state.get("inline_scripts", ()), *state.get("first_party_scripts", ())):
            config = aio_config(script.text)
            if config is not None:
                state["aio"], state["aio_source"] = config, script.source
                return

    for step, fn in (
        ("har", read_har),
        ("html", read_html),
        ("parse", parse_html),
        ("resources", read_resources),
        ("har_scripts", read_har_scripts),
        ("aio", read_aio),
    ):
        _guard(errors, step, fn)
    state.pop("entries", None)
    return Material(**state, errors=tuple(errors))


def load(view: View, ctx: Context) -> Material:
    """Everything readable about one page view. Empty `Material` for images and videos.

    Cached on the files, not the view: `View` is unhashable and view IDs repeat across
    folders with the same name.
    """
    if view.kind is not ViewKind.PAGE:
        return Material()
    try:
        root = ctx.store.get_incident(view.incident_id).root
    except Exception:  # noqa: BLE001
        return Material(errors=("incident not found",))
    found = landing_url(view)
    page_host = ((urlsplit(found[0]).hostname or "").lower() or None) if found else None
    base = Path(root)
    stamp = (
        *_file_stamp(base, view.primary),
        *_file_stamp(base, view.har),
        *_dir_stamp(base, view.resources_dir),
    )
    return _load(root, view.primary, view.har, view.resources_dir, page_host, stamp)


def _file_stamp(root: Path, rel: str | None) -> tuple[int, int]:
    """(mtime in ns, size) of one file; zeros when absent."""
    if not rel:
        return 0, 0
    try:
        st = (root / rel).stat()
    except OSError:
        return 0, 0
    return st.st_mtime_ns, st.st_size


def _dir_stamp(root: Path, rel: str | None) -> tuple[int, int, int]:
    """(file count, total size, newest mtime in ns) of a resources folder.

    A directory's own mtime does not change when a file inside it is edited in place,
    so the files themselves are looked at.
    """
    if not rel:
        return 0, 0, 0
    count = size = newest = 0
    try:
        for p in (root / rel).rglob("*"):
            try:
                st = p.stat()
            except OSError:
                continue
            if p.is_file():
                count, size, newest = count + 1, size + st.st_size, max(newest, st.st_mtime_ns)
    except OSError:
        return 0, 0, 0
    return count, size, newest


def clear_cache() -> None:
    _load.cache_clear()


# --- emitting observations --------------------------------------------------------------


class Emitter:
    """Builds observations for one view and extractor; de-duplicates on (namespace, value).

    The first source wins, so callers emit locations in order of trust.
    """

    def __init__(self, view: View, extractor: str, version: str):
        self.view, self.extractor, self.version = view, extractor, version
        self._seen: set[tuple[str, str]] = set()
        self.out: list[Observation] = []

    def __call__(
        self,
        signal_id: str,
        namespace: str,
        value: Any,
        source: Source,
        raw: str | None = None,
        purpose: Purpose = Purpose.LINKAGE,
        confidence: float = 1.0,
    ) -> bool:
        if is_placeholder(value):
            return False
        key = (namespace, json.dumps(value, sort_keys=True, default=str))
        if key in self._seen:
            return False
        self._seen.add(key)
        self.out.append(
            Observation(
                view_id=self.view.view_id,
                signal_id=signal_id,
                namespace=namespace,
                value=value,
                raw=raw if raw is not None else (value if isinstance(value, str) else None),
                purpose=purpose,
                source=source,
                extractor=self.extractor,
                version=self.version,
                confidence=confidence,
            )
        )
        return True


# --- helpers for extractors -------------------------------------------------------------


def at(source: Source | None, detail: str) -> Source:
    """A narrower source inside `source`: `html:script[2]` + `aio:campaign.uuid`."""
    if source is None:
        return Source(locator=detail)
    loc = f"{source.locator}>{detail}" if source.locator else detail
    return Source(file=source.file, locator=loc)


def query_params(url: str) -> list[tuple[str, str]]:
    """Query parameters in order, names lower-cased, blank values kept out."""
    try:
        query = urlsplit(url).query
    except ValueError:
        return []
    return [(k.strip().lower(), v.strip()) for k, v in parse_qsl(query) if v.strip()]


# Third-party libraries browsers save into `_files/`: their code names the vendor's
# endpoints, never the operator's.
_LIBRARY_STEMS = (
    "fbevents", "gtag", "gtm", "analytics", "jquery", "events-s", "uwt", "bat.bing",
    "insight.min", "pixel", "tfa", "tag.js", "recaptcha", "api.js",
)  # fmt: skip


def is_library_script(file: str | None) -> bool:
    if not file:
        return False
    name = _strip_save_suffix(Path(file).name).lower()
    return name.startswith(_LIBRARY_STEMS)


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def run_steps(steps: list[Callable[[], None]]) -> None:
    """Run harvesting steps; a failing step is dropped, the others still run."""
    for step in steps:
        try:
            step()
        except Exception:  # noqa: BLE001, S112 — an extractor never raises on bad input
            continue
