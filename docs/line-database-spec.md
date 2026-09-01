# LINE 本機資料庫串接規格

版本：2.1

用途：TRADER 中央訊號機唯讀擷取目前 Windows／macOS 使用者已登入的 LINE Desktop 聊天資料。

## 1. 邊界與非目標

- 只讀取操作者本人、目前作業系統帳號可存取的本機 LINE Desktop 資料。
- 不登入其他人的帳號、不連線到 LINE 私有 API、不修改 LINE DB。
- 不把 DB key、完整訊息或聊天室內部 ID 寫入 log。
- 不用 OCR、截圖、剪貼簿或模糊文字去重。
- 教授的 [Android 研究文章](https://blog.csdn.net/qq_24280381/article/details/72854220)是舊 Android 版的欄位內容加密分析；其 `android_id` 衍生法不是 Windows／macOS Desktop DB 的規格。

## 2. 已驗證與待驗證矩陣

| 項目 | macOS | Windows |
|---|---|---|
| DB 自動定位 | 已在目前機器驗證 | 已實作 Win32、UWP、MSIX 候選搜尋；待 Windows 實機驗收 |
| cipher | `aes128cbc`, `legacy=0`, `kdf_iter=1` 已驗證 | 先以同參數探測，不得在實機驗證前宣稱相容 |
| key store | macOS Keychain | Windows Credential Manager 已實作，待實機驗收 |
| 訊息 schema | `_message`、群組與 OpenChat 表已驗證 | 必須由 `verify` 指令確認 |
| 引用關係 | relation type 3 + `_relatedMessageId` 已驗證 | 必須用真實引用訊息驗證 |

AES-128-CBC codec 的定義以 [SQLite3 Multiple Ciphers 官方文件](https://utelle.github.io/SQLite3MultipleCiphers/docs/ciphers/cipher_aes128cbc/)為準。Windows 版本若驗證失敗，應新增 Windows provider／codec profile，不可在 collector 裡塞平台判斷。

## 3. 元件責任

```text
discovery.py
  找候選檔案；不開啟、不解密、不遞迴掃描整顆磁碟

keys.py + windows_credentials.py
  從 OS 安全儲存區取得 32-hex key；不記錄 key

sqlite_provider.py
  read-only 開啟、套用 codec、驗證 schema、執行參數化 SQL

source.py
  首次以名稱綁定穩定 chat/sender ID；保存 rowid high-water mark

signal_collector.py
  來源專屬嚴格解析、backlog 安全、建立 identity、發布 Hub

ledger.py
  保存 LINE message → execution/event/Hub sequence；不保存訊息正文

mt5_client_agent.py + trade_manager/manager.py
  依 exact execution_id 下單或刪除指定 pending order
```

平台移植只能修改 discovery、key provider 或 SQLite provider；聊天室模型、collector、Hub 和 MT5 identity 不應跟平台耦合。

## 4. Database provider contract

`SQLiteLineDatabaseProvider` 必須提供：

- `database_path`：已解析的單一檔案。
- `database_id`：由路徑與檔頭雜湊得到的非機密穩定識別。
- `connect()`：使用 `SQLITE_OPEN_READONLY`、URI `mode=ro` 與 `PRAGMA query_only=ON`。
- `integrity_check()`：回傳 `ok` 才能啟動 collector。
- `resolve_chats(targets)`：第一次以完整名稱解析唯一 chat ID；綁定後只以 chat ID 解析。
- `resolve_sender_ids(chat, names)`：第一次把受信任顯示名稱解析成唯一 sender ID；同名或找不到皆拒絕綁定。
- `latest_rowid(chat)`：取得聊天室目前 high-water mark。
- `fetch_after(chat, rowid, limit)`：只讀取 `rowid > cursor`，同聊天室依 rowid 遞增。
- `fetch_message_metadata(chat, ids)`：重新讀取 revision/status/event/content metadata 並只回正文雜湊；UNSENT 用於精確收回同步，reaction／一般 edit 仍只供診斷。
- `close()`：釋放連線。

目前 codec profile：

```sql
PRAGMA cipher='aes128cbc';
PRAGMA legacy=0;
PRAGMA kdf_iter=1;
PRAGMA key='<32-hex，由 key provider 提供>';
PRAGMA query_only=ON;
```

不得把 key 字串插入錯誤訊息或 debug log。除 `PRAGMA key` 外，所有動態查詢值必須使用 SQL parameters。

## 5. 最低支援 schema

訊息資料庫至少需要：

```text
_message
  rowid
  _id
  _chatId
  _createdTime
  _from
  _text
  _contentType
  _messageRelationType
  _relatedMessageId
  _rev
  _status
  _type
  _attribute
  _eventInfo
  _contentMetadata
  _reactionStatus

_squareChat 或 _groupChat
_squareMember 或 _contact
```

聊天室查找：

- OpenChat：`_squareChat._name` → `_squareChatMid`
- 一般群組：`_groupChat._chatName` → `_chatMid`

回覆／引用：

- `reply._messageRelationType == 3`
- `reply._relatedMessageId == original._id`
- 原訊息 join 條件同時包含相同 `_chatId`，避免跨聊天室誤連。
- collector 只 join 原訊息作者，不再讀取被引用正文；撤單目標完全由 `_relatedMessageId` 查總帳。

如果 Windows schema 名稱不同，先新增 schema adapter 與 fixture 測試，不得用欄位位置或猜測式 fallback。

## 6. 游標與 identity

- 第一次設定以 `chat_name`／`trusted_senders` 找到唯一對象，將 chat ID、chat kind 與 sender IDs 寫入 `line_db_cursor.json`。
- 綁定完成後，群組或使用者改名不影響授權；相同顯示名稱也不能冒用。顯示名稱只用於 UI。
- 第一次啟動將每個聊天室 cursor baseline 到目前最大 rowid，不回放既有歷史。
- Hub 發布成功後才 acknowledge 該 rowid；失敗時同列重試。
- DB 被替換、latest rowid 小於 cursor 時重新 baseline，不能把舊列當新單。
- `event_id = hash(chat_id, message_id, event_type, index)`，供 Hub 傳輸冪等。
- `execution_id = hash(chat_id, original_message_id, signal_index)`，供 MT5 精確定位。
- 這些是 exact identity，不是以文字、時間或方向判斷的模糊去重。
- `line_message_ledger.sqlite3` 保存當時解析狀態、execution ID、event ID 與 Hub sequence。只存正文 SHA-256 和 sender ID 雜湊，不存正文。

## 7. 嚴格解析與 backlog

每則 LINE row 預設最多一張訂單。正式 collector 不再使用 OCR 相容規則、最後一個方向、30 字距離切單或固定 confidence。

明確解析結果為：

- `accepted`
- `rejected_missing_entry`
- `rejected_invalid_geometry`
- `rejected_unknown_format`
- `manual_review`
- `rejected_stale_backlog`
- `shadow_accepted`／`shadow_cancel`（Shadow mode 稽核狀態）

來源 profile：

- `mid_frequency_v1`：中頻「乘」的 `Buy/Sell：價格`，以及有完整 SL／TP 的「價格附近多／空」。
- `yuyu_range_v1`：高頻 yuyu 的「價格-價格多／空」及已在歷史資料驗證過的單一價格版本。

`trusted_senders`／綁定後的 sender IDs 為空時採 fail-closed：可以讀 row 做診斷，但不會發布交易或撤單。

中頻報單預設最多補 300 秒，高頻最多 180 秒。超過限制的舊報單會記入總帳且不發布 `trade_signal`；系統只發布不具執行能力的 `signal_rejected`，讓 LINE Bot 說明「訊號已過期、未補下」。撤單訊息不套用此報單時效，若總帳中存在已發布 execution，仍會發布撤單以同步狀態。這是斷線安全閥，不是 OCR 時代的模糊過期判斷。

可信任供應者的訊息若具備報單骨架，但因點位幾何錯誤、缺少進場／SL／TP、同則多單或過期而無法執行，也會發布 `signal_rejected`。事件沿用 message ID 衍生的 exact `event_id`，HTTP 重送不會重複通知；會員端只前移 cursor、不建立 MT5 訂單。只有方向評論、一般聊天或非受信任發送者不會觸發未掛單通知，避免誤報與洗版。

Shadow mode 使用相同 DB、身分綁定、parser、幾何驗證與總帳，但 trade/cancel 都不發布 Hub。它不是另一套模擬 parser；正式接 MT5 前應用它比對一至兩週。

### 收回同步

2026-08-27 在目前兩個真實聊天室的唯讀驗證結果：

- 中頻 70 筆、高頻 152 筆收回樣本。
- 222 筆全部同時符合 `_contentMetadata.UNSENT=true`、`_eventInfo.type=20`、`_type=3`、`_attribute=1`。
- 其中 116 筆為 `_rev=2`，證明 LINE 會原地更新舊 row，不能只依 `rowid > cursor`。
- 收回後原 message ID 仍存在，可直接查總帳 execution IDs；正文則已清除。

collector 只重新檢查 `recall_watch_seconds` 期間內、曾發布成訂單的 message IDs，預設三十天。只有下列條件全部成立才發布 `cancel_signal(cancel_reason=line_unsent)`：

1. metadata 同時為 `UNSENT=true`、event type 20、message type 3、attribute 1。
2. 當前 revision 大於總帳保存的發布時 revision。
3. 總帳仍有該 message ID 的已發布 execution ID。
4. 同一 recall event ID 尚未處理。

資料庫沒有在已驗證欄位中保存可靠的「按下收回」時間：收回樣本的 `_createdTime` 與 `_deliveredTime` 都仍是原訊息時間且完全相同。系統因此分開保存：

- `message_time`：原訊息時間。
- `recall_detected_at`：collector 實際看到 UNSENT 的時間。
- `recall_observation_window_started_at`：上一次成功檢查時間；在線時可將收回時間限制在約一個輪詢週期內。
- `recall_time_source=database_poll_detection`：明確表示這不是 LINE 官方精確收回時間。

不得把 `_chat._lastUpdatedTime` 或目前 DB 檔案修改時間冒充單一訊息的收回時間。離線期間發生的收回只能確定在重新連線時已發生。

## 8. 引用／收回撤單規格

只有同時符合下列條件才發布 `cancel_signal`：

1. LINE row 是 relation type 3，且 `_relatedMessageId` 非空。
2. 移除空白／裝飾符號後，正文完整等於：`撤`、`撤單`、`撤掉`、`取消`、`取消掛單` 或 `全部撤單`。
3. 回覆者是原作者，或在 `trusted_senders` 中。
4. 被引用訊息作者的穩定 sender ID 受信任。
5. 總帳存在該 message ID 已發布的 execution ID；禁止重新解析被引用正文。

`這張不要撤`、`撤了嗎`、`如果還沒到要撤嗎`、`先撤` 等不完全相等的句子不得自動執行。

會員端只能對這些 exact IDs 執行 pending delete：

- 找到已成交 position：視為事件已處理，保留部位。
- pending 尚未取得 MT5 ticket：Hub seq 不前移，下一輪重試。
- 找到指定 pending ticket：只送 `action=delete`，並帶回相同 `trade_id` 供 EA 回報對帳。
- 寫出 delete 後保持 `COMMAND_SENT`，不能前移 Hub cursor。
- MT5 掛單連續消失 4 秒後才成為 `MT5_CONFIRMED`；EA／券商拒絕則進 `FAILED_RETRY`。
- 不可回退成「同方向」、「最近一單」或「最接近價格」。
- 收回與文字回覆共用相同 MT5 狀態機；若已成交，結果為 `ALREADY_FILLED`，不得平倉。

## 9. 安全要求

來源名稱是中央端、Hub 與會員端共同使用的授權合約：

- `黃金報單🈲言群` = 乘／中頻。
- `焦點利潤(yuyu)` = yuyu／高頻。
- `超高頻交易` = 獨立市場資料模型，不屬於 LINE collector，也不得帶 LINE identity。
- collector 的 `source`、會員方案的 `sources` 與會員端 `source_profiles` 必須使用完全相同的值。
- Hub 過濾來源後仍須回傳原始掃描 cursor；會員端只有在本批可見事件全數成功處理後才能前移到該 cursor，避免低方案會員被連續高頻事件卡住。

- 原始 DB 永遠只讀；Web 測試 API 不回傳訊息正文或 key。
- macOS key 存 Keychain；Windows key 存目前使用者的 Credential Manager。
- `LINE_DB_KEY` 只作開發／CI override，不建議長期存成使用者環境變數。
- `line_db_cursor.json` 含有綁定後的 chat/sender IDs，必須視為本機敏感狀態，不得上傳或分享。
- launcher settings、log、issue、commit 與截圖不得包含 key、訊息正文或內部 ID。
- 發布前執行秘密掃描與 `git status --untracked-files=all`。

## 10. Windows 驗收條件

Windows 支援只有在真實機器完成以下項目後才能標記為「已驗證」：

1. `find` 能找到登入中帳號的候選 `.edb`。
2. Credential Manager 寫入／讀回正常，key 不出現在 log 與設定 JSON。
3. `verify` 回報解密、integrity 與最低 schema 全部通過。
4. Web「測試 LINE 資料庫」能解析指定聊天室。
5. 新報單只發布一次。
6. 真實 LINE 引用訊息能取得原作者、原文與 `_relatedMessageId`。
7. 引用「撤單」由總帳命中原 execution ID，只刪除該 pending ticket。
8. 對已成交單回覆「撤單」不會送出 close。
9. delete 指令只有在 MT5 對帳確認後才前移會員端 cursor；拒絕時會重試。
10. 重啟中央端與會員端後不回放過期報單，且仍能認領同 execution ID。

完整 Windows 操作程序見 [windows-line-database.md](windows-line-database.md)。
