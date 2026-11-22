"""The Probe agent API: queue a job for the sandbox, follow it, add its result.

Everything in a job's status was written inside the sandbox, so it is reshaped here and
never trusted for its types.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..intake import IntakeError, run_stages
from ..probe.jobs import (
    JOB_ID,
    PROBE_NAMES,
    create_job,
    importable_run,
    job_state,
    list_jobs,
    refresh_analysis,
    relay_confirmations,
    sandbox_root,
    worker_alive,
)
from ..probe.mailbox import watch_resend
from ..probe.persona import ledger_path
from ..probe.policy import ProbeRefused
from ..store import Store

log = logging.getLogger(__name__)
RELAY_EVERY = 5  # seconds between looks at the inbox while a run waits for mail


class ProbeRequest(BaseModel):
    url: str = ""
    level: int = 2
    live: bool = False
    fake: bool = True


def _as_list(value) -> list:
    return value if isinstance(value, list) else []


def job_view(job: dict, added: set[str]) -> dict:
    findings = [
        {
            "found": PROBE_NAMES.get(str(f.get("ns")), str(f.get("ns"))),
            "value": str(f.get("value")),
            "page": str(f.get("where")),
        }
        for f in _as_list(job.get("findings"))
        if isinstance(f, dict)
    ]
    return {
        "id": str(job["id"]),
        "target": "Fake test site" if job.get("fake") else str(job.get("url", "")),
        "level": job.get("level"),
        "state": str(job.get("state")),
        "created": str(job.get("created", "")),
        "error": str(job["error"])[:300] if job.get("error") else None,
        "steps": [str(s) for s in _as_list(job.get("steps"))],
        "pages": len(_as_list(job.get("pages"))),
        "registered": job.get("registered") is True,
        "loggedIn": job.get("logged_in") is True,
        "emailConfirmed": job.get("email_confirmed") is True,
        "checkoutSeen": job.get("checkout_seen") is True,
        "findings": findings,
        "added": job["id"] in added,
    }


def probe_router(data_dir: Path, db_path: Path, on_change: Callable[[], None]) -> APIRouter:
    router = APIRouter(prefix="/api/probe")
    root = sandbox_root(data_dir)
    resend_key = os.environ.get("FTF_RESEND_API_KEY", "").strip()
    mail_domain = os.environ.get("FTF_PROBE_MAIL_DOMAIN", "").strip()

    def relay_loop() -> None:
        while True:
            try:
                relay_confirmations(root, resend_key, mail_domain)
            except (OSError, ValueError, KeyError) as exc:
                log.warning("could not read the bait inbox: %s", type(exc).__name__)
            time.sleep(RELAY_EVERY)

    if resend_key and mail_domain:
        threading.Thread(target=relay_loop, name="probe-mail-relay", daemon=True).start()

    def added_runs() -> set[str]:
        store = Store(db_path)
        try:
            return {i.incident_id for i in store.incidents()}
        finally:
            store.close()

    @router.get("")
    def overview() -> dict:
        jobs = list_jobs(root)
        added = added_runs() if any(j["state"] == "done" for j in jobs) else set()
        return {
            "workerAlive": worker_alive(root),
            "inboxEnabled": bool(os.environ.get("FTF_RESEND_API_KEY", "").strip()),
            "jobs": [job_view(j, added) for j in jobs],
        }

    @router.post("")
    def start(request: ProbeRequest) -> dict:
        if not worker_alive(root):
            raise HTTPException(409, "The isolated worker is not running.")
        try:
            job_id = create_job(
                root, request.url, request.level, live=request.live, fake=request.fake
            )
        except (ProbeRefused, ValueError) as exc:
            raise HTTPException(400, str(exc).capitalize() + ".") from exc
        return {"id": job_id}

    @router.post("/inbox")
    def inbox() -> dict:
        key = os.environ.get("FTF_RESEND_API_KEY", "").strip()
        if not key:
            raise HTTPException(400, "No Resend key is set.")
        try:
            report = watch_resend(key, ledger_path(data_dir))
        except OSError as exc:
            raise HTTPException(502, f"Could not read the inbox: {exc}") from exc
        if report["new"]:
            store = Store(db_path)
            try:
                refresh_analysis(store)
            finally:
                store.close()
            on_change()
        return {
            "messages": report["messages"],
            "attributed": report["attributed"],
            "new": report["new"],
            "resold": [
                {
                    "site": str(r["site"]),
                    "sender": str(r["sender_domain"]),
                    "subject": str(r["subject"]),
                }
                for r in report["resold"]
            ],
        }

    @router.post("/{job_id}/add")
    def add(job_id: str) -> dict:
        if not JOB_ID.match(job_id) or job_state(root, job_id).get("state") != "done":
            raise HTTPException(404, "This run has no finished result.")
        if job_id in added_runs():
            return {"added": True}
        store = Store(db_path)
        try:
            run_stages(store, importable_run(root, job_id), enrich=True)
        except (ProbeRefused, IntakeError) as exc:
            raise HTTPException(400, str(exc).capitalize() + ".") from exc
        finally:
            store.close()
        on_change()
        return {"added": True}

    return router
