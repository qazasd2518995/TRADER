"""建置後檢查：打出來的東西該有的有沒有、不該有的有沒有混進去。

為什麼需要這支
--------------
訊號中心必須帶 SQLite3MC 版 APSW 才能解開 LINE DB；會員端則不應夾帶它。
PyInstaller 對延遲匯入與原生 extension 可能建置成功卻漏檔，因此在產物上驗證。

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
        ["apsw"],
        [],
        8,
    ),
    "client": (
        "黃金跟單會員端",
        [],
        # 會員端不擷取畫面，夾進去只是讓安裝檔多 244 MB
        ["apsw", "rapidocr", "onnxruntime", "PySide6"],
        8,
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
