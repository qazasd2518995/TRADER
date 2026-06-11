# 雲端 Hub 部署（Fly.io）

中央訊號 Hub（`copy_trader/central/hub_server.py`）部署在 Fly.io，網址固定，會員端與訊號中心都連同一個 Hub。部署設定就在 repo 根目錄：`Dockerfile`、`fly.toml`、`.dockerignore`。

- 線上 App：`gold-signal-hub-tw`
- 公開網址：`https://gold-signal-hub-tw.fly.dev`
- 區域：`nrt`（東京，離台灣最近的低延遲區）
- 容器只跑純標準函式庫的 Hub，不含 LINE 擷取或 MT5 下單邏輯。

## 首次部署

需要先安裝 [flyctl](https://fly.io/docs/flyctl/install/) 並登入（`fly auth login`）。

```sh
# 1. 建立持久化磁碟（保存訊號歷史與 seq 序號，重啟不歸零）
fly volumes create hub_data --region nrt --size 1

# 2. 部署（在 repo 根目錄執行，會讀取 fly.toml + Dockerfile）
fly deploy

# 3. 設定 Hub 密碼（會員端 / 訊號中心都要填同一個）
fly secrets set COPY_TRADER_HUB_TOKEN=<your-token>
```

## 日常更新

改動 `copy_trader/central/hub_server.py` 後重新部署：

```sh
fly deploy
```

## 換密碼

```sh
fly secrets set COPY_TRADER_HUB_TOKEN=<new-token>
```

換完後，每台訊號中心 / 會員端的「Hub 密碼」欄位也要同步更新。

## 健康檢查

```sh
curl https://gold-signal-hub-tw.fly.dev/health
# {"ok": true, "latest_seq": N, "count": N, "auth_required": true}
```

`auth_required: true` 代表 token 已生效（沒帶 token 讀 `/signals` 會收到 401）。

## 設計重點

- **Dockerfile** 只 `COPY copy_trader/central`，並把 `copy_trader/__init__.py` 清空，避免容器載入 Windows/macOS 專用模組（pywin32、tkinter、剪貼板擷取等）。Hub 本身只用 Python 標準函式庫。
- **Store 路徑** 由 `COPY_TRADER_HUB_STORE=/data/central_hub_signals.jsonl` 指到掛載的磁碟。即使磁碟被清空，會員端 agent 也會自動把 `last_seq` 對齊到 Hub 目前的 `latest_seq`，不會重放歷史訊號或漏單。
- **Token** 不寫進 image 或 repo，只透過 `fly secrets` 在執行期注入。
- **`.dockerignore`** 排除整個 repo 只留 `copy_trader/`，讓每次部署的 build context 維持在數十 KB（repo 根目錄含 `tools/line_chat.db` 約 1GB，務必別一起上傳）。

完整跟單流程與中央 / 會員端設定見 [`central-signal-system.md`](central-signal-system.md)。
