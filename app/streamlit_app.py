"""Follow the Fake interface: match, material and add-material screens."""

from __future__ import annotations

import json
import os

import streamlit as st

from ftf.config import get_settings
from ftf.enrich.warning_lists import (
    related_listed_views,
    run_warning_lists,
    warning_observations,
    warning_risk_reasons,
)
from ftf.grouping import assign, build_groups
from ftf.intake import (
    ANALYSIS_LOCK,
    STAGES,
    IntakeError,
    add_zip,
    allowed_folder,
    discard_upload,
    run_stages,
)
from ftf.material import material_summary
from ftf.reporting import build_pair_report, render_pair_report
from ftf.store import Store

UNAVAILABLE = {
    "not_run": "Not run for this view. Run `ftf perceive` or `ftf patterns discover`.",
    "unavailable": "Unavailable: the model stage produced no verified result for this view.",
}

st.set_page_config(page_title="Follow the Fake", layout="wide")
st.title("Follow the Fake")

settings = get_settings()
store = Store(settings.db_path)


def add_material() -> None:
    st.subheader("Add material")
    upload = st.file_uploader("Incident folder as a zip", type=["zip"])
    root = os.environ.get("FTF_UI_FOLDER_ROOT")
    folder = st.text_input(
        "Or a folder on the server",
        disabled=not root,
        help=f"Inside {root}" if root else "Off until FTF_UI_FOLDER_ROOT is set.",
    )
    enrich = st.checkbox("Look up DNS and RDAP (network)", value=True)
    st.text_input(
        "URL", disabled=True, help="Available once the live capture service (HAC-14) is connected."
    )
    st.caption("URL capture is not connected yet.")
    if not st.button("Analyse material"):
        return
    if not upload and not folder.strip():
        st.warning("Choose a zip or enter a folder.")
        return
    progress = st.progress(0.0, text="Starting")

    def on_stage(name: str, index: int, total: int) -> None:
        progress.progress(index / total, text=f"Stage {index + 1} of {len(STAGES)}: {name}")

    path = None
    try:
        path = (
            add_zip(upload, upload.name, settings.data_dir / "uploads")
            if upload
            else allowed_folder(folder.strip(), root)
        )
        result = run_stages(store, path, on_stage, enrich=enrich)
    except IntakeError as exc:
        if upload and path is not None:
            discard_upload(path)
        progress.empty()
        st.error(str(exc))
        return
    progress.progress(1.0, text="Done")
    st.session_state.pop("comparison_report", None)
    st.session_state.pop("assignments", None)
    st.success(
        f"Added {result['views']} views in {len(result['incidents'])} incident(s). "
        f"Campaigns: {result['campaigns']}, families: {result['families']}."
    )


def show_status_table(title: str, section: dict) -> None:
    st.subheader(title)
    if section["status"] != "available":
        st.info(UNAVAILABLE[section["status"]])
    if section["rows"]:
        st.dataframe(section["rows"], width="stretch")


tab_match, tab_material, tab_add = st.tabs(["Match", "Material", "Add material"])
with tab_add:
    add_material()

incidents = store.incidents()
if not incidents:
    with tab_match:
        st.info("Nothing ingested yet. Use Add material.")
    with tab_material:
        st.info("Nothing ingested yet. Use Add material.")
    st.stop()

labels = {v.view_id: v.label for v in store.views()}
with st.sidebar:
    st.subheader("Selected material")
    incident_id = st.selectbox("Incident", [i.incident_id for i in incidents])
    views = store.views(incident_id)
    view = st.radio("View", views, format_func=lambda v: f"{v.kind.value}: {v.label}")
if view is None:
    for tab in (tab_match, tab_material):
        with tab:
            st.info("This incident has no views.")
    st.stop()

with tab_match:
    st.subheader("Closest known material")
    assignments = st.session_state.setdefault("assignments", {})
    if view.view_id not in assignments:
        with ANALYSIS_LOCK:
            groups = build_groups(store, persist_funnel=False)
            assignments[view.view_id] = assign(store, view.view_id, groups)
    outcome = assignments[view.view_id]
    st.write(f"Outcome: **{outcome.headline()}**")
    for reason in outcome.reasons:
        st.caption(reason)
    related = [
        {
            "relation": relation,
            "view": item.view_id,
            "label": labels.get(item.view_id, ""),
            "strength": round(item.strength, 3),
            "coverage": round(item.relation.coverage, 3),
        }
        for relation, items in outcome.related.items()
        for item in items[:5]
    ]
    if related:
        st.dataframe(related, width="stretch")
    st.subheader("Full comparison report")
    candidates = [v for v in store.views() if v.view_id != view.view_id]
    if candidates:
        other = st.selectbox(
            "Compare with", candidates, format_func=lambda v: f"{v.view_id} — {v.label}"
        )
        if st.button("Explain comparison"):
            with ANALYSIS_LOCK:
                st.session_state["comparison_report"] = build_pair_report(
                    store, view.view_id, other.view_id
                )
        report = st.session_state.get("comparison_report")
        if (
            report
            and report["objects"]["a"]["view_id"] == view.view_id
            and report["objects"]["b"]["view_id"] == other.view_id
        ):
            markdown = render_pair_report(report)
            st.download_button(
                "Download JSON",
                json.dumps(report, ensure_ascii=False, indent=2),
                file_name="comparison.json",
                mime="application/json",
            )
            st.download_button(
                "Download Markdown", markdown, file_name="comparison.md", mime="text/markdown"
            )
            st.markdown(markdown)

with tab_material:
    summary = material_summary(store, view.view_id)
    st.subheader("Why this item is suspicious")
    st.write(f"Kind of page: {summary['page_kind'] or 'not read yet'}")
    suspicion = summary["suspicion"]
    st.write(f"Level: **{suspicion['level']}** independent kinds of reason (not a probability).")
    for kind, reasons in suspicion["reasons_by_kind"].items():
        st.markdown(f"**{kind.replace('_', ' ')}**")
        for reason in reasons:
            source = reason["source"]
            st.write(f"- {reason['summary']} ({source['file'] or '-'}:{source['locator'] or '-'})")
    if suspicion["unchecked_kinds"]:
        st.caption("Not checked: " + ", ".join(suspicion["unchecked_kinds"]))
    show_status_table("Expected elements", summary["expectations"])
    show_status_table("Persuasion patterns", summary["patterns"])

    st.subheader("Observations")
    rows = [
        {
            "signal": o.signal_id,
            "namespace": o.namespace,
            "value": str(o.value),
            "state": o.state.value,
            "source": o.source.locator,
        }
        for o in store.observations(view.view_id)
    ]
    if rows:
        st.dataframe(rows, width="stretch")
    else:
        st.caption("No observations for this view yet.")
    st.subheader("Evidence involving this view")
    evidence = [
        {
            "score": e.score,
            "layer": e.layer_id,
            "other view": labels.get(e.view_b if e.view_a == view.view_id else e.view_a, ""),
            "shared": "; ".join(f"{s['ns']}={s['value']}" for s in e.shared),
        }
        for e in store.evidence(view_a=view.view_id, min_score=0.01)
    ]
    if evidence:
        st.dataframe(evidence, width="stretch")
    else:
        st.caption("No links found yet.")

    st.subheader("Warning lists")
    offline = st.checkbox("Use offline warning snapshots", value=True)
    if st.button("Check warning lists"):
        with st.spinner("Checking warning lists"):
            run_warning_lists(
                store,
                get_settings().data_dir / "warning_lists",
                view_ids=[view.view_id],
                refresh=not offline,
            )
        st.session_state.pop("comparison_report", None)
        st.rerun()
    warnings = warning_observations(store, view.view_id)
    if warnings:
        st.caption("A miss says nothing about legitimacy. Fuzzy matches require verification.")
        for reason in warning_risk_reasons(warnings):
            st.write(reason["message"])
        st.dataframe(
            [
                {
                    "source": o["value"]["source"],
                    "state": o["state"],
                    "input": (o["value"]["input"] or {}).get("value"),
                    "result": o["value"]["message"],
                    "snapshot date": (o["value"]["snapshot"] or {}).get("snapshot_date"),
                    "refresh error": o["value"]["refresh_error"],
                }
                for o in warnings
            ],
            width="stretch",
        )
        with st.expander("Warning-list provenance and match details"):
            st.json(warnings)
        related = related_listed_views(store, view.view_id)
        if related:
            st.caption(
                "Views referencing the same listed entity; this is context, not a campaign link."
            )
            st.dataframe(
                [{k: row[k] for k in ("view_id", "label", "entity_name")} for row in related],
                width="stretch",
            )
    else:
        st.caption("Warning lists have not been checked for this view.")
