"""What discovery has asked and seen, kept in its own tables of the shared SQLite file.

Separate from `store.py` on purpose: the shared store stays frozen, and discovery's
bookkeeping (runs, queries, every ad ever seen) is not evidence until an ad is ingested.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import LibraryAd, LibraryQuery, Triage

_SCHEMA = """
CREATE TABLE IF NOT EXISTS discovery_runs (
    run_id TEXT PRIMARY KEY, source TEXT NOT NULL, started TEXT NOT NULL, finished TEXT,
    status TEXT NOT NULL, counts TEXT NOT NULL DEFAULT '{}', error TEXT, screenshot TEXT);
CREATE TABLE IF NOT EXISTS discovery_queries (
    query_id TEXT PRIMARY KEY, body TEXT NOT NULL, origin TEXT NOT NULL, reason TEXT,
    enabled INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL,
    last_run TEXT, last_status TEXT, last_success TEXT, last_count INTEGER);
CREATE TABLE IF NOT EXISTS discovery_ads (
    source TEXT NOT NULL, ad_id TEXT NOT NULL, page_id TEXT,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
    is_active INTEGER, end_date TEXT, record TEXT NOT NULL, raw TEXT,
    triage TEXT, status TEXT NOT NULL DEFAULT 'new', incident_id TEXT, note TEXT,
    PRIMARY KEY (source, ad_id));
CREATE INDEX IF NOT EXISTS discovery_ads_status ON discovery_ads (status);
CREATE INDEX IF NOT EXISTS discovery_ads_page ON discovery_ads (page_id);
CREATE TABLE IF NOT EXISTS discovery_hits (
    run_id TEXT NOT NULL, query_id TEXT NOT NULL, source TEXT NOT NULL, ad_id TEXT NOT NULL,
    PRIMARY KEY (run_id, query_id, source, ad_id));
"""

# Ad status, in the order an ad normally moves through them.
NEW, SCREENED_OUT, SELECTED, INGESTED, FAILED = (
    "new",
    "screened_out",
    "selected",
    "ingested",
    "failed",
)


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class DiscoveryState:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)

    def close(self) -> None:
        self.db.close()

    # -- runs ------------------------------------------------------------------------

    def start_run(self, source: str) -> str:
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        with self.db:
            self.db.execute(
                "INSERT INTO discovery_runs (run_id, source, started, status) VALUES (?,?,?,?)",
                (run_id, source, now(), "running"),
            )
        return run_id

    def finish_run(
        self, run_id: str, status: str, counts: dict[str, int], error: str | None = None,
        screenshot: str | None = None,
    ) -> None:  # fmt: skip
        with self.db:
            self.db.execute(
                "UPDATE discovery_runs SET finished=?, status=?, counts=?, error=?, screenshot=? "
                "WHERE run_id=?",
                (now(), status, json.dumps(counts), error, screenshot, run_id),
            )

    def runs(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM discovery_runs ORDER BY started DESC, rowid DESC LIMIT ?", (limit,)
        )
        return [{**dict(r), "counts": json.loads(r["counts"] or "{}")} for r in rows]

    # -- queries ---------------------------------------------------------------------

    def upsert_query(self, query: LibraryQuery) -> bool:
        """Add a query, or refresh its reason. True when it was new. Enabled flag is kept."""
        exists = self.db.execute(
            "SELECT 1 FROM discovery_queries WHERE query_id=?", (query.query_id,)
        ).fetchone()
        with self.db:
            if exists:
                self.db.execute(
                    "UPDATE discovery_queries SET body=?, reason=? WHERE query_id=?",
                    (query.model_dump_json(), query.reason, query.query_id),
                )
            else:
                self.db.execute(
                    "INSERT INTO discovery_queries (query_id, body, origin, reason, created) "
                    "VALUES (?,?,?,?,?)",
                    (
                        query.query_id,
                        query.model_dump_json(),
                        query.origin.value,
                        query.reason,
                        now(),
                    ),
                )
        return not exists

    def set_enabled(self, query_id: str, enabled: bool) -> None:
        with self.db:
            self.db.execute(
                "UPDATE discovery_queries SET enabled=? WHERE query_id=?", (int(enabled), query_id)
            )

    def queries(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM discovery_queries"
        if enabled_only:
            sql += " WHERE enabled=1"
        rows = self.db.execute(sql + " ORDER BY origin, created")
        return [{**dict(r), "query": LibraryQuery.model_validate_json(r["body"])} for r in rows]

    def mark_query(self, query_id: str, status: str, count: int) -> None:
        stamp = now()
        with self.db:
            self.db.execute(
                "UPDATE discovery_queries SET last_run=?, last_status=?, last_count=?, "
                "last_success=CASE WHEN ?='ok' THEN ? ELSE last_success END WHERE query_id=?",
                (stamp, status, count, status, stamp, query_id),
            )

    def last_success(self, query_id: str) -> str | None:
        row = self.db.execute(
            "SELECT last_success FROM discovery_queries WHERE query_id=?", (query_id,)
        ).fetchone()
        return row["last_success"] if row else None

    # -- ads -------------------------------------------------------------------------

    def record_ad(self, run_id: str, query_id: str, ad: LibraryAd, raw: dict[str, Any]) -> str:
        """Store a sighting. Returns 'new', 'stopped' (was active, now is not) or 'seen'."""
        stamp = now()
        row = self.db.execute(
            "SELECT is_active FROM discovery_ads WHERE source=? AND ad_id=?", (ad.source, ad.ad_id)
        ).fetchone()
        active = None if ad.is_active is None else int(ad.is_active)
        with self.db:
            if row is None:
                self.db.execute(
                    "INSERT INTO discovery_ads (source, ad_id, page_id, first_seen, last_seen, "
                    "is_active, end_date, record, raw) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        ad.source,
                        ad.ad_id,
                        ad.page_id,
                        stamp,
                        stamp,
                        active,
                        ad.end_date,
                        ad.model_dump_json(),
                        json.dumps(raw, ensure_ascii=False),
                    ),
                )
                outcome = "new"
            else:
                self.db.execute(
                    "UPDATE discovery_ads SET last_seen=?, is_active=?, end_date=?, record=?, raw=? "
                    "WHERE source=? AND ad_id=?",
                    (
                        stamp,
                        active,
                        ad.end_date,
                        ad.model_dump_json(),
                        json.dumps(raw, ensure_ascii=False),
                        ad.source,
                        ad.ad_id,
                    ),
                )
                outcome = "stopped" if row["is_active"] == 1 and active == 0 else "seen"
            self.db.execute(
                "INSERT OR IGNORE INTO discovery_hits VALUES (?,?,?,?)",
                (run_id, query_id, ad.source, ad.ad_id),
            )
        return outcome

    def ad(self, ad_id: str, source: str = "meta") -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT * FROM discovery_ads WHERE source=? AND ad_id=?", (source, ad_id)
        ).fetchone()
        return self._ad_row(row) if row else None

    def ads(self, status: str | None = None, run_id: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT a.* FROM discovery_ads a", []
        if run_id is not None:
            sql += " JOIN (SELECT DISTINCT source, ad_id FROM discovery_hits WHERE run_id=?) h " \
                   "USING (source, ad_id)"  # fmt: skip
            args.append(run_id)
        if status is not None:
            sql += " WHERE a.status=?"
            args.append(status)
        rows = self.db.execute(sql + " ORDER BY a.first_seen DESC, a.ad_id", args)
        return [self._ad_row(r) for r in rows]

    def set_triage(self, ad_id: str, triage: Triage, status: str, source: str = "meta") -> None:
        with self.db:
            self.db.execute(
                "UPDATE discovery_ads SET triage=?, status=? WHERE source=? AND ad_id=?",
                (triage.model_dump_json(), status, source, ad_id),
            )

    def set_status(
        self, ad_id: str, status: str, incident_id: str | None = None, note: str | None = None,
        source: str = "meta",
    ) -> None:  # fmt: skip
        with self.db:
            self.db.execute(
                "UPDATE discovery_ads SET status=?, incident_id=COALESCE(?, incident_id), note=? "
                "WHERE source=? AND ad_id=?",
                (status, incident_id, note, source, ad_id),
            )

    def known_page_ids(self) -> list[str]:
        rows = self.db.execute(
            "SELECT DISTINCT page_id FROM discovery_ads WHERE page_id IS NOT NULL AND status=?",
            (INGESTED,),
        )
        return [r[0] for r in rows]

    @staticmethod
    def _ad_row(row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        out["ad"] = LibraryAd.model_validate_json(row["record"])
        out["raw"] = json.loads(row["raw"]) if row["raw"] else None
        out["triage"] = Triage.model_validate_json(row["triage"]) if row["triage"] else None
        return out
