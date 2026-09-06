#!/usr/bin/env python3
"""錄 Android 畫面（模擬器或實體手機）。

    python scripts/record_android.py --devices              # 看接到什麼
    python scripts/record_android.py --frames --seconds 60  # 逐張 PNG
    python scripts/record_android.py --video --seconds 600  # 直接錄 MP4

為什麼比抓視窗好
  adb 是直接跟 Android 系統要畫面，不是抓 Windows 視窗。所以：
    1. 視窗被蓋住、最小化、甚至用 -no-window 完全不顯示，都拿得到畫面
    2. 不會拍到桌面上其他東西，也不用裁掉標題列
    3. 解析度固定，不受螢幕縮放或視窗大小影響
  桌面版 MT5 那邊踩過的坑（截圖 API 不理會捲動、要找子視窗、要裁帳號欄）
  在這裡全部不存在。

兩種模式
  --frames  每張都是 PNG，想怎麼後製都行，但每張要 100~300 ms，約 3~8 fps
  --video   用 Android 內建的 screenrecord，效率高很多，但單檔上限 3 分鐘
            （Android 的限制），長時間錄要接續多檔
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "android"

# Android 的 screenrecord 單檔上限就是 3 分鐘，超過會自己停。
SEGMENT_SEC = 175


def adb(*args: str, serial: str = "", binary: bool = False, timeout: float = 60.0):
    cmd = ["adb"] + (["-s", serial] if serial else []) + list(args)
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if r.returncode != 0:
        detail = r.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"adb {' '.join(args)} 失敗：{detail}")
    return r.stdout if binary else r.stdout.decode("utf-8", "replace")


def devices() -> List[tuple]:
    out = adb("devices", "-l")
    rows = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line or "\t" not in line and " " not in line:
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] in ("device", "offline", "unauthorized"):
            rows.append((parts[0], parts[1], " ".join(parts[2:])))
    return rows


def grab(serial: str = "") -> bytes:
    """抓一張 PNG。用 exec-out 而不是 shell —— shell 會把換行改掉，PNG 就壞了。"""
    return adb("exec-out", "screencap", "-p", serial=serial, binary=True, timeout=20)


def record_frames(serial: str, seconds: float, fps: float, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.png"):
        old.unlink()
    interval = 1.0 / fps
    deadline = time.monotonic() + seconds
    n, slow = 0, 0
    nxt = time.monotonic()
    print(f"逐張擷取：{fps:g} fps 目標 · {seconds:g} 秒 → {out_dir}")
    while time.monotonic() < deadline:
        t0 = time.monotonic()
        try:
            (out_dir / f"{n:05d}.png").write_bytes(grab(serial))
            n += 1
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            print(f"  第 {n} 張抓不到：{exc}")
        took = time.monotonic() - t0
        if took > interval:
            slow += 1
        nxt += interval
        slack = nxt - time.monotonic()
        if slack > 0:
            time.sleep(slack)
        else:
            nxt = time.monotonic()          # 落後就重新對齊，不要越積越多
    if slow:
        print(f"  （{slow} 張沒跟上目標 fps —— screencap 本身就要 100~300 ms）")
    print(f"完成：{n} 張")
    return n


def record_video(serial: str, seconds: float, out_dir: Path,
                 bitrate: str = "2M", size: str = "") -> List[Path]:
    """用裝置內建的 screenrecord，分段錄再拉回來。

    單檔 3 分鐘是 Android 的硬限制，所以長時間錄一定要分段。分段之間會掉
    幾百毫秒 —— 要完全無縫得用 --frames，或改用外部擷取。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    made: List[Path] = []
    left = seconds
    idx = 0
    while left > 0:
        chunk = min(left, SEGMENT_SEC)
        remote = f"/sdcard/rec_{idx:03d}.mp4"
        args = ["shell", "screenrecord", "--bit-rate", bitrate,
                "--time-limit", str(int(chunk))]
        if size:
            args += ["--size", size]
        args.append(remote)
        print(f"  第 {idx + 1} 段：錄 {int(chunk)} 秒…")
        adb(*args, serial=serial, timeout=chunk + 60)
        local = out_dir / f"{idx:03d}.mp4"
        adb("pull", remote, str(local), serial=serial, timeout=120)
        adb("shell", "rm", remote, serial=serial)
        made.append(local)
        left -= chunk
        idx += 1
    total = sum(p.stat().st_size for p in made) / 1024 / 1024
    print(f"完成：{len(made)} 段，共 {total:.1f} MB → {out_dir}")
    return made


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--devices", action="store_true", help="列出接到的裝置")
    ap.add_argument("--serial", default="", help="指定裝置（多台時用）")
    ap.add_argument("--frames", action="store_true", help="逐張 PNG")
    ap.add_argument("--video", action="store_true", help="錄成 MP4")
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--fps", type=float, default=5.0, help="--frames 的目標 fps")
    ap.add_argument("--bitrate", default="2M", help="--video 的位元率")
    ap.add_argument("--size", default="", help="--video 的解析度，例如 720x1280")
    ap.add_argument("--out", default="", help="輸出目錄")
    args = ap.parse_args()

    try:
        found = devices()
    except FileNotFoundError:
        raise SystemExit("找不到 adb —— Android SDK platform-tools 裝好了嗎？")
    except RuntimeError as exc:
        raise SystemExit(str(exc))

    if args.devices or not found:
        print(f"接到的裝置 {len(found)} 個：")
        for serial, state, extra in found:
            print(f"  {serial:24} {state:12} {extra}")
        if not found:
            print("  （沒有。模擬器開著嗎？手機的 USB 偵錯開了嗎？）")
        return

    serial = args.serial or found[0][0]
    out = Path(args.out) if args.out else OUT_DIR / time.strftime("%Y%m%d_%H%M%S")
    print(f"裝置：{serial}")
    if args.video:
        record_video(serial, args.seconds, out, args.bitrate, args.size)
    else:
        record_frames(serial, args.seconds, args.fps, out)


if __name__ == "__main__":
    main()
