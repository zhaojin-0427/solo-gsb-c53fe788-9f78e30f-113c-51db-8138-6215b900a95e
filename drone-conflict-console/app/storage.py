"""SQLite persistence for imported scenarios and saved plans.

A *scenario* keeps the exact imported JSON (input snapshot) together with the
safety parameters used for the most recent solve, so saved plans can always be
reopened and compared against their original input.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

_LOCK = threading.Lock()

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
"""


class Store:
    def __init__(self, path: str | Path = "data/console.db"):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
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
