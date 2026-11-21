"""The session record (VV-25, VV-40…VV-42): one run, one incident folder.

    victim-<run-id>/
      stage-01/ dom.html screenshot.png manifest.json page.har resources/   one per stage
      stage-02/ …
      run_files/                         run-level records; a `_files` folder, so ingestion
        run.json                         treats everything here as resources, not views
        steps.jsonl navigation.jsonl forms.jsonl chat.jsonl blocks.jsonl
        session.har                      the whole session, bodies included
        steps/step-NNN.png               one screenshot per step
        perception.json material.txt     the perception fingerprint and the text it quotes

Each stage is a capture bundle (`manifest.json` + `dom.html`), so ingestion turns it into one
page view. Its manifest names the stage it came from and how (`victim.arrived_from`); the
`victim.run` extractor turns that into a funnel edge with its proof.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

RUN_FILES = "run_files"
STAGE_PREFIX = "stage-"
MAX_RESOURCE_BYTES = 5 << 20
_RESOURCE_TYPES = ("image/", "text/css", "javascript", "font/", "video/")


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").removesuffix("+00:00") + "Z"


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


@dataclass
class Stage:
    index: int
    url: str
    kind: str
    started: str  # when the action that led here began (HAR entries from here on belong to it)
    arrived_from: dict[str, Any] | None
    dir: Path
    visits: int = 1
    kinds: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.dir.name


class Recorder:
    def __init__(self, out_dir: Path, run_id: str):
        self.run_id = run_id
        self.root = Path(out_dir) / f"victim-{run_id}"
        self.files = self.root / RUN_FILES
        (self.files / "steps").mkdir(parents=True, exist_ok=True)
        self.stages: list[Stage] = []
        self._lines: dict[str, int] = {}

    # -- logs ----------------------------------------------------------------------

    def append(self, log: str, record: dict[str, Any]) -> int:
        """Append one JSON line; returns its 1-based line number (used in locators)."""
        record = {"at": now_iso(), **record}
        with open(self.files / f"{log}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._lines[log] = self._lines.get(log, 0) + 1
        return self._lines[log]

    def step_screenshot(self, page: Any, step: int, suffix: str = "") -> str | None:
        rel = f"{RUN_FILES}/steps/step-{step:03d}{suffix}.png"
        try:
            page.screenshot(path=str(self.root / rel))
        except Exception:  # noqa: BLE001 - a page mid-navigation cannot always be shot
            return None
        return rel

    # -- stages --------------------------------------------------------------------

    def stage_for(self, url: str) -> Stage | None:
        key = stage_key(url)
        return next((s for s in self.stages if stage_key(s.url) == key), None)

    def new_stage(
        self, page: Any, url: str, kind: str, started: str, arrived_from: dict[str, Any] | None
    ) -> Stage:
        index = len(self.stages) + 1
        stage = Stage(
            index=index,
            url=url,
            kind=kind,
            started=started,
            arrived_from=arrived_from,
            dir=self.root / f"{STAGE_PREFIX}{index:02d}",
            kinds=[kind],
        )
        stage.dir.mkdir(parents=True, exist_ok=True)
        self.stages.append(stage)
        with contextlib.suppress(Exception):
            page.screenshot(path=str(stage.dir / "screenshot.png"), full_page=True)
        self.snapshot(page, stage)
        self.write_manifest(stage)
        return stage

    def snapshot(self, page: Any, stage: Stage) -> None:
        """Save the stage's DOM as it is now; the last snapshot before leaving is kept."""
        with contextlib.suppress(Exception):
            (stage.dir / "dom.html").write_text(page.content(), encoding="utf-8")

    def write_manifest(self, stage: Stage, extra: dict[str, Any] | None = None) -> None:
        manifest = {
            "page_url": stage.url,
            "captured_at": stage.started,
            "capture_tool": "ftf.victim",
            "victim": {
                "run_id": self.run_id,
                "stage": stage.index,
                "stage_kind": stage.kind,
                "stage_kinds_seen": stage.kinds,
                "arrived_from": stage.arrived_from,
                **(extra or {}),
            },
        }
        (stage.dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    # -- the end -------------------------------------------------------------------

    def write_json(self, name: str, data: Any) -> None:
        (self.files / name).write_text(
            json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
        )

    def split_har(self, session_har: Path) -> None:
        """Give each stage the part of the session HAR that happened while it was current."""
        try:
            har = json.loads(session_har.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        entries = har.get("log", {}).get("entries", [])
        starts = [_parse_iso(s.started) for s in self.stages]
        buckets: list[list[dict[str, Any]]] = [[] for _ in self.stages]
        for entry in entries:
            at = _parse_iso(entry.get("startedDateTime", ""))
            index = 0
            for i, start in enumerate(starts):
                if at is not None and start is not None and at >= start:
                    index = i
            if buckets:
                buckets[index].append(entry)
        for stage, bucket in zip(self.stages, buckets, strict=True):
            part = {"log": {**har.get("log", {}), "entries": bucket}}
            (stage.dir / "page.har").write_text(
                json.dumps(part, ensure_ascii=False), encoding="utf-8"
            )
            self._write_resources(stage, bucket)

    def _write_resources(self, stage: Stage, entries: list[dict[str, Any]]) -> None:
        res = stage.dir / "resources"
        for entry in entries:
            content = entry.get("response", {}).get("content", {})
            mime = (content.get("mimeType") or "").lower()
            text = content.get("text")
            if not text or not any(t in mime for t in _RESOURCE_TYPES):
                continue
            try:
                body = (
                    base64.b64decode(text) if content.get("encoding") == "base64" else text.encode()
                )
            except (ValueError, TypeError):
                continue
            if not body or len(body) > MAX_RESOURCE_BYTES:
                continue
            url = entry.get("request", {}).get("url", "")
            base = re.sub(r"[^\w.-]", "_", Path(urlsplit(url).path).name)[:80] or "resource"
            digest = hashlib.sha256(body).hexdigest()[:10]
            res.mkdir(exist_ok=True)
            (res / f"{digest}-{base}").write_bytes(body)


_TRACKING = re.compile(
    r"^(fbclid|gclid|ttclid|msclkid|_ga|utm_visit|ref|random|t|ts|_)$", re.IGNORECASE
)


def stage_key(url: str) -> str:
    """What makes two URLs one stage: host, path and query (minus click IDs), not the fragment.

    The query matters: in case 61 the article and the chat are both `glowpixel.org/`.
    """
    parts = urlsplit(url.split("#")[0])
    query = "&".join(
        sorted(q for q in parts.query.split("&") if q and not _TRACKING.match(q.split("=")[0]))
    )
    return f"{(parts.hostname or '').lower()}{parts.path.rstrip('/') or '/'}?{query}"
