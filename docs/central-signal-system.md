# 中央訊號系統

中央機從已登入的 LINE Desktop 本機資料庫讀取訊息，會員機只接收 Hub 事件並操作自己的 MT5。兩端皆使用瀏覽器控制台，不包含原生桌面 UI。

## 中央機

```bash
python -m copy_trader.central.central_signal_center_web
```

設定頁的重要欄位：

- `加密資料庫路徑`：留空會做有限範圍自動搜尋；多個候選時先用驗證工具找出唯一訊息 DB。
- `安全金鑰名稱`：macOS Keychain／Windows Credential Manager 預設均為 `line-db-research`。
- `聊天室設定`：JSON 陣列，`chat_name` 必須與 DB 完全一致，`trusted_senders` 採完整名稱比對。
- `雲端 Hub URL`：填入時直接發布到雲端；留空則在本機啟動 Hub。

範例：

```json
[
  {
    "name": "gold_signal_1",
    "chat_name": "（乘）黃金報單🈲言群",
    "display_name": "黃金報單🈲言群",
    "trusted_senders": ["乘", "James"]
  }
]
```

第一次連線只會把每個聊天室的游標設在當前最大 rowid，不會把既有歷史當成新單。之後只有成功處理／發布的列才會 acknowledge；網路失敗時同一列會重試，Hub 以精確 `event_id` 回傳原紀錄而不建立第二筆。

## 引用撤單

當 LINE 訊息是 relation type 3 的回覆／引用，而且正文是簡短撤單命令時：

1. 讀取 `_relatedMessageId` 與原始訊息正文。
2. 確認回覆者為原作者或設定中的 trusted sender。
3. 確認原始訊息來自 trusted sender 且可完整解析為報單。
4. 由 chat ID、原始 message ID 與訊號索引重建相同 `execution_id`。
5. 會員端只呼叫 `cancel_pending_order(execution_id)`。

已成交部位、其他方向或同群其他掛單都不會被撤掉。

## 會員端

```bash
python -m copy_trader.central.client_agent_web
```

會員端登入後從 Hub 最新序號開始，不補下歷史事件。重啟時會依 MT5 magic number 與 comment 認領本系統仍存在的掛單／持倉，使引用撤單與馬丁結果追蹤維持相同 identity。

## 唯讀與金鑰安全

- SQLite 以 `mode=ro`、`SQLITE_OPEN_READONLY`、`PRAGMA query_only=ON` 開啟。
- 金鑰只由 provider 取得，不存入 launcher settings、Hub payload 或 log。
- Web 測試 API 只回完整性、聊天室類型與 rowid，不回訊息正文。

Windows 首次設定、debugger 擷取注意事項與版本驗收表見 [windows-line-database.md](windows-line-database.md)；完整 provider/schema contract 見 [line-database-spec.md](line-database-spec.md)。
