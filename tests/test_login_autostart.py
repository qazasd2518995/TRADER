"""登入之後要自己開始跟單。

`auto_start` 原本只在程式啟動時讀一次，但那個時間點通常還沒登入
（start_service 會擋未登入），於是全新會員的實際體驗是：

    裝好 → 啟動 → 自動開始失敗（尚未登入）→ 登入 → **什麼都沒發生**

他會坐在那裡不知道為什麼沒跟單。2026-09-09 把 instance 4 從 ops4 轉給
trial04 時實際踩到：登入成功、也有在回報，但 running=False 一直沒跟單。

設定的註解本來就寫著「會員端拿掉了開關，登入後自動開始跟單」——
意圖一直是這樣，只是沒實作。
"""
from __future__ import annotations

import unittest
from unittest import mock

from copy_trader.central import web_launcher as W


def _state(**kw):
    st = W.LauncherState.__new__(W.LauncherState)
    st.role = "client"
    st.settings = {"auto_start": "true"}
    st.worker = None
    st.client_agent = None
    st.auth = None
    st.auth_error = ""
    st.auth_checked_at = 0.0
    st.usage = None
    st.status = ""
    st.logs_written = []
    st.started = 0
    st._log = st.logs_written.append
    st._save_session = lambda: None
    st.start_service = lambda: setattr(st, "started", st.started + 1)
    st.schedule_wants_running = lambda: None
    st.is_running = lambda: False
    st._hub_base = lambda: "http://hub"
    st._device_label = lambda: "PC"
    for k, v in kw.items():
        setattr(st, k, v)
    return st


def _ok_login(member=None):
    """假裝 Hub 回了一次成功登入。"""
    return (200, {"ok": True, "member": member or {
        "username": "trial04", "tier_label": "進階版",
        "session_token": "tok", "entitlements": {"sources": ["A", "B"]},
    }})


class LoginStartsFollowingTests(unittest.TestCase):
    def _login(self, st):
        with mock.patch.object(W.LauncherState, "_hub_call",
                               lambda self, path, body=None: _ok_login()):
            return st.login("trial04", "pw")

    def test_login_starts_the_service(self):
        st = _state()
        out = self._login(st)
        self.assertTrue(out["ok"], out)
        self.assertEqual(st.started, 1, "登入成功卻沒有開始跟單")

    def test_respects_auto_start_off(self):
        st = _state(settings={"auto_start": "false"})
        self._login(st)
        self.assertEqual(st.started, 0, "使用者關掉自動開始，就不該自作主張")

    def test_respects_the_schedule(self):
        """「早上九點才開始跟」設了就要算數，不能一登入就跟上。"""
        st = _state(schedule_wants_running=lambda: False)
        self._login(st)
        self.assertEqual(st.started, 0)
        self.assertEqual(st.status, "等待排程時段")

    def test_starts_when_inside_the_schedule(self):
        st = _state(schedule_wants_running=lambda: True)
        self._login(st)
        self.assertEqual(st.started, 1)

    def test_does_not_restart_when_already_running(self):
        st = _state(is_running=lambda: True)
        self._login(st)
        self.assertEqual(st.started, 0)

    def test_start_failure_does_not_break_login(self):
        """開不起來也要讓他登進去 —— 至少能看設定、能自己按開始。"""
        def boom():
            raise RuntimeError("MT5 資料夾不存在")
        st = _state(start_service=boom)
        out = self._login(st)
        self.assertTrue(out["ok"], "自動開始失敗不該讓登入本身失敗")
        self.assertTrue(any("自動開始失敗" in line for line in st.logs_written))

    def test_failed_login_starts_nothing(self):
        st = _state()
        with mock.patch.object(W.LauncherState, "_hub_call",
                               lambda self, p, b=None: (401, {"ok": False,
                                                              "error": "bad_credentials"})):
            out = st.login("trial04", "wrong")
        self.assertFalse(out["ok"])
        self.assertEqual(st.started, 0)


if __name__ == "__main__":
    unittest.main()
