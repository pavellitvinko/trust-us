"""Follow the Fake interface: add material, the library of everything added, campaigns, method."""

from __future__ import annotations

import html
import importlib.util
import json
import re
import threading
from urllib.parse import quote, urlsplit

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
from ftf.grouping import build_groups
from ftf.intake import ANALYSIS_LOCK, IntakeError
from ftf.jobs import link_case, save_files, worker_for
from ftf.library import (
    KIND_NAMES,
    LAYER_NAMES,
    Item,
    Library,
    build_library,
    case_title,
    filter_matches,
    match_help,
)
from ftf.material import expectation_profiles
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

st.set_page_config(page_title="Follow the Fake", layout="wide", initial_sidebar_state="collapsed")

settings = get_settings()
_LOCAL = threading.local()


def db() -> Store:
    """This thread's store: a fragment rerun can run in another thread than the full run."""
    stores = _LOCAL.__dict__.setdefault("stores", {})
    key = str(settings.db_path)
    if key not in stores:
        stores[key] = Store(settings.db_path)
    return stores[key]


worker = worker_for(settings.db_path, settings.data_dir)
ensure_seeded(db())  # with FTF_SEED_CORPUS=1, an empty store gets the shipped corpus
load_plugins()
# A running analysis already holds the current settings, so never wait for it here.
if ANALYSIS_LOCK.acquire(blocking=False):
    try:
        apply_saved()
    finally:
        ANALYSIS_LOCK.release()

VICTIM = importlib.util.find_spec("ftf.victim") is not None
DISCOVERY = importlib.util.find_spec("ftf.discovery") is not None
ICONS = {"Page": ":material/language:", "Image": ":material/image:", "Video": ":material/movie:"}
BADGES = {"campaign": "red", "kit": "orange", "duplicate": "blue", "none": "gray"}
BUSY = "Available when the current analysis finishes."
TIER_HINTS = {
    "identity": (
        "Shared operator-controlled identifiers, such as a tracking pixel ID or campaign record. "
        "Helps connect items to the same campaign."
    ),
    "artifact": (
        "Same or near-identical material, such as images, text or ad creatives. "
        "Shows content reuse; the verdict tells you whether the items are duplicates."
    ),
    "kit": (
        "Shared templates, code, forms or tracking software. "
        "Different campaigns can use the same kit."
    ),
    "playbook": (
        "Similar persuasion methods, such as story structure, roles or page layout. "
        "Shows a similar approach, which alone does not establish a shared campaign."
    ),
}
# Meta's mark, from the Tabler icon set ("brand-meta", MIT licence).
META = (
    '<svg class="ftf-meta" viewBox="0 0 24 24" fill="none" stroke="#0866ff" stroke-width="2"'
    ' stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M12 10.174c1.766 -2.784 3.315 -4.174 4.648 -4.174c2 0 3.263 2.213 4 5.217'
    "c.704 2.869 .5 6.783 -2 6.783c-1.114 0 -2.648 -1.565 -4.148 -3.652"
    'a27.627 27.627 0 0 1 -2.5 -4.174z"/>'
    '<path d="M12 10.174c-1.766 -2.784 -3.315 -4.174 -4.648 -4.174c-2 0 -3.263 2.213 -4 5.217'
    "c-.704 2.869 -.5 6.783 2 6.783c1.114 0 2.648 -1.565 4.148 -3.652"
    'c1 -1.391 1.833 -2.783 2.5 -4.174z"/></svg>'
)
_AD_LIBRARY = re.compile(r"facebook\.com/ads/library/?\?(?:.*&)?id=\d+")
_DOMAIN = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$", re.IGNORECASE)

st.html(
    """<style>
    [data-testid="stMainBlockContainer"] {padding-top: 3.5rem; max-width: 1180px}
    [class*="st-key-row-"] {position: relative; padding: 2px 6px 2px 4px}
    [class*="st-key-row-"] [data-testid="stMarkdownContainer"] p {margin-bottom: 0}
    [class*="st-key-row-"][class*="-sel"] {background: rgba(28, 79, 156, 0.07); border-radius: 6px}
    [class*="st-key-row-step"]::before {content: ""; position: absolute; left: 14.5px; width: 0;
      border-left: 2px solid #1c4f9c; top: -0.75rem; bottom: -0.75rem; z-index: 0}
    [class*="st-key-row-stepfirst"]::before {top: 50%}
    [class*="st-key-row-steplast"]::before {bottom: 50%}
    .ftf-dot {display: inline-flex; width: 1.15rem; height: 1.15rem; border-radius: 50%;
      background: #dde6f3; color: #1c4f9c; font-size: .7rem; font-weight: 600;
      align-items: center; justify-content: center; position: relative; z-index: 1}
    [class*="st-key-title-"] button {padding: 0; min-height: 0; text-align: left;
      justify-content: flex-start; font-weight: 500}
    [class*="st-key-icon-"] p {font-size: 1.5rem; line-height: 1; text-align: center}
    [class*="st-key-title-"] button p {font-size: 0.95rem; white-space: nowrap; overflow: hidden;
      text-overflow: ellipsis; max-width: 28rem}
    .ftf-j {display: grid; grid-template-columns: 1.3rem minmax(0, 1fr); column-gap: .7rem;
      font-size: .9rem; line-height: 1.45}
    .ftf-j .rail {display: flex; flex-direction: column; align-items: center}
    .ftf-j .bar {flex: 1; min-height: .9rem; border-left: 2px solid #1c4f9c}
    .ftf-j .bar.dash {border-left: 2px dashed #a9a69c}
    .ftf-j .txt {padding-bottom: .55rem}
    .ftf-j .proof {color: #6b6a65; font-size: .8rem; padding-bottom: .55rem}
    .ftf-j .proof code {font-size: .75rem; background: #f1efe8; padding: 0 .3rem;
      border-radius: 4px}
    .ftf-j small {color: #6b6a65}
    .ftf-j .me {font-weight: 600}
    .ftf-j .muted {color: #6b6a65; font-size: .8rem}
    .ftf-meta {width: 1.15rem; height: 1.15rem; vertical-align: -0.2rem; flex: none}
    a.ftf-btn {display: inline-flex; align-items: center; gap: .45rem; min-height: 2.5rem;
      padding: .25rem .75rem; border: 1px solid #d5d3cc; border-radius: .5rem;
      color: inherit; text-decoration: none; font-size: .875rem; background: #fff}
    a.ftf-btn:hover {border-color: #1c4f9c; color: #1c4f9c}
    </style>"""
)


# -- Add material --------------------------------------------------------------------------


def detect(text: str) -> tuple[str, bool] | None:
    """What a pasted value is, and whether it is a URL that can be opened."""
    text = text.strip()
    if not text:
        return None
    if _AD_LIBRARY.search(text):
        return "Meta ad · captured from the Ad Library", True
    if text.startswith(("http://", "https://")) and urlsplit(text).hostname:
        return "Webpage · captured live", True
    if _DOMAIN.match(text):
        return "Domain · captured live", True
    return None


def _form_key(name: str) -> str:
    return f"{name}_{st.session_state.setdefault('form', 0)}"


def add_link() -> None:
    text = st.session_state.get(_form_key("link"), "").strip()
    if not detect(text):
        st.session_state["intake_error"] = "Paste a full link or a domain first."
        return
    links = st.session_state.setdefault("links", [])
    if text not in links:
        links.append(text)
    st.session_state.pop("intake_error", None)
    st.session_state["form"] += 1


def remove_link(text: str) -> None:
    st.session_state["links"] = [x for x in st.session_state.get("links", []) if x != text]


def submit() -> None:
    files = list(st.session_state.get(_form_key("files")) or [])
    folder = list(st.session_state.get(_form_key("folder")) or [])
    links = list(st.session_state.get("links", []))
    if not (files or folder or links):
        st.session_state["intake_error"] = "Add a link or choose files first."
        return
    try:
        if folder and all("/" not in f.name for f in folder):
            raise IntakeError("the folder arrived without its structure: use Browse to choose it")
        uploads = folder + files
        if uploads:
            path = save_files(((f.name, f) for f in uploads), worker.uploads)
        else:
            path = link_case(links[0], worker.uploads)
    except IntakeError as exc:
        st.session_state["intake_error"] = str(exc).capitalize() + "."
        return
    names = links + [f.name.split("/")[-1] for f in uploads]
    worker.submit(path, names, links)
    st.session_state["links"] = []
    st.session_state.pop("intake_error", None)
    st.session_state["form"] += 1
    st.session_state["go_library"] = True


def page_add() -> None:
    _, middle, _ = st.columns([1, 3, 1])
    with middle:
        st.markdown("### What did you find?")
        with st.container(border=True):
            with st.container(horizontal=True, vertical_alignment="bottom"):
                st.text_input(
                    "Link",
                    key=_form_key("link"),
                    placeholder="Paste a link to a webpage or a Meta Ad Library ad",
                    label_visibility="collapsed",
                )
                st.button("Add", on_click=add_link)
            found = detect(st.session_state.get(_form_key("link"), ""))
            if found and found[0].startswith("Meta"):
                st.markdown(
                    f'{META} <small style="color:#6b6a65">{found[0]}</small>',
                    unsafe_allow_html=True,
                )
            else:
                st.caption(found[0] if found else "Press Enter after pasting to check the link.")
            is_url = bool(found and found[1])
            st.toggle(
                "Victim visitor",
                key=_form_key("victim"),
                disabled=not (is_url and VICTIM),
                help=(
                    "Walk this link's funnel as a persona. It never pays or installs."
                    if VICTIM
                    else "Not in this build: the victim visitor lives on its own branch."
                ),
            )
            left, right = st.columns(2)
            left.file_uploader("Files", accept_multiple_files=True, key=_form_key("files"))
            right.file_uploader(
                "Folder", accept_multiple_files="directory", key=_form_key("folder")
            )
            with st.popover("Supported files", icon=":material/info:", type="tertiary"):
                st.markdown(
                    "| | |\n|---|---|\n"
                    "| Saved page | `.html` with its `_files` folder |\n"
                    "| Web archive | `.har` |\n"
                    "| Screenshot | `.png` `.jpg` `.webp` `.gif` |\n"
                    "| Video | `.mp4` `.mov` `.webm` with `.info.json` |\n"
                    "| Folder | any of these; each sub-folder is one case |"
                )
                st.caption("Choose a folder with Browse: a dragged folder loses its structure.")

        links = st.session_state.get("links", [])
        for text in links:
            with st.container(horizontal=True, vertical_alignment="center"):
                if _AD_LIBRARY.search(text):
                    st.markdown(f"{META} {html.escape(text)}", unsafe_allow_html=True)
                else:
                    st.markdown(f":material/link: {text}")
                st.button(
                    "",
                    icon=":material/close:",
                    key=f"rm-{text}",
                    type="tertiary",
                    on_click=remove_link,
                    args=(text,),
                    help="Remove",
                )
        if error := st.session_state.get("intake_error"):
            st.error(error)
        files = st.session_state.get(_form_key("files")) or []
        folder = st.session_state.get(_form_key("folder")) or []
        count = len(links) + len(files) + len(folder)
        with st.container(horizontal=True, horizontal_alignment="right"):
            st.button(
                "Analyse" if count < 2 else f"Analyse {count} items together",
                type="primary",
                on_click=submit,
            )
        st.markdown("##### Or let a tool find material")
        with st.container(border=True):
            st.markdown(f"{META} **Meta Ad Library**", unsafe_allow_html=True)
            st.caption("Search ads by keyword, page or ad ID. Paste a found ad's link above.")
            with st.container(horizontal=True, vertical_alignment="bottom"):
                terms = st.text_input(
                    "Search the Ad Library",
                    key="ad_terms",
                    placeholder="inwestycja, an advertiser's name or an ad ID",
                    label_visibility="collapsed",
                ).strip()
                url = (
                    ad_library_url(ad_id=terms) if terms.isdigit() else ad_library_url(terms=terms)
                )
                st.link_button("Search", url, icon=":material/open_in_new:", disabled=not terms)
            if DISCOVERY:
                st.caption("Automated searches: `ftf discover run`, then review the ads it found.")
    if st.session_state.pop("go_library", False):
        st.switch_page(PAGES["library"])


# -- Library -------------------------------------------------------------------------------


@st.cache_resource
def library_cache():
    return {}, threading.Lock()


_CACHE, _CACHE_LOCK = library_cache()


def current_library() -> Library | None:
    """The last complete Library. Rebuilt only while no analysis runs, so it is never torn."""
    key = str(settings.db_path)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and cached["generation"] == worker.generation:
            return cached["library"]
        if not ANALYSIS_LOCK.acquire(blocking=False):
            return cached["library"] if cached else None
        try:
            reader = Store(settings.db_path)
            try:
                groups = build_groups(reader, persist_funnel=False)
                previous = cached["library"] if cached else None
                library = build_library(reader, groups, settings.data_dir, previous)
                snapshot = reader.db.serialize()
            finally:
                reader.close()
        finally:
            ANALYSIS_LOCK.release()
        _CACHE[key] = {"generation": worker.generation, "library": library, "snapshot": snapshot}
        return library


def _row_key(prefix: str, view_id: str, selected: bool) -> str:
    return f"{prefix}-{re.sub(r'[^A-Za-z0-9]', '-', view_id)}{'-sel' if selected else ''}"


def risk_badge(item: Item) -> str:
    if item.risk.level == "error":
        return f":red[:material/report: {item.risk.count}]"
    if item.risk.level == "warning":
        return f":orange[:material/warning: {item.risk.count}]"
    return ""


def label_badges(item: Item) -> str:
    return " ".join(f":{BADGES[label.kind]}-badge[{label.text}]" for label in item.labels)


def select(view_id: str) -> None:
    st.session_state["selected"] = view_id
    st.session_state.pop("library_compare", None)


def show_matches(view_id: str) -> None:
    st.session_state.setdefault("library_previous_selected", st.session_state.get("selected"))
    st.session_state["library_filter"] = {"matches_of": view_id}
    select(view_id)


def close_matches() -> None:
    st.session_state.pop("library_filter", None)
    st.session_state.pop("library_compare", None)
    previous = st.session_state.pop("library_previous_selected", None)
    if previous:
        st.session_state["selected"] = previous


def item_row(item: Item, show_case: bool, selected: bool) -> None:
    prefix = "row"
    if item.step:  # the steps of one journey are joined by a line through their dots
        place = {1: "first", item.step.total: "last"}.get(item.step.position, "mid")
        prefix = f"row-step{place}"
    with st.container(
        key=_row_key(prefix, item.view_id, selected), horizontal=True, vertical_alignment="center"
    ):
        dot = f'<span class="ftf-dot">{item.step.position}</span>' if item.step else "&nbsp;"
        st.markdown(dot, unsafe_allow_html=True, width=22)
        if item.thumbnail:
            st.image(item.thumbnail, width=36)
        else:
            with st.container(key=_row_key("icon", item.view_id, False), width=36):
                st.markdown(f":gray[{ICONS[KIND_NAMES[item.kind]]}]")
        with st.container(gap=None, width="stretch"):
            st.button(
                item.title,
                key=_row_key("title", item.view_id, False),
                type="tertiary",
                on_click=select,
                args=(item.view_id,),
            )
            sub = KIND_NAMES[item.kind] + (f" · {item.host}" if item.host else "")
            if show_case:
                sub += f" · {case_title(item.case_id)}"
            note = f" · :blue[{item.note}]" if item.note else ""
            st.caption(f"{label_badges(item)} {sub}{note}")
        st.markdown(risk_badge(item) or "&nbsp;", width=44)
        st.button(
            str(len(item.matches)) if item.matches else "",
            icon=":material/fingerprint:",
            key=f"matches-{item.view_id}",
            type="tertiary",
            disabled=not item.matches,
            help=match_help(item),
            on_click=show_matches,
            args=(item.view_id,),
        )


def pending_rows(library: Library | None, show: bool) -> None:
    """Submissions the last complete Library does not have yet, with their stage."""
    known = {c.case_id for c in library.cases} if library else set()
    for job in reversed(list(worker.jobs.values())):
        if job.status == "done" and set(job.incidents) <= known:
            continue
        if job.status == "failed":
            st.error(f"Couldn't analyse {', '.join(job.names[:3])}: {job.error}")
            continue
        stage = f":gray-badge[:material/progress_activity: {job.stage_name}]"
        new_incidents = [i for i in job.incidents if i not in known]
        if not new_incidents:
            for name in job.names[:6]:
                st.markdown(f":material/schedule: {name} {stage}")
            if len(job.names) > 6:
                st.caption(f"and {len(job.names) - 6} more")
            continue
        for incident_id in new_incidents:
            for view in db().views(incident_id)[:12] if show else []:
                st.markdown(f"{ICONS[KIND_NAMES[view.kind]]} {view.label} {stage}")


def case_block(case, selected: str | None, kinds: set[str]) -> None:
    items = [i for i in case.items if KIND_NAMES[i.kind] in kinds]
    if not items:
        return
    with st.container(border=True):
        st.markdown(f"**{case.title}** :gray[· {len(case.items)} items]")
        quiet = [
            i
            for i in items
            if i.kind.value == "image" and i.labels[0].kind == "none" and i.risk.level == "none"
        ]
        folded = quiet[1:] if len(quiet) > 3 else []
        for item in items:
            if item not in folded:
                item_row(item, False, item.view_id == selected)
        if folded:
            with st.expander(f"{len(folded)} more images with no link yet"):
                for item in folded:
                    item_row(item, False, item.view_id == selected)


def ad_library_url(ad_id: str | None = None, terms: str | None = None) -> str:
    """Meta's public Ad Library: one ad by ID, or a keyword search in Poland."""
    base = "https://www.facebook.com/ads/library/?active_status=all&ad_type=all"
    if ad_id:
        return f"{base}&id={quote(ad_id)}"
    return f"{base}&country=PL&media_type=all&search_type=keyword_unordered&q={quote(terms or '')}"


def journey_html(library: Library, item: Item) -> str:
    """The journey as one line: dots for steps, proofs on the line, a dashed open end."""
    journey = library.journeys[item.view_id]
    rows = []

    def row(rail: str, text: str) -> None:
        rows.append(f'<div class="rail">{rail}</div><div>{text}</div>')

    for ad in journey.ads:
        row(
            f'{META}<span class="bar dash"></span>',
            f'<div class="txt">Meta ad {html.escape(ad)}<br>'
            "<small>Ads Manager ID · not in the library</small></div>",
        )
        row('<span class="bar"></span>', '<div class="proof">Its ID is in the landing URL</div>')
    last = len(journey.steps) - 1
    for n, (view_id, title) in enumerate(journey.steps):
        role = "Entry page" if n == 0 else "Destination so far" if n == last else "Step"
        me = view_id == item.view_id
        bar = '<span class="bar dash"></span>' if n == last else '<span class="bar"></span>'
        row(
            f'<span class="ftf-dot">{n + 1}</span>{bar}',
            f'<div class="txt"><span class="{"me" if me else ""}">{html.escape(title)}</span>'
            f"<br><small>{role}{' · this item' if me else ''}</small></div>",
        )
        if n < last:
            proof = html.escape(journey.proofs[n])
            row('<span class="bar"></span>', f'<div class="proof">{proof}</div>')
    row('<span class="bar dash"></span>', '<div class="proof">Next step not captured</div>')
    return f'<div class="ftf-j">{"".join(rows)}</div>'


def meta_link(label: str, url: str, title: str) -> None:
    """A link styled as a button, with Meta's mark (Streamlit buttons take only their icons)."""
    st.markdown(
        f'<a class="ftf-btn" href="{html.escape(url)}" target="_blank" rel="noopener" '
        f'title="{html.escape(title)}">{META} {html.escape(label)}</a>',
        unsafe_allow_html=True,
        width="content",
    )


def journey_actions(library: Library, item: Item) -> None:
    journey = library.journeys[item.view_id]
    on_path = [library.get(view_id) for view_id, _ in journey.steps]
    library_ids = [i.library_id for i in on_path if i and i.library_id]
    with st.container(horizontal=True):
        if library_ids:
            meta_link(
                "Find the Meta ad in Ad Library",
                ad_library_url(ad_id=library_ids[0]),
                "Opens this ad in Meta's Ad Library by its Library ID.",
            )
        elif journey.ads and journey.host:
            meta_link(
                f"Search Ad Library for {journey.host}",
                ad_library_url(terms=journey.host),
                "The landing URL carries the Ads Manager ad ID, which the Ad Library cannot "
                "look up. This searches the Library for ads that mention the landing domain.",
            )
        st.button(
            "Continue with victim visitor",
            icon=":material/person_search:",
            key=f"vv-{item.view_id}",
            disabled=not VICTIM,
            help=(
                "Walk on from the last captured page as a persona."
                if VICTIM
                else "Not in this build: the victim visitor lives on its own branch."
            ),
        )


def panel(library: Library, item: Item) -> None:
    with st.container(border=True):
        st.markdown(f"##### {ICONS[KIND_NAMES[item.kind]]} {item.title}")
        where = KIND_NAMES[item.kind] + (f" · {item.host}" if item.host else "")
        st.caption(f"{where} · {case_title(item.case_id)}")
        st.markdown(f"{label_badges(item)} {risk_badge(item)}")
        if item.note:
            st.caption(item.note)

        if item.view_id in library.journeys:
            steps = len(library.journeys[item.view_id].steps)
            st.markdown(f"**Journey** :gray[· {steps} known steps]")
            # st.html strips inline SVG (the Meta mark); a raw HTML block in markdown keeps it.
            st.markdown(journey_html(library, item), unsafe_allow_html=True)
            journey_actions(library, item)
        else:
            st.markdown("**Journey**")
            st.caption("No proven step to or from this item.")
            if item.library_id:
                meta_link(
                    "Find the Meta ad in Ad Library",
                    ad_library_url(ad_id=item.library_id),
                    "Opens this ad in Meta's Ad Library by its Library ID.",
                )

        st.markdown("**Findings**")
        if item.risk.findings:
            for finding in item.risk.findings:
                st.markdown(f":gray[{finding.kind}]  \n{finding.summary}")
                st.caption(f"`{finding.source}`")
        else:
            st.caption("No risk findings.")
        if item.risk.unchecked:
            st.caption("Not checked: " + ", ".join(item.risk.unchecked).lower())

        compare_section(library, item)
        warning_section(item)
        with st.expander("All observations"):
            rows = [
                {
                    "signal": o.signal_id,
                    "namespace": o.namespace,
                    "value": str(o.value),
                    "state": o.state.value,
                    "source": o.source.locator,
                }
                for o in db().observations(item.view_id)
            ]
            if rows:
                st.dataframe(rows, width="stretch", hide_index=True)
            else:
                st.caption("No observations for this item yet.")


def compare_section(library: Library, item: Item) -> None:
    with st.expander("Compare with another item"):
        others = [i for i in library.newest() if i.view_id != item.view_id]
        if not others:
            st.caption("Add more material to compare.")
            return
        other = st.selectbox(
            "Compare with",
            others,
            format_func=lambda i: f"{i.title} · {case_title(i.case_id)}",
            key=f"cmp-{item.view_id}",
        )
        busy = bool(worker.active())
        if st.button("Explain comparison", disabled=busy, help=BUSY if busy else None):
            report = library_pair_report(item.view_id, other.view_id)
            if report:
                st.session_state["comparison_report"] = report
            st.session_state.pop("narrative", None)
        report = st.session_state.get("comparison_report")
        if (
            report
            and report["objects"]["a"]["view_id"] == item.view_id
            and report["objects"]["b"]["view_id"] == other.view_id
        ):
            markdown = render_pair_report(report)
            with st.container(horizontal=True):
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
            st.markdown("**Narrative**")
            st.caption("Written by the model from the table above. Each sentence cites its facts.")
            pair_key = (item.view_id, other.view_id)
            if st.button("Write narrative", disabled=busy, help=BUSY if busy else None):
                with st.spinner("Writing from the evidence table"):
                    st.session_state["narrative"] = {
                        "pair": pair_key,
                        "result": pair_narrative(db(), item.view_id, other.view_id, report=report),
                    }
            written = st.session_state.get("narrative")
            if written and written["pair"] == pair_key:
                # Plain text: model output can repeat hostile links from the page.
                st.text(render_narrative(written["result"]))
                with st.expander("Facts given to the model"):
                    st.json(written["result"]["facts"])


def warning_section(item: Item) -> None:
    with st.expander("Warning lists"):
        offline = st.checkbox("Use offline warning snapshots", value=True)
        busy = bool(worker.active())
        if st.button("Check warning lists", disabled=busy, help=BUSY if busy else None):
            with st.spinner("Checking warning lists"), ANALYSIS_LOCK:
                run_warning_lists(
                    db(),
                    settings.data_dir / "warning_lists",
                    view_ids=[item.view_id],
                    refresh=not offline,
                )
            worker.invalidate()
            st.session_state.pop("comparison_report", None)
            st.session_state.pop("corpus_export", None)
            st.rerun()
        warnings = warning_observations(db(), item.view_id)
        if not warnings:
            st.caption("Warning lists have not been checked for this item.")
            return
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
            hide_index=True,
        )
        st.json(warnings, expanded=False)
        related = related_listed_views(db(), item.view_id)
        if related:
            st.caption("Items naming the same listed entity; context, not a campaign link.")
            st.dataframe(
                [{k: row[k] for k in ("view_id", "label", "entity_name")} for row in related],
                width="stretch",
                hide_index=True,
            )


def library_pair_report(a: str, b: str) -> dict | None:
    """Read the complete Library snapshot; never wait on background analysis."""
    if not ANALYSIS_LOCK.acquire(blocking=False):
        st.info(BUSY)
        return None
    try:
        snapshot = _CACHE[str(settings.db_path)]["snapshot"]
        reader = Store(":memory:")
        try:
            reader.db.deserialize(snapshot)
            return build_pair_report(reader, a, b)
        finally:
            reader.close()
    finally:
        ANALYSIS_LOCK.release()


def open_library_comparison(a: str, b: str) -> None:
    report = library_pair_report(a, b)
    if report:
        st.session_state["library_compare"] = report


def layer_breakdown(report: dict) -> None:
    with st.expander("Layer breakdown"):
        for row in report["layers"]:
            st.markdown(f"**{LAYER_NAMES.get(row['layer_id'], 'Evidence layer')}**")
            st.caption(f"`{row['layer_id']}` · Score: {row['score']} · {row['status']}")
            for shared in row["shared"]:
                st.text(str(shared["value"]))
                if shared["demoted_to_kit"]:
                    st.caption("Demoted to kit evidence")
                for side, refs in shared["sources"].items():
                    for ref in refs:
                        obs = report["sources"][ref]
                        source = obs["source"]
                        st.caption(
                            f"{side.upper()}: {obs['namespace']} · "
                            f"{source['file'] or 'File not recorded'} · "
                            f"{source['locator'] or 'Location not recorded'}"
                        )
                if not any(shared["sources"].values()):
                    st.caption("Source observation unavailable; derived comparison value")


def comparison_panel(report: dict) -> None:
    with st.container(border=True):
        with st.container(horizontal=True, vertical_alignment="center"):
            st.markdown("##### Comparison", width="stretch")
            if st.button("Close comparison", icon=":material/close:", type="tertiary"):
                st.session_state.pop("library_compare", None)
                st.rerun()
        st.write(" · ".join(obj["label"] for obj in report["objects"].values()))
        markdown = render_pair_report(report)
        with st.container(horizontal=True):
            st.download_button(
                "Download JSON",
                json.dumps(report, ensure_ascii=False, indent=2),
                file_name="comparison.json",
                mime="application/json",
            )
            st.download_button(
                "Download Markdown", markdown, file_name="comparison.md", mime="text/markdown"
            )
        layer_breakdown(report)
        with st.expander("Full comparison report", expanded=True):
            st.markdown(markdown)


def match_table(library: Library, anchor: Item, selected: str, kinds: set[str]) -> None:
    heading, close = st.columns([20, 1], vertical_alignment="top")
    heading.markdown(f"#### Matches for {anchor.title}")
    with close:
        st.button(
            "",
            icon=":material/close:",
            help="Close matches",
            key="close-matches",
            on_click=close_matches,
            type="tertiary",
        )
    item_row(anchor, True, anchor.view_id == selected)
    if not library.playbook_run:
        st.caption("Playbook: not run")
    rows = [
        m
        for m in filter_matches(anchor)
        if KIND_NAMES[library.get(m.view_id).kind] in kinds
    ]
    st.caption("Scroll sideways for coverage, clues and Compare.")
    st.html(
        """<style>
        .st-key-match-scroll {overflow-x: auto; padding-bottom: 12px}
        .st-key-match-table {min-width: 1120px}
        .ftf-tier {display: inline-block; padding: 2px 6px; border-radius: 4px;
            background: rgba(28, 131, 225, .12); font-size: .875em;
            white-space: nowrap; cursor: help}
        </style>"""
    )
    with st.container(key="match-scroll"), st.container(key="match-table", width=1120):
        widths = [2.2, 1, 1.2, 1, 1, 1.5, 1.5, 0.9]
        cols = st.columns(widths, gap="small")
        for col, title in zip(
            cols,
            [
                "Matched item",
                "Case",
                "Verdict",
                "Tiers",
                "Strength",
                "Coverage",
                "Deciding clue",
                "Compare",
            ],
            strict=True,
        ):
            col.caption(title)
        for match in rows:
            item = library.get(match.view_id)
            cols = st.columns(widths, gap="small", vertical_alignment="center")
            with cols[0]:
                if item.thumbnail:
                    st.image(item.thumbnail, width=36)
                st.button(
                    item.title,
                    key=f"match-title-{item.view_id}",
                    help=item.title,
                    type="tertiary",
                    on_click=select,
                    args=(item.view_id,),
                )
            cols[1].write(case_title(item.case_id) + (" · same case" if match.same_case else ""))
            cols[2].write(match.verdict)
            for tier in match.tiers:
                cols[3].markdown(
                    f'<span class="ftf-tier" title="{html.escape(TIER_HINTS[tier], quote=True)}">'
                    f'{html.escape(tier)}</span>',
                    unsafe_allow_html=True,
                )
            cols[4].markdown(
                match.strength, help=f"Stored confidence: {match.confidence or 'not assessed'}"
            )
            cols[5].write(match.coverage)
            cols[6].write(match.clue)
            cols[7].button(
                "Compare",
                key=f"match-compare-{item.view_id}",
                disabled=bool(worker.active()),
                help=BUSY if worker.active() else None,
                on_click=open_library_comparison,
                args=(anchor.view_id, item.view_id),
            )
    if not rows:
        st.caption("No matches for the selected material types.")


def library_body() -> None:
    library = current_library()
    active = worker.active()
    generation = worker.generation
    count = f"{len(library.items())} items · {len(library.cases)} cases" if library else ""
    with st.container(horizontal=True, vertical_alignment="center"):
        st.markdown(f"### Library :gray[{count}]", width="content")
        if active:
            job = active[0]
            st.markdown(f":material/progress_activity: :gray[Analysing · {job.stage_name}]")
        st.space("stretch")
        mode = st.segmented_control(
            "View",
            ["Newest", "By case"],
            default="Newest",
            key="mode",
            label_visibility="collapsed",
        )
        kinds = st.pills(
            "Show",
            ["Page", "Image", "Video"],
            format_func=lambda k: f"{k}s",
            selection_mode="multi",
            default=["Page", "Image", "Video"],
            key="kinds",
            label_visibility="collapsed",
        )
    kinds = set(kinds or ["Page", "Image", "Video"])
    if library is None or not library.cases:
        pending_rows(library, True)
        if not worker.jobs:
            st.info("Nothing here yet.")
            st.page_link(PAGES["add"], label="Add material", icon=":material/add:")
        return
    items = library.newest()
    selected = st.session_state.get("selected")
    if library.get(selected or "") is None:
        selected = items[0].view_id
        st.session_state["selected"] = selected
    matches_of = st.session_state.get("library_filter", {}).get("matches_of")
    anchor = library.get(matches_of) if matches_of else None
    if anchor:
        st.html('<style>[data-testid="stMainBlockContainer"] {max-width: 1800px}</style>')
    left, right = st.columns([3, 2], gap="medium")
    with left:
        pending_rows(library, True)
        if anchor:
            match_table(library, anchor, selected, kinds)
        elif mode == "By case":
            for case in library.cases:
                case_block(case, selected, kinds)
        else:
            for item in items:
                if KIND_NAMES[item.kind] in kinds:
                    item_row(item, True, item.view_id == selected)
    with right:
        report = st.session_state.get("library_compare")
        if report:
            comparison_panel(report)
        else:
            panel(library, library.get(selected))
    st.session_state["seen_generation"] = generation


def page_library() -> None:
    if worker.active():

        @st.fragment(run_every=1.5)
        def live() -> None:
            if not worker.active() and st.session_state.get("seen_generation") != worker.generation:
                st.rerun(scope="app")
            library_body()

        live()
    else:
        library_body()


# -- Campaigns, method, report (unchanged screens) ----------------------------------------


def number_rows(values: dict) -> list[dict]:
    return [{"setting": k, "value": float(v)} for k, v in values.items()]


def recompute() -> None:
    with ANALYSIS_LOCK:
        run_method(db())
    worker.invalidate()
    for key in ("comparison_report", "map_cache", "corpus_export"):
        st.session_state.pop(key, None)


def page_report() -> None:
    st.markdown("### Report")
    st.caption("Private project material: do not publish or redistribute.")
    if not db().incidents():
        st.info("Nothing to report yet.")
        return
    if st.button("Build report"):
        with ANALYSIS_LOCK, st.spinner("Building the report from stored results"):
            files = export_files(db())
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


def page_campaigns() -> None:
    st.markdown("### Campaigns")
    if not db().incidents():
        st.info("Nothing to show yet.")
        return
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
            cache[tuple(enabled)] = map_data(db(), enabled)
    data = cache[tuple(enabled)]
    st.caption("Boxes are families and campaigns. A line is a stored relation in a chosen tier.")
    st.graphviz_chart(to_dot(data), width="stretch")
    st.markdown("#### Clusters")
    for cluster in data["clusters"]:
        with st.expander(f"Family {cluster['family']}: {len(cluster['campaigns'])} campaign(s)"):
            for campaign, members in cluster["campaigns"].items():
                st.write(f"**{campaign}**: " + ", ".join(members))
    st.markdown("#### Links and shared identifiers")
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
    st.markdown("#### Pattern library")
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


def page_method() -> None:
    st.markdown("### Method")
    st.markdown("#### Layers")
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
    st.markdown("#### Weights and thresholds")
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
    st.markdown("#### Expectation profiles")
    profiles = expectation_profiles(db())
    if not profiles:
        st.info("Not run yet. Use `ftf perceive`.")
    for kind, rows in profiles.items():
        with st.expander(kind):
            st.dataframe(rows, width="stretch")


has_material = bool(db().incidents()) or bool(worker.jobs)
PAGES = {
    "add": st.Page(page_add, title="Add material", url_path="add", default=not has_material),
    "library": st.Page(page_library, title="Library", url_path="library", default=has_material),
    "campaigns": st.Page(page_campaigns, title="Campaigns", url_path="campaigns"),
    "method": st.Page(page_method, title="Method", url_path="method"),
    "report": st.Page(page_report, title="Report", url_path="report"),
}
st.navigation(list(PAGES.values()), position="top").run()
