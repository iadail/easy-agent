from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class SQLiteRunStore:
    def __init__(self, base_path: Path, database_name: str) -> None:
        self.base_path = base_path
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.trace_path = self.base_path / "traces"
        self.trace_path.mkdir(parents=True, exist_ok=True)
        self.db_path = self.base_path / database_name
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    graph_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_payload TEXT NOT NULL,
                    output_payload TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS node_events (
                    run_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    output_payload TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    run_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            connection.commit()

    def create_run(self, run_id: str, graph_name: str, input_payload: Any) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                "INSERT INTO runs(run_id, graph_name, status, input_payload, created_at) VALUES (?, ?, ?, ?, ?)",
                (run_id, graph_name, "running", json.dumps(input_payload), self._now()),
            )
            connection.commit()

    def finish_run(self, run_id: str, status: str, output_payload: Any) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                "UPDATE runs SET status = ?, output_payload = ? WHERE run_id = ?",
                (status, json.dumps(output_payload), run_id),
            )
            connection.commit()

    def record_node(
        self,
        run_id: str,
        node_id: str,
        status: str,
        attempt: int,
        output: Any,
        error: str | None,
    ) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO node_events(run_id, node_id, status, attempt, output_payload, error_message, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, node_id, status, attempt, json.dumps(output), error, self._now()),
            )
            connection.commit()

    def record_event(self, run_id: str, kind: str, payload: Any) -> None:
        encoded = json.dumps(payload)
        with closing(self._connect()) as connection:
            connection.execute(
                "INSERT INTO events(run_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
                (run_id, kind, encoded, self._now()),
            )
            connection.commit()
        with (self.trace_path / f"{run_id}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"kind": kind, "payload": payload, "created_at": self._now()}) + "\n")

    def load_trace(self, run_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            run_row = connection.execute(
                "SELECT graph_name, status, input_payload, output_payload, created_at FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if run_row is None:
                raise KeyError(f"Run not found: {run_id}")
            node_rows = connection.execute(
                """
                SELECT node_id, status, attempt, output_payload, error_message, created_at
                FROM node_events WHERE run_id = ? ORDER BY created_at ASC
                """,
                (run_id,),
            ).fetchall()
            event_rows = connection.execute(
                "SELECT kind, payload, created_at FROM events WHERE run_id = ? ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
        return {
            "graph_name": run_row[0],
            "status": run_row[1],
            "input_payload": json.loads(run_row[2]),
            "output_payload": json.loads(run_row[3]) if run_row[3] else None,
            "created_at": run_row[4],
            "nodes": [
                {
                    "node_id": row[0],
                    "status": row[1],
                    "attempt": row[2],
                    "output_payload": json.loads(row[3]) if row[3] else None,
                    "error_message": row[4],
                    "created_at": row[5],
                }
                for row in node_rows
            ],
            "events": [
                {"kind": row[0], "payload": json.loads(row[1]), "created_at": row[2]} for row in event_rows
            ],
        }

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()


