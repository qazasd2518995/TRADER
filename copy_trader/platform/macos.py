# copy_trader/platform/macos.py
"""
macOS platform implementation using Quartz (CoreGraphics) and AppKit.
Requires: pyobjc-framework-Quartz, pyobjc-framework-Cocoa
System permissions: Screen Recording, Accessibility
"""
import glob
import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, List

from PIL import Image
from .base import ClipboardControlBase, ScreenCaptureBase, KeyboardControlBase, PlatformConfigBase, WindowInfo

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
        NSWorkspace,
    )
    APPKIT_AVAILABLE = True
except ImportError:
    APPKIT_AVAILABLE = False

try:
    from ApplicationServices import (
        AXIsProcessTrusted,
        AXIsProcessTrustedWithOptions,
        kAXTrustedCheckOptionPrompt,
    )
    ACCESSIBILITY_AVAILABLE = True
except ImportError:
    ACCESSIBILITY_AVAILABLE = False


def get_macos_permission_status(prompt: bool = False) -> dict:
    """Check macOS capture/accessibility permissions and optionally prompt."""
    status = {
        "screen_recording": None,
        "accessibility": None,
    }

    if not QUARTZ_AVAILABLE:
        return status

    try:
        status["screen_recording"] = bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception as e:
        logger.debug(f"Unable to preflight screen recording access: {e}")

    if prompt and status["screen_recording"] is False:
        try:
            request_fn = getattr(Quartz, "CGRequestScreenCaptureAccess", None)
            if callable(request_fn):
                status["screen_recording"] = bool(request_fn())
        except Exception as e:
            logger.debug(f"Unable to request screen recording access: {e}")

    if ACCESSIBILITY_AVAILABLE:
        try:
            if prompt:
                status["accessibility"] = bool(
                    AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
                )
            else:
                status["accessibility"] = bool(AXIsProcessTrusted())
        except Exception as e:
            logger.debug(f"Unable to check accessibility access: {e}")

    return status


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

    @staticmethod
    def _list_process_windows_via_jxa(owner_name: str) -> List[dict]:
        """Best-effort fallback: ask System Events for per-window titles and bounds."""
        if not owner_name:
            return []

        script = f"""
const owner = {json.dumps(owner_name)};
const se = Application("System Events");
function safeCall(fn, fallback) {{
  try {{
    return fn();
  }} catch (e) {{
    return fallback;
  }}
}}
const proc = safeCall(() => se.processes.byName(owner), null);
const windows = proc ? safeCall(() => proc.windows(), []) : [];
const out = [];
for (let i = 0; i < windows.length; i += 1) {{
  const win = windows[i];
  const title = safeCall(() => win.name(), "") || "";
  const position = safeCall(() => win.position(), [0, 0]) || [0, 0];
  const size = safeCall(() => win.size(), [0, 0]) || [0, 0];
  out.push({{
    title: title,
    x: Number(position[0] || 0),
    y: Number(position[1] || 0),
    width: Number(size[0] || 0),
    height: Number(size[1] || 0),
  }});
}}
JSON.stringify(out);
"""
        try:
            completed = subprocess.run(
                ["osascript", "-l", "JavaScript", "-e", script],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if completed.returncode != 0:
                return []
            payload = completed.stdout.strip()
            if not payload:
                return []
            data = json.loads(payload)
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.debug(f"JXA window lookup failed for {owner_name}: {e}")
            return []

    @staticmethod
    def _match_title_by_bounds(bounds: tuple, window_meta: List[dict]) -> str:
        if not window_meta:
            return ""

        x, y, width, height = bounds
        best_title = ""
        best_score = None
        for item in window_meta:
            score = (
                abs(int(item.get("x", 0)) - x)
                + abs(int(item.get("y", 0)) - y)
                + abs(int(item.get("width", 0)) - width)
                + abs(int(item.get("height", 0)) - height)
            )
            if best_score is None or score < best_score:
                best_score = score
                best_title = str(item.get("title", "") or "").strip()

        if best_score is not None and best_score <= 80:
            return best_title
        return ""

    def enumerate_windows(self, title_filter: str = "") -> List[WindowInfo]:
        if not QUARTZ_AVAILABLE:
            return []

        window_list = CGWindowListCopyWindowInfo(
            kCGWindowListOptionAll, kCGNullWindowID
        )
        if window_list is None:
            return []

        candidates = []
        owners_needing_fallback = set()
        lowered_filter = title_filter.lower()
        result = []
        for win in window_list:
            owner = str(win.get("kCGWindowOwnerName", "") or "").strip()
            title = str(win.get("kCGWindowName", "") or "").strip()
            layer = win.get("kCGWindowLayer", -1)
            wid = win.get("kCGWindowNumber", 0)
            pid = win.get("kCGWindowOwnerPID", 0)

            # Skip non-standard windows (desktop, menubar, etc.)
            if layer != 0:
                continue

            if not owner and not title:
                continue

            bounds = win.get("kCGWindowBounds", {})
            x = int(bounds.get("X", 0))
            y = int(bounds.get("Y", 0))
            w = int(bounds.get("Width", 0))
            h = int(bounds.get("Height", 0))
            if w <= 0 or h <= 0:
                continue

            if not title or title.lower() == owner.lower():
                if owner:
                    owners_needing_fallback.add(owner)

            candidates.append({
                "window_id": wid,
                "title": title,
                "owner": owner,
                "pid": pid,
                "bounds": (x, y, w, h),
            })

        fallback_titles = {
            owner: self._list_process_windows_via_jxa(owner)
            for owner in owners_needing_fallback
        }

        for item in candidates:
            owner = item["owner"]
            title = item["title"]
            bounds = item["bounds"]
            resolved_title = title or owner

            if not title or title.lower() == owner.lower():
                fallback_title = self._match_title_by_bounds(bounds, fallback_titles.get(owner, []))
                if fallback_title:
                    resolved_title = fallback_title

            haystacks = [resolved_title.lower(), owner.lower()]
            if lowered_filter and not any(lowered_filter in haystack for haystack in haystacks):
                continue

            result.append(WindowInfo(
                window_id=item["window_id"],
                title=resolved_title,
                owner_name=owner,
                bounds=bounds,
                is_visible=True,
                pid=item["pid"],
            ))

        result.sort(key=lambda w: ((w.owner_name or "").lower(), (w.title or "").lower(), w.bounds[1], w.bounds[0]))
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


class MacClipboardControl(ClipboardControlBase):
    """macOS clipboard control for LINE Desktop copy-based signal capture."""

    KEY_A = 0x00
    KEY_C = 0x08
    KEY_END = 0x77
    KEY_PAGE_UP = 0x74

    def copy_chat_tail(self, window_id: int, screens: int = 2) -> str:
        if not self._is_available() or not window_id:
            return ""

        screens = max(1, min(int(screens or 2), 10))
        old_pid = self._frontmost_pid()
        backup = self._backup_clipboard()
        text = ""

        try:
            if not self._clear_clipboard():
                return ""
            if not self._focus_window(window_id):
                return ""
            self._click_chat_area(window_id)

            self._tap_key(self.KEY_END, Quartz.kCGEventFlagMaskCommand)
            time.sleep(0.12)
            for _ in range(screens):
                self._tap_key(self.KEY_PAGE_UP, Quartz.kCGEventFlagMaskShift)
                time.sleep(0.08)
            time.sleep(0.08)
            self._tap_key(self.KEY_C, Quartz.kCGEventFlagMaskCommand)
            text = self._read_clipboard_text(timeout=1.2)
        except Exception as e:
            logger.warning("macOS copy_chat_tail failed for window_id=%s: %s", window_id, e)
        finally:
            self._restore_clipboard(backup)
            self._restore_frontmost_pid(old_pid)

        return text or ""

    def copy_chat_all(self, window_id: int) -> str:
        if not self._is_available() or not window_id:
            return ""

        old_pid = self._frontmost_pid()
        backup = self._backup_clipboard()
        text = ""

        try:
            if not self._clear_clipboard():
                return ""
            if not self._focus_window(window_id):
                return ""
            self._click_chat_area(window_id)

            self._tap_key(self.KEY_END, Quartz.kCGEventFlagMaskCommand)
            time.sleep(0.12)
            self._tap_key(self.KEY_A, Quartz.kCGEventFlagMaskCommand)
            time.sleep(0.12)
            self._tap_key(self.KEY_C, Quartz.kCGEventFlagMaskCommand)
            text = self._read_clipboard_text(timeout=1.5)
        except Exception as e:
            logger.warning("macOS copy_chat_all failed for window_id=%s: %s", window_id, e)
        finally:
            self._restore_clipboard(backup)
            self._restore_frontmost_pid(old_pid)

        return text or ""

    def _is_available(self) -> bool:
        if not QUARTZ_AVAILABLE:
            logger.error("Quartz is not available for macOS clipboard copy")
            return False
        if not APPKIT_AVAILABLE:
            logger.error("AppKit is not available for macOS clipboard copy")
            return False
        return True

    def _window_record(self, window_id: int) -> Optional[dict]:
        if not QUARTZ_AVAILABLE:
            return None
        try:
            window_list = CGWindowListCopyWindowInfo(kCGWindowListOptionAll, kCGNullWindowID)
        except Exception:
            return None
        for item in window_list or []:
            if int(item.get("kCGWindowNumber", 0) or 0) == int(window_id):
                return item
        return None

    def _frontmost_pid(self) -> Optional[int]:
        if not APPKIT_AVAILABLE:
            return None
        try:
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is None:
                return None
            return int(app.processIdentifier())
        except Exception:
            return None

    def _restore_frontmost_pid(self, pid: Optional[int]) -> None:
        if not pid or not APPKIT_AVAILABLE:
            return
        try:
            app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            if app is not None:
                app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
        except Exception:
            pass

    def _focus_window(self, window_id: int) -> bool:
        record = self._window_record(window_id)
        if not record or not APPKIT_AVAILABLE:
            return False

        pid = int(record.get("kCGWindowOwnerPID", 0) or 0)
        if not pid:
            return False

        try:
            app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            if app is None:
                return False
            app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
            time.sleep(0.18)
            return True
        except Exception as e:
            logger.debug("macOS focus_window failed for window_id=%s: %s", window_id, e)
            return False

    def _point(self, x: float, y: float):
        if hasattr(Quartz, "CGPointMake"):
            return Quartz.CGPointMake(float(x), float(y))
        return (float(x), float(y))

    def _post_mouse(self, event_type, x: float, y: float) -> None:
        event = Quartz.CGEventCreateMouseEvent(
            None,
            event_type,
            self._point(x, y),
            Quartz.kCGMouseButtonLeft,
        )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def _cursor_location(self):
        try:
            event = Quartz.CGEventCreate(None)
            return Quartz.CGEventGetLocation(event)
        except Exception:
            return None

    def _click_chat_area(self, window_id: int) -> bool:
        record = self._window_record(window_id)
        if not record:
            return False

        bounds = record.get("kCGWindowBounds", {}) or {}
        x = float(bounds.get("X", 0) or 0)
        y = float(bounds.get("Y", 0) or 0)
        width = float(bounds.get("Width", 0) or 0)
        height = float(bounds.get("Height", 0) or 0)
        if width <= 0 or height <= 0:
            return False

        click_x = x + width * 0.50
        click_y = y + height * 0.65
        old_pos = self._cursor_location()

        try:
            self._post_mouse(Quartz.kCGEventMouseMoved, click_x, click_y)
            time.sleep(0.03)
            self._post_mouse(Quartz.kCGEventLeftMouseDown, click_x, click_y)
            time.sleep(0.03)
            self._post_mouse(Quartz.kCGEventLeftMouseUp, click_x, click_y)
            time.sleep(0.10)
            return True
        except Exception as e:
            logger.debug("macOS click_chat_area failed for window_id=%s: %s", window_id, e)
            return False
        finally:
            if old_pos is not None:
                try:
                    self._post_mouse(Quartz.kCGEventMouseMoved, old_pos.x, old_pos.y)
                except Exception:
                    pass

    def _tap_key(self, key_code: int, flags: int = 0) -> None:
        down = Quartz.CGEventCreateKeyboardEvent(None, key_code, True)
        up = Quartz.CGEventCreateKeyboardEvent(None, key_code, False)
        if flags:
            Quartz.CGEventSetFlags(down, flags)
            Quartz.CGEventSetFlags(up, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        time.sleep(0.035)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)

    def _backup_clipboard(self) -> Optional[str]:
        try:
            completed = subprocess.run(
                ["pbpaste"],
                capture_output=True,
                text=True,
                timeout=1,
                check=False,
            )
            return completed.stdout if completed.returncode == 0 else ""
        except Exception as e:
            logger.debug("macOS clipboard backup failed: %s", e)
            return None

    def _restore_clipboard(self, backup: Optional[str]) -> None:
        if backup is None:
            return

        try:
            subprocess.run(
                ["pbcopy"],
                input=backup,
                text=True,
                timeout=1,
                check=False,
            )
        except Exception as e:
            logger.debug("macOS clipboard restore failed: %s", e)

    def _clear_clipboard(self) -> bool:
        try:
            completed = subprocess.run(
                ["pbcopy"],
                input="",
                text=True,
                timeout=1,
                check=False,
            )
            return completed.returncode == 0
        except Exception as e:
            logger.debug("macOS clipboard clear failed: %s", e)
            return False

    def _read_clipboard_text(self, timeout: float = 1.0) -> str:
        deadline = time.time() + max(0.2, timeout)
        while time.time() < deadline:
            try:
                completed = subprocess.run(
                    ["pbpaste"],
                    capture_output=True,
                    text=True,
                    timeout=1,
                    check=False,
                )
                if completed.returncode == 0 and completed.stdout:
                    return completed.stdout
            except Exception:
                pass
            time.sleep(0.05)
        return ""
