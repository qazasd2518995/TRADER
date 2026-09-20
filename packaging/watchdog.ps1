# 黃金跟單守護 watchdog
# 訊號中心 / 會員端實例 / LINE / 掛機用的 MT5 當掉就自動拉起來。
# 由工作排程器每幾分鐘跑一次 + 登入時跑。
# 手動換版前放一個 pause.lock,守護就整輪不動作,避免跟換版程序打架。
#
# 這個檔案必須存成 UTF-8 with BOM —— 沒有 BOM 的話 powershell.exe 會用系統
# ANSI 碼頁讀,中文字串全部變亂碼,Log() 寫出來的東西沒人看得懂。
$ErrorActionPreference = 'SilentlyContinue'
$dir   = "$env:LOCALAPPDATA\黃金跟單守護"
$log   = Join-Path $dir 'watchdog.log'
$pause = Join-Path $dir 'pause.lock'

if (Test-Path $pause) { exit 0 }   # 更新中,暫停守護

function Log($m) {
  try { Add-Content -Path $log -Value ("{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) -Encoding UTF8 } catch {}
  # log 太大就砍半,免得無限長
  try { if ((Get-Item $log).Length -gt 500KB) { (Get-Content $log -Tail 200) | Set-Content $log -Encoding UTF8 } } catch {}
}

function Ensure-Alive($exe, $instance) {
  if (-not (Test-Path $exe)) { return }
  $name  = Split-Path $exe -Leaf
  $procs = @(Get-CimInstance Win32_Process -Filter "Name='$name'")
  $alive = $false
  foreach ($p in $procs) {
    if ($instance) {
      if ($p.CommandLine -match ("--instance\s+" + $instance + '(\D|$)')) { $alive = $true; break }
    } else {
      $alive = $true; break
    }
  }
  if (-not $alive) {
    $wd = Split-Path $exe
    if ($instance) { Start-Process -FilePath $exe -WorkingDirectory $wd -ArgumentList ("--instance " + $instance) }
    else           { Start-Process -FilePath $exe -WorkingDirectory $wd }
    Log ("重啟 " + $name + $(if ($instance) { " --instance $instance" } else { "" }))
  }
}

# 多個實例共用同一個執行檔名稱時,只比對名稱會把「有一台活著」誤判成「全都活著」。
# 四台 MT5 全叫 terminal64.exe,所以改用完整執行檔路徑辨識。
function Ensure-Alive-ByPath($exe, $argline, $label) {
  if (-not (Test-Path $exe)) { Log ("找不到執行檔,跳過: " + $exe); return }
  $name = Split-Path $exe -Leaf
  $alive = @(Get-CimInstance Win32_Process -Filter "Name='$name'" |
             Where-Object { $_.ExecutablePath -eq $exe }).Count -gt 0
  if (-not $alive) {
    $wd = Split-Path $exe
    if ($argline) { Start-Process -FilePath $exe -WorkingDirectory $wd -ArgumentList $argline }
    else          { Start-Process -FilePath $exe -WorkingDirectory $wd }
    Log ("重啟 " + $label)
  }
}

# 訊號中心(單一,無參數)
Ensure-Alive "C:\Users\zheng\黃金訊號中心\黃金訊號中心.exe" $null

# 五個會員端實例。5 是 Exness 實倉 —— 它原本不在這個清單裡,掛掉不會有人
# 拉起來,而那正是唯一一台有真錢的。2026-09-09 補上。
# 實例對應哪個帳號會變(換過好幾次),所以這裡不寫死註記以免過期誤導;
# 要查就看 %APPDATA%\黃金跟單系統\instance_N\member_session.json。
$member = "$env:LOCALAPPDATA\Programs\黃金跟單會員端\黃金跟單會員端.exe"
# 實例 1 刻意不在清單裡。它對應的 MT5(C:\Program Files\MetaTrader 5)是
# Lirunex 實倉 502009168 —— 那個帳戶由代理商的系統在操作,我們只讀它的成交
# 當訊號源,**絕對不能對它下單**。掛機端不跑,就沒有人會寫 commands.json。
foreach ($n in 2, 3, 4, 5, 6, 7) { Ensure-Alive $member $n }

# LINE 本體。2026-09-11 補上。
#
# 訊號**來源**跟訊號**出口**都是它:訊號中心讀的是 LINE 桌面版寫出來的
# 加密資料庫,社群播報又是驅動它的視窗發訊息。LINE 沒開 = 整套又瞎又啞。
# 那天重開機之後 LINE 沒有自動啟動(它不在啟動資料夾也不在 Run 機碼),
# 而守護當時只顧訊號中心跟會員端 —— 於是系統「看起來全部運行中」,實際上
# 十三個小時收不到任何訊號,沒有任何一行日誌說不對勁。
#
# 注意 LINE 重開之後,社群播報需要的那個聊天室要是獨立視窗。LINE 通常會自己
# 還原,沒還原的話訊號中心的啟動自檢會寫一行「聊天視窗目前沒開」。
Ensure-Alive-ByPath "$env:LOCALAPPDATA\LINE\bin\current\LINE.exe" $null "LINE"

# 掛機用的 MT5。2026-09-11 補上,同一次重開機事故 —— 會員端全部活著並正常
# 回報,但每一台都是 mt5_not_running:訊號收得到、發得出去、社群也播得出去,
# 就是沒有任何一台會真的下單,包含唯一有真錢的那台。
#
# **一定要帶 /portable。** 少了它 MT5 會改用 %APPDATA%\MetaQuotes 當資料夾,
# 橋接 EA 就把 account_info.json 寫到那邊去 —— 行程活著、圖表正常,但會員端
# 讀的是 D:\MT5-N\MQL5\Files,永遠等不到檔案更新,看起來就像 EA 壞了。
#
# 光是「行程活著」不代表橋接活著。2026-09-21 實測:MT5-6/7 跟來源帳戶三台
# 都成功啟動並登入券商,但終端日誌裡連一行 expert loaded 都沒有 —— profile
# 裡那張掛著 EA 的圖表不見了,於是行程在跑、帳號連著、橋接檔卻停在開機那一刻。
# 所以重啟一律帶 /config: 把 EA 釘回圖表上。代號不寫死:直接讀那台自己的
# symbol_info.json(EA 上次用的代號),換券商換帳號都不必改這支程式。
function Start-MT5($dir, $label) {
  $exe = Join-Path $dir 'terminal64.exe'
  if (-not (Test-Path $exe)) { Log ("找不到終端,跳過: " + $exe); return }
  $alive = @(Get-CimInstance Win32_Process -Filter "Name='terminal64.exe'" |
             Where-Object { $_.ExecutablePath -eq $exe }).Count -gt 0
  if ($alive) { return }
  $startArgs = @('/portable')
  $sym = $null
  try {
    $si = Join-Path $dir 'MQL5\Files\symbol_info.json'
    if (Test-Path $si) {
      $rawsi = Get-Content $si -Raw -ErrorAction Stop
      if ($rawsi -match '"symbol"\s*:\s*"([^"]+)"') { $sym = $Matches[1] }
    }
  } catch {}
  if ($sym) {
    $ini = Join-Path $dir 'watchdog_start.ini'
    # 這個檔案**不含任何帳密** —— 登入由 MT5 自己記住(KeepPrivate)。
    $body = "[Experts]`r`nEnabled=1`r`nAccount=1`r`nProfile=1`r`n`r`n[StartUp]`r`nExpert=MT5_File_Bridge_Enhanced`r`nSymbol=$sym`r`nPeriod=M1"
    try { $body | Out-File -FilePath $ini -Encoding ascii -ErrorAction Stop; $startArgs += "/config:$ini" } catch {}
  }
  Start-Process -FilePath $exe -WorkingDirectory $dir -ArgumentList $startArgs
  Log ("重啟 " + $label + $(if ($sym) { " (EA 釘回 $sym)" } else { " (沒有代號資訊,只帶 /portable)" }))
}

# 6、7 是 2026-09-21 補的:那次重開機之後這兩台整整五小時沒人拉起來,而實例 6
# 的會員端明明活著 —— 又是一次「看起來全部運行中」。
foreach ($n in 2, 3, 4, 5, 6, 7) {
  Start-MT5 "D:\MT5-$n" "MT5-$n"
}

# 來源帳戶的終端(Lirunex 實倉 502009168)。**這台是超高頻訊號的來源。**
#
# 它以前不在清單裡,理由是「不是拿來掛機的」—— 那句話只對了一半:我們確實
# 永遠不對它下單(靠的是不跑實例 1,沒有人會寫它的 commands.json),但我們**要
# 讀它**。2026-09-21 發現它已經死了 6.8 天,超高頻整整一週沒有任何訊號來源,
# 而且沒有任何一行日誌說不對勁。
#
# 再說一次:這裡只重啟「終端」。實例 1 依然刻意不在上面的會員端清單裡。
Start-MT5 "C:\Program Files\MetaTrader 5" "來源帳戶終端(超高頻訊號源,唯讀)"
