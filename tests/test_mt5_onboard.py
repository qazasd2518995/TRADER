"""會員端一鍵把 MT5 設定好。

要取代的是安裝說明裡最長的那一段：找資料夾 → 複製 .mq5 → F4 → F7 編譯 →
開 XAUUSD 圖表 → 拖 EA 上去 → 勾允許演算法交易。六步，每步都有人卡住。

這裡釘住的是那些「錯了不會馬上看出來」的地方：

  1. [Experts] Enabled 要真的變成 1，而且不能把 common.ini 其他設定弄壞
     —— 那個檔案同時存著帳號、代理伺服器、通知設定
  2. 明文密碼用完一定要刪
  3. MT5 已經在跑就不能再啟動一次（同一個可攜目錄不接受第二個實例，
     而且兩個 EA 會把同一張單下兩次）
  4. 密碼絕不能寫進 settings.json
"""
from __future__ import annotations

import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from copy_trader.central import mt5_onboard as onboard


def _fake_install(root: Path, *, common_ini: str = "") -> Path:
    """做一個長得像可攜式 MT5 的目錄，回傳 terminal64.exe 的路徑。"""
    terminal = root / "terminal64.exe"
    terminal.write_bytes(b"")
    (root / "MQL5" / "Experts").mkdir(parents=True, exist_ok=True)
    (root / "MQL5" / "Files").mkdir(parents=True, exist_ok=True)
    if common_ini:
        (root / "config").mkdir(parents=True, exist_ok=True)
        (root / "config" / "common.ini").write_text(common_ini, encoding="utf-8")
    return terminal


class AlgoTradingSwitchTests(unittest.TestCase):
    def test_turns_the_switch_on_without_touching_anything_else(self):
        original = (
            "[Common]\n"
            "Login=441064104\n"
            "Server=Exness-MT5Real43\n"
            "ProxyEnable=0\n"
            "[Experts]\n"
            "AllowDllImport=0\n"
            "Enabled=0\n"
            "Account=1\n"
            "[Notification]\n"
            "Enable=0\n"
        )
        with TemporaryDirectory() as tmp:
            terminal = _fake_install(Path(tmp), common_ini=original)
            onboard.enable_algo_trading(terminal)
            text = (terminal.parent / "config" / "common.ini").read_text(encoding="utf-8")
        self.assertIn("Enabled=1", text)
        self.assertNotIn("Enabled=0", text)
        # 帳號、代理、通知那些一個都不能掉 —— 這個檔案不是只給我們用的
        self.assertIn("Login=441064104", text)
        self.assertIn("ProxyEnable=0", text)
        self.assertIn("[Notification]", text)
        self.assertIn("Enable=0", text)
        self.assertIn("AllowDllImport=0", text)

    def test_adds_the_section_when_it_is_missing(self):
        with TemporaryDirectory() as tmp:
            terminal = _fake_install(Path(tmp), common_ini="[Common]\nLogin=1\n")
            onboard.enable_algo_trading(terminal)
            text = (terminal.parent / "config" / "common.ini").read_text(encoding="utf-8")
        self.assertIn("[Experts]", text)
        self.assertIn("Enabled=1", text)
        self.assertIn("Login=1", text)

    def test_creates_the_file_on_a_fresh_install(self):
        with TemporaryDirectory() as tmp:
            terminal = _fake_install(Path(tmp))
            onboard.enable_algo_trading(terminal)
            path = terminal.parent / "config" / "common.ini"
            # 斷言必須在 with 裡面 —— 出了這個區塊暫存目錄就被刪光，
            # 「檔案不存在」會變成永遠成立的假結果。
            self.assertTrue(path.is_file())
            self.assertIn("Enabled=1", path.read_text(encoding="utf-8"))

    def test_missing_keys_are_appended_to_an_existing_section(self):
        with TemporaryDirectory() as tmp:
            terminal = _fake_install(Path(tmp),
                                     common_ini="[Experts]\nAllowDllImport=0\n")
            onboard.enable_algo_trading(terminal)
            text = (terminal.parent / "config" / "common.ini").read_text(encoding="utf-8")
        for key in ("Enabled=1", "Account=1", "Profile=1"):
            self.assertIn(key, text)


class StartupIniTests(unittest.TestCase):
    def test_ini_carries_what_mt5_needs(self):
        with TemporaryDirectory() as tmp:
            terminal = _fake_install(Path(tmp))
            path = onboard._write_startup_ini(          # noqa: SLF001
                terminal, "441064104", "hunter2", "Exness-MT5Real43",
                "XAUUSD247m", "M5")
            text = path.read_text(encoding="ascii")
        self.assertIn("Login=441064104", text)
        self.assertIn("Server=Exness-MT5Real43", text)
        self.assertIn("KeepPrivate=1", text)            # 之後就不需要這個檔了
        self.assertIn(f"Expert={onboard.EA_NAME}", text)
        self.assertIn("Symbol=XAUUSD247m", text)
        self.assertIn("Enabled=1", text)

    def test_shred_removes_the_plaintext_password(self):
        with TemporaryDirectory() as tmp:
            terminal = _fake_install(Path(tmp))
            path = onboard._write_startup_ini(          # noqa: SLF001
                terminal, "1", "hunter2", "S", "XAUUSD", "M5")
            self.assertIn("hunter2", path.read_text(encoding="ascii"))
            onboard._shred(path)                        # noqa: SLF001
            # 一樣要在 with 裡面斷言：在外面的話暫存目錄早就沒了，
            # 這個測試會在 _shred 完全失效的情況下照樣通過。
            self.assertFalse(path.exists())


class GuardTests(unittest.TestCase):
    def test_rejects_a_non_numeric_login_before_launching_anything(self):
        result = onboard.onboard(mt5_path="", login="test", password="x", server="S")
        self.assertFalse(result.ok)
        self.assertIn("數字", result.reason)

    def test_running_terminal_is_not_started_a_second_time(self):
        """第二個實例會讓兩個 EA 讀同一份 commands.json —— 同一張單下兩次。"""
        with TemporaryDirectory() as tmp:
            terminal = _fake_install(Path(tmp))
            (terminal.parent / "MQL5" / "Files" / "account_info.json").write_text(
                "{}", encoding="ascii")
            with mock.patch.object(onboard.subprocess, "Popen") as popen:
                result = onboard.onboard(mt5_path=str(terminal.parent),
                                         login="441064104", password="x",
                                         server="Exness-MT5Real43")
            popen.assert_not_called()
        self.assertTrue(result.ok)
        self.assertIn("已經在運作", result.reason)

    def test_stale_bridge_does_not_count_as_running(self):
        with TemporaryDirectory() as tmp:
            terminal = _fake_install(Path(tmp))
            probe = terminal.parent / "MQL5" / "Files" / "account_info.json"
            probe.write_text("{}", encoding="ascii")
            old = time.time() - (onboard.FRESH_SECONDS + 60)
            import os
            os.utime(probe, (old, old))
            self.assertFalse(onboard.bridge_is_fresh(terminal))


class PasswordNeverPersistedTests(unittest.TestCase):
    """券商密碼一旦落到設定檔或 Hub 上，責任性質就完全不同了。"""

    def test_client_settings_have_no_password_key(self):
        from copy_trader.central.web_launcher import LauncherState

        keys = LauncherState.defaults(type("X", (), {"role": "client"})())
        self.assertIn("mt5_login", keys)
        self.assertIn("mt5_server", keys)
        for key in keys:
            self.assertNotIn("password", key.lower(),
                             f"會員端設定不該有密碼欄位，但看到 {key}")


if __name__ == "__main__":
    unittest.main()
