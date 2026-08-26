"""Persistent rowid cursor over one or more configured LINE chats."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import threading
import time
from typing import Iterable
from dataclasses import replace

from .models import LineChatTarget, LineDatabaseMessage, ResolvedLineChat


logger = logging.getLogger(__name__)


class LineDatabaseSource:
    """Poll exact LINE rows and acknowledge them transactionally.

    A per-chat SQLite rowid cursor replaces OCR hashes and fuzzy deduplication.
    The first run baselines at the current maximum rowid so existing chat
    history is never replayed as fresh trades. Collector-level age limits remain
    as a deliberate restart/backlog safety valve.
    """

    def __init__(
        self,
        provider,
        targets: Iterable[LineChatTarget],
        state_path: str | Path,
        batch_size: int = 500,
    ):
        self.provider = provider
        self.targets = list(targets)
        if not self.targets:
            raise ValueError("at least one LINE chat target is required")
        self.state_path = Path(state_path)
        self.batch_size = max(1, min(int(batch_size), 5000))
        self._lock = threading.Lock()
        self._resolved: list[ResolvedLineChat] | None = None
        self._state = self._load_state()

    def _load_state(self) -> dict:
        try:
            if self.state_path.exists():
                value = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    return value
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("LINE DB cursor could not be loaded; a fresh baseline will be used: %s", exc)
        return {"version": 2, "databases": {}}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.state_path)

    @property
    def chats(self) -> list[ResolvedLineChat]:
        if self._resolved is None:
            database = self._database_state()
            bindings = database.setdefault("bindings", {})
            prepared = []
            for target in self.targets:
                binding = bindings.get(target.name) or {}
                same_chat_configuration = (
                    binding.get("configured_chat_name") == target.chat_name
                )
                same_sender_configuration = (
                    list(binding.get("configured_trusted_senders") or ())
                    == list(target.trusted_senders)
                )
                prepared.append(
                    replace(
                        target,
                        chat_id=(
                            target.chat_id
                            or (
                                str(binding.get("chat_id") or "")
                                if same_chat_configuration
                                else ""
                            )
                        ),
                        chat_kind=(
                            target.chat_kind
                            or (
                                str(binding.get("chat_kind") or "")
                                if same_chat_configuration
                                else ""
                            )
                        ),
                        trusted_sender_ids=(
                            target.trusted_sender_ids
                            or (
                                tuple(str(value) for value in binding.get("trusted_sender_ids") or ())
                                if same_chat_configuration and same_sender_configuration
                                else ()
                            )
                        ),
                    )
                )

            resolved = self.provider.resolve_chats(prepared)
            changed = False
            stable: list[ResolvedLineChat] = []
            for chat in resolved:
                target = chat.target
                sender_ids = target.trusted_sender_ids
                resolver = getattr(self.provider, "resolve_sender_ids", None)
                if target.trusted_senders and not sender_ids and callable(resolver):
                    mapped = resolver(chat, target.trusted_senders)
                    missing = [name for name in target.trusted_senders if name not in mapped]
                    if missing:
                        raise RuntimeError(
                            f"trusted LINE sender could not be bound in {target.name}: "
                            + ", ".join(repr(name) for name in missing)
                        )
                    sender_ids = tuple(mapped[name] for name in target.trusted_senders)
                    target = replace(target, trusted_sender_ids=sender_ids)
                    chat = ResolvedLineChat(target=target, chat_id=chat.chat_id, kind=chat.kind)

                new_binding = {
                    "chat_id": chat.chat_id,
                    "chat_kind": chat.kind,
                    "trusted_sender_ids": list(sender_ids),
                    "configured_chat_name": target.chat_name,
                    "configured_trusted_senders": list(target.trusted_senders),
                    "bound_at": time.time(),
                }
                old_binding = bindings.get(target.name) or {}
                if any(old_binding.get(key) != value for key, value in new_binding.items() if key != "bound_at"):
                    bindings[target.name] = new_binding
                    changed = True
                stable.append(chat)
            self._resolved = stable
            if changed:
                self._state["version"] = 2
                self._save_state()
        return self._resolved

    def _database_state(self) -> dict:
        databases = self._state.setdefault("databases", {})
        return databases.setdefault(self.provider.database_id, {"chats": {}, "bindings": {}})

    def _chat_state(self, chat: ResolvedLineChat) -> dict:
        database = self._database_state()
        return database.setdefault("chats", {}).setdefault(chat.chat_id, {})

    def ensure_baseline(self) -> bool:
        """Create missing cursors. Return True when any baseline was created."""
        changed = False
        with self._lock:
            for chat in self.chats:
                state = self._chat_state(chat)
                if "last_rowid" in state:
                    continue
                latest = self.provider.latest_rowid(chat)
                state.update(
                    {
                        "target_name": chat.target.name,
                        "chat_name": chat.target.chat_name,
                        "last_rowid": latest,
                        "baselined_at": time.time(),
                    }
                )
                logger.info(
                    "LINE DB baseline %r: rowid=%s (history will not be replayed)",
                    chat.target.display_name,
                    latest,
                )
                changed = True
            if changed:
                self._save_state()
        return changed

    def poll(self) -> list[LineDatabaseMessage]:
        self.ensure_baseline()
        messages: list[LineDatabaseMessage] = []
        for chat in self.chats:
            state = self._chat_state(chat)
            cursor = int(state.get("last_rowid") or 0)
            latest = self.provider.latest_rowid(chat)
            if latest < cursor:
                # The DB was vacuumed/replaced. Re-baseline rather than replaying
                # old rows as new orders.
                logger.warning(
                    "LINE DB rowid moved backwards for %r (%s -> %s); re-baselining",
                    chat.target.display_name,
                    cursor,
                    latest,
                )
                state["last_rowid"] = latest
                state["baselined_at"] = time.time()
                self._save_state()
                continue
            messages.extend(self.provider.fetch_after(chat, cursor, self.batch_size))
        # Acknowledgement is a high-water mark per chat, so rows from the same
        # chat must never be reordered by a malformed/edited timestamp.
        messages.sort(key=lambda message: (message.chat.target.name, message.rowid))
        return messages

    def acknowledge(self, message: LineDatabaseMessage) -> None:
        with self._lock:
            state = self._chat_state(message.chat)
            current = int(state.get("last_rowid") or 0)
            if message.rowid > current:
                state["last_rowid"] = message.rowid
                state["last_message_id"] = message.message_id
                state["updated_at"] = time.time()
                self._save_state()

    def status(self) -> dict:
        self.ensure_baseline()
        return {
            "database": str(self.provider.database_path),
            "database_id": self.provider.database_id,
            "integrity_check": self.provider.integrity_check(),
            "chats": [
                {
                    "name": chat.target.name,
                    "chat_name": chat.target.chat_name,
                    "display_name": chat.target.display_name,
                    "kind": chat.kind,
                    "identity_bound": bool(chat.target.chat_id or chat.target.trusted_sender_ids),
                    "trusted_sender_count": len(chat.target.trusted_sender_ids),
                    "parser_profile": chat.target.parser_profile,
                    "max_trade_age_seconds": chat.target.max_trade_age_seconds,
                    "recall_watch_seconds": chat.target.recall_watch_seconds,
                    "last_rowid": int(self._chat_state(chat).get("last_rowid") or 0),
                    "latest_rowid": self.provider.latest_rowid(chat),
                }
                for chat in self.chats
            ],
        }
