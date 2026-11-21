"""Command line: ftf ingest | run | perceive | views | observations | compare | pairs."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import pipeline
from .config import get_settings
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


def cmd_perceive(args) -> None:
    from .perception.image import run_perceive
    from .registry import Context

    summary = run_perceive(Context(_store()), args.view or None)
    print(
        f"image views: {summary['views']}, read: {summary['read']}, "
        f"not observable: {summary['not_observable']}, observations: {summary['observations']}"
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ftf", description="Follow the Fake")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest", help="ingest an incident folder, or a folder of incident folders")
    p.add_argument("path")
    p.add_argument("--no-run", action="store_true", help="only ingest; do not extract and compare")
    p.set_defaults(func=cmd_ingest)

    sub.add_parser("run", help="extract, fingerprint and compare everything stored").set_defaults(
        func=cmd_run
    )
    p = sub.add_parser("perceive", help="read stored image views with the vision model")
    p.add_argument("--view", action="append", help="only this view id; may be repeated")
    p.set_defaults(func=cmd_perceive)

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

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
