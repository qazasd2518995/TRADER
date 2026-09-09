"""訊號中心的「一鍵新增本機掛機端」。

為什麼只有本機能做：會員端只對 Hub 發出連線、沒有任何進來的路（那正是不必開
通訊埠、不必改防火牆的原因）。所以「遠端幫會員開實例」做不到也不該做 ——
這個功能管的是「訊號中心自己那台養的幾個實例」。

建立順序寫死在程式裡，因為手動做很容易錯，而錯的代價是真的下錯單：

  * **先寫設定檔，再第一次啟動。** auto_start 預設 true，而 mt5_files_dir 空的
    時候會走自動偵測（掃 %APPDATA%\\MetaQuotes\\Terminal\\* 加上
    C:\\Program Files\\MetaTrader 5）—— 也就是可能綁到別人正在用的那一台。
  * **拒絕已被佔用的資料夾。** 兩個掛機端指到同一台 MT5 = 同一個帳戶被下兩次單。
  * **先驗 MT5 活著。** 沒有這一步，就沒有東西可以驗證你綁對了。
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from copy_trader.central import web_launcher as W


class _Base(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self.root = Path(self._dir) / "黃金跟單系統"
        self.root.mkdir()
        self.mt5 = Path(self._dir) / "MT5-6" / "MQL5" / "Files"
        self.mt5.mkdir(parents=True)
        self._write_account(277942668)
        self.spawned = []
        self._patches = [
            mock.patch.object(W.LauncherState, "_instances_root",
                              staticmethod(lambda: self.root)),
            mock.patch.object(W, "_running_instance_numbers", lambda: set()),
            mock.patch.object(W, "_spawn_instance",
                              lambda n: (self.spawned.append(n), (True, ""))[1]),
            mock.patch.object(W, "_watchdog_add", lambda n: True),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self._dir, ignore_errors=True)

    def _write_account(self, login, folder=None, age=0.0):
        folder = folder or self.mt5
        path = folder / "account_info.json"
        path.write_text(json.dumps({"login": login, "server": "Exness-MT5Trial5"}),
                        encoding="utf-8")
        if age:
            old = time.time() - age
            os.utime(path, (old, old))

    def _seed_instance(self, number, mt5_dir, member=""):
        folder = self.root / f"instance_{number}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "client_web_launcher_settings.json").write_text(
            json.dumps({"mt5_files_dir": str(mt5_dir)}), encoding="utf-8")
        if member:
            (folder / "member_session.json").write_text(
                json.dumps({"member": {"username": member}}), encoding="utf-8")

    def create(self, **kw):
        return W.LauncherState.create_local_instance(
            kw.pop("mt5_dir", str(self.mt5)), kw.pop("instance", ""))


class CreateTests(_Base):
    def test_happy_path(self):
        out = self.create()
        self.assertTrue(out["ok"], out.get("error"))
        self.assertEqual(out["mt5_login"], 277942668)
        self.assertEqual(self.spawned, [out["instance"]])

    def test_settings_written_before_launch(self):
        """關鍵：啟動前設定就要在，否則自動偵測會綁到別人的 MT5。"""
        seen = {}

        def _spy(number):
            path = self.root / f"instance_{number}" / "client_web_launcher_settings.json"
            seen["exists"] = path.exists()
            seen["dir"] = json.loads(path.read_text(encoding="utf-8")).get("mt5_files_dir") \
                if path.exists() else None
            return True, ""

        with mock.patch.object(W, "_spawn_instance", _spy):
            out = self.create()
        self.assertTrue(out["ok"], out.get("error"))
        self.assertTrue(seen["exists"], "啟動當下設定檔還不存在 —— 會走自動偵測")
        self.assertEqual(seen["dir"], str(self.mt5))

    def test_auto_numbering_skips_used(self):
        self._seed_instance("1", "X")
        self._seed_instance("2", "Y")
        out = self.create()
        self.assertEqual(out["instance"], "3")

    def test_explicit_number(self):
        self.assertEqual(self.create(instance="7")["instance"], "7")

    def test_rejects_existing_number(self):
        self._seed_instance("7", "X")
        out = self.create(instance="7")
        self.assertFalse(out["ok"])
        self.assertIn("已經存在", out["error"])

    def test_rejects_bad_number(self):
        for bad in ("../evil", "a b", "７", "x" * 20):
            out = self.create(instance=bad)
            self.assertFalse(out["ok"], bad)

    def test_hub_url_inherited_from_central(self):
        """新實例要連同一個 Hub，否則登入了也收不到訊號。"""
        (self.root / "central_web_launcher_settings.json").write_text(
            json.dumps({"hub_url": "https://hub.example"}), encoding="utf-8")
        out = self.create()
        seed = json.loads((self.root / f"instance_{out['instance']}"
                           / "client_web_launcher_settings.json").read_text(encoding="utf-8"))
        self.assertEqual(seed["hub_url"], "https://hub.example")


class RefusalTests(_Base):
    """這些拒絕比功能本身重要 —— 每一條都對應一種會下錯單的情境。"""

    def test_folder_must_exist(self):
        out = self.create(mt5_dir=str(Path(self._dir) / "nope"))
        self.assertFalse(out["ok"])
        self.assertIn("找不到資料夾", out["error"])

    def test_folder_must_have_a_live_mt5(self):
        empty = Path(self._dir) / "MT5-empty" / "MQL5" / "Files"
        empty.mkdir(parents=True)
        out = self.create(mt5_dir=str(empty))
        self.assertFalse(out["ok"])
        self.assertIn("account_info.json", out["error"])

    def test_stale_mt5_is_refused(self):
        """檔案很舊 = MT5 沒在跑或 EA 沒動。綁下去也不會下單。"""
        self._write_account(123, age=3600)
        out = self.create()
        self.assertFalse(out["ok"])
        self.assertIn("沒有寫入資料", out["error"])

    def test_duplicate_folder_is_refused(self):
        """最重要的一條：兩個掛機端指到同一台 MT5 = 重複下單。"""
        self._seed_instance("2", str(self.mt5), member="ops2")
        out = self.create()
        self.assertFalse(out["ok"])
        self.assertIn("重複下單", out["error"])
        self.assertEqual(self.spawned, [], "被拒絕時不該啟動任何東西")

    def test_duplicate_check_ignores_case_and_trailing_slash(self):
        self._seed_instance("2", str(self.mt5).lower() + "\\")
        self.assertFalse(self.create()["ok"])

    def test_blank_path(self):
        self.assertFalse(self.create(mt5_dir="   ")["ok"])

    def test_quoted_path_is_accepted(self):
        """從檔案總管複製路徑常常帶引號，那不該算錯誤。"""
        self.assertTrue(self.create(mt5_dir=f'"{self.mt5}"')["ok"])


class ListTests(_Base):
    def test_lists_configured_instances(self):
        self._seed_instance("1", str(self.mt5), member="ops1")
        rows = W.LauncherState.local_instances()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["member"], "ops1")
        self.assertEqual(rows[0]["mt5_login"], 277942668)

    def test_empty_shells_are_hidden(self):
        """測試或舊版留下的空資料夾會把真正在跑的淹掉。"""
        (self.root / "instance_preview").mkdir()
        (self.root / "instance_smoke").mkdir()
        self._seed_instance("1", str(self.mt5), member="ops1")
        self.assertEqual([r["instance"] for r in W.LauncherState.local_instances()], ["1"])

    def test_running_shell_is_still_listed(self):
        """在跑但還沒設定的實例要看得到 —— 那正是需要處理的狀態。"""
        (self.root / "instance_9").mkdir()
        with mock.patch.object(W, "_running_instance_numbers", lambda: {"9"}):
            rows = W.LauncherState.local_instances()
        self.assertEqual([r["instance"] for r in rows], ["9"])
        self.assertTrue(rows[0]["running"])


class CloneTests(unittest.TestCase):
    """複製一份乾淨的 MT5 目錄。

    清乾淨比複製本身重要 —— 沒清的兩個東西各自對應一種事故：
      Config\\accounts.dat  → 新實例自動登入舊帳號 → 同一帳戶被兩個掛機端下單
      MQL5\\Files\\*        → 建立掛機端時的「MT5 活著嗎」驗證讀到假資料
    """

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self.src = Path(self._dir) / "MT5-5"
        (self.src / "Config").mkdir(parents=True)
        (self.src / "MQL5" / "Files").mkdir(parents=True)
        (self.src / "MQL5" / "Experts").mkdir(parents=True)
        (self.src / "Profiles" / "Charts").mkdir(parents=True)
        (self.src / "bases" / "Exness").mkdir(parents=True)
        (self.src / "logs").mkdir()
        (self.src / "terminal64.exe").write_bytes(b"MZ")
        (self.src / "Config" / "accounts.dat").write_bytes(b"secret")
        (self.src / "Config" / "servers.dat").write_bytes(b"servers")
        (self.src / "MQL5" / "Files" / "account_info.json").write_text('{"login":441064104}')
        (self.src / "MQL5" / "Experts" / "Bridge.ex5").write_bytes(b"ea")
        (self.src / "Profiles" / "Charts" / "chart.chr").write_bytes(b"chart")
        (self.src / "bases" / "Exness" / "big.hcc").write_bytes(b"x" * 4096)
        (self.src / "logs" / "old.log").write_text("old")
        self.dst = Path(self._dir) / "MT5-6"
        W.LauncherState._clone_state = {"phase": "idle"}
        self._patch = mock.patch.object(W, "_mt5_running_at", lambda p: False)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        shutil.rmtree(self._dir, ignore_errors=True)

    def _clone(self, source=None, target=None):
        out = W.LauncherState.start_mt5_clone(str(source or self.src),
                                              str(target or self.dst))
        if out.get("ok"):
            for _ in range(200):                       # 背景執行緒，等它跑完
                if W.LauncherState.clone_status().get("phase") != "running":
                    break
                time.sleep(0.02)
        return out

    def test_copies_what_matters(self):
        self.assertTrue(self._clone()["ok"])
        self.assertEqual(W.LauncherState.clone_status()["phase"], "done")
        self.assertTrue((self.dst / "terminal64.exe").is_file())
        self.assertTrue((self.dst / "MQL5" / "Experts" / "Bridge.ex5").is_file(),
                        "EA 沒帶過去，新那台還要重掛")
        self.assertTrue((self.dst / "Profiles" / "Charts" / "chart.chr").is_file(),
                        "圖表版面沒帶過去，新那台要重開黃金圖表")
        self.assertTrue((self.dst / "Config" / "servers.dat").is_file())

    def test_account_credentials_are_cleared(self):
        """不清的話新實例一開就自動連上舊帳號。"""
        self._clone()
        self.assertFalse((self.dst / "Config" / "accounts.dat").exists())

    def test_bridge_files_are_not_copied(self):
        """舊帳號的 account_info.json 留著，綁定時的驗證就會讀到假資料。"""
        self._clone()
        self.assertFalse((self.dst / "MQL5" / "Files" / "account_info.json").exists())
        self.assertTrue((self.dst / "MQL5" / "Files").is_dir(),
                        "空目錄要留著，EA 第一次寫入才不會失敗")

    def test_bulk_data_is_skipped(self):
        self._clone()
        self.assertFalse((self.dst / "bases").exists(), "行情歷史不用帶，MT5 會重抓")
        self.assertFalse((self.dst / "logs").exists())

    def test_running_source_still_copies_but_warns(self):
        """來源開著時照做但標記。

        最壞的後果是圖表版面沒帶乾淨 —— 新那台要自己重開黃金圖表重掛 EA，
        而那會被第②步的「MT5 活著嗎」檢查擋下來，不會變成下錯單。
        為了它擋住整個流程不划算，但也不能默不作聲。
        """
        with mock.patch.object(W, "_mt5_running_at", lambda p: True):
            out = self._clone()
        self.assertTrue(out["ok"])
        self.assertIn("開著", out["warn"])
        self.assertTrue((self.dst / "terminal64.exe").is_file())

    def test_refuses_bad_source(self):
        out = W.LauncherState.start_mt5_clone(str(Path(self._dir)), str(self.dst))
        self.assertFalse(out["ok"])
        self.assertIn("terminal64.exe", out["error"])

    def test_refuses_existing_target(self):
        self.dst.mkdir()
        out = W.LauncherState.start_mt5_clone(str(self.src), str(self.dst))
        self.assertFalse(out["ok"])
        self.assertIn("已存在", out["error"])

    def test_only_one_clone_at_a_time(self):
        W.LauncherState._clone_state = {"phase": "running"}
        out = W.LauncherState.start_mt5_clone(str(self.src), str(self.dst))
        self.assertFalse(out["ok"])
        self.assertIn("進行中", out["error"])

    def test_reports_the_new_files_dir(self):
        """前端拿它自動填進第②步，省得使用者自己打路徑。"""
        self._clone()
        self.assertEqual(W.LauncherState.clone_status()["files_dir"],
                         str(self.dst / "MQL5" / "Files"))


class AutoPlanTests(_Base):
    """自動決定「從哪複製、複製到哪」。

    每台 MT5 內容差不多，要人去挑是多餘的摩擦。但自動挑要挑對：
    裝在 Program Files 底下那台是一般安裝（設定寫在 %APPDATA%、不在資料夾裡），
    拿它當範本會得到半套，而且目標會落在 Program Files（要管理員權限）。
    """

    def _mt5(self, name):
        root = Path(self._dir) / name
        (root / "MQL5" / "Files").mkdir(parents=True)
        (root / "terminal64.exe").write_bytes(b"MZ")
        return root

    def test_prefers_portable_over_program_files(self):
        prog = Path(self._dir) / "Program Files" / "MetaTrader 5"
        (prog / "MQL5" / "Files").mkdir(parents=True)
        (prog / "terminal64.exe").write_bytes(b"MZ")
        portable = self._mt5("MT5-2")
        self._seed_instance("1", str(prog / "MQL5" / "Files"))
        self._seed_instance("2", str(portable / "MQL5" / "Files"))
        with mock.patch.object(W, "_mt5_running_at", lambda p: False):
            plan = W.LauncherState.suggest_clone_plan()
        self.assertEqual(plan["source"], str(portable))
        self.assertTrue(plan["target"].endswith("MT5-3"), plan["target"])

    def test_prefers_a_closed_one(self):
        a, b = self._mt5("MT5-2"), self._mt5("MT5-3")
        self._seed_instance("2", str(a / "MQL5" / "Files"))
        self._seed_instance("3", str(b / "MQL5" / "Files"))
        with mock.patch.object(W, "_mt5_running_at", lambda p: p == a):
            plan = W.LauncherState.suggest_clone_plan()
        self.assertEqual(plan["source"], str(b), "開著的那台不該被選為範本")
        self.assertFalse(plan["source_running"])

    def test_all_running_still_gives_a_plan(self):
        """全部開著也要給建議 —— 擋住整個流程比帶不乾淨的版面糟。"""
        a = self._mt5("MT5-2")
        self._seed_instance("2", str(a / "MQL5" / "Files"))
        with mock.patch.object(W, "_mt5_running_at", lambda p: True):
            plan = W.LauncherState.suggest_clone_plan()
        self.assertEqual(plan["source"], str(a))
        self.assertTrue(plan["source_running"], "要標記出來，不能默不作聲")

    def test_target_skips_used_numbers(self):
        for n in (2, 3, 4):
            root = self._mt5(f"MT5-{n}")
            self._seed_instance(str(n), str(root / "MQL5" / "Files"))
        with mock.patch.object(W, "_mt5_running_at", lambda p: False):
            plan = W.LauncherState.suggest_clone_plan()
        self.assertTrue(plan["target"].endswith("MT5-5"), plan["target"])

    def test_no_mt5_at_all(self):
        with mock.patch.object(W, "_mt5_running_at", lambda p: False):
            plan = W.LauncherState.suggest_clone_plan()
        self.assertIn("error", plan)

    def test_blank_args_use_the_plan(self):
        """UI 送空字串時要自動填 —— 那正是「省力」的實作點。"""
        a = self._mt5("MT5-2")
        self._seed_instance("2", str(a / "MQL5" / "Files"))
        W.LauncherState._clone_state = {"phase": "idle"}
        with mock.patch.object(W, "_mt5_running_at", lambda p: False):
            out = W.LauncherState.start_mt5_clone("", "")
        self.assertTrue(out["ok"], out.get("error"))
        self.assertEqual(out["source"], str(a))
        for _ in range(200):
            if W.LauncherState.clone_status().get("phase") != "running":
                break
            time.sleep(0.02)


class WatchdogTests(unittest.TestCase):
    """忘了補守護的下場：那個掛機端掛掉沒人拉。實例 5 就這樣裸奔過。"""

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self.script = Path(self._dir) / "黃金跟單守護" / "watchdog.ps1"
        self.script.parent.mkdir(parents=True)
        self.script.write_bytes(
            b"\xef\xbb\xbf" + "# 守護\nforeach ($n in 1, 2, 3) { Ensure-Alive $m $n }\n"
            .encode("utf-8"))
        self._env = mock.patch.dict(os.environ, {"LOCALAPPDATA": self._dir})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self._dir, ignore_errors=True)

    def test_adds_the_number(self):
        self.assertTrue(W._watchdog_add("6"))
        text = self.script.read_bytes().decode("utf-8-sig")
        self.assertIn("foreach ($n in 1, 2, 3, 6)", text)

    def test_keeps_the_bom(self):
        """沒有 BOM 的話 powershell.exe 用系統 ANSI 讀，腳本裡的中文全變亂碼。"""
        W._watchdog_add("6")
        self.assertTrue(self.script.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_idempotent(self):
        W._watchdog_add("2")
        text = self.script.read_bytes().decode("utf-8-sig")
        self.assertIn("foreach ($n in 1, 2, 3)", text, "已存在的號碼不該重複加")

    def test_missing_script_is_not_fatal(self):
        self.script.unlink()
        self.assertFalse(W._watchdog_add("6"))


if __name__ == "__main__":
    unittest.main()
