"""Probe jobs: the app queues a file, the worker inside the sandbox runs it.

The app gets no control over Docker. Everything the worker writes is treated as hostile.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from ..capture import capture_url
from ..config import get_settings
from .agent import ProbeResult, run_probe
from .fake_site import FakeSite
from .policy import Level, ProbeRefused, check_target, require_sandbox, site_key

JOB_ID = re.compile(r"^[0-9a-f]{32}$")
JOB_TIMEOUT = 420  # seconds
HEARTBEAT_AGE = 15  # seconds
MAX_STATUS = 2 << 20  # bytes
ACTIVE = ("queued", "running")
CONFIRM_WAIT = 150  # seconds a run waits for its activation email
DEMO_MAIL_DOMAIN = "bait.example.test"  # reserved name: no message can ever be delivered there
PROBE_NAMES = {
    "contact.iban": "Bank account (IBAN)",
    "contact.wallet": "Crypto wallet",
    "contact.email": "Email",
    "contact.telegram": "Telegram",
    "contact.whatsapp": "WhatsApp",
    "company.nip": "Company NIP",
    "company.krs": "Company KRS",
    "payment.public_key": "Payment key",
    "payment.merchant_id": "Merchant ID",
    "payment.provider": "Payment provider",
    "mail.sender_domain": "Verified mail sender",
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sandbox_root(data_dir: Path) -> Path:
    """The shared folder, as this process sees it."""
    return data_dir if os.environ.get("FTF_PROBE_SANDBOX") == "1" else data_dir / "probe-sandbox"


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temp.chmod(0o644)
    os.replace(temp, path)


def _read(path: Path) -> dict:
    """A JSON object from a regular file of sane size; anything else reads as empty."""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_STATUS:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def create_job(root: Path, url: str, level: int, *, live: bool = False, fake: bool = False) -> str:
    wanted = Level(level)
    url = url.strip()
    if not fake:
        if url and "//" not in url:
            url = f"https://{url}"
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ProbeRefused("enter a web address")
        if wanted >= Level.REGISTER and not live:
            raise ProbeRefused("confirm that this site may be registered on")
    job_id = uuid.uuid4().hex
    _write(
        root / "jobs" / f"{job_id}.json",
        {
            "id": job_id,
            "url": "" if fake else url,
            "level": int(wanted),
            "live": bool(live),
            "fake": bool(fake),
            "created": _now(),
        },
    )
    return job_id


def job_state(root: Path, job_id: str) -> dict:
    if not JOB_ID.match(job_id):
        return {}
    job = _read(root / "jobs" / f"{job_id}.json")
    if not job:
        return {}
    status = _read(root / "status" / f"{job_id}.json")
    return {"state": "queued", **status, **job}


def list_jobs(root: Path, limit: int = 15) -> list[dict]:
    try:
        names = [p.stem for p in (root / "jobs").glob("*.json")]
    except OSError:
        return []
    states = [s for s in (job_state(root, n) for n in names) if s]
    return sorted(states, key=lambda s: str(s.get("created", "")), reverse=True)[:limit]


def worker_alive(root: Path) -> bool:
    try:
        return time.time() - (root / "worker.alive").stat().st_mtime < HEARTBEAT_AGE
    except OSError:
        return False


def step_label(step: dict) -> str:
    action = str(step.get("action", ""))
    path = urlsplit(str(step.get("url", ""))).path or "/"
    return {
        "read": f"Reading {path}",
        "click": f'Clicked "{step.get("label", "")}"',
        "checkout_seen": f"Found a checkout at {path}; card fields left empty",
        "register": "Registered with a bait identity",
        "login": "Logged in",
        "visit_failed": f"Could not open {path}",
        "left_site": "A page sent the agent to another site; it stopped there",
        "register_stopped": f"Skipped registration: needs more than email and password ({', '.join(step.get('needs', []))})",
        "login_stopped": f"Login stopped: asks for {', '.join(step.get('needs', []))}",
        "confirm_waiting": "Waiting for the activation email",
        "email_confirmed": "Email confirmed with the activation link",
        "confirm_timeout": "No activation email arrived in time",
        "confirm_unavailable": "The site wants email confirmation, but no inbox is connected",
        "confirm_failed": "The activation link did not open",
        "register_failed": "Registration failed",
        "login_failed": "Login failed",
    }.get(action, action)


def _summary(result: ProbeResult) -> dict:
    return {
        "pages": result.pages,
        "findings": result.findings,
        "steps": [step_label(s) for s in result.steps],
        "outbound": result.outbound,
        "blocked": len(result.blocked),
        "registered": result.registered,
        "logged_in": result.logged_in,
        "email_confirmed": result.email_confirmed,
        "awaiting_mail": result.awaiting_mail,
        "checkout_seen": result.checkout_seen,
    }


def _link_file(root: Path, job_id: str) -> Path:
    # In the app-owned jobs folder, so the sandbox cannot redirect this write with a symlink.
    return root / "jobs" / f"{job_id}.link"


def wait_for_link(root: Path, job_id: str, timeout: float = CONFIRM_WAIT) -> str | None:
    """Inside the sandbox: wait for the app to hand over this job's activation link."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        link = _read(_link_file(root, job_id)).get("url")
        if isinstance(link, str) and urlsplit(link).scheme in ("http", "https"):
            return link
        time.sleep(2)
    return None


def relay_confirmations(root: Path, api_key: str, mail_domain: str, *, find=None) -> int:
    """In the app: find activation links for running jobs. The app never opens them.

    A job's status comes from the sandbox, so only addresses on the bait domain are looked up.
    """
    from .mailbox import find_confirmation

    find = find or find_confirmation
    domain = mail_domain.strip().lower()
    handed = 0
    for job in list_jobs(root, limit=1000):
        address = job.get("awaiting_mail")
        if job["state"] != "running" or not isinstance(address, str) or not domain:
            continue
        if address.lower().rpartition("@")[2] != domain or _link_file(root, job["id"]).exists():
            continue
        link = find(api_key, address, site_key(str(job.get("url", ""))))
        if link:
            _write(_link_file(root, job["id"]), {"url": link})
            handed += 1
    return handed


def run_job(root: Path, job_id: str) -> None:
    """Run one job. Inside the sandbox only."""
    job = job_state(root, job_id)
    if not job:
        return
    path = root / "status" / f"{job_id}.json"
    state: dict = {"state": "running", "started": _now(), "steps": ["Starting browser"]}
    _write(path, state)

    def on_step(result: ProbeResult) -> None:
        state.update(_summary(result))
        _write(path, state)

    site = None
    try:
        level = Level(int(job["level"]))
        fake = job.get("fake") is True
        settings = get_settings()
        if fake:
            site = FakeSite(confirm=True).start()
        url = site.url if site else str(job["url"])
        check_target(url, level, live=job.get("live") is True, local=fake)
        if not fake:
            require_sandbox(settings.data_dir)
        run_dir = root / "runs" / job_id
        bundle = run_dir / "bundle"
        bundle.mkdir(parents=True, exist_ok=True)
        # A file beside the bundle makes this folder one incident when ingested.
        _write(run_dir / "run.json", {"id": job_id, "url": url, "level": int(level)})
        state["steps"] = ["Capturing the page"]
        _write(path, state)
        capture_url(url, bundle, timeout=20.0)
        # The demo needs no mail setup and leaves the real ledger of bait addresses alone.
        ledger = root / "probe" / ("demo-ledger.json" if fake else "ledger.json")
        if fake:
            ledger.unlink(missing_ok=True)
        result = run_probe(
            url,
            bundle,
            level=int(level),
            mail_domain=DEMO_MAIL_DOMAIN
            if fake
            else os.environ.get("FTF_PROBE_MAIL_DOMAIN", "").strip(),
            ledger_path=ledger,
            live=job.get("live") is True,
            local=fake,
            on_step=on_step,
            confirm_link=(
                site.confirmation_for if site else (lambda _: wait_for_link(root, job_id))
            ),
        )
        state.update(_summary(result))
        state.update(state="done", finished=_now())
    except ProbeRefused as exc:
        state.update(state="failed", error=str(exc), finished=_now())
    except Exception as exc:  # noqa: BLE001 - a failed probe is a result the analyst must see
        lines = str(exc).splitlines()
        state.update(state="failed", error=lines[0] if lines else type(exc).__name__)
        state["finished"] = _now()
    finally:
        if site:
            site.shutdown()
            site.server_close()
    _write(path, state)


def _fail(root: Path, job_id: str, error: str) -> None:
    status = _read(root / "status" / f"{job_id}.json")
    _write(
        root / "status" / f"{job_id}.json",
        {**status, "state": "failed", "error": error, "finished": _now()},
    )


def _next_job(root: Path) -> str | None:
    waiting = [j for j in list_jobs(root, limit=1000) if j["state"] == "queued"]
    return min(waiting, key=lambda j: str(j.get("created", "")))["id"] if waiting else None


def serve(root: Path, *, once: bool = False) -> None:
    """The worker loop. Each job gets its own process, so a crash or hang cannot stop it."""
    (root / "status").mkdir(parents=True, exist_ok=True)
    beat = root / "worker.alive"
    for job in list_jobs(root, limit=1000):
        if job["state"] == "running":
            _fail(root, job["id"], "the worker restarted during this job")
    program = shutil.which("ftf") or "ftf"
    while True:
        beat.touch()
        job_id = _next_job(root)
        if job_id is None:
            if once:
                return
            time.sleep(1)
            continue
        process = subprocess.Popen([program, "probe-worker", "--job", job_id])
        deadline = time.monotonic() + JOB_TIMEOUT
        while process.poll() is None:
            beat.touch()
            if time.monotonic() > deadline:
                process.kill()
                process.wait()
                _fail(root, job_id, f"stopped after {JOB_TIMEOUT} seconds")
                break
            time.sleep(1)
        if job_state(root, job_id).get("state") in ACTIVE:
            _fail(root, job_id, "the job ended unexpectedly")


def importable_run(root: Path, job_id: str) -> Path:
    """The run folder, refused if the sandbox left links or special files in it."""
    if not JOB_ID.match(job_id):
        raise ProbeRefused("unknown job")
    run_dir = root / "runs" / job_id
    if run_dir.is_symlink() or not (run_dir / "run.json").is_file():
        raise ProbeRefused("this job has no result")
    for base, dirs, files in os.walk(run_dir, followlinks=False):
        for name in [*dirs, *files]:
            item = Path(base) / name
            if item.is_symlink() or not (item.is_dir() or item.is_file()):
                raise ProbeRefused(f"unsafe file in the result: {name}")
    return run_dir


def refresh_analysis(store) -> None:
    """Recompute after new mail evidence was added to stored runs."""
    from .. import pipeline
    from ..fusion import run_method
    from ..intake import ANALYSIS_LOCK

    with ANALYSIS_LOCK:
        pipeline.run_extract(store)
        pipeline.run_fingerprint(store)
        pipeline.run_compare_all(store)
        run_method(store)
