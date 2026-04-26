# Central Signal System

這套模式把「訊號取得」和「MT5 下單」拆開。

一般會員交付請優先使用一鍵安裝版，打包方式見 `docs/one-click-installers.md`。下面的命令列方式保留給開發與排錯。

- 中央電腦：常駐 LINE Desktop，使用全選複製文字取得訊號，解析後發布到 Hub。
- Hub：保存並提供訊號 feed，可給網頁或用戶端代理輪詢。
- 用戶端電腦：只跑輕量 MT5 agent，輪詢 Hub，寫入本機 MT5 `MQL5/Files/commands.json` 下單。

## 1. 中央電腦

先啟動 Hub：

```bash
python3 -m copy_trader.central.hub_server --host 0.0.0.0 --port 8765 --token "換成你的密碼"
```

再啟動 LINE 訊號擷取器：

```bash
python3 -m copy_trader.central.signal_collector \
  --hub-url http://127.0.0.1:8765 \
  --token "換成你的密碼" \
  --copy-mode all
```

`--copy-mode all` 會對 LINE 視窗執行 Ctrl+A/Ctrl+C，適合專用的中央訊號機。若仍想讀底部幾屏，可改成 `--copy-mode tail`。

macOS 中央電腦第一次使用時，需要在系統設定授權目前執行程式「螢幕錄製」和「輔助使用」。缺少螢幕錄製時程式會找不到 LINE 視窗；缺少輔助使用時 Cmd+A/C 無法送到 LINE。

Hub 儀表板：

```text
http://中央電腦IP:8765/?token=換成你的密碼
```

若會員端不在同一個網路，建議在一鍵版 `黃金訊號中心` 勾選 `Cloudflare Tunnel`。Windows 中央機先執行一次 `install_cloudflared_windows.bat`，再按開始；狀態紀錄會顯示 `https://...trycloudflare.com`，會員端 Hub URL 填這個公開網址。

## 2. 用戶端電腦

每台用戶電腦都要：

1. 安裝並登入自己的 MT5。
2. 把新版 `mt5_ea/MT5_File_Bridge_Enhanced.mq5` 編譯並掛到圖表上。
3. 啟動本機 MT5 agent：

```bash
python3 -m copy_trader.central.mt5_client_agent \
  --hub-url http://中央電腦IP:8765 \
  --token "換成你的密碼"
```

第一次啟動時 agent 預設從 Hub 目前最新序號開始，不會補下歷史訊號。若測試時要回放 Hub 內既有訊號，加上 `--replay`。

## 3. 設定來源

中央擷取器會沿用現有 `config.json` 裡的 `capture_windows`，也就是原本設定要監控的 LINE 群組視窗。

每台用戶端會沿用自己的本機設定：

- `mt5_files_dir`
- `default_lot_size`
- `symbol_name`
- `partial_close_ratios`
- `use_martingale`
- `martingale_per_source`
- `martingale_lots`
- `martingale_source_lots`

所以中央訊號只決定「方向、進場、SL、TP」；每位用戶的手數和馬丁邏輯仍由自己的電腦控制。

## 4. 網路與安全

Hub 目前是簡單 HTTP feed。實際對外使用時建議：

- Hub token 必須設定，且不要使用容易猜的字串。
- 只開放給用戶端固定 IP，或放在 VPN / Tailscale / ZeroTier 裡。
- 若跨公網部署，前面加 Nginx / Caddy 做 HTTPS。

## 5. 檔案

- `copy_trader/central/hub_server.py`：中央訊號 Hub。
- `copy_trader/central/signal_collector.py`：中央 LINE 全選複製擷取器。
- `copy_trader/central/mt5_client_agent.py`：用戶端 MT5 下單代理。
- `mt5_ea/MT5_File_Bridge_Enhanced.mq5`：已支援市價單與掛單的 MT5 bridge。
