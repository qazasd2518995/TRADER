#!/usr/bin/env python3
"""一鍵建出可以直接發給會員的安裝檔。

  python3 scripts/build-release.py              # 建這個平台的兩個角色
  python3 scripts/build-release.py --client     # 只建會員端
  python3 scripts/build-release.py --central    # 只建訊號中心
  python3 scripts/build-release.py --skip-installer   # 只要 .app / 資料夾

產出：
  macOS    dist/installers/黃金跟單會員端_<版本>_macOS.dmg
  Windows  dist/installers/黃金跟單會員端_<版本>_Windows.exe

跨平台編譯是做不到的：PyInstaller 打包的是「這台機器上的 Python 直譯器 +
這個平台的原生模組」，在 macOS 上生不出 Windows 的 .exe。Windows 那份要嘛
在 Windows 機器上跑這支腳本，要嘛推 tag 讓 GitHub Actions 建
（.github/workflows/release.yml）。
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
OUT = DIST / "installers"
VERSION = "1.0.1"

ROLES = {
    "client":  {"name": "黃金跟單會員端", "label": "會員端"},
    "central": {"name": "黃金訊號中心",   "label": "訊號中心"},
}


def log(msg: str) -> None:
    print(f"  {msg}", flush=True)


def head(msg: str) -> None:
    print(f"\n\033[1m{msg}\033[0m", flush=True)


def run(cmd: list, **kw) -> subprocess.CompletedProcess:
    log("$ " + " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, check=True, **kw)


def plat_key() -> str:
    system = platform.system()
    if system == "Darwin":
        return "macos"
    if system == "Windows":
        return "windows"
    raise SystemExit(f"不支援的平台：{system}（只做 Windows 與 macOS）")


# ---------------------------------------------------------------- 前置檢查
def preflight(plat: str) -> None:
    head("前置檢查")

    try:
        import PyInstaller  # noqa: F401
        log(f"PyInstaller {PyInstaller.__version__}")
    except ImportError:
        raise SystemExit("缺 PyInstaller。先跑：pip install pyinstaller")

    ea = ROOT / "mt5_ea" / "MT5_File_Bridge_Enhanced.mq5"
    if not ea.is_file():
        raise SystemExit(f"找不到 EA 原始碼：{ea}")
    log(f"EA 原始碼 {ea.stat().st_size // 1024} KB")

    # 打包當下匯入一次，讓相依問題現在就炸，而不是等會員按下按鈕才炸
    sys.path.insert(0, str(ROOT))
    for mod in ("copy_trader.central.webui", "copy_trader.central.market",
                "copy_trader.central.stats", "copy_trader.central.web_launcher"):
        try:
            __import__(mod)
        except Exception as exc:
            raise SystemExit(f"{mod} 匯入失敗，先修好再打包：{exc}")
    log("核心模組匯入正常")

    if plat == "windows" and not find_inno():
        log("⚠ 找不到 Inno Setup，會跳過安裝檔（只產出資料夾）")


def find_inno() -> str | None:
    for exe in ("ISCC.exe", "iscc"):
        found = shutil.which(exe)
        if found:
            return found
    for guess in (
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
    ):
        if os.path.isfile(guess):
            return guess
    return None


# ---------------------------------------------------------------- 建置
def build_role(role: str, plat: str) -> Path:
    info = ROLES[role]
    head(f"建置 {info['label']}（{plat}）")

    spec = ROOT / "packaging" / "pyinstaller" / f"{role}-{plat}.spec"
    if not spec.is_file():
        raise SystemExit(f"找不到 spec：{spec}")

    # 每次都清掉舊的，不然改了 spec 卻沿用上一輪的產物，問題會很難查
    for stale in (DIST / info["name"], DIST / f"{info['name']}.app"):
        if stale.exists():
            shutil.rmtree(stale)

    t0 = time.time()
    run([sys.executable, "-m", "PyInstaller", "--noconfirm",
         "--distpath", str(DIST), "--workpath", str(DIST / "build"),
         str(spec)], cwd=ROOT)
    log(f"耗時 {time.time() - t0:.0f} 秒")

    target = (DIST / f"{info['name']}.app") if plat == "macos" else (DIST / info["name"])
    if not target.exists():
        raise SystemExit(f"建置完成但找不到產物：{target}")
    log(f"產物 {target.name}（{dir_size_mb(target):.0f} MB）")
    return target


def dir_size_mb(path: Path) -> float:
    if path.is_file():
        return path.stat().st_size / 1e6
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1e6


# ---------------------------------------------------------------- macOS 打包
def make_dmg(app: Path, role: str) -> Path:
    info = ROLES[role]
    head(f"包成 DMG：{info['label']}")
    OUT.mkdir(parents=True, exist_ok=True)
    dmg = OUT / f"{info['name']}_{VERSION}_macOS.dmg"
    if dmg.exists():
        dmg.unlink()

    # 這一步是關鍵：沒有 Apple 開發者憑證，下載下來的 .app 會被 Gatekeeper
    # 標記隔離、顯示「已損毀，無法打開」。先把自己這台的隔離屬性清掉，
    # 至少本機測試不會被擋；會員端的解法寫在安裝說明裡。
    subprocess.run(["xattr", "-cr", str(app)], check=False)

    # 用一個暫存資料夾當 DMG 內容：.app + 一個指向 /Applications 的捷徑，
    # 這樣使用者打開 DMG 直接拖過去就好，不用自己找路。
    staging = DIST / f"_dmg_{role}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    shutil.copytree(app, staging / app.name, symlinks=True)
    os.symlink("/Applications", staging / "Applications")

    run(["hdiutil", "create", "-volname", info["name"],
         "-srcfolder", str(staging), "-ov", "-format", "UDZO",
         "-fs", "HFS+", str(dmg)])
    shutil.rmtree(staging)
    log(f"{dmg.name}（{dir_size_mb(dmg):.0f} MB）")
    return dmg


# ---------------------------------------------------------------- Windows 打包
def make_installer(role: str) -> Path | None:
    info = ROLES[role]
    iscc = find_inno()
    if not iscc:
        log("跳過安裝檔（沒有 Inno Setup）")
        return None

    head(f"包成安裝檔：{info['label']}")
    OUT.mkdir(parents=True, exist_ok=True)
    iss = ROOT / "packaging" / "inno" / f"{role}-windows.iss"
    if not iss.is_file():
        log(f"跳過：找不到 {iss.name}")
        return None

    run([iscc, f"/DMyAppVersion={VERSION}", str(iss)], cwd=iss.parent)
    made = sorted(OUT.glob(f"*{info['name']}*.exe"))
    if made:
        log(f"{made[-1].name}（{dir_size_mb(made[-1]):.0f} MB）")
        return made[-1]
    return None


# ---------------------------------------------------------------- 冒煙測試
def smoke_data_dir(instance: str) -> Path:
    """冒煙測試那個實例的資料目錄。

    刻意不 import copy_trader.config 來問 —— 那個模組的 DATA_DIR 是載入時就
    算好的常數，這支腳本早就 import 過它（preflight 檢查匯入），現在再設
    環境變數已經來不及。直接照同一套規則自己算。
    """
    if platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support" / "黃金跟單系統"
    else:
        base = Path(os.environ.get("APPDATA", Path.home())) / "黃金跟單系統"
    return base / f"instance_{instance}" if instance else base


def smoke_test(target: Path, role: str, plat: str) -> bool:
    """真的把它跑起來，確認控制台端得出頁面。

    打包最常見的失敗不是建置失敗，而是建置成功但一啟動就 ModuleNotFoundError。
    那種錯誤只有跑起來才看得到，所以這一步不能省。
    """
    head(f"冒煙測試：{ROLES[role]['label']}")

    if plat == "macos":
        exe = target / "Contents" / "MacOS" / ROLES[role]["name"]
    else:
        exe = target / f"{ROLES[role]['name']}.exe"
    if not exe.exists():
        log(f"✗ 找不到執行檔 {exe}")
        return False

    # 控制台綁的是 port 0（系統指派），啟動後把實際 port 寫進資料目錄裡的
    # <role>_web_launcher_port.txt。所以要先算出資料目錄在哪。
    instance = f"smoke{role}"
    env = {**os.environ,
           "COPY_TRADER_INSTANCE": instance,
           # 不要讓建置過程一直彈瀏覽器視窗出來（CI 上也沒有桌面可彈）
           "COPY_TRADER_NO_BROWSER": "1"}
    data_dir = smoke_data_dir(instance)
    port_file = data_dir / f"{role}_web_launcher_port.txt"
    if port_file.exists():
        port_file.unlink()          # 舊的 port 檔會讓「已在執行」的判斷誤觸

    proc = subprocess.Popen([str(exe)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        import urllib.request
        ok = False
        for _ in range(40):
            time.sleep(1)
            if proc.poll() is not None:
                out = (proc.stdout.read() or b"").decode("utf-8", "replace")
                log(f"✗ 程式自己結束了（exit {proc.returncode}）")
                for line in out.strip().splitlines()[-15:]:
                    log(f"    {line}")
                return False
            if not port_file.exists():
                continue
            try:
                port = int(port_file.read_text(encoding="utf-8").strip())
            except Exception:
                continue
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as r:
                    body = r.read(6000).decode("utf-8", "replace")
            except Exception:
                continue
            if "黃金" in body or "<html" in body.lower():
                log(f"✓ 控制台在 port {port} 回應了")
                # 順便打一下 API，確認延遲匯入的模組真的載得起來 ——
                # 打包漏模組的症狀正是「首頁開得出來，一打 API 就 500」
                for api in ("/api/status", "/api/stats", "/api/market?tf=M15"):
                    try:
                        with urllib.request.urlopen(
                                f"http://127.0.0.1:{port}{api}", timeout=5) as r:
                            code = r.status
                        log(f"  {api} → {code}")
                    except Exception as exc:
                        log(f"  ✗ {api} → {exc}")
                        return False
                ok = True
                break
        if not ok:
            log("✗ 40 秒內沒有回應")
            out = b""
            try:
                proc.terminate()
                out = proc.stdout.read() or b""
            except Exception:
                pass
            for line in out.decode("utf-8", "replace").strip().splitlines()[-15:]:
                log(f"    {line}")
        return ok
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description="建出可以發給會員的安裝檔")
    ap.add_argument("--client", action="store_true", help="只建會員端")
    ap.add_argument("--central", action="store_true", help="只建訊號中心")
    ap.add_argument("--skip-installer", action="store_true",
                    help="只建執行檔，不包 DMG / 安裝檔")
    ap.add_argument("--skip-smoke", action="store_true", help="跳過冒煙測試")
    args = ap.parse_args()

    roles = []
    if args.client:
        roles.append("client")
    if args.central:
        roles.append("central")
    if not roles:
        roles = ["client", "central"]

    plat = plat_key()
    preflight(plat)

    results, failed = [], []
    for role in roles:
        target = build_role(role, plat)

        if not args.skip_smoke:
            if not smoke_test(target, role, plat):
                failed.append(ROLES[role]["label"])

        if args.skip_installer:
            results.append((ROLES[role]["label"], target))
            continue
        made = make_dmg(target, role) if plat == "macos" else make_installer(role)
        results.append((ROLES[role]["label"], made or target))

    head("完成")
    for label, path in results:
        try:
            rel = path.relative_to(ROOT)
        except ValueError:
            rel = path
        print(f"  {label:6} → {rel}  ({dir_size_mb(path):.0f} MB)")

    if failed:
        print(f"\n\033[31m  冒煙測試沒過：{'、'.join(failed)}\033[0m")
        print("  安裝檔還是產出來了，但先別發出去。")
        return 1

    if plat == "macos":
        print("\n  提醒：沒有 Apple 開發者憑證，這份 DMG 未經簽章與公證。")
        print("  會員第一次開啟要按右鍵→打開，或跑 xattr -cr。詳見")
        print("  docs/安裝說明.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
