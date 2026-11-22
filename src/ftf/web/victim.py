"""The victim visitor API: list runs, read one, play its video, start the demo, pause or stop.

A run is `ftf victim run` in its own process with a visible browser window, so it can only
be started from here when the app itself runs on a machine with a screen. Inside Docker
the screen still lists and plays finished runs from the shared data folder, and says which
command to run on the host.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..store import Store
from ..victim.control import write_command
from ..victim.persona import load_personas
from ..victim.rules import STOP_WORDS

RUN_ID = re.compile(r"^victim-\d{8}-\d{6}-[0-9a-f]{4}$")
# Files the screen may fetch from a run folder: the video, its captions and the screenshots.
SERVABLE = re.compile(
    r"^(run_files/recording/clip-\d{2}\.(webm|vtt)|run_files/steps/step-\d{3}(-after|-stop)?\.png"
    r"|stage-\d{2}/screenshot\.png)$"
)
MEDIA_TYPES = {".webm": "video/webm", ".vtt": "text/vtt; charset=utf-8", ".png": "image/png"}
DEMO_COMMAND = "uv run ftf victim demo"
LIVE_FRESH_S = 30  # a live.json older than this, with no run.json, is a run that died


class StartRequest(BaseModel):
    persona: str = "retiree-income"


def in_container() -> bool:
    return Path("/.dockerenv").exists() or os.environ.get("FTF_IN_DOCKER") == "1"


def _json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _jsonl(path: Path) -> list[dict[str, Any]]:
    out = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def _status(files: Path, run: dict[str, Any]) -> str:
    if run:
        return "finished"
    live = files / "live.json"
    state = _json(live).get("status", "starting")
    try:
        stale = time.time() - live.stat().st_mtime > LIVE_FRESH_S
    except OSError:
        return "starting"
    # The agent writes live.json at every step; a long-silent one without run.json is dead,
    # unless it is waiting for a person or paused.
    if stale and state not in {"paused", "waiting_for_person"}:
        return "ended without a record"
    return str(state)


def _host(url: str) -> str:
    return urlsplit(url or "").hostname or ""


def _shot(rel: Any) -> str | None:
    return rel if isinstance(rel, str) and SERVABLE.match(rel) else None


def run_summary(root: Path) -> dict[str, Any]:
    files = root / "run_files"
    run = _json(files / "run.json")
    live = _json(files / "live.json")
    rec = run.get("recording") or {}
    persona = run.get("persona") or {}
    stop = str(run.get("stop_reason") or "")
    return {
        "id": root.name,
        "status": _status(files, run),
        "persona": persona.get("id") or str(live.get("persona", "")).split("@")[0],
        "startedAt": run.get("started_at") or "",
        "endedAt": run.get("ended_at") or "",
        "startHost": _host(run.get("start_url", "")),
        "replay": bool(run.get("replay")),
        "steps": int(run.get("steps") or live.get("step") or 0),
        "pages": len(run.get("stages") or []),
        "shape": [str(x) for x in run.get("funnel_shape") or []],
        "stop": stop,
        "stopWords": STOP_WORDS.get(stop, stop.replace("_", " ")),
        "stopDetail": str(run.get("stop_detail") or ""),
        "video": bool(rec.get("clips")),
    }


def run_detail(root: Path, store: Store | None) -> dict[str, Any]:
    files = root / "run_files"
    run = _json(files / "run.json")
    live = _json(files / "live.json")
    out = run_summary(root)
    steps = []
    for s in _jsonl(files / "steps.jsonl"):
        if s.get("actor") == "agent":
            steps.append(
                {
                    "step": s.get("step"),
                    "actor": "agent",
                    "page": s.get("stage"),
                    "action": str(s.get("chosen") or ""),
                    "kind": str(s.get("chosen_kind") or ""),
                    "reason": str(s.get("reason") or ""),
                    "typed": s.get("typed"),
                    "outcome": str(s.get("outcome") or ""),
                    "at": str(s.get("at") or ""),
                    "screenshot": _shot(s.get("screenshot")),
                    "screenshotAfter": _shot(s.get("screenshot_after")),
                }
            )
        else:
            steps.append(
                {
                    "step": s.get("step"),
                    "actor": str(s.get("actor") or "person"),
                    "action": str(s.get("event") or s.get("detail") or ""),
                    "reason": "",
                    "at": str(s.get("at") or ""),
                }
            )
    pages = []
    for st in run.get("stages") or []:
        arrived = st.get("arrived_from") or {}
        pages.append(
            {
                "index": st.get("index"),
                "url": str(st.get("url") or ""),
                "host": _host(st.get("url", "")),
                "kinds": [str(k) for k in st.get("kinds") or []],
                "how": str(arrived.get("how") or ""),
                "step": arrived.get("step"),
                "redirects": [str(r) for r in arrived.get("http_redirects") or []],
                "screenshot": _shot(f"{st.get('dir')}/screenshot.png"),
            }
        )
    forms = []
    for f in _jsonl(files / "forms.jsonl"):
        sent_to = [x for x in f.get("sent_to") or [] if isinstance(x, dict)]
        forms.append(
            {
                "page": f.get("stage"),
                "step": f.get("step"),
                "values": {str(k): str(v) for k, v in (f.get("values_given") or {}).items()},
                "sent": bool(f.get("sent")),
                "sentTo": [
                    {
                        "method": str(x.get("method", "")),
                        "host": _host(x.get("url", "")),
                        "path": urlsplit(str(x.get("url", ""))).path,
                    }
                    for x in sent_to
                ],
                "stop": str(f.get("stop_detail") or ""),
            }
        )
    chat = [
        {
            "who": str(m.get("who") or ""),
            "text": str(m.get("text") or ""),
            "improvised": bool(m.get("improvised")),
        }
        for m in _jsonl(files / "chat.jsonl")
    ]
    clips = [
        {"video": c["video"], "captions": c.get("captions")}
        for c in (run.get("recording") or {}).get("clips", [])
        if isinstance(c, dict) and _shot(c.get("video"))
    ]
    items = []
    if store is not None:
        items = [{"id": v.view_id, "label": v.label} for v in store.views(root.name)]
    out.update(
        steps=steps,
        pages=pages,
        pageCount=len(pages),
        forms=forms,
        chat=chat,
        clips=clips,
        size=(run.get("recording") or {}).get("size"),
        email=str(run.get("email") or ""),
        phone=str(run.get("phone") or ""),
        startUrl=str(run.get("start_url") or ""),
        items=items,
        live={
            k: str(live.get(k) or "")
            for k in ("status", "step", "stage", "url", "next", "reason", "notice")
        },
    )
    return out


def victim_router(victim_dir: Path, db_path: Path) -> APIRouter:
    router = APIRouter(prefix="/api/victim")

    def run_root(run_id: str) -> Path:
        if not RUN_ID.match(run_id):
            raise HTTPException(404, "No such run")
        root = victim_dir / run_id
        if not (root / "run_files").is_dir():
            raise HTTPException(404, "No such run")
        return root

    @router.get("")
    def overview() -> dict:
        runs = (
            sorted((p for p in victim_dir.glob("victim-*") if RUN_ID.match(p.name)), reverse=True)
            if victim_dir.is_dir()
            else []
        )
        personas = [
            {"id": p.id, "summary": p.summary, "matches": p.matches}
            for p in load_personas().values()
        ]
        return {
            "runs": [run_summary(p) for p in runs[:50]],
            "personas": personas,
            "canStart": not in_container(),
            "command": DEMO_COMMAND,
        }

    @router.get("/{run_id}")
    def detail(run_id: str) -> dict:
        root = run_root(run_id)
        store = Store(db_path) if db_path.exists() else None
        try:
            return run_detail(root, store)
        finally:
            if store is not None:
                store.close()

    @router.get("/{run_id}/file/{rel:path}")
    def file(run_id: str, rel: str) -> FileResponse:
        root = run_root(run_id)
        if not SERVABLE.match(rel):
            raise HTTPException(404, "Not a file of this run")
        path = root / rel
        if path.is_symlink() or not path.is_file():
            raise HTTPException(404, "Not a file of this run")
        return FileResponse(path, media_type=MEDIA_TYPES[path.suffix])

    @router.post("/start")
    def start(request: StartRequest) -> dict:
        if in_container():
            raise HTTPException(
                409,
                "The agent opens a browser window, so it runs on the host, not in Docker. "
                f"Run: {DEMO_COMMAND}",
            )
        if request.persona not in load_personas():
            raise HTTPException(400, "Unknown persona")
        victim_dir.mkdir(parents=True, exist_ok=True)
        before = {p.name for p in victim_dir.glob("victim-*")}
        log = open(victim_dir / "last-run.log", "w", encoding="utf-8")  # noqa: SIM115
        cmd = [
            sys.executable,
            "-m",
            "ftf.cli",
            "victim",
            "demo",
            "--persona",
            request.persona,
            "--out",
            str(victim_dir),
        ]
        subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        # The run folder appears within a second or two; tell the screen which one it is.
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            new = sorted({p.name for p in victim_dir.glob("victim-*")} - before)
            if new:
                return {"id": new[-1]}
            time.sleep(0.25)
        return {"id": None}

    @router.post("/{run_id}/{command}")
    def control(run_id: str, command: str) -> dict:
        if command not in {"pause", "resume", "stop"}:
            raise HTTPException(404, "Unknown command")
        root = run_root(run_id)
        write_command(root / "run_files", command)
        return {"ok": True}

    return router
