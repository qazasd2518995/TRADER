"""部位鏡像：把別人操作的帳戶即時抄成超高頻訊號。

來源是代理商在伺服器端操作的帳戶，我們只讀它的 positions.json。這裡釘住的
每一條，都是「抄錯比抄不到嚴重」的情境：

  1. 啟動當下已經開著的部位不能追 —— 那是過去式，現在用市價追進去，
     跟原單的進場價可能差很遠
  2. 讀到寫到一半的檔案不能當成「全部平倉」—— 那會把所有鏡像部位一次砍光
  3. 部位消失要連看兩輪才算平倉 —— 同上，EA 寫檔跟我們讀檔沒有同步
  4. 開倉訊號必須是市價、而且明確帶 managed_exit —— 來源沒有 SL/TP，
     沒有那個旗標會被會員端當成解析失敗擋掉
  5. execution_id 要由 ticket 決定 —— 重啟之後平倉事件才對得回當初那張單
"""
from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from copy_trader.central.mirror_collector import (
    MAX_ENTRY_AGE_SECONDS,
    VANISH_CONFIRM_ROUNDS,
    MirrorCollector,
)


def _pos(ticket: int, *, type_="buy", volume=0.02, price=4350.0,
         comment="", magic=0, opened=None):
    return {
        "ticket": ticket, "symbol": "XAUUSD.r", "type": type_, "volume": volume,
        "price_open": price, "price_current": price, "sl": 0.0, "tp": 0.0,
        "profit": 0.0, "swap": 0.0, "magic": magic, "comment": comment,
        "time_open": "2026.09.11 16:00",
        "time_open_timestamp": int(opened if opened is not None else time.time()),
    }


class _Dir:
    """一個假的 MQL5\\Files 資料夾。"""

    def __init__(self, tmp: str):
        self.path = Path(tmp)
        self.write_account()

    def write_account(self, offset: float = 0.0):
        # timestamp = 伺服器時間；檔案 mtime = 真實時間。兩者差 = 時區偏移。
        (self.path / "account_info.json").write_text(
            json.dumps({"login": 1, "timestamp": int(time.time() + offset)}),
            encoding="utf-8")

    def write_positions(self, rows):
        (self.path / "positions.json").write_text(
            json.dumps({"timestamp": int(time.time()), "positions": rows}),
            encoding="utf-8")

    def write_raw(self, text: str):
        (self.path / "positions.json").write_text(text, encoding="utf-8")


def _collector(d: _Dir, sent: list, **kw) -> MirrorCollector:
    return MirrorCollector(d.path, sent.append, source="超高頻交易",
                           symbol="XAUUSD", **kw)


class PrimingTests(unittest.TestCase):
    def test_first_cycle_records_without_chasing(self):
        with TemporaryDirectory() as tmp:
            d = _Dir(tmp); sent = []
            d.write_positions([_pos(101), _pos(102)])
            c = _collector(d, sent)
            self.assertEqual(c.run_cycle(), 0)
            self.assertEqual(sent, [])
            self.assertEqual(set(c.state.known), {101, 102})

    def test_positions_opened_after_priming_are_mirrored(self):
        with TemporaryDirectory() as tmp:
            d = _Dir(tmp); sent = []
            d.write_positions([_pos(101)])
            c = _collector(d, sent)
            c.run_cycle()
            d.write_positions([_pos(101), _pos(202, type_="sell")])
            self.assertEqual(c.run_cycle(), 1)
            self.assertEqual(len(sent), 1)
            ev = sent[0]
        self.assertEqual(ev["type"], "trade_signal")
        self.assertEqual(ev["signal"]["direction"], "sell")
        self.assertTrue(ev["signal"]["is_market_order"])
        self.assertIsNone(ev["signal"]["entry_price"])
        self.assertIsNone(ev["signal"]["stop_loss"])
        self.assertEqual(ev["signal"]["take_profit"], [])
        self.assertTrue(ev["managed_exit"], "沒有這個旗標會被會員端當成解析失敗擋掉")


class CloseDetectionTests(unittest.TestCase):
    def test_close_needs_two_consecutive_rounds(self):
        with TemporaryDirectory() as tmp:
            d = _Dir(tmp); sent = []
            d.write_positions([])
            c = _collector(d, sent)
            c.run_cycle()
            d.write_positions([_pos(303)])
            c.run_cycle()
            sent.clear()
            d.write_positions([])                  # 第一次消失
            self.assertEqual(c.run_cycle(), 0, "只看一輪就平倉會被半截檔案騙")
            self.assertEqual(sent, [])
            self.assertEqual(c.run_cycle(), 1)     # 第二次才確認
            ev = sent[0]
        self.assertEqual(ev["type"], "close_signal")
        self.assertEqual(ev["target_execution_ids"], ["mirror-ultra-303"])

    def test_reappearing_position_is_not_closed(self):
        """讀到半截檔案之後又讀到完整的，不能把部位誤砍。"""
        with TemporaryDirectory() as tmp:
            d = _Dir(tmp); sent = []
            d.write_positions([_pos(404)])
            c = _collector(d, sent)
            c.run_cycle(); sent.clear()
            d.write_positions([])                  # 消失一輪
            c.run_cycle()
            d.write_positions([_pos(404)])         # 又出現
            c.run_cycle()
            d.write_positions([])                  # 再消失一輪
            self.assertEqual(c.run_cycle(), 0, "計數要被重置")
            self.assertEqual(sent, [])

    def test_unreadable_file_does_nothing(self):
        """讀到寫到一半的 JSON 不能當成「全部平倉」。"""
        with TemporaryDirectory() as tmp:
            d = _Dir(tmp); sent = []
            d.write_positions([_pos(505), _pos(506)])
            c = _collector(d, sent)
            c.run_cycle(); sent.clear()
            d.write_raw('{"timestamp":1789,"positions":[{"ticket":50')
            for _ in range(5):
                self.assertEqual(c.run_cycle(), 0)
            self.assertEqual(sent, [])
            self.assertEqual(set(c.state.known), {505, 506})


class StaleEntryTests(unittest.TestCase):
    def test_old_position_is_not_chased(self):
        with TemporaryDirectory() as tmp:
            d = _Dir(tmp); sent = []
            d.write_positions([])
            c = _collector(d, sent)
            c.run_cycle()
            old = time.time() - (MAX_ENTRY_AGE_SECONDS + 120)
            d.write_positions([_pos(606, opened=old)])
            self.assertEqual(c.run_cycle(), 0)
            self.assertEqual(sent, [])
            # 但它要被記住，否則下一輪又會被當成新部位
            self.assertIn(606, c.state.known)

    def test_fresh_position_is_chased(self):
        with TemporaryDirectory() as tmp:
            d = _Dir(tmp); sent = []
            d.write_positions([])
            c = _collector(d, sent)
            c.run_cycle()
            d.write_positions([_pos(707, opened=time.time() - 5)])
            self.assertEqual(c.run_cycle(), 1)


class FilterTests(unittest.TestCase):
    def test_comment_filter_skips_other_trades(self):
        with TemporaryDirectory() as tmp:
            d = _Dir(tmp); sent = []
            d.write_positions([])
            c = _collector(d, sent, comment_filter="copy_")
            c.run_cycle()
            d.write_positions([_pos(808, comment="manual"), _pos(809, comment="copy_x")])
            c.run_cycle()
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0]["mirror"]["ticket"], 809)

    def test_magic_filter(self):
        with TemporaryDirectory() as tmp:
            d = _Dir(tmp); sent = []
            d.write_positions([])
            c = _collector(d, sent, magic_filter=777)
            c.run_cycle()
            d.write_positions([_pos(900, magic=1), _pos(901, magic=777)])
            c.run_cycle()
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0]["mirror"]["ticket"], 901)


class ExecutionIdTests(unittest.TestCase):
    def test_execution_id_is_derived_from_ticket(self):
        """重啟之後要算得出同一個 ID，平倉事件才對得回當初那張單。"""
        with TemporaryDirectory() as tmp:
            d = _Dir(tmp)
            a = _collector(d, [])
            b = _collector(d, [])
            self.assertEqual(a.execution_id(12345), b.execution_id(12345))
            self.assertNotEqual(a.execution_id(12345), a.execution_id(12346))


if __name__ == "__main__":
    unittest.main()
