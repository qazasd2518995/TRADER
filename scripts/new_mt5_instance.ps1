# 複製一個乾淨的可攜式 MT5 實例目錄。
#
# 為什麼用複製而不是重新安裝：Profiles 一起帶過去，新的那台開起來就已經是
# 黃金圖表 + 掛好 File Bridge EA 的狀態，省掉最容易漏做的兩步。
# 代價是必須把舊帳號的殘留清乾淨，這支腳本就是在做那件事。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\new_mt5_instance.ps1 `
#     -Source "D:\MT5-5" -Target "D:\MT5-6"
#
# 做完之後：
#   1. 啟動 D:\MT5-6\terminal64.exe /portable
#   2. 登入要用的帳號（不會有舊帳號可選，accounts.dat 已清掉）
#   3. 確認黃金圖表的品種對、EA 是笑臉、AutoTrading 綠燈
#   4. 到訊號中心的「本機掛機端」按「＋ 新增掛機端」，填 D:\MT5-6\MQL5\Files
#
# 第 4 步會再驗證一次那台 MT5 真的活著、而且沒有跟別的實例撞資料夾。

[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$Source,
  [Parameter(Mandatory=$true)][string]$Target
)

$ErrorActionPreference = 'Stop'

function Say($m) { Write-Host "  $m" }

if (-not (Test-Path (Join-Path $Source 'terminal64.exe'))) {
  throw "來源不像 MT5 目錄（找不到 terminal64.exe）：$Source"
}
if (Test-Path $Target) {
  throw "目標已存在，換一個名字或先自己刪掉：$Target"
}

# 來源那台還開著的話，Config 與 bases 正在被寫入，複製出來的狀態是半舊半新的。
$running = Get-CimInstance Win32_Process -Filter "Name='terminal64.exe'" |
           Where-Object { $_.CommandLine -like "*$Source*" }
if ($running) {
  throw "來源 MT5 還在執行中（pid $($running.ProcessId)）。先關掉它再複製，" +
        "否則複製到的是寫到一半的設定檔。"
}

Say "從 $Source 複製到 $Target …"

# /XD 排除整個目錄；bases 是行情歷史(百來 MB，MT5 會自己重抓)，logs 沒有帶過去
# 的意義。MQL5\Files 是橋接檔 —— **一定要排除**：舊帳號的 account_info.json
# 留著的話，建立掛機端時的「那台 MT5 活著嗎」驗證會讀到假資料而誤判成已就緒。
$excludeDirs = @(
  (Join-Path $Source 'bases'),
  (Join-Path $Source 'logs'),
  (Join-Path $Source 'MQL5\Files'),
  (Join-Path $Source 'MQL5\Logs'),
  (Join-Path $Source 'Tester')
)
robocopy $Source $Target /E /XD @excludeDirs /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy 失敗，代碼 $LASTEXITCODE" }

# accounts.dat 存的是「登入過哪些帳號」。不清的話新實例一開就自動連上舊帳號 ——
# 那正是「兩個掛機端對同一個 MT5 帳戶重複下單」最容易發生的路徑。
$accounts = Join-Path $Target 'Config\accounts.dat'
if (Test-Path $accounts) {
  Remove-Item $accounts -Force
  Say "已清除 Config\accounts.dat（不會自動登入舊帳號）"
}

# 空的 Files 目錄要留著，EA 第一次寫入才不會失敗
New-Item -ItemType Directory -Path (Join-Path $Target 'MQL5\Files') -Force | Out-Null

$size = "{0:N0} MB" -f ((Get-ChildItem $Target -Recurse -File -ErrorAction SilentlyContinue |
                         Measure-Object Length -Sum).Sum / 1MB)
Say "完成，$Target（$size）"
Write-Host ""
Write-Host "接下來："
Write-Host "  1. 啟動  $Target\terminal64.exe /portable"
Write-Host "  2. 登入要用的帳號"
Write-Host "  3. 確認黃金圖表品種正確、EA 笑臉、AutoTrading 綠燈"
Write-Host "  4. 訊號中心 →「本機掛機端」→「＋ 新增掛機端」"
Write-Host "     填 $Target\MQL5\Files"
Write-Host ""
Write-Host "注意：圖表版面是從來源複製過來的，如果新帳號是**不同券商**，"
Write-Host "      黃金的品種代號會不一樣（例如 XAUUSD.s vs XAUUSD247m），"
Write-Host "      圖表會變空白，要自己換成新券商的代號再重掛 EA。"
