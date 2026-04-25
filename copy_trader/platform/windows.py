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
from typing import Dict, List, Optional

from PIL import Image

from .base import (
    ClipboardControlBase,
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
    import win32clipboard
    WIN32_CLIPBOARD_AVAILABLE = True
except ImportError:
    WIN32_CLIPBOARD_AVAILABLE = False
    logger.warning("win32clipboard not available")

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


# ==================================================================
# Virtual Desktop awareness (Windows 10/11)
# ==================================================================
# 目的：判斷 LINE 視窗是否在使用者當前的虛擬桌面。
# 若 LINE 在別的桌面，抢焦點的 SetForegroundWindow 會把使用者整個拉過去，
# 非常打擾。有了這個判斷，我們就能在 LINE 不在當前桌面時直接跳過複製。
#
# 使用者推薦設定：
#   1. Win+Ctrl+D 建立桌面 2
#   2. 把 LINE Desktop 拖到桌面 2
#   3. 平常在桌面 1 工作 → 完全零打擾
#   4. 偶爾 Win+Ctrl+→ 切去桌面 2 → 複製正常運作
#
# API 來源：shobjidl_core.h 的 IVirtualDesktopManager COM 介面
# CLSID = {aa509086-5ca9-4c25-8f95-589d3c07b48a}
# IID   = {a5cd92ff-29be-454c-8d04-d82879fb3f1b}

_HRESULT = ctypes.c_long
_HWND = ctypes.c_void_p


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_string(cls, s: str) -> "_GUID":
        """Build a GUID from '{xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx}'."""
        g = cls()
        s = s.strip().strip("{}")
        parts = s.split("-")
        g.Data1 = int(parts[0], 16)
        g.Data2 = int(parts[1], 16)
        g.Data3 = int(parts[2], 16)
        d4 = bytes.fromhex(parts[3] + parts[4])
        for i in range(8):
            g.Data4[i] = d4[i]
        return g


class WindowsVirtualDesktop:
    """
    Thin wrapper around IVirtualDesktopManager::IsWindowOnCurrentVirtualDesktop.
    All failures degrade gracefully (returns True = "assume visible") so that
    the rest of the app keeps working on non-Win10+ environments.
    """

    CLSID = "{aa509086-5ca9-4c25-8f95-589d3c07b48a}"
    IID = "{a5cd92ff-29be-454c-8d04-d82879fb3f1b}"

    CLSCTX_ALL = 23
    COINIT_APARTMENTTHREADED = 0x2
    RPC_E_CHANGED_MODE = -2147417850

    def __init__(self):
        self._manager_ptr = ctypes.c_void_p()
        self._is_window_on_current = None  # will hold a callable
        self._initialised = False
        self._init_ok = False
        self._init_error: Optional[str] = None

    def _ensure_inited(self) -> bool:
        if self._initialised:
            return self._init_ok
        self._initialised = True
        try:
            ole32 = ctypes.windll.ole32
            # CoInitializeEx — ignore already-inited / mode-change errors.
            hr = ole32.CoInitializeEx(None, self.COINIT_APARTMENTTHREADED)
            if hr != 0 and hr != 1 and hr != self.RPC_E_CHANGED_MODE:
                # S_OK=0, S_FALSE=1 (already initialised) are fine.
                self._init_error = f"CoInitializeEx hr=0x{hr & 0xFFFFFFFF:08x}"
                # Keep going — CoCreateInstance might still work.

            clsid = _GUID.from_string(self.CLSID)
            iid = _GUID.from_string(self.IID)
            hr = ole32.CoCreateInstance(
                ctypes.byref(clsid), None, self.CLSCTX_ALL,
                ctypes.byref(iid), ctypes.byref(self._manager_ptr),
            )
            if hr != 0 or not self._manager_ptr.value:
                self._init_error = f"CoCreateInstance hr=0x{hr & 0xFFFFFFFF:08x}"
                return False

            # vtable[0..2] are IUnknown; IVirtualDesktopManager adds:
            #   vtable[3] = IsWindowOnCurrentVirtualDesktop(HWND, BOOL*)
            #   vtable[4] = GetWindowDesktopId(HWND, GUID*)
            #   vtable[5] = MoveWindowToDesktop(HWND, REFGUID)
            vtable_ptr = ctypes.cast(self._manager_ptr, ctypes.POINTER(ctypes.c_void_p))[0]
            vtable = ctypes.cast(vtable_ptr, ctypes.POINTER(ctypes.c_void_p))

            proto = ctypes.WINFUNCTYPE(_HRESULT, ctypes.c_void_p, _HWND, ctypes.POINTER(ctypes.c_int))
            self._is_window_on_current = proto(vtable[3])
            self._init_ok = True
            return True
        except Exception as e:
            self._init_error = f"init exception: {e}"
            return False

    def is_window_on_current_desktop(self, hwnd: int) -> bool:
        """
        Returns True if ``hwnd`` is on the active virtual desktop,
        OR if we can't determine (fail-open so the app still works on
        Win7/older or when COM is uncooperative).
        """
        if not hwnd:
            return True
        if not self._ensure_inited():
            return True  # fail-open
        try:
            out = ctypes.c_int(0)
            hr = self._is_window_on_current(self._manager_ptr, hwnd, ctypes.byref(out))
            if hr != 0:
                # Common: hr=0x8002802B (TYPE_E_ELEMENTNOTFOUND) when window is
                # pinned/on no desktop. Treat as visible to avoid false skips.
                return True
            return bool(out.value)
        except Exception as e:
            logger.debug(f"IsWindowOnCurrentVirtualDesktop failed: {e}")
            return True


# Singleton — we only need one manager per process.
_vdm_singleton: Optional[WindowsVirtualDesktop] = None


def get_virtual_desktop_manager() -> WindowsVirtualDesktop:
    global _vdm_singleton
    if _vdm_singleton is None:
        _vdm_singleton = WindowsVirtualDesktop()
    return _vdm_singleton


# --- SendInput structures (more reliable than keybd_event on CEF windows) ---
# Microsoft Learn 文件標示 keybd_event 為 "Superseded"，在 Windows 10/11 對
# Electron/CEF 應用（LINE Desktop、Discord 等）的成功率明顯較差；改用 SendInput
# 的原子 INPUT array 呼叫，不會被使用者輸入或其他 SendInput 插入。

_ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_uint),
        ("time", ctypes.c_uint),
        ("dwExtraInfo", _ULONG_PTR),
    ]

class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_uint),
        ("dwFlags", ctypes.c_uint),
        ("time", ctypes.c_uint),
        ("dwExtraInfo", _ULONG_PTR),
    ]

class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_uint),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort),
    ]

class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]

class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_uint), ("u", _INPUT_UNION)]

_INPUT_KEYBOARD = 1


class WindowsClipboardControl(ClipboardControlBase):
    """
    Windows clipboard control for LINE Desktop copy-based signal capture.

    Flow:
      1. back up the user's clipboard (multi-format)
      2. short-focus the target LINE window (AttachThreadInput-based)
      3. Ctrl+End        → scroll to newest message
      4. Shift+PgUp×N    → select bottom N pages worth of content
      5. Ctrl+C          → copy selection to clipboard
      6. read clipboard text
      7. restore clipboard and previous foreground window

    重要設計：
    * 鍵盤事件用 SendInput（非 keybd_event）— CEF 類應用才穩
    * 每段 modifier 都有 try/finally 保護 KEYUP，例外時不會把 Ctrl/Shift 卡住
    * SetForegroundWindow 透過 AttachThreadInput + Alt tap 繞過 Win10/11 鎖定
    * 剪貼板備份保留 text / image / HTML / file-list 四種常見格式
    """

    VK_MENU = 0x12       # Alt
    VK_CONTROL = 0x11
    VK_SHIFT = 0x10
    VK_END = 0x23
    VK_PRIOR = 0x21      # PageUp

    KEYEVENTF_EXTENDEDKEY = 0x0001
    KEYEVENTF_KEYUP = 0x0002

    CF_TEXT = 1
    CF_UNICODETEXT = 13
    CF_DIB = 8
    CF_HDROP = 15        # file list
    # CF_HTML is registered dynamically; we look up its id if available.

    # ---------------- SendInput ----------------

    def _send_input_keys(self, events: List[tuple]) -> None:
        """
        Send a batch of (vk, key_up, extended) as a single SendInput call.
        Using a single batch makes the sequence atomic — modifier state stays
        consistent even if another app is pumping input concurrently.
        """
        if not events:
            return
        n = len(events)
        arr_t = _INPUT * n
        arr = arr_t()
        for i, (vk, key_up, extended) in enumerate(events):
            flags = 0
            if extended:
                flags |= self.KEYEVENTF_EXTENDEDKEY
            if key_up:
                flags |= self.KEYEVENTF_KEYUP
            arr[i].type = _INPUT_KEYBOARD
            arr[i].ki = _KEYBDINPUT(
                wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0
            )
        sent = ctypes.windll.user32.SendInput(n, ctypes.byref(arr), ctypes.sizeof(_INPUT))
        if sent != n:
            err = ctypes.windll.kernel32.GetLastError()
            logger.debug(f"SendInput returned {sent}/{n}, last_error={err}")

    def _tap(self, vk: int, extended: bool = False):
        self._send_input_keys([(vk, False, extended), (vk, True, extended)])

    # ---------------- Focus acquisition ----------------

    def _force_release_modifiers(self) -> None:
        """Emit KEYUP for Ctrl/Shift/Alt. Safety net in case anything leaked."""
        try:
            self._send_input_keys([
                (self.VK_CONTROL, True, False),
                (self.VK_SHIFT, True, False),
                (self.VK_MENU, True, False),
            ])
        except Exception:
            pass

    def _focus(self, hwnd: int) -> bool:
        """
        Bring ``hwnd`` to the foreground reliably on Win10/11.

        Microsoft's foreground lock rejects SetForegroundWindow from non-foreground
        processes. Standard workarounds (Raymond Chen / PowerToys):
          1. simulate an Alt key-tap so the calling thread is "just" an input source
          2. AttachThreadInput the current thread to the foreground thread's input queue
        Both tricks are used together for maximum reliability.
        """
        try:
            if win32gui.IsIconic(hwnd):
                ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                time.sleep(0.12)
            else:
                ctypes.windll.user32.ShowWindow(hwnd, 5)  # SW_SHOW

            # Alt tap: grants temporary foreground privilege to this thread.
            self._tap(self.VK_MENU)

            # AttachThreadInput trick
            current_tid = ctypes.windll.kernel32.GetCurrentThreadId()
            fg_hwnd = ctypes.windll.user32.GetForegroundWindow()
            fg_tid = ctypes.windll.user32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
            target_tid = ctypes.windll.user32.GetWindowThreadProcessId(hwnd, None)

            attached_fg = False
            attached_tgt = False
            try:
                if fg_tid and fg_tid != current_tid:
                    attached_fg = bool(ctypes.windll.user32.AttachThreadInput(current_tid, fg_tid, True))
                if target_tid and target_tid != current_tid and target_tid != fg_tid:
                    attached_tgt = bool(ctypes.windll.user32.AttachThreadInput(current_tid, target_tid, True))

                ctypes.windll.user32.BringWindowToTop(hwnd)
                ctypes.windll.user32.SetActiveWindow(hwnd)
                ctypes.windll.user32.SetForegroundWindow(hwnd)
            finally:
                if attached_fg:
                    ctypes.windll.user32.AttachThreadInput(current_tid, fg_tid, False)
                if attached_tgt:
                    ctypes.windll.user32.AttachThreadInput(current_tid, target_tid, False)

            time.sleep(0.08)
            return win32gui.GetForegroundWindow() == hwnd
        except Exception as e:
            logger.debug(f"_focus failed for hwnd={hwnd}: {e}")
            return False

    # ---------------- Chat-area seeding click ----------------

    _MOUSEEVENTF_LEFTDOWN = 0x0002
    _MOUSEEVENTF_LEFTUP = 0x0004

    def _click_chat_area(self, hwnd: int) -> bool:
        """
        LINE Desktop (CEF/Chromium) needs a text-selection caret inside the
        message pane before Shift+PgUp can select anything. SetForegroundWindow
        alone leaves keyboard focus on the sidebar/input box, so Shift+PgUp
        becomes a no-op and Ctrl+C returns the old clipboard contents.

        This helper drops a single left-click at 50% width × 65% height —
        safely below the header and above the input bar for any reasonable
        LINE window size. Cursor position is saved and restored so the user
        barely notices.
        """
        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            if right <= left or bottom <= top:
                return False
            cx = (left + right) // 2
            cy = top + int((bottom - top) * 0.65)

            old_pos = win32gui.GetCursorPos()
            try:
                ctypes.windll.user32.SetCursorPos(cx, cy)
                time.sleep(0.03)
                ctypes.windll.user32.mouse_event(self._MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                time.sleep(0.03)
                ctypes.windll.user32.mouse_event(self._MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
                time.sleep(0.05)
            finally:
                try:
                    ctypes.windll.user32.SetCursorPos(*old_pos)
                except Exception:
                    pass
            return True
        except Exception as e:
            logger.debug(f"_click_chat_area failed for hwnd={hwnd}: {e}")
            return False

    # ---------------- Clipboard primitives ----------------

    def _open_clipboard(self, retries: int = 10, delay: float = 0.03) -> bool:
        """OpenClipboard can fail if another app holds it — retry briefly.
        Common offenders on Win10/11: Ditto, ClipboardMaster, RDP, Office."""
        last_err = None
        for _ in range(retries):
            try:
                win32clipboard.OpenClipboard()
                return True
            except Exception as e:
                last_err = e
                time.sleep(delay)
        if last_err:
            logger.debug(f"OpenClipboard failed after retries: {last_err}")
        return False

    _CF_HTML_ID = None

    def _html_format_id(self) -> Optional[int]:
        if self._CF_HTML_ID is not None:
            return self._CF_HTML_ID
        try:
            WindowsClipboardControl._CF_HTML_ID = int(
                ctypes.windll.user32.RegisterClipboardFormatW("HTML Format")
            )
        except Exception:
            WindowsClipboardControl._CF_HTML_ID = 0
        return self._CF_HTML_ID or None

    def _backup_clipboard(self) -> Optional[Dict]:
        """
        Snapshot the clipboard across common formats so we can restore anything
        the user had (text, image, file list, HTML). Returns {} when clipboard
        was empty, None on failure.
        """
        if not WIN32_CLIPBOARD_AVAILABLE:
            return None
        if not self._open_clipboard():
            return None
        snap: Dict = {}
        try:
            # Iterate all formats present
            fmt = 0
            for _ in range(200):  # hard cap — no infinite loop
                try:
                    fmt = win32clipboard.EnumClipboardFormats(fmt)
                except Exception:
                    break
                if not fmt:
                    break
                try:
                    data = win32clipboard.GetClipboardData(fmt)
                except Exception:
                    data = None
                if data is None:
                    continue
                snap[fmt] = data
            return snap
        except Exception as e:
            logger.debug(f"clipboard backup failed: {e}")
            return None
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass

    def _restore_clipboard(self, backup: Optional[Dict]) -> None:
        """Restore backed-up formats. Skips any format we can't re-set cleanly."""
        if backup is None or not WIN32_CLIPBOARD_AVAILABLE:
            return
        if not self._open_clipboard():
            return
        try:
            win32clipboard.EmptyClipboard()
            html_id = self._html_format_id()
            for fmt, data in backup.items():
                try:
                    # Only restore formats we're confident about — mixing raw
                    # handles (e.g. CF_BITMAP, CF_METAFILEPICT) is unsafe because
                    # those handles were freed by the original owner.
                    if fmt in (self.CF_UNICODETEXT, self.CF_TEXT):
                        if isinstance(data, bytes):
                            win32clipboard.SetClipboardData(fmt, data)
                        elif isinstance(data, str):
                            win32clipboard.SetClipboardData(fmt, data)
                    elif fmt == self.CF_DIB and isinstance(data, (bytes, bytearray)):
                        win32clipboard.SetClipboardData(fmt, bytes(data))
                    elif html_id and fmt == html_id and isinstance(data, (bytes, bytearray, str)):
                        payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
                        win32clipboard.SetClipboardData(fmt, payload)
                    # CF_HDROP / CF_BITMAP 我們不重建 — 那些 handle 已經失效。
                except Exception as e:
                    logger.debug(f"restore fmt={fmt} skipped: {e}")
                    continue
        except Exception as e:
            logger.debug(f"clipboard restore failed: {e}")
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass

    def _clear_clipboard(self) -> None:
        if not WIN32_CLIPBOARD_AVAILABLE:
            return
        if not self._open_clipboard():
            return
        try:
            win32clipboard.EmptyClipboard()
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass

    def _read_clipboard_text(self, timeout: float = 1.0) -> str:
        if not WIN32_CLIPBOARD_AVAILABLE:
            return ""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._open_clipboard(retries=2, delay=0.02):
                try:
                    if win32clipboard.IsClipboardFormatAvailable(self.CF_UNICODETEXT):
                        data = win32clipboard.GetClipboardData(self.CF_UNICODETEXT)
                        if isinstance(data, str) and data:
                            # pywin32 偶見尾端混入 NUL（Excel / CEF 來源）
                            if "\x00" in data:
                                data = data.split("\x00", 1)[0]
                            return data
                finally:
                    try:
                        win32clipboard.CloseClipboard()
                    except Exception:
                        pass
            time.sleep(0.04)
        return ""

    # ---------------- Main API ----------------

    def copy_chat_tail(self, window_id: int, screens: int = 2) -> str:
        """
        呼叫者（ClipboardReaderService._should_copy）已負責虛擬桌面判斷；
        本方法不再重複檢查，以免 baseline 一次性複製被誤擋。
        """
        if not WIN32_AVAILABLE or not WIN32_CLIPBOARD_AVAILABLE:
            logger.error("win32 stack not available for clipboard copy")
            return ""
        if not window_id:
            return ""

        screens = max(1, min(int(screens), 10))
        old_fg = None
        try:
            old_fg = win32gui.GetForegroundWindow()
        except Exception:
            old_fg = None

        backup = self._backup_clipboard()
        self._clear_clipboard()
        text = ""

        # Track which modifiers we've pressed so finally can release them.
        ctrl_down = False
        shift_down = False
        try:
            if not self._focus(window_id):
                logger.debug(f"clipboard copy: focus failed for hwnd={window_id}")
                return ""

            # 0. Seed a text-selection caret inside the chat pane. Without this,
            #    LINE CEF routes Shift+PgUp to the sidebar/input box and selects
            #    nothing — Ctrl+C would then return stale clipboard data.
            self._click_chat_area(window_id)

            # 1. Ctrl+End — jump to newest message
            self._send_input_keys([(self.VK_CONTROL, False, False)])
            ctrl_down = True
            self._tap(self.VK_END, extended=True)
            self._send_input_keys([(self.VK_CONTROL, True, False)])
            ctrl_down = False
            time.sleep(0.10)

            # 2. Shift+PageUp × N — select bottom N pages
            self._send_input_keys([(self.VK_SHIFT, False, False)])
            shift_down = True
            for _ in range(screens):
                self._tap(self.VK_PRIOR, extended=True)
                time.sleep(0.06)
            self._send_input_keys([(self.VK_SHIFT, True, False)])
            shift_down = False
            time.sleep(0.08)

            # 3. Ctrl+C — copy. Use one atomic SendInput batch for reliability.
            self._send_input_keys([
                (self.VK_CONTROL, False, False),
                (ord('C'), False, False),
                (ord('C'), True, False),
                (self.VK_CONTROL, True, False),
            ])
            time.sleep(0.10)

            text = self._read_clipboard_text(timeout=0.8)
        except Exception as e:
            logger.warning(f"copy_chat_tail failed for hwnd={window_id}: {e}")
        finally:
            # 1. Always release modifiers that might still be held
            try:
                if ctrl_down:
                    self._send_input_keys([(self.VK_CONTROL, True, False)])
                if shift_down:
                    self._send_input_keys([(self.VK_SHIFT, True, False)])
                # Belt-and-suspenders: force-release all three in case of weird state
                self._force_release_modifiers()
            except Exception:
                pass
            # 2. Restore clipboard so we don't clobber user content
            try:
                self._restore_clipboard(backup)
            except Exception:
                pass
            # 3. Restore previous foreground — use same focus routine for reliability
            if old_fg and old_fg != window_id:
                try:
                    self._focus(old_fg)
                except Exception:
                    pass

        return text or ""

    def copy_chat_all(self, window_id: int) -> str:
        """
        Dedicated central-machine mode: focus LINE, jump to newest message,
        Ctrl+A, Ctrl+C, then restore the user's clipboard and foreground window.

        This intentionally selects the full chat text. It is more intrusive than
        copy_chat_tail(), so it should be used on the always-on signal computer,
        not on an end user's working machine.
        """
        if not WIN32_AVAILABLE or not WIN32_CLIPBOARD_AVAILABLE:
            logger.error("win32 stack not available for clipboard copy")
            return ""
        if not window_id:
            return ""

        old_fg = None
        try:
            old_fg = win32gui.GetForegroundWindow()
        except Exception:
            old_fg = None

        backup = self._backup_clipboard()
        self._clear_clipboard()
        text = ""
        ctrl_down = False

        try:
            if not self._focus(window_id):
                logger.debug(f"clipboard copy all: focus failed for hwnd={window_id}")
                return ""

            # Keep LINE positioned at the latest messages before selecting all.
            self._send_input_keys([(self.VK_CONTROL, False, False)])
            ctrl_down = True
            self._tap(self.VK_END, extended=True)
            self._send_input_keys([(self.VK_CONTROL, True, False)])
            ctrl_down = False
            time.sleep(0.10)

            self._send_input_keys([
                (self.VK_CONTROL, False, False),
                (ord('A'), False, False),
                (ord('A'), True, False),
                (self.VK_CONTROL, True, False),
            ])
            time.sleep(0.08)
            self._send_input_keys([
                (self.VK_CONTROL, False, False),
                (ord('C'), False, False),
                (ord('C'), True, False),
                (self.VK_CONTROL, True, False),
            ])
            time.sleep(0.12)
            text = self._read_clipboard_text(timeout=1.0)
        except Exception as e:
            logger.warning(f"copy_chat_all failed for hwnd={window_id}: {e}")
        finally:
            try:
                if ctrl_down:
                    self._send_input_keys([(self.VK_CONTROL, True, False)])
                self._force_release_modifiers()
            except Exception:
                pass
            try:
                self._restore_clipboard(backup)
            except Exception:
                pass
            if old_fg and old_fg != window_id:
                try:
                    self._focus(old_fg)
                except Exception:
                    pass

        return text or ""
