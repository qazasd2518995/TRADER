# TRADER

以瀏覽器作為唯一控制介面的 LINE → Hub → MT5 跟單系統。中央端唯讀解密本機 LINE Desktop 資料庫，不再截圖、OCR、操作剪貼簿或依賴桌面 GUI。

## 架構

```text
LINE 加密 DB（唯讀）
  → 每聊天室 rowid 游標
  → Regex 完整性與 SL/TP 方向檢查
  → Hub（event_id 精確冪等）
  → 會員端（execution_id 精確下單）
  → MT5 File Bridge EA
```

LINE 回覆／引用的 `_relatedMessageId` 會轉成原始報單的 deterministic `execution_id`。撤單只刪除該 ID 對應且尚未成交的掛單；不猜方向、不猜最近一單，也不會平掉已成交部位。

系統刻意不再使用訊號時效、文字雜湊、模糊去重、同方向 supersede、逾時撤單或價格偏離撤單。仍保留兩種必要的精確狀態：

- LINE 每聊天室的持久化 rowid 游標，避免重啟回放歷史訊息。
- Hub `event_id` 與 MT5 `execution_id` 冪等，避免網路重試重複下單。

## 開發啟動

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m copy_trader.central.central_signal_center_web
```

會員端另開一個終端：

```bash
python -m copy_trader.central.client_agent_web
```

兩者都會在本機啟動 HTTP 控制台並開啟瀏覽器。中央端的設定頁可自動尋找 LINE DB，並設定安全金鑰名稱與聊天室 JSON；按「測試 LINE 資料庫」成功後才啟動。

## LINE DB 金鑰

金鑰永遠不存入 Web 設定、不回傳到瀏覽器，也不寫入日誌。

macOS 預設從目前使用者的 Keychain service `line-db-research` 讀取：

```bash
security add-generic-password -U -a "$USER" -s line-db-research -w '<32 位十六進位金鑰>'
```

Windows 會從目前使用者的 Credential Manager 讀取同名 target，並支援一般 Desktop、UWP 與 MSIX 候選路徑搜尋。第一次移機請依 [Windows LINE DB 設定與實機驗收](docs/windows-line-database.md)取得自己的 DB path/key 並驗證；Windows LINE codec 尚未在本機實測前，不應假設一定與 macOS 相同。

完整資料契約、平台邊界與驗收條件見 [LINE 本機資料庫串接規格](docs/line-database-spec.md)。

## 測試

```bash
python -m unittest discover -s tests -v
```

測試涵蓋首次 baseline 不回放、失敗不前移游標、跨層精確 execution identity、未授權撤單、只撤指定掛單與 Hub 冪等。

## 發布

- Web 安裝包：[docs/one-click-installers.md](docs/one-click-installers.md)
- 中央／Hub 架構：[docs/central-signal-system.md](docs/central-signal-system.md)
- Windows LINE DB 設定：[docs/windows-line-database.md](docs/windows-line-database.md)
- LINE DB 串接規格：[docs/line-database-spec.md](docs/line-database-spec.md)
- Fly.io Hub：[docs/cloud-hub-deploy.md](docs/cloud-hub-deploy.md)
- MT5 bridge：`mt5_ea/MT5_File_Bridge_Enhanced.mq5`
