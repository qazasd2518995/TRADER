# Windows LINE 資料庫設定與實機驗收

這份文件給第一次把 TRADER 移到 Windows 10／11 的操作者。請只研究自己已登入、自己有權存取的 LINE 帳號與本機資料。

> 目前 macOS codec 與 schema 已實機驗證；Windows 的搜尋、Credential Manager 與 provider 架構已完成，但必須依本文件在目標 Windows LINE 版本做一次實機驗收。不要把「找到 `.edb`」誤當成「已成功解密」。

## 1. 準備環境

1. 使用平常登入 LINE 的同一個 Windows 使用者，不要切到另一個系統管理員帳號。
2. 安裝 Windows Desktop LINE，登入後開啟目標聊天室，確認訊息已載入本機。
3. 安裝 Python 3.12 x64，Clone 本專案。
4. 在 PowerShell 執行：

```powershell
cd C:\path\to\TRADER
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

LINE 官方的「Save chat」只保證儲存目前畫面已載入的訊息，不能取代本系統需要的持續資料庫事件來源，參考 [LINE Help：Backing up chats as text files](https://help.line.me/line/android/?contentId=20007388)。

## 2. 自動尋找資料庫

先執行：

```powershell
python -m copy_trader.line_db.windows_setup find
```

工具只檢查下列有限位置，不會遞迴掃描整顆磁碟：

```text
一般 Win32 Desktop：
%LOCALAPPDATA%\LINE\Data\db\

舊 Microsoft Store/UWP：
%LOCALAPPDATA%\Packages\<名稱含 LINE 的 package>\AppData\LINE\Data\db\

可能的 MSIX 資料根：
%LOCALAPPDATA%\Packages\<LINE package>\LocalState\...
%LOCALAPPDATA%\Packages\<LINE package>\LocalCache\...
```

Windows 封裝應用的持久資料通常位於 package 的 `LocalState`，虛擬化檔案也可能進入 `LocalCache`；這是 Windows 的封裝規則，不代表每一版 LINE 一定採用相同子目錄。參考 [Microsoft MSIX troubleshooting guide](https://learn.microsoft.com/en-us/windows/msix/msix-troubleshooting-guide#runtime-and-virtualization-behavior)。

也可以按 `Win + R`，逐一貼上：

```text
%LOCALAPPDATA%\LINE\Data\db
%LOCALAPPDATA%\Packages
```

檔案總管若看不到 `AppData` 是正常的，直接在路徑列貼 `%LOCALAPPDATA%` 即可。

### 有多個 `.edb` 時

- 不要根據副檔名猜；`.edb` 不代表一定是 LINE 訊息 SQLite。
- 優先記錄檔名以 `qw` 開頭、大小、修改時間與所在 package。
- 等金鑰存好後，讓 `verify` 對每個候選實際檢查 codec、integrity 與 schema。
- 只有一個候選通過時，才把該完整路徑填進 Web 控制台。

## 3. 取得目前 Windows LINE 的 DB key

Key 與 LINE 版本／帳號有關。專案不內建固定 key，也不能從教授的 Android `android_id` 公式推導 Windows key。

目前建議在自己的測試機用 x64dbg 觀察 LINE 開啟 DB 時傳入 SQLite codec 的參數。x64dbg 是開源 Windows user-mode debugger，請只從其[官方 GitHub](https://github.com/x64dbg/x64dbg)下載。

以下是「驗證用研究程序」，不是保證每版 LINE 都匯出相同符號：

1. 在工作管理員確認實際持有 `.edb` handle 的 LINE process；一般版 launcher 可能再啟動子 process。
2. 關閉 LINE，以對應架構的 x64dbg 開啟實際 LINE executable。大多數新版使用 x64；不確定時用 `x96dbg.exe` 選擇。
3. 在 Modules／Symbols 搜尋 `sqlite3_key_v2` 或 `sqlite3_key`。若完全沒有符號，先停止，不要照地址硬套其他版本教學。
4. 在 key function 設 breakpoint，重新啟動／登入，讓 LINE 第一次開啟訊息 DB。
5. breakpoint 命中時依 Windows x64 calling convention 檢查：

```text
sqlite3_key_v2(db, dbName, keyPointer, keyLength)
  R8  = keyPointer
  R9D = keyLength

sqlite3_key(db, keyPointer, keyLength)
  RDX = keyPointer
  R8D = keyLength
```

Microsoft 的 x64 ABI 規定前四個 integer／pointer 參數依序位於 `RCX`、`RDX`、`R8`、`R9`，參考 [Microsoft Learn：x64 calling convention](https://learn.microsoft.com/en-us/cpp/build/x64-calling-convention)。

6. 只讀取 `keyLength` 指定的 bytes：

   - 16 個 raw bytes：逐 byte 轉成兩位 hex，最後應為 32 hex。
   - 32 個 ASCII hex characters：直接保存這 32 字元。
   - 長度或內容不是以上兩種：不可截斷、不可猜；記錄版本與模組，視為該版需要新的 capture adapter。

7. 關閉 debugger，不要把 key 貼到聊天室、Issue、README、截圖、PowerShell command line 或 Git。

若 function 在啟動早期已執行，attach 到已登入 process 會錯過；應從 debugger 啟動 LINE。若 LINE 更新後 breakpoint 不再命中，要重新做版本研究，不能沿用舊位址。

## 4. 安全保存 key

不要把 key 寫進 `central_web_launcher_settings.json`。使用不回顯輸入的設定工具：

```powershell
python -m copy_trader.line_db.windows_setup store-key
```

工具會要求輸入兩次，然後寫入目前使用者 Windows Credential Manager，預設 target：

```text
line-db-research
```

程式使用 `CRED_TYPE_GENERIC` 與 `CRED_PERSIST_LOCAL_MACHINE`；Microsoft 文件說明這類 credential 由應用程式定義 blob，並只對同一台機器上的同一使用者後續登入 session 可見，參考 [CREDENTIALW](https://learn.microsoft.com/en-us/windows/win32/api/wincred/ns-wincred-credentialw)與 [CredWriteW](https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credwritew)。

Web 控制台的「安全金鑰名稱」必須與 target 一致。若日後要刪除：

```powershell
python -m copy_trader.line_db.windows_setup delete-key
```

`LINE_DB_KEY` 環境變數仍可作一次性開發測試，而且優先於 Credential Manager；正式常駐不建議使用，因為它比 Credential Manager 更容易被其他程序或診斷輸出看到。

## 5. 驗證候選資料庫

自動測試全部候選：

```powershell
python -m copy_trader.line_db.windows_setup verify
```

或明確指定：

```powershell
python -m copy_trader.line_db.windows_setup `
  --database "$env:LOCALAPPDATA\LINE\Data\db\qwbxxxxxxxx.edb" `
  verify
```

通過條件：

```text
[OK] ...：解密、完整性與訊息 schema 均通過
```

`verify` 也會檢查收回同步需要的 `_rev`、`_type`、`_attribute`、`_eventInfo` 與 `_contentMetadata`；缺少任一欄位都不能宣稱支援收回。

工具不列出聊天室名稱、訊息正文或 key。成功後會印出應填進 Web 控制台的完整 DB 路徑。

如果所有候選都失敗，依序判斷：

1. DB 是否屬於目前登入的 LINE 帳號。
2. debugger 擷取的是不是開啟「訊息 DB」那次呼叫，而非其他 SQLite DB。
3. key 是 16 raw bytes 轉 32 hex，還是本來就是 32 ASCII hex。
4. Windows LINE 是否改用不同 cipher／legacy/page size。
5. schema 是否不再包含 `_message`。

第 4、5 項必須新增版本化 provider 或 schema adapter，不能在 production 逐一暴力猜 codec。

## 6. 啟動 Web 控制台

```powershell
python -m copy_trader.central.central_signal_center_web
```

設定頁填寫：

```text
加密資料庫路徑：verify 唯一通過的完整 .edb 路徑
安全金鑰名稱：line-db-research
聊天室設定：chat_name 必須與 LINE DB 內完整名稱一致
```

也可以：

1. 按「自動尋找資料庫」。
2. 只有一個可判定候選時，畫面會自動填入。
3. 按「測試 LINE 資料庫」。
4. 看到 `integrity=ok` 與正確聊天室數量後才按「開始」。
5. 第一次實機驗收先勾選 `Shadow mode（只解析、不發布）`；確認一至兩週後才關閉。

預設聊天室：

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

`焦點利潤(yuyu)` 是既有高頻交易的產品來源 key。Windows 驗收時要確認測試結果同時找到兩個聊天室；不要誤選名稱相近但不是禁言群的 `Focus forex 焦點利潤`。

## 7. Windows 實機測試順序

不要一開始就連正式 MT5。依序測：

1. `find`：候選位置正確。
2. `store-key`：Credential Manager 保存成功。
3. `verify`：唯一 DB 通過。
4. Web DB test：聊天室解析成功。
5. 測試群發一筆新的完整報單：Hub 只出現一個 trade event。
6. 對該報單使用 LINE 回覆功能輸入「撤單」：Hub cancel event 從總帳取得正確 execution ID。
7. MT5 使用測試帳戶及最小手數：只刪該 pending ticket，且 4 秒確認前會員端 cursor 不前移。
8. 讓另一張單先成交，再引用回覆「撤單」：必須保留 position，不得送 close。
9. 發一張測試掛單後使用 LINE「收回」原訊息：一個輪詢週期內應出現 `cancel_reason=line_unsent`，且只刪該 pending ticket。
10. 重啟兩端：超過 300／180 秒的累積報單不得補下；舊撤單與三十天監看期內的收回仍能同步已發布掛單。

## 8. 故障排查

| 現象 | 處理 |
|---|---|
| `find` 找不到 | 確認使用 Desktop LINE、已登入且開過聊天室；手動檢查 `%LOCALAPPDATA%`；記錄實際路徑並擴充 discovery fixture |
| 找到多個候選 | 用 `verify`，不要以最大檔案或最新時間直接認定 |
| Credential 找不到 | 確認執行中央程式的 Windows 使用者與 `store-key` 相同，安全金鑰名稱一致 |
| key／codec 失敗 | 重新確認 function、pointer、length 與 LINE 版本；不要把錯誤 key 寫進文件 |
| 可解密但 schema 不符 | 保存去識別化 table／column 名稱，新增 Windows schema adapter |
| 找不到聊天室 | `chat_name` 必須完全一致，包含全形符號與 emoji |
| LINE 更新後突然失敗 | 記錄舊／新版號、DB path、cipher 驗證結果；重新擷取 key 並跑完整驗收 |

## 9. 版本驗收紀錄模板

每次 Windows LINE 大版本更新，新增一筆去識別化紀錄：

```text
Windows build：
LINE version／安裝來源（官網或 Store）：
LINE executable path（不含使用者名稱）：
DB layout 類型：windows_desktop / windows_package
DB filename pattern：
key API：sqlite3_key / sqlite3_key_v2 / other
key representation：16 raw bytes / 32 ASCII hex
cipher profile：
required tables：PASS / FAIL
reply relation type 3：PASS / FAIL
exact pending cancel：PASS / FAIL
filled position protected：PASS / FAIL
驗收日期與 commit：
```

架構與資料契約見 [line-database-spec.md](line-database-spec.md)。
