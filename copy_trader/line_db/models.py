"""Platform-neutral models for LINE database polling."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Any, Iterable


def _clean_list(values: Iterable[Any] | None) -> tuple[str, ...]:
    return tuple(str(value).strip() for value in (values or ()) if str(value).strip())


def _normalized_name(value: str) -> str:
    return re.sub(r"\s+", "", value or "").casefold()


SUPPORTED_PARSER_PROFILES = frozenset({
    "strict_gold_v1",
    "mid_frequency_v1",
    "yuyu_range_v1",
})


@dataclass(frozen=True)
class LineChatTarget:
    """One configured LINE chat to monitor.

    ``name`` is the stable internal identifier. ``chat_name`` is the exact name
    stored by LINE. ``display_name`` is the public source label sent to the Hub.
    An empty trusted-sender list is fail-closed: rows may still be observed for
    diagnostics, but none can become trade or cancellation events.
    """

    name: str
    chat_name: str
    display_name: str = ""
    trusted_senders: tuple[str, ...] = field(default_factory=tuple)
    chat_id: str = ""
    chat_kind: str = ""
    trusted_sender_ids: tuple[str, ...] = field(default_factory=tuple)
    parser_profile: str = "strict_gold_v1"
    max_trade_age_seconds: int = 300

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("LINE chat target name must not be empty")
        if not self.chat_name.strip():
            raise ValueError("LINE chat_name must not be empty")
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "chat_name", self.chat_name.strip())
        object.__setattr__(
            self,
            "display_name",
            (self.display_name or self.chat_name).strip(),
        )
        object.__setattr__(self, "trusted_senders", _clean_list(self.trusted_senders))
        object.__setattr__(self, "chat_id", self.chat_id.strip())
        object.__setattr__(self, "chat_kind", self.chat_kind.strip().casefold())
        object.__setattr__(self, "trusted_sender_ids", _clean_list(self.trusted_sender_ids))
        object.__setattr__(
            self,
            "parser_profile",
            (self.parser_profile or "strict_gold_v1").strip(),
        )
        object.__setattr__(
            self,
            "max_trade_age_seconds",
            max(0, int(self.max_trade_age_seconds)),
        )
        if self.chat_kind and self.chat_kind not in {"openchat", "group"}:
            raise ValueError("LINE chat_kind must be openchat or group")
        if self.parser_profile not in SUPPORTED_PARSER_PROFILES:
            raise ValueError(f"unsupported LINE parser_profile: {self.parser_profile}")

    @classmethod
    def from_dict(cls, value: dict[str, Any], index: int = 0) -> "LineChatTarget":
        chat_name = str(value.get("chat_name") or value.get("window_name") or "").strip()
        return cls(
            name=str(value.get("name") or f"line_chat_{index + 1}"),
            chat_name=chat_name,
            display_name=str(value.get("display_name") or chat_name),
            trusted_senders=_clean_list(
                value.get("trusted_senders") or value.get("allowed_senders")
            ),
            chat_id=str(value.get("chat_id") or ""),
            chat_kind=str(value.get("chat_kind") or ""),
            trusted_sender_ids=_clean_list(value.get("trusted_sender_ids")),
            parser_profile=str(value.get("parser_profile") or "strict_gold_v1"),
            max_trade_age_seconds=int(value.get("max_trade_age_seconds", 300)),
        )

    def accepts_trade_sender(self, sender_id: str, sender_name: str = "") -> bool:
        # Once the first-run binding has resolved stable LINE IDs, names are
        # display-only. This prevents a renamed or duplicate member from
        # becoming authorized accidentally.
        if self.trusted_sender_ids:
            return bool(sender_id) and sender_id in self.trusted_sender_ids
        if not self.trusted_senders:
            return False
        if not sender_name:
            # Backward-compatible one-argument calls used to pass the name.
            sender_name = sender_id
        sender = _normalized_name(sender_name)
        return any(_normalized_name(candidate) == sender for candidate in self.trusted_senders)

    def accepts_cancel_sender(
        self,
        sender_id: str,
        sender_name: str,
        original_sender_id: str,
        original_sender_name: str,
    ) -> bool:
        if sender_id and original_sender_id and sender_id == original_sender_id:
            return True
        if self.trusted_sender_ids:
            return (
                bool(sender_id)
                and sender_id in self.trusted_sender_ids
                and bool(original_sender_id)
                and original_sender_id in self.trusted_sender_ids
            )
        if _normalized_name(sender_name) == _normalized_name(original_sender_name):
            return True
        return self.accepts_trade_sender("", sender_name) if self.trusted_senders else False


@dataclass(frozen=True)
class ResolvedLineChat:
    target: LineChatTarget
    chat_id: str
    kind: str


@dataclass(frozen=True)
class LineDatabaseMessage:
    """One exact LINE row plus optional quoted-message context."""

    rowid: int
    message_id: str
    chat: ResolvedLineChat
    created_time_ms: int
    sender_id: str
    sender_name: str
    text: str
    content_type: int = 0
    relation_type: int = 0
    related_message_id: str = ""
    related_sender_id: str = ""
    related_sender_name: str = ""
    related_text: str = ""
    database_id: str = ""
    revision: int = 0
    status: int = 0
    message_type: int = 0
    reaction_status: str = ""

    @property
    def timestamp(self) -> datetime:
        return datetime.fromtimestamp(self.created_time_ms / 1000).astimezone()

    @property
    def is_reply(self) -> bool:
        return self.relation_type == 3 and bool(self.related_message_id)


@dataclass(frozen=True)
class LineMessageMetadata:
    """Revision fields for non-trading edit/recall diagnostics."""

    message_id: str
    revision: int
    status: int
    message_type: int
    reaction_status: str
    text_sha256: str
