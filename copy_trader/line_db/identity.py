"""Stable event/order identities derived from LINE message identity."""

from __future__ import annotations

import hashlib


def line_event_id(chat_id: str, message_id: str, event_type: str, index: int = 0) -> str:
    raw = "\0".join((chat_id, message_id, event_type, str(index)))
    return "line_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def execution_id(chat_id: str, message_id: str, index: int = 0) -> str:
    raw = "\0".join((chat_id, message_id, str(index)))
    # 24 chars; the MT5 comment becomes copy_copy_ln_<16> (29 chars).
    return "copy_ln_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
