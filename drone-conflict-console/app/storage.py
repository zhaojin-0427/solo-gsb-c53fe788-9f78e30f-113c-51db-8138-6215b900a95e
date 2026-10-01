"""SQLite persistence for imported scenarios, saved plans and batches.

A *scenario* keeps the exact imported JSON (input snapshot) together with the
safety parameters used for the most recent solve, so saved plans can always be
reopened and compared against their original input.

An *execution batch* is itself a frozen snapshot: it copies the input tracks,
safety thresholds and planned offsets at creation time. Re-solving or deleting
the originating scenario therefore never changes an existing batch.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

_LOCK = threading.RLock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scenarios (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL DEFAULT '',
    input_json      TEXT NOT NULL,
    safety_radius   REAL NOT NULL,
    safety_height   REAL NOT NULL,
    created_at      REAL NOT NULL,
    solved_at       REAL,
    result_json     TEXT
);

CREATE TABLE IF NOT EXISTS batches (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    source_scenario_id INTEGER,
    name              TEXT NOT NULL DEFAULT '',
    snapshot_json     TEXT NOT NULL,
    safety_radius     REAL NOT NULL,
    safety_height     REAL NOT NULL,
    created_at        REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS batch_reports (
    batch_id      INTEGER NOT NULL,
    sortie_id     TEXT NOT NULL,
    actual_offset INTEGER NOT NULL CHECK (actual_offset >= 0),
    revision      INTEGER NOT NULL CHECK (revision >= 1),
    reported_at   REAL NOT NULL,
    PRIMARY KEY (batch_id, sortie_id),
    FOREIGN KEY (batch_id) REFERENCES batches(id) ON DELETE CASCADE
);
"""


class RevisionConflictError(Exception):
    """A report revision was stale or reused with a different actual offset."""

    def __init__(self, sortie_id: str, submitted: int, stored: int):
        super().__init__(
            f"revision conflict for {sortie_id}: submitted {submitted}, "
            f"stored {stored}"
        )
        self.sortie_id = sortie_id
        self.submitted_revision = submitted
        self.stored_revision = stored


class Store:
    def __init__(self, path: str | Path = "data/console.db"):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def create_scenario(self, input_payload: dict, safety_radius: float,
                        safety_height: float, name: str = "") -> int:
        with _LOCK, self._conn() as c:
            cur = c.execute(
                "INSERT INTO scenarios(name,input_json,safety_radius,safety_height,created_at)"
                " VALUES (?,?,?,?,?)",
                (name, json.dumps(input_payload, ensure_ascii=False),
                 float(safety_radius), float(safety_height), time.time()),
            )
            return int(cur.lastrowid)

    def save_result(self, scenario_id: int, result: dict, safety_radius: float,
                    safety_height: float, name: str | None = None) -> None:
        with _LOCK, self._conn() as c:
            if name is not None:
                c.execute("UPDATE scenarios SET name=? WHERE id=?", (name, scenario_id))
            c.execute(
                "UPDATE scenarios SET result_json=?, solved_at=?, safety_radius=?, safety_height=?"
                " WHERE id=?",
                (json.dumps(result, ensure_ascii=False), time.time(),
                 float(safety_radius), float(safety_height), scenario_id),
            )

    def list_scenarios(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id,name,safety_radius,safety_height,created_at,solved_at,"
                "CASE WHEN result_json IS NOT NULL THEN 1 ELSE 0 END AS has_result "
                "FROM scenarios ORDER BY id DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_scenario(self, scenario_id: int) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM scenarios WHERE id=?", (scenario_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["input"] = json.loads(d.pop("input_json"))
        d["result"] = json.loads(d["result_json"]) if d.get("result_json") else None
        return d

    def delete_scenario(self, scenario_id: int) -> bool:
        with _LOCK, self._conn() as c:
            cur = c.execute("DELETE FROM scenarios WHERE id=?", (scenario_id,))
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Execution batches
    # ------------------------------------------------------------------

    def create_batch(self, source_scenario_id: int | None, name: str,
                     snapshot: dict, safety_radius: float,
                     safety_height: float) -> int:
        with _LOCK, self._conn() as c:
            cur = c.execute(
                "INSERT INTO batches(source_scenario_id,name,snapshot_json,safety_radius,"
                "safety_height,created_at) VALUES (?,?,?,?,?,?)",
                (source_scenario_id, name,
                 json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
                 float(safety_radius), float(safety_height), time.time()),
            )
            return int(cur.lastrowid)

    def list_batches(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT b.id,b.source_scenario_id,b.name,b.safety_radius,b.safety_height,"
                "b.created_at,COUNT(r.sortie_id) AS reported_count "
                "FROM batches b LEFT JOIN batch_reports r "
                "ON b.id = r.batch_id GROUP BY b.id ORDER BY b.id DESC"
            ).fetchall()
            out = [dict(r) for r in rows]
            for row in out:
                row["snapshot"] = self._get_snapshot(row["id"], c)
                row["sortie_count"] = len(row["snapshot"]["input"]["sorties"])
            return out

    def get_batch(self, batch_id: int) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
            if row is None:
                return None
            d = dict(row)
            d["snapshot"] = json.loads(d.pop("snapshot_json"))
            d["reports"] = self._get_reports(batch_id, c)
        return d

    @staticmethod
    def _get_snapshot(batch_id: int, c) -> dict:
        row = c.execute("SELECT snapshot_json FROM batches WHERE id=?", (batch_id,)).fetchone()
        return json.loads(row["snapshot_json"]) if row else {}

    @staticmethod
    def _get_reports(batch_id: int, c) -> dict[str, dict]:
        rows = c.execute(
            "SELECT sortie_id,actual_offset,revision,reported_at "
            "FROM batch_reports WHERE batch_id=? ORDER BY sortie_id",
            (batch_id,),
        ).fetchall()
        return {r["sortie_id"]: dict(r) for r in rows}

    def apply_batch_reports(self, batch_id: int, reports: list[dict]) -> dict[str, str]:
        """Apply one idempotent report request atomically.

        The whole request is accepted only if every item is new or strictly
        newer than the stored revision. Concurrent SQLite writers serialise on
        the connection transaction, and the ``revision > existing`` predicate
        guarantees that an older racing request cannot replace a newer one.
        """
        with _LOCK, self._conn() as c:
            try:
                c.execute("BEGIN IMMEDIATE")
                if c.execute("SELECT 1 FROM batches WHERE id=?", (batch_id,)).fetchone() is None:
                    c.rollback()
                    raise KeyError(batch_id)

                snapshot = self._get_snapshot(batch_id, c)
                known = {s["id"] for s in snapshot["input"]["sorties"]}
                seen: set[str] = set()
                now = time.time()
                outcomes: dict[str, str] = {}
                for item in reports:
                    sid = item["sortie_id"]
                    if sid not in known:
                        raise ValueError(f"unknown sortie id: {sid}")
                    if sid in seen:
                        raise ValueError(f"duplicate report for sortie: {sid}")
                    seen.add(sid)

                    stored = c.execute(
                        "SELECT actual_offset,revision FROM batch_reports "
                        "WHERE batch_id=? AND sortie_id=?",
                        (batch_id, sid),
                    ).fetchone()
                    revision = int(item["revision"])
                    actual = int(item["actual_offset"])
                    if stored is not None:
                        old_revision = int(stored["revision"])
                        if revision < old_revision:
                            raise RevisionConflictError(sid, revision, old_revision)
                        if revision == old_revision:
                            if actual != int(stored["actual_offset"]):
                                raise RevisionConflictError(sid, revision, old_revision)
                            outcomes[sid] = "idempotent"
                            continue
                    elif revision != 1:
                        raise ValueError(
                            f"first report revision for {sid} must be 1, got {revision}"
                        )

                    cur = c.execute(
                        "INSERT INTO batch_reports(batch_id,sortie_id,actual_offset,revision,"
                        "reported_at) VALUES (?,?,?,?,?) "
                        "ON CONFLICT(batch_id,sortie_id) DO UPDATE SET "
                        "actual_offset=excluded.actual_offset,revision=excluded.revision,"
                        "reported_at=excluded.reported_at "
                        "WHERE excluded.revision > batch_reports.revision",
                        (batch_id, sid, actual, revision, now),
                    )
                    if cur.rowcount:
                        outcomes[sid] = "stored"
                    else:
                        stored_rev = c.execute(
                            "SELECT revision FROM batch_reports WHERE batch_id=? AND sortie_id=?",
                            (batch_id, sid),
                        ).fetchone()["revision"]
                        raise RevisionConflictError(sid, revision, int(stored_rev))
                c.commit()
                return outcomes
            except Exception:
                c.rollback()
                raise

    def delete_batch(self, batch_id: int) -> bool:
        with _LOCK, self._conn() as c:
            cur = c.execute("DELETE FROM batches WHERE id=?", (batch_id,))
            return cur.rowcount > 0
