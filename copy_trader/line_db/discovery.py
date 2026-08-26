"""Bounded, read-only discovery of LINE Desktop database candidates."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys
from typing import Iterable


@dataclass(frozen=True)
class DatabaseCandidate:
    path: Path
    source: str

    @property
    def preferred_name(self) -> bool:
        return self.path.name.casefold().startswith("qw")

    def public_dict(self) -> dict:
        try:
            stat = self.path.stat()
            size = stat.st_size
            modified = stat.st_mtime
        except OSError:
            size = 0
            modified = 0.0
        return {
            "path": str(self.path),
            "source": self.source,
            "size": size,
            "modified": modified,
            "preferred_name": self.preferred_name,
        }


def _database_files(directory: Path, source: str) -> list[DatabaseCandidate]:
    """Return direct children only; never recursively crawl the user's disk."""
    if not directory.is_dir():
        return []
    found: list[Path] = []
    for pattern in ("qw*.edb", "*.edb"):
        try:
            for path in directory.glob(pattern):
                if path.is_file() and path not in found:
                    found.append(path)
        except OSError:
            continue
    return [DatabaseCandidate(path.resolve(), source) for path in found]


def _deduplicate(candidates: Iterable[DatabaseCandidate]) -> list[DatabaseCandidate]:
    result: list[DatabaseCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(str(candidate.path))
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def discover_macos_databases(home: Path | None = None) -> list[DatabaseCandidate]:
    user_home = home or Path.home()
    directory = (
        user_home
        / "Library/Containers/jp.naver.line.mac/Data/Library/Containers"
        / "jp.naver.line/Data/db"
    )
    return _database_files(directory, "macos_sandbox")


def windows_database_directories(local_app_data: Path) -> list[tuple[Path, str]]:
    """Known Win32, UWP and MSIX per-user data layouts.

    LINE has shipped as both an unpackaged desktop application and a Store app.
    Package layouts changed over time, so discovery checks a small fixed set of
    data roots under packages whose directory name contains ``LINE``.
    """
    directories: list[tuple[Path, str]] = [
        (local_app_data / "LINE/Data/db", "windows_desktop"),
    ]
    packages = local_app_data / "Packages"
    try:
        package_roots = [
            path for path in packages.iterdir()
            if path.is_dir() and "line" in path.name.casefold()
        ] if packages.is_dir() else []
    except OSError:
        package_roots = []

    for package in sorted(package_roots):
        for suffix in (
            "AppData/LINE/Data/db",
            "LocalState/LINE/Data/db",
            "LocalState/Data/db",
            "LocalCache/Local/LINE/Data/db",
            "LocalCache/Roaming/LINE/Data/db",
        ):
            directories.append((package / suffix, f"windows_package:{package.name}"))
    return directories


def discover_windows_databases(
    local_app_data: str | Path | None = None,
) -> list[DatabaseCandidate]:
    root_value = str(local_app_data or os.environ.get("LOCALAPPDATA") or "").strip()
    if not root_value:
        return []
    root = Path(root_value).expanduser()
    candidates: list[DatabaseCandidate] = []
    for directory, source in windows_database_directories(root):
        candidates.extend(_database_files(directory, source))
    return _deduplicate(candidates)


def discover_database_candidates(
    platform: str | None = None,
    *,
    home: Path | None = None,
    local_app_data: str | Path | None = None,
) -> list[DatabaseCandidate]:
    selected = platform or sys.platform
    if selected == "darwin":
        return discover_macos_databases(home)
    if selected == "win32":
        return discover_windows_databases(local_app_data)
    return []


def choose_database_candidate(candidates: Iterable[DatabaseCandidate]) -> Path:
    values = list(candidates)
    preferred = [candidate for candidate in values if candidate.preferred_name]
    selectable = preferred if len(preferred) == 1 else values
    if len(selectable) == 1:
        return selectable[0].path
    if not values:
        raise RuntimeError(
            "LINE database was not found in known locations; set line_database_path explicitly"
        )
    paths = "\n".join(f"- {candidate.path}" for candidate in values)
    raise RuntimeError(
        "multiple LINE database candidates were found; test them and set "
        f"line_database_path explicitly:\n{paths}"
    )
