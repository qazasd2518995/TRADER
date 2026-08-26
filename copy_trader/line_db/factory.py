"""Configuration parsing and construction for the LINE DB source."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from copy_trader.central.membership import HIGH_FREQ, MID_FREQ

from .keys import default_key_provider
from .models import LineChatTarget
from .source import LineDatabaseSource
from .sqlite_provider import SQLiteLineDatabaseProvider


LEGACY_SINGLE_CHAT_DEFAULT = {
    "name": "gold_signal_1",
    "chat_name": "（乘）黃金報單🈲言群",
    "display_name": MID_FREQ,
    "trusted_senders": ["乘", "James"],
}

PRE_RECALL_MID_FREQUENCY_DEFAULT = {
    **LEGACY_SINGLE_CHAT_DEFAULT,
    "parser_profile": "mid_frequency_v1",
    "max_trade_age_seconds": 300,
}

MID_FREQUENCY_DEFAULT = {
    **PRE_RECALL_MID_FREQUENCY_DEFAULT,
    "recall_watch_seconds": 2592000,
}

LEGACY_YUYU_HIGH_FREQUENCY_DEFAULT = {
    "name": "high_freq_yuyu",
    "chat_name": "🈲禁言群🈲 Focus forex 焦點利潤",
    # This is the stable product source key used by membership entitlements,
    # per-source trade settings and the web console's "高頻交易" alias.
    "display_name": HIGH_FREQ,
    "trusted_senders": ["yuyu（yu__o822"],
}

PRE_RECALL_YUYU_HIGH_FREQUENCY_DEFAULT = {
    **LEGACY_YUYU_HIGH_FREQUENCY_DEFAULT,
    "parser_profile": "yuyu_range_v1",
    "max_trade_age_seconds": 180,
}

YUYU_HIGH_FREQUENCY_DEFAULT = {
    **PRE_RECALL_YUYU_HIGH_FREQUENCY_DEFAULT,
    "recall_watch_seconds": 2592000,
}

DEFAULT_LINE_CHATS = [
    MID_FREQUENCY_DEFAULT,
    YUYU_HIGH_FREQUENCY_DEFAULT,
]


def migrate_legacy_default_line_chats(value: Any) -> tuple[Any, bool]:
    """Upgrade only exact historical defaults to the current strict profiles.

    Explicitly customized chat lists are user configuration and must not be
    expanded silently. The returned value preserves the caller's string/list
    representation so launcher settings remain backward compatible.
    """
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return value, False
    legacy_defaults = (
        [LEGACY_SINGLE_CHAT_DEFAULT],
        [LEGACY_SINGLE_CHAT_DEFAULT, LEGACY_YUYU_HIGH_FREQUENCY_DEFAULT],
        [PRE_RECALL_MID_FREQUENCY_DEFAULT, PRE_RECALL_YUYU_HIGH_FREQUENCY_DEFAULT],
    )
    if parsed not in legacy_defaults:
        return value, False

    migrated = [dict(item) for item in DEFAULT_LINE_CHATS]
    if isinstance(value, str):
        return json.dumps(migrated, ensure_ascii=False, indent=2), True
    return migrated, True


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
