"""Configuration parsing and construction for the LINE DB source."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .keys import default_key_provider
from .models import LineChatTarget
from .source import LineDatabaseSource
from .sqlite_provider import SQLiteLineDatabaseProvider


DEFAULT_LINE_CHATS = [
    {
        "name": "gold_signal_1",
        "chat_name": "（乘）黃金報單🈲言群",
        "display_name": "黃金報單🈲言群",
        "trusted_senders": ["乘", "James"],
    }
]


def parse_line_chat_targets(value: str | list[dict[str, Any]] | None) -> list[LineChatTarget]:
    if value is None or value == "":
        raw = DEFAULT_LINE_CHATS
    elif isinstance(value, str):
        try:
            raw = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line_chats must be valid JSON: {exc}") from exc
    else:
        raw = value
    if not isinstance(raw, list):
        raise ValueError("line_chats must be a JSON array")
    targets = [
        LineChatTarget.from_dict(item, index)
        for index, item in enumerate(raw)
        if isinstance(item, dict)
    ]
    if not targets:
        raise ValueError("line_chats must contain at least one chat")
    names = [target.name for target in targets]
    if len(names) != len(set(names)):
        raise ValueError("line_chats contains duplicate internal names")
    return targets


def build_line_database_source(
    *,
    database_path: str = "",
    keychain_service: str = "line-db-research",
    line_chats: str | list[dict[str, Any]] | None = None,
    state_path: str | Path,
) -> LineDatabaseSource:
    provider = SQLiteLineDatabaseProvider(
        database_path or None,
        default_key_provider(keychain_service),
    )
    return LineDatabaseSource(
        provider,
        parse_line_chat_targets(line_chats),
        state_path=state_path,
    )
