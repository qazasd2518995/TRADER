"""會員端前端純邏輯函式的自動化測試(用 node 跑,直接抽 webui.py 裡的原始碼)。

webui.py 是 4000+ 行的 HTML/CSS/JS 大字串,這個 session 改了很多次,原本全靠人工
截圖驗證。這裡把「不碰 DOM 的純函式」(分批手數解析 pround2 / parseLots / lotsToText、
方案比較表資料 BENEFIT_ROWS / BENEFIT_COLS)直接從 webui.py 抽出來,在 node 裡跑斷言,
任何人改壞馬上被抓到。抽的是「實際會被打包進去的那份原始碼」,不會漂移。

沒有 node 就整組 skip(CI 環境不一定有)。
"""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "copy_trader" / "central" / "webui.py"
NODE = shutil.which("node")


def _balanced(src: str, start: int, opener: str, closer: str) -> str:
    """從 start 起,抓到第一個 opener 與其對應 closer 之間的完整片段(含巢狀)。"""
    i = src.index(opener, start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == opener:
            depth += 1
        elif src[j] == closer:
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise ValueError("unbalanced")


def _extract_function(src: str, name: str) -> str:
    m = re.search(r"function\s+" + re.escape(name) + r"\s*\(", src)
    if not m:
        raise ValueError(f"function {name} not found in webui.py")
    return _balanced(src, m.start(), "{", "}")


def _extract_const_array(src: str, name: str) -> str:
    m = re.search(r"const\s+" + re.escape(name) + r"\s*=\s*", src)
    if not m:
        raise ValueError(f"const {name} not found in webui.py")
    return f"const {name} = " + _balanced(src, m.end(), "[", "]") + ";"


@unittest.skipUnless(NODE, "node 不在 PATH,略過前端邏輯測試")
class WebuiJsLogicTests(unittest.TestCase):
    def setUp(self):
        self.src = WEBUI.read_text(encoding="utf-8")

    def _run_node(self, snippet: str) -> None:
        harness = snippet + "\nconsole.log('ALL_OK');\n"
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
            f.write(harness)
            path = f.name
        try:
            proc = subprocess.run([NODE, path], capture_output=True, text=True, timeout=30)
        finally:
            Path(path).unlink(missing_ok=True)
        if proc.returncode != 0 or "ALL_OK" not in proc.stdout:
            self.fail(f"node 斷言失敗:\nstdout={proc.stdout}\nstderr={proc.stderr}")

    def test_partial_lot_helpers(self):
        fns = "\n".join(_extract_function(self.src, n) for n in ("pround2", "parseLots", "lotsToText"))
        assertions = r"""
const M = (new Function(FNS + "; return {pround2, parseLots, lotsToText};"))();
const {pround2, parseLots, lotsToText} = M;
function eq(a, b, msg){ const A=JSON.stringify(a), B=JSON.stringify(b); if(A!==B){ console.error("FAIL "+msg+": "+A+" != "+B); process.exit(1); } }
// pround2 要跟後端 Python round(x,2) 一致(關鍵:0.03*0.5 = 0.01 而非 0.02)
eq(pround2(0.03*0.5), 0.01, "pround2 0.03*0.5");
eq(pround2(0.10*0.3), 0.03, "pround2 0.10*0.3");
// parseLots 解析:斜線/逗號/空白都吃,至少兩段、都要正數
eq(parseLots("0.01/0.01/0.01"), [0.01,0.01,0.01], "parseLots slash");
eq(parseLots("0.02, 0.01 , 0.01"), [0.02,0.01,0.01], "parseLots comma+space");
eq(parseLots("abc"), null, "parseLots invalid");
eq(parseLots("0.02"), null, "parseLots single");
eq(parseLots("0.01/-0.01/0.01"), null, "parseLots negative");
// lotsToText:佔比 × 基礎手數 還原成手數字串
eq(lotsToText([1/3,1/3,1/3], 0.03), "0.01/0.01/0.01", "lotsToText uniform");
eq(lotsToText([0.5,0.3,0.2], 0.10), "0.05/0.03/0.02", "lotsToText 50/30/20@0.10");
eq(lotsToText(null, 0.03), "0.01/0.01/0.01", "lotsToText default");
"""
        self._run_node("const FNS = " + json.dumps(fns) + ";\n" + assertions)

    def test_benefit_table_data_integrity(self):
        rows = _extract_const_array(self.src, "BENEFIT_ROWS")
        cols = _extract_const_array(self.src, "BENEFIT_COLS")
        assertions = r"""
function bad(msg){ console.error("FAIL "+msg); process.exit(1); }
if (BENEFIT_COLS.length !== 4) bad("方案欄應為 4 欄, 目前 "+BENEFIT_COLS.length);
if (BENEFIT_ROWS.length !== 13) bad("功能列應為 13 列, 目前 "+BENEFIT_ROWS.length);
for (const r of BENEFIT_ROWS) {
  if (!Array.isArray(r) || r.length !== 6) bad("每列要 name+desc+4格, 有一列是 "+JSON.stringify(r));
  if (typeof r[0] !== "string" || typeof r[1] !== "string") bad("功能名/說明要字串: "+JSON.stringify(r));
  for (let i = 2; i < 6; i++) {
    const c = r[i];
    const okType = c === "y" || c === "n" || typeof c === "string" || (Array.isArray(c) && c.length >= 1);
    if (!okType) bad("格子型別不對: "+JSON.stringify(c)+" in "+r[0]);
  }
}
"""
        self._run_node(cols + "\n" + rows + "\n" + assertions)


    def test_tp_mode_options_follow_source_and_tier(self):
        """止盈處理的選項:中頻不給分批平倉,保本移損要進階版,而且下拉絕不會
        停在一個 disabled 的選項上(pickTpMode 一定回一個選得到的值)。"""
        fns = "\n".join(_extract_function(self.src, n)
                        for n in ("tpOptions", "pickTpMode"))
        assertions = r"""
function bad(msg){ console.error("FAIL "+msg); process.exit(1); }
let ENT = {};
const MID_SOURCE = "中頻";
const TIER_LABELS = { trial: "體驗版", basic: "基礎版", advanced: "進階版", flagship: "旗艦版" };
const M = (new Function("ENT", "MID_SOURCE", "TIER_LABELS",
  FNS + "; return {tpOptions, pickTpMode};"));
function api(ent){ return M(ent, MID_SOURCE, TIER_LABELS); }

const PRO  = { partial_close: true,  breakeven: true  };
const FREE = { partial_close: false, breakeven: false };

// 中頻:任何等級都不給分批平倉
for (const ent of [PRO, FREE]) {
  const vals = api(ent).tpOptions(MID_SOURCE).map((o) => o.v);
  if (vals.indexOf("partial") !== -1) bad("中頻不該有分批平倉: "+vals);
  if (vals.indexOf("single") === -1) bad("中頻要有單一點位: "+vals);
}
// 其他來源:進階版三種都在,而且分批/保本是可選的
const proOpts = api(PRO).tpOptions("高頻");
if (proOpts.length !== 3) bad("非中頻應有三個選項, 目前 "+proOpts.length);
if (!proOpts.every((o) => o.ok)) bad("進階版三個選項都該可選");
// 體驗/基礎版:分批與保本都要是灰的,只剩單一點位可選
const freeOpts = api(FREE).tpOptions("高頻");
if (freeOpts.filter((o) => o.ok).map((o) => o.v).join(",") !== "single")
  bad("沒權益時只該剩單一點位: "+JSON.stringify(freeOpts.map((o)=>[o.v,o.ok])));

// pickTpMode 一定回一個「這個等級選得到」的值
const cases = [
  [FREE, MID_SOURCE, "partial", "single"],   // 中頻的舊 partial 設定 → 單一點位
  [FREE, "高頻",     "partial", "single"],   // 沒權益 → 單一點位
  [FREE, "高頻",     "breakeven", "single"],
  [PRO,  "高頻",     "partial", "partial"],
  [PRO,  MID_SOURCE, "partial", "single"],   // 中頻就算有權益也不給分批
  [PRO,  MID_SOURCE, "breakeven", "breakeven"],
  [PRO,  "高頻",     "",        "partial"],  // 沒存過 → 第一個可用的
];
for (const [ent, src, stored, want] of cases) {
  const got = api(ent).pickTpMode(src, stored);
  if (got !== want) bad("pickTpMode("+src+","+stored+") = "+got+", 應為 "+want);
  const opt = api(ent).tpOptions(src).find((o) => o.v === got);
  if (!opt || !opt.ok) bad("pickTpMode 回了一個選不到的值: "+got);
}
"""
        self._run_node("const FNS = " + json.dumps(fns) + ";\n" + assertions)

    def test_side_feature_list_matches_the_benefit_table(self):
        """側欄「方案功能」與會員權益比較表要對得起來:比較表的每一列在側欄
        都找得到對應項目,而且側欄不會出現比較表沒有的功能。"""
        rows_src = _extract_const_array(self.src, "BENEFIT_ROWS")
        # webui.py 裡有好幾個 `const rows =`,只有側欄那個後面接的是 `[{ label: …`
        m = re.search(r"const rows = (?=\[\s*\n\s*\{ label:)", self.src)
        if not m:
            self.fail("paintSide 裡找不到側欄的 const rows")
        side = "const SIDE = " + _balanced(self.src, m.end(), "[", "]") + ";"
        assertions = r"""
function bad(msg){ console.error("FAIL "+msg); process.exit(1); }
const labels = SIDE.map((r) => r.label);
// 比較表的每一列 → 側欄至少要有一項對得起來(名稱不必逐字相同,但要涵蓋)
const MAP = {
  "使用策略": ["低頻訊號跟單", "中頻訊號跟單", "高頻訊號跟單", "超高頻訊號跟單"],
  "每日跟單限制": ["不限次數跟單"],
  "馬丁設定": ["馬丁策略設定"],
  "手數設定": ["跟單手數上限"],
  "績效報表": ["績效報表"],
  "各頻率勝率": ["各頻率勝率分析"],
  "每日虧損上限": ["每日止盈 / 止損"],
  "手機跟單通知": ["手機跟單通知"],
  "分批止盈 / 保本移損": ["分批止盈設定", "保本移損設定"],
  "本金比例自動調整手數": ["本金比例自動調手數"],
  "非開盤自動暫停計時": ["非開盤自動暫停計時"],
  "自動排程": ["自動排程"],
  "真人分析建議": ["真人分析建議"],
};
const covered = new Set();
for (const row of BENEFIT_ROWS) {
  const want = MAP[row[0]];
  if (!want) bad("比較表多了一列「"+row[0]+"」,側欄沒有對應項目(改了表就要一起改 paintSide)");
  for (const w of want) {
    if (labels.indexOf(w) === -1) bad("側欄少了「"+w+"」(對應比較表的「"+row[0]+"」)");
    covered.add(w);
  }
}
for (const l of labels) {
  if (!covered.has(l)) bad("側欄多了「"+l+"」,比較表沒有這一項");
}
// 每一項都要標出需要的等級
const TIERS = ["trial", "basic", "advanced", "flagship"];
for (const r of SIDE) {
  if (TIERS.indexOf(r.need) === -1) bad("「"+r.label+"」的 need 不是合法等級: "+r.need);
}
"""
        stubs = """
const e = { sources: [], max_lot: null, martingale: false, partial_close: false,
            breakeven: false, mobile_notify: false, time_pause: false,
            schedule_max: 0, schedule_weekdays: false };
const maxLot = e.max_lot, schedMax = 0;
const has = () => false, atLeast = () => false, lots = (x) => String(x);
const LOW_SOURCE = "低", MID_SOURCE = "中", HIGH_SOURCE = "高", ULTRA_SOURCE = "超";
"""
        self._run_node(stubs + side + "\n" + rows_src + "\n" + assertions)


if __name__ == "__main__":
    unittest.main()
