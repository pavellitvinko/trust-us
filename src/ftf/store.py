"""SQLite storage for everything the pipeline produces.

Rows are keyed so that re-running a stage replaces its previous output: stages are
idempotent. The interface is small on purpose; swapping SQLite for Postgres later means
re-implementing this file only.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from .contracts import Artifact, Evidence, Fingerprint, Incident, Observation, Relation, View

_SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (incident_id TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS artifacts (
    incident_id TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY (incident_id, path));
CREATE INDEX IF NOT EXISTS artifacts_sha ON artifacts (sha256);
CREATE TABLE IF NOT EXISTS views (
    view_id TEXT PRIMARY KEY, incident_id TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    view_id TEXT NOT NULL, extractor TEXT NOT NULL, namespace TEXT NOT NULL,
    value_key TEXT NOT NULL, body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS observations_view ON observations (view_id, extractor);
CREATE INDEX IF NOT EXISTS observations_value ON observations (namespace, value_key);
CREATE TABLE IF NOT EXISTS fingerprints (
    view_id TEXT NOT NULL, layer_id TEXT NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY (view_id, layer_id));
CREATE TABLE IF NOT EXISTS evidence (
    view_a TEXT NOT NULL, view_b TEXT NOT NULL, layer_id TEXT NOT NULL,
    score REAL NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY (view_a, view_b, layer_id));
CREATE TABLE IF NOT EXISTS relations (
    view_a TEXT NOT NULL, view_b TEXT NOT NULL, type TEXT NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY (view_a, view_b));
"""


def value_key(value) -> str:
    """Stable text form of an observation value, used for exact-match lookups."""
    return (
        value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False)
    )


def ordered(a: str, b: str) -> tuple[str, str]:
    """Pairs are stored once, smaller id first."""
    return (a, b) if a <= b else (b, a)


class Store:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.executescript(_SCHEMA)

    def close(self) -> None:
        self.db.close()

    # -- incidents, artifacts, views -------------------------------------------------

    def put_incident(
        self, incident: Incident, artifacts: Iterable[Artifact], views: Iterable[View]
    ) -> None:
        """Replace an incident and everything derived from it."""
        iid = incident.incident_id
        with self.db:
            old = [
                r[0]
                for r in self.db.execute("SELECT view_id FROM views WHERE incident_id=?", (iid,))
            ]
            for vid in old:
                self.db.execute("DELETE FROM observations WHERE view_id=?", (vid,))
                self.db.execute("DELETE FROM fingerprints WHERE view_id=?", (vid,))
                self.db.execute("DELETE FROM evidence WHERE view_a=? OR view_b=?", (vid, vid))
                self.db.execute("DELETE FROM relations WHERE view_a=? OR view_b=?", (vid, vid))
            self.db.execute("DELETE FROM views WHERE incident_id=?", (iid,))
            self.db.execute("DELETE FROM artifacts WHERE incident_id=?", (iid,))
            self.db.execute(
                "INSERT OR REPLACE INTO incidents VALUES (?,?)", (iid, incident.model_dump_json())
            )
            self.db.executemany(
                "INSERT INTO artifacts VALUES (?,?,?,?)",
                [(a.incident_id, a.path, a.sha256, a.model_dump_json()) for a in artifacts],
            )
            self.db.executemany(
                "INSERT INTO views VALUES (?,?,?,?)",
                [(v.view_id, v.incident_id, v.kind.value, v.model_dump_json()) for v in views],
            )

    def get_incident(self, incident_id: str) -> Incident:
        row = self.db.execute(
            "SELECT body FROM incidents WHERE incident_id=?", (incident_id,)
        ).fetchone()
        if row is None:
            raise KeyError(incident_id)
        return Incident.model_validate_json(row[0])

    def incidents(self) -> list[Incident]:
        rows = self.db.execute("SELECT body FROM incidents ORDER BY incident_id")
        return [Incident.model_validate_json(r[0]) for r in rows]

    def artifacts(self, incident_id: str) -> list[Artifact]:
        rows = self.db.execute(
            "SELECT body FROM artifacts WHERE incident_id=? ORDER BY path", (incident_id,)
        )
        return [Artifact.model_validate_json(r[0]) for r in rows]

    def views(self, incident_id: str | None = None) -> list[View]:
        if incident_id is None:
            rows = self.db.execute("SELECT body FROM views ORDER BY incident_id, view_id")
        else:
            rows = self.db.execute(
                "SELECT body FROM views WHERE incident_id=? ORDER BY view_id", (incident_id,)
            )
        return [View.model_validate_json(r[0]) for r in rows]

    def get_view(self, view_id: str) -> View:
        row = self.db.execute("SELECT body FROM views WHERE view_id=?", (view_id,)).fetchone()
        if row is None:
            raise KeyError(view_id)
        return View.model_validate_json(row[0])

    # -- observations ----------------------------------------------------------------

    def put_observations(
        self, view_id: str, extractor: str, observations: Iterable[Observation]
    ) -> None:
        """Replace one extractor's output for one view."""
        with self.db:
            self.db.execute(
                "DELETE FROM observations WHERE view_id=? AND extractor=?", (view_id, extractor)
            )
            self.db.executemany(
                "INSERT INTO observations (view_id, extractor, namespace, value_key, body) VALUES (?,?,?,?,?)",
                [
                    (o.view_id, extractor, o.namespace, value_key(o.value), o.model_dump_json())
                    for o in observations
                ],
            )

    def observations(self, view_id: str) -> list[Observation]:
        rows = self.db.execute(
            "SELECT body FROM observations WHERE view_id=? ORDER BY id", (view_id,)
        )
        return [Observation.model_validate_json(r[0]) for r in rows]

    def views_with_value(self, namespace: str, value) -> list[str]:
        """Inverted index: which views carry this exact value. Used for candidate generation."""
        rows = self.db.execute(
            "SELECT DISTINCT view_id FROM observations WHERE namespace=? AND value_key=?",
            (namespace, value_key(value)),
        )
        return [r[0] for r in rows]

    # -- fingerprints, evidence, relations -------------------------------------------

    def put_fingerprint(self, fp: Fingerprint) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO fingerprints VALUES (?,?,?)",
                (fp.view_id, fp.layer_id, fp.model_dump_json()),
            )

    def fingerprints(
        self, layer_id: str | None = None, view_id: str | None = None
    ) -> list[Fingerprint]:
        sql, args = "SELECT body FROM fingerprints WHERE 1=1", []
        if layer_id is not None:
            sql, args = sql + " AND layer_id=?", args + [layer_id]
        if view_id is not None:
            sql, args = sql + " AND view_id=?", args + [view_id]
        rows = self.db.execute(sql + " ORDER BY view_id, layer_id", args)
        return [Fingerprint.model_validate_json(r[0]) for r in rows]

    def put_evidence(self, ev: Evidence) -> None:
        a, b = ordered(ev.view_a, ev.view_b)
        ev = ev.model_copy(update={"view_a": a, "view_b": b})
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO evidence VALUES (?,?,?,?,?)",
                (a, b, ev.layer_id, ev.score, ev.model_dump_json()),
            )

    def evidence(
        self, view_a: str | None = None, view_b: str | None = None, min_score: float = 0.0
    ) -> list[Evidence]:
        sql, args = "SELECT body FROM evidence WHERE score>=?", [min_score]
        if view_a is not None and view_b is not None:
            a, b = ordered(view_a, view_b)
            sql, args = sql + " AND view_a=? AND view_b=?", args + [a, b]
        elif view_a is not None:
            sql, args = sql + " AND (view_a=? OR view_b=?)", args + [view_a, view_a]
        rows = self.db.execute(sql + " ORDER BY score DESC, view_a, view_b, layer_id", args)
        return [Evidence.model_validate_json(r[0]) for r in rows]

    def put_relation(self, rel: Relation) -> None:
        a, b = ordered(rel.view_a, rel.view_b)
        rel = rel.model_copy(update={"view_a": a, "view_b": b})
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO relations VALUES (?,?,?,?)",
                (a, b, rel.type.value, rel.model_dump_json()),
            )

    def relations(self) -> list[Relation]:
        rows = self.db.execute("SELECT body FROM relations ORDER BY view_a, view_b")
        return [Relation.model_validate_json(r[0]) for r in rows]

    def get_relation(self, view_a: str, view_b: str) -> Relation | None:
        """The stored relation for one pair, in either order; None when the pair was never fused."""
        a, b = ordered(view_a, view_b)
        row = self.db.execute(
            "SELECT body FROM relations WHERE view_a = ? AND view_b = ?", (a, b)
        ).fetchone()
        return Relation.model_validate_json(row[0]) if row else None
