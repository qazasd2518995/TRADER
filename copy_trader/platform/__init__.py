# copy_trader/platform/__init__.py
"""
Platform auto-detection.
Imports the correct platform implementation based on sys.platform.
"""
import sys

if sys.platform == "win32":
    from .windows import (
        WindowsScreenCapture as ScreenCapture,
        WindowsKeyboardControl as KeyboardControl,
        WindowsPlatformConfig as PlatformConfig,
        WindowsClipboardControl as ClipboardControl,
    )
elif sys.platform == "darwin":
    from .macos import (
        MacScreenCapture as ScreenCapture,
        MacKeyboardControl as KeyboardControl,
        MacPlatformConfig as PlatformConfig,
        MacClipboardControl as ClipboardControl,
    )
else:
    raise RuntimeError(f"Unsupported platform: {sys.platform}")

from .base import (
    ClipboardControlBase,
    KeyboardControlBase,
    PlatformConfigBase,
    ScreenCaptureBase,
    WindowInfo,
)

__all__ = [
    "ScreenCapture",
    "KeyboardControl",
    "PlatformConfig",
    "ClipboardControl",
    "WindowInfo",
    "ScreenCaptureBase",
    "KeyboardControlBase",
    "PlatformConfigBase",
    "ClipboardControlBase",
]
