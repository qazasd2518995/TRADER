"""LINE 通知停掉的時候，要有人看得到。

2026-09-09：訊號正常發布、三台 MT5 都掛上單了，但群組完全沒收到通知。
程式沒有 bug —— LINE 官方帳號的月推播額度**按送達人數計**，4 人的群組推
一則就扣 4，免費方案 200 則等於一個月只發得了 50 次訊號。額度在 199/200
見底，push 一律 429。

失敗只寫一行 logger.warning，而 Hub 的 log 被每秒好幾次的 /signals 輪詢
洗掉，等於沒人看得到。這個測試釘住兩件事：

  1. push 失敗的原因要留在記憶體裡，/admin/line/status 問得到
  2. quota() 要算出「還能發幾次訊號」，不是丟一個原始的 200 給人自己除
"""
from __future__ import annotations

import unittest
import urllib.error
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from copy_trader.central.hub_server import LineNotifyState, _line_error_zh


def _state(groups=("Cgroup1",), token="tok"):
    tmp = TemporaryDirectory()
    st = LineNotifyState(Path(tmp.name) / "line.json", token=token, secret="")
    st._groups = {g: {"added_at": 0.0, "name": ""} for g in groups}   # noqa: SLF001
    st._tmp = tmp                                                     # 撐住生命週期
    return st


def _http_error(code, body=b'{"message":"You have reached your monthly limit."}'):
    return urllib.error.HTTPError("https://api.line.me", code, "err", {},
                                  __import__("io").BytesIO(body))


class PushFailureIsVisibleTests(unittest.TestCase):
    def test_quota_exhausted_leaves_a_readable_reason(self):
        st = _state()
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(429)):
            self.assertEqual(st.push_text("hi"), 0)
        self.assertIn("額度", st.last_error, "後台要看得懂，不能只丟 HTTP 429")
        self.assertGreater(st.last_error_at, 0)

    def test_bad_token_says_so(self):
        st = _state()
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(401)):
            st.push_text("hi")
        self.assertIn("金鑰", st.last_error)

    def test_network_failure_is_also_recorded(self):
        st = _state()
        with mock.patch("urllib.request.urlopen", side_effect=OSError("timed out")):
            st.push_text("hi")
        self.assertIn("LINE", st.last_error)

    def test_success_clears_nothing_but_stamps_last_ok(self):
        st = _state()
        with mock.patch("urllib.request.urlopen", mock.mock_open(read_data=b"{}")):
            self.assertEqual(st.push_text("hi"), 1)
        self.assertGreater(st.last_ok_at, 0)
        self.assertEqual(st.last_error, "")

    def test_disabled_notifier_never_pretends_to_send(self):
        st = _state(token="")
        self.assertFalse(st.enabled)
        self.assertEqual(st.push_text("hi"), 0)


class QuotaTests(unittest.TestCase):
    """後台要的是「還能發幾次訊號」，不是原始額度數字。"""

    def _with_api(self, st, mapping):
        return mock.patch.object(LineNotifyState, "_api_get",
                                 lambda self, path: mapping.get(path))

    def _mapping(self, limit=200, used=199, members=4):
        return {
            "/v2/bot/message/quota": {"type": "limited", "value": limit},
            "/v2/bot/message/quota/consumption": {"totalUsage": used},
            "/v2/bot/group/Cgroup1/members/count": {"count": members},
        }

    def test_counts_signals_not_messages(self):
        """實際出事的那組數字：剩 1 則額度、群組 4 人 → 一次都發不了。"""
        st = _state()
        with self._with_api(st, self._mapping()):
            q = st.quota()
        self.assertEqual(q["remaining"], 1)
        self.assertEqual(q["cost_per_signal"], 4)
        self.assertEqual(q["signals_left"], 0, "剩 1 則卻要扣 4，不能顯示成還能發")

    def test_healthy_account(self):
        st = _state()
        with self._with_api(st, self._mapping(limit=3000, used=200)):
            q = st.quota()
        self.assertEqual(q["signals_left"], 700)

    def test_cost_sums_every_group(self):
        st = _state(groups=("Cgroup1", "Cgroup2"))
        mapping = self._mapping(limit=200, used=0)
        mapping["/v2/bot/group/Cgroup2/members/count"] = {"count": 6}
        with self._with_api(st, mapping):
            q = st.quota()
        self.assertEqual(q["cost_per_signal"], 10)
        self.assertEqual(q["signals_left"], 20)

    def test_unlimited_plan_has_no_signals_left(self):
        """付費無上限方案沒有 value —— 不能因此算出 0 然後在後台亮紅燈。"""
        st = _state()
        with self._with_api(st, {"/v2/bot/message/quota": {"type": "none"},
                                 "/v2/bot/message/quota/consumption": {"totalUsage": 9000},
                                 "/v2/bot/group/Cgroup1/members/count": {"count": 4}}):
            q = st.quota()
        self.assertEqual(q["type"], "none")
        self.assertNotIn("signals_left", q)
        self.assertNotIn("remaining", q)

    def test_result_is_cached(self):
        """後台每 60 秒重整一次，每次都打三支 LINE API 沒必要。"""
        st = _state()
        mapping = self._mapping()
        calls = []

        def fake(_self, path):
            calls.append(path)
            return mapping.get(path)

        with mock.patch.object(LineNotifyState, "_api_get", fake):
            st.quota()
            n = len(calls)
            self.assertTrue(n, "第一次要真的去問")
            st.quota()
            self.assertEqual(len(calls), n, "第二次應該吃快取")
            st.quota(force=True)
            self.assertGreater(len(calls), n, "force=True 要繞過快取")

    def test_cache_expires(self):
        st = _state()
        with self._with_api(st, self._mapping(used=100)):
            st.quota()
            st._quota_at = 0.0                       # noqa: SLF001 假裝過期
            with self._with_api(st, self._mapping(used=196)):
                q = st.quota()
        self.assertEqual(q["used"], 196)

    def test_push_failure_invalidates_the_cache(self):
        """推播剛失敗時最該看到新的額度，這時還餵舊快取就是騙人。"""
        st = _state()
        with self._with_api(st, self._mapping(used=100)):
            st.quota()
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(429)):
            st.push_text("hi")
        self.assertEqual(st._quota_at, 0.0)          # noqa: SLF001

    def test_api_down_keeps_the_last_known_numbers(self):
        st = _state()
        with self._with_api(st, self._mapping(used=100)):
            st.quota()
        with self._with_api(st, {}):
            st._quota_at = 0.0                       # noqa: SLF001
            q = st.quota()
        self.assertEqual(q["used"], 100, "問不到就沿用舊的，不要變成空白")

    def test_disabled_notifier_returns_empty(self):
        st = _state(token="")
        self.assertEqual(st.quota(), {})


class ErrorWordingTests(unittest.TestCase):
    def test_every_common_code_is_translated(self):
        for code in (400, 401, 403, 429):
            self.assertNotIn("HTTP", _line_error_zh(code, "detail"),
                             f"{code} 應該有中文說法")

    def test_unknown_code_still_says_something(self):
        self.assertIn("500", _line_error_zh(500, "boom"))


if __name__ == "__main__":
    unittest.main()
