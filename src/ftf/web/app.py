"""The web app: a JSON API over the store and the background worker, plus the built screens.

Run it with ``ftf serve``. The screens are a React app in ``frontend/``; when its build
(``frontend/dist``) is present it is served from ``/``, so one port carries everything.
"""

from __future__ import annotations

import io
import json
import os
import threading
from pathlib import Path
from typing import Annotated

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile as StarletteUpload

from ..config import get_settings
from ..corpus import ensure_seeded
from ..corpus_report import export_files, export_zip
from ..grouping import build_groups
from ..intake import ANALYSIS_LOCK, IntakeError
from ..jobs import Job, check_link, link_case, save_files, worker_for
from ..library import Library, build_library
from ..llm_checks import new_steps
from ..method_settings import apply_saved
from ..processing import item_processing
from ..registry import load_plugins
from ..reporting import build_pair_report, render_pair_report
from ..store import Store
from . import library_report, views
from .inspection import cleaner_for, comparison_sources
from .inspection_routes import router as inspection_router
from .probe import probe_router
from .victim import victim_router

MAX_UPLOAD_FILES = 50_000  # parts in one upload: a saved site is hundreds of files


class LibraryCache:
    """The last complete Library, rebuilt only while no analysis runs so it is never torn."""

    def __init__(self, db_path: Path, data_dir: Path, worker):
        self.db_path, self.data_dir, self.worker = db_path, data_dir, worker
        self._lock = threading.Lock()
        self._generation: int | None = None
        self.library: Library | None = None

    def get(self) -> Library | None:
        with self._lock:
            if self.library is not None and self._generation == self.worker.generation:
                return self.library
            if not ANALYSIS_LOCK.acquire(blocking=False):
                return self.library  # an analysis is running: keep showing the last one
            try:
                generation = self.worker.generation
                store = Store(self.db_path)
                try:
                    groups = build_groups(store, persist_funnel=False)
                    self.library = build_library(store, groups, self.data_dir, self.library)
                finally:
                    store.close()
            finally:
                ANALYSIS_LOCK.release()
            self._generation = generation
            return self.library


def job_payload(job: Job) -> dict:
    return {
        "id": job.job_id,
        "names": job.names,
        "status": job.status,
        "stage": job.stage_name,
        "error": job.error,
        "createdAt": job.created_at,
        "kind": job.kind,
    }


def create_app() -> FastAPI:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    worker = worker_for(settings.db_path, settings.data_dir)
    store = Store(settings.db_path)
    try:
        ensure_seeded(store)  # with FTF_SEED_CORPUS=1, an empty store gets the shipped corpus
    finally:
        store.close()
    load_plugins()
    if ANALYSIS_LOCK.acquire(blocking=False):
        try:
            apply_saved()
        finally:
            ANALYSIS_LOCK.release()
    cache = LibraryCache(settings.db_path, settings.data_dir, worker)
    cards: dict[tuple[str, float], bytes] = {}

    app = FastAPI(title="Follow the Fake", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.include_router(inspection_router(settings))

    def library_or_none() -> Library | None:
        return cache.get()

    def patterns_ready() -> bool:
        from ..patterns.library import PatternLibraryStore

        version = PatternLibraryStore(settings.data_dir / "patterns/library").latest_version()
        return bool(version and version.patterns)

    def item_or_404(view_id: str):
        library = library_or_none()
        item = library.get(view_id) if library else None
        if item is None:
            raise HTTPException(404, "No such item in the library")
        return library, item

    @app.get("/api/meta")
    def meta() -> dict:
        return views.explain_payload()

    @app.get("/api/library")
    def library() -> dict:
        lib = library_or_none()
        payload = (
            views.library_payload(lib)
            if lib
            else {
                "items": [],
                "cases": 0,
                "linked": 0,
                "map": {
                    "nodes": [],
                    "edges": [],
                    "campaignWhy": {},
                    "repeats": [],
                    "journeys": {},
                },
            }
        )
        jobs = [job_payload(j) for j in reversed(list(worker.jobs.values()))]
        tracked = worker.item_checks_snapshot()
        reader = Store(settings.db_path)
        try:
            for row in payload["items"]:
                view = reader.get_view(row["id"])
                row["processing"] = item_processing(
                    view,
                    reader.observations(view.view_id),
                    tracked.get(view.view_id),
                    patterns_available=patterns_ready(),
                )
        finally:
            reader.close()
        return {
            **payload,
            "generation": worker.generation,
            "busy": bool(worker.active()),
            "jobs": [j for j in jobs if j["status"] in ("queued", "running", "failed")],
            "llmRun": worker.last_llm_run,
            "checkSteps": new_steps(),
        }

    @app.post("/api/library/llm-checks", status_code=202)
    def start_llm_checks(skipped: Annotated[list[str] | None, Body()] = None) -> dict:
        reader = Store(settings.db_path)
        try:
            if not any(not v.meta.get("shipped") for v in reader.views()):
                raise HTTPException(400, "Add source material before running LLM checks.")
        finally:
            reader.close()
        try:
            return job_payload(worker.submit_llm(skipped))
        except IntakeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/library/llm-checks/{job_id}/skip/{step_id}")
    def skip_llm_step(job_id: str, step_id: str) -> dict:
        try:
            worker.skip_llm_step(job_id, step_id)
        except IntakeError as exc:
            raise HTTPException(409, str(exc)) from exc
        return worker.last_llm_run

    @app.get("/api/campaigns")
    def campaigns() -> dict:
        lib = library_or_none()
        payload = (
            views.campaigns_payload(lib)
            if lib
            else {"count": "", "campaigns": [], "kits": [], "unlinked": "", "repeats": ""}
        )
        return {**payload, "generation": worker.generation, "busy": bool(worker.active())}

    @app.get("/api/campaigns/{campaign_id}")
    def campaign(campaign_id: str) -> dict:
        lib = library_or_none()
        found = lib.campaigns.get(campaign_id) if lib else None
        if found is None or not found.name:
            raise HTTPException(404, "No such campaign")
        return views.campaign_detail(lib, found)

    @app.get("/api/kits/{kit_id}")
    def kit(kit_id: str) -> dict:
        lib = library_or_none()
        found = lib.kits.get(kit_id) if lib else None
        if found is None:
            raise HTTPException(404, "No such kit")
        return views.kit_detail(lib, found)

    @app.get("/api/items/{view_id}")
    def item(view_id: str) -> dict:
        lib, found = item_or_404(view_id)
        reader = Store(settings.db_path)
        try:
            view = reader.get_view(view_id)
            processing = item_processing(
                view,
                reader.observations(view_id),
                worker.item_checks_snapshot().get(view_id),
                patterns_available=patterns_ready(),
            )
        finally:
            reader.close()
        video_runs = [
            j for j in worker.jobs.values() if j.kind == "video" and j.view_ids == [view_id]
        ]
        video_run = video_runs[-1] if video_runs else None
        return {
            **views.detail(lib, found),
            "processing": processing,
            "busy": bool(worker.active()),
            "videoAnalysis": {
                **job_payload(video_run),
                "steps": video_run.steps,
                "results": video_run.results,
                "issues": video_run.issues,
            }
            if video_run
            else None,
        }

    @app.post("/api/items/{view_id}/checks", status_code=202)
    def run_item_checks(view_id: str, checks: Annotated[list[str] | None, Body()] = None) -> dict:
        try:
            return job_payload(worker.submit_item_checks(view_id, checks))
        except KeyError as exc:
            raise HTTPException(404, "No such item in the library") from exc
        except IntakeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/items/{view_id}/video-analysis", status_code=202)
    def analyze_video(view_id: str) -> dict:
        try:
            return job_payload(worker.submit_video(view_id))
        except KeyError as exc:
            raise HTTPException(404, "No such item in the library") from exc
        except IntakeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/items/{view_id}/video-analysis/cancel")
    def cancel_video(view_id: str) -> dict:
        active = next(
            (j for j in worker.active() if j.kind == "video" and j.view_ids == [view_id]), None
        )
        if active is None:
            raise HTTPException(409, "No active video analysis for this item.")
        worker.scheduler.cancel(active.job_id)
        return job_payload(active)

    @app.get("/api/pictures/{view_id}")
    def item_picture(view_id: str, size: Annotated[str, Query(pattern="^(card|full)$")] = "card"):
        _, found = item_or_404(view_id)
        if not found.thumbnail:
            raise HTTPException(404, "This item has no picture")
        path = Path(found.thumbnail)
        if size == "full":
            return FileResponse(path)
        key = (str(path), path.stat().st_mtime)
        if key not in cards:
            with Image.open(path) as im:
                im = im.convert("RGB")
                im = im.crop((0, 0, im.width, min(im.height, int(im.width / 1.6))))
                im.thumbnail((480, 480))
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=72)
            cards[key] = buf.getvalue()
        return Response(
            cards[key], media_type="image/jpeg", headers={"Cache-Control": "max-age=300"}
        )

    @app.post("/api/material")
    async def add_material(request: Request) -> dict:
        # A saved site is hundreds of files; Starlette's default cap is 1000 parts.
        form = await request.form(max_files=MAX_UPLOAD_FILES, max_fields=MAX_UPLOAD_FILES)
        try:
            files = [f for f in form.getlist("files") if isinstance(f, StarletteUpload)]
            links = [str(x).strip() for x in form.getlist("links") if str(x).strip()]
            return await run_in_threadpool(submit_material, files, links)
        finally:
            await form.close()

    def submit_material(files: list, links: list[str]) -> dict:
        if not (files or links):
            raise HTTPException(400, "Add a link or choose files first.")
        try:
            # http(s) only and never the server's own networks (HAC-45); the capture checks again.
            links = [check_link(x) for x in links]
            if files:
                path = save_files(((f.filename or "file", f.file) for f in files), worker.uploads)
            else:
                path = link_case(links[0], worker.uploads)
        except IntakeError as exc:
            raise HTTPException(400, str(exc).capitalize() + ".") from exc
        # Name what the person added: a folder by its own name, a loose file by its file name.
        tops = [(f.filename or "file").split("/", 1) for f in files]
        names = links + list(dict.fromkeys(t[0] + "/" if len(t) > 1 else t[0] for t in tops))
        return job_payload(worker.submit(path, names, links))

    @app.get("/api/compare/{a}/{b}")
    def compare(a: str, b: str, format: str = "view"):
        item_or_404(a)
        item_or_404(b)
        if not ANALYSIS_LOCK.acquire(blocking=False):
            raise HTTPException(409, "Available when the current analysis finishes.")
        try:
            reader = Store(settings.db_path)
            try:
                report = build_pair_report(reader, a, b)
                sources = comparison_sources(reader, report) if format == "view" else {}
                clean_a, clean_b = cleaner_for(reader, a), cleaner_for(reader, b)
            finally:
                reader.close()
        finally:
            ANALYSIS_LOCK.release()
        markdown = render_pair_report(report)
        if format == "md":
            return Response(
                markdown,
                media_type="text/markdown; charset=utf-8",
                headers={"Content-Disposition": 'attachment; filename="comparison.md"'},
            )
        if format == "json":
            return Response(
                json.dumps(report, ensure_ascii=False, indent=2),
                media_type="application/json",
                headers={"Content-Disposition": 'attachment; filename="comparison.json"'},
            )
        shared_refs = {
            ref
            for layer in report["layers"]
            for shared in layer["shared"]
            for refs in shared["sources"].values()
            for ref in refs
        }
        return {
            "markdown": clean_b(clean_a(markdown)),
            "sources": sources,
            "sharedRefs": sorted(shared_refs),
        }

    @app.get("/api/report/{name}")
    def report(name: str):
        if name not in ("follow-the-fake-export.zip", "corpus-report.md"):
            raise HTTPException(404)
        if not ANALYSIS_LOCK.acquire(blocking=False):
            raise HTTPException(409, "Available when the current analysis finishes.")
        try:
            reader = Store(settings.db_path)
            try:
                files = export_files(reader)
            finally:
                reader.close()
        finally:
            ANALYSIS_LOCK.release()
        if name.endswith(".md"):
            return Response(
                files["report.md"],
                media_type="text/markdown; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{name}"'},
            )
        return Response(
            export_zip(files),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.get("/api/library/report.{kind}")
    def library_report_file(kind: str):
        """The state of the whole library: `pdf` to keep, `html` or `json` to look at."""
        if kind not in ("pdf", "html", "json"):
            raise HTTPException(404)
        # Counted between analyses, so the library and the store describe the same moment.
        if not ANALYSIS_LOCK.acquire(blocking=False):
            raise HTTPException(409, "Available when the current analysis finishes.")
        try:
            lib = library_or_none()
            reader = Store(settings.db_path)
            try:
                method = library_report.build_method(reader)
            finally:
                reader.close()
        finally:
            ANALYSIS_LOCK.release()
        stats = library_report.build_stats(
            lib if lib else Library(cases=[], journeys={}, links={}), method
        )
        if kind == "json":
            return stats
        page = library_report.render_html(stats)
        if kind == "html":
            return Response(page, media_type="text/html; charset=utf-8")
        try:
            pdf = library_report.to_pdf(page)
        except library_report.PdfUnavailable as exc:
            raise HTTPException(
                503, f"The PDF needs Chromium (`playwright install chromium`): {exc}"
            ) from exc
        name = f"library-report-{stats['generated_at'][:10]}.pdf"
        return Response(
            pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    app.include_router(probe_router(settings.data_dir, settings.db_path, worker.invalidate))
    app.include_router(victim_router(settings.victim_dir, settings.db_path, worker.invalidate))

    dist = Path(os.environ.get("FTF_WEB_DIST", "frontend/dist"))
    if not (dist / "index.html").is_file():

        @app.get("/", include_in_schema=False)
        def not_built() -> Response:
            return Response(
                "The screens are not built yet: run `npm ci && npm run build` in frontend/, "
                "then restart `ftf serve`. The API is at /api/docs.",
                media_type="text/plain",
                status_code=503,
            )

        return app

    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def screens(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        target = (dist / path).resolve()
        if path and target.is_file() and target.is_relative_to(dist.resolve()):
            return FileResponse(target)
        return FileResponse(dist / "index.html")  # the app routes everything else

    return app
