"""
Screen Capture Service (cross-platform)
Captures specified screen regions or windows for OCR processing.
Uses platform abstraction layer for Windows (pywin32) and macOS (Quartz).
"""
import subprocess
import tempfile
import time
import hashlib
import io
import base64
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

try:
    from copy_trader.platform import ScreenCapture, KeyboardControl, WindowInfo
    PLATFORM_AVAILABLE = True
except ImportError:
    PLATFORM_AVAILABLE = False
    logger.warning("Platform layer not available")

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    logger.warning("Pillow not available. Install: pip install Pillow")


def get_window_id_by_name(window_name: str, app_name: str = "LINE") -> Optional[int]:
    """Get window ID by window title (cross-platform)."""
    if not PLATFORM_AVAILABLE:
        logger.error("Platform layer not available")
        return None

    try:
        sc = ScreenCapture()
        windows = sc.enumerate_windows(window_name)
        if windows:
            # Prefer shortest title (exact/closest match)
            windows.sort(key=lambda w: len(w.title))
            wid = windows[0].window_id
            logger.info(f"Found window: '{windows[0].title}' (ID: {wid})")
            return wid
        logger.warning(f"Window not found: {window_name}")
        return None
    except Exception as e:
        logger.error(f"Error finding window: {e}")
        return None


def list_app_windows(app_name: str = "LINE") -> List[Dict]:
    """List visible windows with stable IDs and user-friendly labels."""
    if not PLATFORM_AVAILABLE:
        return []

    try:
        sc = ScreenCapture()
        windows = sc.enumerate_windows(app_name)
        counts = Counter(
            ((w.owner_name or app_name or "").strip(), (w.title or w.owner_name or app_name or "").strip())
            for w in windows
        )

        items = []
        for w in windows:
            owner = (w.owner_name or app_name or "").strip()
            name = (w.title or owner).strip()
            x, y, width, height = w.bounds

            label = name or f"Window {w.window_id}"
            if owner and name and owner.lower() != name.lower():
                label = f"{name} | {owner}"

            is_generic = not name or (owner and name.lower() == owner.lower())
            if is_generic or counts[(owner, name)] > 1:
                label = f"{label} [{x},{y} {width}×{height}]"

            items.append({
                "id": w.window_id,
                "window_id": w.window_id,
                "name": name,
                "window_name": name,
                "owner": owner,
                "label": label,
                "bounds": {
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                },
            })

        return items
    except Exception:
        return []


def capture_window_preview(
    window_id: Optional[int] = None,
    window_name: Optional[str] = None,
    app_name: str = "LINE",
    max_width: int = 360,
    jpeg_quality: int = 70,
) -> Optional[Dict]:
    """Capture a lightweight preview image for a detected window."""
    if not PLATFORM_AVAILABLE or not PIL_AVAILABLE:
        return {"ok": False, "message": "Platform capture layer not available"}

    try:
        target_id = window_id or get_window_id_by_name(window_name or "", app_name)
        if not target_id:
            target_name = window_name or app_name or "unknown window"
            return {"ok": False, "message": f"找不到視窗：{target_name}"}

        sc = ScreenCapture()
        rect = None
        visible = None
        screen_access = None
        try:
            rect = sc.get_window_rect(target_id)
        except Exception:
            rect = None
        try:
            visible = sc.is_window_visible(target_id)
        except Exception:
            visible = None

        if sys.platform == "darwin":
            try:
                from Quartz import CGPreflightScreenCaptureAccess
                screen_access = bool(CGPreflightScreenCaptureAccess())
            except Exception:
                screen_access = None

        img = sc.capture_window(target_id)
        if img is None:
            reasons = []
            if screen_access is False:
                reasons.append("螢幕錄製權限尚未對目前這個 App 生效")
            if visible is False:
                reasons.append("視窗目前不在可擷取狀態，可能已最小化、切到其他桌面或已關閉")
            if rect:
                reasons.append(f"視窗尺寸 {rect[2]}×{rect[3]}")
            else:
                reasons.append("macOS 沒有回傳可用的視窗影像")
            return {"ok": False, "message": "；".join(reasons)}

        width, height = img.size
        if width <= 0 or height <= 0:
            return {"ok": False, "message": "視窗影像尺寸為 0，macOS 沒有提供可讀畫面"}

        preview = img.copy()
        if width > max_width:
            ratio = max_width / float(width)
            preview = preview.resize((max_width, max(1, int(height * ratio))))

        buf = io.BytesIO()
        preview.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
        data_url = f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
        return {
            "ok": True,
            "data_url": data_url,
            "width": preview.size[0],
            "height": preview.size[1],
            "window_id": target_id,
        }
    except Exception as e:
        logger.error(f"Failed to capture window preview: {e}")
        return {"ok": False, "message": f"預覽擷取失敗：{e}"}


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
    """Service for capturing screen regions or windows (cross-platform)."""

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
        self._last_frames: Dict[str, str] = {}  # window_name -> last image path (for diff)
        self._last_force_refresh: float = 0.0
        # Force a full OCR refresh more aggressively so subtle chat updates
        # do not stay hidden behind window-hash dedup for up to a minute.
        self.FORCE_REFRESH_INTERVAL: float = 10.0

        # Initialize platform layer
        self._screen_capture = ScreenCapture() if PLATFORM_AVAILABLE else None
        self._keyboard = KeyboardControl() if PLATFORM_AVAILABLE else None

        Path(self.temp_dir).mkdir(parents=True, exist_ok=True)
        logger.info(f"ScreenCaptureService initialized with {len(self.regions)} regions, {len(self.windows)} windows")

    def capture_region(self, region: CaptureRegion) -> CapturedFrame:
        """Capture a single screen region (cross-platform)."""
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
                image_hash=img_hash
            )
        except Exception as e:
            logger.error(f"Screen capture failed: {e}")
            raise RuntimeError(f"Screen capture failed: {e}")

    # Throttle scroll: track last scroll time per window
    SCROLL_INTERVAL = 5  # seconds between scrolls per window

    def __init_scroll_times(self):
        if not hasattr(self, '_scroll_times'):
            self._scroll_times: Dict[int, float] = {}

    def _send_scroll_to_bottom(self, hwnd: int, force: bool = False):
        """Scroll window to bottom to ensure latest messages are visible (cross-platform)."""
        self.__init_scroll_times()

        now = time.time()
        if not force and now - self._scroll_times.get(hwnd, 0) < self.SCROLL_INTERVAL:
            return
        self._scroll_times[hwnd] = now

        if self._keyboard:
            try:
                attempts = 2 if force else 1
                for attempt in range(attempts):
                    self._keyboard.send_scroll_to_bottom(hwnd)
                    if attempt + 1 < attempts:
                        time.sleep(0.12)
                logger.debug(f"Scrolled window {hwnd} to bottom")
            except Exception as e:
                logger.debug(f"Failed to scroll window {hwnd}: {e}")

    def scroll_window_to_bottom(self, source_name: str, force: bool = False) -> bool:
        """Scroll a configured capture window to the bottom by source name."""
        for window in self.windows:
            if window.name != source_name:
                continue
            hwnd = window.get_window_id()
            if not hwnd:
                return False
            self._send_scroll_to_bottom(hwnd, force=force)
            return True
        return False

    def capture_window(self, window: CaptureWindow) -> Optional[CapturedFrame]:
        """Capture a window by its handle. Works even if window is in background (cross-platform)."""
        hwnd = window.get_window_id()
        if not hwnd:
            logger.error(f"Could not get window handle for '{window.name}'")
            return None

        if not PLATFORM_AVAILABLE:
            logger.error("Platform layer not available for window capture")
            return None

        # Scroll to bottom to ensure latest messages are visible
        self._send_scroll_to_bottom(hwnd)
        time.sleep(0.12)

        timestamp = time.time()
        filename = f"{window.name}_{int(timestamp * 1000)}.png"
        filepath = str(Path(self.temp_dir) / filename)

        try:
            img = self._screen_capture.capture_window(hwnd)
            if img is None:
                logger.error(f"Window capture returned None for '{window.name}'")
                return None

            img.save(filepath)

            # Check for black/empty capture (permission issue on macOS)
            try:
                import numpy as np
                arr = np.array(img)
                if arr.mean() < 5:
                    logger.warning(f"Capture returned black image for '{window.name}', check screen recording permission")
            except ImportError:
                pass

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
                    # Dedup on the chat's lower area where the newest messages appear.
                    phash = self._compute_perceptual_hash(frame.image_path, crop_bottom_ratio=0.45)
                    last_hash = self._last_hashes.get(window.name)
                    if last_hash and self._phash_similar(phash, last_hash, threshold=4):
                        logger.debug(f"Window '{window.name}' unchanged (chat-bottom hash distance < 4), skipping")
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

    def compute_diff_region(self, window_name: str, new_image_path: str) -> Optional[str]:
        """
        Compare new frame with previous frame, crop to changed region only.
        Returns path to cropped diff image, or None to use full image.
        """
        prev_path = self._last_frames.get(window_name)
        self._last_frames[window_name] = new_image_path

        if not prev_path or not Path(prev_path).exists():
            return None  # No previous frame, use full image

        try:
            import cv2
            import numpy as np

            prev = cv2.imread(prev_path, cv2.IMREAD_GRAYSCALE)
            curr = cv2.imread(new_image_path, cv2.IMREAD_GRAYSCALE)

            if prev is None or curr is None:
                return None
            if prev.shape != curr.shape:
                return None  # Different sizes, can't diff

            # Compute absolute difference
            diff = cv2.absdiff(prev, curr)
            _, thresh = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)

            # Find bounding box of all changed pixels
            coords = cv2.findNonZero(thresh)
            if coords is None:
                return None  # No changes

            x, y, w, h = cv2.boundingRect(coords)

            # Skip tiny changes (cursor blink, timestamp update)
            if w * h < 2000:
                return None

            # Expand region with padding
            img = cv2.imread(new_image_path)
            img_h, img_w = img.shape[:2]
            pad = 20
            x1 = max(0, x - pad)
            y1 = max(0, y - pad)
            x2 = min(img_w, x + w + pad)
            y2 = min(img_h, y + h + pad)

            cropped = img[y1:y2, x1:x2]
            diff_path = new_image_path.replace('.png', '_diff.png')
            cv2.imwrite(diff_path, cropped)

            logger.debug(f"Diff region: ({x1},{y1})-({x2},{y2}) = {x2-x1}x{y2-y1} from {img_w}x{img_h}")
            return diff_path

        except Exception as e:
            logger.debug(f"Diff detection failed: {e}")
            return None

    def _compute_perceptual_hash(self, filepath: str, crop_bottom_ratio: float = 1.0) -> str:
        """Compute dHash (difference hash), optionally focused on the chat bottom area."""
        try:
            import imagehash
            from PIL import Image
            with Image.open(filepath) as img:
                if crop_bottom_ratio < 1.0:
                    top = int(img.height * (1.0 - crop_bottom_ratio))
                    side_pad = int(img.width * 0.04)
                    left = max(0, side_pad)
                    right = max(left + 1, img.width - side_pad)
                    bottom = img.height
                    if bottom > top:
                        img = img.crop((left, top, right, bottom))
                return str(imagehash.dhash(img))  # dHash: ~2x faster than pHash
        except ImportError:
            return self._compute_file_hash(filepath)

    def _phash_similar(self, hash1: str, hash2: str, threshold: int = 3) -> bool:
        """Check if two hashes are similar (Hamming distance)."""
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
    """Interactive region selector."""
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
