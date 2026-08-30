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

    def _inherited_cursor(self, chat: ResolvedLineChat) -> dict | None:
        """在其他 database_id 底下,找同一個聊天室最近一次的游標。

        為什麼需要這個:`database_id` 是 sha256(檔案路徑 + 檔頭 16 bytes),而 LINE
        大約每天會改寫一次 .edb 的檔頭。檔頭一變,同一個檔案就會被算出新的 id,
        於是 `_database_state()` 拿到一份空的狀態、`ensure_baseline()` 把游標直接
        設在「當下最新那一則」—— **上次輪詢之後進來的訊息全部靜默跳過**。
        `database_id` 只在程序啟動時算一次,所以觸發點是「訊號中心重啟」,而守護
        排程每 3 分鐘會拉起當掉的行程,漏單只會越來越頻繁。

        實測 2026-08-30:游標檔裡累積了 5 個 database_id,其中三次換 id 分別漏掉
        中頻 3/6/0 則、高頻 36/21/4 則,裡面至少有 6 筆是真實報單(乘的 Buy 4570、
        yuyu 的 4600-4601 空…)。

        接手舊游標是安全的,有兩層互相獨立的保護:
          1. `line_event_id` / `execution_id` 都只由 chat_id + message_id 算出,
             跟 database_id 無關 —— 重讀已發布過的訊息會產生同一個 id,Hub 的
             冪等會擋掉。
          2. collector 的 `max_trade_age_seconds`(中頻 300 秒 / 高頻 180 秒)
             會把補讀到的舊訊息當成過期拒絕。
        """
        best: tuple[float, dict] | None = None
        for database_id, database in (self._state.get("databases") or {}).items():
            if database_id == self.provider.database_id:
                continue
            state = ((database or {}).get("chats") or {}).get(chat.chat_id)
            if not isinstance(state, dict) or "last_rowid" not in state:
                continue
            stamp = float(state.get("updated_at") or state.get("baselined_at") or 0)
            if best is None or stamp > best[0]:
                best = (stamp, state)
        return best[1] if best else None

    def ensure_baseline(self) -> bool:
        """Create missing cursors. Return True when any baseline was created."""
        changed = False
        with self._lock:
            for chat in self.chats:
                state = self._chat_state(chat)
                if "last_rowid" in state:
                    continue
                latest = self.provider.latest_rowid(chat)
                fresh = {
                    "target_name": chat.target.name,
                    "chat_name": chat.target.chat_name,
                    "last_rowid": latest,
                    "baselined_at": time.time(),
                }

                # 換 database_id 時盡量接手舊游標,不要重新 baseline —— 見
                # _inherited_cursor 的說明。舊游標比 latest 還大代表那真的是
                # 另一個資料庫的 rowid(換帳號/換檔案),那時只能重新 baseline。
                inherited = self._inherited_cursor(chat)
                carried = int((inherited or {}).get("last_rowid") or 0)
                if inherited is not None and 0 < carried <= latest:
                    fresh["last_rowid"] = carried
                    fresh["carried_from_rowid"] = carried
                    if inherited.get("last_message_id"):
                        fresh["last_message_id"] = inherited["last_message_id"]
                    logger.info(
                        "LINE DB 換了 database_id,%r 接手舊游標 rowid=%s(最新 %s,"
                        " 會補讀中間 %s 筆列;過期的會被 collector 拒絕)",
                        chat.target.display_name, carried, latest, latest - carried,
                    )
                else:
                    logger.info(
                        "LINE DB baseline %r: rowid=%s (history will not be replayed)",
                        chat.target.display_name,
                        latest,
                    )
                state.update(fresh)
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
