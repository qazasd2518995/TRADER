# copy_trader/platform/base.py
"""
Platform abstraction layer — base interfaces.
Each platform (Windows/macOS) implements these ABCs.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List
from PIL import Image


@dataclass
class WindowInfo:
    """Platform-agnostic window information."""
    window_id: int
    title: str
    owner_name: str
    bounds: tuple  # (x, y, width, height)
    is_visible: bool
    pid: int = 0


class ScreenCaptureBase(ABC):
    """Abstract screen capture interface."""

    @abstractmethod
    def enumerate_windows(self, title_filter: str = "") -> List[WindowInfo]:
        """List visible windows, optionally filtered by title substring."""

    @abstractmethod
    def capture_window(self, window_id: int) -> Optional[Image.Image]:
        """Capture a window by its ID. Works even if window is occluded."""

    @abstractmethod
    def capture_region(self, x: int, y: int, w: int, h: int) -> Optional[Image.Image]:
        """Capture a rectangular screen region."""

    @abstractmethod
    def is_window_visible(self, window_id: int) -> bool:
        """Check if window is on screen."""

    @abstractmethod
    def get_window_rect(self, window_id: int) -> Optional[tuple]:
        """Get window bounds as (x, y, width, height)."""


class KeyboardControlBase(ABC):
    """Abstract keyboard/window control interface."""

    @abstractmethod
    def activate_window(self, window_id: int) -> bool:
        """Bring window to foreground."""

    @abstractmethod
    def send_scroll_to_bottom(self, window_id: int) -> bool:
        """Send key combo to scroll window content to bottom."""


class PlatformConfigBase(ABC):
    """Abstract platform path configuration."""

    @abstractmethod
    def get_mt5_files_path(self) -> Optional[Path]:
        """Auto-detect MT5 MQL5/Files directory."""

    @abstractmethod
    def get_app_data_path(self) -> Path:
        """Get application data storage path."""

    @abstractmethod
    def get_tesseract_path(self) -> Optional[str]:
        """Get Tesseract executable path, or None if not found."""


class ClipboardControlBase(ABC):
    """
    Abstract clipboard + chat-copy interface.

    The canonical flow for LINE Desktop copy-based signal capture:
      backup clipboard → activate target window → Ctrl+End → Shift+PgUp×N →
      Ctrl+C → read clipboard → restore clipboard → restore previous foreground.
    """

    @abstractmethod
    def copy_chat_tail(self, window_id: int, screens: int = 2) -> str:
        """
        Copy the bottom ``screens`` pages of chat content from ``window_id``
        and return the clipboard text. Returns "" on failure.
        The implementation MUST back up and restore the user's clipboard
        and restore the previous foreground window before returning.
        """
