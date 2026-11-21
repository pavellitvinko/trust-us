"""Interface stub: lists what is stored. The Perception track builds the real screens here."""

from __future__ import annotations

import json

import streamlit as st

from ftf.config import get_settings
from ftf.enrich.warning_lists import (
    related_listed_views,
    run_warning_lists,
    warning_observations,
    warning_risk_reasons,
)
from ftf.reporting import build_pair_report, render_pair_report
from ftf.store import Store

st.set_page_config(page_title="Follow the Fake", layout="wide")
st.title("Follow the Fake")

store = Store(get_settings().db_path)
incidents = store.incidents()
if not incidents:
    st.info("Nothing ingested yet. Run: ftf ingest <folder>")
    st.stop()

labels = {v.view_id: v.label for v in store.views()}
left, right = st.columns([1, 2])
with left:
    st.subheader("Material")
    incident_id = st.selectbox("Incident", [i.incident_id for i in incidents])
    views = store.views(incident_id)
    view = st.radio("View", views, format_func=lambda v: f"{v.kind.value}: {v.label}")
with right:
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
    st.subheader("Full comparison report")
    candidates = [v for v in store.views() if v.view_id != view.view_id]
    if candidates:
        other = st.selectbox(
            "Compare with", candidates, format_func=lambda v: f"{v.view_id} — {v.label}"
        )
        if st.button("Explain comparison"):
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
