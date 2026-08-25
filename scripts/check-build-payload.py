"""建置後檢查：打出來的東西該有的有沒有、不該有的有沒有混進去。

為什麼需要這支
--------------
2026-08-25：四份 spec 合併成共用的 _common.py 時，EXCLUDES 被兩個角色共用，
而裡面含 rapidocr / onnxruntime / cv2 / numpy。對會員端那是正確的（他不擷取
畫面），對訊號中心卻是致命的 —— 整條擷取管線就是 PrintWindow + OCR。

打出來的訊號中心從 278 MB 變成 34 MB，**建置過程零錯誤零警告**，
要等使用者按下開始、log 噴 RapidOCR 載入失敗才會發現。

這種「少了東西但建置成功」的失敗最難抓，所以用大小 + 模組雙重檢查釘死。

用法：
    python scripts/check-build-payload.py            # 檢查 dist/ 下兩個
    python scripts/check-build-payload.py central    # 只檢查訊號中心
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"

# 角色 → (資料夾名, 必須存在的模組, 必須不存在的模組, 大小下限 MB)
SPEC = {
    "central": (
        "黃金訊號中心",
        # 擷取管線的核心，少一個就等於不會讀訊號
        ["rapidocr", "onnxruntime", "cv2", "numpy"],
        [],
        200,
    ),
    "client": (
        "黃金跟單會員端",
        [],
        # 會員端不擷取畫面，夾進去只是讓安裝檔多 244 MB
        ["rapidocr", "onnxruntime"],
        15,
    ),
}


def find_module(internal: Path, name: str) -> bool:
    """PyInstaller 會把套件放成資料夾或 .pyd/.dll，兩種都算數。"""
    if not internal.is_dir():
        return False
    low = name.lower()
    for p in internal.iterdir():
        n = p.name.lower()
        if n == low or n.startswith(low + ".") or n.startswith(low + "-"):
            return True
    return False


def check(role: str) -> bool:
    folder, must_have, must_not, min_mb = SPEC[role]
    root = DIST / folder
    print(f"\n▎{role}  ({folder})")
    if not root.is_dir():
        print(f"   ✗ 找不到 {root}")
        return False

    size_mb = sum(f.stat().st_size for f in root.rglob("*") if f.is_file()) / (1024 * 1024)
    internal = root / "_internal"
    ok = True

    if size_mb < min_mb:
        print(f"   ✗ 大小 {size_mb:.0f} MB，低於下限 {min_mb} MB —— 極可能漏打包了東西")
        ok = False
    else:
        print(f"   ✓ 大小 {size_mb:.0f} MB（下限 {min_mb} MB）")

    for m in must_have:
        if find_module(internal, m):
            print(f"   ✓ 有 {m}")
        else:
            print(f"   ✗ 缺少 {m} —— 這是必要模組")
            ok = False

    for m in must_not:
        if find_module(internal, m):
            print(f"   ✗ 混進了 {m} —— 應該被排除")
            ok = False
        else:
            print(f"   ✓ 沒有 {m}（已正確排除）")

    return ok


def main() -> int:
    roles = sys.argv[1:] or list(SPEC)
    bad = [r for r in roles if r not in SPEC]
    if bad:
        print(f"未知角色：{bad}，可用：{list(SPEC)}")
        return 2
    results = {r: check(r) for r in roles}
    print()
    print("=" * 60)
    for r, good in results.items():
        print(f"  {r:<10}{'✓ 通過' if good else '✗ 不通過'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
