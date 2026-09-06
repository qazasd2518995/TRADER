"""管理端角色，以及訊號端心跳。

管理端跟訊號端連的是同一個 Hub。只要管理端有辦法啟動，超高頻策略就會對
同一個 Hub 發單 —— 兩台同時發 = 會員重複下單。所以「管理端不能發布」是
安全保證，不是 UI 細節，這裡當作不可退讓的行為來測。
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging" / "pyinstaller"))

from copy_trader.central import webui
from copy_trader.central.hub_server import CentralHeartbeat
from copy_trader.central.web_launcher import LauncherState, _infer_role


class RoleInferenceTests(unittest.TestCase):
    def test_explicit_roles(self):
        self.assertEqual(_infer_role("admin"), "admin")
        self.assertEqual(_infer_role("central"), "central")
        self.assertEqual(_infer_role("client"), "client")

    def test_unknown_default_falls_back(self):
        self.assertEqual(_infer_role("nonsense"), "client")

    def test_admin_wins_over_signal_keywords(self):
        """檔名同時含「訊號」和「管理」時，絕不能猜成會發訊號的那一個。"""
        original = sys.argv[0]
        try:
            sys.argv[0] = "黃金訊號管理端.exe"
            self.assertEqual(_infer_role(None), "admin")
        finally:
            sys.argv[0] = original


class AdminCannotPublishTests(unittest.TestCase):
    def test_start_service_is_refused(self):
        state = LauncherState("admin")
        with self.assertRaises(PermissionError):
            state.start_service()
        self.assertFalse(state.is_running())

    def test_refusal_is_not_a_race(self):
        """連續按也不能有任何一次成功。"""
        state = LauncherState("admin")
        for _ in range(5):
            with self.assertRaises(PermissionError):
                state.start_service()
        self.assertIsNone(state.worker)

    def test_identity_is_separate_from_the_signal_side(self):
        state = LauncherState("admin")
        self.assertIn("管理端", state.title)
        # 設定檔要各自獨立，否則兩個角色會互相蓋掉對方的設定
        self.assertTrue(state.settings_path.name.startswith("admin_"))
        self.assertNotEqual(state.settings_path,
                            LauncherState("central").settings_path)


class AdminUITests(unittest.TestCase):
    class _State:
        def __init__(self, role):
            self.role = role
            self.title = "t"
            self.auth = None

    def _inputs(self, role):
        html = webui.render(self._State(role))
        return set(re.findall(r'<(?:input|textarea)[^>]*id="([a-z_]+)"', html)), html

    def test_admin_only_has_connection_settings(self):
        ids, _ = self._inputs("admin")
        self.assertIn("hub_url", ids)
        self.assertIn("token", ids)
        for gone in ("line_database_path", "line_chats", "market_mt5_files_dir",
                     "ultra_strategy_enabled", "host", "port"):
            self.assertNotIn(gone, ids, f"管理端不該出現 {gone}")

    def test_signal_side_controls_are_hidden_for_admin(self):
        _, html = self._inputs("admin")
        # CSS 規則要在，否則 signal-only 標記等於沒作用
        self.assertIn('body[data-role="admin"] .client-only', html)
        self.assertIn(".signal-only { display: none; }", html)
        # 開始／停止與影子對照都要標上 signal-only
        self.assertRegex(html, r'id="start"[^>]*|signal-only[^>]*id="start"')
        self.assertIn('signal-only" id="shadowCard"', html)

    def test_admin_keeps_the_member_admin_surface(self):
        _, html = self._inputs("admin")
        for keep in ("viewMembers", "ibRows", "cenStats", "refreshCentralStatus"):
            self.assertIn(keep, html)

    def test_other_roles_are_unchanged(self):
        central_ids, central = self._inputs("central")
        self.assertIn("line_database_path", central_ids)
        self.assertIn("shadowCard", central)
        client_ids, _ = self._inputs("client")
        self.assertNotIn("hub_url", client_ids)   # 會員永遠看不到 token/hub


class HeartbeatStoreTests(unittest.TestCase):
    def test_empty_until_reported(self):
        """沒回報過就是空的 —— Hub 不能編一個假的「正常」出來。"""
        self.assertEqual(CentralHeartbeat().snapshot(), {})

    def test_round_trip(self):
        beat = CentralHeartbeat()
        beat.update({"status": "運行中", "device": "SIGNAL-PC", "line_ok": True,
                     "published_today": 7, "last_publish_at": 1700000000.0,
                     "shadow": {"settled": 3, "pending": 1}})
        snap = beat.snapshot()
        self.assertEqual(snap["status"], "運行中")
        self.assertEqual(snap["published_today"], 7)
        self.assertTrue(snap["line_ok"])
        self.assertEqual(snap["shadow"]["settled"], 3)
        self.assertGreater(snap["reported_at"], 0)

    def test_latest_report_wins(self):
        beat = CentralHeartbeat()
        beat.update({"status": "運行中"})
        beat.update({"status": "已停止"})
        self.assertEqual(beat.snapshot()["status"], "已停止")

    def test_garbage_fields_do_not_crash(self):
        beat = CentralHeartbeat()
        beat.update({"published_today": "abc", "started_at": None,
                     "last_publish_at": "x", "shadow": "not a dict"})
        snap = beat.snapshot()
        self.assertEqual(snap["published_today"], 0)
        self.assertIsNone(snap["started_at"])
        self.assertEqual(snap["shadow"], {})

    def test_long_strings_are_trimmed(self):
        beat = CentralHeartbeat()
        beat.update({"line_detail": "x" * 5000, "version": "y" * 500})
        snap = beat.snapshot()
        self.assertLessEqual(len(snap["line_detail"]), 200)
        self.assertLessEqual(len(snap["version"]), 40)

    def test_snapshot_is_a_copy(self):
        beat = CentralHeartbeat()
        beat.update({"status": "運行中"})
        beat.snapshot()["status"] = "被改掉"
        self.assertEqual(beat.snapshot()["status"], "運行中")


class PackagingTests(unittest.TestCase):
    """打包設定：管理端的映像檔裡不該有任何發布路徑。"""

    def test_admin_excludes_every_publishing_path(self):
        from _common import excludes, hidden

        blocked = excludes("admin")
        for module in ("copy_trader.central.signal_collector",
                       "copy_trader.central.ultra_strategy",
                       "copy_trader.line_db", "apsw"):
            self.assertIn(module, blocked)
        # 也不該被 hiddenimports 從後門帶進來
        for module in hidden("admin", "macos"):
            self.assertNotIn("signal_collector", module)
            self.assertNotIn("ultra_strategy", module)
            self.assertNotIn("line_db", module)

    def test_other_roles_still_get_what_they_need(self):
        from _common import hidden

        central = hidden("central", "macos")
        self.assertIn("copy_trader.central.signal_collector", central)
        self.assertIn("copy_trader.central.exec_shadow", central)
        self.assertIn("copy_trader.central.mt5_client_agent",
                      hidden("client", "macos"))


if __name__ == "__main__":
    unittest.main()
