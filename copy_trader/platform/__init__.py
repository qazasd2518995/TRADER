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
    )
elif sys.platform == "darwin":
    from .macos import (
        MacScreenCapture as ScreenCapture,
        MacKeyboardControl as KeyboardControl,
        MacPlatformConfig as PlatformConfig,
    )
else:
    raise RuntimeError(f"Unsupported platform: {sys.platform}")

from .base import WindowInfo, ScreenCaptureBase, KeyboardControlBase, PlatformConfigBase

__all__ = [
    "ScreenCapture",
    "KeyboardControl",
    "PlatformConfig",
    "WindowInfo",
    "ScreenCaptureBase",
    "KeyboardControlBase",
    "PlatformConfigBase",
]
