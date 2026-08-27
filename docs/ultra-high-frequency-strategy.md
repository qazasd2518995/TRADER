# 超高頻交易第三來源規格

版本：1.0（`ultra_confluence_v1`）
狀態：可產生真實掛單事件；預設關閉，尚未通過正期望驗證。

## 1. 身分與邊界

第三來源的固定產品 key 是：

```text
超高頻交易
```

它不是 LINE 群組，也不冒用乘或 yuyu：

- 不讀 LINE DB、不帶 `line_chat_id`／`line_message_id`。
- `source=超高頻交易`、`source_name=ultra_confluence_v1`。
- execution ID 使用 `copy_uhf_*`，與 `copy_ln_*` 完全分離。
- Hub、會員端來源開關、MT5 `signal_sources.json`、績效卡都以這個來源獨立統計。
- 只有旗艦版 entitlement 收得到；體驗／基礎／進階方案在 Hub 端就會被濾掉。

中央端與會員端是雙重開關。中央端未勾「啟用實單訊號」時不產生新事件；會員端
未勾「超高頻交易」時，即使 Hub 有事件也不下單。這裡沒有 shadow 分支：兩邊都
開啟後就是實際寫入 MT5 的掛單。

## 2. 資料契約

中央機必須另外開一個 MT5，並在 XAUUSD 圖表掛上本專案最新版
`MT5_File_Bridge_Enhanced.mq5`：

```text
ExportChartData = true
ChartBarCount   = 400（不得低於 400）
EnableTrading   = 可關；中央 MT5 只負責提供行情
```

策略每個新收完的 M1 bar 只評估一次，輸入為：

- `rates_M1.json`：最近六小時水平位重測與 M1 收復。
- `rates_M15.json`：8／21 EMA 方向。
- `rates_H1.json`：8／21 EMA、斜率、ATR(14)。
- `<SYMBOL>_price.json`：中央券商即時 bid／ask 與 spread。
- `symbol_info.json`：券商商品名稱與 digits。

形成中的 K 線不參與判斷。即時價檔或最後已收 M1 超過 90 秒、缺少原生
M15/H1、資料根數不足或 spread 超過設定值時，一律不發布。

## 3. `ultra_confluence_v1` 規則

研究過程曾同時測試順勢回踩與流動性反轉；反轉分支在時間序列回測中失敗，正式程式
已移除，只留下較小、可解釋的順勢候選：

1. H1 ATR(14) 必須在 4–60 美元。
2. ATR < 25 時使用 5 美元網格；ATR ≥ 25 時使用 10 美元網格。
3. H1 與 M15 的 EMA(8)／EMA(21) 差及短期斜率必須同向。
4. 最近 15 分鐘的位移絕對值必須小於 0.35 × H1 ATR，避免追逐已過度延伸行情。
5. 最近 15 分鐘高低範圍至少 0.35 × H1 ATR，排除沒有活動的盤整。
6. 候選網格在前六小時必須出現 2–4 個、彼此相隔至少八分鐘的觸碰 episode。
7. 最後一根 M1 必須碰到該網格並向趨勢方向收回；目前價與 Entry 的距離必須介於
   `max(0.30, 2 × spread)` 和 `0.55 × grid`。
8. 只發布 Buy Limit／Sell Limit，不發布市價單，也不允許穿價後轉成 stop 單。

風險距離：

```text
risk = clamp(0.35 × H1 ATR, grid, 1.5 × grid)，再四捨五入到 0.5 美元
SL   = Entry ± 1R
TP   = Entry ± [1R, 2R, 3R]
```

事件的 `strategy` 欄保存 setup、ATR、grid、重測次數、H1/M15 方向、15 分鐘位移、
15 分鐘範圍、spread、掃掠深度與 K 棒收盤位置。這些是可稽核特徵，不是虛構的
confidence 分數。

## 4. 訊號生命週期

中央端同一時間只維護一張尚未逾時的超高頻候選；預設 20 分鐘未成交就發布：

```text
type=cancel_signal
source=超高頻交易
cancel_reason=strategy_expired
target_execution_ids=[原 copy_uhf ID]
```

會員端沿用既有精確撤單狀態機。若仍是 pending 就刪除指定 ticket；若已成交則標記
`ALREADY_FILLED` 並保留部位，絕不把「掛單逾時」解讀成平倉。

會員端還會在真正寫 MT5 指令前執行：

- `expires_at` 已過：略過。
- 本地券商價格已穿 Entry 或拿不到即時價：略過。
- MT5 bridge 收到 `pending_order_type=limit`，若價格在寫檔後才穿越，拒絕而不改成 stop。
- 預設同來源活動中訂單最多 1 張。
- 預設每日接受最多 12 張。
- 預設同來源當日已實現虧損達 25 帳戶幣別後停止新單。
- 預設固定 0.01 手、均注、保本移損；不沿用乘／yuyu 的馬丁層級。

每日單數使用 execution ID 持久化冪等計數；重送相同事件不會重複占用額度。每日損益
從 `closed_trades.json` 依 position 去除分批成交重複後，再以 `signal_sources.json`
歸回本來源。

## 5. 目前回測證據

可重跑工具：

```powershell
python scripts\backtest_ultra_strategy.py `
  --dukascopy 2026-01-01 2026-08-26 `
  --spread 0.40 `
  --cache-dir .cache\dukascopy-xau
```

模擬使用逐分鐘 BID K、固定 0.40 美元 spread、20 分鐘掛單期限、同時一張活動單、
TP1 後移保本／TP2 後移到 TP1；同一根 M1 同時碰到 SL 與 TP 時保守地先算 SL。

第一個寬鬆版本（順勢＋反轉）在完整期間：

```text
1,351 筆已平倉
勝 296／輸 757／保本 298
淨值 -153R
Profit factor 0.798
```

移除反轉並收緊為目前最小規則後，以時間先後切分：

```text
訓練／規格期 2026-01-01～2026-04-30：334 筆，-4R，PF 0.978
後段檢查期   2026-05-01～2026-08-26：346 筆，+1R，PF 1.006
```

完整期間不在四月底重設未平倉狀態時，共 684 筆已平倉（勝 170／輸 367／保本 147），
正收益筆數占 24.85%，淨值 -5R，PF 0.986，最大回撤 37R。

這兩段合起來接近零期望，不能宣稱策略已獲利或已有可靠勝率。回測也未包含滑價、
隔夜成本、不同會員券商報價差、斷線與新聞跳空；Dukascopy 歷史價不等於會員的 MT5
成交價。因此中央開關預設為關，會員來源也預設為關。

## 6. 如果仍要做最小實盤驗證

這是執行步驟，不是獲利建議：

1. 中央端填「中央 MT5 Files 路徑」，確認新版 EA 已產生 M1/M15/H1 與即時價。
2. 中央端保留每日 12 張、20 分鐘撤單、spread 1.20 的上限，勾「啟用實單訊號」。
3. 測試會員必須是旗艦版；在來源表確認「超高頻交易」為均注 0.01、同時 1 張、每日
   12 張、每日虧損 25，再親自打開跟單。
4. 重新編譯並載入最新版 MT5 bridge；舊 EA 不認 `pending_order_type=limit` 的硬語意。
5. 只以面板中 `source=超高頻交易` 的實際已平倉結果計算勝率、PF、平均 R、最大回撤，
   不和中頻、高頻或其他 EA 合併。
6. 任一筆變成 stop／market、同時超過一張、逾時未撤或來源歸類錯誤，立即關閉中央
   開關與會員來源，保存去識別化 log 後修正。

在實盤樣本證明正期望前，不應提高 0.01 手、不應開馬丁，也不應把「訊號數增加」當成
策略品質提高。
