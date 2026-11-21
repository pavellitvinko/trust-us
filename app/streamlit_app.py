"""Interface stub: lists what is stored. The Perception track builds the real screens here."""

from __future__ import annotations

import streamlit as st

from ftf.config import get_settings
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
