"""Command line: ftf capture | ingest | run | enrich | perceive | views | suspicions | observations |
compare | pairs | relations | explain | groups | incidents | assign | eval | loio | ablate |
validate | load-fixtures."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import pipeline
from .config import get_settings
from .contracts import RelationType
from .fusion import run_method
from .ingest import ingest_path
from .store import Store


def _store() -> Store:
    from .corpus import ensure_seeded

    store = Store(get_settings().db_path)
    ensure_seeded(store)
    return store


def cmd_ingest(args) -> None:
    from .intake import IntakeError, refuse_known_overwrite

    store = _store()
    if not args.replace_known:
        try:
            refuse_known_overwrite(store, Path(args.path))
        except IntakeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
    total = 0
    for incident, views in ingest_path(Path(args.path), store):
        total += len(views)
        kinds = {}
        for v in views:
            kinds[v.kind.value] = kinds.get(v.kind.value, 0) + 1
        summary = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) or "no views"
        print(f"incident {incident.incident_id}: {summary}")
    if total == 0:
        # A silent success here hides a wrong mount (Docker creates an empty /sample).
        print(
            f"error: no views found in {args.path}. In Docker, set FTF_SAMPLE_DIR "
            "to the folder with the material.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if not args.no_run:
        cmd_run(args)


def _capture_dir(url: str | None, replay: Path | None) -> Path:
    from datetime import UTC, datetime
    from urllib.parse import urlsplit

    host = urlsplit(url if url and "//" in url else f"//{url or ''}").hostname
    name = host or (replay.stem if replay else "capture")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return get_settings().data_dir / "captures" / f"{name}-{stamp}"


def cmd_capture(args) -> None:
    """Capture a URL (or replay an archived HAR) into a bundle; optionally ingest and run it."""
    from .capture import capture_url
    from .ingest import ingest_folder

    replay = Path(args.replay) if args.replay else None
    if not args.url and replay is None:
        print("error: give a URL, or --replay with an archived HAR", file=sys.stderr)
        raise SystemExit(2)
    incident_dir = Path(args.out) if args.out else _capture_dir(args.url, replay)
    if args.cloaking or args.crawler:  # --crawler only makes sense inside a differential
        _capture_cloaking(args, incident_dir)
        return
    result = capture_url(
        args.url,
        incident_dir / "bundle",
        replay_har=replay,
        proxy=args.proxy,
        timeout=args.timeout,
    )
    man = result.manifest
    print(f"capture {man.mode}: {man.status.value}  {man.page_url}")
    if man.error:
        print(f"  error: {man.error}")
    if man.final_url and man.final_url != man.page_url:
        print(f"  landed on: {man.final_url}")
    for hop in man.redirects:
        status = f" {hop.status}" if hop.status else ""
        print(f"  hop {hop.index}: {hop.kind.value}{status}  {hop.from_url} -> {hop.to_url}")
    if man.server and man.server.ip:
        print(f"  server: {man.server.ip}:{man.server.port}  HTTP {man.server.status}")
    print(
        f"  files: {', '.join(sorted(man.files.values())) or 'manifest only'}; "
        f"resources: {len(man.resources)}; blocked: {len(man.blocked)}"
    )
    print(f"  bundle: {result.bundle}")
    if args.ingest:
        # A dead domain is still material: its view keeps the failure and gets DNS and RDAP.
        _, views = ingest_folder(incident_dir, _store(), args.incident)
        for v in views:
            print(f"view {v.view_id}  {v.label}")
        cmd_run(args)
    if not result.ok:
        raise SystemExit(3)


def _capture_cloaking(args, incident_dir: Path) -> None:
    """One URL under several visitor profiles; with --ingest, the difference score per pair."""
    from .capture import capture_differential, differential_profiles
    from .ingest import ingest_folder

    if not args.url or args.replay:
        print("error: --cloaking needs a live URL (a replay serves one recording)", file=sys.stderr)
        raise SystemExit(2)
    result = capture_differential(
        args.url,
        incident_dir,
        profiles=differential_profiles(crawler=args.crawler),
        proxy=args.proxy,
        timeout=args.timeout,
    )
    for name, r in result.captures.items():
        man = r.manifest
        landed = f" -> {man.final_url}" if man.final_url and man.final_url != man.page_url else ""
        print(f"{name:9} {man.status.value:18} {man.page_url}{landed}")
        if man.error:
            print(f"          error: {man.error}")
    print(f"  incident: {incident_dir}")
    if args.ingest:
        store = _store()
        _, views = ingest_folder(incident_dir, store, args.incident)
        for v in views:
            print(f"view {v.view_id}  {v.label}")
        cmd_run(args)
        for v in views:
            for o in store.observations(v.view_id):
                if o.namespace.startswith("cloak."):
                    print(f"  {o.signal_id} {o.namespace} [{o.state.value}] {o.raw}")
    if not result.ok:
        raise SystemExit(3)


def _compare_and_group(store, args) -> None:
    """Shared tail of run and load-fixtures. With --candidates the brute-force pass is skipped:
    fusion compares the candidate pairs itself and stores their evidence."""
    candidates = getattr(args, "candidates", False)
    if not candidates:
        print(f"evidence rows: {pipeline.run_compare_all(store)}")
    groups = run_method(store, candidates=candidates)
    print(
        f"relations: {len(store.relations())}; campaigns: {len(groups.campaigns)}; "
        f"families: {len(groups.families)}"
    )


def _print_enrich(counts) -> None:
    namespaces = sorted({ns for ns, _ in counts})
    if not namespaces:
        print("enrichment: no page views with a known domain")
        return
    for ns in namespaces:
        present, missing = counts[(ns, "present")], counts[(ns, "not_observable")]
        print(f"  {ns:22} present {present:4}  not observable {missing:4}")


def cmd_run(args) -> None:
    store = _store()
    print(f"observations: {pipeline.run_extract(store)}")
    if not getattr(args, "no_enrich", False):
        print("enrichment (DNS, RDAP):")
        _print_enrich(pipeline.run_enrich(store))
    _cached_warning_lists(store)
    print(f"fingerprints: {pipeline.run_fingerprint(store)}")
    if cloaking := pipeline.run_cloaking(store):
        print(f"cloaking differential rows: {cloaking}")
    _compare_and_group(store, args)


def cmd_load_fixtures(args) -> None:
    """Load the hand-made fixture observations into the configured store and run everything."""
    from .eval.fixtures import OBSERVATIONS, store_from_fixtures

    store = store_from_fixtures(Path(args.path) if args.path else OBSERVATIONS, store=_store())
    print(f"views: {len(store.views())}")
    print(f"fingerprints: {pipeline.run_fingerprint(store)}")
    _compare_and_group(store, args)


def cmd_perceive(args) -> None:
    from .perception.expectations import run_expectations
    from .perception.faces import run_perceive_faces
    from .perception.image import run_perceive
    from .perception.page import run_perceive_pages
    from .perception.techniques import run_techniques
    from .registry import Context

    ctx = Context(_store())
    for label, run in (("image", run_perceive), ("page", run_perceive_pages)):
        summary = run(ctx, args.view or None)
        print(
            f"{label} views: {summary['views']}, read: {summary['read']}, "
            f"not observable: {summary['not_observable']}, "
            f"failed but kept earlier reading: {summary['kept']}, "
            f"observations: {summary['observations']}"
        )
    # After page reading, which names each page's kind.
    checks = run_expectations(ctx, args.view or None)
    print(
        f"expectation views: {checks['views']}, checked: {checks['checked']}, "
        f"not observable: {checks['not_observable']}, "
        f"failed but kept earlier check: {checks['kept']}, profiles: {checks['profiles']}"
    )
    faces = run_perceive_faces(ctx, args.view or None)
    print(f"faces: {faces}")
    # After image and page reading, so a screenshot's text can be quoted as technique evidence.
    cards = run_techniques(ctx, args.view or None)
    print(
        f"technique views: {cards['views']}, with cards: {cards['with_cards']}, "
        f"not observable: {cards['not_observable']}, "
        f"failed but kept earlier cards: {cards['kept']}, cards: {cards['cards']}"
    )
    _cached_warning_lists(ctx.store, args.view or None)


def _cached_warning_lists(store, view_ids=None) -> None:
    from .enrich.warning_lists import run_warning_lists

    summary = run_warning_lists(
        store,
        get_settings().data_dir / "warning_lists",
        view_ids=view_ids,
    )
    print(f"warning-list observations: {summary['observations']} (offline snapshots)")


def cmd_enrich(args) -> None:
    from .enrich.warning_lists import run_warning_lists
    from .enrich.warning_sources import cache_snapshot, make_snapshot

    cache = get_settings().data_dir / "warning_lists"
    if args.import_gambling:
        if not args.snapshot_date:
            raise SystemExit("--snapshot-date YYYY-MM-DD is required for a manual snapshot")
        try:
            content = Path(args.import_gambling).read_bytes().decode("utf-8-sig")
            snapshot = make_snapshot(
                "gambling",
                content,
                f"{args.snapshot_date}T00:00:00+00:00",
                access="manual",
            )
            cache_snapshot(snapshot, cache)
        except (OSError, ValueError) as exc:
            raise SystemExit(f"cannot import manual gambling snapshot: {exc}") from exc
    store = _store()
    infrastructure = pipeline.run_enrich(store, args.view) if not args.offline else {}
    summary = run_warning_lists(
        store,
        cache,
        view_ids=args.view,
        refresh=not args.offline,
    )
    summary["infrastructure"] = [
        {"namespace": ns, "state": state, "count": count}
        for (ns, state), count in sorted(infrastructure.items())
    ]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    store.close()


def cmd_patterns(args) -> None:
    """Model stage: needs technique cards from `ftf perceive` and groups from `ftf run`."""
    from .patterns.workflow import library_rows, run_discover, run_match, run_stability
    from .registry import Context

    ctx = Context(_store())
    if args.action == "list":
        for row in library_rows():
            print(row)
        return
    if args.action == "stability":
        print(run_stability(ctx))
        return
    run = run_discover if args.action == "discover" else run_match
    summary = run(ctx, args.view or None)
    print(summary)
    if summary.get("reason"):
        raise SystemExit(1)
    print("run `ftf run` to rebuild fingerprints, comparisons and groups with pattern.score")


def cmd_corpus(args) -> None:
    from .corpus import CorpusError, build_corpus, corpus_dir, load_corpus

    target = Path(args.dir) if args.dir else corpus_dir()
    try:
        if args.action == "build":
            if not args.sample:
                raise CorpusError("build needs the sample folder")
            manifest = build_corpus(Path(args.sample), target)
            print(f"corpus written to {target}: {manifest['counts']}")
        else:
            store = Store(get_settings().db_path)
            print(f"loaded {load_corpus(store, target)} known views")
    except CorpusError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def cmd_report(args) -> None:
    """Write the corpus report and JSON/CSV exports from stored results."""
    from .corpus_report import write_export

    store = _store()
    if not store.views():
        print("error: nothing ingested yet", file=sys.stderr)
        raise SystemExit(1)
    out = Path(args.out) if args.out else get_settings().data_dir / "export"
    paths = write_export(store, out)
    print(f"wrote {len(paths)} files to {out}")
    print("private project material: do not publish or redistribute")


def cmd_demo(args) -> None:
    """Rehearse the demo sequence on a temporary store; stored data is not touched."""
    from .demo import run_demo

    def show(step) -> None:
        print(f"[{step.status.upper():7}] {step.name}: {step.detail}", flush=True)

    print("mode: shipped settings, local text vectors, no model calls, no DNS, RDAP or crt.sh")
    steps = run_demo(
        Path(args.sample), live_url=args.live_url, capture=not args.no_capture, on_step=show
    )
    failed = sum(s.status == "failed" for s in steps)
    skipped = sum(s.status == "skipped" for s in steps)
    print(f"{len(steps) - failed - skipped} passed, {failed} failed, {skipped} skipped")
    if failed:
        raise SystemExit(1)


def cmd_ground(args) -> None:
    """Check expectation profiles against genuine pages (model and live capture)."""
    from .perception.grounding import load_grounding, run_grounding

    try:
        kinds = load_grounding() if args.show else run_grounding(args.kind or None)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    if args.kind:
        kinds = {kind: entry for kind, entry in kinds.items() if kind in args.kind}
    if not kinds:
        print("no grounding results yet")
        return
    for kind, entry in sorted(kinds.items()):
        print(f"{kind}: {entry['status']}")
        for page in entry.get("pages", []):
            share = page.get("control_present_share")
            control = "" if share is None else f", control present {share:.0%}"
            print(f"  page {page.get('status', '-')}{control}: {page['url']}")
        for key, row in entry.get("elements", {}).items():
            rate = row["false_alarm_rate"]
            shown = "n/a" if rate is None else f"{rate:.0%}"
            print(f"  {row['decision']:10} false alarms {shown:4} {key}")
        for key in entry.get("added", {}):
            print(f"  added      {key}")


def cmd_narrate(args) -> None:
    """Write a narrative for a pair or a campaign from the evidence table (model)."""
    from .narrative import campaign_narrative, pair_narrative, render_narrative

    store = _store()
    try:
        if args.campaign and args.views:
            raise ValueError("give two view ids or --campaign, not both")
        if args.campaign:
            result = campaign_narrative(store, args.campaign)
        elif len(args.views) == 2:
            result = pair_narrative(store, *args.views)
        else:
            raise ValueError("give two view ids, or --campaign")
    except (KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(render_narrative(result))
    if result["status"] != "available":
        raise SystemExit(1)
    if result["rejected"]:
        print(f"\n({result['rejected']} unsupported sentence(s) removed)")


def cmd_faces(args) -> None:
    """Set up the private face service and load the closed reference list."""
    from .faces.client import FaceServiceError, bootstrap, service_url
    from .faces.enroll import enroll

    url = service_url()
    if not url:
        print("error: FTF_COMPREFACE_URL is not set", file=sys.stderr)
        raise SystemExit(1)
    try:
        report = enroll(bootstrap(url))
    except FaceServiceError as exc:
        print(f"error: face service: {exc.reason}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"added {report['added']}, already loaded {report['already']}")
    print(f"ready: {', '.join(report['people_ready']) or 'nobody'}")
    for item in report["skipped"]:
        print(f"skipped {item['person']}: {item['title']} ({item['why']})")
    if report["people_missing"]:
        print(f"error: no photo for {', '.join(report['people_missing'])}", file=sys.stderr)
        raise SystemExit(1)


def cmd_views(args) -> None:
    for v in _store().views():
        print(f"{v.view_id}  {v.kind.value:5}  {v.label}")


def cmd_suspicions(args) -> None:
    """Show why a view, or every view, is suspicious."""
    from .grouping.suspicion import reason_summary_for_view

    store = _store()
    views = [store.get_view(args.view_id)] if args.view_id else store.views()
    for view in views:
        summary = reason_summary_for_view(view.view_id, store.observations(view.view_id))
        print(f"{view.view_id}  {view.label}: level {summary.level}")
        for kind, reasons in summary.reasons_by_kind.items():
            print(f"  {kind}:")
            for r in reasons:
                print(f"    [{r.signal_id}] {r.summary} ({r.source.file}:{r.source.locator})")
        for r in summary.face_matches:
            print(f"  context: {r.summary} ({r.source.file}:{r.source.locator})")
        if summary.unchecked_kinds:
            print(f"  not checked: {', '.join(summary.unchecked_kinds)}")


def cmd_observations(args) -> None:
    for o in _store().observations(args.view_id):
        print(f"{o.signal_id:4} {o.namespace:22} {o.value!s:60} [{o.source.locator}]")


def cmd_compare(args) -> None:
    store = _store()
    for e in pipeline.compare_views(store, args.view_a, args.view_b):
        if not e.comparable:
            print(f"{e.layer_id:16} not comparable")
            continue
        print(f"{e.layer_id:16} score {e.score:.3f}")
        for s in e.shared:
            print(f"    shared  {s['ns']}: {s['value']}")
        for d in e.differing:
            print(f"    differs {d['ns']}: {d['a']} vs {d['b']}")


def cmd_pairs(args) -> None:
    store = _store()
    labels = {v.view_id: v.label for v in store.views()}
    for e in store.evidence(min_score=args.min_score):
        shared = "; ".join(f"{s['ns']}={s['value']}" for s in e.shared)
        print(
            f"{e.score:.3f}  {e.layer_id:12} {labels.get(e.view_a, e.view_a)}  <->  {labels.get(e.view_b, e.view_b)}  [{shared}]"
        )


def cmd_relations(args) -> None:
    store = _store()
    labels = {v.view_id: v.label for v in store.views()}
    wanted = {RelationType(t) for t in args.type} if args.type else None
    rows = [
        r
        for r in store.relations()
        if (wanted is None or r.type in wanted) and r.coverage >= args.min_coverage
    ]
    order = list(RelationType)
    rows.sort(key=lambda r: (order.index(r.type), -max(r.tier_scores.values(), default=0)))
    for r in rows:
        tiers = " ".join(f"{t}={s:.2f}" for t, s in r.tier_scores.items() if s > 0) or "-"
        print(
            f"{r.type.value:22} {r.confidence:6} cov={r.coverage:.2f}  "
            f"{labels.get(r.view_a, r.view_a)}  <->  {labels.get(r.view_b, r.view_b)}  [{tiers}]"
        )
        if args.reasons:
            for reason in r.reasons:
                print(f"    {reason}")


def cmd_explain(args) -> None:
    """Explain the pair with linked source observations, without changing stored results."""
    from .reporting import build_pair_report, render_pair_report

    store = _store()
    try:
        report = build_pair_report(store, args.view_a, args.view_b)
        output = (
            json.dumps(report, ensure_ascii=False, indent=2)
            if args.format == "json"
            else render_pair_report(report)
        )
        if args.output:
            Path(args.output).write_text(output, encoding="utf-8")
        else:
            print(output)
    finally:
        store.close()


def cmd_eval(args) -> None:
    """Agreement with the reference pairs from the baseline report."""
    from .eval import evaluate, format_report
    from .eval.fixtures import store_from_fixtures

    if args.fixtures:
        store = store_from_fixtures()
        pipeline.run_fingerprint(store)
        run_method(store)
    else:
        store = _store()
    print(format_report(evaluate(store), show_agreed=args.all))


def cmd_groups(args) -> None:
    from .grouping import build_groups

    store = _store()
    labels = {v.view_id: v.label for v in store.views()}
    g = build_groups(store)
    for fid, cids in sorted(g.families.items()):
        print(f"family {fid}  ({len(cids)} campaign{'s' if len(cids) != 1 else ''})")
        for cid in cids:
            print(f"  campaign {cid}")
            for v in g.campaigns[cid]:
                print(f"    {v:16} {labels.get(v, '')}")
    if g.playbooks:
        print()
        for pid, views in sorted(g.playbooks.items()):
            print(f"playbook {pid}: " + ", ".join(views))
    if g.rejected_merges:
        print()
        for a, b, why in g.rejected_merges:
            print(f"rejected merge {a} <-> {b}: {why}")


def cmd_incidents(args) -> None:
    """Per-folder roll-up: campaigns, funnel edges, co-captured views and mismatches."""
    from .grouping import build_groups, rollup

    store = _store()
    labels = {v.view_id: v.label for v in store.views()}
    g = build_groups(store)
    for s in rollup(store, g, g.funnel_edges):
        n = len(s.campaigns)
        print(f"incident {s.incident_id}: {n} campaign{'s' if n != 1 else ''}")
        for cid, views in s.campaigns.items():
            others = s.shared_campaigns.get(cid)
            tag = f"  (also: {', '.join(others)})" if others else "  (only this folder)"
            print(f"  {cid}{tag}")
            for v in views:
                print(f"      {v:16} {labels.get(v, '')}")
        for e in s.funnel:
            print(f"  funnel {e.source} -> {e.target}: " + "; ".join(e.evidence))
        for e in s.co_captured:
            print(f"  co-captured {e.source} ~ {e.target} (display only)")
        for m in s.mismatches:
            print(
                f"  MISMATCH: ad shows {m['ad_shows']} but the folder's pages are {m['page_hosts']}"
            )


def cmd_assign(args) -> None:
    """Open-set outcome for one stored view against the rest of the corpus."""
    from .grouping import assign

    store = _store()
    a = assign(store, args.view_id, exclude_incident=not args.same_incident)
    print(f"{a.view_id}: {a.headline()}")
    for reason in a.reasons:
        print(f"  - {reason}")
    for rtype, items in a.related.items():
        for x in items[: args.top]:
            print(
                f"  {rtype:22} {x.view_id:16} strength {x.strength:.2f} cov {x.relation.coverage:.2f}"
            )


def cmd_loio(args) -> None:
    """Leave-one-incident-out on the fixtures, or on real files with --sample."""
    if args.sample:
        from .eval import sample

        base = sample.extracted(Path(args.sample))
        print("\n".join(sample.format_loio(sample.loio_sample(base, incidents=args.incident))))
        return
    from .eval.loio import format_loio, loio_all

    print(format_loio(loio_all(incidents=args.incident or None)))


def cmd_validate(args) -> None:
    """Real-file validation, model off: reference agreement, checkpoint, negative pairs,
    confidence audit, degradation, ablation and leave-one-out, as one Markdown document."""
    from .eval import sample

    parts = [p for p in ("candidates", "degrade", "ablate", "loio") if p not in (args.skip or [])]
    workdir = get_settings().data_dir / "tmp"
    text = sample.validation_report(Path(args.path), workdir=workdir, parts=parts)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)


def cmd_ablate(args) -> None:
    if args.sample:
        from .eval import sample

        base = sample.extracted(Path(args.sample))
        if args.degrade:
            workdir = get_settings().data_dir / "tmp"
            runs = sample.degrade_sample(Path(args.sample), base, workdir=workdir)
        else:
            runs = sample.ablate_sample(base)
        print("\n".join(sample.format_runs(runs)))
        return
    from .eval.ablation import ablate, degrade, format_runs

    if args.degrade:
        print(format_runs(degrade(), "Degradation runs on the fixtures"))
    else:
        print(format_runs(ablate(), "Layer ablation on the fixtures (one layer off at a time)"))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ftf", description="Follow the Fake")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("capture", help="capture a URL with a headless browser into a bundle")
    p.add_argument("url", nargs="?", help="page to capture; optional with --replay")
    p.add_argument("--replay", metavar="HAR", help="serve the browser from this archived HAR")
    p.add_argument("--out", help="incident folder to write (default: $FTF_DATA_DIR/captures/…)")
    p.add_argument("--proxy", help="proxy server, e.g. http://host:3128 (egress is recorded)")
    p.add_argument("--timeout", type=float, default=30.0, help="navigation timeout, seconds")
    p.add_argument("--ingest", action="store_true", help="ingest the bundle and run everything")
    p.add_argument("--incident", help="incident id for --ingest (default: the folder name)")
    p.add_argument("--candidates", action="store_true", help="compare candidate pairs only")
    p.add_argument("--no-enrich", action="store_true", help="skip DNS, RDAP and crt.sh lookups")
    p.add_argument(
        "--cloaking",
        action="store_true",
        help="cloaking differential: capture as ad click (mobile, social referrer, query) and bare",
    )
    p.add_argument(
        "--crawler", action="store_true", help="cloaking differential with a Googlebot profile too"
    )
    p.set_defaults(func=cmd_capture)

    p = sub.add_parser("ingest", help="ingest an incident folder, or a folder of incident folders")
    p.add_argument("path")
    p.add_argument("--no-run", action="store_true", help="only ingest; do not extract and compare")
    p.add_argument("--candidates", action="store_true", help="compare candidate pairs only")
    p.add_argument("--no-enrich", action="store_true", help="skip DNS, RDAP and crt.sh lookups")
    p.add_argument(
        "--replace-known", action="store_true", help="let this folder replace shipped incidents"
    )
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("run", help="extract, enrich, fingerprint and compare everything stored")
    p.add_argument("--candidates", action="store_true", help="compare candidate pairs only")
    p.add_argument("--no-enrich", action="store_true", help="skip DNS, RDAP and crt.sh lookups")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("perceive", help="read images and pages, and observe techniques")
    p.add_argument("--view", action="append", help="only this view id; may be repeated")
    p.set_defaults(func=cmd_perceive)

    p = sub.add_parser("enrich", help="DNS/RDAP/crt.sh enrichment plus CERT/KNF refresh and local checks")
    p.add_argument("--view", action="append", help="only this view id; repeatable")
    p.add_argument("--offline", action="store_true", help="use cached/bundled snapshots only")
    p.add_argument(
        "--import-gambling",
        metavar="FILE",
        help="browser-downloaded MF XML or domain-per-line export",
    )
    p.add_argument("--snapshot-date", metavar="YYYY-MM-DD", help="date of the manual export")
    p.set_defaults(func=cmd_enrich)

    p = sub.add_parser("corpus", help="build or load the shipped known corpus")
    p.add_argument("action", choices=("build", "load"))
    p.add_argument("sample", nargs="?", help="sample folder, for build")
    p.add_argument("--dir", help="corpus folder (default: ./corpus)")
    p.set_defaults(func=cmd_corpus)

    p = sub.add_parser("report", help="corpus report (Markdown) and JSON/CSV export")
    p.add_argument("--out", help="output folder (default: $FTF_DATA_DIR/export)")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("demo", help="rehearse the demo sequence and check each step")
    p.add_argument("sample", help="folder with the organiser sample")
    browser = p.add_mutually_exclusive_group()
    browser.add_argument("--live-url", help="URL for the live capture step")
    browser.add_argument("--no-capture", action="store_true", help="skip the browser steps")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("ground", help="check expectation profiles against genuine pages")
    p.add_argument("--kind", action="append", help="only this kind of page; may be repeated")
    p.add_argument("--show", action="store_true", help="show stored results; run nothing")
    p.set_defaults(func=cmd_ground)

    p = sub.add_parser("narrate", help="model-written narrative from the evidence table")
    p.add_argument("views", nargs="*", help="two view ids")
    p.add_argument("--campaign", help="a campaign id, instead of two views")
    p.set_defaults(func=cmd_narrate)

    p = sub.add_parser("faces", help="face reference list in the private CompreFace service")
    p.add_argument("action", choices=("setup",))
    p.set_defaults(func=cmd_faces)

    sub.add_parser("views", help="list views").set_defaults(func=cmd_views)

    p = sub.add_parser("suspicions", help="show why a view is suspicious")
    p.add_argument("view_id", nargs="?", default=None, help="optional; show all if not given")
    p.set_defaults(func=cmd_suspicions)

    p = sub.add_parser("observations", help="show one view's observations")
    p.add_argument("view_id")
    p.set_defaults(func=cmd_observations)

    p = sub.add_parser("compare", help="compare two views layer by layer")
    p.add_argument("view_a")
    p.add_argument("view_b")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("pairs", help="list stored evidence, strongest first")
    p.add_argument("--min-score", type=float, default=0.01)
    p.set_defaults(func=cmd_pairs)

    p = sub.add_parser("relations", help="list typed relations between views")
    p.add_argument("--type", action="append", help="relation type to show; repeatable")
    p.add_argument("--min-coverage", type=float, default=0.0)
    p.add_argument("--reasons", action="store_true", help="print the reasons under each row")
    p.set_defaults(func=cmd_relations)

    p = sub.add_parser("patterns", help="induce, match and inspect the pattern library (model)")
    p.add_argument("action", choices=("discover", "match", "list", "stability"))
    p.add_argument("--view", action="append", help="only this view id; may be repeated")
    p.set_defaults(func=cmd_patterns)

    p = sub.add_parser("explain", help="layer-by-layer evidence and the typed relation for a pair")
    p.add_argument("view_a")
    p.add_argument("view_b")
    p.add_argument("--format", choices=("markdown", "json"), default="markdown")
    p.add_argument("--output", help="write the report to a file instead of stdout")
    p.set_defaults(func=cmd_explain)

    p = sub.add_parser(
        "load-fixtures", help="load tests/fixtures/observations.json and run everything"
    )
    p.add_argument("path", nargs="?", default=None)
    p.add_argument("--candidates", action="store_true", help="compare candidate pairs only")
    p.set_defaults(func=cmd_load_fixtures)

    sub.add_parser(
        "groups", help="campaigns, families and playbooks from stored relations"
    ).set_defaults(func=cmd_groups)

    sub.add_parser(
        "incidents", help="per-folder roll-up with funnel edges and mismatches"
    ).set_defaults(func=cmd_incidents)

    p = sub.add_parser("assign", help="open-set outcome for one view against the corpus")
    p.add_argument("view_id")
    p.add_argument("--same-incident", action="store_true", help="also compare with its own folder")
    p.add_argument("--top", type=int, default=3)
    p.set_defaults(func=cmd_assign)

    p = sub.add_parser("loio", help="leave-one-incident-out on the fixtures")
    p.add_argument("--incident", action="append", help="only these incidents; repeatable")
    p.add_argument("--sample", help="run on this folder of real incident folders, model off")
    p.set_defaults(func=cmd_loio)

    p = sub.add_parser(
        "validate",
        help="real-file validation report, model off (docs/method-results-real-sample.md)",
    )
    p.add_argument("path", help="a folder of incident folders")
    p.add_argument("--out", help="write the Markdown report here instead of printing it")
    p.add_argument(
        "--skip",
        action="append",
        choices=["candidates", "degrade", "ablate", "loio"],
        help="leave a part out",
    )
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("ablate", help="layer ablation table on the fixtures")
    p.add_argument("--degrade", action="store_true", help="degradation runs instead of ablation")
    p.add_argument("--sample", help="run on this folder of real incident folders, model off")
    p.set_defaults(func=cmd_ablate)

    p = sub.add_parser("eval", help="agreement with the reference pairs (docs/baseline-report.md)")
    p.add_argument("--fixtures", action="store_true", help="evaluate the fixtures in memory")
    p.add_argument("--all", action="store_true", help="also list the agreed pairs")
    p.set_defaults(func=cmd_eval)

    args = parser.parse_args(argv)
    # Evaluation measures the shipped method, so analyst edits must not change its results.
    if args.command not in ("eval", "loio", "ablate", "validate", "load-fixtures", "demo"):
        from .method_settings import apply_saved

        apply_saved()
    args.func(args)


if __name__ == "__main__":
    main()
