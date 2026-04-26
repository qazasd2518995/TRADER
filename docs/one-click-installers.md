# One-Click Installers

這裡提供新的「不用命令列」版本。程式啟動後會自動開啟本機瀏覽器控制台，使用者在網頁上按開始/停止即可。

- `黃金訊號中心`：安裝在中央 Windows / macOS 訊號電腦，負責 Hub + LINE 全選複製擷取。
- `黃金跟單會員端`：安裝在會員 Windows / macOS 電腦，負責接 Hub 訊號並寫入本機 MT5。

## Windows 產出安裝檔

在 Windows build 電腦執行：

```bat
build_one_click_windows.bat
```

輸出：

```text
dist\黃金訊號中心
dist\黃金跟單會員端
dist\installers\黃金訊號中心_安裝檔.exe
dist\installers\黃金跟單會員端_安裝檔.exe
```

若電腦沒有安裝 Inno Setup，腳本會先產出可直接執行的資料夾；安裝 Inno Setup 後重跑即可產出正式安裝檔。

## macOS 產出 DMG

在 macOS build 電腦執行：

```bash
bash build_one_click_macos_client.sh
bash build_one_click_macos_central.sh
```

輸出：

```text
dist/黃金跟單會員端.app
dist/黃金訊號中心.app
dist/installers/黃金跟單會員端.dmg
dist/installers/黃金訊號中心.dmg
```

macOS 中央訊號中心需要授權「螢幕錄製」和「輔助使用」，否則程式看不到 LINE 視窗或無法送出 Cmd+A/C。

## 使用流程

中央電腦：

1. 安裝 `黃金訊號中心_安裝檔.exe` 或 `黃金訊號中心.dmg`。
2. 如果會員不在同一個網路，Windows 中央機先執行一次 `install_cloudflared_windows.bat`。
3. 開啟 `黃金訊號中心`。
4. 保留預設 `Hub 監聽 IP = 127.0.0.1`、Port `8765`，並勾選 `Cloudflare Tunnel`。
5. 按 `開始`。
6. 畫面紀錄會顯示會員端要填的 Cloudflare 公開 Hub URL。

會員電腦：

1. 安裝 `黃金跟單會員端`。
2. 把新版 `mt5_ea/MT5_File_Bridge_Enhanced.mq5` 編譯並掛到 MT5 圖表。
3. 開啟 `黃金跟單會員端`。
4. 填中央電腦顯示的 Cloudflare Hub URL 和密碼。
5. 如自動偵測不到 MT5，手動選擇 `MQL5/Files` 資料夾。
6. 按 `開始`。

## 開機自動啟動

Windows 安裝檔有「開機後自動啟動」選項。macOS 可在「系統設定 → 一般 → 登入項目」加入 App。程式內也有「開啟程式後自動開始」勾選項：

- 安裝時勾開機自動啟動：Windows 登入後會開程式。
- 程式內勾自動開始：程式開啟後會自動啟動 Hub 或會員端 agent。

兩個都勾起來，才會達到接近全自動常駐。
