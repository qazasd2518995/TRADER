"""圖表/行情層要跟著券商代號走：有後綴的代號(XAUUSD.s、XAUUSD247m)也要
讀得到 <SYMBOL>_price.json，不能寫死成純 XAUUSD。"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from copy_trader.central.market import build_market, live_tick


def _write_price(directory: Path, symbol: str, bid=4454.1, ask=4454.4):
    (directory / f"{symbol}_price.json").write_text(json.dumps({
        "symbol": symbol, "bid": bid, "ask": ask,
        "timestamp": int(time.time()),
    }), encoding="utf-8")


class MarketSymbolTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.dir = Path(self._dir.name)

    def tearDown(self):
        self._dir.cleanup()

    def _settings(self, **extra):
        base = {"mt5_files_dir": str(self.dir)}
        base.update(extra)
        return base

    def test_exness_suffix_symbol_detected(self):
        # Exness 黃金：XAUUSD247m，settings 沒有明確 symbol
        _write_price(self.dir, "XAUUSD247m")
        market = build_market(self._settings(), "M15")
        self.assertEqual(market["symbol"], "XAUUSD247m")
        self.assertIsNotNone(market["price"])
        self.assertEqual(market["price"]["symbol"], "XAUUSD247m")
        # live_tick 走同一套解析
        tick = live_tick(self._settings())
        self.assertIsNotNone(tick)
        self.assertEqual(tick["symbol"], "XAUUSD247m")

    def test_dot_suffix_symbol_detected(self):
        _write_price(self.dir, "XAUUSD.s")
        market = build_market(self._settings(), "M15")
        self.assertEqual(market["symbol"], "XAUUSD.s")
        self.assertIsNotNone(market["price"])

    def test_plain_xauusd_still_works(self):
        # 回歸：MetaQuotes demo 的純 XAUUSD 不能因為這次改動壞掉
        _write_price(self.dir, "XAUUSD")
        market = build_market(self._settings(), "M15")
        self.assertEqual(market["symbol"], "XAUUSD")
        self.assertIsNotNone(market["price"])

    def test_explicit_setting_wins(self):
        # 會員明確設了有效 symbol 就以設定為準（即使目錄裡是別的）
        _write_price(self.dir, "XAUUSD247m")
        market = build_market(self._settings(symbol="XAUUSDpro"), "M15")
        self.assertEqual(market["symbol"], "XAUUSDpro")

    def test_no_price_file_falls_back_to_default(self):
        # 空目錄：偵測不到就退回預設，不炸
        market = build_market(self._settings(), "M15")
        self.assertEqual(market["symbol"], "XAUUSD")
        self.assertIsNone(market["price"])


if __name__ == "__main__":
    unittest.main()
