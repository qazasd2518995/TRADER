# Web 控制台安裝包

兩個角色都以 PyInstaller 包裝 Python Web 控制台；啟動後開啟本機瀏覽器，不包含 Tauri、React、PySide、Tk、OCR 或剪貼簿自動化。

## Windows

```bat
build_one_click_windows.bat
```

會建置：

```text
dist\黃金訊號中心
dist\黃金跟單會員端
dist\installers\GoldCopyTrader-Signal-*-Windows.exe
dist\installers\GoldCopyTrader-Member-*-Windows.exe
dist\installers\MT5_File_Bridge_Enhanced.mq5
```

若未安裝 Inno Setup，仍會保留可直接執行的 PyInstaller 資料夾。

Windows 中央機目前需要：

- 使用「自動尋找資料庫」或設定工具取得 LINE DB 完整路徑。
- 將自己的 32-hex key 存入目前使用者的 Windows Credential Manager。
- 依 [Windows LINE DB 設定與實機驗收](windows-line-database.md)確認該版 LINE 的 codec、schema 與引用關係；若不同，只替換 provider/schema adapter。

## macOS

```bash
bash build_one_click_macos_client.sh
bash build_one_click_macos_central.sh
```

中央包會帶入 `apsw-sqlite3mc`；會員包不需要 LINE DB extension。圖示位於 `packaging/assets/`，不再依賴舊桌面專案。

會員端建置也會把安裝包內同一份最新版 EA 另外複製為 `dist/installers/MT5_File_Bridge_Enhanced.mq5`，方便既有會員只更新 EA。安裝包與獨立 EA 必須有相同 SHA-256 內容雜湊。

## 建置後檢查

```bash
python scripts/check-build-payload.py
```

檢查會確認中央包含 APSW、會員包不含 APSW／OCR／PySide 堆疊。完整發布流程可用：

```bash
python scripts/build-release.py --all
```
