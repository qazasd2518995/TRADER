#!/usr/bin/env python
"""手動發一筆測試訊號到 Hub（以及把它撤回）。

平常訊號只從 LINE 進來，沒有手動發布的路徑。要驗證「Hub → 會員端 → MT5 下單」
整條鏈路時就需要這支。

**這支腳本會讓會員端的 MT5 真的去掛單。** 所以預設是乾跑（只印出要送什麼），
必須自己加 --send 才會真的發出去。

用法：
  # 1. 先看要送什麼（不會送出）
  python scripts/send_test_signal.py

  # 2. 確認無誤再真的送出
  python scripts/send_test_signal.py --send

  # 3. 測完把單撤掉（execution_id 由上一步印出來）
  python scripts/send_test_signal.py --cancel test-20260907-153000 --send

價位預設離市價 300 美元（買單掛在市價下方），故意遠到不會成交。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

SETTINGS = Path.home() / "AppData/Roaming/黃金跟單系統/central_web_launcher_settings.json"
LAST_BAR = Path(__file__).resolve().parents[1] / "data/market_history/XAUUSD247m_M1.json"

# 必須跟 membership.py 的來源顯示名稱一字不差，否則 Hub 會把它從會員的
# 訊號流裡濾掉（沒買那個來源的會員本來就不該收到）。
MID_FREQ = "黃金報單🈲言群"
HIGH_FREQ = "焦點利潤(yuyu)"


def load_hub() -> tuple[str, str]:
    if not SETTINGS.exists():
        sys.exit(f"找不到訊號中心設定檔：{SETTINGS}")
    cfg = json.loads(SETTINGS.read_text(encoding="utf-8"))
    url = (cfg.get("hub_url") or "").rstrip("/")
    token = cfg.get("token") or ""
    if not url or not token:
        sys.exit("設定檔裡沒有 hub_url 或 token")
    return url, token


def last_close() -> float:
    """最後一根 M1 的收盤價。休市時這是上一個交易日的收盤，拿來抓距離夠用。"""
    try:
        data = json.loads(LAST_BAR.read_text(encoding="utf-8"))
        bars = data if isinstance(data, list) else (data.get("bars") or data.get("rates") or [])
        return float(bars[-1]["c"])
    except Exception:                                   # noqa: BLE001
        return 0.0


def post(url: str, token: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{url}/signals",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_trade(args, price: float) -> dict:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    exec_id = f"test-{stamp}"
    buy = args.direction == "buy"
    entry = args.entry if args.entry else round(
        price - args.distance if buy else price + args.distance, 2)
    # 停損再往外 10 元、止盈往市價方向 10 元 —— 純粹讓欄位有值，反正掛在
    # 300 元外根本不會成交。
    sl = args.sl if args.sl else round(entry - 10 if buy else entry + 10, 2)
    tp = args.tp if args.tp else round(entry + 10 if buy else entry - 10, 2)
    return {
        "event_id": f"test-event-{stamp}",
        "type": "trade_signal",
        "execution_id": exec_id,
        "source": args.source,
        "source_name": "manual_test",
        "sender": "manual_test",
        "line_chat_id": "manual-test",
        "line_message_id": exec_id,
        "line_rowid": 0,
        "line_revision": 1,
        "message_time": datetime.now().astimezone().isoformat(),
        "signal_index": 0,
        "signal": {
            "symbol": "XAUUSD",
            "direction": args.direction,
            "entry_price": entry,
            "is_market_order": False,
            "stop_loss": sl,
            "take_profit": [tp],
            "lot_size": None,
            "parse_status": "ok",
            "parse_method": "manual_test",
            "raw_text_summary": f"【測試訊號 請勿當真】{args.direction} {entry}",
            "error": None,
        },
    }


def build_cancel(args) -> dict:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return {
        "event_id": f"test-cancel-{stamp}",
        "type": "cancel_signal",
        "source": args.source,
        "source_name": "manual_test",
        "line_chat_id": "manual-test",
        "line_message_id": args.cancel,
        "target_line_message_id": args.cancel,
        "target_execution_ids": [args.cancel],
        "target_signals": [],
        "cancel_reason": args.reason,
        "line_revision": 2,
        "message_time": datetime.now().astimezone().isoformat(),
        "recall_detected_at": datetime.now().astimezone().isoformat(),
        "recall_observation_window_started_at": "",
        "recall_time_source": "manual_test",
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="發一筆測試訊號（預設只預覽）")
    ap.add_argument("--send", action="store_true",
                    help="真的送出。不加這個就只印出內容，不碰 Hub。")
    ap.add_argument("--direction", choices=["buy", "sell"], default="buy")
    ap.add_argument("--distance", type=float, default=300.0,
                    help="離市價多遠（美元），預設 300")
    ap.add_argument("--entry", type=float, help="直接指定進場價，蓋過 --distance")
    ap.add_argument("--sl", type=float)
    ap.add_argument("--tp", type=float)
    ap.add_argument("--source", default=MID_FREQ,
                    help=f"訊號來源顯示名稱，預設「{MID_FREQ}」")
    ap.add_argument("--cancel", metavar="EXECUTION_ID",
                    help="改成發撤單，撤掉指定的 execution_id")
    ap.add_argument("--reason", default="manual_test_cleanup",
                    help="撤單原因，會寫進帳本與 LINE 通知。"
                         "撤來源重貼的重複單請用 duplicate_signal")
    args = ap.parse_args()

    url, token = load_hub()
    price = last_close()
    payload = build_cancel(args) if args.cancel else build_trade(args, price)

    print(f"Hub      : {url}")
    print(f"最後收盤 : {price}")
    print(f"動作     : {'撤單' if args.cancel else '掛單'}")
    print("-" * 60)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print("-" * 60)

    if not args.send:
        print("這是乾跑，什麼都沒送出。確認無誤後加 --send 才會真的發布。")
        return

    result = post(url, token, payload)
    print("Hub 回應 :", json.dumps(result, ensure_ascii=False))
    if not args.cancel:
        print()
        print(f"測完記得撤掉：")
        print(f"  python scripts/send_test_signal.py "
              f"--cancel {payload['execution_id']} --send")


if __name__ == "__main__":
    main()
