# 中央訊號系統

中央機從已登入的 LINE Desktop 本機資料庫讀取訊息，會員機只接收 Hub 事件並操作自己的 MT5。兩端皆使用瀏覽器控制台，不包含原生桌面 UI。

## 中央機

```bash
python -m copy_trader.central.central_signal_center_web
```

設定頁的重要欄位：

- `加密資料庫路徑`：留空會做有限範圍自動搜尋；多個候選時先用驗證工具找出唯一訊息 DB。
- `安全金鑰名稱`：macOS Keychain／Windows Credential Manager 預設均為 `line-db-research`。
- `聊天室設定`：JSON 陣列；第一次以 `chat_name`／`trusted_senders` 綁定穩定內部 ID，後續名稱只供顯示。
- `雲端 Hub URL`：填入時直接發布到雲端；留空則在本機啟動 Hub。
- `Shadow mode`：走完整 DB／parser／總帳，但不發布 Hub；Windows 初次串接時建議先開啟。

範例：

```json
[
  {
    "name": "gold_signal_1",
    "chat_name": "（乘）黃金報單🈲言群",
    "display_name": "黃金報單🈲言群",
    "trusted_senders": ["乘", "James"],
    "parser_profile": "mid_frequency_v1",
    "max_trade_age_seconds": 300,
    "recall_watch_seconds": 2592000
  },
  {
    "name": "high_freq_yuyu",
    "chat_name": "🈲禁言群🈲 Focus forex 焦點利潤",
    "display_name": "焦點利潤(yuyu)",
    "trusted_senders": ["yuyu（yu__o822"],
    "parser_profile": "yuyu_range_v1",
    "max_trade_age_seconds": 180,
    "recall_watch_seconds": 2592000
  }
]
```

`display_name` 是 Hub、會員等級與每來源交易設定共用的穩定來源 key：`黃金報單🈲言群` 對應中頻交易，`焦點利潤(yuyu)` 對應高頻交易，不可任意改成聊天室顯示名稱。

從最初只含「乘」的 LINE DB 版本升級時，若已存設定仍完全等於當時的單一聊天室預設，中央機會在記憶體中自動補上 yuyu。任何自訂聊天室 JSON 都不會被自動修改。

第一次連線只會把每個聊天室的游標設在當前最大 rowid，不會把既有歷史當成新單，同時把唯一 chat/sender ID 綁定保存在本機 cursor state。之後只有成功處理／發布的列才會 acknowledge；網路失敗時同一列會重試，Hub 以精確 `event_id` 回傳原紀錄而不建立第二筆。

正常即時訊息直接處理；重啟後累積而超過來源上限的報單會記入總帳但不補下，並由 LINE Bot 顯示過期原因。舊撤單不受報單時效限制，仍可撤銷總帳內已發布且 MT5 尚未成交的指定訂單。

## 未掛單原因通知

可信任訊號源發布具備報單特徵、但無法安全解析的訊息時，中央端會發布只供通知／稽核的 `signal_rejected` 事件。LINE Bot 會顯示來源、訊息時間、可辨識的方向與點位，以及未掛單原因，例如點位關係錯誤、缺少進場價、缺少 SL／TP、同則多單或訊號過期。這種事件不會轉成訂單；會員端只前移 Hub cursor 後繼續處理下一則訊號。

一般行情評論即使提到「多／空」，只要沒有 SL 與 TP 的報單骨架就不通知。拒絕事件使用穩定 event ID，因此中央端重試不會讓 Bot 重複推播。

## yuyu 百點誤植修正

高頻 yuyu 若只有進場、SL 或整組 TP 其中一個欄位家族落在相鄰百點，系統會嘗試 `±100`，但只有唯一候選同時符合來源歷史中的完整排列（SL／TP1 距離 4–15 點、三個 TP 等距 5 或 10 點）才會修正並發布。修正前後點位會保存到總帳及 Hub，LINE Bot 也會明示「原值 → 修正值」。沒有唯一解就維持 `signal_rejected`，不猜測下單。

此功能只套用 `yuyu_range_v1`；中頻來源的距離分布較廣，不自動套用。`TP 4480/4485/4490` 搭配 `SL 4469` 支持的是進場 4474；`TP 4380/4385/4390` 搭配進場 4374 才支持 SL 4369。

Shadow mode 會把結果記為 `shadow_accepted`／`shadow_cancel`，但不會建立 Hub event。它適合正式交易前的一至兩週人工對照；關閉後才會開始發布新訊息，Shadow 期間的歷史不會補下。

## 引用撤單

當 LINE 訊息是 relation type 3 的回覆／引用，而且正文是簡短撤單命令時：

1. 讀取 `_relatedMessageId`，且正文必須完整等於明確撤單命令。
2. 以穩定 sender ID 確認回覆者與被引用作者均受信任。
3. 直接從 `line_message_ledger.sqlite3` 查出原報單發布時保存的 execution IDs。
4. 發布 exact cancel event；不重新解析原文。
5. 會員端只呼叫 `cancel_pending_order(execution_id)`。

寫出 pending-delete 只算 `COMMAND_SENT`。會員端會等待 MT5 掛單消失並通過 4 秒確認才前移 Hub cursor；拒絕會重試，若已成交則標成 `ALREADY_FILLED` 並保留部位。其他方向或同群其他掛單都不會被撤掉。

## 訊息收回

LINE 收回不是新增 row，而可能把原 row 更新為 `_rev=2`。中央端會對三十天內、總帳中仍為已發布狀態的訊息重新讀 metadata；同時看到 `UNSENT=true`、event type 20、message type 3、attribute 1 與 revision 增加時，直接用原 message ID 查總帳並發布 `cancel_reason=line_unsent`。

收回後正文已不存在，但系統仍知道它原本對應的方向、進場、SL／TP、execution ID 與 Hub sequence。這些結構化資料足以精確取消掛單，不需要保存完整聊天室正文。

Web／Hub 顯示的 `recall_detected_at` 是本機偵測時間。正常一秒輪詢時，收回發生時間可縮小在前後約一秒的觀察區間；中央端離線時無法知道歷史上精確按下收回的分鐘，只能在重連後立刻同步最終狀態。

## 會員端

```bash
python -m copy_trader.central.client_agent_web
```

會員端登入後從 Hub 最新序號開始，不補下歷史事件。重啟時會依 MT5 magic number 與 comment 認領本系統仍存在的掛單／持倉，使引用撤單與馬丁結果追蹤維持相同 identity。

旗艦版可為每個來源選擇「本金比例」動態手數；完整公式、0.01 手下限與失敗處理見 [`dynamic-lot-sizing.md`](dynamic-lot-sizing.md)。

來源與會員方案的固定路由：

- `黃金報單🈲言群`：乘的中頻交易，體驗版、基礎版、進階版與旗艦版可接收。
- `焦點利潤(yuyu)`：yuyu 的高頻交易，只有進階版與旗艦版可接收。
- Hub 在伺服器端先依會員方案過濾，會員端無法用修改本機設定取得未授權來源。
- 會員端會採用 Hub 的原始分頁 cursor；即使整頁都是被過濾的高頻訊號，基礎會員也能繼續前進並收到後續中頻訊號。
- 會員面板會依登入方案預先列出已授權來源，不必等第一筆成交後才出現。

## 唯讀與金鑰安全

- SQLite 以 `mode=ro`、`SQLITE_OPEN_READONLY`、`PRAGMA query_only=ON` 開啟。
- 金鑰只由 provider 取得，不存入 launcher settings、Hub payload 或 log。
- Web 測試 API 只回完整性、聊天室類型與 rowid，不回訊息正文。

Windows 首次設定、debugger 擷取注意事項與版本驗收表見 [windows-line-database.md](windows-line-database.md)；完整 provider/schema contract 見 [line-database-spec.md](line-database-spec.md)。
