"""Read-only evidence navigation. Captured content is returned as data, never as HTML."""

from __future__ import annotations

import hashlib
import json
import math
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException

from ..contracts import Artifact, Observation, Purpose, State
from ..corpus_report import (
    TEXT_NAMESPACES,
    _hash,
    _walk,
    is_secret_namespace,
    make_cleaner,
    sensitive_values,
)
from ..lookup import lookup
from ..models.redact import redact_json, redact_text
from ..store import Store

MAX_SOURCE = 10 << 20
CONTEXT = 3000


def evidence_ref(observation: Observation) -> str:
    body = json.dumps(observation.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(body.encode()).hexdigest()[:24]


def artifact_ref(path: str) -> str:
    return hashlib.sha256(path.encode()).hexdigest()[:24]


def get_view(store: Store, view_id: str):
    try:
        return store.get_view(view_id)
    except KeyError:
        raise HTTPException(404, "No such item") from None


def observation_for(store: Store, view_id: str, ref: str) -> Observation:
    get_view(store, view_id)
    for observation in store.observations(view_id):
        if evidence_ref(observation) == ref:
            return observation
    raise HTTPException(404, "This evidence is no longer available. Reopen the item.")


def cleaner_for(store: Store, view_id: str):
    view = get_view(store, view_id)
    # A source file can contain facts belonging to another view in the same case.
    observations = [o for v in store.views(view.incident_id) for o in store.observations(v.view_id)]
    secrets, _ = sensitive_values(observations)
    clean = make_cleaner(secrets, set())
    return lambda text: redact_text(clean(text))


def can_pivot(o: Observation) -> bool:
    return (
        o.state is State.PRESENT
        and o.purpose in (Purpose.LINKAGE, Purpose.BOTH)
        and isinstance(o.value, str | int | float)
        and not isinstance(o.value, bool)
        and bool(str(o.value).strip())
        and o.namespace not in TEXT_NAMESPACES
    )


def evidence_row(o: Observation, clean) -> dict:
    value = redact_json(_walk(o.value, clean))
    if is_secret_namespace(o.namespace):
        value = _walk(o.value, _hash)
    return {
        "ref": evidence_ref(o),
        "viewId": o.view_id,
        "namespace": o.namespace,
        "value": value,
        "state": o.state.value,
        "extractor": o.extractor,
        "version": o.version,
        "source": clean(":".join(x for x in (o.source.file, o.source.locator) if x)),
        "pivot": can_pivot(o),
    }


def evidence_rows(store: Store, view_id: str) -> list[dict]:
    clean = cleaner_for(store, view_id)
    return [evidence_row(o, clean) for o in store.observations(view_id)]


def artifact_path(store: Store, view_id: str, ref: str) -> tuple[Path, Artifact]:
    view = get_view(store, view_id)
    artifact = next(
        (a for a in store.artifacts(view.incident_id) if artifact_ref(a.path) == ref), None
    )
    if artifact is None:
        raise HTTPException(404, "No such stored artifact")
    root = Path(store.get_incident(view.incident_id).root).resolve()
    relative = Path(artifact.path)
    if relative.is_absolute() or ".." in relative.parts:
        raise HTTPException(404, "Artifact is outside the case folder")
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "Original artifact is missing or outside the case folder")
    return path, artifact


class _Tags(HTMLParser):
    """Locate source spans using the same per-tag, zero-based indexing as extraction."""

    def __init__(self, text: str):
        super().__init__(convert_charrefs=False)
        self.text = text
        self.lines = [0, *(m.end() for m in re.finditer("\n", text))]
        self.counts: dict[str, int] = {}
        self.spans: dict[tuple[str, int], tuple[int, int]] = {}
        self.open: list[tuple[str, int, int]] = []

    def position(self) -> int:
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        index = self.counts.get(tag, 0)
        self.counts[tag] = index + 1
        start = self.position()
        self.spans[tag, index] = (start, start + len(self.get_starttag_text()))
        if tag not in {"input", "img", "meta", "link", "base", "br", "hr", "source", "wbr"}:
            self.open.append((tag, index, start))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for n in range(len(self.open) - 1, -1, -1):
            name, index, start = self.open[n]
            if name == tag:
                end = self.text.find(">", self.position())
                self.spans[tag, index] = (start, end + 1 if end >= 0 else len(self.text))
                del self.open[n:]
                break


def _excerpt(text: str, span: tuple[int, int] | None, clean) -> dict:
    start, end = span or (0, 0)
    before = clean(text[max(0, start - CONTEXT) : start])
    match = clean(text[start : min(end, start + CONTEXT * 2)])
    after = clean(text[end : end + CONTEXT] if span else text[: CONTEXT * 2])
    return {
        "before": before,
        "highlight": match,
        "after": after,
        "truncated": (start > CONTEXT or end - start > CONTEXT * 2 or len(text) > end + CONTEXT),
    }


def _har_location(data: dict, locator: str) -> tuple[str, bool]:
    match = re.match(r"har:entries\[(\d+)\](.*)", locator)
    if not match:
        return json.dumps(data, ensure_ascii=False, indent=2), False
    entry = data["log"]["entries"][int(match[1])]
    node = entry
    suffix = match[2]
    # Only a fixed data path grammar; locators are never evaluated as code.
    tokens = re.findall(r"\.([\w]+)|\[(\d+)\]", suffix)
    exact = "".join(f".{key}" if key else f"[{index}]" for key, index in tokens) == suffix
    if exact:
        try:
            for key, index in tokens:
                node = node[key] if key else node[int(index)]
        except (KeyError, IndexError, TypeError):
            node, exact = entry, False
    else:
        exact = False
    return json.dumps(redact_json(node if exact else entry), ensure_ascii=False, indent=2), exact


def source_payload(store: Store, view_id: str, ref: str) -> dict:
    o = observation_for(store, view_id, ref)
    clean = cleaner_for(store, view_id)
    result = {
        "record": evidence_row(o, clean),
        "before": "",
        "highlight": "",
        "after": "",
        "resolution": "unavailable",
        "message": "Original source is unavailable.",
        "download": None,
        "image": None,
        "videoTime": None,
        "truncated": False,
    }
    if not o.source.file:
        return result
    try:
        path, artifact = artifact_path(store, view_id, artifact_ref(o.source.file))
    except HTTPException:
        return result
    url = f"/api/items/{quote(view_id, safe='')}/artifacts/{artifact_ref(artifact.path)}"
    result["download"] = url
    locator = o.source.locator or ""
    if artifact.media_type.startswith("image/") and path.suffix.lower() != ".svg":
        result.update(image=url + "?preview=true", message="Original image.", resolution="file")
        return result
    view = get_view(store, view_id)
    if view.kind.value == "video" and o.source.file == view.primary:
        match = re.search(r"video:t=(\d+(?:\.\d+)?)s", locator)
        seconds = float(match[1]) if match else 0
        result.update(
            videoTime=seconds if math.isfinite(seconds) else 0,
            resolution="file",
            message="Open this video on its item page.",
        )
        return result
    if artifact.media_type.startswith(("image/", "video/")):
        result["message"] = "Download the original artifact to inspect this format."
        return result
    with path.open("rb") as stream:
        content = stream.read(MAX_SOURCE + 1)
    limited = len(content) > MAX_SOURCE
    text = content[:MAX_SOURCE].decode("utf-8", errors="replace")
    span = None
    resolution = "region"
    if locator.startswith("har:") and not limited:
        try:
            text, exact = _har_location(json.loads(text), locator)
            if exact:
                span, resolution = (0, len(text)), "exact"
        except (ValueError, KeyError, IndexError, TypeError):
            pass
    # Redact the complete text before cutting context, so a cut cannot expose part of a secret.
    else:
        text = clean(text)
    if match := re.match(r"html:(\w+)\[(\d+)\]", locator):
        parser = _Tags(text)
        parser.feed(text)
        span = parser.spans.get((match[1], int(match[2])))
        resolution = "exact" if span and match.end() == len(locator) else "region"
    elif locator.startswith("html:comment[saved from url]"):
        if match := re.search(r"<!--\s*saved from url[\s\S]*?-->", text, re.IGNORECASE):
            span, resolution = match.span(), "region"
    elif locator.startswith("manifest.json:") and not limited:
        try:
            text = json.dumps(
                json.loads(text)[locator.split(":", 1)[1]], ensure_ascii=False, indent=2
            )
            span, resolution = (0, len(text)), "exact"
        except (ValueError, KeyError, TypeError):
            pass
    # HAR values need structural redaction before serialisation; clean the whole result too.
    if locator.startswith("har:"):
        text = clean(text)
        if resolution == "exact":
            span = (0, len(text))
    result.update(_excerpt(text, span, lambda value: value))
    result.update(
        resolution=resolution,
        message=(
            "Highlighted cited location."
            if resolution == "exact"
            else "Exact location unavailable; showing source context and the recorded locator."
        ),
    )
    result["truncated"] |= limited
    return result


def pivot_payload(store: Store, view_id: str, ref: str, all_namespaces: bool) -> dict:
    o = observation_for(store, view_id, ref)
    if not can_pivot(o):
        raise HTTPException(400, "This observation is not a present scalar identifier")
    clean = cleaner_for(store, view_id)
    # Preserve the store's canonical representation for non-string scalars too.
    from ..store import value_key

    hits = lookup(store, value_key(o.value), None if all_namespaces else [o.namespace])
    return {
        "record": evidence_row(o, clean),
        "hits": [
            {
                "id": h.view_id,
                "title": clean(h.label),
                "case": h.incident_id,
                "namespaces": h.namespaces,
                "exact": h.exact,
                "origin": h.view_id == view_id,
            }
            for h in hits
        ],
    }


def comparison_sources(store: Store, report: dict) -> dict:
    rows = {}
    for side, view in report["objects"].items():
        clean = cleaner_for(store, view["view_id"])
        for i, o in enumerate(store.observations(view["view_id"]), 1):
            key = f"{side}:{i}"
            if key in report["sources"]:
                rows[key] = evidence_row(o, clean)
    return rows
