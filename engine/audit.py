"""
audit.py
========
Persistent, append-only audit log (the record an examiner replays to
reconstruct exactly how a report was produced).

Immutability is enforced *in the database*, not by convention:
  * BEFORE UPDATE and BEFORE DELETE triggers RAISE(ABORT, ...), so no row can be
    altered or removed after insertion.
  * Each row carries a hash chain (row_hash = sha256(prev_hash || payload)).
    Any retroactive edit -- even one that somehow bypassed the triggers -- breaks
    the chain and is detectable via ``verify_chain``.

Events recorded: graph_construction, figure_computation, reconciliation,
configuration_change, export (at minimum, per the assignment).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    event        TEXT NOT NULL,
    trigger      TEXT NOT NULL,
    data_json    TEXT NOT NULL,
    retention    TEXT NOT NULL,
    recorded_at  TEXT NOT NULL,
    prev_hash    TEXT NOT NULL,
    row_hash     TEXT NOT NULL
);

-- Append-only: forbid mutation of existing rows.
CREATE TRIGGER IF NOT EXISTS audit_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only: UPDATE forbidden');
END;

CREATE TRIGGER IF NOT EXISTS audit_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only: DELETE forbidden');
END;
"""

# Retention policy (from sample_fund_guidelines.pdf section 5.1).
RETENTION = {
    "graph_construction": "7y",
    "figure_computation": "7y",
    "reconciliation": "7y",
    "configuration_change": "7y",
    "export": "10y",
    "run_started": "7y",
    "narrative_firewall": "7y",
}


class AuditLog:
    def __init__(self, path: str, as_of: str = "2024-01-01T00:00:00Z"):
        self.path = path
        self.as_of = as_of
        self.conn = sqlite3.connect(path)
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def _last_hash(self) -> str:
        row = self.conn.execute(
            "SELECT row_hash FROM audit_log ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else "GENESIS"

    def record(self, event: str, trigger: str, data: dict[str, Any],
               recorded_at: Optional[str] = None) -> str:
        """Append one immutable event; returns its row_hash."""
        prev = self._last_hash()
        payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
        ts = recorded_at or self.as_of
        retention = RETENTION.get(event, "7y")
        row_hash = hashlib.sha256(
            f"{prev}|{event}|{trigger}|{payload}|{retention}|{ts}".encode()
        ).hexdigest()
        self.conn.execute(
            "INSERT INTO audit_log "
            "(event, trigger, data_json, retention, recorded_at, prev_hash, row_hash) "
            "VALUES (?,?,?,?,?,?,?)",
            (event, trigger, payload, retention, ts, prev, row_hash),
        )
        self.conn.commit()
        return row_hash

    def all(self) -> list[dict]:
        cur = self.conn.execute(
            "SELECT seq, event, trigger, data_json, retention, recorded_at, "
            "prev_hash, row_hash FROM audit_log ORDER BY seq"
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def verify_chain(self) -> bool:
        """Recompute the hash chain; True iff intact."""
        prev = "GENESIS"
        for row in self.all():
            expect = hashlib.sha256(
                f"{prev}|{row['event']}|{row['trigger']}|{row['data_json']}|"
                f"{row['retention']}|{row['recorded_at']}".encode()
            ).hexdigest()
            if expect != row["row_hash"] or row["prev_hash"] != prev:
                return False
            prev = row["row_hash"]
        return True

    def close(self) -> None:
        self.conn.close()
