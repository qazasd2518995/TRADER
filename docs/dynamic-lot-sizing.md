# 本金比例動態手數規格

## 產品與權限

- 設定名稱：`本金比例`（內部模式 `risk_percent`）。
- 僅旗艦版 `dynamic_lot=true` 可啟用。
- 體驗、基礎、進階版仍看得到「本金比例（需旗艦版）」選項，但選項為灰色且不可選。
- 會員端後端會再次檢查 entitlement；直接修改 `settings.json` 也會被強制改回均注。
- 這是每個訊號來源各自設定的模式，中頻、高頻、超高頻及未來接上的低頻互不共用設定。低頻訊號源目前仍在建置，設定會保留但不會憑空產生交易。

## 計算

每則訊號抵達會員電腦時，使用會員自己的 MT5 帳戶與券商商品規格計算：

```text
可用本金 = min(balance, equity)
本筆風險金額 = 可用本金 × risk_percent / 100
每手停損金額 = abs(entry - stop_loss) / tick_size × tick_value_loss
原始手數 = 本筆風險金額 / 每手停損金額
實際手數 = floor(原始手數 / volume_step) × volume_step
```

採用 `min(balance, equity)` 的效果：已實現獲利增加 balance 後才放大；浮動虧損使 equity 下降時立即縮小；不會拿尚未實現的浮盈放大下一筆交易。

掛單用訊號的實際掛單價；市價單買進用本地券商 ask、賣出用 bid。`tick_value_loss` 由最新版 Bridge EA 寫入 `symbol_info.json`，舊版檔案則相容回退 `tick_value`。

## 邊界與失敗規則

- UI 預設每筆風險 `0.5%`，允許 `0.01%`～`5%`；後端也強制上限 5%。
- 一律向下對齊券商 `volume_step`，不會四捨五入而超過會員設定的風險。
- 最低手數為 `max(0.01, broker volume_min)`。計算結果不足最低手數時略過該訊號，不會硬補到 0.01。
- 最高手數不超過券商 `volume_max`。
- `account_info.json` 超過 15 秒未更新、MT5 未連線、缺少報價或商品規格時，視為暫時錯誤：不下單、不前移 Hub cursor，資料恢復後重試。
- 缺少 SL、進場價與 SL 相同、或計算結果低於最低手數，視為該訊號的永久風控拒絕：不下單，但會前移該事件 cursor，避免卡死後續訊號。
- 本金比例模式不使用、也不更新馬丁層級。

## Windows 上線要求

請使用本版本的 `mt5_ea/MT5_File_Bridge_Enhanced.mq5` 重新在 MetaEditor 編譯並掛到 XAUUSD 圖表。Bridge 應持續產生：

- `account_info.json`：balance、equity、連線狀態。
- `symbol_info.json`：volume min/max/step、tick size、tick value loss。
- `XAUUSD*_price.json`：市價單使用的 bid / ask。

動態手數只控制最大預估停損金額，無法消除滑價、跳空、點差、佣金或券商拒單；實際虧損仍可能高於公式值。
