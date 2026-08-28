"""券商代號偵測 / 解析的回歸測試。

背景(2026-08-28):axel 券商用 XAUUSD.s 而非 XAUUSD。EA 現在一律跟著圖表品種走
並把代號寫進 symbol_info.json;會員端要能可靠偵測到,否則掛單(送錯代號被拒)與
K 線圖(讀錯價格檔)都會失效。也要修掉「設定檔殘留舊代號 + 舊價格檔卡死」的坑。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from copy_trader.config import Config, detect_mt5_symbol


def _write(directory: Path, name: str, payload: dict, mtime: float | None = None):
    path = directory / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class DetectSymbolTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.dir = Path(self._d.name)

    def tearDown(self):
        self._d.cleanup()

    def test_symbol_info_is_authoritative(self):
        _write(self.dir, "symbol_info.json", {"symbol": "XAUUSD.s", "digits": 3})
        # 就算有一個叫 XAUUSD 的殘檔,也以 symbol_info.json 為準
        _write(self.dir, "XAUUSD_price.json", {"symbol": "XAUUSD", "bid": 0, "ask": 0})
        self.assertEqual(detect_mt5_symbol(str(self.dir)), "XAUUSD.s")

    def test_live_price_file_detected_when_no_symbol_info(self):
        _write(self.dir, "XAUUSD.s_price.json", {"symbol": "XAUUSD.s", "bid": 4600.0, "ask": 4600.3})
        self.assertEqual(detect_mt5_symbol(str(self.dir)), "XAUUSD.s")

    def test_stale_zero_price_file_is_skipped_for_live_one(self):
        # 舊 XAUUSD 殘檔 bid=0 且「比較新」;活的 XAUUSD.s 檔比較舊。要選有報價的那個。
        live = _write(self.dir, "XAUUSD.s_price.json", {"symbol": "XAUUSD.s", "bid": 4600.0, "ask": 4600.3}, mtime=1000)
        stale = _write(self.dir, "XAUUSD_price.json", {"symbol": "XAUUSD", "bid": 0, "ask": 0}, mtime=2000)
        self.assertEqual(detect_mt5_symbol(str(self.dir)), "XAUUSD.s")

    def test_symbol_inferred_from_filename_when_field_missing(self):
        _write(self.dir, "XAUUSD.s_price.json", {"bid": 4600.0, "ask": 4600.3})
        self.assertEqual(detect_mt5_symbol(str(self.dir)), "XAUUSD.s")

    def test_empty_dir_returns_blank_not_default(self):
        self.assertEqual(detect_mt5_symbol(str(self.dir)), "")

    def test_missing_dir_returns_blank(self):
        self.assertEqual(detect_mt5_symbol(str(self.dir / "nope")), "")


class ResolveSymbolTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.dir = Path(self._d.name)

    def tearDown(self):
        self._d.cleanup()

    def test_broker_files_override_stale_configured_symbol(self):
        # 設定檔還寫著預設 XAUUSD,但券商其實是 XAUUSD.s → 要以券商檔案為準(修掉卡死)
        _write(self.dir, "symbol_info.json", {"symbol": "XAUUSD.s"})
        _write(self.dir, "XAUUSD_price.json", {"symbol": "XAUUSD", "bid": 0, "ask": 0})
        cfg = Config(mt5_files_dir=str(self.dir), symbol_name="XAUUSD")
        self.assertEqual(cfg.symbol_name, "XAUUSD.s")

    def test_configured_symbol_preserved_when_no_broker_files(self):
        # 空目錄(EA 還沒寫檔)不能把使用者設好的代號蓋成預設
        cfg = Config(mt5_files_dir=str(self.dir), symbol_name="XAUUSD.s")
        self.assertEqual(cfg.symbol_name, "XAUUSD.s")

    def test_standard_broker_files_win_over_wrong_configured_suffix(self):
        # 反向:設定誤填 XAUUSD.s,但券商是標準 XAUUSD → 也要被券商檔案糾正
        _write(self.dir, "symbol_info.json", {"symbol": "XAUUSD"})
        cfg = Config(mt5_files_dir=str(self.dir), symbol_name="XAUUSD.s")
        self.assertEqual(cfg.symbol_name, "XAUUSD")


if __name__ == "__main__":
    unittest.main()
