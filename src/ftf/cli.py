"""Command line: ftf ingest | run | views | observations | compare | pairs | relations | explain | load-fixtures."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import pipeline
from .config import get_settings
from .contracts import RelationType
from .fusion import fuse_pair, prepare, run_method
from .ingest import ingest_path
from .store import Store


def _store() -> Store:
    return Store(get_settings().db_path)


def cmd_ingest(args) -> None:
    store = _store()
    for incident, views in ingest_path(Path(args.path), store):
        kinds = {}
        for v in views:
            kinds[v.kind.value] = kinds.get(v.kind.value, 0) + 1
        summary = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) or "no views"
        print(f"incident {incident.incident_id}: {summary}")
    if not args.no_run:
        cmd_run(args)


def cmd_run(args) -> None:
    store = _store()
    print(f"observations: {pipeline.run_extract(store)}")
    print(f"fingerprints: {pipeline.run_fingerprint(store)}")
    print(f"evidence rows: {pipeline.run_compare_all(store)}")
    groups = run_method(store)
    print(
        f"relations: {len(store.relations())}; campaigns: {len(groups.campaigns)}; families: {len(groups.families)}"
    )


def cmd_load_fixtures(args) -> None:
    """Load the hand-made fixture observations into the configured store and run everything."""
    from .eval.fixtures import OBSERVATIONS, store_from_fixtures

    store = store_from_fixtures(Path(args.path) if args.path else OBSERVATIONS, store=_store())
    print(f"views: {len(store.views())}")
    print(f"fingerprints: {pipeline.run_fingerprint(store)}")
    print(f"evidence rows: {pipeline.run_compare_all(store)}")
    groups = run_method(store)
    print(
        f"relations: {len(store.relations())}; campaigns: {len(groups.campaigns)}; families: {len(groups.families)}"
    )


def cmd_views(args) -> None:
    for v in _store().views():
        print(f"{v.view_id}  {v.kind.value:5}  {v.label}")


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
    """Compare two views layer by layer and show the typed relation with its reasons."""
    store = _store()
    prepare(store)
    for e in pipeline.compare_views(store, args.view_a, args.view_b):
        if not e.comparable:
            print(f"{e.layer_id:18} not comparable")
            continue
        print(f"{e.layer_id:18} score {e.score:.3f}")
        for s in e.shared:
            extra = f" (w={s['weight']})" if "weight" in s else ""
            print(f"    shared  {s['ns']}: {s['value']}{extra}")
        for d in e.differing:
            print(f"    differs {d['ns']}: {d['a']} vs {d['b']}")
    r = fuse_pair(store, args.view_a, args.view_b, persist=False)
    print()
    print(f"relation: {r.type.value}  confidence={r.confidence or '-'}  coverage={r.coverage:.2f}")
    print("tiers:    " + "  ".join(f"{t}={s:.3f}" for t, s in r.tier_scores.items()))
    for reason in r.reasons:
        print(f"  - {reason}")


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
    """Leave-one-incident-out on the fixtures."""
    from .eval.loio import format_loio, loio_all

    print(format_loio(loio_all(incidents=args.incident or None)))


def cmd_ablate(args) -> None:
    from .eval.ablation import ablate, degrade, format_runs

    if args.degrade:
        print(format_runs(degrade(), "Degradation runs on the fixtures"))
    else:
        print(format_runs(ablate(), "Layer ablation on the fixtures (one layer off at a time)"))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ftf", description="Follow the Fake")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest", help="ingest an incident folder, or a folder of incident folders")
    p.add_argument("path")
    p.add_argument("--no-run", action="store_true", help="only ingest; do not extract and compare")
    p.add_argument("--candidates", action="store_true", help="compare candidate pairs only")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("run", help="extract, fingerprint and compare everything stored")
    p.add_argument("--candidates", action="store_true", help="compare candidate pairs only")
    p.set_defaults(func=cmd_run)
    sub.add_parser("views", help="list views").set_defaults(func=cmd_views)

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

    p = sub.add_parser("explain", help="layer-by-layer evidence and the typed relation for a pair")
    p.add_argument("view_a")
    p.add_argument("view_b")
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
    p.set_defaults(func=cmd_loio)

    p = sub.add_parser("ablate", help="layer ablation table on the fixtures")
    p.add_argument("--degrade", action="store_true", help="degradation runs instead of ablation")
    p.set_defaults(func=cmd_ablate)

    p = sub.add_parser("eval", help="agreement with the reference pairs (docs/baseline-report.md)")
    p.add_argument("--fixtures", action="store_true", help="evaluate the fixtures in memory")
    p.add_argument("--all", action="store_true", help="also list the agreed pairs")
    p.set_defaults(func=cmd_eval)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
