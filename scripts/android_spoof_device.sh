#!/usr/bin/env bash
# 把 userdebug AVD 的裝置身分改成一台真實的 Pixel 7。
#
# 為什麼要這麼做：MT5 手機版在原生 AVD 上會偵測到模擬器然後 exit(0)（不是崩潰，
# 見 dumpsys activity exit-info 的 reason=1 EXIT_SELF）。這支腳本一次只動一組屬性，
# 每組改完都可以單獨測 MT5，才問得出它到底查哪一項。
#
# 前提：AVD 必須是 google_apis（非 Play Store）映像，且用 -writable-system 開機，
#       否則 adb root / remount 會被拒。
#
# 用法：
#   scripts/android_spoof_device.sh backup      先備份原始 build.prop
#   scripts/android_spoof_device.sh selinux     只關 SELinux（不需重開機）
#   scripts/android_spoof_device.sh identity    只改機型／品牌／指紋
#   scripts/android_spoof_device.sh restore     還原
set -euo pipefail

# Git Bash 會把 /system/... 這種參數轉成 Windows 路徑，adb 收到就爛掉了
export MSYS_NO_PATHCONV=1

ADB="${LOCALAPPDATA}/Android/Sdk/platform-tools/adb.exe"
WORK="data/android"
mkdir -p "$WORK"

need_root() {
  "$ADB" root >/dev/null 2>&1 || true
  sleep 3
  local who
  who=$("$ADB" shell id -u 2>/dev/null | tr -d '\r')
  [ "$who" = "0" ] || { echo "拿不到 root（uid=$who）。映像是不是還是 Play Store 版？"; exit 1; }
}

case "${1:-}" in
  backup)
    need_root
    "$ADB" pull /system/build.prop "$WORK/build.prop.orig"
    echo "原始 build.prop 已備份到 $WORK/build.prop.orig"
    ;;

  selinux)
    # 這一項不用重開機，也不用動 build.prop —— 先單獨測，因為 MT5 自殺前
    # 最後一個動作就是被 SELinux 擋下的 ro.serialno 讀取。
    need_root
    "$ADB" shell setenforce 0
    echo "SELinux 現在是：$("$ADB" shell getenforce | tr -d '\r')"
    ;;

  identity)
    need_root
    [ -f "$WORK/build.prop.orig" ] || { echo "先跑 backup"; exit 1; }
    "$ADB" remount >/dev/null

    # 冒充 Pixel 7（panther）。挑 Google 自家機種是因為映像本來就是 Google 血統，
    # 指紋格式、GMS 套件都對得起來，比冒充三星少一堆不一致的破綻。
    python - "$WORK/build.prop.orig" "$WORK/build.prop.patched" <<'PY'
import sys
src, dst = sys.argv[1], sys.argv[2]
FINGERPRINT = ("google/panther/panther:14/UP1A.231105.001/"
               "10817346:user/release-keys")
NEW = {
    "ro.product.model": "Pixel 7",
    "ro.product.name": "panther",
    "ro.product.device": "panther",
    "ro.product.brand": "google",
    "ro.product.manufacturer": "Google",
    "ro.product.system.model": "Pixel 7",
    "ro.product.system.name": "panther",
    "ro.product.system.device": "panther",
    "ro.product.system.brand": "google",
    "ro.product.system.manufacturer": "Google",
    "ro.build.product": "panther",
    "ro.build.fingerprint": FINGERPRINT,
    "ro.system.build.fingerprint": FINGERPRINT,
    "ro.build.description": "panther-user 14 UP1A.231105.001 10817346 release-keys",
    "ro.hardware": "panther",
    "ro.boot.hardware": "panther",
    # 這兩個是所有模擬器偵測程式庫的頭號檢查項
    "ro.kernel.qemu": "0",
    "ro.boot.qemu": "0",
}
seen, out = set(), []
for line in open(src, encoding="utf-8", errors="replace"):
    key = line.split("=", 1)[0].strip()
    if key in NEW:
        out.append(f"{key}={NEW[key]}\n")
        seen.add(key)
    else:
        out.append(line)
# build.prop 裡本來沒有的照樣補上；init 讀不到就是讀不到，補了不會更糟
missing = [k for k in NEW if k not in seen]
if missing:
    out.append("\n# --- spoof ---\n")
    out += [f"{k}={NEW[k]}\n" for k in missing]
open(dst, "w", encoding="utf-8", newline="\n").writelines(out)
print(f"覆寫 {len(seen)} 項、新增 {len(missing)} 項")
PY

    "$ADB" push "$WORK/build.prop.patched" /system/build.prop >/dev/null
    "$ADB" shell chmod 644 /system/build.prop
    echo "已寫入，重開機套用中…"
    "$ADB" reboot
    ;;

  restore)
    need_root
    "$ADB" remount >/dev/null
    "$ADB" push "$WORK/build.prop.orig" /system/build.prop >/dev/null
    "$ADB" shell chmod 644 /system/build.prop
    "$ADB" reboot
    echo "已還原並重開機"
    ;;

  *)
    sed -n '2,20p' "$0"
    exit 1
    ;;
esac
