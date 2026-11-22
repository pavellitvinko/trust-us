"""Measure cold, warm and one-item core processing without modifying the source library.

Run with --live to make paid page/image calls. Each cold run gets a separate temporary
database and empty model cache. The output contains timings and coverage, not source text.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path

from ftf.config import get_settings
from ftf.jobs import Job, Worker
from ftf.store import Store


def sequential_parity(worker):
    """Replay the same validated answers serially; compare evidence and final relations."""
    from ftf import pipeline
    from ftf.fusion import run_method
    from ftf.intake import ANALYSIS_LOCK
    from ftf.models.gateway import request_budget
    from ftf.perception.image import run_perceive
    from ftf.perception.page import run_perceive_pages
    from ftf.registry import Context
    from ftf.scheduler import observation_map

    with worker.scheduler.lock, ANALYSIS_LOCK:
        source = Store(worker.db_path)
        baseline = Store(":memory:")
        try:
            source.db.backup(baseline.db)
            expected_observations = observation_map(source)
            expected_relations = {
                (r.view_a, r.view_b): (r.type, r.confidence, r.coverage) for r in source.relations()
            }
            baseline.db.execute(
                "DELETE FROM observations WHERE extractor IN ('perception.image','perception.page')"
            )
            for table in ("fingerprints", "evidence", "relations"):
                baseline.db.execute(f"DELETE FROM {table}")
            baseline.db.commit()
            pipeline.run_extract(baseline, fast=True)
            metrics = {}
            with request_budget(30, metrics):
                run_perceive(Context(baseline), settings=get_settings())
                run_perceive_pages(Context(baseline), settings=get_settings())
            pipeline.run_fingerprint(baseline, local_only=True)
            run_method(baseline)
            actual_relations = {
                (r.view_a, r.view_b): (r.type, r.confidence, r.coverage)
                for r in baseline.relations()
            }
            return {
                "observationsEqual": observation_map(baseline) == expected_observations,
                "relationsEqual": actual_relations == expected_relations,
                "cacheOnly": not metrics.get("providerCalls"),
                "pairs": len(actual_relations),
            }
        finally:
            source.close()
            baseline.close()


def measure(worker, label, ids=None):
    job = Job(label, worker.data_dir, [label], kind="llm")
    started = time.monotonic()
    worker.scheduler.schedule(job, ids, include_additional=False)
    deadline = started + 320
    first = None
    generation = worker.generation
    while not job.core.get("finishedAt") and time.monotonic() < deadline:
        if first is None and worker.generation != generation:
            first = time.monotonic() - started
        time.sleep(0.05)
    tasks = [t for t in worker.scheduler.tasks.values() if t["run"] == job.job_id]
    metrics = {}
    for task in tasks:
        for key, value in task.get("metrics", {}).items():
            metrics[key] = metrics.get(key, 0) + value
    return {
        "run": label,
        "wallSeconds": round(time.monotonic() - started, 3),
        "firstPublicationSeconds": round(first, 3) if first is not None else None,
        "core": job.core,
        "metrics": metrics,
        "reads": [
            {"kind": t["stage"], "state": t["state"], "result": t["result"]}
            for t in tasks
            if t["stage"] in ("page", "image")
        ],
        "mode": "live" if get_settings().openai_api_key else "local_only",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", type=Path, default=Path("data/ftf.sqlite"))
    parser.add_argument("--cold-runs", type=int, default=3)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    original = get_settings()
    if args.live and not original.openai_api_key:
        parser.error("--live requires OPENAI_API_KEY")
    if not args.live:
        os.environ["OPENAI_API_KEY"] = ""
        os.environ["GEMINI_API_KEY"] = ""
    # Use the packaged public-suffix list; no network/cache mutation for suffix parsing.
    import tldextract

    from ftf.extract import _material

    _material._tld = tldextract.TLDExtract(cache_dir=None, suffix_list_urls=())
    results = []
    with tempfile.TemporaryDirectory(prefix="ftf-core-benchmark-") as scratch:
        for index in range(args.cold_runs):
            folder = Path(scratch) / str(index)
            folder.mkdir()
            os.environ["FTF_DATA_DIR"] = str(folder)
            source = sqlite3.connect(f"file:{args.source_db.resolve()}?mode=ro", uri=True)
            target = sqlite3.connect(folder / "ftf.sqlite")
            source.backup(target)
            source.close()
            for table in ("observations", "fingerprints", "evidence", "relations"):
                target.execute(f"DELETE FROM {table}")
            for table in ("processing_runs", "processing_tasks"):
                if target.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
                    target.execute(f"DELETE FROM {table}")
            target.commit()
            target.close()
            worker = Worker(folder / "ftf.sqlite", folder)
            try:
                result = measure(worker, f"cold-{index + 1}")
                if args.live and result["core"].get("targetMet") and index == 0:
                    result["sequentialParity"] = sequential_parity(worker)
                results.append(result)
                print(json.dumps({k: v for k, v in result.items() if k != "reads"}), flush=True)
                if args.live and not result["core"].get("targetMet"):
                    break
                if index == 0:
                    result = measure(worker, "warm")
                    results.append(result)
                    print(json.dumps({k: v for k, v in result.items() if k != "reads"}), flush=True)
                    store = Store(worker.db_path)
                    view = next(
                        v
                        for v in store.views()
                        if v.kind.value != "video" and not v.meta.get("shipped")
                    )
                    incident = store.get_incident(view.incident_id)
                    new_id = "benchmark-addition"
                    new_view = view.model_copy(
                        update={"incident_id": new_id, "view_id": f"{new_id}:one"}
                    )
                    store.put_incident(
                        incident.model_copy(update={"incident_id": new_id}),
                        [
                            a.model_copy(update={"incident_id": new_id})
                            for a in store.artifacts(view.incident_id)
                        ],
                        [new_view],
                    )
                    store.close()
                    result = measure(worker, "single-item", [new_view.view_id])
                    results.append(result)
                    print(json.dumps({k: v for k, v in result.items() if k != "reads"}), flush=True)
            finally:
                worker.scheduler.close(wait=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"results": results}, indent=2))
    if args.live and any(
        r.get("sequentialParity")
        and not all(
            r["sequentialParity"][key]
            for key in ("observationsEqual", "relationsEqual", "cacheOnly")
        )
        for r in results
    ):
        raise SystemExit(
            "Sequential replay did not match the concurrent results using only cached answers. See the report."
        )
    if args.live and not all(r["core"].get("targetMet") for r in results):
        raise SystemExit(
            "One or more runs missed the core coverage/performance target. See the report."
        )


if __name__ == "__main__":
    main()
