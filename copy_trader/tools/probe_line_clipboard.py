"""
LINE 剪贴板探测脚本 (Windows)
====================================
跑這支腳本之前請確認：
  1. LINE Desktop 已開啟並登入
  2. 準備好一個有報單歷史的聊天室

用法（在專案根目錄）：
    python -m copy_trader.tools.probe_line_clipboard --title "黃金報單"

參數：
  --title      視窗標題關鍵字 (必填)。比對方式為「子字串不分大小寫」
  --screens    往上翻幾屏以擴大選取範圍 (預設 2)
  --dump-file  將結果同時存成 txt 檔 (預設 probe_dump.txt)
  --keep-clip  不還原剪貼板 (除錯用，平常別開)

腳本會：
  1. 列出所有 LINE 視窗，並定位符合 --title 的那一個
  2. 備份當前剪貼板
  3. 短暫 focus LINE 視窗 → Ctrl+End → Shift+PgUp×N → Ctrl+C
  4. 讀取剪貼板文字並印出前後 20 行
  5. 還原原本的剪貼板與前景視窗
"""
import argparse
import ctypes
import sys
import time
from pathlib import Path

try:
    import win32gui
    import win32con
    import win32clipboard
except ImportError:
    print("[x] 缺少 pywin32，請先執行: pip install pywin32")
    sys.exit(1)


VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_END = 0x23
VK_HOME = 0x24
VK_PRIOR = 0x21  # PageUp
VK_NEXT = 0x22   # PageDown
KEYEVENTF_EXTENDEDKEY = 0x01
KEYEVENTF_KEYUP = 0x02

CF_UNICODETEXT = 13
CF_TEXT = 1


def enum_line_windows(title_filter: str):
    hits = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd) or ""
        if not title:
            return
        if title_filter.lower() in title.lower():
            try:
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                hits.append((hwnd, title, (left, top, right - left, bottom - top)))
            except Exception:
                hits.append((hwnd, title, (0, 0, 0, 0)))

    win32gui.EnumWindows(cb, None)
    return hits


def key_down(vk, extended=False):
    flags = KEYEVENTF_EXTENDEDKEY if extended else 0
    ctypes.windll.user32.keybd_event(vk, 0, flags, 0)


def key_up(vk, extended=False):
    flags = (KEYEVENTF_EXTENDEDKEY if extended else 0) | KEYEVENTF_KEYUP
    ctypes.windll.user32.keybd_event(vk, 0, flags, 0)


def tap(vk, extended=False):
    key_down(vk, extended)
    key_up(vk, extended)


def activate(hwnd):
    try:
        if win32gui.IsIconic(hwnd):
            ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            time.sleep(0.15)
        ctypes.windll.user32.BringWindowToTop(hwnd)
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        time.sleep(0.1)
        return win32gui.GetForegroundWindow() == hwnd
    except Exception as e:
        print(f"[!] activate failed: {e}")
        return False


def backup_clipboard():
    """備份剪貼板當前的 unicode 文字 (其他格式先不處理)。"""
    try:
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(CF_UNICODETEXT):
                return ("text", win32clipboard.GetClipboardData(CF_UNICODETEXT))
        finally:
            win32clipboard.CloseClipboard()
    except Exception as e:
        print(f"[!] backup failed: {e}")
    return (None, None)


def restore_clipboard(backup):
    kind, data = backup
    if kind != "text" or data is None:
        return
    try:
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(CF_UNICODETEXT, data)
        finally:
            win32clipboard.CloseClipboard()
    except Exception as e:
        print(f"[!] restore failed: {e}")


def read_clipboard_text():
    for _ in range(20):  # 等最多 1 秒
        try:
            win32clipboard.OpenClipboard()
            try:
                if win32clipboard.IsClipboardFormatAvailable(CF_UNICODETEXT):
                    return win32clipboard.GetClipboardData(CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            pass
        time.sleep(0.05)
    return ""


def clear_clipboard():
    try:
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        pass


def copy_chat_tail(hwnd, screens: int) -> str:
    """
    把指定 LINE 視窗「底部 N 屏」文字複製出來。
    流程：focus → Ctrl+End 滾到底 → Shift+PgUp×N 選取 → Ctrl+C
    """
    old_fg = win32gui.GetForegroundWindow()
    clear_clipboard()

    if not activate(hwnd):
        return ""

    try:
        # 1. Ctrl+End 滾到最底
        key_down(VK_CONTROL)
        tap(VK_END, extended=True)
        key_up(VK_CONTROL)
        time.sleep(0.12)

        # 2. Shift + PageUp × N 選取從底部往上 N 屏
        key_down(VK_SHIFT)
        for _ in range(max(1, screens)):
            tap(VK_PRIOR, extended=True)
            time.sleep(0.08)
        key_up(VK_SHIFT)
        time.sleep(0.1)

        # 3. Ctrl+C 複製
        key_down(VK_CONTROL)
        tap(ord('C'))
        key_up(VK_CONTROL)
        time.sleep(0.15)

        text = read_clipboard_text()
    finally:
        # 還原前景視窗 (盡力)
        if old_fg and old_fg != hwnd:
            try:
                ctypes.windll.user32.SetForegroundWindow(old_fg)
            except Exception:
                pass

    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True, help="LINE 視窗標題關鍵字")
    ap.add_argument("--screens", type=int, default=2, help="Shift+PgUp 次數 (預設 2)")
    ap.add_argument("--dump-file", default="probe_dump.txt")
    ap.add_argument("--keep-clip", action="store_true", help="不還原原剪貼板 (除錯用)")
    args = ap.parse_args()

    print(f"\n=== LINE 剪貼板探測 ===")
    print(f"視窗標題關鍵字: {args.title}")
    print(f"選取屏數 (Shift+PgUp): {args.screens}\n")

    hits = enum_line_windows(args.title)
    if not hits:
        print("[x] 找不到視窗，請確認 LINE 是否開啟且標題包含關鍵字")
        sys.exit(2)

    for hwnd, title, bounds in hits[:10]:
        print(f"  hwnd={hwnd}  title='{title}'  bounds={bounds}")

    hwnd, title, _ = hits[0]
    print(f"\n[→] 鎖定第一個結果: hwnd={hwnd} title='{title}'\n")

    backup = None if args.keep_clip else backup_clipboard()

    t0 = time.time()
    text = copy_chat_tail(hwnd, args.screens)
    elapsed = (time.time() - t0) * 1000

    if not args.keep_clip:
        restore_clipboard(backup)

    if not text:
        print("[x] 剪貼板沒有文字 — LINE 可能沒收到鍵盤事件 (CEF 限制)")
        print("    請嘗試手動驗證: 對 LINE 視窗按 Ctrl+End → Shift+PgUp → Ctrl+C")
        sys.exit(3)

    lines = text.splitlines()
    print(f"[✓] 複製成功: 耗時 {elapsed:.0f}ms, 共 {len(lines)} 行, {len(text)} 字元\n")

    print("---------- 前 20 行 ----------")
    for ln in lines[:20]:
        print(repr(ln))
    print("---------- 後 20 行 ----------")
    for ln in lines[-20:]:
        print(repr(ln))

    dump_path = Path(args.dump_file).resolve()
    dump_path.write_text(text, encoding="utf-8")
    print(f"\n[✓] 完整內容已寫入: {dump_path}")
    print("\n請把這份 dump 的樣貌告訴我 (尤其是時間戳/發送者格式)，我就能寫對應的 parser。")


if __name__ == "__main__":
    main()
