"""Read-only SQLite3 Multiple Ciphers provider for LINE Desktop databases."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

from .discovery import choose_database_candidate, discover_database_candidates
from .keys import DatabaseKeyProvider
from .models import LineChatTarget, LineDatabaseMessage, ResolvedLineChat


class SQLiteLineDatabaseProvider:
    """Open the encrypted LINE database without modifying it.

    The codec settings are the values verified against LINE for macOS 26.2.0.
    The class is platform-neutral so a future Windows locator/key provider can
    reuse it if the Windows database is verified to use the same format.
    """

    def __init__(self, database_path: str | Path | None, key_provider: DatabaseKeyProvider):
        self.database_path = self.locate_database(database_path)
        self.key_provider = key_provider
        self._connection = None
        self._database_id = ""

    @staticmethod
    def locate_database(explicit: str | Path | None = None) -> Path:
        if explicit:
            path = Path(explicit).expanduser().resolve()
            if not path.is_file():
                raise RuntimeError(f"LINE database not found: {path}")
            return path

        return choose_database_candidate(discover_database_candidates())

    @property
    def database_id(self) -> str:
        if not self._database_id:
            with self.database_path.open("rb") as handle:
                header = handle.read(16)
            self._database_id = hashlib.sha256(
                str(self.database_path).encode("utf-8") + b"\0" + header
            ).hexdigest()[:24]
        return self._database_id

    def connect(self):
        if self._connection is not None:
            return self._connection
        try:
            import apsw
        except ImportError as exc:
            raise RuntimeError(
                "apsw-sqlite3mc is required to read the encrypted LINE database"
            ) from exc

        key = self.key_provider.get_key()
        uri = self.database_path.as_uri() + "?mode=ro"
        flags = apsw.SQLITE_OPEN_READONLY | apsw.SQLITE_OPEN_URI
        connection = apsw.Connection(uri, flags=flags)
        try:
            connection.setbusytimeout(5000)
            connection.execute("PRAGMA cipher='aes128cbc'")
            connection.execute("PRAGMA legacy=0")
            connection.execute("PRAGMA kdf_iter=1")
            connection.execute(f"PRAGMA key='{key}'")
            connection.execute("PRAGMA query_only=ON")
            connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except Exception:
            connection.close()
            raise
        self._connection = connection
        return connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def integrity_check(self) -> str:
        return str(self.connect().execute("PRAGMA integrity_check").fetchone()[0])

    def resolve_chats(self, targets: Iterable[LineChatTarget]) -> list[ResolvedLineChat]:
        connection = self.connect()
        resolved: list[ResolvedLineChat] = []
        for target in targets:
            matches = []
            for kind, table, id_column, name_column in (
                ("openchat", "_squareChat", "_squareChatMid", "_name"),
                ("group", "_groupChat", "_chatMid", "_chatName"),
            ):
                row = connection.execute(
                    f'SELECT "{id_column}" FROM "{table}" WHERE "{name_column}"=?',
                    (target.chat_name,),
                ).fetchone()
                if row:
                    matches.append((kind, str(row[0])))
            if not matches:
                raise RuntimeError(f"LINE chat was not found: {target.chat_name}")
            if len(matches) > 1:
                raise RuntimeError(
                    f"LINE chat name is ambiguous across chat types: {target.chat_name}"
                )
            kind, chat_id = matches[0]
            resolved.append(ResolvedLineChat(target=target, chat_id=chat_id, kind=kind))
        return resolved

    def latest_rowid(self, chat: ResolvedLineChat) -> int:
        row = self.connect().execute(
            "SELECT COALESCE(max(rowid), 0) FROM _message WHERE _chatId=?",
            (chat.chat_id,),
        ).fetchone()
        return int(row[0] or 0)

    def fetch_after(
        self,
        chat: ResolvedLineChat,
        rowid: int,
        limit: int = 500,
    ) -> list[LineDatabaseMessage]:
        rows = self.connect().execute(
            """
            SELECT r.rowid,
                   r._id,
                   r._createdTime,
                   r._from,
                   COALESCE(rsm._displayName, rc._displayNameOverridden,
                            rc._displayName, r._from, ''),
                   r._text,
                   COALESCE(r._contentType, 0),
                   COALESCE(r._messageRelationType, 0),
                   COALESCE(r._relatedMessageId, ''),
                   COALESCE(o._from, ''),
                   COALESCE(osm._displayName, oc._displayNameOverridden,
                            oc._displayName, o._from, ''),
                   COALESCE(o._text, '')
              FROM _message AS r
              LEFT JOIN _message AS o
                     ON o._id=r._relatedMessageId AND o._chatId=r._chatId
              LEFT JOIN _squareMember AS rsm ON rsm._squareMemberMid=r._from
              LEFT JOIN _contact AS rc ON rc._mid=r._from
              LEFT JOIN _squareMember AS osm ON osm._squareMemberMid=o._from
              LEFT JOIN _contact AS oc ON oc._mid=o._from
             WHERE r._chatId=? AND r.rowid>?
             ORDER BY r.rowid
             LIMIT ?
            """,
            (chat.chat_id, max(0, int(rowid)), max(1, min(int(limit), 5000))),
        )
        messages = []
        for values in rows:
            messages.append(
                LineDatabaseMessage(
                    rowid=int(values[0]),
                    message_id=str(values[1] or ""),
                    chat=chat,
                    created_time_ms=int(values[2] or 0),
                    sender_id=str(values[3] or ""),
                    sender_name=str(values[4] or ""),
                    text=str(values[5] or ""),
                    content_type=int(values[6] or 0),
                    relation_type=int(values[7] or 0),
                    related_message_id=str(values[8] or ""),
                    related_sender_id=str(values[9] or ""),
                    related_sender_name=str(values[10] or ""),
                    related_text=str(values[11] or ""),
                )
            )
        return messages
