"""The shipped known corpus: processed results only, never the organiser's files.

Export drops whole-page text, hashes secrets and clears local paths. Shipped views are marked, so
later stages keep their stored results instead of trying to re-read material that is not there.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from .contracts import Evidence, Fingerprint, Incident, Observation, Relation, View
from .store import Store

SCHEMA = 1
DATA_FILE = "corpus.jsonl.gz"
MANIFEST_FILE = "manifest.json"
DROPPED_NAMESPACES = frozenset(
    {"page.clean_text", "page.body_text", "page.read_text", "expect.read_text"}
)
MAX_VALUE_CHARS = 2000
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


def _walk(value: Any, clean) -> Any:
    if isinstance(value, str):
        return clean(value)
    if isinstance(value, list):
        return [_walk(item, clean) for item in value]
    if isinstance(value, dict):
        return {key: _walk(item, clean) for key, item in value.items()}
    return value


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)


def _secret_values(rows: list[Observation]) -> list[str]:
    from .models.redact import is_secret_name

    found = set()
    for o in rows:
        if is_secret_name(o.namespace.rsplit(".", 1)[-1]):
            found.update(s for s in _strings([o.value, o.raw]) if s)
    return sorted(found, key=len, reverse=True)


def _too_long(value: Any) -> bool:
    return any(len(s) > MAX_VALUE_CHARS for s in _strings(value))


def _input_hash(store: Store) -> str:
    rows = sorted(
        (a.incident_id, a.path, a.sha256)
        for incident in store.incidents()
        for a in store.artifacts(incident.incident_id)
    )
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def export_corpus(store: Store, out_dir: Path, mode: str) -> dict[str, Any]:
    """Write the sanitised corpus and its manifest, and check the result before returning."""
    from .models.redact import hash_secret, redact_text

    views = store.views()
    observations = {v.view_id: store.observations(v.view_id) for v in views}
    secrets = _secret_values([o for rows in observations.values() for o in rows])

    def clean(text: str) -> str:
        for secret in secrets:
            text = text.replace(secret, hash_secret(secret))
        return redact_text(text)

    records: list[dict[str, Any]] = []

    def add(kind: str, model) -> None:
        records.append({"kind": kind, "body": _walk(model.model_dump(mode="json"), clean)})

    for incident in store.incidents():
        add("incident", incident.model_copy(update={"root": "."}))
    for view in views:
        add("view", view.model_copy(update={"meta": {**view.meta, "shipped": True}}))
        for o in observations[view.view_id]:
            if o.namespace in DROPPED_NAMESPACES or _too_long(o.value):
                continue
            raw = o.raw[:MAX_RAW_CHARS] if isinstance(o.raw, str) else o.raw
            add("observation", o.model_copy(update={"raw": raw}))
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


def load_corpus(store: Store, source: Path) -> int:
    """Load the shipped corpus into an empty store. Returns the number of views."""
    path = Path(source) / DATA_FILE
    if not path.is_file():
        raise CorpusError(f"no corpus at {path}")
    if store.incidents():
        raise CorpusError("the store already has incidents; load needs an empty store")
    manifest = json.loads((Path(source) / MANIFEST_FILE).read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA:
        raise CorpusError(f"unsupported corpus schema: {manifest.get('schema')}")
    grouped: dict[str, list[dict]] = defaultdict(list)
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            record = json.loads(line)
            grouped[record["kind"]].append(record["body"])
    views = [View.model_validate(b) for b in grouped["view"]]
    by_incident: dict[str, list[View]] = defaultdict(list)
    for view in views:
        by_incident[view.incident_id].append(view)
    for body in grouped["incident"]:
        incident = Incident.model_validate(body)
        store.put_incident(incident, [], by_incident[incident.incident_id])
    rows: dict[tuple[str, str], list[Observation]] = defaultdict(list)
    for body in grouped["observation"]:
        o = Observation.model_validate(body)
        rows[(o.view_id, o.extractor)].append(o)
    for (view_id, extractor), observations in rows.items():
        store.put_observations(view_id, extractor, observations)
    for body in grouped["fingerprint"]:
        store.put_fingerprint(Fingerprint.model_validate(body))
    for body in grouped["evidence"]:
        store.put_evidence(Evidence.model_validate(body))
    for body in grouped["relation"]:
        store.put_relation(Relation.model_validate(body))
    return len(views)


def ensure_seeded(store: Store) -> bool:
    """With FTF_SEED_CORPUS=1, give an empty store the shipped corpus."""
    if os.environ.get("FTF_SEED_CORPUS") != "1" or store.incidents():
        return False
    source = corpus_dir()
    if not (source / DATA_FILE).is_file():
        return False
    load_corpus(store, source)
    return True
