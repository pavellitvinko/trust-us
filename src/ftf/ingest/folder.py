"""Turn a folder of captured material into an incident with artifacts and views.

A view is the unit of analysis: one page, one image, or one video. A folder is only a
container; folder 2 of the sample holds two unrelated pages.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlsplit

from ..contracts import Artifact, Incident, View, ViewKind
from ..store import Store
from .har import first_party_request, load_har, main_document

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
HTML_EXT = {".html", ".htm"}
_SAVED_FROM = re.compile(r"saved from url=\(\d+\)(\S+)")
# Browsers in some locales append a "downloaded" suffix to saved resources.
_SAVE_SUFFIXES = (".pobrane", ".pobierz", ".download")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _media_type(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".har"):
        return "application/har+json"
    for suffix in _SAVE_SUFFIXES:
        name = name.removesuffix(suffix)
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def saved_from_url(html_path: Path) -> str | None:
    """The URL a browser recorded when the page was saved, if it is a real URL."""
    try:
        with open(html_path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(4096)
    except OSError:
        return None
    match = _SAVED_FROM.search(head)
    if not match:
        return None
    url = match.group(1)
    return url if (urlsplit(url).hostname or "").count(".") >= 1 else None


def _view_id(incident_id: str, key: str) -> str:
    return f"{incident_id}:{hashlib.sha1(f'{incident_id}|{key}'.encode()).hexdigest()[:10]}"


def _is_under(path: Path, dirs: set[Path]) -> bool:
    return any(d in path.parents for d in dirs)


def ingest_folder(
    root: Path, store: Store, incident_id: str | None = None
) -> tuple[Incident, list[View]]:
    """Ingest one folder as one incident. Re-ingesting replaces the previous result."""
    root = root.resolve()
    incident_id = incident_id or root.name
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.name != ".DS_Store")
    rel = lambda p: p.relative_to(root).as_posix()

    roles: dict[Path, str] = {}
    views: list[View] = []

    # 1. Capture bundles: a directory holding manifest.json + dom.html is one page view.
    bundle_dirs = {
        p.parent for p in files if p.name == "manifest.json" and (p.parent / "dom.html").is_file()
    }
    for bdir in sorted(bundle_dirs):
        har = bdir / "page.har"
        attachments = [
            bdir / n
            for n in ("screenshot.png", "page.mhtml", "manifest.json")
            if (bdir / n).is_file()
        ]
        try:
            page_url = json.loads((bdir / "manifest.json").read_text(encoding="utf-8")).get(
                "page_url"
            )
        except (OSError, ValueError):
            page_url = None
        views.append(
            View(
                view_id=_view_id(incident_id, rel(bdir / "dom.html")),
                incident_id=incident_id,
                kind=ViewKind.PAGE,
                label=f"{incident_id} · capture bundle {bdir.name}",
                primary=rel(bdir / "dom.html"),
                har=rel(har) if har.is_file() else None,
                resources_dir=rel(bdir / "resources") if (bdir / "resources").is_dir() else None,
                attachments=[rel(a) for a in attachments],
                meta={
                    "origin": "capture_bundle",
                    **({"manifest_url": page_url} if page_url else {}),
                },
            )
        )
        roles[bdir / "dom.html"] = "primary"
        if har.is_file():
            roles[har] = "har"
        for a in attachments:
            roles[a] = "attachment"

    # 2. Resource directories: "<page>_files" next to a saved page, and anything inside a bundle.
    resource_dirs = {
        p for p in root.rglob("*") if p.is_dir() and p.name.endswith("_files")
    } | bundle_dirs
    for p in files:
        if p not in roles and _is_under(p, resource_dirs):
            roles[p] = "resource"

    # 3. Saved pages.
    page_views: dict[Path, View] = {}
    for p in files:
        if p in roles or p.suffix.lower() not in HTML_EXT:
            continue
        url = saved_from_url(p)
        res_dir = p.with_name(p.stem + "_files")
        view = View(
            view_id=_view_id(incident_id, rel(p)),
            incident_id=incident_id,
            kind=ViewKind.PAGE,
            label=f"{incident_id} · {urlsplit(url).hostname if url else p.stem[:40]}",
            primary=rel(p),
            resources_dir=rel(res_dir) if res_dir.is_dir() else None,
            meta={"origin": "saved_page", **({"saved_from": url} if url else {})},
        )
        page_views[p] = view
        roles[p] = "primary"

    # 4. HAR files: attach to the page they belong to, or stand alone as a page view.
    for p in files:
        if p in roles or not p.name.lower().endswith(".har"):
            continue
        roles[p] = "har"
        entries = load_har(p)
        doc = main_document(entries)
        doc_key = "har_document"
        if doc is None:  # no HTML document captured: fall back to the busiest first-party host
            doc, doc_key = first_party_request(entries), "har_first_party"
        har_host = urlsplit(doc[1]).hostname if doc else None
        siblings = [v for q, v in page_views.items() if q.parent == p.parent and v.har is None]
        match = next(
            (
                v
                for v in siblings
                if har_host and urlsplit(v.meta.get("saved_from", "")).hostname == har_host
            ),
            None,
        )
        if match is None:
            # A page whose saved-from URL is unusable still belongs with the only HAR beside it.
            unknown = [v for v in siblings if "saved_from" not in v.meta]
            if (
                len(unknown) == 1
                and len(
                    [f for f in files if f.parent == p.parent and f.name.lower().endswith(".har")]
                )
                == 1
            ):
                match = unknown[0]
        if match is not None:
            match.har = rel(p)
            if har_host and "saved_from" not in match.meta:
                match.meta[doc_key] = doc[1]
                match.label = f"{incident_id} · {har_host}"
        else:
            views.append(
                View(
                    view_id=_view_id(incident_id, rel(p)),
                    incident_id=incident_id,
                    kind=ViewKind.PAGE,
                    label=f"{incident_id} · {har_host or p.name} (HAR only)",
                    har=rel(p),
                    meta={"origin": "har_only", **({doc_key: doc[1]} if doc else {})},
                )
            )
    views.extend(page_views.values())

    # 5. Images and videos that are not page resources. A video's metadata file rides along.
    for p in files:
        if p in roles:
            continue
        ext = p.suffix.lower()
        if ext in IMAGE_EXT:
            kind, attachments = ViewKind.IMAGE, []
        elif ext in VIDEO_EXT:
            kind = ViewKind.VIDEO
            attachments = [
                q
                for q in files
                if q.parent == p.parent and q.name.startswith(f"{p.stem}.") and q.suffix == ".json"
            ]
        else:
            continue
        roles[p] = "primary"
        for a in attachments:
            roles[a] = "attachment"
        views.append(
            View(
                view_id=_view_id(incident_id, rel(p)),
                incident_id=incident_id,
                kind=kind,
                label=f"{incident_id} · {p.name}",
                primary=rel(p),
                attachments=[rel(a) for a in attachments],
                meta={"origin": "media_file"},
            )
        )

    artifacts = [
        Artifact(
            incident_id=incident_id,
            path=rel(p),
            sha256=_sha256(p),
            size=p.stat().st_size,
            media_type=_media_type(p),
            role=roles.get(p, "other"),
        )
        for p in files
    ]
    incident = Incident(incident_id=incident_id, root=str(root), label=root.name)
    views.sort(key=lambda v: (v.kind.value, v.primary or v.har or ""))
    store.put_incident(incident, artifacts, views)
    return incident, views


def ingest_path(path: Path, store: Store) -> list[tuple[Incident, list[View]]]:
    """Ingest one incident folder, or a collection whose sub-folders are incidents."""
    path = path.resolve()
    own_files = [p for p in path.iterdir() if p.is_file() and p.name != ".DS_Store"]
    subdirs = sorted(p for p in path.iterdir() if p.is_dir() and not p.name.startswith("."))
    if own_files or not subdirs:
        return [ingest_folder(path, store)]
    return [ingest_folder(d, store) for d in subdirs]
