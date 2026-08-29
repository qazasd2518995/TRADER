"""自動排程的回歸測試。

需求(2026-08-30):會員端要能設「每天幾點開始跟單、幾點停」。等級只決定「有沒有
這個功能」—— 進階版(PRO)以上才有,不分單一/多組/進階,有的話幾組與能不能挑星期
都一樣。跨午夜要成立 —— 黃金是通宵盤,「晚上開盤跟到凌晨」是最常見的用法。

這裡測的是純邏輯:時間視窗判定 + 等級閘門 + 只在跨越邊界時動作。
"""
from __future__ import annotations

import json
import time
import unittest

from copy_trader.central.membership import SCHEDULE_LIMIT, tier_entitlements
from copy_trader.central.web_launcher import LauncherState, _schedule_active


def moment(weekday: int, hour: int, minute: int = 0) -> time.struct_time:
    """做一個假的 struct_time。只有 tm_wday / tm_hour / tm_min 會被讀到。

    2026-08-31 是星期一,所以「日 = 31 + weekday」剛好對上 tm_wday。
    """
    return time.struct_time((2026, 8, 31 + weekday, hour, minute, 0, weekday, 243 + weekday, 0))


class ScheduleWindowTests(unittest.TestCase):
    def test_plain_window(self):
        s = {"start": "09:00", "end": "17:00", "days": []}
        self.assertFalse(_schedule_active(s, moment(0, 8, 59)))
        self.assertTrue(_schedule_active(s, moment(0, 9, 0)))
        self.assertTrue(_schedule_active(s, moment(0, 16, 59)))
        self.assertFalse(_schedule_active(s, moment(0, 17, 0)))   # 結束時刻本身不算

    def test_overnight_window_wraps(self):
        s = {"start": "21:00", "end": "02:00", "days": []}
        self.assertTrue(_schedule_active(s, moment(2, 21, 0)))
        self.assertTrue(_schedule_active(s, moment(2, 23, 59)))
        self.assertTrue(_schedule_active(s, moment(3, 1, 59)))
        self.assertFalse(_schedule_active(s, moment(3, 2, 0)))
        self.assertFalse(_schedule_active(s, moment(3, 12, 0)))

    def test_overnight_weekday_follows_the_day_it_started(self):
        # 只勾週五:跟到週六凌晨兩點仍然算週五那一段,不必另外把週六勾起來
        s = {"start": "21:00", "end": "02:00", "days": [4]}
        self.assertTrue(_schedule_active(s, moment(4, 22, 0)))    # 週五晚上
        self.assertTrue(_schedule_active(s, moment(5, 1, 0)))     # 週六凌晨 = 週五那段
        self.assertFalse(_schedule_active(s, moment(5, 22, 0)))   # 週六晚上不跟

    def test_weekday_filter(self):
        s = {"start": "09:00", "end": "17:00", "days": [0, 1, 2, 3, 4]}
        self.assertTrue(_schedule_active(s, moment(4, 10)))
        self.assertFalse(_schedule_active(s, moment(5, 10)))      # 週六

    def test_bad_or_empty_window_is_inactive(self):
        for bad in ({"start": "", "end": "17:00"}, {"start": "25:00", "end": "17:00"},
                    {"start": "09:00", "end": "09:00"}, {}):
            self.assertFalse(_schedule_active(bad, moment(0, 10)), bad)


class _FakeState(LauncherState):
    """不碰檔案系統的 LauncherState:只留排程要用到的東西。"""

    def __init__(self, tier_ent, schedules, running=False):
        self.role = "client"
        self.auth = {"entitlements": tier_ent}
        self.settings = {"auto_schedules": json.dumps(schedules, ensure_ascii=False)}
        self._sched_prev = None
        self._running = running
        self.started = 0
        self.stopped = 0
        self.logs_written = []

    def is_running(self):
        return self._running

    def start_service(self):
        self.started += 1
        self._running = True

    def stop_service(self):
        self.stopped += 1
        self._running = False

    def _log(self, message):
        self.logs_written.append(message)


TRIAL = {"schedule": False}
BASIC = {"schedule": False}
ADVANCED = {"schedule": True}
FLAGSHIP = {"schedule": True}

WIN = {"enabled": True, "start": "09:00", "end": "17:00", "days": [0]}


class ScheduleEntitlementTests(unittest.TestCase):
    def test_only_advanced_and_above_have_schedules(self):
        self.assertFalse(tier_entitlements("trial")["schedule"])
        self.assertFalse(tier_entitlements("basic")["schedule"])
        self.assertTrue(tier_entitlements("advanced")["schedule"])
        self.assertTrue(tier_entitlements("flagship")["schedule"])

    def test_tiers_without_the_feature_get_nothing(self):
        for ent in (TRIAL, BASIC):
            st = _FakeState(ent, [WIN, WIN])
            self.assertEqual(st.active_schedules(), [])
            self.assertIsNone(st.schedule_wants_running())

    def test_advanced_and_flagship_behave_identically(self):
        rows = [WIN, dict(WIN, start="20:00", end="22:00")]
        self.assertEqual(_FakeState(ADVANCED, rows).active_schedules(),
                         _FakeState(FLAGSHIP, rows).active_schedules())

    def test_weekday_choice_is_kept_for_every_tier_that_has_the_feature(self):
        for ent in (ADVANCED, FLAGSHIP):
            self.assertEqual(_FakeState(ent, [WIN]).active_schedules()[0]["days"], [0])

    def test_panel_limit_caps_the_list(self):
        st = _FakeState(ADVANCED, [WIN] * (SCHEDULE_LIMIT + 5))
        self.assertEqual(len(st.active_schedules()), SCHEDULE_LIMIT)

    def test_all_seven_days_collapses_to_every_day(self):
        st = _FakeState(FLAGSHIP, [dict(WIN, days=[0, 1, 2, 3, 4, 5, 6])])
        self.assertEqual(st.active_schedules()[0]["days"], [])

    def test_disabled_and_broken_rows_are_dropped(self):
        st = _FakeState(FLAGSHIP, [dict(WIN, enabled=False),
                                   {"enabled": True, "start": "x", "end": "17:00"},
                                   "not a dict"])
        self.assertEqual(st.active_schedules(), [])

    def test_broken_json_is_ignored(self):
        st = _FakeState(FLAGSHIP, [])
        st.settings["auto_schedules"] = "{oops"
        self.assertEqual(st.active_schedules(), [])


class ScheduleTickTests(unittest.TestCase):
    def _tick_at(self, st, wants):
        st.schedule_wants_running = lambda now=None: wants   # type: ignore[assignment]
        st.schedule_tick()

    def test_starts_on_entering_window(self):
        st = _FakeState(ADVANCED, [WIN])
        self._tick_at(st, True)
        self.assertEqual((st.started, st.stopped), (1, 0))

    def test_stops_on_leaving_window(self):
        st = _FakeState(ADVANCED, [WIN], running=True)
        self._tick_at(st, True)      # 先記住「在時段內」
        self._tick_at(st, False)
        self.assertEqual(st.stopped, 1)

    def test_manual_stop_inside_window_is_respected(self):
        # 進了時段自動開始 → 會員自己按停止 → 不該每 20 秒被拉回來
        st = _FakeState(ADVANCED, [WIN])
        self._tick_at(st, True)
        self.assertEqual(st.started, 1)
        st._running = False          # 會員手動停止
        self._tick_at(st, True)
        self._tick_at(st, True)
        self.assertEqual(st.started, 1)

    def test_no_schedule_means_hands_off(self):
        st = _FakeState(TRIAL, [WIN], running=True)
        st.schedule_tick()
        self.assertEqual((st.started, st.stopped), (0, 0))

    def test_not_logged_in_does_not_start(self):
        st = _FakeState(ADVANCED, [WIN])
        st.auth = None
        self._tick_at(st, True)
        self.assertEqual(st.started, 0)


if __name__ == "__main__":
    unittest.main()
