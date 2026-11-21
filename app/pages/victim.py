"""Victim visitor (VV-61): start a run, watch it, read the funnel and the perception.

The run itself is `ftf victim run` in its own process: it opens a visible browser window on
this machine. This page starts it, shows its live state and sends pause, resume and stop.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import streamlit as st

from ftf.config import get_settings
from ftf.store import Store
from ftf.victim.control import write_command
from ftf.victim.persona import load_personas

st.set_page_config(page_title="Victim visitor", layout="wide")
st.title("Victim visitor")
st.caption(
    "Walks a scam funnel as a synthetic persona in a visible browser window, never pays, "
    "and records every stage with the navigation that proves it."
)

settings = get_settings()
runs_dir = settings.victim_dir
personas = load_personas()


def _jsonl(path: Path) -> list[dict]:
    try:
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]
    except (OSError, ValueError):
        return []


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


with st.expander("Start a run", expanded=not runs_dir.exists()):
    url = st.text_input(
        "Ad link (with its query string), or replay:61 for the local replay of case 61",
        "replay:61",
    )
    persona_id = st.selectbox(
        "Persona",
        ["(suggest from the ad)", *personas],
        format_func=lambda p: p if p not in personas else f"{p} — {personas[p].summary}",
    )
    cols = st.columns(3)
    max_steps = cols[0].number_input("Max steps", 1, 200, 40)
    pace = cols[1].number_input("Pace (1 = persona speed)", 0.0, 3.0, 1.0, 0.1)
    live = cols[2].checkbox("Live site (needs the mentor check)", value=False)
    if st.button("Start run", type="primary"):
        cmd = [
            sys.executable,
            "-m",
            "ftf.cli",
            "victim",
            "run",
            url,
            "--max-steps",
            str(max_steps),
            "--pace",
            str(pace),
        ]
        if persona_id in personas:
            cmd += ["--persona", persona_id]
        if live:
            cmd.append("--live")
        runs_dir.mkdir(parents=True, exist_ok=True)
        log = open(runs_dir / "last-run.log", "w", encoding="utf-8")  # noqa: SIM115
        subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
        st.success("Started: a browser window opens on this machine.")

runs = sorted(runs_dir.glob("victim-*"), reverse=True) if runs_dir.exists() else []
if not runs:
    st.info("No runs yet.")
    st.stop()
root = st.selectbox("Run", runs, format_func=lambda p: p.name)
files = root / "run_files"


@st.fragment(run_every="2s")
def live_view() -> None:
    state = _json(files / "live.json")
    run = _json(files / "run.json")
    status = state.get("status", "finished" if run else "unknown")
    st.subheader(f"Status: {status}")
    if status not in {"finished", "unknown"}:
        c = st.columns(3)
        if c[0].button("Pause"):
            write_command(files, "pause")
        if c[1].button("Resume"):
            write_command(files, "resume")
        if c[2].button("Stop"):
            write_command(files, "stop")
    if state.get("notice"):
        st.warning(state["notice"])
    left, right = st.columns([2, 3])
    with left:
        st.markdown(
            f"**Persona** {state.get('persona', '')}  \n**Step** {state.get('step', '')}  "
            f"\n**Stage** {state.get('stage', '')}"
        )
        st.markdown(f"**Next action** {state.get('next', '')}  \n**Why** {state.get('reason', '')}")
        steps = [s for s in _jsonl(files / "steps.jsonl")]
        st.dataframe(
            [
                {
                    "step": s.get("step"),
                    "actor": s.get("actor"),
                    "action": s.get("chosen") or s.get("event"),
                    "why": s.get("reason", ""),
                    "outcome": s.get("outcome", ""),
                }
                for s in steps
            ][::-1],
            width="stretch",
            height=300,
        )
    with right:
        shots = sorted((files / "steps").glob("step-*.png"))
        if shots:
            st.image(str(shots[-1]), caption=shots[-1].name)


live_view()

run = _json(files / "run.json")
if not run:
    st.stop()

st.header("Funnel")
st.caption(f"Stop: **{run['stop_reason']}** — {run['stop_detail']}")
st.markdown(" → ".join(f"`{k}`" for k in run.get("funnel_shape", [])))
for stage in run.get("stages", []):
    arrived = stage.get("arrived_from") or {}
    if stage["index"] > 1:
        hops = arrived.get("http_redirects") or []
        st.markdown(
            f"↓ *{arrived.get('how')}* at step {arrived.get('step')}: {arrived.get('action', '')}"
            + (f"  \n↳ HTTP redirects: {', '.join(hops)}" if hops else "")
        )
    c1, c2 = st.columns([1, 3])
    shot = root / stage["dir"] / "screenshot.png"
    if shot.is_file():
        c1.image(str(shot), width=220)
    c2.markdown(f"**Stage {stage['index']}** · {' / '.join(stage['kinds'])}  \n`{stage['url']}`")

forms = _jsonl(files / "forms.jsonl")
if forms:
    st.header("Forms")
    for f in forms:
        st.markdown(
            f"Stage {f.get('stage')}: asked for "
            + ", ".join(
                f"{x['label'] or x['name']}{'*' if x.get('required') else ''}" for x in f["fields"]
            )
            + (" — **sent**" if f.get("sent") else f" — **not sent**: {f.get('stop_detail', '')}")
        )
        if f.get("sent_to") or f.get("sent_while_typing"):
            st.json(
                {
                    "sent_to": f.get("sent_to", []),
                    "sent_while_typing": f.get("sent_while_typing", []),
                },
                expanded=False,
            )

chat = _jsonl(files / "chat.jsonl")
if chat:
    with st.expander(f"Chat transcript ({len(chat)} messages)"):
        for m in chat:
            who = "assistant" if m["who"] == "bot" else "user"
            with st.chat_message(who):
                st.write(m["text"] + (" *(improvised)*" if m.get("improvised") else ""))

st.header("What are they trying to convince me of?")
p = _json(files / "perception.json")
if not p:
    st.caption("No perception yet: run `ftf victim perceive <run folder>`.")
elif p.get("state") != "present":
    st.caption(f"Not observable: {p.get('reason')}")
else:
    for field in (
        "asked_action",
        "asked_data",
        "money_path",
        "promise",
        "belief",
        "borrowed_authority",
        "levers",
        "doubt_handling",
        "tailoring",
        "next_step_promised",
        "verdict",
    ):
        section = p.get(field, {})
        st.markdown(f"**{field.replace('_', ' ').capitalize()}**")
        if not section.get("items"):
            st.caption(f"empty: {section.get('empty_reason')}")
        for item in section.get("items", []):
            label = {k: v for k, v in item.items() if k not in {"quote", "evidence"}}
            st.markdown(
                f"- {label}  \n  > {item['evidence']['quote_found']}  \n  "
                f"<small>{item['evidence'].get('label', '')}</small>",
                unsafe_allow_html=True,
            )
    if p.get("dropped"):
        st.caption(f"{len(p['dropped'])} items dropped because their quote was not found.")

st.header("What the run linked to")
store = Store(settings.db_path)
views = store.views(root.name)
if not views:
    st.caption("Not ingested yet (`ftf ingest` the run folder, or let the run finish).")
else:
    labels = {v.view_id: v.label for v in store.views()}
    rows = []
    for view in views:
        for e in store.evidence(view_a=view.view_id, min_score=0.2):
            other = e.view_b if e.view_a == view.view_id else e.view_a
            if other.split(":")[0] == root.name:
                continue
            rows.append(
                {
                    "stage": view.primary,
                    "other view": labels.get(other, other),
                    "layer": e.layer_id,
                    "score": round(e.score, 3),
                    "shared": "; ".join(f"{s['ns']}={s['value']}" for s in e.shared),
                }
            )
    from ftf.victim.cli import shared_identifiers

    shared = shared_identifiers(store, root.name)
    rows += [
        {
            "stage": "",
            "other view": ", ".join(sorted(where)),
            "layer": "shared identifier",
            "score": None,
            "shared": f"{ns}={value}",
        }
        for (ns, value), where in sorted(shared.items())
    ]
    if rows:
        st.dataframe(rows, width="stretch")
    else:
        st.caption("No links to other material yet.")
