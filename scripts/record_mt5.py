#!/usr/bin/env python3
"""錄 MT5 視窗 —— 給策略測試器的視覺回放用。

    python scripts/record_mt5.py --list                 # 看有哪些視窗
    python scripts/record_mt5.py --seconds 90 --fps 10  # 錄 90 秒
    python scripts/record_mt5.py --seconds 90 --crop-title  # 裁掉標題列

為什麼需要這支
  ChartScreenShot 在策略測試器裡不會寫出檔案（MQL5 論壇已確認，實測也是：
  回傳成功、chart id 是假的 12345、檔案不存在）。所以測試器的視覺回放只能
  從外面錄。

用 PrintWindow 而不是抓整個螢幕
  PrintWindow 直接跟視窗要它的畫面，所以：
    1. 視窗被別的東西蓋住也拍得到，你可以繼續做別的事
    2. 只拿到 MT5 的內容，不會錄進桌面上其他東西
    3. 尺寸固定，不受螢幕解析度或視窗位置影響

隱私
  MT5 的標題列有帳號與券商名稱。--crop-title 會把上緣裁掉；要拿去對外
  推廣的話一定要裁，不然等於把帳戶資訊公開。
"""
from __future__ import annotations

import argparse
import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import List, Optional, Tuple

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "recordings"

from PIL import Image                                    # noqa: E402

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.SetProcessDPIAware()

PW_RENDERFULLCONTENT = 2


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


def _exe_of(hwnd: int) -> str:
    """視窗屬於哪個執行檔。"""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid.value)      # QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(32768)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1].lower()
    finally:
        k32.CloseHandle(h)
    return ""


def find_chart(parent: int) -> Optional[Tuple[int, str, int, int]]:
    """在終端底下找出圖表那個子視窗。

    直接錄整個終端會連功能表、工具列、終端機面板一起錄進去 —— 那裡有帳號
    與餘額，而且畫面大半是無關的介面。圖表是 MDI 子視窗，EnumWindows 只列
    最上層視窗，找不到它，要用 EnumChildWindows 往下找。
    """
    best: Optional[Tuple[int, str, int, int]] = None

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        nonlocal best
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        # 圖表視窗的類別名是 AfxFrameOrView*，而且是畫面上最大的那塊
        if "AfxFrameOrView" not in cls.value or w < 400 or h < 300:
            return True
        if best is None or w * h > best[2] * best[3]:
            best = (hwnd, cls.value, w, h)
        return True

    user32.EnumChildWindows(parent, cb, 0)
    return best


def find_windows(exe: str = "terminal64.exe") -> List[Tuple[int, str, int, int]]:
    """靠執行檔找視窗，不靠標題。

    MT5 的標題是「帳號 - 伺服器: 模擬帳戶 - ...」，裡面根本沒有 MetaTrader
    這個字，比對標題會找不到。而且標題會隨帳號、語言、開啟的圖表而變 ——
    比對行程才穩。
    """
    out: List[Tuple[int, str, int, int]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        if _exe_of(hwnd) != exe.lower():
            return True
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        if w < 200 or h < 200:
            return True                    # 工具提示之類的小視窗不算
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        out.append((hwnd, buf.value, w, h))
        return True

    user32.EnumWindows(cb, 0)
    out.sort(key=lambda x: -x[2] * x[3])    # 最大的那個通常是主視窗
    return out


class WindowGrabber:
    """重複使用同一組 GDI 物件。每張都重建的話會慢一倍，而且長時間錄會漏控制代碼。"""

    def __init__(self, hwnd: int):
        self.hwnd = hwnd
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        self.w, self.h = r.right - r.left, r.bottom - r.top
        self.hdc = user32.GetWindowDC(hwnd)
        self.memdc = gdi32.CreateCompatibleDC(self.hdc)
        self.bmp = gdi32.CreateCompatibleBitmap(self.hdc, self.w, self.h)
        gdi32.SelectObject(self.memdc, self.bmp)
        self.hdr = BITMAPINFOHEADER()
        self.hdr.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        self.hdr.biWidth = self.w
        self.hdr.biHeight = -self.h              # 負的 = 由上而下，省一次翻轉
        self.hdr.biPlanes = 1
        self.hdr.biBitCount = 32
        self.buf = ctypes.create_string_buffer(self.w * self.h * 4)

    def grab(self) -> Optional[Image.Image]:
        if not user32.PrintWindow(self.hwnd, self.memdc, PW_RENDERFULLCONTENT):
            return None
        gdi32.GetDIBits(self.memdc, self.bmp, 0, self.h, self.buf,
                        ctypes.byref(self.hdr), 0)
        return Image.frombuffer("RGB", (self.w, self.h), self.buf,
                                "raw", "BGRX", 0, 1)

    def close(self) -> None:
        gdi32.DeleteObject(self.bmp)
        gdi32.DeleteDC(self.memdc)
        user32.ReleaseDC(self.hwnd, self.hdc)


def record(hwnd: int, seconds: float, fps: float, out_dir: Path,
           crop_title: int = 0, crop_bottom: int = 0) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.png"):
        old.unlink()

    grab = WindowGrabber(hwnd)
    interval = 1.0 / fps
    deadline = time.monotonic() + seconds
    n, dropped = 0, 0
    print(f"錄製中：{grab.w}x{grab.h} · {fps:g} fps · {seconds:g} 秒 → {out_dir}")
    try:
        nxt = time.monotonic()
        while time.monotonic() < deadline:
            img = grab.grab()
            if img is None:
                dropped += 1
            else:
                if crop_title or crop_bottom:
                    img = img.crop((0, crop_title, grab.w, grab.h - crop_bottom))
                img.save(out_dir / f"{n:05d}.png")
                n += 1
            nxt += interval
            slack = nxt - time.monotonic()
            if slack > 0:
                time.sleep(slack)
            else:
                nxt = time.monotonic()          # 落後就重新對齊，不要越積越多
    except KeyboardInterrupt:
        print("  （手動中止）")
    finally:
        grab.close()
    print(f"完成：{n} 張" + (f"，{dropped} 張抓不到" if dropped else ""))
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="列出可錄的視窗")
    ap.add_argument("--match", default="terminal64.exe",
                    help="要錄哪個執行檔的視窗（預設 MT5）")
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--fps", type=float, default=10.0)
    ap.add_argument("--crop-title", dest="crop_title", type=int, default=0,
                    metavar="像素",
                    help="從上緣裁掉幾個像素（標題列有帳號與券商名稱，"
                         "要對外的話務必裁掉；建議 90）")
    ap.add_argument("--crop-bottom", dest="crop_bottom", type=int, default=0,
                    metavar="像素",
                    help="從下緣裁掉幾個像素（終端機面板會顯示餘額與權益；"
                         "建議 340）")
    ap.add_argument("--chart-only", dest="chart_only", action="store_true",
                    help="只錄圖表子視窗（不含功能表、工具列、終端機面板）")
    ap.add_argument("--out", default="", help="輸出目錄")
    args = ap.parse_args()

    wins = find_windows(args.match)
    if args.list or not wins:
        print(f"{args.match} 的視窗：")
        for hwnd, title, w, h in wins:
            print(f"  hwnd={hwnd}  {w}x{h}  {title[:80]}")
        if not wins:
            print("  （沒找到，MT5 開著嗎？）")
        return

    hwnd, title, w, h = wins[0]
    if args.chart_only:
        chart = find_chart(hwnd)
        if chart is None:
            print("  找不到圖表子視窗，改錄整個終端")
        else:
            hwnd, title, w, h = chart
            print(f"圖表子視窗：{w}x{h}（{title}）")
    if not args.chart_only:
        print(f"目標：{title[:80]}")
    out = Path(args.out) if args.out else OUT_DIR / time.strftime("%Y%m%d_%H%M%S")
    if not (args.crop_title or args.crop_bottom or args.chart_only):
        print("  ⚠ 沒有裁切：標題列有帳號與券商、底部面板有餘額。"
              "要對外的話加 --crop-title 90 --crop-bottom 340")
    record(hwnd, args.seconds, args.fps, out, args.crop_title, args.crop_bottom)


if __name__ == "__main__":
    main()
