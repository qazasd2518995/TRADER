"""Browser control-panel entry point for the member MT5 client app.

多開：同一台電腦要跑兩個跟單帳號時，第二份用 `--instance 2` 啟動，資料目錄會
分流到 `%APPDATA%\\黃金跟單系統\\instance_2\\`，settings / state / port /
config.json / log 全部獨立。不加參數時路徑與以前完全相同。
"""

import os
import sys


def _apply_instance_from_argv() -> None:
    """把 --instance <名稱> 轉成環境變數 COPY_TRADER_INSTANCE。

    必須在 import web_launcher (會連帶 import config) 之前跑完 —— config.DATA_DIR
    是模組載入時就算好的常數，等到 import 之後再設環境變數已經來不及。
    """
    if os.environ.get("COPY_TRADER_INSTANCE"):
        return  # 已由外部環境指定，不覆蓋
    argv = sys.argv[1:]
    for i, arg in enumerate(argv):
        if arg == "--instance" and i + 1 < len(argv):
            os.environ["COPY_TRADER_INSTANCE"] = argv[i + 1]
            return
        if arg.startswith("--instance="):
            os.environ["COPY_TRADER_INSTANCE"] = arg.split("=", 1)[1]
            return


_apply_instance_from_argv()

from copy_trader.central.web_launcher import main  # noqa: E402  (必須排在上面之後)


if __name__ == "__main__":
    main("client")
