"""執行設定影子對照。

這東西的價值完全建立在「數字可信」上，所以測試重點不是它會不會跑，
而是它會不會在資料還沒到齊時就給答案、會不會把還在跑的單算成已實現、
以及不同設定是不是真的算出不同結果。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from copy_trader.backtest import Rules
from copy_trader.central.bar_store import BarStore
from copy_trader.central.exec_shadow import ExecutionShadow


def _bars(seq, start=1_700_000_000, step=60):
    """seq = [(low, high), ...] -> M1 K 線。"""
    return [{"t": start + i * step, "o": (l + h) / 2, "h": h, "l": l, "c": (l + h) / 2}
            for i, (l, h) in enumerate(seq)]


def _payload(entry=3300.0, stop=3294.0, tps=(3305.0, 3310.0, 3315.0),
             direction="buy", exec_id="x1", source="yuyu"):
    return {
        "type": "trade_signal",
        "execution_id": exec_id,
        "source": "高頻（yuyu）",
        "source_name": source,
        "signal": {"direction": direction, "entry_price": entry,
                   "stop_loss": stop, "take_profit": list(tps)},
    }


class _Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.store = BarStore(root / "m1.json")
        self.now = 1_700_000_000.0
        self.shadow = ExecutionShadow(root / "shadow.json", self.store,
                                      cost_usd=20.0, clock=lambda: self.now)

    def tearDown(self):
        self._tmp.cleanup()


class RecordTests(_Fixture):
    def test_records_a_complete_trade_signal(self):
        self.assertTrue(self.shadow.record(_payload()))
        self.assertEqual(self.shadow.summary()["pending"], 1)

    def test_ignores_non_trade_events(self):
        self.assertFalse(self.shadow.record({"type": "cancel", "signal": {}}))
        self.assertFalse(self.shadow.record({"type": "trade_rejected"}))

    def test_ignores_signals_without_full_geometry(self):
        """沒有停損或止盈就模擬不出出場，記了也只是雜訊。"""
        self.assertFalse(self.shadow.record(_payload(tps=())))
        p = _payload()
        p["signal"]["stop_loss"] = None
        self.assertFalse(self.shadow.record(p))

    def test_same_execution_id_is_not_double_counted(self):
        self.assertTrue(self.shadow.record(_payload(exec_id="dup")))
        self.assertFalse(self.shadow.record(_payload(exec_id="dup")))
        self.assertEqual(self.shadow.summary()["pending"], 1)

    def test_survives_restart(self):
        self.shadow.record(_payload())
        again = ExecutionShadow(self.shadow.path, self.store, clock=lambda: self.now)
        self.assertEqual(again.summary()["pending"], 1)


class SettlementTests(_Fixture):
    def test_does_not_settle_while_bars_are_incomplete(self):
        """單子還在跑、K 線也還沒補滿 —— 這時候給數字就是在騙自己。"""
        self.shadow.record(_payload())
        self.store.ingest(_bars([(3300, 3301)] * 30), "XAUUSD")   # 只有 30 分鐘
        self.assertEqual(self.shadow.evaluate(), 0)
        s = self.shadow.summary()
        self.assertEqual(s["pending"], 1)
        self.assertEqual(s["settled"], 0)

    def test_settles_once_the_trade_actually_exits(self):
        self.shadow.record(_payload())
        # 成交 -> 一路走到 TP3
        self.store.ingest(_bars([(3300, 3301), (3300, 3306), (3302, 3311),
                                 (3302, 3316)]), "XAUUSD")
        self.assertEqual(self.shadow.evaluate(), 1)
        self.assertEqual(self.shadow.summary()["settled"], 1)

    def test_settles_when_window_has_passed_even_if_unfilled(self):
        """掛單一直沒成交，窗口過了就該定案成未成交，不能永遠掛著。"""
        self.shadow.record(_payload(entry=3200.0, stop=3194.0,
                                    tps=(3205.0, 3210.0, 3215.0)))
        self.store.ingest(_bars([(3300, 3301)] * 400), "XAUUSD")
        self.store.ingest(_bars([(3300, 3301)], start=1_700_000_000 + 29 * 3600))
        self.assertEqual(self.shadow.evaluate(), 1)
        s = self.shadow.summary()
        self.assertEqual(s["settled"], 1)
        # 沒成交的單不計入任何設定的績效
        self.assertTrue(all(v["n"] == 0 for v in s["variants"]))

    def test_settled_results_do_not_change_afterwards(self):
        self.shadow.record(_payload())
        self.store.ingest(_bars([(3300, 3301), (3300, 3306), (3302, 3311),
                                 (3302, 3316)]), "XAUUSD")
        self.shadow.evaluate()
        before = self.shadow.summary()["variants"][0]["total"]
        self.store.ingest(_bars([(3200, 3400)] * 60, start=1_700_000_000 + 3600))
        self.assertEqual(self.shadow.evaluate(), 0)      # 已定案不再重算
        self.assertEqual(self.shadow.summary()["variants"][0]["total"], before)


class ComparisonTests(_Fixture):
    def test_tp1_all_out_beats_partials_when_price_comes_back(self):
        """觸及 TP1 後回頭 —— 這正是 yuyu 最常見的形狀（79 筆裡 36 筆）。

        現行設定只實現 TP1 的一半再保本出場；TP1 全出實現整筆。
        """
        self.shadow.record(_payload())
        self.store.ingest(_bars([(3300, 3301), (3300, 3306), (3299, 3301)]), "XAUUSD")
        self.shadow.evaluate()
        rows = {v["name"]: v for v in self.shadow.summary()["variants"]}
        live = rows["現行設定"]["total"]
        allout = rows["TP1全出"]["total"]
        self.assertAlmostEqual(live, 0.5 * 5 * 100 - 20, places=1)     # +230
        self.assertAlmostEqual(allout, 5 * 100 - 20, places=1)         # +480
        self.assertGreater(allout, live)
        self.assertEqual(rows["TP1全出"]["delta"], round(allout - live, 1))

    def test_tighter_stop_can_lose_where_the_original_survives(self):
        """收緊停損不是免費的 —— 有些原本撐過去的單會被掃掉，要看得出來。"""
        self.shadow.record(_payload())          # 停損 3294，0.8 倍 = 3295.2
        self.store.ingest(_bars([(3300, 3301), (3295, 3300), (3302, 3306),
                                 (3302, 3311), (3302, 3316)]), "XAUUSD")
        self.shadow.evaluate()
        rows = {v["name"]: v for v in self.shadow.summary()["variants"]}
        self.assertLess(rows["TP1全出+停損0.8"]["total"], 0)
        self.assertGreater(rows["TP1全出"]["total"], 0)

    def test_cost_is_deducted_from_filled_trades(self):
        shadow = ExecutionShadow(self.shadow.path.with_name("c.json"), self.store,
                                 cost_usd=100.0, clock=lambda: self.now,
                                 variants={"只吃TP1": Rules(partials=(1.0,))})
        shadow.record(_payload())
        self.store.ingest(_bars([(3300, 3301), (3300, 3306), (3299, 3301)]), "XAUUSD")
        shadow.evaluate()
        # 5 美元 x 100 = 500，扣掉 100 成本
        self.assertAlmostEqual(shadow.summary()["variants"][0]["total"], 400.0, places=1)


class BarLookbackTests(_Fixture):
    """實際踩到的 bug：只取 ts 之後的 K 線，引擎拿不到「發單當下」那根，
    整批訊號全部評不出來，卻因為窗口已過而被標成已定案 —— 帳面上 62 筆
    已定案、0 筆成交。訊號時間幾乎不會剛好落在 K 線邊界，所以要往前多取。"""

    def test_signal_between_bars_still_evaluates(self):
        base = 1_700_000_000
        # 成交當根不吃止盈（見 test_range_fade 的前視偏誤），所以三檔止盈
        # 要多一根才走得完 —— 這裡測的是取 K 線的範圍，不是出場邏輯。
        self.store.ingest(_bars([(3300, 3301), (3300, 3306), (3302, 3311),
                                 (3302, 3316), (3302, 3316)], start=base), "XAUUSD")
        self.now = base + 25          # 落在第一根 K 線中間，不是邊界
        self.shadow.record(_payload())
        self.shadow.evaluate()
        s = self.shadow.summary()
        self.assertEqual(s["settled"], 1)
        self.assertEqual(s["unevaluable"], 0)
        self.assertTrue(all(v["n"] == 1 for v in s["variants"]))

    def test_gap_in_bars_is_reported_not_silently_zeroed(self):
        """輪詢停過、K 線有洞的訊號要標出來，不能當成 0 混進統計。"""
        base = 1_700_000_000
        self.now = base + 25
        self.shadow.record(_payload())
        # 只有訊號之後很久的 K 線 —— 發單當下那段完全沒有
        self.store.ingest(_bars([(3300, 3301)] * 5, start=base + 30 * 3600), "XAUUSD")
        self.shadow.evaluate()
        s = self.shadow.summary()
        self.assertEqual(s["unevaluable"], 1)
        self.assertEqual(s["settled"], 0)          # 算不出來的不算已定案
        self.assertTrue(all(v["n"] == 0 for v in s["variants"]))


class SummaryTests(_Fixture):
    def test_filters_by_source(self):
        self.shadow.record(_payload(exec_id="a", source="yuyu"))
        self.shadow.record(_payload(exec_id="b", source="mid"))
        self.store.ingest(_bars([(3300, 3301), (3300, 3306), (3302, 3311),
                                 (3302, 3316)]), "XAUUSD")
        self.shadow.evaluate()
        self.assertEqual(self.shadow.summary()["settled"], 2)
        self.assertEqual(self.shadow.summary(source="yuyu")["settled"], 1)
        self.assertEqual(sorted(self.shadow.summary()["sources"]), ["mid", "yuyu"])

    def test_empty_summary_is_safe(self):
        s = self.shadow.summary()
        self.assertEqual(s["settled"], 0)
        self.assertEqual(len(s["variants"]), 4)
        self.assertTrue(all(v["n"] == 0 for v in s["variants"]))

    def test_corrupt_ledger_does_not_crash_startup(self):
        """訊號中心不能因為一個統計檔壞掉就起不來。"""
        self.shadow.path.write_text("{ not json", encoding="utf-8")
        again = ExecutionShadow(self.shadow.path, self.store, clock=lambda: self.now)
        self.assertEqual(again.summary()["settled"], 0)
        self.assertTrue(again.record(_payload()))


if __name__ == "__main__":
    unittest.main()
