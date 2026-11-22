"""`ftf victim …` commands (VV-60, VV-62).

    ftf victim personas
    ftf victim run <url> [--persona ID] [--max-steps N] [--live]
    ftf victim run replay:61            # the local replay of sample case 61
    ftf victim demo                     # replay:61 as retiree-income, paced and recorded for a video
    ftf victim perceive <run folder>    # (re)build the perception fingerprint
    ftf victim show <run folder>        # stages, proofs, forms, stop reason

A run always opens a visible browser window. A URL that is not on this machine is refused
unless `--live` is given: the mentor check, the mailbox and the phone range are still open
(requirements section 13).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from ..config import get_settings
from .contacts import contacts_for, load_contacts
from .persona import get_persona, load_personas, suggest_persona

DEFAULT_CASE_61 = Path("testing_sample/61")


def _resolve_case(arg: str | None) -> Path:
    sample = os.environ.get("FTF_SAMPLE_DIR")  # the same setting Docker uses for the sample
    candidates = (
        [Path(arg)]
        if arg
        else [
            *([Path(sample) / "61"] if sample else []),
            DEFAULT_CASE_61,
            Path(__file__).resolve().parents[3] / "testing_sample" / "61",
        ]
    )
    for path in candidates:
        if path.is_dir():
            return path
    raise SystemExit(
        f"case 61 not found (looked in {', '.join(map(str, candidates))}); pass --case"
    )


def cmd_personas(args: argparse.Namespace) -> None:
    book = load_contacts(get_settings().victim_contacts)
    for persona in load_personas().values():
        print(f"{persona.ref:22} {persona.summary}")
        print(f"{'':22} matches: {persona.matches}")
        for scope in ("local", "live"):
            c = contacts_for(book, persona, "run-id", local=scope == "local")
            email = c.email or ("a new mail.tm inbox per run" if c.provider == "mail.tm" else "-")
            print(f"{'':22} {scope:5} e-mail {email}, phone {c.phone or '-'}")
    print(f"\nContacts from {book.path} (version {book.version}); see docs/victim-contacts.md.")


def cmd_run(args: argparse.Namespace) -> None:
    from . import rules
    from .agent import RunOptions, VictimRun
    from .decide import ModelDecider, RuleDecider

    settings = get_settings()
    replay = None
    url = args.url
    if url.startswith("replay:"):
        from .replay import serve_case61

        if url != "replay:61":
            raise SystemExit("only replay:61 exists")
        replay = serve_case61(_resolve_case(args.case))
        url = replay.ad_url
        print(f"Replay of sample case 61 served locally at {replay.case.origin} (not a live site).")
    elif not rules.is_local(url) and not args.live:
        raise SystemExit(
            "Refusing a live URL without --live: the mentor check, the team mailbox and the safe "
            "phone range are still open (docs/victim-visitor-requirements.md section 13)."
        )
    if args.persona:
        persona = get_persona(args.persona)
    else:
        pid, why = suggest_persona(" ".join(filter(None, [args.ad_text, url])))
        persona = get_persona(pid)
        print(f"Persona suggested: {persona.ref} ({why}). Choose another with --persona.")
    if args.decider == "model" or (args.decider == "auto" and settings.openai_api_key):
        decider: Any = ModelDecider(settings)
    else:
        decider = RuleDecider()
        if args.decider == "auto":
            print("No OPENAI_API_KEY: actions are chosen by the persona rules, not the model.")
    book = load_contacts(args.contacts or settings.victim_contacts)
    preview = contacts_for(book, persona, "run-id", local=rules.is_local(url))
    print(f"Contacts ({preview.scope} scope of {book.path}, version {book.version}):")
    print(f"  e-mail: {preview.email or '-'} ({preview.email_policy})")
    print(f"  phone:  {preview.phone or '-'} ({preview.phone_policy})")
    options = RunOptions(
        max_steps=args.max_steps,
        pace=args.pace,
        human_wait_s=args.human_wait,
        max_seconds=args.max_minutes * 60,
        record_video=not args.no_video,
        overlay=not args.no_overlay,
        demo=args.demo,
    )
    out_dir = Path(args.out) if args.out else settings.victim_dir
    try:
        result = VictimRun(
            url,
            persona,
            out_dir,
            settings=settings,
            decider=decider,
            options=options,
            fallback=replay.case.fallback if replay else None,
            replay=replay is not None,
            contacts=book,
        ).run()
    finally:
        if replay is not None:
            replay.close()
    print(f"\nRun {result.run_id}: {len(result.stages)} stages, {result.steps} steps.")
    print(f"Stopped: {result.stop_reason} — {result.stop_detail}")
    print(f"Written to {result.root}")
    for clip in (result.manifest.get("recording") or {}).get("clips", []):
        print(f"Video: {result.root / clip['video']}  (captions {clip['captions']})")
    if not args.no_perceive:
        _perceive(result.root, persona)
    if not args.no_ingest:
        _ingest(result.root)
    _show(result.root)


def _perceive(root: Path, persona: Any) -> None:
    from .perceive import perceive_run

    p = perceive_run(root, persona)
    if p.get("state") != "present":
        print(f"Perception not observable: {p.get('reason')} (needs the reading model).")
        return
    kept = sum(len(p[f]["items"]) for f in p if isinstance(p.get(f), dict) and "items" in p[f])
    print(f"Perception: {kept} items kept with quotes found, {len(p.get('dropped', []))} dropped.")


def _ingest(root: Path) -> None:
    """VV-62: ingest and compare the run as soon as it ends."""
    from .. import pipeline
    from ..ingest import ingest_folder
    from ..store import Store

    store = Store(get_settings().db_path)
    incident, views = ingest_folder(root, store)
    ids = [v.view_id for v in views]
    pipeline.run_extract(store, ids)
    pipeline.run_fingerprint(store, ids)
    pipeline.run_compare_all(store)
    print(f"Ingested as incident {incident.incident_id}: {len(views)} views.")
    labels = {v.view_id: v.label for v in store.views()}
    for view in views:
        for o in store.observations(view.view_id):
            if o.namespace == "funnel.edge":
                proof = json.loads(o.raw or "{}")
                print(
                    f"  funnel {labels.get(o.value['from_view'], o.value['from_view'])} -> {view.label}: "
                    f"{proof.get('how')} at step {proof.get('step')}; redirects {proof.get('http_redirects')}"
                )
    others = {}
    for view in views:
        for e in store.evidence(view_a=view.view_id, min_score=0.2):
            other = e.view_b if e.view_a == view.view_id else e.view_a
            if other.split(":")[0] != incident.incident_id:
                others.setdefault(other, []).append(f"{e.layer_id} {e.score:.2f}")
    for other, layers in sorted(others.items()):
        print(f"  linked to {labels.get(other, other)}: {', '.join(layers)}")
    shared = shared_identifiers(store, incident.incident_id)
    for (ns, value), where in sorted(shared.items()):
        print(f"  shares {ns}={str(value)[:60]} with {', '.join(sorted(where))}")
    if not others and not shared:
        print("  no links to other material yet")


def shared_identifiers(store: Any, incident_id: str) -> dict[tuple[str, str], set[str]]:
    """Identity-type values (signal groups A and I) the run shares with other incidents."""
    out: dict[tuple[str, str], set[str]] = {}
    for view in store.views(incident_id):
        for o in store.observations(view.view_id):
            if o.state.value != "present" or not o.signal_id.startswith(("A", "I")):
                continue
            if o.namespace.startswith(("funnel.", "victim.")) or not isinstance(o.value, str):
                continue
            others = {v.split(":")[0] for v in store.views_with_value(o.namespace, o.value)}
            others.discard(incident_id)
            if others:
                out.setdefault((o.namespace, o.value), set()).update(others)
    return out


def _show(root: Path) -> None:
    run = json.loads((root / "run_files" / "run.json").read_text(encoding="utf-8"))
    print(f"\nPersona {run['persona']['id']}@{run['persona']['version']}, start {run['start_url']}")
    for stage in run["stages"]:
        arrived = stage.get("arrived_from") or {}
        print(f"  stage {stage['index']} [{'/'.join(stage['kinds'])}] {stage['url'][:100]}")
        print(
            f"      reached by {arrived.get('how')} at step {arrived.get('step')}"
            + (
                f" via {len(arrived['http_redirects'])} HTTP redirect(s)"
                if arrived.get("http_redirects")
                else ""
            )
        )
    print(f"  funnel shape: {' > '.join(run.get('funnel_shape', []))}")
    print(f"  stop: {run['stop_reason']} — {run['stop_detail']}")


def cmd_demo(args: argparse.Namespace) -> None:
    """The demo run: the local replay of case 61, one persona, paced and recorded."""
    defaults = {
        "url": "replay:61",
        "persona": args.persona or "retiree-income",
        "ad_text": None,
        "max_steps": 30,
        "max_minutes": 6.0,
        "human_wait": 60.0,
        "pace": 1.0,
        "decider": args.decider,
        "case": args.case,
        "out": args.out,
        "live": False,
        "contacts": None,
        "no_perceive": args.no_perceive,
        "no_ingest": args.no_ingest,
        "no_video": False,
        "no_overlay": False,
        "demo": True,
    }
    cmd_run(argparse.Namespace(**defaults))


def cmd_perceive(args: argparse.Namespace) -> None:
    root = Path(args.run)
    run = json.loads((root / "run_files" / "run.json").read_text(encoding="utf-8"))
    _perceive(root, get_persona(run["persona"]["id"]))
    if not args.no_ingest:
        _ingest(root)


def cmd_mail(args: argparse.Namespace) -> None:
    """VV-33: read the run's mail.tm inbox; record new messages; re-ingest the run."""
    from .mailbox import MailboxError, fetch_mail

    root = Path(args.run)
    if not (root / "run_files" / "mailbox.json").is_file():
        raise SystemExit(f"{root} has no mail.tm inbox (only live runs with provider mail.tm do)")
    try:
        new = fetch_mail(root)
    except MailboxError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"{len(new)} new message(s)")
    for m in new:
        print(f"  {m['received_at']}  from {m['from']}  {m['subject'][:80]}")
        if m["link_hosts"]:
            print(f"      links to: {', '.join(m['link_hosts'])}")
    if new and not args.no_ingest:
        _ingest(root)


def cmd_show(args: argparse.Namespace) -> None:
    _show(Path(args.run))


def add_parser(sub: Any) -> None:
    victim = sub.add_parser("victim", help="walk a scam funnel as a persona (visible browser)")
    vsub = victim.add_subparsers(dest="victim_command", required=True)

    vsub.add_parser("personas", help="list the persona library").set_defaults(func=cmd_personas)

    p = vsub.add_parser("run", help="walk a funnel from an ad link; always opens a visible window")
    p.add_argument("url", help="the ad's link with its query string, or replay:61")
    p.add_argument("--persona", help="persona id; suggested from the ad text if omitted")
    p.add_argument("--ad-text", help="the ad's text, used to suggest a persona")
    p.add_argument("--max-steps", type=int, default=40)
    p.add_argument("--max-minutes", type=float, default=10.0)
    p.add_argument(
        "--human-wait", type=float, default=600.0, help="seconds to wait for a person at a CAPTCHA"
    )
    p.add_argument(
        "--pace", type=float, default=1.0, help="1 = persona reading speed, 0 = no waits"
    )
    p.add_argument("--decider", choices=["auto", "model", "rules"], default="auto")
    p.add_argument("--case", help="folder of sample case 61, for replay:61")
    p.add_argument("--out", help="where to write the run (default: FTF_VICTIM_DIR or data/victim)")
    p.add_argument("--live", action="store_true", help="allow a URL that is not on this machine")
    p.add_argument(
        "--contacts",
        help="contacts file (default: FTF_VICTIM_CONTACTS, else src/ftf/victim/contacts.json)",
    )
    p.add_argument("--no-perceive", action="store_true")
    p.add_argument("--no-ingest", action="store_true")
    p.add_argument(
        "--demo", action="store_true", help="short reading waits, a pause on each highlight"
    )
    p.add_argument("--no-video", action="store_true", help="do not record the browser")
    p.add_argument(
        "--no-overlay", action="store_true", help="no caption bar or highlight on the page"
    )
    p.set_defaults(func=cmd_run)

    p = vsub.add_parser(
        "demo", help="replay:61 as retiree-income, paced and recorded for a video (no live site)"
    )
    p.add_argument("--persona", help="persona id (default retiree-income)")
    p.add_argument("--decider", choices=["auto", "model", "rules"], default="rules")
    p.add_argument("--case", help="folder of sample case 61")
    p.add_argument("--out", help="where to write the run (default: FTF_VICTIM_DIR or data/victim)")
    p.add_argument("--no-perceive", action="store_true")
    p.add_argument("--no-ingest", action="store_true")
    p.set_defaults(func=cmd_demo)

    p = vsub.add_parser("perceive", help="build the perception fingerprint of a finished run")
    p.add_argument("run")
    p.add_argument("--no-ingest", action="store_true")
    p.set_defaults(func=cmd_perceive)

    p = vsub.add_parser("mail", help="read a run's mail.tm inbox and record what arrived")
    p.add_argument("run")
    p.add_argument("--no-ingest", action="store_true")
    p.set_defaults(func=cmd_mail)

    p = vsub.add_parser("show", help="summarise a finished run")
    p.add_argument("run")
    p.set_defaults(func=cmd_show)
