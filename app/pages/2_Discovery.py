"""Discovery: search the Meta Ad Library, screen new ads, ingest the relevant ones."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from ftf.config import get_settings
from ftf.discovery.models import LibraryQuery, QueryKind, QueryOrigin
from ftf.discovery.run import ingest_ads, run_discovery
from ftf.discovery.service import make_source, open_state, paths, plan_queries
from ftf.discovery.state import INGESTED, NEW, SCREENED_OUT, SELECTED
from ftf.store import Store

st.set_page_config(page_title="Discovery · Follow the Fake", layout="wide")
st.title("Discovery")
st.caption(
    "Searches the public Meta Ad Library (country PL) in a browser window you can watch, one page "
    "at a time. If the library asks for a CAPTCHA or a login, the run pauses: handle it in that "
    "window and the run continues. The tool itself never logs in or solves a CAPTCHA. Meta's "
    "robots.txt and Terms do not permit automated collection; the team accepted this for the "
    "hackathon. A login uses your own Facebook account, which Meta may restrict."
)

settings = get_settings()
store, state = Store(settings.db_path), open_state(settings)

# -- queries ------------------------------------------------------------------------------

st.subheader("Queries")
with st.expander("Add to the watchlist"), st.form("add_query", clear_on_submit=True):
    kind = st.radio(
        "Search by", ["keywords", "advertiser page ID", "Ad Library ID"], horizontal=True
    )
    terms = st.text_input("Keywords or ID", max_chars=100)
    exact = st.checkbox("Exact phrase")
    reason = st.text_input("Why watch this", value="analyst watchlist")
    if st.form_submit_button("Add") and terms.strip():
        query_kind = {"keywords": QueryKind.KEYWORDS, "advertiser page ID": QueryKind.PAGE,
                      "Ad Library ID": QueryKind.AD}[kind]  # fmt: skip
        state.upsert_query(LibraryQuery(kind=query_kind, terms=terms.strip(), exact_phrase=exact,
                                        origin=QueryOrigin.WATCHLIST, reason=reason))  # fmt: skip
        st.success(f"Added {terms.strip()}")

use_model_queries = st.checkbox("Include queries proposed by the model", value=False)
queries, notes = plan_queries(store, state, model=use_model_queries, settings=settings)
for note in notes:
    st.info(note)
rows = state.queries()
table = [
    {
        "use": bool(r["enabled"]),
        "query": r["query"].label(),
        "origin": r["origin"],
        "reason": r["reason"],
        "last run": r["last_run"] or "never",
        "last result": f"{r['last_status']} ({r['last_count']})" if r["last_status"] else "",
        "id": r["query_id"],
    }
    for r in rows
]
edited = st.data_editor(
    table,
    width="stretch",
    hide_index=True,
    disabled=["query", "origin", "reason", "last run", "last result", "id"],
    key="queries_table",
)
for before, after in zip(table, edited, strict=True):
    if before["use"] != after["use"]:
        state.set_enabled(after["id"], after["use"])
enabled_ids = {r["id"] for r in edited if r["use"]}
run_queries = [q for q in queries if q.query_id in enabled_ids]

# -- run ----------------------------------------------------------------------------------

st.subheader("Run")
c1, c2, c3, c4 = st.columns(4)
max_ads = c1.number_input("Ads per query", 5, 200, 50, step=5)
max_ingest = c2.number_input("Ads to ingest", 0, 100, 20)
screen = c3.checkbox("Screen with the model", value=bool(settings.openai_api_key))
media = c4.checkbox("Download creatives", value=True)
b1, b2 = st.columns([3, 1])
show_browser = b1.checkbox(
    "Show the browser, and let me handle a CAPTCHA or login when the library asks",
    value=True,
    help="Needs a screen: run the app on your own computer. Inside Docker there is none, so the "
    "browser runs hidden and a CAPTCHA or login request stops the run.",
)
wait_min = b2.number_input("Wait for me (min)", 1, 60, 10, disabled=not show_browser)
replay = st.text_input(
    "Replay saved responses from a folder instead of the live library (optional)"
)


def _source():
    return make_source(
        Path(replay) if replay.strip() else None,
        headed=show_browser,
        settings=settings,
        human=show_browser,
        human_wait_s=float(wait_min) * 60,
        notify=lambda msg: st.warning(msg, icon="🖐") if msg.startswith("Action") else st.write(msg),
    )


if st.button(
    f"Run discovery ({len(run_queries)} queries)", type="primary", disabled=not run_queries
):
    source = _source()
    with st.status("Running discovery", expanded=True) as status:
        try:
            report = run_discovery(
                store, state, source, run_queries, paths(settings).incidents,
                max_ads=int(max_ads), max_ingest=int(max_ingest), use_model=screen,
                download_media=media, progress=st.write,
            )  # fmt: skip
        finally:
            source.close()
        label = {"ok": "complete", "partial": "complete, some queries failed",
                 "blocked": "stopped: the library blocked the browser",
                 "failed": "failed"}.get(report.status, report.status)  # fmt: skip
        status.update(
            label=f"Discovery {label}",
            state="error" if report.status in ("blocked", "failed") else "complete",
        )
    c = report.counts
    st.write(
        f"Searched {c.get('queries', 0)} queries; saw {c.get('seen', 0)} ads, {c.get('new', 0)} new, "
        f"{c.get('stopped', 0)} stopped since last seen; selected {c.get('selected', 0)}, "
        f"ingested {c.get('ingested', 0)}."
        + (
            f" You cleared {c['human_waits']} CAPTCHA or login request(s)."
            if c.get("human_waits")
            else ""
        )
    )
    if report.error:
        st.error(report.error)
    if report.screenshot and Path(report.screenshot).is_file():
        st.image(report.screenshot, caption="What the library showed when it blocked the run")
    for item in report.ingested:
        with st.container(border=True):
            st.markdown(f"**Incident `{item.incident_id}`** (ad {item.ad_id})")
            for m in item.known:
                st.write(
                    f"Shares `{m['ns']}` = `{m['value']}` with incident {', '.join(m['incidents'])}"
                )
            if item.links:
                st.dataframe(item.links, width="stretch", hide_index=True)
            if not item.known and not item.links:
                st.caption("No link to known material yet.")

# -- ads ----------------------------------------------------------------------------------

st.subheader("Ads seen")
status_filter = st.multiselect(
    "Status",
    [NEW, SELECTED, INGESTED, SCREENED_OUT, "failed"],
    default=[SELECTED, INGESTED, SCREENED_OUT],
)
ads = [row for row in state.ads() if row["status"] in status_filter]
ad_table = []
for row in ads:
    ad, t = row["ad"], row["triage"]
    ad_table.append({
        "ingest": False,
        "ad": ad.library_url,
        "page": ad.page_name,
        "dates": f"{ad.start_date or '?'} – {ad.end_date or 'running'}",
        "Meta enforcement": ad.enforcement,
        "links to": ", ".join(sorted({d for u in ad.link_urls for d in [u.split('/')[2]] if '//' in u})),
        "screen": t.verdict if t else "",
        "score": t.score if t else None,
        "known matches": "; ".join(f"{m['ns']}={m['value']}" for m in (t.known_matches if t else [])),
        "reasons": " | ".join(t.reasons) if t else "",
        "status": row["status"],
        "incident": row["incident_id"] or "",
        "id": ad.ad_id,
    })  # fmt: skip
if ad_table:
    picked = st.data_editor(
        ad_table, width="stretch", hide_index=True, key="ads_table",
        column_config={"ad": st.column_config.LinkColumn("ad", display_text=r"id=(\d+)")},
        disabled=[k for k in ad_table[0] if k != "ingest"],
    )  # fmt: skip
    chosen = [r["id"] for r in picked if r["ingest"]]
    if st.button(f"Ingest selected ({len(chosen)})", disabled=not chosen):
        source = _source()
        try:
            with st.spinner("Ingesting"):
                done = ingest_ads(
                    store, state, source, chosen, paths(settings).incidents, download_media=media
                )
        finally:
            source.close()
        st.success(f"Ingested {len(done)} ads: " + ", ".join(i.incident_id for i in done))
else:
    st.caption("No ads with these statuses yet.")

with st.expander("Recent runs"):
    st.dataframe(
        [
            {
                "run": r["run_id"],
                "status": r["status"],
                "source": r["source"],
                **r["counts"],
                "error": r["error"] or "",
            }
            for r in state.runs()
        ],
        width="stretch",
        hide_index=True,
    )
