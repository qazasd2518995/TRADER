"""「止盈處理」三種模式的回歸測試。

需求(2026-08-30):
  * 中頻訊號一單只有一個止盈,分批平倉沒有東西可以分 → 只給「單一點位 / 保本移損」,
    舊設定裡的 partial 要被收斂成 single。
  * 保本移損是進階版(PRO)以上的權益;體驗/基礎版一路降到單一點位。
  * 保本移損新增「距離觸發」:進場後順向走滿 X(美元)就把停損移到進場價,
    單一 TP 的來源也適用 —— 以前非得有兩個以上的止盈才會保本。
"""
from __future__ import annotations

import json
import pathlib
import tempfile
import unittest
from unittest import mock

from copy_trader.central.membership import LOW_FREQ, MID_FREQ, ULTRA_HIGH_FREQ
from copy_trader.central.web_launcher import LauncherState
from copy_trader.trade_manager.manager import OrderStatus, TradeManager


class ProfileTpModeTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.tm = TradeManager(self._d.name)

    def tearDown(self):
        self._d.cleanup()

    def test_single_is_a_valid_mode(self):
        self.tm.source_profiles = {"S": {"tp_mode": "single"}}
        self.assertEqual(self.tm.profile_for("S")["tp_mode"], "single")

    def test_unknown_mode_still_falls_back_to_partial(self):
        self.tm.source_profiles = {"S": {"tp_mode": "nonsense"}}
        self.assertEqual(self.tm.profile_for("S")["tp_mode"], "partial")

    def test_breakeven_distance_parsed_and_floored_at_zero(self):
        self.tm.source_profiles = {
            "A": {"tp_mode": "breakeven", "breakeven_distance": 3.5},
            "B": {"tp_mode": "breakeven", "breakeven_distance": -2},
            "C": {"tp_mode": "breakeven", "breakeven_distance": "x"},
            "D": {"tp_mode": "breakeven"},
        }
        self.assertEqual(self.tm.profile_for("A")["breakeven_distance"], 3.5)
        self.assertEqual(self.tm.profile_for("B")["breakeven_distance"], 0.0)
        self.assertEqual(self.tm.profile_for("C")["breakeven_distance"], 0.0)
        self.assertEqual(self.tm.profile_for("D")["breakeven_distance"], 0.0)


class _Signal:
    def __init__(self, direction, tps):
        self.direction = direction
        self.take_profit = list(tps)
        self.symbol = "XAUUSD"
        self.stop_loss = None
        self.entry_price = None
        self.lot_size = None


class _Order:
    def __init__(self, signal, entry):
        self.signal = signal
        self.entry_price = entry
        self.ticket = 1
        self.status = OrderStatus.FILLED
        self.source_window = "S"
        self.sl_trail_index = 0
        self.trailed_sl = None


class BreakevenDistanceTests(unittest.TestCase):
    """_check_trailing_sl 的距離觸發。用假的報價與假的 _modify_position。"""

    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.tm = TradeManager(self._d.name)
        self.moved = []
        self.tm._modify_position = lambda ticket, sl=None, tp=None: (
            self.moved.append((ticket, sl)) or True)

    def tearDown(self):
        self._d.cleanup()

    def _run(self, profile, signal, entry, price):
        self.tm.source_profiles = {"S": profile}
        order = _Order(signal, entry)
        self.tm.orders = {"sig": order}
        with mock.patch.object(self.tm, "_get_current_price", return_value=price):
            self.tm._check_trailing_sl()
        return order

    def test_single_tp_buy_moves_to_entry_after_distance(self):
        sig = _Signal("buy", [4010.0])
        profile = {"tp_mode": "breakeven", "breakeven_distance": 3.0}
        # 只走了 2 美元 → 還不動
        self._run(profile, sig, 4000.0, 4002.0)
        self.assertEqual(self.moved, [])
        # 走滿 3 美元 → 停損推到進場價
        order = self._run(profile, sig, 4000.0, 4003.0)
        self.assertEqual(self.moved, [(1, 4000.0)])
        self.assertEqual(order.sl_trail_index, 1)

    def test_single_tp_sell_uses_the_other_direction(self):
        sig = _Signal("sell", [3990.0])
        profile = {"tp_mode": "breakeven", "breakeven_distance": 3.0}
        self._run(profile, sig, 4000.0, 4002.0)     # 走反了
        self.assertEqual(self.moved, [])
        self._run(profile, sig, 4000.0, 3996.5)     # 順向 3.5 美元
        self.assertEqual(self.moved, [(1, 4000.0)])

    def test_zero_distance_single_tp_does_nothing(self):
        # 沒設距離、又只有一個止盈 → 沒有階梯可爬,維持原本行為(不動停損)
        sig = _Signal("buy", [4010.0])
        self._run({"tp_mode": "breakeven"}, sig, 4000.0, 4009.0)
        self.assertEqual(self.moved, [])

    def test_ladder_advances_past_breakeven(self):
        # 保本之後照樣逐關推進:跨過 TP1 與 TP2 → 停損推到 TP1
        sig = _Signal("buy", [4005.0, 4010.0, 4015.0])
        order = self._run({"tp_mode": "breakeven", "breakeven_distance": 1.0},
                          sig, 4000.0, 4011.0)
        self.assertEqual(self.moved, [(1, 4005.0)])
        self.assertEqual(order.sl_trail_index, 2)

    def test_distance_beats_a_nearer_first_tp(self):
        """面板寫「價格觸及保本距離時保本」,就不能因為第一個止盈比較近而提前保本。

        距離 5、TP1 在 +2:走到 +3 時 TP1 已經到了,但保本距離還沒到 → 不動。
        走到 +5 才保本。
        """
        sig = _Signal("buy", [4002.0, 4010.0, 4015.0])
        profile = {"tp_mode": "breakeven", "breakeven_distance": 5.0}
        self._run(profile, sig, 4000.0, 4003.0)      # 過了 TP1,沒到保本距離
        self.assertEqual(self.moved, [])
        order = self._run(profile, sig, 4000.0, 4005.0)
        self.assertEqual(self.moved, [(1, 4000.0)])
        self.assertEqual(order.sl_trail_index, 1)

    def test_zero_distance_falls_back_to_first_tp(self):
        # 沒設距離就維持舊行為 —— 升級不能讓既有來源突然完全不保本
        sig = _Signal("buy", [4002.0, 4010.0, 4015.0])
        self._run({"tp_mode": "breakeven"}, sig, 4000.0, 4003.0)
        self.assertEqual(self.moved, [(1, 4000.0)])

    def test_sell_distance_beats_a_nearer_first_tp(self):
        sig = _Signal("sell", [3998.0, 3990.0, 3985.0])
        profile = {"tp_mode": "breakeven", "breakeven_distance": 5.0}
        self._run(profile, sig, 4000.0, 3997.0)
        self.assertEqual(self.moved, [])
        self._run(profile, sig, 4000.0, 3995.0)
        self.assertEqual(self.moved, [(1, 4000.0)])

    def test_other_modes_are_left_alone(self):
        sig = _Signal("buy", [4010.0])
        self._run({"tp_mode": "single", "breakeven_distance": 1.0}, sig, 4000.0, 4009.0)
        self.assertEqual(self.moved, [])


class _ClampState(LauncherState):
    def __init__(self, ent):
        self.role = "client"
        self.auth = {"entitlements": ent}


ADV = {"sources": [MID_FREQ], "max_lot": None, "martingale": True,
       "partial_close": True, "breakeven": True}
BASIC = {"sources": [MID_FREQ], "max_lot": 0.1, "martingale": False,
         "partial_close": False, "breakeven": False}


class ClampTests(unittest.TestCase):
    def test_mid_frequency_never_uses_partial(self):
        st = _ClampState(ADV)
        out = st._clamp_to_entitlements({MID_FREQ: {"enabled": True, "tp_mode": "partial"}})
        self.assertEqual(out[MID_FREQ]["tp_mode"], "single")

    def test_mid_frequency_keeps_breakeven_for_advanced(self):
        st = _ClampState(ADV)
        out = st._clamp_to_entitlements({MID_FREQ: {"enabled": True, "tp_mode": "breakeven"}})
        self.assertEqual(out[MID_FREQ]["tp_mode"], "breakeven")

    def test_basic_tier_falls_all_the_way_to_single(self):
        st = _ClampState(BASIC)
        out = st._clamp_to_entitlements({MID_FREQ: {"enabled": True, "tp_mode": "breakeven"}})
        self.assertEqual(out[MID_FREQ]["tp_mode"], "single")

    def test_other_sources_keep_partial_when_entitled(self):
        st = _ClampState(dict(ADV, sources=[MID_FREQ, "其他來源"]))
        out = st._clamp_to_entitlements({"其他來源": {"enabled": True, "tp_mode": "partial"}})
        self.assertEqual(out["其他來源"]["tp_mode"], "partial")


class _Defaults(LauncherState):
    """只借 defaults() / _load_settings()，不碰真正的資料目錄。"""

    def __init__(self, settings_path=None):
        self.role = "client"
        if settings_path is not None:
            self.settings_path = settings_path


class UnlaunchedSourceDefaultTests(unittest.TestCase):
    """還沒接訊號源的來源(超高頻、低頻)一定要預設「不跟單」。

    stats.source_settings() 對「沒設定過」的來源預設 enabled=True —— 所以只要
    等級授權了那個來源、面板又把它畫出來，第一筆訊號進來就會在沒人按過同意的
    情況下成交。種一筆 enabled=False 的 profile 進去才擋得住。
    """

    def test_defaults_ship_them_disabled(self):
        profiles = json.loads(_Defaults().defaults()["source_profiles"])
        for name in (ULTRA_HIGH_FREQ, LOW_FREQ):
            self.assertIn(name, profiles, name)
            self.assertFalse(profiles[name]["enabled"], name)

    def test_upgrading_an_existing_install_seeds_them_too(self):
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "settings.json"
            # 舊版存下來的設定：只有中頻，兩個新來源都還不存在
            path.write_text(json.dumps({
                "source_profiles": json.dumps({MID_FREQ: {"enabled": True}}, ensure_ascii=False),
            }, ensure_ascii=False), encoding="utf-8")
            data = _Defaults(path)._load_settings()
        profiles = json.loads(data["source_profiles"])
        self.assertTrue(profiles[MID_FREQ]["enabled"])          # 原本的設定不動
        for name in (ULTRA_HIGH_FREQ, LOW_FREQ):
            self.assertFalse(profiles[name]["enabled"], name)


if __name__ == "__main__":
    unittest.main()
