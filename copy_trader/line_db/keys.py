"""Secure LINE database-key providers.

Keys are never logged. macOS uses Keychain, Windows uses the current user's
Credential Manager, and ``LINE_DB_KEY`` is an explicit development override.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import getpass
import os
import re
import subprocess
import sys


KEY_RE = re.compile(r"[0-9a-fA-F]{32}")


def validate_database_key(value: str) -> str:
    key = (value or "").strip()
    if not KEY_RE.fullmatch(key):
        raise RuntimeError("LINE DB key must be exactly 32 hexadecimal characters")
    return key.lower()


class DatabaseKeyProvider(ABC):
    @abstractmethod
    def get_key(self) -> str:
        """Return a validated key without logging it."""


class EnvironmentKeyProvider(DatabaseKeyProvider):
    def __init__(self, variable: str = "LINE_DB_KEY"):
        self.variable = variable

    def get_key(self) -> str:
        value = os.environ.get(self.variable, "")
        if not value:
            raise RuntimeError(f"environment variable {self.variable} is not set")
        return validate_database_key(value)


class MacOSKeychainKeyProvider(DatabaseKeyProvider):
    def __init__(self, service: str = "line-db-research", account: str = ""):
        self.service = service.strip() or "line-db-research"
        self.account = account.strip() or getpass.getuser()

    def get_key(self) -> str:
        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-a",
                self.account,
                "-s",
                self.service,
                "-w",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"LINE DB key was not found in macOS Keychain (service={self.service})"
            )
        return validate_database_key(result.stdout)


class WindowsCredentialManagerKeyProvider(DatabaseKeyProvider):
    def __init__(self, target: str = "line-db-research"):
        self.target = target.strip() or "line-db-research"

    def get_key(self) -> str:
        from .windows_credentials import read_generic_credential

        return validate_database_key(read_generic_credential(self.target))


def default_key_provider(service: str = "line-db-research") -> DatabaseKeyProvider:
    if os.environ.get("LINE_DB_KEY"):
        return EnvironmentKeyProvider()
    if sys.platform == "darwin":
        return MacOSKeychainKeyProvider(service=service)
    if sys.platform == "win32":
        return WindowsCredentialManagerKeyProvider(target=service)
    raise RuntimeError(
        "No LINE DB key provider is configured for this platform. "
        "Set LINE_DB_KEY for development."
    )
