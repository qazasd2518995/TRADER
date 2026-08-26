"""Read-only SQLite3 Multiple Ciphers provider for LINE Desktop databases."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

from .discovery import choose_database_candidate, discover_database_candidates
from .keys import DatabaseKeyProvider
from .models import LineChatTarget, LineDatabaseMessage, LineMessageMetadata, ResolvedLineChat


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
                if target.chat_kind and target.chat_kind != kind:
                    continue
                if target.chat_id:
                    row = connection.execute(
                        f'SELECT "{id_column}" FROM "{table}" WHERE "{id_column}"=?',
                        (target.chat_id,),
                    ).fetchone()
                    if row:
                        matches.append((kind, str(row[0])))
                    continue
                rows = connection.execute(
                    f'SELECT "{id_column}" FROM "{table}" WHERE "{name_column}"=?',
                    (target.chat_name,),
                )
                matches.extend((kind, str(row[0])) for row in rows if row and row[0])
            if not matches:
                if target.chat_id:
                    raise RuntimeError(
                        f"bound LINE chat ID is no longer present for target: {target.name}"
                    )
                raise RuntimeError(f"LINE chat was not found: {target.chat_name}")
            if len(matches) > 1:
                raise RuntimeError(
                    f"LINE chat name is ambiguous: {target.chat_name}"
                )
            kind, chat_id = matches[0]
            resolved.append(ResolvedLineChat(target=target, chat_id=chat_id, kind=kind))
        return resolved

    def resolve_sender_ids(
        self,
        chat: ResolvedLineChat,
        sender_names: Iterable[str],
    ) -> dict[str, str]:
        """Resolve configured display names once, rejecting ambiguous matches."""
        wanted = {"".join(str(name).split()).casefold(): str(name) for name in sender_names}
        if not wanted:
            return {}
        rows = self.connect().execute(
            """
            SELECT DISTINCT m._from,
                   COALESCE(sm._displayName, c._displayNameOverridden,
                            c._displayName, m._from, '')
              FROM _message AS m
              LEFT JOIN _squareMember AS sm ON sm._squareMemberMid=m._from
              LEFT JOIN _contact AS c ON c._mid=m._from
             WHERE m._chatId=? AND COALESCE(m._from, '')<>''
            """,
            (chat.chat_id,),
        )
        candidates: dict[str, set[str]] = {key: set() for key in wanted}
        for sender_id, display_name in rows:
            normalized = "".join(str(display_name or "").split()).casefold()
            if normalized in candidates and sender_id:
                candidates[normalized].add(str(sender_id))

        resolved: dict[str, str] = {}
        for normalized, configured_name in wanted.items():
            ids = candidates[normalized]
            if len(ids) > 1:
                raise RuntimeError(
                    f"trusted sender name is ambiguous in LINE chat {chat.target.name}: "
                    f"{configured_name!r}"
                )
            if ids:
                resolved[configured_name] = next(iter(ids))
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
                   '',
                   COALESCE(r._rev, 0),
                   COALESCE(r._status, 0),
                   COALESCE(r._type, 0),
                   COALESCE(r._reactionStatus, 0)
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
                    database_id=self.database_id,
                    revision=int(values[12] or 0),
                    status=int(values[13] or 0),
                    message_type=int(values[14] or 0),
                    reaction_status=str(values[15] or ""),
                )
            )
        return messages

    def fetch_message_metadata(
        self,
        chat: ResolvedLineChat,
        message_ids: Iterable[str],
    ) -> list[LineMessageMetadata]:
        """Re-read revision/status fields without exposing message bodies.

        The strict UNSENT combination is verified for recall synchronization;
        other edit/reaction values remain diagnostic-only.
        """
        ids = tuple(dict.fromkeys(str(value) for value in message_ids if str(value)))
        if not ids:
            return []
        if len(ids) > 500:
            raise ValueError("at most 500 LINE message IDs may be checked at once")
        placeholders = ",".join("?" for _ in ids)
        rows = self.connect().execute(
            f"""
            SELECT _id, COALESCE(_rev, 0), COALESCE(_status, 0),
                   COALESCE(_type, 0), COALESCE(_reactionStatus, 0),
                   COALESCE(_text, ''), COALESCE(_createdTime, 0),
                   COALESCE(_attribute, 0), COALESCE(_eventInfo, ''),
                   COALESCE(_contentMetadata, '')
              FROM _message
             WHERE _chatId=? AND _id IN ({placeholders})
            """,
            (chat.chat_id, *ids),
        )
        result = []
        for row in rows:
            try:
                event_info = json.loads(str(row[8] or ""))
            except (TypeError, ValueError, json.JSONDecodeError):
                event_info = {}
            try:
                content_metadata = json.loads(str(row[9] or ""))
            except (TypeError, ValueError, json.JSONDecodeError):
                content_metadata = {}
            event_type = str(event_info.get("type") or "") if isinstance(event_info, dict) else ""
            unsent = bool(
                isinstance(content_metadata, dict)
                and content_metadata.get("UNSENT") is True
                and event_type == "20"
                and int(row[3] or 0) == 3
                and int(row[7] or 0) == 1
            )
            result.append(
                LineMessageMetadata(
                    message_id=str(row[0] or ""),
                    revision=int(row[1] or 0),
                    status=int(row[2] or 0),
                    message_type=int(row[3] or 0),
                    reaction_status=str(row[4] or ""),
                    text_sha256=hashlib.sha256(str(row[5] or "").encode("utf-8")).hexdigest(),
                    created_time_ms=int(row[6] or 0),
                    attribute=int(row[7] or 0),
                    event_type=event_type,
                    unsent=unsent,
                )
            )
        return result
