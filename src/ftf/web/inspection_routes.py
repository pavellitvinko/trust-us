"""Small HTTP adapters for stored evidence and local media inspection."""

from contextlib import contextmanager

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..store import Store
from . import inspection, media


class PivotRequest(BaseModel):
    viewId: str = Field(max_length=256)
    ref: str = Field(pattern=r"^[0-9a-f]{24}$")
    allNamespaces: bool = False


def router(settings) -> APIRouter:
    routes = APIRouter()

    @contextmanager
    def reader(view_id):
        store = Store(settings.db_path)
        try:
            inspection.get_view(store, view_id)
            yield store
        finally:
            store.close()

    @routes.get("/api/items/{view_id}/evidence")
    def evidence(view_id: str):
        with reader(view_id) as store:
            return {"rows": inspection.evidence_rows(store, view_id)}

    @routes.get("/api/items/{view_id}/evidence/{ref}")
    def source(view_id: str, ref: str):
        with reader(view_id) as store:
            return inspection.source_payload(store, view_id, ref)

    @routes.get("/api/items/{view_id}/artifacts/{ref}")
    def artifact(view_id: str, ref: str, preview: bool = False):
        with reader(view_id) as store:
            path, found = inspection.artifact_path(store, view_id, ref)
        allowed = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"}
        if preview and found.media_type not in allowed:
            raise HTTPException(400, "This artifact is available as a download only")
        return FileResponse(
            path,
            filename=path.name,
            media_type=found.media_type if preview else "application/octet-stream",
            content_disposition_type="inline" if preview else "attachment",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @routes.post("/api/lookup")
    def pivot(body: PivotRequest):
        with reader(body.viewId) as store:
            return inspection.pivot_payload(store, body.viewId, body.ref, body.allNamespaces)

    @routes.get("/api/items/{view_id}/media")
    def video_metadata(view_id: str):
        with reader(view_id) as store:
            return media.metadata(store, view_id, settings.data_dir)

    @routes.get("/api/items/{view_id}/media/video")
    def video(view_id: str, prepared: bool = False):
        with reader(view_id) as store:
            path = media.video_path(store, view_id)
        if prepared:
            path = media.converted_path(path, settings.data_dir)
            if not path.is_file():
                raise HTTPException(404, "Prepare compatible playback first")
        return FileResponse(
            path,
            media_type="video/mp4" if prepared else None,
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @routes.post("/api/items/{view_id}/media/prepare")
    def prepare(view_id: str):
        with reader(view_id) as store:
            path = media.video_path(store, view_id)
        media.prepare(path, settings.data_dir)
        return {"prepared": True}

    @routes.get("/api/items/{view_id}/media/frame")
    def frame(view_id: str, seconds: float = Query(ge=0, le=86400, allow_inf_nan=False)):
        with reader(view_id) as store:
            path = media.video_path(store, view_id)
        return FileResponse(
            media.frame(path, settings.data_dir, round(seconds, 3)), media_type="image/jpeg"
        )

    @routes.get("/api/items/{view_id}/media/thumbnail")
    def thumbnail(view_id: str):
        with reader(view_id) as store:
            info = media.metadata(store, view_id, settings.data_dir)
            if not info["frames"]:
                raise HTTPException(404, info["error"] or "No video frame is available")
            path = media.video_path(store, view_id)
        # Use the same timestamp and file as the first keyframe on the item page.
        return FileResponse(
            media.frame(path, settings.data_dir, info["frames"][0]["seconds"]),
            media_type="image/jpeg",
        )

    return routes
