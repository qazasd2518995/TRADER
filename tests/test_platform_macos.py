# tests/test_platform_macos.py
"""
macOS platform layer tests.
Run on macOS only: pytest tests/test_platform_macos.py -v
"""
import sys
import pytest
from pathlib import Path

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")


class TestMacScreenCapture:
    def test_enumerate_windows_returns_list(self):
        from copy_trader.platform.macos import MacScreenCapture
        sc = MacScreenCapture()
        windows = sc.enumerate_windows()
        assert isinstance(windows, list)
        assert len(windows) > 0

    def test_enumerate_windows_with_filter(self):
        from copy_trader.platform.macos import MacScreenCapture
        sc = MacScreenCapture()
        windows = sc.enumerate_windows("Finder")
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

    def test_is_window_visible(self):
        from copy_trader.platform.macos import MacScreenCapture
        sc = MacScreenCapture()
        windows = sc.enumerate_windows()
        if windows:
            assert sc.is_window_visible(windows[0].window_id) is True
        assert sc.is_window_visible(999999999) is False

    def test_get_window_rect(self):
        from copy_trader.platform.macos import MacScreenCapture
        sc = MacScreenCapture()
        windows = sc.enumerate_windows()
        if windows:
            rect = sc.get_window_rect(windows[0].window_id)
            assert rect is not None
            assert len(rect) == 4
        assert sc.get_window_rect(999999999) is None


class TestMacPlatformConfig:
    def test_mt5_files_path(self):
        from copy_trader.platform.macos import MacPlatformConfig
        config = MacPlatformConfig()
        path = config.get_mt5_files_path()
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
        result = kb.activate_window(999999)
        assert isinstance(result, bool)

    def test_send_scroll_no_crash(self):
        from copy_trader.platform.macos import MacKeyboardControl
        kb = MacKeyboardControl()
        result = kb.send_scroll_to_bottom(999999)
        assert isinstance(result, bool)
