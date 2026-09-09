"""MT5 橋接檔不保證是 UTF-8。

EA 用 FileWriteString 寫 account_info.json，帳號持有人姓名帶的是系統 ANSI
碼頁 —— 中文 Windows 上就是 cp950(Big5)。帳號名是中文的會員，那個檔案就不是
合法的 UTF-8。

同一個坑踩了兩次，因為有**兩份各自獨立的讀檔實作**：

  2026-09-09 早上  config._read_json_dict   → 會員端整個不回報狀態，
                                              手機控制台上一片空白
  2026-09-09 下午  stats._read_json         → 電腦版面板的餘額、淨值變空白

第二次是同一台機器（MT5 277942668，帳號名是中文）。修第一處時沒想到還有第二份。
這個測試同時釘住兩份，以後再加第三份讀檔就會被這裡抓到。
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from copy_trader.central.stats import _read_json
from copy_trader.config import _read_json_dict

# 實際出事的那個帳號的資料形狀。姓名用 cp950 編碼的中文，那正是 0xad 那個
# 位元組的來源 —— 用 UTF-8 讀會在第 28 個位元組炸掉。
ACCOUNT = {"login": 277942668, "name": "陳大文", "server": "Exness-MT5Trial5",
           "currency": "USD", "balance": 435.95, "equity": 435.95,
           "margin": 0.0, "free_margin": 435.95, "profit": 0.0}


class _Base(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self.path = Path(self._dir) / "account_info.json"

    def tearDown(self):
        shutil.rmtree(self._dir, ignore_errors=True)

    def write(self, encoding):
        self.path.write_bytes(json.dumps(ACCOUNT, ensure_ascii=False).encode(encoding))

    def check(self, reader):
        got = reader(self.path)
        self.assertIsNotNone(got, "讀不到 —— 面板上的餘額會整個空白")
        # 數字一個都不能少。名字變亂碼可以接受，讀不到餘額不行。
        for key in ("login", "balance", "equity", "free_margin", "server", "currency"):
            self.assertEqual(got[key], ACCOUNT[key], f"{key} 對不上")


class ConfigReaderTests(_Base):
    """會員端上報狀態走這一份。壞掉的話手機控制台整片空白。"""

    def test_cp950_account_name(self):
        self.write("cp950")
        self.check(_read_json_dict)

    def test_plain_utf8_still_works(self):
        self.write("utf-8")
        self.check(_read_json_dict)

    def test_utf8_with_bom(self):
        self.write("utf-8-sig")
        self.check(_read_json_dict)

    def test_missing_file(self):
        self.assertEqual(_read_json_dict(self.path), {})

    def test_garbage_is_not_fatal(self):
        self.path.write_bytes(b"\xff\xfe not json at all")
        self.assertEqual(_read_json_dict(self.path), {})


class StatsReaderTests(_Base):
    """電腦版面板的餘額／淨值走這一份。"""

    def test_cp950_account_name(self):
        self.write("cp950")
        self.check(_read_json)

    def test_plain_utf8_still_works(self):
        self.write("utf-8")
        self.check(_read_json)

    def test_utf8_with_bom(self):
        self.write("utf-8-sig")
        self.check(_read_json)

    def test_missing_file(self):
        self.assertIsNone(_read_json(self.path))

    def test_truncated_json_still_gives_up(self):
        """讀到 EA 寫到一半的半截 JSON 時要回 None，不能卡住也不能亂猜。

        latin-1 永遠解得開位元組，所以不能只靠解碼成功就當作讀到了 ——
        還是要 json.loads 過得去才算。
        """
        self.path.write_bytes(b'{"login": 27794266')
        self.assertIsNone(_read_json(self.path, retries=1))


class BothReadersAgreeTests(_Base):
    """兩份實作對同一個檔案要給出同樣的數字。分歧就是下一次事故。"""

    def test_same_numbers(self):
        for encoding in ("utf-8", "utf-8-sig", "cp950"):
            with self.subTest(encoding=encoding):
                self.write(encoding)
                a, b = _read_json_dict(self.path), _read_json(self.path)
                for key in ("login", "balance", "equity"):
                    self.assertEqual(a[key], b[key], f"{encoding} 的 {key} 兩邊不一致")


if __name__ == "__main__":
    unittest.main()
