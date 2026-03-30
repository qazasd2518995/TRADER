# macOS 移植实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Windows 版 OCR 自动跟单黄金 MT5 系统移植到 macOS (Apple Silicon M3)，采用平台抽象层共用业务逻辑。

**Architecture:** 新增 `copy_trader/platform/` 平台抽象层，将 Windows 特定代码（屏幕截图、键盘模拟、路径配置）封装到 `platform/windows.py`，macOS 实现放 `platform/macos.py`。现有模块 `screen_capture.py`、`ocr.py`、`config.py` 改为调用 platform 层。Tauri `lib.rs` 增加 macOS sidecar 路径。

**Tech Stack:** Python 3.14, pyobjc-framework-Quartz, pyobjc-framework-Cocoa, pyobjc-framework-Vision, Tauri 2.0, RapidOCR (ONNX), PyInstaller

---

## File Structure

### New Files
- `copy_trader/platform/__init__.py` — 平台自动侦测，导出 ScreenCapture, KeyboardControl, PlatformConfig
- `copy_trader/platform/base.py` — ABC 抽象接口：ScreenCaptureBase, KeyboardControlBase, PlatformConfigBase
- `copy_trader/platform/windows.py` — pywin32 实现（从 screen_capture.py 提取）
- `copy_trader/platform/macos.py` — macOS Quartz/AppKit 实现
- `sidecar_requirements_macos.txt` — macOS Python 依赖
- `copy-trader-sidecar-macos.spec` — macOS PyInstaller 打包配置
- `build_sidecar_macos.sh` — macOS sidecar 编译脚本
- `build_tauri_macos.sh` — macOS Tauri 完整编译脚本
- `tests/test_platform_macos.py` — macOS 平台层测试

### Modified Files
- `copy_trader/signal_capture/screen_capture.py` — 改为调用 platform 层
- `copy_trader/signal_capture/ocr.py` — WinRT 替换为 macOS Vision Framework
- `copy_trader/config.py` — 路径改为平台自适应
- `src-tauri/src/lib.rs` — 增加 macOS sidecar 路径
- `src-tauri/tauri.conf.json` — 增加 macOS bundle 配置

---

### Task 1: 创建平台抽象层基础接口

**Files:**
- Create: `copy_trader/platform/__init__.py`
- Create: `copy_trader/platform/base.py`

- [ ] **Step 1: 创建 platform 目录和 base.py**

```python
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
```

- [ ] **Step 2: 创建 __init__.py 平台侦测**

```python
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
```

- [ ] **Step 3: Commit**

```bash
git add copy_trader/platform/__init__.py copy_trader/platform/base.py
git commit -m "feat: add platform abstraction layer with base interfaces"
```

---

### Task 2: 创建 Windows 平台实现

**Files:**
- Create: `copy_trader/platform/windows.py`

- [ ] **Step 1: 创建 windows.py，从现有代码提取 Windows 实现**

```python
# copy_trader/platform/windows.py
"""
Windows platform implementation using pywin32.
Extracted from signal_capture/screen_capture.py.
"""
import os
import sys
import glob
import time
import logging
from pathlib import Path
from typing import Optional, List

from PIL import Image
from .base import ScreenCaptureBase, KeyboardControlBase, PlatformConfigBase, WindowInfo

logger = logging.getLogger(__name__)

try:
    import win32gui
    import win32ui
    import win32con
    import win32api
    WIN32_AVAILABLE = True
except ImportError:
    WIN32_AVAILABLE = False
    logger.warning("pywin32 not available")


class WindowsScreenCapture(ScreenCaptureBase):
    """Windows screen capture using Win32 GDI / PrintWindow."""

    def enumerate_windows(self, title_filter: str = "") -> List[WindowInfo]:
        if not WIN32_AVAILABLE:
            return []
        result = []

        def enum_callback(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title and (not title_filter or title_filter.lower() in title.lower()):
                    try:
                        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                        result.append(WindowInfo(
                            window_id=hwnd,
                            title=title,
                            owner_name="",
                            bounds=(left, top, right - left, bottom - top),
                            is_visible=True,
                        ))
                    except Exception:
                        pass

        try:
            win32gui.EnumWindows(enum_callback, None)
        except Exception:
            pass
        return result

    def capture_window(self, window_id: int) -> Optional[Image.Image]:
        if not WIN32_AVAILABLE:
            return None

        import ctypes
        hwnd = window_id
        hwnd_dc = None
        mfc_dc = None
        save_dc = None
        bitmap = None

        try:
            # Restore minimized window
            if win32gui.IsIconic(hwnd):
                ctypes.windll.user32.ShowWindow(hwnd, 4)
                time.sleep(0.3)

            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width = right - left
            height = bottom - top
            if width <= 0 or height <= 0:
                return None

            hwnd_dc = win32gui.GetWindowDC(hwnd)
            mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
            save_dc = mfc_dc.CreateCompatibleDC()

            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
            save_dc.SelectObject(bitmap)

            result = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 3)
            if not result:
                ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 2)

            bmp_info = bitmap.GetInfo()
            bmp_str = bitmap.GetBitmapBits(True)
            img = Image.frombuffer(
                'RGB',
                (bmp_info['bmWidth'], bmp_info['bmHeight']),
                bmp_str, 'raw', 'BGRX', 0, 1
            )
            return img

        except Exception as e:
            logger.error(f"PrintWindow capture failed: {e}")
            # Fallback: visible area screenshot
            try:
                from PIL import ImageGrab
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                return ImageGrab.grab(bbox=(left, top, right, bottom))
            except Exception:
                return None

        finally:
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
        from PIL import ImageGrab
        try:
            return ImageGrab.grab(bbox=(x, y, x + w, y + h))
        except Exception as e:
            logger.error(f"Region capture failed: {e}")
            return None

    def is_window_visible(self, window_id: int) -> bool:
        if not WIN32_AVAILABLE:
            return False
        try:
            return bool(win32gui.IsWindowVisible(window_id))
        except Exception:
            return False

    def get_window_rect(self, window_id: int) -> Optional[tuple]:
        if not WIN32_AVAILABLE:
            return None
        try:
            left, top, right, bottom = win32gui.GetWindowRect(window_id)
            return (left, top, right - left, bottom - top)
        except Exception:
            return None


class WindowsKeyboardControl(KeyboardControlBase):
    """Windows keyboard control using ctypes.windll.user32."""

    def activate_window(self, window_id: int) -> bool:
        if not WIN32_AVAILABLE:
            return False
        try:
            import ctypes
            ctypes.windll.user32.SetForegroundWindow(window_id)
            return True
        except Exception:
            return False

    def send_scroll_to_bottom(self, window_id: int) -> bool:
        if not WIN32_AVAILABLE:
            return False
        try:
            import ctypes
            VK_END = 0x23
            VK_CONTROL = 0x11
            KEYEVENTF_EXTENDEDKEY = 0x01
            KEYEVENTF_KEYUP = 0x02

            old_fg = win32gui.GetForegroundWindow()
            ctypes.windll.user32.SetForegroundWindow(window_id)
            time.sleep(0.05)

            ctypes.windll.user32.keybd_event(VK_CONTROL, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_END, 0, KEYEVENTF_EXTENDEDKEY, 0)
            ctypes.windll.user32.keybd_event(VK_END, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
            ctypes.windll.user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
            time.sleep(0.05)

            if old_fg and old_fg != window_id:
                ctypes.windll.user32.SetForegroundWindow(old_fg)
            return True
        except Exception as e:
            logger.debug(f"Scroll failed: {e}")
            return False


class WindowsPlatformConfig(PlatformConfigBase):
    """Windows path configuration."""

    def get_mt5_files_path(self) -> Optional[Path]:
        search_paths = [
            r"C:\Program Files\MetaTrader 5\MQL5\Files",
            r"C:\Program Files (x86)\MetaTrader 5\MQL5\Files",
            r"C:\Program Files\*MetaTrader*\MQL5\Files",
            r"C:\Program Files (x86)\*MetaTrader*\MQL5\Files",
        ]
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            search_paths.append(os.path.join(appdata, "MetaQuotes", "Terminal", "*", "MQL5", "Files"))

        for pattern in search_paths:
            matches = glob.glob(pattern)
            for match in matches:
                if os.path.isdir(match):
                    return Path(match)
        return Path(r"C:\Program Files\MetaTrader 5\MQL5\Files")

    def get_app_data_path(self) -> Path:
        if getattr(sys, 'frozen', False):
            return Path(os.environ.get('APPDATA', '~')) / '黃金跟單系統'
        return Path(__file__).parent.parent.parent

    def get_tesseract_path(self) -> Optional[str]:
        import shutil
        path = shutil.which("tesseract")
        if path:
            return path
        common_paths = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ]
        username = os.getenv("USERNAME", "")
        if username:
            common_paths.append(rf"C:\Users\{username}\AppData\Local\Programs\Tesseract-OCR\tesseract.exe")
        for p in common_paths:
            if Path(p).exists():
                return p
        return None
```

- [ ] **Step 2: Commit**

```bash
git add copy_trader/platform/windows.py
git commit -m "feat: add Windows platform implementation (extracted from screen_capture.py)"
```

---

### Task 3: 创建 macOS 平台实现

**Files:**
- Create: `copy_trader/platform/macos.py`
- Create: `tests/test_platform_macos.py`

- [ ] **Step 1: 创建 macOS 屏幕截图、键盘控制和路径配置**

```python
# copy_trader/platform/macos.py
"""
macOS platform implementation using Quartz (CoreGraphics) and AppKit.
Requires: pyobjc-framework-Quartz, pyobjc-framework-Cocoa
System permissions: Screen Recording, Accessibility
"""
import logging
import struct
import sys
import glob
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
    )
    QUARTZ_AVAILABLE = True
except ImportError:
    QUARTZ_AVAILABLE = False
    logger.warning("pyobjc-framework-Quartz not available. Install: pip install pyobjc-framework-Quartz")

try:
    from AppKit import (
        NSRunningApplication,
        NSWorkspace,
        NSApplicationActivateIgnoringOtherApps,
    )
    APPKIT_AVAILABLE = True
except ImportError:
    APPKIT_AVAILABLE = False


def _cgimage_to_pil(cg_image) -> Optional[Image.Image]:
    """Convert a Quartz CGImage to a PIL Image."""
    if cg_image is None:
        return None

    width = Quartz.CGImageGetWidth(cg_image)
    height = Quartz.CGImageGetHeight(cg_image)

    # Create a bitmap context to render the CGImage
    color_space = Quartz.CGColorSpaceCreateDeviceRGB()
    bytes_per_row = width * 4
    context = Quartz.CGBitmapContextCreate(
        None, width, height, 8, bytes_per_row,
        color_space,
        Quartz.kCGImageAlphaPremultipliedLast,
    )
    if context is None:
        return None

    Quartz.CGContextDrawImage(context, CGRectMake(0, 0, width, height), cg_image)
    data = Quartz.CGBitmapContextGetData(context)
    if data is None:
        return None

    # Read raw pixel bytes from the context
    buf = (Quartz.c_uint8 * (bytes_per_row * height)).from_address(data.__pointer__)
    raw_bytes = bytes(buf)

    img = Image.frombytes("RGBA", (width, height), raw_bytes, "raw", "RGBA")
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
            layer = win.get("kCGWindowLayer", 0)
            wid = win.get("kCGWindowNumber", 0)
            pid = win.get("kCGWindowOwnerPID", 0)

            # Skip desktop and menu bar windows
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
            # Find the owning app's PID from window info
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
            import time
            from Quartz import (
                CGEventCreateKeyboardEvent,
                CGEventPost,
                CGEventSetFlags,
                kCGHIDEventTap,
                kCGEventFlagMaskCommand,
            )

            # Activate the window first
            self.activate_window(window_id)
            time.sleep(0.05)

            # Cmd+End (End key = keycode 119 on macOS)
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
        # MetaTrader 5 macOS (Wine-based) default path
        base = Path.home() / "Library" / "Application Support"

        search_paths = [
            base / "net.metaquotes.wine.metatrader5" / "drive_c" / "Program Files" / "MetaTrader 5" / "MQL5" / "Files",
        ]

        # Also search for any MetaQuotes Wine prefix
        wine_pattern = str(base / "net.metaquotes.wine.*" / "drive_c" / "Program Files" / "*MetaTrader*" / "MQL5" / "Files")
        for match in glob.glob(wine_pattern):
            search_paths.append(Path(match))

        for p in search_paths:
            if p.is_dir():
                return p

        # Fallback: return the most common path
        return search_paths[0] if search_paths else None

    def get_app_data_path(self) -> Path:
        if getattr(sys, 'frozen', False):
            return Path.home() / "Library" / "Application Support" / "黃金跟單系統"
        return Path(__file__).parent.parent.parent

    def get_tesseract_path(self) -> Optional[str]:
        import shutil
        path = shutil.which("tesseract")
        if path:
            return path
        # Homebrew default
        homebrew_path = Path("/opt/homebrew/bin/tesseract")
        if homebrew_path.exists():
            return str(homebrew_path)
        return None
```

- [ ] **Step 2: 创建 macOS 平台测试**

```python
# tests/test_platform_macos.py
"""
macOS platform layer tests.
Run on macOS only: pytest tests/test_platform_macos.py -v
"""
import sys
import pytest
from pathlib import Path

# Skip entire module on non-macOS
pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")


class TestMacScreenCapture:
    def test_enumerate_windows_returns_list(self):
        from copy_trader.platform.macos import MacScreenCapture
        sc = MacScreenCapture()
        windows = sc.enumerate_windows()
        assert isinstance(windows, list)
        # There should be at least one window on a running macOS system
        assert len(windows) > 0

    def test_enumerate_windows_with_filter(self):
        from copy_trader.platform.macos import MacScreenCapture
        sc = MacScreenCapture()
        # Filter for Finder which is always running
        windows = sc.enumerate_windows("Finder")
        # May or may not have Finder windows open, but should not error
        assert isinstance(windows, list)

    def test_window_info_fields(self):
        from copy_trader.platform.macos import MacScreenCapture
        from copy_trader.platform.base import WindowInfo
        sc = MacScreenCapture()
        windows = sc.enumerate_windows()
        if windows:
            w = windows[0]
            assert isinstance(w, WindowInfo)
            assert isinstance(w.window_id, int)
            assert isinstance(w.title, str)
            assert len(w.bounds) == 4

    def test_capture_region(self):
        from copy_trader.platform.macos import MacScreenCapture
        from PIL import Image
        sc = MacScreenCapture()
        img = sc.capture_region(0, 0, 100, 100)
        # May be None if screen recording permission not granted
        if img is not None:
            assert isinstance(img, Image.Image)
            assert img.size[0] > 0
            assert img.size[1] > 0

    def test_capture_window(self):
        from copy_trader.platform.macos import MacScreenCapture
        from PIL import Image
        sc = MacScreenCapture()
        windows = sc.enumerate_windows()
        if windows:
            img = sc.capture_window(windows[0].window_id)
            if img is not None:
                assert isinstance(img, Image.Image)


class TestMacPlatformConfig:
    def test_mt5_files_path(self):
        from copy_trader.platform.macos import MacPlatformConfig
        config = MacPlatformConfig()
        path = config.get_mt5_files_path()
        # Should return a Path (may or may not exist)
        assert path is None or isinstance(path, Path)

    def test_app_data_path(self):
        from copy_trader.platform.macos import MacPlatformConfig
        config = MacPlatformConfig()
        path = config.get_app_data_path()
        assert isinstance(path, Path)

    def test_tesseract_path(self):
        from copy_trader.platform.macos import MacPlatformConfig
        config = MacPlatformConfig()
        path = config.get_tesseract_path()
        assert path is None or isinstance(path, str)


class TestMacKeyboardControl:
    def test_activate_window_no_crash(self):
        from copy_trader.platform.macos import MacKeyboardControl
        kb = MacKeyboardControl()
        # Should not crash even with invalid window ID
        result = kb.activate_window(999999)
        assert isinstance(result, bool)
```

- [ ] **Step 3: 运行测试**

Run: `cd /Users/justin/trader && python -m pytest tests/test_platform_macos.py -v`
Expected: Tests pass (screen capture tests may skip if no screen recording permission)

- [ ] **Step 4: Commit**

```bash
git add copy_trader/platform/macos.py tests/test_platform_macos.py
git commit -m "feat: add macOS platform implementation with Quartz/AppKit"
```

---

### Task 4: 重构 screen_capture.py 使用平台层

**Files:**
- Modify: `copy_trader/signal_capture/screen_capture.py`

- [ ] **Step 1: 替换 screen_capture.py 的 Windows 直接调用为 platform 层调用**

Replace the Windows-specific imports (lines 17-24) with:

```python
try:
    from copy_trader.platform import ScreenCapture, KeyboardControl, WindowInfo
    PLATFORM_AVAILABLE = True
except ImportError:
    PLATFORM_AVAILABLE = False
    logger.warning("Platform layer not available")
```

Replace `get_window_id_by_name()` function (lines 35-71) with:

```python
def get_window_id_by_name(window_name: str, app_name: str = "LINE") -> Optional[int]:
    """Get window ID by window title."""
    if not PLATFORM_AVAILABLE:
        logger.error("Platform layer not available")
        return None

    try:
        sc = ScreenCapture()
        windows = sc.enumerate_windows(window_name)
        if windows:
            # Prefer shortest title (exact/closest match)
            windows.sort(key=lambda w: len(w.title))
            hwnd = windows[0].window_id
            logger.info(f"Found window: '{windows[0].title}' (ID: {hwnd})")
            return hwnd
        logger.warning(f"Window not found: {window_name}")
        return None
    except Exception as e:
        logger.error(f"Error finding window: {e}")
        return None
```

Replace `list_app_windows()` function (lines 74-96) with:

```python
def list_app_windows(app_name: str = "LINE") -> List[Dict]:
    """List all visible windows (optionally filter by app name in title)."""
    if not PLATFORM_AVAILABLE:
        return []

    try:
        sc = ScreenCapture()
        windows = sc.enumerate_windows(app_name)
        return [
            {'id': w.window_id, 'name': w.title, 'owner': w.owner_name or app_name}
            for w in windows
        ]
    except Exception:
        return []
```

In `ScreenCaptureService.__init__()`, add platform instances (after line 148):

```python
        self._screen_capture = ScreenCapture() if PLATFORM_AVAILABLE else None
        self._keyboard = KeyboardControl() if PLATFORM_AVAILABLE else None
```

Replace `_send_scroll_to_bottom()` method (lines 189-229) with:

```python
    def _send_scroll_to_bottom(self, hwnd: int):
        """Scroll window to bottom to ensure latest messages are visible."""
        self.__init_scroll_times()
        now = time.time()
        if now - self._scroll_times.get(hwnd, 0) < self.SCROLL_INTERVAL:
            return
        self._scroll_times[hwnd] = now

        if self._keyboard:
            try:
                self._keyboard.send_scroll_to_bottom(hwnd)
                logger.debug(f"Scrolled window {hwnd} to bottom")
            except Exception as e:
                logger.debug(f"Failed to scroll window {hwnd}: {e}")
```

Replace `capture_window()` method (lines 231-360) with:

```python
    def capture_window(self, window: CaptureWindow) -> Optional[CapturedFrame]:
        """Capture a window by its handle. Works even if window is in background."""
        hwnd = window.get_window_id()
        if not hwnd:
            logger.error(f"Could not get window handle for '{window.name}'")
            return None

        if not PLATFORM_AVAILABLE:
            logger.error("Platform layer not available for window capture")
            return None

        # Scroll to bottom
        self._send_scroll_to_bottom(hwnd)

        timestamp = time.time()
        filename = f"{window.name}_{int(timestamp * 1000)}.png"
        filepath = str(Path(self.temp_dir) / filename)

        try:
            img = self._screen_capture.capture_window(hwnd)
            if img is None:
                logger.error(f"Window capture returned None for '{window.name}'")
                return None

            # Check for black/empty capture (permission issue on macOS)
            import numpy as np
            arr = np.array(img)
            if arr.mean() < 5:
                logger.warning(f"Capture returned black image for '{window.name}', check screen recording permission")

            img.save(filepath)
            width, height = img.size
            img_hash = self._compute_file_hash(filepath)
            dummy_region = CaptureRegion(x=0, y=0, width=width, height=height, name=window.name)

            return CapturedFrame(
                image_path=filepath,
                timestamp=timestamp,
                region=dummy_region,
                image_hash=img_hash,
                source_name=window.name,
            )
        except Exception as e:
            logger.error(f"Error capturing window: {e}")
            return None
```

Replace `capture_region()` method (lines 156-180) with:

```python
    def capture_region(self, region: CaptureRegion) -> CapturedFrame:
        """Capture a single screen region."""
        if not PLATFORM_AVAILABLE:
            raise RuntimeError("Platform layer not available for screen capture")

        timestamp = time.time()
        filename = f"{region.name}_{int(timestamp * 1000)}.png"
        filepath = str(Path(self.temp_dir) / filename)

        try:
            img = self._screen_capture.capture_region(region.x, region.y, region.width, region.height)
            if img is None:
                raise RuntimeError("Screen capture returned None")
            img.save(filepath)
            img_hash = self._compute_file_hash(filepath)

            return CapturedFrame(
                image_path=filepath,
                timestamp=timestamp,
                region=region,
                image_hash=img_hash,
            )
        except Exception as e:
            logger.error(f"Screen capture failed: {e}")
            raise RuntimeError(f"Screen capture failed: {e}")
```

Remove the old `WIN32_AVAILABLE` import block and the `PIL_AVAILABLE` check (the platform layer handles this).

- [ ] **Step 2: Commit**

```bash
git add copy_trader/signal_capture/screen_capture.py
git commit -m "refactor: screen_capture.py to use platform abstraction layer"
```

---

### Task 5: 重构 config.py 使用平台层

**Files:**
- Modify: `copy_trader/config.py`

- [ ] **Step 1: 修改 _get_data_dir() 和 Config 类的路径逻辑**

Replace `_get_data_dir()` (lines 14-25) with:

```python
def _get_data_dir() -> Path:
    """資料目錄：config.json、signals、logs 都存這裡。

    - PyInstaller 打包環境 → 平台專屬應用資料目錄
    - 開發環境 → 專案根目錄
    """
    try:
        from copy_trader.platform import PlatformConfig
        return PlatformConfig().get_app_data_path()
    except ImportError:
        if getattr(sys, 'frozen', False):
            # Fallback if platform layer not available
            if sys.platform == "darwin":
                return Path.home() / "Library" / "Application Support" / "黃金跟單系統"
            return Path(os.environ.get('APPDATA', '~')) / '黃金跟單系統'
        return Path(__file__).parent.parent
```

Replace `Config.mt5_files_dir` default (line 106) with:

```python
    mt5_files_dir: str = ""
```

Replace `Config.__post_init__()` (lines 116-144) — change the MT5 auto-detect section:

```python
    def __post_init__(self):
        self.groq_api_key = self._GROQ_API_KEY
        if self._GEMINI_API_KEY:
            self.gemini_api_key = self._GEMINI_API_KEY

        # Auto-detect MT5 Files directory
        if not self.mt5_files_dir or not os.path.exists(self.mt5_files_dir):
            self.mt5_files_dir = self._find_mt5_files_dir()

        # Default capture windows
        if self.capture_mode == "window":
            if not self.capture_windows:
                self.capture_windows = [
                    CaptureWindow(
                        window_name="黃金報單🈲言群",
                        app_name="LINE",
                        name="gold_signal_1"
                    ),
                    CaptureWindow(
                        window_name="鄭",
                        app_name="LINE",
                        name="gold_signal_2"
                    ),
                ]
        else:
            if not self.capture_regions:
                self.capture_regions = [
                    CaptureRegion(x=696, y=99, width=375, height=566, name="line_gold_signal")
                ]
```

Replace `Config._find_mt5_files_dir()` (lines 146-164) with:

```python
    def _find_mt5_files_dir(self) -> str:
        """Auto-detect MT5 Files directory using platform layer."""
        try:
            from copy_trader.platform import PlatformConfig
            path = PlatformConfig().get_mt5_files_path()
            if path and path.is_dir():
                return str(path)
        except ImportError:
            pass

        # Fallback: platform-specific hardcoded paths
        if sys.platform == "darwin":
            mac_path = Path.home() / "Library" / "Application Support" / \
                "net.metaquotes.wine.metatrader5" / "drive_c" / "Program Files" / "MetaTrader 5" / "MQL5" / "Files"
            if mac_path.is_dir():
                return str(mac_path)
            return str(mac_path)
        else:
            search_paths = [
                r"C:\Program Files\MetaTrader 5\MQL5\Files",
                r"C:\Program Files (x86)\MetaTrader 5\MQL5\Files",
            ]
            for p in search_paths:
                if os.path.isdir(p):
                    return p
            return r"C:\Program Files\MetaTrader 5\MQL5\Files"
```

- [ ] **Step 2: Commit**

```bash
git add copy_trader/config.py
git commit -m "refactor: config.py to use platform layer for path detection"
```

---

### Task 6: 重构 ocr.py 添加 macOS Vision Framework

**Files:**
- Modify: `copy_trader/signal_capture/ocr.py`

- [ ] **Step 1: 替换 WinRT OCR 为 macOS Vision Framework**

Replace the WinRT import block (lines 29-39) with:

```python
# Try platform-native OCR
NATIVE_OCR_AVAILABLE = False
import sys
if sys.platform == "win32":
    try:
        import asyncio
        from winsdk.windows.media.ocr import OcrEngine
        from winsdk.windows.globalization import Language
        from winsdk.windows.graphics.imaging import BitmapDecoder
        from winsdk.windows.storage import StorageFile, FileAccessMode
        NATIVE_OCR_AVAILABLE = True
    except ImportError:
        pass
elif sys.platform == "darwin":
    try:
        import Vision
        import Quartz
        NATIVE_OCR_AVAILABLE = True
    except ImportError:
        pass
```

In `OCRService.__init__()`, change `winrt` references to `native` (around line 67):

```python
        elif NATIVE_OCR_AVAILABLE:
            self.engine = "native"
            if sys.platform == "darwin":
                logger.info("Using macOS Vision Framework OCR")
            else:
                logger.info("Using Windows native OCR (WinRT)")
```

Update engine selection in `extract_text()` (around line 254):

```python
        elif self.engine == "native":
            if sys.platform == "darwin":
                return self._extract_with_vision(image_path)
            else:
                return self._extract_with_winrt(image_path)
```

Update fallback chains — replace `WINRT_AVAILABLE` with `NATIVE_OCR_AVAILABLE` and route to the correct native method.

Add macOS Vision Framework OCR method (after `_extract_with_winrt`):

```python
    def _extract_with_vision(self, image_path: str) -> str:
        """Extract text using macOS Vision Framework."""
        try:
            import objc
            from Foundation import NSURL, NSArray
            from Quartz import (
                CGImageSourceCreateWithURL,
                CGImageSourceCreateImageAtIndex,
            )
            from Vision import (
                VNRecognizeTextRequest,
                VNImageRequestHandler,
            )

            file_url = NSURL.fileURLWithPath_(str(Path(image_path).resolve()))
            image_source = CGImageSourceCreateWithURL(file_url, None)
            if image_source is None:
                logger.error(f"Failed to load image: {image_path}")
                return ""

            cg_image = CGImageSourceCreateImageAtIndex(image_source, 0, None)
            if cg_image is None:
                return ""

            handler = VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, None)
            request = VNRecognizeTextRequest.alloc().init()
            request.setRecognitionLanguages_(["zh-Hant", "zh-Hans", "en"])
            request.setRecognitionLevel_(1)  # VNRequestTextRecognitionLevelAccurate
            request.setUsesLanguageCorrection_(True)

            success, error = handler.performRequests_error_([request], None)
            if not success or error:
                logger.error(f"Vision OCR error: {error}")
                return ""

            results = request.results()
            if not results:
                return ""

            lines = []
            for observation in results:
                candidate = observation.topCandidates_(1)
                if candidate and len(candidate) > 0:
                    text = candidate[0].string()
                    confidence = candidate[0].confidence()
                    if confidence >= 0.3:
                        lines.append(text)

            return " ".join(lines).strip()

        except Exception as e:
            logger.error(f"Vision Framework OCR error: {e}")
            if TESSERACT_AVAILABLE and self._tesseract_ready:
                logger.info("Falling back to Tesseract")
                return self._extract_with_tesseract(image_path)
            return ""
```

Update `_setup_tesseract()` to be platform-aware (lines 141-172):

```python
    def _setup_tesseract(self):
        """Setup Tesseract path."""
        import shutil
        tesseract_path = shutil.which("tesseract")
        if tesseract_path:
            pytesseract.pytesseract.tesseract_cmd = tesseract_path
            return True

        try:
            from copy_trader.platform import PlatformConfig
            path = PlatformConfig().get_tesseract_path()
            if path:
                pytesseract.pytesseract.tesseract_cmd = path
                logger.info(f"Tesseract found at: {path}")
                return True
        except ImportError:
            pass

        logger.warning("Tesseract not found.")
        return False
```

Update the error message in `__init__` (around line 83) to remove Windows-specific text:

```python
                "  3. pip install pyobjc-framework-Vision  (macOS native OCR)\n"
                "     or pip install winsdk  (Windows native OCR)\n"
```

- [ ] **Step 2: Commit**

```bash
git add copy_trader/signal_capture/ocr.py
git commit -m "refactor: ocr.py to support macOS Vision Framework alongside WinRT"
```

---

### Task 7: 修改 Tauri lib.rs 支持 macOS sidecar

**Files:**
- Modify: `src-tauri/src/lib.rs`

- [ ] **Step 1: 修改 spawn_sidecar() 中的 sidecar 路径**

Replace lines 109-115 in `spawn_sidecar()`:

```rust
    // Platform-specific sidecar binary name
    let sidecar_path = if cfg!(target_os = "macos") {
        let path = resource_dir.join("binaries/copy-trader-sidecar-aarch64-apple-darwin");
        if path.exists() {
            path
        } else {
            resource_dir.join("binaries/copy-trader-sidecar")
        }
    } else {
        let path = resource_dir.join("binaries/copy-trader-sidecar-x86_64-pc-windows-msvc.exe");
        if path.exists() {
            path
        } else {
            resource_dir.join("binaries/copy-trader-sidecar.exe")
        }
    };
```

- [ ] **Step 2: Commit**

```bash
git add src-tauri/src/lib.rs
git commit -m "feat: lib.rs supports macOS sidecar binary path"
```

---

### Task 8: 修改 tauri.conf.json 支持 macOS bundle

**Files:**
- Modify: `src-tauri/tauri.conf.json`

- [ ] **Step 1: 添加 macOS bundle 配置，保留 Windows 配置**

Replace the `bundle` section (lines 31-49):

```json
  "bundle": {
    "active": true,
    "targets": "all",
    "icon": [
      "icons/32x32.png",
      "icons/128x128.png",
      "icons/128x128@2x.png",
      "icons/icon.ico",
      "icons/icon.icns"
    ],
    "resources": [
      "binaries/*",
      "../mt5_ea/*"
    ],
    "macOS": {
      "minimumSystemVersion": "10.15"
    },
    "windows": {
      "nsis": {
        "displayLanguageSelector": false
      }
    }
  },
```

- [ ] **Step 2: 生成 macOS icon.icns**

Run:
```bash
cd /Users/justin/trader/src-tauri
# Generate icns from existing png if not present
if [ ! -f icons/icon.icns ]; then
    mkdir -p /tmp/icon.iconset
    cp icons/32x32.png /tmp/icon.iconset/icon_32x32.png
    cp icons/128x128.png /tmp/icon.iconset/icon_128x128.png
    cp "icons/128x128@2x.png" "/tmp/icon.iconset/icon_128x128@2x.png"
    # Create larger sizes from 128x128@2x
    sips -z 256 256 "icons/128x128@2x.png" --out /tmp/icon.iconset/icon_256x256.png 2>/dev/null
    sips -z 512 512 "icons/128x128@2x.png" --out /tmp/icon.iconset/icon_512x512.png 2>/dev/null
    iconutil -c icns /tmp/icon.iconset -o icons/icon.icns 2>/dev/null || true
    rm -rf /tmp/icon.iconset
fi
```

- [ ] **Step 3: Commit**

```bash
git add src-tauri/tauri.conf.json src-tauri/icons/icon.icns
git commit -m "feat: add macOS bundle config and icon to tauri.conf.json"
```

---

### Task 9: 创建 macOS 依赖文件和 build 脚本

**Files:**
- Create: `sidecar_requirements_macos.txt`
- Create: `copy-trader-sidecar-macos.spec`
- Create: `build_sidecar_macos.sh`
- Create: `build_tauri_macos.sh`

- [ ] **Step 1: 创建 macOS Python 依赖文件**

```
# sidecar_requirements_macos.txt
Pillow>=10.0
rapidocr>=3.7.0
onnxruntime>=1.17.0
pyobjc-framework-Quartz>=10.0
pyobjc-framework-Cocoa>=10.0
pyobjc-framework-Vision>=10.0
groq>=0.5.0
anthropic>=0.25.0
imagehash>=4.3.0
pyinstaller>=6.0
```

- [ ] **Step 2: 创建 macOS PyInstaller spec**

```python
# copy-trader-sidecar-macos.spec
# -*- mode: python ; coding: utf-8 -*-
"""
黃金跟單系統 — macOS Sidecar PyInstaller 打包配置
執行方式: pyinstaller --noconfirm copy-trader-sidecar-macos.spec
"""
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# RapidOCR model files
rapid_datas = []
rapid_hidden = []
try:
    rapid_datas = collect_data_files('rapidocr', include_py_files=False)
    rapid_hidden = [
        'rapidocr',
        'rapidocr.main',
        'rapidocr.cal_rec_boxes',
        'rapidocr.cal_rec_boxes.main',
        'rapidocr.ch_ppocr_cls',
        'rapidocr.ch_ppocr_cls.main',
        'rapidocr.ch_ppocr_cls.utils',
        'rapidocr.ch_ppocr_det',
        'rapidocr.ch_ppocr_det.main',
        'rapidocr.ch_ppocr_det.utils',
        'rapidocr.ch_ppocr_rec',
        'rapidocr.ch_ppocr_rec.main',
        'rapidocr.ch_ppocr_rec.typings',
        'rapidocr.ch_ppocr_rec.utils',
        'rapidocr.inference_engine',
        'rapidocr.inference_engine.base',
        'rapidocr.inference_engine.onnxruntime',
        'rapidocr.inference_engine.onnxruntime.main',
        'rapidocr.inference_engine.onnxruntime.provider_config',
        'rapidocr.utils',
        'rapidocr.utils.download_file',
        'rapidocr.utils.load_image',
        'rapidocr.utils.log',
        'rapidocr.utils.output',
        'rapidocr.utils.parse_parameters',
        'rapidocr.utils.process_img',
        'rapidocr.utils.to_json',
        'rapidocr.utils.to_markdown',
        'rapidocr.utils.typings',
        'rapidocr.utils.utils',
        'rapidocr.utils.vis_res',
    ]
except Exception:
    print("Warning: RapidOCR not found, skipping...")

a = Analysis(
    ['copy_trader/sidecar_main.py'],
    pathex=['copy_trader'],
    binaries=[],
    datas=[
        ('mt5_ea/MT5_File_Bridge_Enhanced.mq5', 'mt5_ea'),
    ] + rapid_datas,
    hiddenimports=[
        # macOS platform — Quartz, AppKit, Vision
        'objc', 'Quartz', 'AppKit', 'Vision', 'Foundation',
        'CoreFoundation',
        # PIL — image processing
        'PIL', 'PIL.Image',
        'imagehash',
        # OCR
        'rapidocr', 'onnxruntime',
        # LLM parsers (groq + dependencies)
        'groq', 'httpx', 'httpx._transports', 'httpx._transports.default',
        'httpcore', 'httpcore._async', 'httpcore._sync',
        'anyio', 'anyio._backends', 'anyio._backends._asyncio',
        'sniffio', 'distro', 'h11',
        # Auth
        'boto3', 'botocore', 'bcrypt', 'auth_handler',
        # copy_trader modules
        'config', 'app', 'mt5_reader',
        'platform', 'platform.base', 'platform.macos',
        'signal_capture', 'signal_capture.screen_capture', 'signal_capture.ocr',
        'signal_capture.bubble_detector',
        'signal_parser', 'signal_parser.keyword_filter', 'signal_parser.groq_parser',
        'signal_parser.groq_vision_parser', 'signal_parser.gemini_vision_parser',
        'google.genai', 'google.genai.types',
        'signal_parser.parser', 'signal_parser.prompts', 'signal_parser.regex_parser',
        'trade_manager', 'trade_manager.manager',
    ] + rapid_hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # No GUI needed
        'PySide6', 'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets',
        'qasync',
        'tkinter', 'unittest', 'xmlrpc', 'pydoc',
        'matplotlib', 'scipy', 'jupyter', 'notebook', 'IPython', 'pytest',
        # Windows-only
        'win32gui', 'win32ui', 'win32con', 'win32api', 'winsdk',
        'paddleocr', 'paddle', 'paddlepaddle', 'rapidocr_onnxruntime',
        'pywt', 'PyWavelets',
    ],
    cipher=block_cipher,
)

# Remove unnecessary large binaries
_exclude_bins = [
    'qt6quick', 'qt6qml', 'qt6pdf', 'qt6opengl',
    'opencv_videoio_ffmpeg',
    '_avif',
]
a.binaries = [b for b in a.binaries if not any(x in b[0].lower() for x in _exclude_bins)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='copy-trader-sidecar',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX not recommended on macOS arm64
    console=True,
    onefile=True,
)
```

- [ ] **Step 3: 创建 macOS build 脚本**

```bash
#!/bin/bash
# build_sidecar_macos.sh — Build the Python sidecar for macOS
set -e

echo "=== Installing macOS Python dependencies ==="
pip install -r sidecar_requirements_macos.txt

echo "=== Building sidecar with PyInstaller ==="
pyinstaller --noconfirm copy-trader-sidecar-macos.spec

echo "=== Copying sidecar to Tauri binaries ==="
mkdir -p src-tauri/binaries
cp dist/copy-trader-sidecar src-tauri/binaries/copy-trader-sidecar-aarch64-apple-darwin

echo "=== Done! Sidecar built at src-tauri/binaries/copy-trader-sidecar-aarch64-apple-darwin ==="
```

```bash
#!/bin/bash
# build_tauri_macos.sh — Full macOS build (sidecar + Tauri)
set -e

echo "=== Step 1: Build Python sidecar ==="
bash build_sidecar_macos.sh

echo "=== Step 2: Install frontend dependencies ==="
npm install

echo "=== Step 3: Build Tauri app ==="
npm run tauri build

echo "=== Build complete! ==="
echo "Output: src-tauri/target/release/bundle/dmg/"
```

- [ ] **Step 4: 设置脚本可执行权限**

Run:
```bash
chmod +x build_sidecar_macos.sh build_tauri_macos.sh
```

- [ ] **Step 5: Commit**

```bash
git add sidecar_requirements_macos.txt copy-trader-sidecar-macos.spec build_sidecar_macos.sh build_tauri_macos.sh
git commit -m "feat: add macOS build scripts and PyInstaller spec"
```

---

### Task 10: 验证 macOS 依赖安装和基础功能

**Files:** None (verification only)

- [ ] **Step 1: 安装 macOS Python 依赖**

Run:
```bash
cd /Users/justin/trader
pip install pyobjc-framework-Quartz pyobjc-framework-Cocoa pyobjc-framework-Vision Pillow imagehash
```
Expected: All packages install successfully on arm64

- [ ] **Step 2: 验证 pyobjc 可以导入**

Run:
```bash
python3 -c "
import Quartz
from Quartz import CGWindowListCopyWindowInfo, kCGWindowListOptionOnScreenOnly, kCGNullWindowID
windows = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID)
print(f'Found {len(windows)} windows')
for w in windows[:5]:
    print(f'  {w.get(\"kCGWindowOwnerName\", \"?\")} - {w.get(\"kCGWindowName\", \"\")}')
print('Quartz OK')
"
```
Expected: Lists visible windows, prints "Quartz OK"

- [ ] **Step 3: 验证 Vision Framework**

Run:
```bash
python3 -c "
from Vision import VNRecognizeTextRequest
req = VNRecognizeTextRequest.alloc().init()
langs = req.recognitionLanguages()
print(f'Vision Framework OK, languages: {langs}')
"
```
Expected: Prints available languages

- [ ] **Step 4: 验证 RapidOCR 在 arm64 上可用**

Run:
```bash
pip install rapidocr onnxruntime
python3 -c "
from rapidocr import RapidOCR
ocr = RapidOCR()
print('RapidOCR + ONNX Runtime OK on arm64')
"
```
Expected: Prints success message

- [ ] **Step 5: 验证平台层能找到 LINE 窗口**

Run (需要 LINE 在运行):
```bash
python3 -c "
import sys
sys.path.insert(0, '.')
from copy_trader.platform.macos import MacScreenCapture
sc = MacScreenCapture()
windows = sc.enumerate_windows('LINE')
print(f'LINE windows found: {len(windows)}')
for w in windows:
    print(f'  ID={w.window_id} title={w.title} owner={w.owner_name}')
"
```
Expected: Lists LINE windows if LINE is running

- [ ] **Step 6: 验证 MT5 JSON 文件可读**

Run:
```bash
python3 -c "
import json
from pathlib import Path
mt5_path = Path.home() / 'Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/MQL5/Files'
price = json.loads((mt5_path / 'XAUUSD_price.json').read_text())
print(f'MT5 price: {price}')
account = json.loads((mt5_path / 'account_info.json').read_text())
print(f'Account balance: {account.get(\"balance\", \"?\")}')
print('MT5 file bridge OK')
"
```
Expected: Prints current gold price and account info

- [ ] **Step 7: Commit verification results (no code changes)**

This is a verification-only task. No commit needed unless tests revealed issues that were fixed.

---

### Task 11: 端到端集成测试

**Files:** None (testing only)

- [ ] **Step 1: 测试完整 OCR pipeline（截图 → OCR → 文字）**

Run (需要 LINE 在运行且有屏幕录制权限):
```bash
python3 -c "
import sys, tempfile
sys.path.insert(0, '.')
from copy_trader.platform.macos import MacScreenCapture
from copy_trader.signal_capture.ocr import OCRService

sc = MacScreenCapture()
windows = sc.enumerate_windows('LINE')
if not windows:
    print('ERROR: No LINE windows found. Start LINE first.')
    sys.exit(1)

wid = windows[0].window_id
print(f'Capturing window: {windows[0].title} (ID={wid})')

img = sc.capture_window(wid)
if img is None:
    print('ERROR: Capture returned None. Grant screen recording permission.')
    sys.exit(1)

print(f'Captured image: {img.size}')

# Save and OCR
with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
    img.save(f.name)
    print(f'Saved to: {f.name}')

    ocr = OCRService()
    print(f'OCR engine: {ocr.engine}')
    text = ocr.extract_text(f.name)
    print(f'OCR result ({len(text)} chars):')
    print(text[:500])
"
```
Expected: Captures LINE window, runs OCR, outputs recognized text

- [ ] **Step 2: 测试 config 自动侦测 MT5 路径**

Run:
```bash
python3 -c "
import sys
sys.path.insert(0, '.')
from copy_trader.config import Config
c = Config()
print(f'MT5 dir: {c.mt5_files_dir}')
import os
print(f'Exists: {os.path.isdir(c.mt5_files_dir)}')
"
```
Expected: Prints macOS MT5 path, exists=True
