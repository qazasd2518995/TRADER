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


if __name__ == "__main__":
    unittest.main()
