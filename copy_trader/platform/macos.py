# copy_trader/platform/macos.py
"""
macOS platform implementation using Quartz (CoreGraphics) and AppKit.
Requires: pyobjc-framework-Quartz, pyobjc-framework-Cocoa
System permissions: Screen Recording, Accessibility
"""
import glob
import logging
import sys
import time
from pathlib import Path
from typing import Optional, List

from PIL import Image
from .base import ScreenCaptureBase, KeyboardControlBase, PlatformConfigBase, WindowInfo

logger = logging.getLogger(__name__)

try:
    import Quartz
    from Quartz import (
        CGWindowListCopyWindowInfo,
        CGWindowListCreateImage,
        CGRectNull,
        CGRectMake,
        kCGWindowListOptionOnScreenOnly,
        kCGWindowListOptionAll,
        kCGWindowListOptionIncludingWindow,
        kCGWindowImageDefault,
        kCGWindowImageBoundsIgnoreFraming,
        kCGNullWindowID,
        CGImageGetWidth,
        CGImageGetHeight,
        CGImageGetBytesPerRow,
        CGImageGetDataProvider,
        CGDataProviderCopyData,
    )
    QUARTZ_AVAILABLE = True
except ImportError:
    QUARTZ_AVAILABLE = False
    logger.warning("pyobjc-framework-Quartz not available. Install: pip install pyobjc-framework-Quartz")

try:
    from AppKit import (
        NSRunningApplication,
        NSApplicationActivateIgnoringOtherApps,
    )
    APPKIT_AVAILABLE = True
except ImportError:
    APPKIT_AVAILABLE = False


def _cgimage_to_pil(cg_image) -> Optional[Image.Image]:
    """Convert a Quartz CGImage to a PIL Image.

    Uses CGDataProvider to extract raw pixel data, handles row padding
    (bytes_per_row may be larger than width * 4 due to memory alignment).
    """
    if cg_image is None:
        return None

    width = CGImageGetWidth(cg_image)
    height = CGImageGetHeight(cg_image)
    if width <= 0 or height <= 0:
        return None

    bytes_per_row = CGImageGetBytesPerRow(cg_image)
    provider = CGImageGetDataProvider(cg_image)
    data = CGDataProviderCopyData(provider)
    if data is None:
        return None

    raw = bytes(data)

    # Handle row padding: macOS may pad rows for memory alignment
    expected_bpr = width * 4
    if bytes_per_row != expected_bpr:
        rows = []
        for y in range(height):
            offset = y * bytes_per_row
            rows.append(raw[offset:offset + expected_bpr])
        raw = b''.join(rows)

    # macOS CGImage uses BGRA pixel format
    img = Image.frombytes("RGBA", (width, height), raw, "raw", "BGRA")
    return img.convert("RGB")


class MacScreenCapture(ScreenCaptureBase):
    """macOS screen capture using Quartz CGWindowList APIs."""

    def enumerate_windows(self, title_filter: str = "") -> List[WindowInfo]:
        if not QUARTZ_AVAILABLE:
            return []

        window_list = CGWindowListCopyWindowInfo(
            kCGWindowListOptionOnScreenOnly, kCGNullWindowID
        )
        if window_list is None:
            return []

        result = []
        for win in window_list:
            owner = win.get("kCGWindowOwnerName", "")
            title = win.get("kCGWindowName", "")
            layer = win.get("kCGWindowLayer", -1)
            wid = win.get("kCGWindowNumber", 0)
            pid = win.get("kCGWindowOwnerPID", 0)

            # Skip non-standard windows (desktop, menubar, etc.)
            if layer != 0:
                continue

            display_title = title or owner
            if not display_title:
                continue

            if title_filter and title_filter.lower() not in display_title.lower():
                continue

            bounds = win.get("kCGWindowBounds", {})
            x = int(bounds.get("X", 0))
            y = int(bounds.get("Y", 0))
            w = int(bounds.get("Width", 0))
            h = int(bounds.get("Height", 0))

            result.append(WindowInfo(
                window_id=wid,
                title=display_title,
                owner_name=owner,
                bounds=(x, y, w, h),
                is_visible=True,
                pid=pid,
            ))

        return result

    def capture_window(self, window_id: int) -> Optional[Image.Image]:
        if not QUARTZ_AVAILABLE:
            return None

        try:
            cg_image = CGWindowListCreateImage(
                CGRectNull,
                kCGWindowListOptionIncludingWindow,
                window_id,
                kCGWindowImageBoundsIgnoreFraming,
            )
            return _cgimage_to_pil(cg_image)
        except Exception as e:
            logger.error(f"macOS window capture failed: {e}")
            return None

    def capture_region(self, x: int, y: int, w: int, h: int) -> Optional[Image.Image]:
        if not QUARTZ_AVAILABLE:
            return None

        try:
            rect = CGRectMake(x, y, w, h)
            cg_image = CGWindowListCreateImage(
                rect,
                kCGWindowListOptionOnScreenOnly,
                kCGNullWindowID,
                kCGWindowImageDefault,
            )
            return _cgimage_to_pil(cg_image)
        except Exception as e:
            logger.error(f"macOS region capture failed: {e}")
            return None

    def is_window_visible(self, window_id: int) -> bool:
        if not QUARTZ_AVAILABLE:
            return False
        window_list = CGWindowListCopyWindowInfo(
            kCGWindowListOptionOnScreenOnly, kCGNullWindowID
        )
        if window_list is None:
            return False
        return any(w.get("kCGWindowNumber") == window_id for w in window_list)

    def get_window_rect(self, window_id: int) -> Optional[tuple]:
        if not QUARTZ_AVAILABLE:
            return None
        window_list = CGWindowListCopyWindowInfo(
            kCGWindowListOptionAll, kCGNullWindowID
        )
        if window_list is None:
            return None
        for w in window_list:
            if w.get("kCGWindowNumber") == window_id:
                bounds = w.get("kCGWindowBounds", {})
                return (
                    int(bounds.get("X", 0)),
                    int(bounds.get("Y", 0)),
                    int(bounds.get("Width", 0)),
                    int(bounds.get("Height", 0)),
                )
        return None


class MacKeyboardControl(KeyboardControlBase):
    """macOS keyboard control using Quartz CGEvent and AppKit."""

    def activate_window(self, window_id: int) -> bool:
        if not APPKIT_AVAILABLE or not QUARTZ_AVAILABLE:
            return False

        try:
            window_list = CGWindowListCopyWindowInfo(
                kCGWindowListOptionAll, kCGNullWindowID
            )
            pid = None
            for w in (window_list or []):
                if w.get("kCGWindowNumber") == window_id:
                    pid = w.get("kCGWindowOwnerPID")
                    break

            if pid is None:
                return False

            app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            if app is None:
                return False

            app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
            return True
        except Exception as e:
            logger.debug(f"activate_window failed: {e}")
            return False

    def send_scroll_to_bottom(self, window_id: int) -> bool:
        if not QUARTZ_AVAILABLE:
            return False

        try:
            from Quartz import (
                CGEventCreateKeyboardEvent,
                CGEventPost,
                CGEventSetFlags,
                kCGHIDEventTap,
                kCGEventFlagMaskCommand,
            )

            self.activate_window(window_id)
            time.sleep(0.05)

            # Cmd+End: End key = keycode 0x77 (119) on macOS
            KEYCODE_END = 0x77

            event_down = CGEventCreateKeyboardEvent(None, KEYCODE_END, True)
            CGEventSetFlags(event_down, kCGEventFlagMaskCommand)
            CGEventPost(kCGHIDEventTap, event_down)

            event_up = CGEventCreateKeyboardEvent(None, KEYCODE_END, False)
            CGEventPost(kCGHIDEventTap, event_up)

            logger.debug(f"Sent Cmd+End to window {window_id}")
            return True
        except Exception as e:
            logger.debug(f"send_scroll_to_bottom failed: {e}")
            return False


class MacPlatformConfig(PlatformConfigBase):
    """macOS path configuration."""

    def get_mt5_files_path(self) -> Optional[Path]:
        base = Path.home() / "Library" / "Application Support"

        # MetaTrader 5 macOS (Wine-based) default path
        default_path = (
            base / "net.metaquotes.wine.metatrader5" / "drive_c"
            / "Program Files" / "MetaTrader 5" / "MQL5" / "Files"
        )
        if default_path.is_dir():
            return default_path

        # Search for any MetaQuotes Wine prefix
        wine_pattern = str(
            base / "net.metaquotes.wine.*" / "drive_c"
            / "Program Files" / "*MetaTrader*" / "MQL5" / "Files"
        )
        for match in glob.glob(wine_pattern):
            if Path(match).is_dir():
                return Path(match)

        # Return default even if not found (user may configure later)
        return default_path

    def get_app_data_path(self) -> Path:
        if getattr(sys, 'frozen', False):
            return Path.home() / "Library" / "Application Support" / "黃金跟單系統"
        return Path(__file__).parent.parent.parent

    def get_tesseract_path(self) -> Optional[str]:
        import shutil
        path = shutil.which("tesseract")
        if path:
            return path
        # Homebrew default on Apple Silicon
        homebrew_path = Path("/opt/homebrew/bin/tesseract")
        if homebrew_path.exists():
            return str(homebrew_path)
        return None
