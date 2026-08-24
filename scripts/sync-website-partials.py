#!/usr/bin/env python3
"""把共用內容同步到各頁面。

做兩件事：

1. 導覽與 Footer 從 index.html 同步到其他四頁。
2. HTML 裡的備援文字同步成 zh-Hant.json 的值。

為什麼需要這支：官網是純靜態、沒有建置步驟，五個頁面各自是一份完整的 HTML。
好處是部署只要丟檔案、關掉 JS 也讀得到；代價是導覽與 Footer 有五份拷貝。
Footer 裡還包含十條免責聲明，手動同步遲早會漏。

index.html 是唯一的來源。改完它之後跑這支：

    python3 scripts/sync-website-partials.py           # 同步
    python3 scripts/sync-website-partials.py --check   # 只檢查，不改（給 CI 用）

第 2 件事的理由：頁面上每個 data-i18n 元素都寫了一份繁中原文，
關掉 JS 時看到的就是它。改了語言檔卻忘了改 HTML，就會出現
「開著 JS 顯示 A、關掉 JS 顯示 B」的不一致 —— 這種 bug 平常看不出來。

離開碼 0 = 全部一致，1 = 有差異（--check 模式）或同步失敗。
"""
from __future__ import annotations

import argparse
import html as html_mod
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "website"
SOURCE = ROOT / "index.html"

# (區塊名稱, 起始標記, 結束標記)。結束標記是下一段的開頭，不含在區塊裡。
BLOCKS = [
    ("導覽",
     "<!-- ================================================================ 導覽 -->",
     "<main>"),
    ("Footer",
     "<!-- ============================================================== Footer -->",
     '<script src="/assets/js/site-config.js"></script>'),
]


def extract(text: str, start: str, end: str, where: str) -> str:
    try:
        a = text.index(start)
        b = text.index(end, a)
    except ValueError:
        raise SystemExit(f"✗ {where} 裡找不到標記：{start[:40]}…")
    return text[a:b]



class _Grab(HTMLParser):
    """取出每個 data-i18n 元素的完整內文（含巢狀標籤內的文字）。

    不用 regex ——「非貪婪比對到第一個結束標籤」會在 <b>粗體</b>內文 這種結構上
    只抓到粗體那一段，之前就是這樣把免責聲明截斷的。
    """

    VOID = {"img", "br", "meta", "link", "input", "source", "path",
            "circle", "rect", "stop", "hr", "col", "area", "embed", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[list] = []
        self.found: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        if tag in self.VOID:
            return
        self.stack.append([tag, dict(attrs).get("data-i18n"), []])

    def handle_endtag(self, tag):
        while self.stack:
            frame = self.stack.pop()
            text = "".join(frame[2]).strip()
            if frame[1] and frame[1] not in self.found:
                self.found[frame[1]] = " ".join(text.split())
            if self.stack:
                self.stack[-1][2].append(text)
            if frame[0] == tag:
                break

    def handle_data(self, data):
        if self.stack:
            self.stack[-1][2].append(data)


def check_fallbacks(check_only: bool) -> int:
    """比對（或修正）HTML 備援文字與 zh-Hant.json。"""
    lang_path = ROOT / "i18n" / "zh-Hant.json"
    if not lang_path.exists():
        print(f"✗ 找不到 {lang_path}")
        return 1
    zh = json.loads(lang_path.read_text(encoding="utf-8"))

    drift = 0
    for page in sorted(ROOT.rglob("*.html")):
        text = page.read_text(encoding="utf-8")
        grab = _Grab()
        grab.feed(text)

        stale = {
            k: (have, " ".join(zh[k].split()))
            for k, have in grab.found.items()
            if k in zh and have and " ".join(zh[k].split()) != have
        }
        if not stale:
            continue

        rel = page.relative_to(ROOT)
        if check_only:
            print(f"  ✗ {rel} 有 {len(stale)} 處備援文字與語言檔不符：")
            for k, (have, want) in list(stale.items())[:4]:
                print(f"      {k}: HTML {have[:28]!r} → 應為 {want[:28]!r}")
            drift += 1
            continue

        # 只換「該元素直接的純文字內容」，有巢狀標籤的（例如免責聲明的 <b>）跳過，
        # 那些已經拆成獨立的 key，不該在這裡動。
        fixed = 0
        for key, (_, want) in stale.items():
            pattern = re.compile(
                r'(data-i18n="' + re.escape(key) + r'"[^>]*>)([^<]*)(</)')
            new_text, n = pattern.subn(
                lambda m: m.group(1) + html_mod.escape(want, quote=False) + m.group(3), text)
            if n:
                text = new_text
                fixed += n
        if fixed:
            page.write_text(text, encoding="utf-8")
            print(f"  ↻ {rel} 修正 {fixed} 處備援文字")
            drift += 1

    return drift


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="只比對不寫入，有差異時離開碼 1")
    args = ap.parse_args()

    if not SOURCE.exists():
        print(f"✗ 找不到來源 {SOURCE}")
        return 1

    src = SOURCE.read_text(encoding="utf-8")
    blocks = [(name, extract(src, s, e, "index.html"), s, e) for name, s, e in BLOCKS]

    targets = sorted(p for p in ROOT.glob("*/index.html"))
    if not targets:
        print("沒有其他頁面需要同步")
        return 0

    drifted = 0
    for page in targets:
        text = page.read_text(encoding="utf-8")
        original = text
        changed = []
        for name, want, start, end in blocks:
            have = extract(text, start, end, str(page.relative_to(ROOT)))
            if have != want:
                text = text.replace(have, want, 1)
                changed.append(name)

        rel = page.relative_to(ROOT)
        if not changed:
            print(f"  ✓ {rel}")
            continue

        drifted += 1
        if args.check:
            print(f"  ✗ {rel} 與 index.html 不一致：{', '.join(changed)}")
        else:
            page.write_text(text, encoding="utf-8")
            print(f"  ↻ {rel} 已同步：{', '.join(changed)}")

    print("\n備援文字（關掉 JS 時看到的內容）：")
    fb = check_fallbacks(args.check)
    if not fb:
        print("  ✓ 與語言檔一致")

    if (drifted or fb) and args.check:
        print(f"\n✗ 有不一致。跑一次 "
              f"`python3 scripts/sync-website-partials.py` 修正。")
        return 1
    print(f"\n✓ {len(targets)} 個頁面{'皆一致' if not (drifted or fb) else '已同步'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
