"""用 LINE 桌面版播報到社群 —— 會出人命的是「以為送出去了其實沒有」。

官方帳號 Bot 進不去 LINE 社群(OpenChat)，所以社群這條路只能驅動登入中的
桌面版 client。它跟 API 不一樣的地方在於：**沒有回應碼**。視窗被關掉、
Enter 被吃掉、螢幕鎖住，全都長得像「成功」。所以這裡釘住的每一條，都是
「不能回報成功」的情境：

  1. 輸入框有使用者打到一半的字 -> 不送、也不准蓋掉
  2. 寫進去的內容跟要發的不一樣 -> 清空並中止（寧可不發，不可發錯）
  3. Enter 沒被吃掉、輸入框還有字 -> 失敗，且要把殘留清掉
  4. 送出後資料庫裡查不到那則 -> 失敗（這是唯一能證明真的送到的證據）
  5. 螢幕鎖定 -> 直接失敗，而且不浪費重試次數
  6. 同一個 event_id 重送 -> 社群不能收到第二則一樣的訊號
"""
from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

from copy_trader.central import line_desktop_sender as lds


class _FakeValuePattern:
    def __init__(self, box):
        self._box = box

    @property
    def Value(self):
        return self._box.text

    def SetValue(self, value):
        self._box.text = value


class _FakeBox:
    """假的輸入框。swallow_enter=True 模擬 Enter 沒生效。"""

    def __init__(self, text="", swallow_enter=False):
        self.text = text
        self.swallow_enter = swallow_enter
        self.sent = []
        self.focused = False

    def GetValuePattern(self):
        return _FakeValuePattern(self)

    def SetFocus(self):
        self.focused = True

    def SendKey(self, key, waitTime=0.5):
        if self.swallow_enter:
            return
        self.sent.append(self.text)
        self.text = ""


class _FakeInitializer:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _fake_uiautomation():
    module = types.ModuleType("uiautomation")
    module.UIAutomationInitializerInThread = _FakeInitializer
    module.SetGlobalSearchTimeout = lambda *args: None
    module.GetRootControl = lambda: None
    module.Keys = types.SimpleNamespace(VK_RETURN=13)
    return module


def _sender(box, *, receipt=(101, "msg-1")):
    sender = lds.LineDesktopSender("測試社群", database_path="x.edb", min_interval=0.0)
    sender._find_window = lambda auto: types.SimpleNamespace(NativeWindowHandle=0)
    sender._find_input = lambda window: box
    sender.resolve_chat = lambda: ("chat-1", "me", "openchat")
    sender._max_rowid = lambda chat_id: 0
    sender._await_receipt = lambda *args: receipt
    return sender


class DesktopSendTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(sys.modules, {"uiautomation": _fake_uiautomation()})
        patcher.start()
        self.addCleanup(patcher.stop)
        unlocked = mock.patch.object(lds, "workstation_locked", return_value=False)
        unlocked.start()
        self.addCleanup(unlocked.stop)

    def test_sends_and_confirms_from_the_database(self):
        box = _FakeBox()
        result = _sender(box).send("📌 新訊號\nXAUUSD 買進 BUY")
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(result.message_id, "msg-1")
        self.assertEqual(result.rowid, 101)
        self.assertEqual(box.sent, ["📌 新訊號\nXAUUSD 買進 BUY"])

    def test_never_clobbers_a_draft(self):
        box = _FakeBox(text="使用者打到一半")
        result = _sender(box).send("訊號")
        self.assertFalse(result.ok)
        self.assertIn("草稿", result.reason)
        self.assertEqual(box.text, "使用者打到一半")   # 原封不動
        self.assertEqual(box.sent, [])

    def test_aborts_when_the_box_does_not_hold_what_we_staged(self):
        box = _FakeBox()

        class Tampered(_FakeValuePattern):
            def SetValue(self, value):
                self._box.text = value[:-1] if value else value    # 少一個字

        box.GetValuePattern = lambda: Tampered(box)
        result = _sender(box).send("訊號內容")
        self.assertFalse(result.ok)
        self.assertIn("不符", result.reason)
        self.assertEqual(box.sent, [])

    def test_enter_that_does_nothing_is_a_failure(self):
        box = _FakeBox(swallow_enter=True)
        result = _sender(box).send("訊號")
        self.assertFalse(result.ok)
        self.assertIn("Enter", result.reason)
        # 殘留一定要清掉，否則下一則會被自己的草稿檢查擋住
        self.assertEqual(box.text, "")

    def test_no_database_receipt_means_not_sent(self):
        box = _FakeBox()
        result = _sender(box, receipt=None).send("訊號")
        self.assertFalse(result.ok)
        self.assertIn("資料庫", result.reason)

    def test_receipt_can_be_skipped_when_the_caller_says_so(self):
        box = _FakeBox()
        sender = _sender(box, receipt=None)
        result = sender.send("訊號", verify_receipt=False)
        self.assertTrue(result.ok)
        self.assertEqual(box.sent, ["訊號"])


class PreflightTests(unittest.TestCase):
    """打包漏掉 uiautomation 的話，要在啟動時就講，不能等第一筆訊號。"""

    def test_missing_uiautomation_raises_so_the_feature_is_disabled(self):
        sender = lds.LineDesktopSender("測試社群", database_path="x.edb")
        with mock.patch.dict(sys.modules, {"uiautomation": None}):
            with self.assertRaises(ImportError):
                sender.preflight()

    def test_reports_a_closed_window_without_disabling(self):
        sender = lds.LineDesktopSender("測試社群", database_path="x.edb")
        sender.resolve_chat = lambda: ("chat-1", "me", "openchat")
        sender._find_window = lambda auto: None
        with mock.patch.dict(sys.modules, {"uiautomation": _fake_uiautomation()}):
            result = sender.preflight()
        self.assertFalse(result.ok)
        self.assertIn("視窗", result.reason)

    def test_ready_when_window_and_chat_both_resolve(self):
        sender = lds.LineDesktopSender("測試社群", database_path="x.edb")
        sender.resolve_chat = lambda: ("chat-1", "me", "openchat")
        sender._find_window = lambda auto: object()
        sender._find_input = lambda window: _FakeBox()
        with mock.patch.dict(sys.modules, {"uiautomation": _fake_uiautomation()}):
            result = sender.preflight()
        self.assertTrue(result.ok, result.reason)


class LockedScreenTests(unittest.TestCase):
    def test_locked_screen_fails_without_burning_retries(self):
        with mock.patch.dict(sys.modules, {"uiautomation": _fake_uiautomation()}), \
             mock.patch.object(lds, "workstation_locked", return_value=True):
            box = _FakeBox()
            sender = _sender(box)
            calls = []
            original = sender.send

            def counting(*args, **kwargs):
                calls.append(1)
                return original(*args, **kwargs)

            sender.send = counting
            result = sender.send_with_retry("訊號", attempts=3)
        self.assertFalse(result.ok)
        self.assertIn("螢幕已鎖定", result.reason)
        self.assertEqual(len(calls), 1, "鎖定螢幕等一下也不會好，不該重試")


class LauncherDeduplicationTests(unittest.TestCase):
    """重送同一筆訊號（Hub 那端叫 already_published）不能讓社群收到兩則。"""

    def test_same_event_id_is_announced_once(self):
        from copy_trader.central.web_launcher import LauncherState

        state = LauncherState.__new__(LauncherState)
        state._line_desktop_seen = {}
        sent = []
        state.line_desktop = types.SimpleNamespace(
            send_with_retry=lambda text: sent.append(text)
            or lds.SendResult(True, "ok", message_id="m"))

        payload = {
            "event_id": "evt-1",
            "type": "signal",
            "source": "焦點利潤(yuyu)",
            "message_time": "2026-09-09T23:59:00+08:00",
            "signal": {"direction": "BUY", "symbol": "XAUUSD", "entry_price": 4404.0,
                       "stop_loss": 4399.0, "take_profit": [4410.0]},
        }
        for _ in range(3):
            state._notify_line_desktop(payload)
        for thread in list(__import__("threading").enumerate()):
            if thread is not __import__("threading").current_thread():
                thread.join(timeout=5)
        self.assertEqual(len(sent), 1, f"應該只播報一次，實際 {len(sent)} 次")
        self.assertIn("XAUUSD", sent[0])

    def test_no_sender_configured_is_a_no_op(self):
        from copy_trader.central.web_launcher import LauncherState

        state = LauncherState.__new__(LauncherState)
        state._line_desktop_seen = {}
        state.line_desktop = None
        state._notify_line_desktop({"event_id": "evt-2", "type": "signal"})   # 不能爆



class WindowWaitTests(unittest.TestCase):
    """聊天視窗還沒還原時要等，不是馬上放棄。

    2026-09-11 實際漏掉三則通知：使用者剛重新登入 LINE，同步下來的三筆訊號
    通知全部送不出去 —— 當下 LINE 還沒把獨立的聊天視窗還原回來，而舊的重試
    只等了 3 秒。等下去是安全的：播報是通知不是下單，單子由 Hub 直接發給
    會員端，完全不經過這條路。
    """

    def setUp(self):
        patcher = mock.patch.dict(sys.modules, {"uiautomation": _fake_uiautomation()})
        patcher.start()
        self.addCleanup(patcher.stop)
        unlocked = mock.patch.object(lds, "workstation_locked", return_value=False)
        unlocked.start()
        self.addCleanup(unlocked.stop)
        nosleep = mock.patch.object(lds.time, "sleep", lambda _s: None)
        nosleep.start()
        self.addCleanup(nosleep.stop)

    def test_keeps_waiting_while_the_window_is_missing(self):
        box = _FakeBox()
        sender = _sender(box)
        calls = {"n": 0}

        def flaky(text, **kwargs):
            calls["n"] += 1
            if calls["n"] < 4:          # 前三次視窗都還沒開
                return lds.SendResult(False, "找不到聊天視窗：測試社群（是不是被關掉了）")
            return lds.SendResult(True, "已送出並確認", message_id="m")

        sender.send = flaky
        result = sender.send_with_retry("訊號", attempts=2)
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(calls["n"], 4, "視窗沒開不該算進重試次數")

    def test_gives_up_once_the_wait_window_closes(self):
        box = _FakeBox()
        sender = _sender(box)
        sender.send = lambda text, **kw: lds.SendResult(
            False, "找不到聊天視窗：測試社群（是不是被關掉了）")
        result = sender.send_with_retry("訊號", attempts=2, window_wait=0.0)
        self.assertFalse(result.ok)
        self.assertIn("找不到聊天視窗", result.reason)

    def test_other_failures_still_use_the_attempt_budget(self):
        """「Enter 沒生效」這種等再久也不會好，不該吃掉整個等待視窗。"""
        box = _FakeBox()
        sender = _sender(box)
        calls = {"n": 0}

        def always_fail(text, **kwargs):
            calls["n"] += 1
            return lds.SendResult(False, "Enter 沒有送出訊息，輸入框仍有內容")

        sender.send = always_fail
        result = sender.send_with_retry("訊號", attempts=2)
        self.assertFalse(result.ok)
        self.assertEqual(calls["n"], 2)


if __name__ == "__main__":
    unittest.main()
