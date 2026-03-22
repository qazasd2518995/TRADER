"""
Screen Capture Service for Windows
Captures specified screen regions or windows for OCR processing.
Supports window capture for background windows using win32gui.
"""
import subprocess
import tempfile
import time
import hashlib
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from pathlib import Path
import logging

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
    from PIL import ImageGrab, Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    logger.warning("Pillow not available. Install: pip install Pillow")


def get_window_id_by_name(window_name: str, app_name: str = "LINE") -> Optional[int]:
    """
    Get window handle (HWND) by window title.

    Args:
        window_name: Part of window title to match
        app_name: Application name (used for logging)

    Returns:
        Window handle (HWND) or None if not found
    """
    if not WIN32_AVAILABLE:
        logger.error("win32gui not available. Install pywin32")
        return None

    result = []

    def enum_callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if window_name in title:
                result.append((hwnd, title))

    try:
        win32gui.EnumWindows(enum_callback, None)
        if result:
            # Prefer shortest title (exact/closest match) to avoid matching
            # unrelated windows that happen to contain the search string
            result.sort(key=lambda x: len(x[1]))
            hwnd, title = result[0]
            logger.info(f"Found window: '{title}' (HWND: {hwnd})")
            return hwnd
        logger.warning(f"Window not found: {window_name}")
        return None
    except Exception as e:
        logger.error(f"Error finding window: {e}")
        return None


def list_app_windows(app_name: str = "LINE") -> List[Dict]:
    """List all visible windows (optionally filter by app name in title)."""
    if not WIN32_AVAILABLE:
        return []

    result = []

    def enum_callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title and (not app_name or app_name.lower() in title.lower()):
                result.append({
                    'id': hwnd,
                    'name': title,
                    'owner': app_name
                })

    try:
        win32gui.EnumWindows(enum_callback, None)
    except Exception:
        pass

    return result


@dataclass
class CaptureRegion:
    """Defines a screen region to capture."""
    x: int
    y: int
    width: int
    height: int
    name: str = "default"


@dataclass
class CaptureWindow:
    """Defines a window to capture by handle or name."""
    window_id: Optional[int] = None
    window_name: Optional[str] = None
    app_name: str = "LINE"
    name: str = "default"

    def get_window_id(self) -> Optional[int]:
        """Get window handle, looking it up by name if needed."""
        if self.window_id:
            return self.window_id
        if self.window_name:
            return get_window_id_by_name(self.window_name, self.app_name)
        return None


@dataclass
class CapturedFrame:
    """A captured screen frame with metadata."""
    image_path: str
    timestamp: float
    region: CaptureRegion
    image_hash: str = ""
    source_name: str = ""  # Name of the window/region that produced this frame


class ScreenCaptureService:
    """Service for capturing screen regions or windows on Windows."""

    def __init__(
        self,
        regions: List[CaptureRegion] = None,
        windows: List[CaptureWindow] = None,
        temp_dir: str = None
    ):
        self.regions = regions or []
        self.windows = windows or []
        self.temp_dir = temp_dir or tempfile.mkdtemp(prefix="copy_trader_")
        self._last_hashes: Dict[str, str] = {}
        self._last_force_refresh: float = 0.0
        self.FORCE_REFRESH_INTERVAL: float = 300.0  # Force OCR every 5 minutes (was 60s — too aggressive)

        Path(self.temp_dir).mkdir(parents=True, exist_ok=True)
        logger.info(f"ScreenCaptureService initialized with {len(self.regions)} regions, {len(self.windows)} windows")

    def capture_region(self, region: CaptureRegion) -> CapturedFrame:
        """Capture a single screen region using Pillow ImageGrab."""
        if not PIL_AVAILABLE:
            raise RuntimeError("Pillow not available for screen capture")

        timestamp = time.time()
        filename = f"{region.name}_{int(timestamp * 1000)}.png"
        filepath = str(Path(self.temp_dir) / filename)

        try:
            bbox = (region.x, region.y, region.x + region.width, region.y + region.height)
            img = ImageGrab.grab(bbox=bbox)
            img.save(filepath)

            img_hash = self._compute_file_hash(filepath)

            return CapturedFrame(
                image_path=filepath,
                timestamp=timestamp,
                region=region,
                image_hash=img_hash
            )
        except Exception as e:
            logger.error(f"Screen capture failed: {e}")
            raise RuntimeError(f"Screen capture failed: {e}")

    # Throttle scroll: track last scroll time per window
    _last_scroll_times: Dict[int, float] = {}
    SCROLL_INTERVAL = 30  # seconds between scrolls per window

    def _send_scroll_to_bottom(self, hwnd: int):
        """
        Scroll LINE window to bottom to ensure latest messages are visible.
        Throttled to every 30 seconds per window to avoid disrupting user.
        Uses focus + End key (the only method that works for LINE's CEF rendering).
        """
        import ctypes

        now = time.time()
        last_scroll = self._last_scroll_times.get(hwnd, 0)
        if now - last_scroll < self.SCROLL_INTERVAL:
            return

        self._last_scroll_times[hwnd] = now

        try:
            # Briefly set focus, send Ctrl+End then End x3, restore previous window
            old_fg = win32gui.GetForegroundWindow()
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            time.sleep(0.08)

            VK_END = 0x23
            VK_CONTROL = 0x11
            KEYEVENTF_EXTENDEDKEY = 0x01
            KEYEVENTF_KEYUP = 0x02

            # Ctrl+End: jump to absolute bottom
            # Must use KEYEVENTF_EXTENDEDKEY for End key, otherwise it sends
            # Numpad 1 instead of End when NumLock is on.
            ctypes.windll.user32.keybd_event(VK_CONTROL, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_END, 0, KEYEVENTF_EXTENDEDKEY, 0)
            ctypes.windll.user32.keybd_event(VK_END, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
            ctypes.windll.user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
            time.sleep(0.05)

            # Extra End key presses to ensure we're at the very bottom
            for _ in range(3):
                ctypes.windll.user32.keybd_event(VK_END, 0, KEYEVENTF_EXTENDEDKEY, 0)
                ctypes.windll.user32.keybd_event(VK_END, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
                time.sleep(0.03)

            time.sleep(0.05)
            if old_fg:
                ctypes.windll.user32.SetForegroundWindow(old_fg)
            logger.debug(f"Scrolled window {hwnd} to bottom")
        except Exception as e:
            logger.debug(f"Failed to scroll window {hwnd}: {e}")

    def capture_window(self, window: CaptureWindow) -> Optional[CapturedFrame]:
        """
        Capture a window by its handle. Works even if window is in background.
        Uses Win32 API PrintWindow for background capture.
        """
        hwnd = window.get_window_id()
        if not hwnd:
            logger.error(f"Could not get window handle for '{window.name}'")
            return None

        if not WIN32_AVAILABLE:
            logger.error("win32gui not available for window capture")
            return None

        # Scroll to bottom to ensure latest messages are visible
        self._send_scroll_to_bottom(hwnd)

        timestamp = time.time()
        filename = f"{window.name}_{int(timestamp * 1000)}.png"
        filepath = str(Path(self.temp_dir) / filename)

        hwnd_dc = None
        mfc_dc = None
        save_dc = None
        bitmap = None

        try:
            # Get window dimensions
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width = right - left
            height = bottom - top

            if width <= 0 or height <= 0:
                logger.error("Window has zero dimensions")
                return None

            # Create device context and bitmap
            hwnd_dc = win32gui.GetWindowDC(hwnd)
            mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
            save_dc = mfc_dc.CreateCompatibleDC()

            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
            save_dc.SelectObject(bitmap)

            # Use PrintWindow to capture (works for background windows)
            import ctypes
            ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 2)

            # Convert to PIL Image and save
            bmp_info = bitmap.GetInfo()
            bmp_str = bitmap.GetBitmapBits(True)
            img = Image.frombuffer(
                'RGB',
                (bmp_info['bmWidth'], bmp_info['bmHeight']),
                bmp_str, 'raw', 'BGRX', 0, 1
            )
            img.save(filepath)

            img_hash = self._compute_file_hash(filepath)
            dummy_region = CaptureRegion(x=0, y=0, width=width, height=height, name=window.name)

            return CapturedFrame(
                image_path=filepath,
                timestamp=timestamp,
                region=dummy_region,
                image_hash=img_hash,
                source_name=window.name
            )

        except Exception as e:
            logger.error(f"Error capturing window via PrintWindow: {e}")
            # Fallback: screenshot of window area (requires window to be visible)
            try:
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                img = ImageGrab.grab(bbox=(left, top, right, bottom))
                img.save(filepath)
                img_hash = self._compute_file_hash(filepath)
                dummy_region = CaptureRegion(x=left, y=top, width=right-left, height=bottom-top, name=window.name)
                return CapturedFrame(
                    image_path=filepath,
                    timestamp=timestamp,
                    region=dummy_region,
                    image_hash=img_hash
                )
            except Exception as e2:
                logger.error(f"Fallback capture also failed: {e2}")
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

    def capture_single_window(self, source_name: str) -> Optional[CapturedFrame]:
        """Capture a specific window by its source name (no dedup)."""
        for window in self.windows:
            if window.name == source_name:
                return self.capture_window(window)
        return None

    def capture_all_regions(self, deduplicate: bool = True) -> List[CapturedFrame]:
        """Capture all configured regions."""
        frames = []

        # Force refresh: clear phash cache periodically so new short messages get detected
        now = time.time()
        if now - self._last_force_refresh >= self.FORCE_REFRESH_INTERVAL:
            self._last_hashes.clear()
            self._last_force_refresh = now
            logger.debug("Force-cleared phash cache for periodic refresh")

        for region in self.regions:
            try:
                frame = self.capture_region(region)

                if deduplicate:
                    last_hash = self._last_hashes.get(region.name)
                    if frame.image_hash == last_hash:
                        logger.debug(f"Region '{region.name}' unchanged, skipping")
                        Path(frame.image_path).unlink(missing_ok=True)
                        continue
                    self._last_hashes[region.name] = frame.image_hash

                frames.append(frame)
                logger.debug(f"Captured region '{region.name}'")

            except Exception as e:
                logger.error(f"Failed to capture region '{region.name}': {e}")

        for window in self.windows:
            try:
                frame = self.capture_window(window)
                if frame is None:
                    continue

                if deduplicate:
                    # Use perceptual hash for better dedup (tolerates minor rendering differences)
                    phash = self._compute_perceptual_hash(frame.image_path)
                    last_hash = self._last_hashes.get(window.name)
                    if last_hash and self._phash_similar(phash, last_hash, threshold=6):
                        logger.debug(f"Window '{window.name}' unchanged (phash distance < 3), skipping")
                        Path(frame.image_path).unlink(missing_ok=True)
                        continue
                    if last_hash:
                        try:
                            import imagehash
                            h1 = imagehash.hex_to_hash(phash)
                            h2 = imagehash.hex_to_hash(last_hash)
                            dist = h1 - h2
                            logger.info(f"Window '{window.name}' changed (phash distance={dist}), processing")
                        except Exception:
                            logger.info(f"Window '{window.name}' changed, processing")
                    self._last_hashes[window.name] = phash

                frames.append(frame)
                logger.debug(f"Captured window '{window.name}'")

            except Exception as e:
                logger.error(f"Failed to capture window '{window.name}': {e}")

        return frames

    def _compute_file_hash(self, filepath: str) -> str:
        """Compute MD5 hash of a file."""
        hasher = hashlib.md5()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _compute_perceptual_hash(self, filepath: str) -> str:
        """Compute perceptual hash of an image."""
        try:
            import imagehash
            from PIL import Image
            with Image.open(filepath) as img:
                return str(imagehash.phash(img))
        except ImportError:
            return self._compute_file_hash(filepath)

    def _phash_similar(self, hash1: str, hash2: str, threshold: int = 3) -> bool:
        """Check if two perceptual hashes are similar (Hamming distance)."""
        try:
            import imagehash
            h1 = imagehash.hex_to_hash(hash1)
            h2 = imagehash.hex_to_hash(hash2)
            return (h1 - h2) < threshold
        except Exception:
            return hash1 == hash2

    def cleanup_old_files(self, max_age_seconds: int = 300):
        """Clean up old screenshot files."""
        now = time.time()
        temp_path = Path(self.temp_dir)

        for f in temp_path.glob("*.png"):
            try:
                if now - f.stat().st_mtime > max_age_seconds:
                    f.unlink()
                    logger.debug(f"Cleaned up old file: {f}")
            except Exception as e:
                logger.warning(f"Failed to clean up {f}: {e}")


def select_region_interactive() -> CaptureRegion:
    """Interactive region selector for Windows."""
    print("Please configure the screen capture region.")
    print("Open your chat application and note the window position.\n")

    try:
        x = int(input("X coordinate (left edge): "))
        y = int(input("Y coordinate (top edge): "))
        width = int(input("Width: "))
        height = int(input("Height: "))
        name = input("Name for this region (e.g., 'line_chat'): ") or "chat_window"

        return CaptureRegion(x=x, y=y, width=width, height=height, name=name)
    except ValueError as e:
        raise RuntimeError(f"Invalid input: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    print("Testing screen capture...")
    print("Listing visible windows:\n")

    windows = list_app_windows("")
    for w in windows[:20]:
        print(f"  HWND: {w['id']} | Title: {w['name']}")

    print(f"\nTotal visible windows: {len(windows)}")
