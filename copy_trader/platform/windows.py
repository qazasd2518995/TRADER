# copy_trader/platform/windows.py
"""
Windows platform implementation of the platform abstraction layer.
Extracts all pywin32 / ctypes code from signal_capture/screen_capture.py.
"""
import ctypes
import glob
import logging
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

from PIL import Image

from .base import (
    KeyboardControlBase,
    PlatformConfigBase,
    ScreenCaptureBase,
    WindowInfo,
)

logger = logging.getLogger(__name__)

try:
    import win32gui
    import win32ui
    import win32con
    import win32api
    WIN32_AVAILABLE = True
except ImportError:
    WIN32_AVAILABLE = False
    logger.warning("win32gui not available. Install pywin32: pip install pywin32")

try:
    from PIL import ImageGrab
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    logger.warning("Pillow not available. Install: pip install Pillow")


class WindowsScreenCapture(ScreenCaptureBase):
    """Windows screen capture using win32gui / PrintWindow / GDI."""

    def enumerate_windows(self, title_filter: str = "") -> List[WindowInfo]:
        """List visible windows, optionally filtered by title substring."""
        if not WIN32_AVAILABLE:
            logger.error("win32gui not available")
            return []

        results: List[WindowInfo] = []

        def _callback(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title and (not title_filter or title_filter.lower() in title.lower()):
                    try:
                        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                        bounds = (left, top, right - left, bottom - top)
                    except Exception:
                        bounds = (0, 0, 0, 0)
                    results.append(WindowInfo(
                        window_id=hwnd,
                        title=title,
                        owner_name="",
                        bounds=bounds,
                        is_visible=True,
                        pid=0,
                    ))

        try:
            win32gui.EnumWindows(_callback, None)
        except Exception as e:
            logger.error(f"EnumWindows failed: {e}")

        return results

    def capture_window(self, window_id: int) -> Optional[Image.Image]:
        """
        Capture a window by HWND using PrintWindow.
        Works even if the window is occluded or in the background.
        Uses flag 3 (PW_RENDERFULLCONTENT | PW_CLIENTONLY) with fallback to flag 2.
        Full GDI cleanup is always performed in the finally block.
        """
        if not WIN32_AVAILABLE:
            logger.error("win32gui not available for window capture")
            return None

        hwnd = window_id
        hwnd_dc = None
        mfc_dc = None
        save_dc = None
        bitmap = None

        try:
            # Restore minimized windows — PrintWindow cannot capture minimized windows
            if win32gui.IsIconic(hwnd):
                # SW_SHOWNOACTIVATE (4): restore without stealing focus
                ctypes.windll.user32.ShowWindow(hwnd, 4)
                time.sleep(0.3)
                logger.debug(f"Restored minimized window: {hwnd}")

            # Get window dimensions
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width = right - left
            height = bottom - top

            if width <= 0 or height <= 0:
                logger.error(f"Window {hwnd} has zero dimensions")
                return None

            # Create device context and bitmap
            hwnd_dc = win32gui.GetWindowDC(hwnd)
            mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
            save_dc = mfc_dc.CreateCompatibleDC()

            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
            save_dc.SelectObject(bitmap)

            # Flag 3 = PW_RENDERFULLCONTENT | PW_CLIENTONLY: client area only (no title bar)
            # Flag 2 = PW_RENDERFULLCONTENT: captures hardware-accelerated content including title bar
            result = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 3)
            if not result:
                # Retry with flag=2 (include title bar)
                ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 2)

            # Convert bitmap to PIL Image
            bmp_info = bitmap.GetInfo()
            bmp_str = bitmap.GetBitmapBits(True)
            img = Image.frombuffer(
                'RGB',
                (bmp_info['bmWidth'], bmp_info['bmHeight']),
                bmp_str, 'raw', 'BGRX', 0, 1
            )
            return img

        except Exception as e:
            logger.error(f"PrintWindow capture failed for hwnd={hwnd}: {e}")
            # Fallback: screenshot of the window area (requires window to be visible)
            try:
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                img = ImageGrab.grab(bbox=(left, top, right, bottom))
                return img
            except Exception as e2:
                logger.error(f"Fallback capture also failed for hwnd={hwnd}: {e2}")
                return None

        finally:
            # Always release GDI resources to prevent resource leak
            try:
                if bitmap:
                    win32gui.DeleteObject(bitmap.GetHandle())
            except Exception:
                pass
            try:
                if save_dc:
                    save_dc.DeleteDC()
            except Exception:
                pass
            try:
                if mfc_dc:
                    mfc_dc.DeleteDC()
            except Exception:
                pass
            try:
                if hwnd_dc:
                    win32gui.ReleaseDC(hwnd, hwnd_dc)
            except Exception:
                pass

    def capture_region(self, x: int, y: int, w: int, h: int) -> Optional[Image.Image]:
        """Capture a rectangular screen region using PIL.ImageGrab."""
        if not PIL_AVAILABLE:
            logger.error("Pillow not available for region capture")
            return None
        try:
            bbox = (x, y, x + w, y + h)
            return ImageGrab.grab(bbox=bbox)
        except Exception as e:
            logger.error(f"Region capture failed: {e}")
            return None

    def is_window_visible(self, window_id: int) -> bool:
        """Check if window is on screen (visible and not iconic)."""
        if not WIN32_AVAILABLE:
            return False
        try:
            return bool(win32gui.IsWindowVisible(window_id))
        except Exception:
            return False

    def get_window_rect(self, window_id: int) -> Optional[tuple]:
        """Get window bounds as (x, y, width, height)."""
        if not WIN32_AVAILABLE:
            return None
        try:
            left, top, right, bottom = win32gui.GetWindowRect(window_id)
            return (left, top, right - left, bottom - top)
        except Exception as e:
            logger.error(f"GetWindowRect failed for hwnd={window_id}: {e}")
            return None


class WindowsKeyboardControl(KeyboardControlBase):
    """
    Windows keyboard / window control using ctypes.windll.user32.
    Matches the keybd_event implementation in screen_capture.py exactly.
    """

    VK_CONTROL = 0x11
    VK_END = 0x23
    VK_NEXT = 0x22
    KEYEVENTF_EXTENDEDKEY = 0x01
    KEYEVENTF_KEYUP = 0x02

    def _tap_key(self, vk_code: int, extended: bool = False):
        flags = self.KEYEVENTF_EXTENDEDKEY if extended else 0
        ctypes.windll.user32.keybd_event(vk_code, 0, flags, 0)
        ctypes.windll.user32.keybd_event(vk_code, 0, flags | self.KEYEVENTF_KEYUP, 0)

    def _activate_window_best_effort(self, window_id: int) -> bool:
        """Restore and foreground the target window with a few Win32 fallbacks."""
        try:
            if win32gui.IsIconic(window_id):
                ctypes.windll.user32.ShowWindow(window_id, 9)  # SW_RESTORE
                time.sleep(0.12)
            else:
                ctypes.windll.user32.ShowWindow(window_id, 5)  # SW_SHOW

            ctypes.windll.user32.BringWindowToTop(window_id)
            ctypes.windll.user32.SetActiveWindow(window_id)
            ctypes.windll.user32.SetForegroundWindow(window_id)
            time.sleep(0.08)

            return win32gui.GetForegroundWindow() == window_id
        except Exception as e:
            logger.debug(f"_activate_window_best_effort failed for hwnd={window_id}: {e}")
            return False

    def activate_window(self, window_id: int) -> bool:
        """Bring window to foreground using SetForegroundWindow."""
        try:
            if not WIN32_AVAILABLE:
                return False
            return self._activate_window_best_effort(window_id)
        except Exception as e:
            logger.error(f"SetForegroundWindow failed for hwnd={window_id}: {e}")
            return False

    def send_scroll_to_bottom(self, window_id: int) -> bool:
        """
        Send Ctrl+End to scroll the window content to the bottom.
        Uses brief focus switch + keybd_event because PostMessage does not work
        with CEF/Chromium rendering engines (e.g. LINE).
        Saves and restores the previous foreground window.
        """
        if not WIN32_AVAILABLE:
            logger.error("win32gui not available for keyboard control")
            return False

        try:
            # Save current foreground window
            old_fg = win32gui.GetForegroundWindow()

            # Briefly focus the target window (required for CEF)
            self._activate_window_best_effort(window_id)

            # Ctrl+End: preferred shortcut for jumping to the latest messages
            ctypes.windll.user32.keybd_event(self.VK_CONTROL, 0, 0, 0)
            self._tap_key(self.VK_END, extended=True)
            ctypes.windll.user32.keybd_event(self.VK_CONTROL, 0, self.KEYEVENTF_KEYUP, 0)

            # LINE/CEF sometimes misses Ctrl+End even when the window has focus.
            # Follow with End and PageDown bursts as fallbacks so the visible chat
            # is more likely to land on the newest message area.
            time.sleep(0.06)
            self._tap_key(self.VK_END, extended=True)
            time.sleep(0.04)
            for _ in range(3):
                self._tap_key(self.VK_NEXT, extended=True)
                time.sleep(0.04)

            # Restore previous foreground window
            if old_fg and old_fg != window_id:
                self._activate_window_best_effort(old_fg)

            logger.debug(f"Scrolled window {window_id} to bottom")
            return True

        except Exception as e:
            logger.debug(f"Failed to scroll window {window_id}: {e}")
            return False


class WindowsPlatformConfig(PlatformConfigBase):
    """
    Windows platform path configuration.
    Mirrors the path-detection logic from config.py and screen_capture.py.
    """

    def get_mt5_files_path(self) -> Optional[Path]:
        """
        Auto-detect MT5 MQL5/Files directory on Windows.
        Searches Program Files and AppData\\MetaQuotes\\Terminal first;
        returns the primary default path if nothing is found on disk.
        """
        search_patterns = [
            r"C:\Program Files\MetaTrader 5\MQL5\Files",
            r"C:\Program Files (x86)\MetaTrader 5\MQL5\Files",
            r"C:\Program Files\*MetaTrader*\MQL5\Files",
            r"C:\Program Files (x86)\*MetaTrader*\MQL5\Files",
        ]
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            search_patterns.append(
                os.path.join(appdata, "MetaQuotes", "Terminal", "*", "MQL5", "Files")
            )

        for pattern in search_patterns:
            for match in glob.glob(pattern):
                if os.path.isdir(match):
                    return Path(match)

        # Return the canonical default even if it doesn't exist yet
        return Path(r"C:\Program Files\MetaTrader 5\MQL5\Files")

    def get_app_data_path(self) -> Path:
        """
        Return the application data storage path.
        When running as a PyInstaller frozen exe, use %APPDATA%/黃金跟單系統
        so that data persists across app updates.
        In development, fall back to the project root.
        """
        if getattr(sys, 'frozen', False):
            base = Path(os.environ.get('APPDATA', '~'))
            path = base / '黃金跟單系統'
        else:
            path = Path(__file__).parent.parent.parent

        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_tesseract_path(self) -> Optional[str]:
        """Return the Tesseract executable path, or None if not found."""
        candidate = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        if os.path.isfile(candidate):
            return candidate
        # Not found — caller should handle gracefully
        return None
