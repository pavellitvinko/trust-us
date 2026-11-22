"""A local replay of a sample funnel, so the victim can be tested without touching a live site.

Case 61 is served on `http://glowpixel.localhost:<port>/`. Chromium resolves every
`*.localhost` name to the loopback address, so nothing leaves the machine. The routing is
our reconstruction from the saved URLs, not the operator's server:

- the ad link (`/?utm_id=xWHi…`, the article's own URL in its HAR) serves the saved article,
  with its links to `https://glowpixel.org/` rewritten to this server;
- the article's call-to-action links (`/?cd8474…=…`) answer 302 to the chat's URL;
- the chat's URL (`/?utm_id=xjxk…`) serves the chat page as the HAR recorded it;
- a bare `/` serves the saved decoy page (`strona_3`), as a cloaker would; any other query
  (such as the page after the lead form, which the sample never saved) says it is not recorded;
- any other path is answered from the HAR bodies, then from the saved `_files` folders;
- POST requests are answered with the HAR's recorded response, or `{}`.

`replay_fallback` answers requests to other hosts from the HARs, so a page can load its
recorded CDN scripts while the browser stays confined to loopback (see `browser.py`).
"""

from __future__ import annotations

import base64
import json
import mimetypes
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlsplit

ORIGINAL_HOST = "glowpixel.org"
REPLAY_HOST = "glowpixel.localhost"
ARTICLE_UTM_ID = "xWHiQdT0yt16pq8FCb0u"
CHAT_QUERY = (
    "utm_id=xjxkmObS3y9oLQ9mfVgW&utm_medium=U46vux8s3z&utm_campaign=isyPsj1ZmL"
    "&utm_content=LyTgHoJLnc&utm_visit=01a158a4-44e9-7100-bc0a-a93edf8bc367"
)
CTA_PARAM = "cd8474fb251b91804e868339aef776d6"
# What an ad click looks like: the article's recorded URL with a Facebook click ID added.
AD_QUERY = (
    f"utm_id={ARTICLE_UTM_ID}&utm_medium=ROR1qwj3fk&utm_campaign=KosMMIF9P4"
    "&utm_content=YmjRtdN0XW&utm_visit=01a158a9-054c-73e2-ab76-e210e18fdc98"
    "&fbclid=IwZXh0bgNhZW0BMABhZGlkAasreplay"
)
NOT_RECORDED = (
    b"<!doctype html><html><head><meta charset='utf-8'><title>Not in the replay</title></head>"
    b"<body><p>Replay of case 61: the sample has no recording of this step of the funnel.</p>"
    b"</body></html>"
)
_SAVE_SUFFIXES = (".pobrane", ".pobierz", ".download")
_HOP_HEADERS = {"content-encoding", "content-length", "transfer-encoding", "connection"}


@dataclass
class Recorded:
    status: int
    headers: dict[str, str]
    body: bytes


@dataclass
class HarIndex:
    """Recorded responses by full URL, and by host and path as a fallback."""

    by_url: dict[str, Recorded] = field(default_factory=dict)
    by_path: dict[tuple[str, str], Recorded] = field(default_factory=dict)

    def add_har(self, path: Path) -> None:
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))["log"]["entries"]
        except (OSError, ValueError, KeyError):
            return
        for entry in entries:
            response = entry.get("response") or {}
            status = int(response.get("status") or 0)
            if status <= 0:
                continue
            content = response.get("content") or {}
            text = content.get("text") or ""
            body = base64.b64decode(text) if content.get("encoding") == "base64" else text.encode()
            headers = {
                h["name"].lower(): h["value"]
                for h in response.get("headers", [])
                if h.get("name", "").lower() not in _HOP_HEADERS
            }
            if content.get("mimeType") and "content-type" not in headers:
                headers["content-type"] = content["mimeType"]
            url = entry["request"]["url"]
            recorded = Recorded(status, headers, body)
            self.by_url.setdefault(url, recorded)
            parts = urlsplit(url)
            if body:
                self.by_path.setdefault(((parts.hostname or ""), parts.path), recorded)

    def lookup(self, url: str) -> Recorded | None:
        if url in self.by_url and self.by_url[url].body:
            return self.by_url[url]
        parts = urlsplit(url)
        return self.by_path.get(((parts.hostname or ""), parts.path))


def _strip_save_suffix(name: str) -> str:
    for suffix in _SAVE_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _guess_type(name: str) -> str:
    return mimetypes.guess_type(_strip_save_suffix(name))[0] or "application/octet-stream"


@dataclass
class Case61:
    case_dir: Path
    port: int = 0
    article_html: bytes = b""
    decoy_html: bytes = b""
    chat: Recorded | None = None
    har: HarIndex = field(default_factory=HarIndex)
    files: dict[str, Path] = field(default_factory=dict)  # "/<dir>_files/<name>" -> file
    posts: list[dict[str, str]] = field(default_factory=list)

    @classmethod
    def load(cls, case_dir: Path) -> Case61:
        case = cls(case_dir=Path(case_dir))
        for har in sorted(case.case_dir.rglob("*.har")):
            case.har.add_har(har)
        for html in sorted(case.case_dir.rglob("*.html")):
            if html.parent.name == "strona_1":
                case.article_html = html.read_bytes()
            elif html.parent.name == "strona_3":
                case.decoy_html = html.read_bytes()
        for res in case.case_dir.rglob("*"):
            if res.is_file() and any(p.name.endswith("_files") for p in res.parents):
                top = next(p for p in res.parents if p.name.endswith("_files"))
                rel = res.relative_to(top.parent).as_posix()
                case.files["/" + rel] = res
                case.files["/" + _strip_save_suffix(rel)] = res
        chat_url = f"https://{ORIGINAL_HOST}/?{CHAT_QUERY}"
        case.chat = case.har.by_url.get(chat_url)
        if not case.article_html or case.chat is None:
            raise FileNotFoundError(f"{case_dir} does not look like sample case 61")
        return case

    @property
    def origin(self) -> str:
        return f"http://{REPLAY_HOST}:{self.port}"

    @property
    def ad_url(self) -> str:
        return f"{self.origin}/?{AD_QUERY}"

    def rewrite(self, body: bytes) -> bytes:
        """Point the page's links at this server instead of the live site."""
        return body.replace(f"https://{ORIGINAL_HOST}/".encode(), f"{self.origin}/".encode())

    def answer(self, method: str, raw_path: str) -> tuple[int, dict[str, str], bytes]:
        parts = urlsplit(raw_path)
        query = dict(parse_qsl(parts.query))
        if method == "POST":
            self.posts.append({"path": raw_path})
            recorded = self.har.lookup(f"https://{ORIGINAL_HOST}{raw_path}")
            if recorded is not None:
                return recorded.status, recorded.headers, recorded.body
            return 200, {"content-type": "application/json"}, b"{}"
        html = {"content-type": "text/html; charset=utf-8"}
        if parts.path == "/":
            if CTA_PARAM in query:
                return 302, {"location": f"/?{CHAT_QUERY}"}, b""
            if query.get("utm_id") == ARTICLE_UTM_ID:
                return 200, html, self.rewrite(self.article_html)
            if query.get("utm_id") == "xjxkmObS3y9oLQ9mfVgW" and self.chat is not None:
                return 200, html, self.rewrite(self.chat.body)
            if query:  # a funnel step (e.g. the page after the lead form) the sample never saved
                return 404, html, NOT_RECORDED
            return 200, html, self.rewrite(self.decoy_html)
        recorded = self.har.lookup(f"https://{ORIGINAL_HOST}{raw_path}")
        if recorded is not None and recorded.status < 400:
            return recorded.status, recorded.headers, recorded.body
        local = self.files.get(unquote(parts.path))
        if local is not None:
            return 200, {"content-type": _guess_type(local.name)}, local.read_bytes()
        return 404, {"content-type": "text/plain"}, b"not in the replay"

    def fallback(self, url: str) -> Recorded | None:
        """A recorded answer for another host, so the browser never has to go out."""
        host = urlsplit(url).hostname or ""
        if host == ORIGINAL_HOST:
            return None  # the live site: never answered, the replay server stands in for it
        recorded = self.har.lookup(url)
        return recorded if recorded is not None and recorded.status < 400 else None


class _Handler(BaseHTTPRequestHandler):
    case: Case61

    def log_message(self, *args) -> None:  # quiet
        pass

    def _reply(self, method: str) -> None:
        if method == "POST":
            length = int(self.headers.get("content-length") or 0)
            self.rfile.read(min(length, 1 << 20))
        status, headers, body = self.case.answer(method, self.path)
        self.send_response(status)
        for name, value in headers.items():
            if name.lower() not in _HOP_HEADERS:
                self.send_header(name, value)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:
        self._reply("GET")

    def do_HEAD(self) -> None:
        self._reply("HEAD")

    def do_POST(self) -> None:
        self._reply("POST")


@dataclass
class ReplayServer:
    case: Case61
    server: ThreadingHTTPServer
    thread: threading.Thread

    @property
    def ad_url(self) -> str:
        return self.case.ad_url

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def serve_case61(case_dir: Path, port: int = 0) -> ReplayServer:
    case = Case61.load(case_dir)
    handler = type("Case61Handler", (_Handler,), {"case": case})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    case.port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="victim-replay")
    thread.start()
    return ReplayServer(case, server, thread)


def quote_path(path: str) -> str:
    return quote(path, safe="/")
