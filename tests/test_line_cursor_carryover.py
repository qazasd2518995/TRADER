"""換 database_id 時不能重新 baseline 的回歸測試。

需求(2026-08-30):`database_id` = sha256(路徑 + 檔頭 16 bytes)，而 LINE 大約每天會
改寫一次 .edb 的檔頭。檔頭一變同一個檔案就算出新的 id，於是游標狀態是空的、
`ensure_baseline()` 直接把游標設在「當下最新那一則」—— 上次輪詢之後的訊息全部
靜默跳過。`database_id` 只在程序啟動時算一次，所以觸發點是訊號中心重啟。

實測那天游標檔裡有 5 個 database_id，三次換 id 分別漏掉中頻 3/6/0 則、
高頻 36/21/4 則，其中至少 6 筆是真實報單。

這裡驗證：換 id 之後要接手舊游標，而不是跳到最新。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from copy_trader.line_db.models import LineChatTarget, ResolvedLineChat
from copy_trader.line_db.source import LineDatabaseSource

CHAT_ID = "mchat0001"


class _Provider:
    """只實作 LineDatabaseSource 會用到的部分。"""

    def __init__(self, database_id: str, latest: int):
        self.database_id = database_id
        self._latest = latest
        self.fetched: list[tuple[str, int]] = []

    def resolve_chats(self, targets):
        return [ResolvedLineChat(target=t, chat_id=CHAT_ID, kind="openchat") for t in targets]

    def resolve_sender_ids(self, chat, senders):
        return tuple()

    def latest_rowid(self, chat):
        return self._latest

    def fetch_after(self, chat, rowid, limit=500):
        self.fetched.append((chat.chat_id, rowid))
        return []


def _target():
    return LineChatTarget(name="mid", chat_name="（乘）黃金報單", display_name="中頻",
                          chat_id=CHAT_ID, chat_kind="openchat")


class CursorCarryoverTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / "cursor.json"

    def tearDown(self):
        self._dir.cleanup()

    def _source(self, provider):
        src = LineDatabaseSource(provider, [_target()], state_path=self.path)
        # chats 這個 property 會呼叫 provider.resolve_chats，跑一次把它建好
        src.chats
        return src

    def _state(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_first_run_baselines_at_latest(self):
        src = self._source(_Provider("db-a", 1000))
        src.ensure_baseline()
        chats = self._state()["databases"]["db-a"]["chats"]
        self.assertEqual(chats[CHAT_ID]["last_rowid"], 1000)
        self.assertNotIn("carried_from_rowid", chats[CHAT_ID])

    def test_new_database_id_carries_the_old_cursor(self):
        # 第一次:baseline 在 1000，然後處理到 1200
        src = self._source(_Provider("db-a", 1000))
        src.ensure_baseline()
        with src._lock:
            src._chat_state(src.chats[0]).update({"last_rowid": 1200, "updated_at": 100.0,
                                                  "last_message_id": "m-1200"})
            src._save_state()

        # 檔頭被改寫 → 新的 database_id，這時最新已經到 1500
        src2 = self._source(_Provider("db-b", 1500))
        src2.ensure_baseline()
        state = self._state()["databases"]["db-b"]["chats"][CHAT_ID]
        self.assertEqual(state["last_rowid"], 1200, "應該接手舊游標，不是跳到 1500")
        self.assertEqual(state["carried_from_rowid"], 1200)
        self.assertEqual(state["last_message_id"], "m-1200")

        # 而且下一次 poll 真的會從 1200 往後補讀
        src2.poll()
        self.assertEqual(src2.provider.fetched, [(CHAT_ID, 1200)])

    def test_picks_the_most_recent_of_several_old_ids(self):
        seed = {"version": 2, "databases": {
            "db-old": {"chats": {CHAT_ID: {"last_rowid": 500, "updated_at": 10.0}}, "bindings": {}},
            "db-mid": {"chats": {CHAT_ID: {"last_rowid": 900, "updated_at": 50.0}}, "bindings": {}},
        }}
        self.path.write_text(json.dumps(seed), encoding="utf-8")
        src = self._source(_Provider("db-new", 1500))
        src.ensure_baseline()
        self.assertEqual(self._state()["databases"]["db-new"]["chats"][CHAT_ID]["last_rowid"], 900)

    def test_cursor_beyond_latest_falls_back_to_baseline(self):
        # 舊游標比目前資料庫的最新還大 = 真的換了另一個檔案(換帳號)，
        # 那些 rowid 沒有意義，只能重新 baseline。
        seed = {"version": 2, "databases": {
            "db-other": {"chats": {CHAT_ID: {"last_rowid": 9999, "updated_at": 10.0}}, "bindings": {}},
        }}
        self.path.write_text(json.dumps(seed), encoding="utf-8")
        src = self._source(_Provider("db-new", 1500))
        src.ensure_baseline()
        state = self._state()["databases"]["db-new"]["chats"][CHAT_ID]
        self.assertEqual(state["last_rowid"], 1500)
        self.assertNotIn("carried_from_rowid", state)

    def test_existing_cursor_is_never_overwritten(self):
        src = self._source(_Provider("db-a", 1000))
        src.ensure_baseline()
        with src._lock:
            src._chat_state(src.chats[0])["last_rowid"] = 1234
            src._save_state()
        src.ensure_baseline()      # 再跑一次不該動它
        self.assertEqual(self._state()["databases"]["db-a"]["chats"][CHAT_ID]["last_rowid"], 1234)


if __name__ == "__main__":
    unittest.main()
