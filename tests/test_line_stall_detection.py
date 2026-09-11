"""LINE 靜默停擺的偵測。

2026-09-11 實際事故：重開機之後 LINE 沒有自動啟動(它不在啟動資料夾也不在 Run
機碼)，十三個小時完全收不到任何訊號。而訊號中心對外回報的是：

    line_ok = True   line_cursor = ok   心跳 17 秒前

**全綠。** 因為每一項量的都是「能不能讀到那個檔案」—— 檔案一直都在、讀得到、
integrity_check 也照樣回 ok。沒有任何一項量得到「LINE 有沒有在往裡面寫」。

同一個盲點有三種進法，而且都不會拋例外：
  1. LINE 沒開
  2. LINE 被登出(強制關閉之後會這樣)
  3. 訊號中心握著的是已經被換掉的檔案句柄(LINE 重新登入時換檔或換金鑰)

唯一抓得到的指標是「整個資料庫多久沒有新列」。這裡驗證那個指標，以及連線
真的壞掉時擷取端會重建而不是每秒噴一次同樣的例外到天亮。
"""
from __future__ import annotations

import unittest
from unittest import mock

from copy_trader.central.web_launcher import (
    LINE_QUIET_ALERT_SECONDS,
    LINE_REBUILD_AFTER_FAILURES,
    LauncherState,
)


def _state() -> LauncherState:
    state = LauncherState.__new__(LauncherState)
    state._line_rowid_seen = 0
    state._line_rowid_at = 0.0
    state._line_quiet_logged = False
    return state


class QuietDetectionTests(unittest.TestCase):
    def test_new_rows_keep_the_counter_at_zero(self):
        state = _state()
        self.assertEqual(state._line_quiet_seconds(100, 1000.0), 0.0)
        self.assertEqual(state._line_quiet_seconds(105, 1060.0), 0.0)
        self.assertEqual(state._line_quiet_seconds(106, 1120.0), 0.0)

    def test_frozen_rowid_makes_the_quiet_time_grow(self):
        state = _state()
        state._line_quiet_seconds(100, 1000.0)
        self.assertEqual(state._line_quiet_seconds(100, 1060.0), 60.0)
        self.assertEqual(state._line_quiet_seconds(100, 1600.0), 600.0)

    def test_crossing_the_threshold_logs_once_not_every_minute(self):
        state = _state()
        state._line_quiet_seconds(100, 1000.0)
        over = 1000.0 + LINE_QUIET_ALERT_SECONDS + 1
        with self.assertLogs("copy_trader.central.web_launcher", "WARNING") as caught:
            state._line_quiet_seconds(100, over)
        self.assertIn("沒有任何新訊息", caught.output[0])
        # 心跳每分鐘一次，門檻過了就每分鐘噴一行的話，日誌會被洗掉 ——
        # 那正是這個功能要解決的問題本身。
        with mock.patch.object(
            __import__("copy_trader.central.web_launcher", fromlist=["logger"]),
            "logger",
        ) as fake_logger:
            state._line_quiet_seconds(100, over + 60)
            state._line_quiet_seconds(100, over + 120)
            fake_logger.warning.assert_not_called()

    def test_recovery_rearms_the_warning(self):
        state = _state()
        state._line_quiet_seconds(100, 1000.0)
        over = 1000.0 + LINE_QUIET_ALERT_SECONDS + 1
        with self.assertLogs("copy_trader.central.web_launcher", "WARNING"):
            state._line_quiet_seconds(100, over)
        state._line_quiet_seconds(101, over + 10)          # LINE 回來了
        with self.assertLogs("copy_trader.central.web_launcher", "WARNING") as again:
            state._line_quiet_seconds(101, over + 10 + LINE_QUIET_ALERT_SECONDS + 1)
        self.assertIn("沒有任何新訊息", again.output[0])

    def test_unknown_rowid_reports_nothing_rather_than_a_false_alarm(self):
        state = _state()
        self.assertIsNone(state._line_quiet_seconds(0, 1000.0))


class RebuildPolicyTests(unittest.TestCase):
    def test_rebuild_threshold_tolerates_a_transient_lock(self):
        """LINE 正在寫、資料庫瞬間鎖住會失敗一兩次就恢復，不該為此重建連線。

        但也不能無限忍 —— 原本的程式碼是「永遠不重建」，連線壞掉之後每秒噴一次
        同樣的例外，到天亮都不會自己好。
        """
        self.assertGreater(LINE_REBUILD_AFTER_FAILURES, 1)
        self.assertLessEqual(LINE_REBUILD_AFTER_FAILURES, 10)


class ProviderProbeTests(unittest.TestCase):
    def test_latest_insert_rowid_ignores_the_chat_filter(self):
        """整庫計數不能只看訊號來源群 —— 那兩個群安靜好幾小時是正常的。"""
        import copy_trader.line_db.sqlite_provider as sp

        executed = []

        class _Conn:
            def execute(self, sql, *args):
                executed.append(" ".join(sql.split()))
                return self

            def fetchone(self):
                return (316696,)

        provider = sp.SQLiteLineDatabaseProvider.__new__(sp.SQLiteLineDatabaseProvider)
        provider.connect = lambda: _Conn()
        self.assertEqual(provider.latest_insert_rowid(), 316696)
        self.assertEqual(len(executed), 1)
        self.assertNotIn("_chatId", executed[0])
        self.assertIn("max(rowid)", executed[0])



class HeartbeatFieldTests(unittest.TestCase):
    """Hub 的心跳是白名單，新欄位沒登記就會被安靜丟掉。

    2026-09-11 踩過：訊號端算好 line_quiet_seconds 也送出去了，Hub 的
    CentralHeartbeat.update() 卻只收固定那幾個鍵，於是後台永遠看到 None ——
    看起來像功能沒做，其實是在最後一哩被丟掉。白名單本身是對的（不讓訊號端
    塞任意欄位進來），所以要做的是補登記，不是拿掉白名單。
    """

    def test_quiet_seconds_survives_the_whitelist(self):
        from copy_trader.central.hub_server import CentralHeartbeat

        hb = CentralHeartbeat()
        hb.update({"device": "PC", "status": "運行中", "line_ok": True,
                   "line_cursor": "ok", "line_quiet_seconds": 1234.5})
        self.assertEqual(hb.snapshot()["line_quiet_seconds"], 1234.5)

    def test_missing_quiet_seconds_stays_unknown_not_zero(self):
        """舊版訊號端不會回報這個欄位。當成 0 會變成「永遠正常」的假綠燈。"""
        from copy_trader.central.hub_server import CentralHeartbeat

        hb = CentralHeartbeat()
        hb.update({"device": "PC", "status": "運行中", "line_ok": True})
        self.assertIsNone(hb.snapshot()["line_quiet_seconds"])


if __name__ == "__main__":
    unittest.main()
