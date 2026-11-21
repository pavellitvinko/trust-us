"""`ftf discover …` commands."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..config import get_settings
from ..store import Store
from .models import LibraryQuery, QueryKind, QueryOrigin
from .run import RunReport, ingest_ads, run_discovery
from .service import make_source, open_state, paths, plan_queries


def _one_off(args) -> list[LibraryQuery]:
    out = []
    for terms in args.query or []:
        out.append(LibraryQuery(terms=terms[:100], exact_phrase=args.exact, origin=QueryOrigin.MANUAL,
                                reason="given on the command line"))  # fmt: skip
    for page in args.page or []:
        out.append(LibraryQuery(kind=QueryKind.PAGE, terms=page, origin=QueryOrigin.MANUAL,
                                reason="given on the command line"))  # fmt: skip
    for ad in args.ad or []:
        out.append(LibraryQuery(kind=QueryKind.AD, terms=ad, origin=QueryOrigin.MANUAL,
                                reason="given on the command line"))  # fmt: skip
    return out


def _source(args):
    return make_source(
        Path(args.replay) if args.replay else None,
        headed=not args.headless,
        human=not args.no_human,
        human_wait_s=args.wait_human * 60,
        notify=lambda msg: print(f"!! {msg}", flush=True),
    )


def _browser_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--headless", action="store_true",
                   help="no browser window; a CAPTCHA or login request then stops the run")  # fmt: skip
    p.add_argument("--no-human", action="store_true",
                   help="keep the window but stop on a CAPTCHA or login request instead of waiting")  # fmt: skip
    p.add_argument("--wait-human", type=float, default=10, metavar="MIN",
                   help="how long to wait for you to handle a CAPTCHA or login (default 10)")  # fmt: skip


def print_report(report: RunReport) -> None:
    c = report.counts
    print(f"run {report.run_id}: {report.status}")
    if report.error:
        print(f"  {report.error}")
    if report.screenshot:
        print(f"  screenshot: {report.screenshot}")
    print(
        f"  queries {c.get('queries', 0)} (failed {c.get('failed_queries', 0)}), ads seen "
        f"{c.get('seen', 0)}, new {c.get('new', 0)}, stopped since last seen {c.get('stopped', 0)}, "
        f"selected {c.get('selected', 0)}, ingested {c.get('ingested', 0)}"
    )
    if c.get("human_waits"):
        print(f"  you cleared {c['human_waits']} CAPTCHA or login request(s) in the browser")
    if report.skipped_queries:
        print(f"  over the per-run limit, not searched: {len(report.skipped_queries)} queries")
    for item in report.ingested:
        print(f"  incident {item.incident_id} (ad {item.ad_id})")
        for match in item.known:
            print(
                f"    shares {match['ns']}={match['value']} with incident {', '.join(match['incidents'])}"
            )
        if not item.links and not item.known:
            print("    no link to known material yet")
        for link in item.links[:5]:
            print(
                f"    {link['score']:.2f} {link['layer']:14} {link['label']}  [{'; '.join(link['shared'])}]"
            )


def cmd_queries(args) -> None:
    store, state = Store(get_settings().db_path), open_state()
    queries, notes = plan_queries(store, state, model=args.propose)
    for note in notes:
        print(f"note: {note}")
    rows = {row["query_id"]: row for row in state.queries()}
    for q in queries:
        row = rows.get(q.query_id, {})
        last = (
            f"last {row.get('last_status')} {row.get('last_run')}"
            if row.get("last_run")
            else "never run"
        )
        print(f"{q.query_id}  {q.origin.value:9} {q.label()[:60]:60}  {last}\n    {q.reason}")
    disabled = [r for r in rows.values() if not r["enabled"]]
    if disabled:
        print(f"disabled: {len(disabled)} ({', '.join(r['query_id'] for r in disabled)})")


def cmd_add(args) -> None:
    state = open_state()
    for q in _one_off(args):
        q = q.model_copy(
            update={"origin": QueryOrigin.WATCHLIST, "reason": args.reason or "analyst watchlist"}
        )
        state.upsert_query(q)
        print(f"added {q.query_id}  {q.label()}")


def cmd_toggle(args) -> None:
    state = open_state()
    for query_id in args.query_id:
        state.set_enabled(query_id, args.enable)
    print("updated")


def cmd_run(args) -> None:
    settings = get_settings()
    store, state = Store(settings.db_path), open_state(settings)
    extra = _one_off(args)
    only_given = bool(extra) and not args.all
    queries, notes = plan_queries(
        store, state, watchlist=not only_given, pivots=not only_given and not args.no_pivots,
        model=args.propose and not only_given, extra=extra,
    )  # fmt: skip
    for note in notes:
        print(f"note: {note}")
    if not queries:
        print("no queries: add some with `ftf discover add`, or give --query / --page / --ad")
        return
    source = _source(args)
    try:
        report = run_discovery(
            store, state, source, queries, paths(settings).incidents,
            max_ads=args.max_ads, max_ingest=args.select, ingest=not args.no_ingest,
            use_model=not args.no_screen, download_media=not args.no_media,
            progress=lambda msg: print(f"… {msg}", flush=True),
        )  # fmt: skip
    finally:
        source.close()
    print_report(report)


def cmd_ingest(args) -> None:
    settings = get_settings()
    store, state = Store(settings.db_path), open_state(settings)
    source = _source(args)
    try:
        done = ingest_ads(store, state, source, args.ad_id, paths(settings).incidents,
                          download_media=not args.no_media)  # fmt: skip
    finally:
        source.close()
    print_report(RunReport(run_id="manual", status="ok", ingested=done,
                           counts={"ingested": len(done)}))  # fmt: skip


def cmd_status(args) -> None:
    state = open_state()
    for run in state.runs(args.limit):
        c = run["counts"]
        print(f"{run['run_id']}  {run['status']:8} {run['source']:9} new {c.get('new', 0):3}  "
              f"selected {c.get('selected', 0):3}  ingested {c.get('ingested', 0):3}  {run['error'] or ''}")  # fmt: skip
    by_status: dict[str, int] = {}
    for row in state.ads():
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    print("ads by status:", ", ".join(f"{k} {v}" for k, v in sorted(by_status.items())) or "none")


def register(sub: argparse._SubParsersAction) -> None:
    disc = sub.add_parser("discover", help="find suspicious ads in the Meta Ad Library")
    dsub = disc.add_subparsers(dest="discover_command", required=True)

    def one_off(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--query", action="append", help="keywords (at most 100 characters); repeatable"
        )
        p.add_argument("--exact", action="store_true", help="match --query as an exact phrase")
        p.add_argument("--page", action="append", help="every ad of this advertiser page ID")
        p.add_argument("--ad", action="append", help="one ad by its Ad Library ID")

    p = dsub.add_parser("queries", help="list the queries a run would use")
    p.add_argument("--propose", action="store_true", help="also ask the model for queries")
    p.set_defaults(func=cmd_queries)

    p = dsub.add_parser("add", help="add queries to the watchlist")
    one_off(p)
    p.add_argument("--reason", help="why this query is watched")
    p.set_defaults(func=cmd_add)

    for name, enable in (("enable", True), ("disable", False)):
        p = dsub.add_parser(name, help=f"{name} queries by ID")
        p.add_argument("query_id", nargs="+")
        p.set_defaults(func=cmd_toggle, enable=enable)

    p = dsub.add_parser("run", help="search, screen, and ingest the most relevant new ads")
    one_off(p)
    p.add_argument(
        "--all",
        action="store_true",
        help="with --query/--page/--ad, also run the watchlist and pivots",
    )
    p.add_argument(
        "--no-pivots", action="store_true", help="skip queries derived from known material"
    )
    p.add_argument("--propose", action="store_true", help="also run model-proposed queries")
    p.add_argument("--max-ads", type=int, default=50, help="ads per query (default 50)")
    p.add_argument("--select", type=int, default=20, help="ads to ingest per run (default 20)")
    p.add_argument("--no-ingest", action="store_true", help="only search and screen")
    p.add_argument(
        "--no-screen",
        action="store_true",
        help="no model screening; pick by known links and Meta enforcement only",
    )
    p.add_argument("--no-media", action="store_true", help="do not download the ads' creatives")
    _browser_options(p)
    p.add_argument(
        "--replay", help="read saved responses from this directory instead of the live library"
    )
    p.set_defaults(func=cmd_run)

    p = dsub.add_parser("ingest", help="ingest ads already seen, by Ad Library ID")
    p.add_argument("ad_id", nargs="+")
    p.add_argument("--no-media", action="store_true")
    _browser_options(p)
    p.add_argument(
        "--replay", help="take ad images from saved responses instead of the live library"
    )
    p.set_defaults(func=cmd_ingest)

    p = dsub.add_parser("status", help="recent runs and ads by status")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_status)
