"""Follow the Fake interface: match, material, add material, campaign map and method."""

from __future__ import annotations

import json
import os

import streamlit as st

from ftf.campaign_map import TOGGLES, map_data, to_dot
from ftf.config import get_settings
from ftf.corpus import ensure_seeded
from ftf.corpus_report import export_files, export_zip
from ftf.enrich.warning_lists import (
    related_listed_views,
    run_warning_lists,
    warning_observations,
    warning_risk_reasons,
)
from ftf.fusion import run_method
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
from ftf.material import expectation_profiles, material_summary
from ftf.method_settings import apply_saved, current_values, reset, save
from ftf.narrative import pair_narrative, render_narrative
from ftf.patterns.analyst import (
    LibraryEditError,
    merge_patterns,
    rename_pattern,
    retire_pattern,
)
from ftf.patterns.library import PatternLibraryStore
from ftf.registry import layers, load_plugins
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
ensure_seeded(store)
load_plugins()
# A running analysis already holds the current settings, so never wait for it here.
if ANALYSIS_LOCK.acquire(blocking=False):
    try:
        apply_saved()
    finally:
        ANALYSIS_LOCK.release()


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
    st.session_state.pop("map_cache", None)
    st.session_state.pop("corpus_export", None)
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


def number_rows(values: dict) -> list[dict]:
    return [{"setting": k, "value": float(v)} for k, v in values.items()]


def recompute() -> None:
    with ANALYSIS_LOCK:
        run_method(store)
    for key in ("assignments", "comparison_report", "map_cache", "corpus_export"):
        st.session_state.pop(key, None)


def show_export() -> None:
    st.subheader("Corpus report and export")
    st.caption("Private project material: do not publish or redistribute.")
    if st.button("Build report"):
        with ANALYSIS_LOCK, st.spinner("Building the report from stored results"):
            files = export_files(store)
        st.session_state["corpus_export"] = {
            "report": files["report.md"],
            "zip": export_zip(files),
        }
    built = st.session_state.get("corpus_export")
    if built:
        st.download_button(
            "Download all (zip)",
            built["zip"],
            file_name="follow-the-fake-export.zip",
            mime="application/zip",
        )
        st.download_button(
            "Download report (Markdown)",
            built["report"],
            file_name="corpus-report.md",
            mime="text/markdown",
        )
        with st.expander("Preview report"):
            st.markdown(built["report"])


def show_map() -> None:
    st.subheader("Campaign map")
    columns = st.columns(len(TOGGLES))
    enabled = [
        name
        for column, name in zip(columns, TOGGLES, strict=True)
        if column.checkbox(name.capitalize(), value=name != "playbook", key=f"map_{name}")
    ]
    if not st.toggle("Build map", key="map_on"):
        st.caption("The map is built on request, because it reads every stored relation.")
        return
    cache = st.session_state.setdefault("map_cache", {})
    if tuple(enabled) not in cache:
        with ANALYSIS_LOCK:
            cache[tuple(enabled)] = map_data(store, enabled)
    data = cache[tuple(enabled)]
    st.caption("Boxes are families and campaigns. A line is a stored relation in a chosen tier.")
    st.graphviz_chart(to_dot(data), width="stretch")
    st.subheader("Clusters")
    for cluster in data["clusters"]:
        with st.expander(f"Family {cluster['family']}: {len(cluster['campaigns'])} campaign(s)"):
            for campaign, members in cluster["campaigns"].items():
                st.write(f"**{campaign}**: " + ", ".join(members))
    st.subheader("Links and shared identifiers")
    if data["edges"]:
        st.dataframe(
            [
                {
                    "a": e["a"],
                    "b": e["b"],
                    "relation": e["relation"],
                    "confidence": e["confidence"],
                    "tiers": ", ".join(e["tiers"]),
                    "shared": "; ".join(e["shared"][:6]),
                }
                for e in data["edges"]
            ],
            width="stretch",
        )
    else:
        st.caption("No links in the chosen tiers.")


def show_pattern_library() -> None:
    st.subheader("Pattern library")
    library = PatternLibraryStore(settings.data_dir / "patterns" / "library")
    version = library.latest_version()
    if version is None:
        st.info("Not run yet. Use `ftf patterns discover`.")
        return
    st.caption(f"Version {version.version_id}")
    st.dataframe(
        [
            {
                "id": p.pattern_id,
                "name": p.name,
                "description": p.description,
                "examples": len(p.positive_examples),
                "retired": bool(p.metadata and p.metadata.analyst_retired_at),
            }
            for p in version.patterns
        ],
        width="stretch",
    )
    ids = [p.pattern_id for p in version.patterns]
    chosen = st.selectbox("Pattern", ids, key="library_pattern")
    name = st.text_input("New name", key="library_name")
    active = [
        p.pattern_id
        for p in version.patterns
        if p.pattern_id != chosen and not (p.metadata and p.metadata.analyst_retired_at)
    ]
    target = st.selectbox("Merge it into", active or [None])
    rename, retire, merge = st.columns(3)
    try:
        if rename.button("Rename"):
            with ANALYSIS_LOCK:
                rename_pattern(library, chosen, name)
            st.session_state.pop("corpus_export", None)
            st.rerun()
        if retire.button("Retire"):
            with ANALYSIS_LOCK:
                retire_pattern(library, chosen)
            st.session_state.pop("corpus_export", None)
            st.rerun()
        if merge.button("Merge", disabled=target is None):
            with ANALYSIS_LOCK:
                merge_patterns(library, target, chosen)
            st.session_state.pop("corpus_export", None)
            st.rerun()
    except LibraryEditError as exc:
        st.error(str(exc))
    st.caption("Run `ftf patterns match` and `ftf run` so stored scores follow these edits.")


def show_method() -> None:
    st.subheader("Layers")
    st.dataframe(
        [
            {
                "layer": x.name,
                "version": x.version,
                "tier": x.tier.value,
                "group": x.group,
                "comparator": x.comparator,
            }
            for x in layers.all()
        ],
        width="stretch",
    )
    st.subheader("Weights and thresholds")
    values = current_values()
    edited = {}
    for section, title in (
        ("tier_weights", "Tier weights"),
        ("group_weights", "Group weights"),
        ("thresholds", "Thresholds"),
    ):
        st.markdown(f"**{title}**")
        rows = st.data_editor(
            number_rows(values[section]),
            disabled=["setting"],
            hide_index=True,
            key=f"method_{section}",
        )
        edited[section] = {row["setting"]: row["value"] for row in rows}
    minimum = st.number_input(
        "Minimum artifact groups", min_value=1, value=int(values["artifact_min_groups"]), step=1
    )
    apply, restore = st.columns(2)
    if apply.button("Save and recompute"):
        with ANALYSIS_LOCK:
            errors = save({**edited, "artifact_min_groups": int(minimum)})
            if not errors:
                recompute()
        if errors:
            st.error("; ".join(errors))
        else:
            st.rerun()
    if restore.button("Restore shipped settings"):
        with ANALYSIS_LOCK:
            reset()
            recompute()
        for section in ("tier_weights", "group_weights", "thresholds"):
            st.session_state.pop(f"method_{section}", None)
        st.rerun()
    show_pattern_library()
    st.subheader("Expectation profiles")
    profiles = expectation_profiles(store)
    if not profiles:
        st.info("Not run yet. Use `ftf perceive`.")
    for kind, rows in profiles.items():
        with st.expander(kind):
            st.dataframe(rows, width="stretch")


tab_match, tab_material, tab_add, tab_map, tab_method, tab_export = st.tabs(
    ["Match", "Material", "Add material", "Campaign map", "Method", "Export"]
)
with tab_add:
    add_material()

incidents = store.incidents()
if not incidents:
    with tab_match:
        st.info("Nothing ingested yet. Use Add material.")
    with tab_material:
        st.info("Nothing ingested yet. Use Add material.")
    with tab_map:
        st.info("Nothing ingested yet. Use Add material.")
    with tab_method:
        st.info("Nothing ingested yet. Use Add material.")
    with tab_export:
        st.info("Nothing ingested yet. Use Add material.")
    st.stop()

with tab_export:
    show_export()
with tab_map:
    show_map()
with tab_method:
    show_method()

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
            st.session_state.pop("narrative", None)
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
            st.subheader("Narrative")
            st.caption("Written by the model from the table above. Each sentence cites its facts.")
            pair_key = (view.view_id, other.view_id)
            if st.button("Write narrative"):
                with st.spinner("Writing from the evidence table"):
                    st.session_state["narrative"] = {
                        "pair": pair_key,
                        "result": pair_narrative(
                            store, view.view_id, other.view_id, report=report
                        ),
                    }
            written = st.session_state.get("narrative")
            if written and written["pair"] == pair_key:
                # Plain text: model output can repeat hostile links from the page.
                st.text(render_narrative(written["result"]))
                with st.expander("Facts given to the model"):
                    st.json(written["result"]["facts"])

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
    if suspicion["face_matches"]:
        st.markdown("**Possible face matches (context, not counted in the level)**")
        for reason in suspicion["face_matches"]:
            source = reason["source"]
            st.text(f"{reason['summary']} ({source['file'] or '-'}:{source['locator'] or '-'})")
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
        st.session_state.pop("corpus_export", None)
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
