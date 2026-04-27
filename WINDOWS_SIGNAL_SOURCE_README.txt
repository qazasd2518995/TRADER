Windows 訊號源電腦使用方式
==========================

這個資料夾只用來建立「黃金訊號中心」，不包含會員端。

1. 先在 Windows 安裝 Python 3.10 以上，安裝時勾選 Add Python to PATH。
2. 若要產生正式安裝檔，先安裝 Inno Setup。
   沒安裝也可以，會產出可直接執行的 dist\黃金訊號中心 資料夾。
3. 雙擊 build_signal_center_windows.bat。
   如果視窗仍然消失，右鍵資料夾空白處開啟 Terminal / PowerShell，執行：
   cmd /k build_signal_center_windows.bat
   並把 build_signal_center_windows.log 傳回來。
4. 產出後優先使用：
   dist\installers\黃金訊號中心_安裝檔.exe
5. 如果沒有 installers，改用：
   dist\黃金訊號中心\黃金訊號中心.exe

中央訊號源電腦第一次使用：

1. 安裝並登入 LINE Desktop。
2. 打開要截取訊號的 LINE 群組。
3. 執行 install_cloudflared_windows.bat。
4. 開啟 黃金訊號中心。
5. 設定維持：
   Hub 監聽 IP：127.0.0.1
   Hub Port：8765
   複製模式：全選複製
   Cloudflare Tunnel：勾選
6. 按開始。
7. 狀態紀錄會顯示 https://xxxx.trycloudflare.com，會員端 Hub URL 填這個。

注意：

- Quick Tunnel 免費，但重新啟動後公開網址可能會換。
- Hub 密碼要給會員端填同一組。
- LINE 群組視窗標題要符合程式設定的群組名稱。
