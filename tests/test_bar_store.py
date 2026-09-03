"""滾動 K 線倉庫與「挑一台活的 MT5」。

影子對照的數字全建立在這份 K 線上，所以壞掉的方式都要測到：
覆蓋形成中的 K 線、寫檔中斷、以及最重要的 —— 設定指著一台已經關掉的
MT5 時不能就這樣算了（實際踩過，那台停了 5.9 天）。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from copy_trader.central import bar_store
from copy_trader.central.bar_store import (
    BarStore, detect_server_offset, pick_live_mt5_dir,
)


def _bar(t, o=1.0, h=2.0, l=0.5, c=1.5):
    return {"t": t, "o": o, "h": h, "l": l, "c": c}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "m1.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_ingest_and_query(self):
        s = BarStore(self.path)
        self.assertEqual(s.ingest([_bar(100), _bar(160)], "XAUUSD"), 2)
        self.assertEqual(s.count, 2)
        self.assertEqual([b["t"] for b in s.bars_since(0)], [100, 160])
        self.assertEqual([b["t"] for b in s.bars_since(160)], [160])

    def test_forming_bar_is_overwritten_not_duplicated(self):
        """最後一根還在形成，每次輪詢都會拿到更新版 —— 要覆蓋不是變兩根。"""
        s = BarStore(self.path)
        s.ingest([_bar(100, h=2.0)])
        self.assertEqual(s.ingest([_bar(100, h=9.0)]), 0)      # 不算新增
        self.assertEqual(s.count, 1)
        self.assertEqual(s.bars_since(0)[0]["h"], 9.0)

    def test_high_low_are_widened_to_contain_open_close(self):
        s = BarStore(self.path)
        s.ingest([{"t": 1, "o": 5.0, "h": 3.0, "l": 4.0, "c": 6.0}])
        row = s.bars_since(0)[0]
        self.assertEqual(row["h"], 6.0)
        self.assertEqual(row["l"], 4.0)

    def test_malformed_bars_are_skipped(self):
        s = BarStore(self.path)
        s.ingest([_bar(1), {"t": "x"}, {"nope": 1}, None])
        self.assertEqual(s.count, 1)

    def test_persist_and_reload(self):
        s = BarStore(self.path)
        s.ingest([_bar(100), _bar(160)], "XAUUSDm")
        self.assertTrue(s.flush(force=True))
        again = BarStore(self.path)
        self.assertEqual(again.count, 2)
        self.assertEqual(again.symbol, "XAUUSDm")

    def test_flush_is_rate_limited_unless_forced(self):
        now = [1000.0]
        s = BarStore(self.path, flush_interval=300.0, clock=lambda: now[0])
        s.ingest([_bar(1)])
        self.assertTrue(s.flush())                 # 第一次一定寫
        s.ingest([_bar(2)])
        self.assertFalse(s.flush())                # 還沒到間隔
        now[0] += 301
        self.assertTrue(s.flush())

    def test_corrupt_file_does_not_crash(self):
        self.path.write_text("not json at all", encoding="utf-8")
        s = BarStore(self.path)
        self.assertEqual(s.count, 0)
        s.ingest([_bar(1)])
        self.assertEqual(s.count, 1)

    def test_retention_drops_old_bars(self):
        s = BarStore(self.path, retention_days=1.0)
        s.ingest([_bar(0), _bar(86400 * 2)])
        self.assertEqual([b["t"] for b in s.bars_since(0)], [86400 * 2])

    def test_covers_reports_whether_the_window_is_complete(self):
        s = BarStore(self.path)
        s.ingest([_bar(0), _bar(3600)])
        self.assertTrue(s.covers(0, 1.0))
        self.assertFalse(s.covers(0, 2.0))
        self.assertFalse(BarStore(self.path.with_name("empty.json")).covers(0, 1.0))


class ServerTimeOffsetTests(unittest.TestCase):
    """K 線時間戳是券商伺服器時間，不同券商差好幾小時。

    這台機器上 Exness 三台快 3 小時、另一家是 0。訊號時間是真實 epoch，
    不換算就會靜靜地錯開三小時 —— 成交率和勝率會變成隨機數字，而且完全
    看不出哪裡不對。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _terminal(self, offset_sec, now=1_788_458_700):
        import os
        d = self.root / f"mt5_{int(offset_sec)}"
        d.mkdir(parents=True, exist_ok=True)
        newest = int(now + offset_sec)
        newest -= newest % 60                       # 形成中那根的分鐘起點
        bars = [{"t": newest - 60 * i, "o": 1, "h": 2, "l": 0.5, "c": 1.5}
                for i in range(5)]
        f = d / "rates_M1.json"
        f.write_text(json.dumps({"symbol": "XAUUSD", "bars": bars}), encoding="utf-8")
        os.utime(f, (now, now))                     # 檔案時間 = 真實當下
        return d

    def test_detects_a_three_hour_broker_offset(self):
        self.assertEqual(detect_server_offset(self._terminal(3 * 3600)), 10800.0)

    def test_detects_no_offset(self):
        self.assertEqual(detect_server_offset(self._terminal(0)), 0.0)

    def test_returns_none_when_it_cannot_measure(self):
        """量不出來就要說量不出來，不能猜一個把倉庫弄髒。"""
        self.assertIsNone(detect_server_offset(self.root / "missing"))
        bad = self.root / "bad"
        bad.mkdir()
        (bad / "rates_M1.json").write_text("{}", encoding="utf-8")
        self.assertIsNone(detect_server_offset(bad))

    def test_ingest_shifts_bars_back_to_real_time(self):
        s = BarStore(self.root / "m1.json")
        s.ingest([{"t": 1000 + 10800, "o": 1, "h": 2, "l": 0.5, "c": 1.5}],
                 "XAUUSD", offset_sec=10800.0)
        self.assertEqual(s.bars_since(0)[0]["t"], 1000)

    def test_two_terminals_land_on_one_timeline(self):
        """換終端不能讓倉庫裡混著兩種時基。"""
        s = BarStore(self.root / "m1.json")
        s.ingest([{"t": 1000 + 10800, "o": 1, "h": 2, "l": 0.5, "c": 1.5}],
                 offset_sec=10800.0)
        s.ingest([{"t": 1060, "o": 1, "h": 2, "l": 0.5, "c": 1.5}], offset_sec=0.0)
        self.assertEqual([b["t"] for b in s.bars_since(0)], [1000, 1060])


class LiveDirTests(unittest.TestCase):
    """挑 MT5：設定指著關掉的那台時要自己找到活的。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.now = 1_000_000.0
        # 讓候選只有我們造出來的這些，不要掃到真實機器
        self._orig = bar_store._candidate_dirs
        bar_store._candidate_dirs = lambda configured="": self.candidates

    def tearDown(self):
        bar_store._candidate_dirs = self._orig
        self._tmp.cleanup()

    def _mt5(self, name, age_sec):
        d = self.root / name
        d.mkdir(parents=True, exist_ok=True)
        f = d / "rates_M1.json"
        f.write_text(json.dumps({"bars": []}), encoding="utf-8")
        import os
        stamp = self.now - age_sec
        os.utime(f, (stamp, stamp))
        return d

    def test_picks_the_live_one_when_configured_is_dead(self):
        dead = self._mt5("dead", 6 * 86400)        # 停了六天（實際踩到的情形）
        live = self._mt5("live", 30)
        self.candidates = [dead, live]
        self.assertEqual(pick_live_mt5_dir(str(dead), clock=lambda: self.now), live)

    def test_sticks_with_the_current_choice_while_it_is_alive(self):
        """不同終端可能接不同券商，一直換等於把兩份行情混在一起。"""
        a = self._mt5("a", 120)
        b = self._mt5("b", 5)                       # 更新，但 a 還活著
        self.candidates = [a, b]
        self.assertEqual(pick_live_mt5_dir("", current=a, clock=lambda: self.now), a)

    def test_switches_away_from_a_terminal_that_died(self):
        a = self._mt5("a", 4 * 3600)                # 目前這台停了
        b = self._mt5("b", 10)
        self.candidates = [a, b]
        self.assertEqual(pick_live_mt5_dir("", current=a, clock=lambda: self.now), b)

    def test_keeps_current_when_everything_is_stale(self):
        """週末休市全部都停 —— 沿用原本那台就好，反正沒有新 K 線。"""
        a = self._mt5("a", 3 * 86400)
        b = self._mt5("b", 4 * 86400)
        self.candidates = [a, b]
        self.assertEqual(pick_live_mt5_dir("", current=a, clock=lambda: self.now), a)

    def test_returns_none_when_there_is_nothing_at_all(self):
        self.candidates = []
        self.assertIsNone(pick_live_mt5_dir("", clock=lambda: self.now))


class CandidateDiscoveryTests(unittest.TestCase):
    def test_reads_mt5_dirs_from_local_member_instances(self):
        """本機各會員實例的設定就記著自己那台 MT5，可攜版靠這個才找得到。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "instance_2").mkdir()
        (root / "instance_2" / "client_web_launcher_settings.json").write_text(
            json.dumps({"mt5_files_dir": r"D:\MT5-2\MQL5\Files"}), encoding="utf-8")
        orig = bar_store._instance_roots
        bar_store._instance_roots = lambda: [root]
        try:
            found = [str(p) for p in bar_store._candidate_dirs("")]
        finally:
            bar_store._instance_roots = orig
        self.assertIn(r"D:\MT5-2\MQL5\Files", found)

    def test_deduplicates_candidates(self):
        orig = bar_store._instance_roots
        bar_store._instance_roots = lambda: []
        try:
            found = bar_store._candidate_dirs(r"C:\Same\Files")
        finally:
            bar_store._instance_roots = orig
        lowered = [str(p).lower() for p in found]
        self.assertEqual(len(lowered), len(set(lowered)))


if __name__ == "__main__":
    unittest.main()
