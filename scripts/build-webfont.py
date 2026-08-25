#!/usr/bin/env python3
"""把昭源黑體子集化成官網實際用得到的那些字。

  python3 scripts/build-webfont.py            # 產出 woff2
  python3 scripts/build-webfont.py --check    # 只檢查有沒有缺字，不重建

為什麼要自己子集化
------------------
昭源黑體的完整字符集有 47,724 個字，可變字型原檔 48 MB。官方有提供 Google
Fonts 那種 unicode-range 分塊版本，瀏覽器只會抓需要的塊 —— 但那份 CSS 本身
就 224 KB，而且 CSS 是阻擋渲染的資源，等於首次載入先付 224 KB 的稅。

這個站的文案是固定的（全部在 i18n JSON 和 HTML 裡），實際用到的中文字大概
一千出頭。直接子集化成那些字，一個檔案就搞定，CSS 只要幾行。

實測（首頁，869 個字元）
------------------------
  現在 Google Fonts   CSS 493 KB（阻擋渲染）+ 18 個分塊共 1230 KB，20+ 次請求
  自架子集            273 KB／字重，1 次請求，CSS 只有幾行

切成固定字重而不是可變字型：可變字型要帶整條字重軸的 delta，同樣的字集
841 KB；切成四個固定字重各 273 KB，而瀏覽器只會抓那一頁真的用到的那幾個。

代價是文案改了要重跑這支腳本，否則新字會變成豆腐格。所以：
  · 產出的檔名帶內容雜湊，改了字型檔就換檔名（快取才能安心設 immutable）
  · --check 會列出「文案裡有、但字型子集裡沒有」的字
  · CSS 的 font-family 後面永遠接系統字型當後備，真的漏了也不會變豆腐
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "website"
OUT_DIR = SITE / "assets" / "fonts"
SRC = ROOT / "vendor" / "ChironHeiHKVF.ttf"

# 這幾個字重要在 CSS 裡用到。可變字型可以生出任意值，但實體 instance 越少檔案
# 越小，所以只切我們真的會用的。
WEIGHTS = [400, 500, 700, 900]

# 內容裡不會出現、但介面隨時可能生出來的字元。少了它們會在某些狀態下缺字：
# 數字與標點是報價、日期、金額會用到的；全形標點是中文排版的基本盤。
ALWAYS = set(
    "0123456789"
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    " !\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
    "，。、；：？！…—～「」『』（）〈〉《》【】"
    "％＄＋－×÷≈≠≤≥°′″"
    "↑↓←→▲▼△▽●○◆◇■□★☆✓✗"
    "　"  # 全形空格，標題排版有用到
)


def collect_chars() -> tuple[set, dict]:
    """掃出網站實際會顯示的每一個字元。

    來源分兩塊：i18n 的語言檔（JS 執行後塞進畫面的文字），以及 HTML 裡的
    後備文字與寫死的內容（meta、og、無 JS 時看到的字）。兩邊都要掃，只掃
    一邊都會漏。
    """
    chars: set = set()
    where: dict = {}

    def add(text: str, src: str) -> None:
        for ch in text:
            if ch not in chars:
                where[ch] = src
            chars.add(ch)

    for f in sorted((SITE / "i18n").glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        for v in data.values():
            if isinstance(v, str):
                add(v, f.name)

    for f in sorted(SITE.rglob("*.html")):
        s = f.read_text(encoding="utf-8")
        s = re.sub(r"<script\b.*?</script>", " ", s, flags=re.S | re.I)
        s = re.sub(r"<style\b.*?</style>", " ", s, flags=re.S | re.I)
        # meta 的 content 是給爬蟲看的，不會渲染成字，但 og 標題會出現在分享卡片，
        # 那是別人的裝置在畫，不需要我們的字型 —— 所以只取會渲染的部分。
        s = re.sub(r"<[^>]+>", " ", s)
        add(s, f.relative_to(SITE).as_posix())

    # 站上的 JS 也有幾句寫死的中文（例如載入失敗的提示）
    for f in sorted((SITE / "assets" / "js").glob("*.js")):
        for m in re.finditer(r'["\'`]([^"\'`]*[一-鿿][^"\'`]*)["\'`]', f.read_text(encoding="utf-8")):
            add(m.group(1), f"js/{f.name}")

    chars |= ALWAYS
    chars = {c for c in chars if c.isprintable() or c == "　"}
    return chars, where


def cjk_only(chars: set) -> set:
    return {c for c in chars if "一" <= c <= "鿿"}


def subset(chars: set) -> list:
    """每個字重切一個檔。回傳 [(weight, Path), ...]。"""
    from fontTools import subset as fts
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer

    if not SRC.is_file():
        sys.exit(f"找不到字型來源：{SRC}\n"
                 f"下載：curl -sL -o {SRC} "
                 f"https://raw.githubusercontent.com/chiron-fonts/chiron-hei-hk/"
                 f"master/VAR_TTF/ChironHeiHKVF.ttf")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    text = "".join(sorted(chars))
    for stale in OUT_DIR.glob("chiron-hei-hk-*.woff2"):
        stale.unlink()

    out = []
    for wght in WEIGHTS:
        font = TTFont(str(SRC))
        # 先實體化再子集化。順序不能反 —— 只釘 PADG 不釘 wght 的話，
        # 後面子集化會在 gvar 撞到殘留的字形參照（KeyError）。
        # 實體化之後 gvar / HVAR 那些 delta 表整個消失，這是體積從 841 KB
        # 掉到 273 KB 的關鍵。
        font = instancer.instantiateVariableFont(
            font, {"wght": wght, "PADG": 0}, inplace=True, updateFontNames=False)

        opts = fts.Options()
        opts.flavor = "woff2"
        opts.desubroutinize = False
        # 只留真的用得到的：kern 字距、palt 中文標點擠壓、ccmp/locl 組字與在地字形。
        # 留 "*" 會把整套 GSUB 的替代字都拉進來，白多 300 KB。
        opts.layout_features = ["kern", "palt", "ccmp", "locl"]
        opts.name_IDs = ["*"]
        opts.notdef_outline = True

        s = fts.Subsetter(options=opts)
        s.populate(text=text)
        s.subset(font)

        tmp = OUT_DIR / f"_tmp-{wght}.woff2"
        font.save(str(tmp))
        font.close()

        digest = hashlib.sha256(tmp.read_bytes()).hexdigest()[:8]
        final = OUT_DIR / f"chiron-hei-hk-{wght}.{digest}.woff2"
        tmp.replace(final)
        out.append((wght, final))
    return out


def check(chars: set, where: dict) -> int:
    """字型子集裡缺了哪些內容用得到的字。"""
    from fontTools.ttLib import TTFont

    found = sorted(OUT_DIR.glob("chiron-hei-hk-*.woff2"))
    if not found:
        print("尚未產出字型檔，先跑一次不加 --check 的版本")
        return 1

    # 每個字重都是同一份字集切出來的，取交集才抓得到「某個字重漏了」
    have = None
    for f in found:
        font = TTFont(str(f), lazy=True)
        cur = {chr(cp) for cp in font.getBestCmap()}
        font.close()
        have = cur if have is None else (have & cur)

    missing = {c for c in chars if c not in have and c.strip()}
    total = sum(f.stat().st_size for f in found)
    print(f"字型檔 {len(found)} 個，合計 {total/1024:.0f} KB")
    for f in found:
        print(f"    {f.name:36} {f.stat().st_size/1024:6.0f} KB")
    print(f"內容用到 {len(chars)} 個字元，其中中文 {len(cjk_only(chars))} 個")

    if missing:
        print(f"\n✗ 缺 {len(missing)} 個字（會落到後備的系統字型）：")
        for c in sorted(missing)[:40]:
            print(f"    {c!r}  來自 {where.get(c, '?')}")
        print("\n  改過文案就要重跑：python3 scripts/build-webfont.py")
        return 1
    print("\n✓ 沒有缺字")
    return 0


def write_css(built: list) -> Path:
    """產生 @font-face。檔名帶雜湊，所以這份 CSS 每次重建都要跟著更新。"""
    lines = [
        "/* 昭源黑體 · 由 scripts/build-webfont.py 產生，不要手改 */",
        "/* 來源 https://github.com/chiron-fonts/chiron-hei-hk （SIL OFL 1.1） */",
        "/* 只含官網用得到的字。改過文案要重跑腳本，否則新字會變豆腐格。 */",
        "",
    ]
    for wght, path in built:
        lines += [
            "@font-face {",
            "  font-family: 'Chiron Hei HK';",
            "  font-style: normal;",
            f"  font-weight: {wght};",
            # swap：字型還沒到就先用系統字型畫，不要留白。中文字型檔大，
            # 這個差別在慢速網路上很明顯。
            "  font-display: swap;",
            f"  src: url('../fonts/{path.name}') format('woff2');",
            "}",
            "",
        ]
    out = SITE / "assets" / "css" / "fonts.css"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="把昭源黑體子集化成官網用得到的字")
    ap.add_argument("--check", action="store_true", help="只檢查缺字，不重建")
    args = ap.parse_args()

    chars, where = collect_chars()
    if args.check:
        return check(chars, where)

    print(f"掃到 {len(chars)} 個字元（中文 {len(cjk_only(chars))} 個）")
    print(f"來源：{SRC.name}（{SRC.stat().st_size/1048576:.0f} MB）")
    print(f"切 {len(WEIGHTS)} 個字重：{WEIGHTS}\n")

    built = subset(chars)
    css = write_css(built)
    print(f"✓ {css.relative_to(ROOT)}\n")
    return check(chars, where)


if __name__ == "__main__":
    sys.exit(main())
