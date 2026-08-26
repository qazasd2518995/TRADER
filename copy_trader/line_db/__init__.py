"""Structured, read-only LINE database message source."""

from .discovery import DatabaseCandidate, discover_database_candidates
from .keys import (
    DatabaseKeyProvider,
    WindowsCredentialManagerKeyProvider,
    default_key_provider,
    validate_database_key,
)
from .models import LineChatTarget, LineDatabaseMessage, ResolvedLineChat
from .source import LineDatabaseSource
from .sqlite_provider import SQLiteLineDatabaseProvider

__all__ = [
    "DatabaseKeyProvider",
    "DatabaseCandidate",
    "LineChatTarget",
    "LineDatabaseMessage",
    "LineDatabaseSource",
    "ResolvedLineChat",
    "SQLiteLineDatabaseProvider",
    "WindowsCredentialManagerKeyProvider",
    "default_key_provider",
    "discover_database_candidates",
    "validate_database_key",
]
