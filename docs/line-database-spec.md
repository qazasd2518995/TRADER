# LINE 本機資料庫串接規格

版本：1.0

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
  每個 database_id + chat_id 保存 rowid high-water mark

signal_collector.py
  完整報單檢查、建立 event/execution identity、發布 Hub

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
- `resolve_chats(targets)`：以聊天室完整名稱解析唯一 chat ID；不存在或重名皆拒絕。
- `latest_rowid(chat)`：取得聊天室目前 high-water mark。
- `fetch_after(chat, rowid, limit)`：只讀取 `rowid > cursor`，同聊天室依 rowid 遞增。
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

如果 Windows schema 名稱不同，先新增 schema adapter 與 fixture 測試，不得用欄位位置或猜測式 fallback。

## 6. 游標與 identity

- 第一次啟動將每個聊天室 cursor baseline 到目前最大 rowid，不回放既有歷史。
- Hub 發布成功後才 acknowledge 該 rowid；失敗時同列重試。
- DB 被替換、latest rowid 小於 cursor 時重新 baseline，不能把舊列當新單。
- `event_id = hash(chat_id, message_id, event_type, index)`，供 Hub 傳輸冪等。
- `execution_id = hash(chat_id, original_message_id, signal_index)`，供 MT5 精確定位。
- 這些是 exact identity，不是以文字、時間或方向判斷的模糊去重。

## 7. 引用撤單規格

只有同時符合下列條件才發布 `cancel_signal`：

1. LINE row 是 relation type 3，且 `_relatedMessageId` 非空。
2. 回覆正文是短撤單命令，不是疑問句、SL／TP 修改或新報單。
3. 回覆者是原作者，或在 `trusted_senders` 中。
4. 被引用訊息作者受信任，正文仍能完整解析成報單。
5. 由被引用 message ID 重建原本相同的 execution ID。

會員端只能對這些 exact IDs 執行 pending delete：

- 找到已成交 position：視為事件已處理，保留部位。
- pending 尚未取得 MT5 ticket：Hub seq 不前移，下一輪重試。
- 找到指定 pending ticket：只送 `action=delete`。
- 不可回退成「同方向」、「最近一單」或「最接近價格」。

## 8. 安全要求

- 原始 DB 永遠只讀；Web 測試 API 不回傳訊息正文或 key。
- macOS key 存 Keychain；Windows key 存目前使用者的 Credential Manager。
- `LINE_DB_KEY` 只作開發／CI override，不建議長期存成使用者環境變數。
- `line_db_cursor.json`、launcher settings、log、issue、commit 與截圖不得包含 key。
- 發布前執行秘密掃描與 `git status --untracked-files=all`。

## 9. Windows 驗收條件

Windows 支援只有在真實機器完成以下項目後才能標記為「已驗證」：

1. `find` 能找到登入中帳號的候選 `.edb`。
2. Credential Manager 寫入／讀回正常，key 不出現在 log 與設定 JSON。
3. `verify` 回報解密、integrity 與最低 schema 全部通過。
4. Web「測試 LINE 資料庫」能解析指定聊天室。
5. 新報單只發布一次。
6. 真實 LINE 引用訊息能取得原作者、原文與 `_relatedMessageId`。
7. 引用「撤單」只刪除該報單 pending ticket。
8. 對已成交單回覆「撤單」不會送出 close。
9. 重啟中央端與會員端後不回放舊訊號，且仍能認領同 execution ID。

完整 Windows 操作程序見 [windows-line-database.md](windows-line-database.md)。
