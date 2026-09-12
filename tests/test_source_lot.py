"""跟隨來源手數：來源下多少，我們就按倍率等比例下多少。

會員問的其實是這件事 —— 鏡像來源跑的是 0.02 起跳的網格，我們卻每一筆都
0.01。那不只是「數字不一樣」：來源加碼攤平的時候我們沒跟著加，來源只用最
小手數試單的時候我們卻押一樣多，兩邊的損益從第二筆開始就沒有可比性。

這裡釘住的每一條都是「算錯比不算嚴重」的情境：

  1. 只准縮小 —— 來源的本金跟會員的無關，放大它沒有風控意義
  2. 縮到不足最低手數要**補回 0.01**，不是丟掉那一筆（跟本金比例模式相反，
     理由寫在 calculate_source_lot 的 docstring）
  3. 訊號沒帶手數就退回基礎手數，不能拿 None 去乘
  4. 讀不到券商規格不能整批擋下來 —— 那是本金比例模式才付得起的代價
  5. 其他模式（均注／馬丁）絕對不能被來源手數影響
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from copy_trader.trade_manager.manager import TradeManager


class _Signal:
    def __init__(self, lot_size):
        self.lot_size = lot_size


def _manager(tmp: Path, profile: dict, *, symbol_info: dict | None = None):
    m = TradeManager.__new__(TradeManager)
    m.mt5_files_dir = tmp
    m.source_profiles = {"超高頻交易": profile}
    m.default_lot_size = 0.01
    m.use_martingale = False
    m.martingale_multiplier = 2.0
    m.martingale_max_level = 5
    m.martingale_source_lots = {}
    m.partial_close_ratios = [0.5, 0.3, 0.2]
    if symbol_info is not None:
        (tmp / "symbol_info.json").write_text(json.dumps(symbol_info), encoding="utf-8")
    return m


class RatioTests(unittest.TestCase):
    def test_ratio_one_copies_the_source_exactly(self):
        with TemporaryDirectory() as tmp:
            m = _manager(Path(tmp), {"mode": "source", "source_ratio": 1.0},
                         symbol_info={"volume_min": 0.01, "volume_step": 0.01})
            for volume in (0.02, 0.08, 0.44):
                self.assertEqual(
                    m.calculate_source_lot(_Signal(volume), "超高頻交易"), volume)

    def test_half_ratio_scales_the_whole_grid(self):
        """網格的形狀要保留 —— 每一格都減半，不是全部壓成同一個數字。"""
        with TemporaryDirectory() as tmp:
            m = _manager(Path(tmp), {"mode": "source", "source_ratio": 0.5},
                         symbol_info={"volume_min": 0.01, "volume_step": 0.01})
            got = [m.calculate_source_lot(_Signal(v), "超高頻交易")
                   for v in (0.02, 0.10, 0.44)]
            self.assertEqual(got, [0.01, 0.05, 0.22])

    def test_ratio_above_one_is_clamped(self):
        with TemporaryDirectory() as tmp:
            m = _manager(Path(tmp), {"mode": "source", "source_ratio": 5.0},
                         symbol_info={"volume_min": 0.01, "volume_step": 0.01})
            self.assertEqual(m.calculate_source_lot(_Signal(0.10), "超高頻交易"), 0.10)

    def test_result_is_floored_to_the_broker_step(self):
        with TemporaryDirectory() as tmp:
            m = _manager(Path(tmp), {"mode": "source", "source_ratio": 0.3},
                         symbol_info={"volume_min": 0.01, "volume_step": 0.01})
            # 0.07 × 0.3 = 0.021 → 無條件捨去到 0.02
            self.assertEqual(m.calculate_source_lot(_Signal(0.07), "超高頻交易"), 0.02)


class MinimumTests(unittest.TestCase):
    def test_below_minimum_is_raised_not_dropped(self):
        """漏掉網格中的一格會讓平倉配對與損益比較整個失真，寧可下 0.01。"""
        with TemporaryDirectory() as tmp:
            m = _manager(Path(tmp), {"mode": "source", "source_ratio": 0.1},
                         symbol_info={"volume_min": 0.01, "volume_step": 0.01})
            self.assertEqual(m.calculate_source_lot(_Signal(0.02), "超高頻交易"), 0.01)

    def test_broker_minimum_above_001_is_respected(self):
        with TemporaryDirectory() as tmp:
            m = _manager(Path(tmp), {"mode": "source", "source_ratio": 1.0},
                         symbol_info={"volume_min": 0.10, "volume_step": 0.01})
            self.assertEqual(m.calculate_source_lot(_Signal(0.02), "超高頻交易"), 0.10)

    def test_volume_max_caps_the_result(self):
        with TemporaryDirectory() as tmp:
            m = _manager(Path(tmp), {"mode": "source", "source_ratio": 1.0},
                         symbol_info={"volume_min": 0.01, "volume_step": 0.01,
                                      "volume_max": 0.20})
            self.assertEqual(m.calculate_source_lot(_Signal(0.44), "超高頻交易"), 0.20)


class DegradedInputTests(unittest.TestCase):
    def test_missing_symbol_info_still_trades(self):
        """讀不到券商規格就用 0.01 級距近似，不能整批擋掉鏡像單。"""
        with TemporaryDirectory() as tmp:
            m = _manager(Path(tmp), {"mode": "source", "source_ratio": 1.0})
            self.assertEqual(m.calculate_source_lot(_Signal(0.07), "超高頻交易"), 0.07)

    def test_signal_without_volume_falls_back_to_base_lot(self):
        with TemporaryDirectory() as tmp:
            m = _manager(Path(tmp), {"mode": "source", "source_ratio": 1.0,
                                     "base_lot": 0.03},
                         symbol_info={"volume_min": 0.01, "volume_step": 0.01})
            for bad in (None, 0, -1, "abc"):
                self.assertEqual(
                    m.calculate_source_lot(_Signal(bad), "超高頻交易"), 0.03)


class OtherModesUnaffectedTests(unittest.TestCase):
    def test_flat_ignores_the_source_volume(self):
        with TemporaryDirectory() as tmp:
            m = _manager(Path(tmp), {"mode": "flat", "base_lot": 0.01},
                         symbol_info={"volume_min": 0.01, "volume_step": 0.01})
            self.assertEqual(m.profile_for("超高頻交易")["mode"], "flat")
            self.assertEqual(m.get_martingale_lot_size("超高頻交易"), 0.01)

    def test_unknown_mode_does_not_become_source(self):
        with TemporaryDirectory() as tmp:
            m = _manager(Path(tmp), {"mode": "follow_source"},
                         symbol_info={"volume_min": 0.01, "volume_step": 0.01})
            self.assertEqual(m.profile_for("超高頻交易")["mode"], "flat")


class PayloadTests(unittest.TestCase):
    def test_mirror_publishes_the_source_volume_as_lot_size(self):
        """沒有這個欄位，跟隨來源模式每一筆都只會退回基礎手數。"""
        import time
        from copy_trader.central.mirror_collector import MirrorCollector

        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "account_info.json").write_text(
                json.dumps({"login": 1, "timestamp": int(time.time())}),
                encoding="utf-8")
            sent = []
            c = MirrorCollector(d, sent.append, source="超高頻交易", symbol="XAUUSD")
            row = {"ticket": 99, "type": "buy", "volume": 0.08,
                   "price_open": 4350.0, "comment": "", "magic": 0,
                   "time_open_timestamp": int(time.time())}
            c._publish_open(99, row)          # noqa: SLF001
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0]["signal"]["lot_size"], 0.08)
            self.assertEqual(sent[0]["mirror"]["source_volume"], 0.08)


class MembershipGateTests(unittest.TestCase):
    def test_only_volume_carrying_sources_may_pick_source_mode(self):
        from copy_trader.central import membership

        self.assertIn("source", membership.SOURCE_MODES)
        self.assertIn(membership.ULTRA_HIGH_FREQ, membership.SOURCE_VOLUME_SOURCES)
        self.assertNotIn(membership.MID_FREQ, membership.SOURCE_VOLUME_SOURCES)
        self.assertNotIn(membership.HIGH_FREQ, membership.SOURCE_VOLUME_SOURCES)

    def test_sanitizer_downgrades_source_mode_on_a_line_source(self):
        """LINE 報單沒有手數可跟，選了只會每筆退回基礎手數 —— 當場改掉。"""
        from copy_trader.central import membership

        ent = {"dynamic_lot": True, "martingale": True, "partial_close": True}
        rejected = []
        out = membership._clean_source_profile(          # noqa: SLF001
            membership.HIGH_FREQ, {"mode": "source"}, ent, rejected)
        self.assertEqual(out["mode"], "flat")
        self.assertIn(f"source:{membership.HIGH_FREQ}:no_source_volume", rejected)

    def test_sanitizer_keeps_source_mode_on_the_mirror_source(self):
        from copy_trader.central import membership

        ent = {"dynamic_lot": True}
        rejected = []
        out = membership._clean_source_profile(          # noqa: SLF001
            membership.ULTRA_HIGH_FREQ,
            {"mode": "source", "source_ratio": 0.5}, ent, rejected)
        self.assertEqual(out["mode"], "source")
        self.assertEqual(out["source_ratio"], 0.5)
        self.assertEqual(rejected, [])

    def test_sanitizer_clamps_the_ratio_to_one(self):
        from copy_trader.central import membership

        out = membership._clean_source_profile(          # noqa: SLF001
            membership.ULTRA_HIGH_FREQ,
            {"mode": "source", "source_ratio": 9.0}, {"dynamic_lot": True}, [])
        self.assertEqual(out["source_ratio"], 1.0)


if __name__ == "__main__":
    unittest.main()
