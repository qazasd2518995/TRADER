"""Durable mapping from exact LINE messages to Hub/MT5 execution identities."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Iterable

from .models import LineDatabaseMessage


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _hub_sequence(response: dict[str, Any]) -> int | None:
    sequence = int(response.get("seq") or 0)
    if not sequence:
        published = response.get("published")
        if isinstance(published, list) and published and isinstance(published[0], dict):
            sequence = int(published[0].get("seq") or 0)
    return sequence or None


class LineMessageLedger:
    """Persistent audit ledger that deliberately does not store message bodies.

    The ledger is the authoritative cancellation lookup. Parser changes can no
    longer change which execution IDs a reply-cancel targets.
    """

    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._create_schema()

    def _create_schema(self) -> None:
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS line_messages (
                    database_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    source_label TEXT NOT NULL,
                    rowid_value INTEGER NOT NULL,
                    sender_id_hash TEXT NOT NULL,
                    created_time_ms INTEGER NOT NULL,
                    text_sha256 TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,
                    message_status INTEGER NOT NULL DEFAULT 0,
                    parser_profile TEXT NOT NULL,
                    parse_status TEXT NOT NULL,
                    execution_ids_json TEXT NOT NULL DEFAULT '[]',
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (database_id, chat_id, message_id)
                );

                CREATE TABLE IF NOT EXISTS line_executions (
                    execution_id TEXT PRIMARY KEY,
                    database_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    signal_index INTEGER NOT NULL,
                    event_id TEXT NOT NULL,
                    signal_json TEXT NOT NULL,
                    publish_state TEXT NOT NULL,
                    hub_sequence INTEGER,
                    published_at REAL,
                    UNIQUE (database_id, chat_id, message_id, signal_index),
                    FOREIGN KEY (database_id, chat_id, message_id)
                      REFERENCES line_messages(database_id, chat_id, message_id)
                );

                CREATE TABLE IF NOT EXISTS line_cancellations (
                    event_id TEXT PRIMARY KEY,
                    database_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    target_message_id TEXT NOT NULL,
                    target_execution_ids_json TEXT NOT NULL,
                    hub_sequence INTEGER,
                    published_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS line_recalls (
                    event_id TEXT PRIMARY KEY,
                    database_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    target_execution_ids_json TEXT NOT NULL,
                    observed_revision INTEGER NOT NULL,
                    original_message_time_ms INTEGER NOT NULL,
                    observation_window_started_at REAL,
                    detected_at REAL NOT NULL,
                    state TEXT NOT NULL,
                    hub_sequence INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_line_execution_message
                    ON line_executions(database_id, chat_id, message_id, publish_state);
                """
            )

    @staticmethod
    def _database_id(message: LineDatabaseMessage) -> str:
        return message.database_id or "unknown-database"

    @staticmethod
    def _sender_hash(sender_id: str) -> str:
        return hashlib.sha256((sender_id or "").encode("utf-8")).hexdigest()[:24]

    def record_message(
        self,
        message: LineDatabaseMessage,
        *,
        parser_profile: str,
        parse_status: str,
        executions: Iterable[dict[str, Any]] = (),
    ) -> None:
        items = list(executions)
        execution_ids = [str(item["execution_id"]) for item in items]
        now = time.time()
        database_id = self._database_id(message)
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO line_messages(
                    database_id, chat_id, message_id, source_name, source_label,
                    rowid_value, sender_id_hash, created_time_ms, text_sha256,
                    revision, message_status, parser_profile, parse_status,
                    execution_ids_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(database_id, chat_id, message_id) DO UPDATE SET
                    rowid_value=excluded.rowid_value,
                    text_sha256=excluded.text_sha256,
                    revision=excluded.revision,
                    message_status=excluded.message_status,
                    parser_profile=excluded.parser_profile,
                    parse_status=excluded.parse_status,
                    execution_ids_json=excluded.execution_ids_json,
                    updated_at=excluded.updated_at
                """,
                (
                    database_id,
                    message.chat.chat_id,
                    message.message_id,
                    message.chat.target.name,
                    message.chat.target.display_name,
                    message.rowid,
                    self._sender_hash(message.sender_id),
                    message.created_time_ms,
                    hashlib.sha256((message.text or "").encode("utf-8")).hexdigest(),
                    message.revision,
                    message.status,
                    parser_profile,
                    parse_status,
                    _json(execution_ids),
                    now,
                ),
            )
            for item in items:
                self._connection.execute(
                    """
                    INSERT INTO line_executions(
                        execution_id, database_id, chat_id, message_id,
                        signal_index, event_id, signal_json, publish_state
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'parsed')
                    ON CONFLICT(execution_id) DO UPDATE SET
                        signal_json=excluded.signal_json,
                        event_id=excluded.event_id
                    """,
                    (
                        str(item["execution_id"]),
                        database_id,
                        message.chat.chat_id,
                        message.message_id,
                        int(item.get("signal_index") or 0),
                        str(item["event_id"]),
                        _json(item.get("signal") or {}),
                    ),
                )

    def mark_trade_published(
        self,
        execution_id: str,
        hub_response: dict[str, Any],
    ) -> None:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE line_executions
                   SET publish_state='published', hub_sequence=?, published_at=?
                 WHERE execution_id=?
                """,
                (_hub_sequence(hub_response), time.time(), execution_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"LINE ledger execution is missing: {execution_id}")

    def execution_ids(
        self,
        database_id: str,
        chat_id: str,
        message_id: str,
        *,
        published_only: bool = False,
    ) -> list[str]:
        state_clause = " AND publish_state='published'" if published_only else ""
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT execution_id
                  FROM line_executions
                 WHERE database_id=? AND chat_id=? AND message_id=?
                   {state_clause}
                 ORDER BY signal_index
                """,
                (database_id or "unknown-database", chat_id, message_id),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def published_execution_ids(
        self,
        database_id: str,
        chat_id: str,
        message_id: str,
    ) -> list[str]:
        return self.execution_ids(
            database_id,
            chat_id,
            message_id,
            published_only=True,
        )

    def execution_signals(self, execution_ids: Iterable[str]) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(str(value) for value in execution_ids if str(value)))
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT execution_id, signal_json
                  FROM line_executions
                 WHERE execution_id IN ({placeholders})
                """,
                ids,
            ).fetchall()
        by_id = {}
        for row in rows:
            try:
                value = json.loads(str(row[1] or "{}"))
            except json.JSONDecodeError:
                value = {}
            by_id[str(row[0])] = value if isinstance(value, dict) else {}
        return [by_id[value] for value in ids if value in by_id]

    def record_cancel_published(
        self,
        message: LineDatabaseMessage,
        *,
        event_id: str,
        target_message_id: str,
        target_execution_ids: Iterable[str],
        hub_response: dict[str, Any],
    ) -> None:
        execution_ids = list(target_execution_ids)
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO line_cancellations(
                    event_id, database_id, chat_id, message_id,
                    target_message_id, target_execution_ids_json,
                    hub_sequence, published_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    hub_sequence=excluded.hub_sequence,
                    published_at=excluded.published_at
                """,
                (
                    event_id,
                    self._database_id(message),
                    message.chat.chat_id,
                    message.message_id,
                    target_message_id,
                    _json(execution_ids),
                    _hub_sequence(hub_response),
                    time.time(),
                ),
            )
            self._connection.executemany(
                """
                UPDATE line_executions
                   SET publish_state='cancel_requested'
                 WHERE execution_id=? AND publish_state='published'
                """,
                ((execution_id,) for execution_id in execution_ids),
            )

    def watched_messages(
        self,
        database_id: str,
        chat_id: str,
        *,
        created_after_ms: int,
        include_shadow: bool = False,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        shadow_clause = (
            "OR (e.publish_state='parsed' AND m.parse_status='shadow_accepted')"
            if include_shadow
            else ""
        )
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT m.message_id, m.revision, m.created_time_ms
                  FROM line_messages AS m
                  JOIN line_executions AS e
                    ON e.database_id=m.database_id
                   AND e.chat_id=m.chat_id
                   AND e.message_id=m.message_id
                 WHERE m.database_id=? AND m.chat_id=?
                   AND m.created_time_ms>=?
                   AND (e.publish_state='published' {shadow_clause})
                 GROUP BY m.message_id, m.revision, m.created_time_ms
                 ORDER BY m.created_time_ms DESC
                 LIMIT ?
                """,
                (
                    database_id or "unknown-database",
                    chat_id,
                    max(0, int(created_after_ms)),
                    max(1, min(int(limit), 10000)),
                ),
            ).fetchall()
        return [dict(row) for row in rows]

    def recall_recorded(self, event_id: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM line_recalls WHERE event_id=?",
                (event_id,),
            ).fetchone()
        return row is not None

    def record_recall(
        self,
        *,
        event_id: str,
        database_id: str,
        chat_id: str,
        message_id: str,
        target_execution_ids: Iterable[str],
        observed_revision: int,
        original_message_time_ms: int,
        observation_window_started_at: float | None,
        detected_at: float,
        state: str,
        hub_response: dict[str, Any] | None = None,
    ) -> None:
        execution_ids = list(target_execution_ids)
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO line_recalls(
                    event_id, database_id, chat_id, message_id,
                    target_execution_ids_json, observed_revision,
                    original_message_time_ms, observation_window_started_at,
                    detected_at, state, hub_sequence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    observed_revision=excluded.observed_revision,
                    detected_at=excluded.detected_at,
                    state=excluded.state,
                    hub_sequence=excluded.hub_sequence
                """,
                (
                    event_id,
                    database_id or "unknown-database",
                    chat_id,
                    message_id,
                    _json(execution_ids),
                    int(observed_revision or 0),
                    int(original_message_time_ms or 0),
                    observation_window_started_at,
                    float(detected_at),
                    state,
                    _hub_sequence(hub_response or {}),
                ),
            )
            final_state = "recalled_shadow" if state == "shadow" else "recall_cancel_requested"
            self._connection.executemany(
                "UPDATE line_executions SET publish_state=? WHERE execution_id=?",
                ((final_state, execution_id) for execution_id in execution_ids),
            )
            self._connection.execute(
                """
                UPDATE line_messages
                   SET revision=?, parse_status=?, updated_at=?
                 WHERE database_id=? AND chat_id=? AND message_id=?
                """,
                (
                    int(observed_revision or 0),
                    "recalled_shadow" if state == "shadow" else "recalled_cancel_published",
                    time.time(),
                    database_id or "unknown-database",
                    chat_id,
                    message_id,
                ),
            )

    def recall_record(self, event_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM line_recalls WHERE event_id=?",
                (event_id,),
            ).fetchone()
        return dict(row) if row else None

    def message_record(self, database_id: str, chat_id: str, message_id: str) -> dict[str, Any] | None:
        """Small read-only helper used by diagnostics and tests."""
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM line_messages
                 WHERE database_id=? AND chat_id=? AND message_id=?
                """,
                (database_id or "unknown-database", chat_id, message_id),
            ).fetchone()
        return dict(row) if row else None

    def close(self) -> None:
        with self._lock:
            self._connection.close()
