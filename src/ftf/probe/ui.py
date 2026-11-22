"""The Probe agent screen: queue a job for the sandbox, follow it, add its result."""

from __future__ import annotations

import os

import streamlit as st

from ..intake import IntakeError, run_stages
from .jobs import (
    ACTIVE,
    PROBE_NAMES,
    create_job,
    importable_run,
    job_state,
    list_jobs,
    refresh_analysis,
    sandbox_root,
    worker_alive,
)
from .mailbox import watch_resend
from .persona import ledger_path
from .policy import ProbeRefused

PROBE_LEVELS = {
    1: "1. Explore: follow links and quiz steps",
    2: "2. Payment: also read deposit and checkout pages",
    3: "3. Register: also create an account and log in",
    4: "4. Watch inbox: also track mail sent to the bait address",
}


def probe_job_name(job: dict) -> str:
    target = "Fake test site" if job.get("fake") else str(job.get("url", ""))[:60]
    return f"{str(job.get('created', ''))[:19]}  {target}  [{job.get('state')}]"


def show_probe_job(root, job_id: str, db, on_change) -> None:
    job = job_state(root, job_id)
    if not job:
        st.warning("This job no longer exists.")
        return
    state = job["state"]
    steps = [str(s) for s in job.get("steps", [])] if isinstance(job.get("steps"), list) else []
    if state in ACTIVE:
        current = steps[-1][:100] if steps else "Waiting for the worker"
        pages = job.get("pages") if isinstance(job.get("pages"), list) else []
        st.progress(min(len(pages), 15) / 15, text=f"Step {len(steps)}: {current}")
    elif state == "done":
        st.success("Finished.")
    else:
        st.error(f"Failed: {str(job.get('error', 'unknown error'))[:300]}")
    columns = st.columns(4)
    columns[0].metric("Pages", len(job.get("pages") or []))
    columns[1].metric("Registered", "yes" if job.get("registered") else "no")
    columns[2].metric("Logged in", "yes" if job.get("logged_in") else "no")
    columns[3].metric("Checkout found", "yes" if job.get("checkout_seen") else "no")
    findings = job.get("findings") if isinstance(job.get("findings"), list) else []
    rows = [
        {
            "found": PROBE_NAMES.get(str(f.get("ns")), str(f.get("ns"))),
            "value": str(f.get("value")),
            "page": str(f.get("where")),
        }
        for f in findings
        if isinstance(f, dict)
    ]
    if rows:
        st.dataframe(rows, width="stretch")
    if steps:
        with st.expander(f"What the agent did ({len(steps)} steps)"):
            st.text("\n".join(steps))
    if state in ACTIVE:
        return
    if state == "done":
        known = {i.incident_id for i in db().incidents()}
        if job_id in known:
            st.caption("Already added to the analysis.")
        elif st.button("Add to analysis", key=f"probe_add_{job_id}"):
            try:
                with st.spinner("Linking the findings with known campaigns"):
                    run_stages(db(), importable_run(root, job_id), enrich=True)
            except (ProbeRefused, IntakeError) as exc:
                st.error(str(exc))
                return
            on_change()
            for key in ("comparison_report", "map_cache", "corpus_export"):
                st.session_state.pop(key, None)
            st.rerun(scope="app")


def probe_page(settings, db, on_change) -> None:
    """The Probe agent screen. `db` returns this thread's store; `on_change` follows new data."""
    st.subheader("Probe agent")
    root = sandbox_root(settings.data_dir)
    alive = worker_alive(root)
    if not alive:
        st.warning("The isolated worker is not running. Start it, then reload this page:")
        st.code("docker compose --profile probe up -d --build probe-worker", language="bash")
    fake = st.checkbox("Use the built-in fake scam site", value=True, key="probe_fake")
    url = st.text_input(
        "URL", disabled=fake, placeholder="https://suspect.example", key="probe_url"
    )
    level = st.radio(
        "How far should it go?",
        list(PROBE_LEVELS),
        index=1,
        format_func=PROBE_LEVELS.get,
        key="probe_level",
    )
    live = False
    if level >= 3 and not fake:
        live = st.checkbox(
            "I confirm this site is approved for registration with a bait identity",
            key="probe_live",
        )
    if st.button("Run agent", type="primary", disabled=not alive):
        try:
            st.session_state["probe_job"] = create_job(root, url, level, live=live, fake=fake)
        except ProbeRefused as exc:
            st.error(str(exc))
    jobs = list_jobs(root)
    if not jobs:
        return
    ids = [j["id"] for j in jobs]
    names = {j["id"]: probe_job_name(j) for j in jobs}
    wanted = st.session_state.get("probe_job")
    selected = st.selectbox(
        "Runs",
        ids,
        index=ids.index(wanted) if wanted in ids else 0,
        format_func=names.get,
    )
    st.session_state["probe_job"] = selected
    active = job_state(root, selected).get("state") in ACTIVE

    def progress() -> None:
        show_probe_job(root, selected, db, on_change)
        if active and job_state(root, selected).get("state") not in ACTIVE:
            st.rerun(scope="app")

    st.fragment(progress, run_every="2s" if active else None)()
    resend_key = os.environ.get("FTF_RESEND_API_KEY", "").strip()
    if resend_key and st.button("Check bait inbox"):
        try:
            with st.spinner("Reading the bait inbox"):
                report = watch_resend(resend_key, ledger_path(settings.data_dir))
                if report["new"]:
                    refresh_analysis(db())
                    on_change()
        except OSError as exc:
            st.error(f"Could not read the inbox: {exc}")
            return
        st.success(
            f"Messages: {report['messages']}. Matched to a site: {report['attributed']}. "
            f"New: {report['new']}. Proven resale: {len(report['resold'])}."
        )
        if report["resold"]:
            st.dataframe(
                [
                    {
                        "given to": r["site"],
                        "mail from": r["sender_domain"],
                        "subject": r["subject"],
                    }
                    for r in report["resold"]
                ],
                width="stretch",
            )
