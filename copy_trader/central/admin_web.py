"""管理端控制台的進入點。

跟訊號中心用同一支 web_launcher，但角色是 admin —— 沒有 LINE 擷取、沒有
超高頻策略、start_service() 直接擋死。這不是 UI 上的隱藏，是結構上就發不了
訊號：兩台同時對同一個 Hub 發單，會員就會重複下單。

管理端只需要兩個設定：Hub 網址與管理 token。會員資料全部來自雲端 Hub，
跟這台機器上有沒有 MT5、有沒有 LINE 完全無關。
"""

from copy_trader.central.web_launcher import main


if __name__ == "__main__":
    main("admin")
