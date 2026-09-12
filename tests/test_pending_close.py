"""平倉要盯到部位真的消失，不是寫出指令就算數。

2026-09-11 的事故有兩層。第一層是指令槽被覆蓋（見 test_mirror_collector 的
CommandSlotTests）。修掉之後五筆平倉指令 EA 全部收到、全部執行 —— 然後券商
全部退件：

    Trade logged: close FAIL ... RetCode: 10018 Detail: Market closed

我們這邊看到的只有「commands.json 寫成功」，於是把它們當成平完了。部位就
這樣抱著過週末。`_close_position` 拿不到券商的結果，所以唯一可靠的判準是
**它還在不在 positions.json 裡**。

這條路上的部位沒有 SL 也沒有 TP，漏掉一次出場就沒有第二道防線 —— 所以這裡
刻意不設放棄條件。
"""
from __future__ import annotations

import json
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from copy_trader.trade_manager.manager import TradeManager


def _manager(tmp: Path) -> TradeManager:
    m = TradeManager.__new__(TradeManager)
    m.mt5_files_dir = tmp
    m.commands_file = tmp / "commands.json"
    m.positions_file = tmp / "positions.json"
    m._pending_closes = {}
    m._lock = threading.Lock()
    m.COMMAND_SLOT_TIMEOUT = 0.3
    return m


def _write_positions(tmp: Path, tickets):
    (tmp / "positions.json").write_text(
        json.dumps({"timestamp": int(time.time()),
                    "positions": [{"ticket": t} for t in tickets]}),
        encoding="utf-8")


def _free_slot(tmp: Path):
    (tmp / "commands.json").write_text("{}", encoding="utf-8")


class RetryTests(unittest.TestCase):
    def test_close_is_resent_while_the_position_is_still_open(self):
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            m = _manager(d)
            _free_slot(d)
            _write_positions(d, [555])
            m._register_pending_close(555, "sig", "mirror")   # noqa: SLF001
            m._pending_closes[555]["next_at"] = 0             # 到期
            _free_slot(d)
            m._retry_pending_closes()                         # noqa: SLF001
            sent = json.loads((d / "commands.json").read_text(encoding="utf-8"))
            self.assertEqual(sent, {"action": "close", "ticket": 555})
            self.assertEqual(m._pending_closes[555]["attempts"], 2)

    def test_it_stops_once_the_position_is_gone(self):
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            m = _manager(d)
            _free_slot(d)
            _write_positions(d, [])                           # 真的平掉了
            m._register_pending_close(555, "sig", "mirror")   # noqa: SLF001
            m._pending_closes[555]["next_at"] = 0
            m._retry_pending_closes()                         # noqa: SLF001
            self.assertEqual(m._pending_closes, {})
            # 沒有再送任何指令
            self.assertEqual((d / "commands.json").read_text(encoding="utf-8"), "{}")

    def test_unreadable_positions_file_does_not_clear_the_queue(self):
        """讀不到就下一輪再說 —— 當成「都平掉了」會讓部位永遠留著。"""
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            m = _manager(d)
            _free_slot(d)
            (d / "positions.json").write_text('{"positions":[{"tick',
                                              encoding="utf-8")
            m._register_pending_close(555, "sig", "mirror")   # noqa: SLF001
            m._pending_closes[555]["next_at"] = 0
            m._retry_pending_closes()                         # noqa: SLF001
            self.assertIn(555, m._pending_closes)

    def test_backoff_grows_and_then_caps(self):
        with TemporaryDirectory() as tmp:
            m = _manager(Path(tmp))
            delays = [m._retry_delay(n) for n in range(1, 12)]   # noqa: SLF001
            self.assertEqual(delays[0], 10.0)
            self.assertTrue(all(b >= a for a, b in zip(delays, delays[1:])),
                            delays)
            self.assertEqual(delays[-1], max(m.CLOSE_RETRY_BACKOFF))

    def test_a_busy_command_slot_is_not_counted_as_an_attempt(self):
        """指令槽忙不是券商退件，不該把重試間隔一路退避成 5 分鐘。"""
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            m = _manager(d)
            _write_positions(d, [555])
            (d / "commands.json").write_text('{"action":"buy"}', encoding="utf-8")
            m._register_pending_close(555, "sig", "mirror")   # noqa: SLF001
            before = m._pending_closes[555]["attempts"]
            m._pending_closes[555]["next_at"] = 0
            m._retry_pending_closes()                         # noqa: SLF001
            self.assertEqual(m._pending_closes[555]["attempts"], before)
            self.assertLess(m._pending_closes[555]["next_at"], time.time() + 5)
            # 前一筆指令必須原封不動
            self.assertIn("buy", (d / "commands.json").read_text(encoding="utf-8"))

    def test_nothing_happens_before_the_retry_is_due(self):
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            m = _manager(d)
            _free_slot(d)
            _write_positions(d, [555])
            m._register_pending_close(555, "sig", "mirror")   # noqa: SLF001
            m._retry_pending_closes()                         # noqa: SLF001
            self.assertEqual((d / "commands.json").read_text(encoding="utf-8"), "{}")
            self.assertEqual(m._pending_closes[555]["attempts"], 1)

    def test_empty_queue_does_not_read_the_positions_file(self):
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            m = _manager(d)
            m._retry_pending_closes()                         # noqa: SLF001
            self.assertFalse((d / "positions.json").exists())


if __name__ == "__main__":
    unittest.main()
