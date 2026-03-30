# macOS 移植设计规格

## 概述

将 Windows 版 OCR 自动跟单黄金 MT5 系统移植到 macOS (Apple Silicon)。采用**平台抽象层**方案，在现有代码基础上建立统一接口，Windows 和 macOS 共用业务逻辑，仅底层系统调用分平台实现。

## 目标环境

- macOS 26.3+ / Apple Silicon (M3 arm64)
- Python 3.14 (Homebrew)
- MetaTrader 5 macOS 版 (net.metaquotes.wine.MetaTrader5, 内嵌 Wine)
- LINE 桌面版 (macOS 原生)
- Tauri 2.0

## 核心约束

- 功能与 Windows 版完全一致
- 业务逻辑代码零修改
- 一个代码库支持两个平台
- macOS 需要屏幕录制权限（系统级授权）

---

## 架构设计

### 1. 平台抽象层

新增 `copy_trader/platform/` 目录：

```
copy_trader/platform/
├── __init__.py      # 平台自动侦测，导出统一接口
├── base.py          # ABC 抽象接口定义
├── windows.py       # pywin32 实现（现有代码重构）
└── macos.py         # macOS 原生 API 实现
```

#### 1.1 抽象接口 (base.py)

```python
from abc import ABC, abstractmethod
from pathlib import Path
from PIL import Image
from dataclasses import dataclass

@dataclass
class WindowInfo:
    window_id: int          # hwnd (Windows) / kCGWindowNumber (macOS)
    title: str              # 窗口标题
    owner_name: str         # 所属应用名称
    bounds: tuple[int, int, int, int]  # (x, y, width, height)
    is_visible: bool

class ScreenCaptureBase(ABC):
    @abstractmethod
    def enumerate_windows(self, title_filter: str = "") -> list[WindowInfo]:
        """列出所有窗口，可按标题过滤"""

    @abstractmethod
    def capture_window(self, window_id: int) -> Image.Image | None:
        """截取指定窗口画面，窗口可被遮挡"""

    @abstractmethod
    def capture_region(self, x: int, y: int, w: int, h: int) -> Image.Image | None:
        """截取屏幕指定区域"""

    @abstractmethod
    def is_window_visible(self, window_id: int) -> bool:
        """窗口是否在屏幕上"""

    @abstractmethod
    def get_window_rect(self, window_id: int) -> tuple[int, int, int, int] | None:
        """取得窗口位置和大小 (x, y, w, h)"""

class KeyboardControlBase(ABC):
    @abstractmethod
    def activate_window(self, window_id: int) -> bool:
        """将窗口带到前台"""

    @abstractmethod
    def send_key_combo(self, keys: list[str]) -> bool:
        """发送组合键，如 ['cmd', 'end'] 或 ['ctrl', 'end']"""

class PlatformConfigBase(ABC):
    @abstractmethod
    def get_mt5_files_path(self) -> Path:
        """MT5 MQL5/Files 目录路径"""

    @abstractmethod
    def get_app_data_path(self) -> Path:
        """应用数据存储路径"""

    @abstractmethod
    def get_tesseract_path(self) -> str | None:
        """Tesseract 可执行文件路径"""
```

#### 1.2 平台自动侦测 (__init__.py)

```python
import sys

if sys.platform == "win32":
    from .windows import WindowsScreenCapture as ScreenCapture
    from .windows import WindowsKeyboardControl as KeyboardControl
    from .windows import WindowsPlatformConfig as PlatformConfig
elif sys.platform == "darwin":
    from .macos import MacScreenCapture as ScreenCapture
    from .macos import MacKeyboardControl as KeyboardControl
    from .macos import MacPlatformConfig as PlatformConfig
else:
    raise RuntimeError(f"Unsupported platform: {sys.platform}")
```

### 2. macOS 屏幕截图实现 (macos.py)

#### 2.1 API 对应关系

| 功能 | Windows API | macOS API |
|------|------------|-----------|
| 枚举窗口 | `win32gui.EnumWindows` | `CGWindowListCopyWindowInfo(kCGWindowListOptionAll, kCGNullWindowID)` |
| 窗口标题 | `win32gui.GetWindowText` | `kCGWindowName` + `kCGWindowOwnerName` |
| 窗口位置 | `win32gui.GetWindowRect` | `kCGWindowBounds` |
| 截取窗口 | `PrintWindow` + GDI bitmap | `CGWindowListCreateImage(bounds, kCGWindowListOptionIncludingWindow, windowID, kCGWindowImageDefault)` |
| 截取区域 | `ImageGrab.grab()` | `CGWindowListCreateImage(CGRectMake(x,y,w,h), kCGWindowListOptionOnScreenOnly, kCGNullWindowID, kCGWindowImageDefault)` |
| 窗口可见 | `IsWindowVisible` | `kCGWindowIsOnscreen` |
| 激活窗口 | `SetForegroundWindow` | `NSRunningApplication.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)` |
| 键盘模拟 | `keybd_event` | `CGEventPost(kCGHIDEventTap, event)` |

#### 2.2 核心依赖

- **`pyobjc-framework-Quartz`** — CGWindowList API、CGEvent API
- **`pyobjc-framework-Cocoa`** — NSRunningApplication、NSWorkspace
- **`pyobjc-framework-Vision`** — macOS Vision Framework OCR（本地备选）

#### 2.3 窗口截图关键实现

```python
from Quartz import (
    CGWindowListCopyWindowInfo,
    CGWindowListCreateImage,
    kCGWindowListOptionIncludingWindow,
    kCGWindowImageDefault,
    kCGNullWindowID,
    CGRectNull,
)

def capture_window(self, window_id: int) -> Image.Image | None:
    # CGWindowListCreateImage 可截取被遮挡的窗口
    cg_image = CGWindowListCreateImage(
        CGRectNull,  # 自动使用窗口边界
        kCGWindowListOptionIncludingWindow,
        window_id,
        kCGWindowImageDefault
    )
    if cg_image is None:
        return None
    # CGImage → PIL Image 转换
    # 通过 CGDataProvider 提取像素数据
    ...
```

#### 2.4 权限处理

macOS 需要「屏幕录制」权限才能截取其他应用窗口。实现策略：
- 首次调用截图时检测权限
- 若无权限，通过 Tauri 前端弹出提示，引导用户到「系统设置 > 隐私与安全 > 屏幕录制」授权
- 提供权限检测方法：尝试截图，若返回全黑图像则判断为未授权

### 3. macOS OCR 实现

#### 3.1 Fallback 链

```
RapidOCR (ONNX) → PaddleOCR → macOS Vision Framework → Tesseract
```

- **RapidOCR** — 主力引擎，ONNX Runtime 原生支持 macOS arm64，无需修改
- **PaddleOCR** — 跨平台，可用
- **macOS Vision Framework** — 替代 WinRT OCR，通过 pyobjc-framework-Vision 调用
- **Tesseract** — 路径改为 `/opt/homebrew/bin/tesseract`

#### 3.2 Vision Framework OCR

```python
from Vision import VNRecognizeTextRequest, VNImageRequestHandler
from Quartz import CGImageSourceCreateWithData, CGImageSourceCreateImageAtIndex

def ocr_with_vision(image: Image.Image) -> list[dict]:
    """使用 macOS Vision Framework 进行文字识别"""
    handler = VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, None)
    request = VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLanguages_(["zh-Hant", "zh-Hans", "en"])  # 繁中、简中、英文
    request.setRecognitionLevel_(1)  # accurate mode
    handler.performRequests_error_([request], None)
    # 提取结果文字和 bounding box
    ...
```

### 4. 路径配置

#### 4.1 macOS 路径

```python
class MacPlatformConfig(PlatformConfigBase):
    def get_mt5_files_path(self) -> Path:
        return Path.home() / "Library/Application Support" / \
            "net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/MQL5/Files"

    def get_app_data_path(self) -> Path:
        return Path.home() / "Library/Application Support/黃金跟單系統"

    def get_tesseract_path(self) -> str | None:
        tesseract = Path("/opt/homebrew/bin/tesseract")
        return str(tesseract) if tesseract.exists() else None
```

#### 4.2 Windows 路径（现有，搬入 windows.py）

```python
class WindowsPlatformConfig(PlatformConfigBase):
    def get_mt5_files_path(self) -> Path:
        return Path("C:/Program Files/MetaTrader 5/MQL5/Files")

    def get_app_data_path(self) -> Path:
        return Path(os.environ.get("APPDATA", "")) / "黃金跟單系統"

    def get_tesseract_path(self) -> str | None:
        tesseract = Path("C:/Program Files/Tesseract-OCR/tesseract.exe")
        return str(tesseract) if tesseract.exists() else None
```

### 5. LINE 窗口滚动

Windows 版发送 Ctrl+End 让 LINE 聊天滚到底部。macOS 版：

```python
class MacKeyboardControl(KeyboardControlBase):
    def send_key_combo(self, keys: list[str]) -> bool:
        """
        macOS LINE 滚动到底部：Cmd+End
        使用 CGEventPost 模拟键盘事件
        """
        from Quartz import (
            CGEventCreateKeyboardEvent,
            CGEventPost,
            kCGHIDEventTap,
            CGEventSetFlags,
            kCGEventFlagMaskCommand,
        )
        # End key = keycode 0x77 (119)
        # Command modifier flag
        event_down = CGEventCreateKeyboardEvent(None, 0x77, True)
        CGEventSetFlags(event_down, kCGEventFlagMaskCommand)
        CGEventPost(kCGHIDEventTap, event_down)

        event_up = CGEventCreateKeyboardEvent(None, 0x77, False)
        CGEventPost(kCGHIDEventTap, event_up)
        return True

    def activate_window(self, window_id: int) -> bool:
        """通过 NSRunningApplication 激活窗口"""
        from AppKit import NSRunningApplication, NSWorkspace
        # 根据 window owner PID 找到对应 app 并激活
        ...
```

### 6. Tauri / Rust 层修改

#### 6.1 lib.rs — Sidecar 名称

```rust
// 现有（Windows only）:
let sidecar = app.shell().sidecar("copy-trader-sidecar").unwrap();

// Tauri 自动根据平台选择对应 binary:
// Windows: binaries/copy-trader-sidecar-x86_64-pc-windows-msvc.exe
// macOS:   binaries/copy-trader-sidecar-aarch64-apple-darwin
```

Tauri 2 的 sidecar 机制已内建平台判断，只需将 macOS 编译产物放到正确命名即可。

#### 6.2 tauri.conf.json

在 externalBin 中无需修改，Tauri 自动附加平台 triple。

### 7. Build 系统

#### 7.1 macOS Sidecar 编译脚本 (build_sidecar.sh)

```bash
#!/bin/bash
pip install -r sidecar_requirements_macos.txt
pyinstaller --noconfirm copy-trader-sidecar-macos.spec
cp dist/copy-trader-sidecar src-tauri/binaries/copy-trader-sidecar-aarch64-apple-darwin
```

#### 7.2 macOS 依赖文件 (sidecar_requirements_macos.txt)

```
Pillow>=10.0
rapidocr_onnxruntime>=3.7.0
onnxruntime>=1.17.0
pyobjc-framework-Quartz>=10.0
pyobjc-framework-Cocoa>=10.0
pyobjc-framework-Vision>=10.0
groq>=0.5.0
anthropic>=0.25.0
pyinstaller>=6.0
```

#### 7.3 Tauri 构建

```bash
npm run tauri build
# 产出: src-tauri/target/release/bundle/dmg/黃金跟單系統.dmg
```

#### 7.4 PyInstaller macOS Spec

基于现有 `copy-trader-sidecar.spec` 修改：
- 移除 `win32gui`, `win32ui`, `win32con` hidden imports
- 加入 `objc`, `Quartz`, `AppKit`, `Vision` hidden imports
- 移除 Windows DLL 排除项
- 目标格式: onefile Unix 可执行文件（无 `.exe` 后缀）

### 8. 品质保证

#### 8.1 平台兼容性验证清单

- [ ] `pyobjc-framework-Quartz` 在 Python 3.14 + arm64 上安装成功
- [ ] `rapidocr_onnxruntime` 在 macOS arm64 上正常运行
- [ ] `onnxruntime` arm64 wheel 可用
- [ ] `CGWindowListCopyWindowInfo` 能正确列出 LINE 窗口
- [ ] `CGWindowListCreateImage` 能截取 LINE 窗口（含被遮挡时）
- [ ] OCR 对中文聊天截图准确率与 Windows 版一致
- [ ] MT5 JSON 文件读写在 Wine 路径下正常工作
- [ ] `CGEventPost` 键盘模拟在有辅助使用权限时工作
- [ ] PyInstaller onefile 在 macOS 上正常运行
- [ ] Tauri .dmg 打包成功且可安装

#### 8.2 需要的 macOS 系统权限

1. **屏幕录制** — 截取其他应用窗口
2. **辅助使用（Accessibility）** — 键盘模拟（CGEventPost）
3. 首次运行需引导用户授权

---

## 改动范围总结

### 新增文件
- `copy_trader/platform/__init__.py`
- `copy_trader/platform/base.py`
- `copy_trader/platform/windows.py`（从现有代码重构）
- `copy_trader/platform/macos.py`（新实现）
- `sidecar_requirements_macos.txt`
- `copy-trader-sidecar-macos.spec`
- `build_sidecar.sh`
- `build_tauri.sh`

### 修改文件
- `copy_trader/signal_capture/screen_capture.py` — 改为调用 platform 层
- `copy_trader/signal_capture/ocr.py` — WinRT → Vision Framework（条件导入）
- `copy_trader/config.py` — 路径改为从 PlatformConfig 获取
- `copy_trader/sidecar_main.py` — 调整 import
- `src-tauri/lib.rs` — sidecar binary 命名（Tauri 已自动处理）

### 不动文件（约 80%）
- `copy_trader/signal_parser/*` — 所有信号解析器
- `copy_trader/trade_manager/*` — 交易管理、马丁格尔
- `copy_trader/mt5_reader.py` — JSON 读写（路径来源改为 PlatformConfig）
- `copy_trader/signal_capture/bubble_detector.py` — 气泡检测
- `src/*` — 整个前端 UI
- `mt5_ea/*` — MQL5 EA
