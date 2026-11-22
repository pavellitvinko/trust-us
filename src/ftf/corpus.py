"""The shipped known corpus: processed results only, never the organiser's files.

Export drops whole-page text, hashes secrets and clears local paths. Shipped views are marked, so
later stages keep their stored results instead of trying to re-read material that is not there.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from .contracts import Evidence, Fingerprint, Incident, Observation, Relation, View
from .corpus_report import (
    TEXT_NAMESPACES,
    _cleaner,
    _hash,
    _walk,
    is_secret_namespace,
    sensitive_values,
)
from .store import Store, ordered, value_key

log = logging.getLogger(__name__)

SCHEMA = 2
DATA_FILE = "corpus.jsonl.gz"
MANIFEST_FILE = "manifest.json"
MAX_TOTAL_CHARS = 10_000  # structural lists such as DOM class sets are long but not prose
MAX_RAW_CHARS = 300


class CorpusError(ValueError):
    """The corpus cannot be built or loaded; the message is safe to show."""


def corpus_dir() -> Path:
    return Path(os.environ.get("FTF_CORPUS_DIR", "corpus"))


def _commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=True
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)


def _too_long(value: Any) -> bool:
    return sum(len(s) for s in _strings(value)) > MAX_TOTAL_CHARS


def _input_hash(store: Store) -> str:
    rows = sorted(
        (a.incident_id, a.path, a.sha256)
        for incident in store.incidents()
        for a in store.artifacts(incident.incident_id)
    )
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def export_corpus(store: Store, out_dir: Path, mode: str) -> dict[str, Any]:
    """Write the sanitised corpus and its manifest, and check the result before returning."""
    views = store.views()
    observations = {v.view_id: store.observations(v.view_id) for v in views}
    secrets, _ = sensitive_values([o for rows in observations.values() for o in rows])

    clean = _cleaner(store)
    records: list[dict[str, Any]] = []

    def add(kind: str, model) -> None:
        records.append({"kind": kind, "body": _walk(model.model_dump(mode="json"), clean)})

    for incident in store.incidents():
        add("incident", incident.model_copy(update={"root": "."}))
    for view in views:
        add("view", view.model_copy(update={"meta": {**view.meta, "shipped": True}}))
        for o in observations[view.view_id]:
            if o.namespace in TEXT_NAMESPACES or _too_long(o.value):
                continue
            value, raw = o.value, o.raw
            if is_secret_namespace(o.namespace):
                value = _walk(value, _hash)
                raw = _hash(raw) if isinstance(raw, str) else raw
            elif isinstance(raw, str):
                raw = clean(raw)[:MAX_RAW_CHARS]
            add("observation", o.model_copy(update={"raw": raw, "value": value}))
        for fingerprint in store.fingerprints(view_id=view.view_id):
            add("fingerprint", fingerprint)
    for evidence in store.evidence():
        add("evidence", evidence)
    for relation in store.relations():
        add("relation", relation)

    blob = "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in records)
    leaked = [s for s in secrets if s in blob]
    if leaked:
        raise CorpusError(f"{len(leaked)} secret value(s) survived sanitising; nothing written")
    out_dir.mkdir(parents=True, exist_ok=True)
    temp = out_dir / f"{DATA_FILE}.tmp"
    # mtime=0 makes the same corpus produce the same bytes.
    with (
        temp.open("wb") as raw_file,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw_file, mtime=0) as fh,
    ):
        fh.write(blob.encode("utf-8"))
    temp.replace(out_dir / DATA_FILE)
    counts = defaultdict(int)
    for record in records:
        counts[record["kind"]] += 1
    manifest = {
        "schema": SCHEMA,
        "commit": _commit(),
        "input_sha256": _input_hash(store),
        "execution_mode": mode,
        "counts": dict(sorted(counts.items())),
        "model_cache": "not included",
        "excluded": "organiser files, whole-page text, local paths; secrets are hashed",
        "restriction": "Private repository only. Do not redistribute.",
    }
    (out_dir / MANIFEST_FILE).write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return manifest


def build_corpus(sample: Path, out_dir: Path) -> dict[str, Any]:
    """Rebuild from an evaluator's copy of the sample, with no model or network calls."""
    from . import pipeline
    from .fusion import run_method
    from .ingest import ingest_path

    os.environ["FTF_EMBEDDER"] = "fake"
    with tempfile.TemporaryDirectory() as scratch:
        store = Store(Path(scratch) / "build.sqlite")
        try:
            if not sum(len(found) for _, found in ingest_path(Path(sample), store)):
                raise CorpusError(f"no views found in {sample}")
            pipeline.run_extract(store)
            pipeline.run_fingerprint(store)
            pipeline.run_compare_all(store)
            run_method(store)
            return export_corpus(store, out_dir, "deterministic; no model or network calls")
        finally:
            store.close()


def _read_records(source: Path) -> dict[str, list]:
    path = Path(source) / DATA_FILE
    if not path.is_file():
        raise CorpusError(f"no corpus at {path}")
    try:
        manifest = json.loads((Path(source) / MANIFEST_FILE).read_text(encoding="utf-8"))
        if manifest.get("schema") != SCHEMA:
            raise CorpusError(f"unsupported corpus schema: {manifest.get('schema')}")
        models = {
            "incident": Incident,
            "view": View,
            "observation": Observation,
            "fingerprint": Fingerprint,
            "evidence": Evidence,
            "relation": Relation,
        }
        out: dict[str, list] = defaultdict(list)
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                record = json.loads(line)
                out[record["kind"]].append(models[record["kind"]].model_validate(record["body"]))
        return out
    except CorpusError:
        raise
    except (OSError, EOFError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise CorpusError(f"damaged corpus at {source}: {type(exc).__name__}") from exc


def load_corpus(store: Store, source: Path) -> int:
    """Load the shipped corpus into an empty store, all or nothing. Returns the view count."""
    records = _read_records(source)
    db = store.db
    db.execute("BEGIN IMMEDIATE")  # also stops two first sessions from seeding at once
    try:
        if db.execute("SELECT 1 FROM incidents LIMIT 1").fetchone():
            raise CorpusError("the store already has incidents; load needs an empty store")
        db.executemany(
            "INSERT INTO incidents VALUES (?,?)",
            [(i.incident_id, i.model_dump_json()) for i in records["incident"]],
        )
        db.executemany(
            "INSERT INTO views VALUES (?,?,?,?)",
            [
                (v.view_id, v.incident_id, v.kind.value, v.model_dump_json())
                for v in records["view"]
            ],
        )
        db.executemany(
            "INSERT INTO observations (view_id, extractor, namespace, value_key, body) "
            "VALUES (?,?,?,?,?)",
            [
                (o.view_id, o.extractor, o.namespace, value_key(o.value), o.model_dump_json())
                for o in records["observation"]
            ],
        )
        db.executemany(
            "INSERT INTO fingerprints VALUES (?,?,?)",
            [(f.view_id, f.layer_id, f.model_dump_json()) for f in records["fingerprint"]],
        )
        db.executemany(
            "INSERT INTO evidence VALUES (?,?,?,?,?)",
            [
                (*ordered(e.view_a, e.view_b), e.layer_id, e.score, e.model_dump_json())
                for e in records["evidence"]
            ],
        )
        db.executemany(
            "INSERT INTO relations VALUES (?,?,?,?)",
            [
                (*ordered(r.view_a, r.view_b), r.type.value, r.model_dump_json())
                for r in records["relation"]
            ],
        )
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return len(records["view"])


def ensure_seeded(store: Store) -> bool:
    """With FTF_SEED_CORPUS=1, give an empty store the shipped corpus."""
    if os.environ.get("FTF_SEED_CORPUS") != "1" or store.incidents():
        return False
    source = corpus_dir()
    if not (source / DATA_FILE).is_file():
        return False
    try:
        load_corpus(store, source)
    except CorpusError as exc:
        log.warning("known corpus not loaded: %s", exc)
        return False
    return True
