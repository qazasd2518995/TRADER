#!/usr/bin/env python3
"""檢查官網的 i18n 完整性。

驗三件事：
  1. HTML 裡每個 data-i18n / data-i18n-attr 的 key，兩份語言檔都要有
  2. 語言檔之間的 key 集合要一致（免得切到英文時某段變回中文）
  3. 語言檔沒有孤兒 key（HTML 裡沒人用，通常是改版後忘了刪）

用法：python3 scripts/check-website-i18n.py
離開碼 0 = 通過，1 = 有問題。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "website"
LANGS = ["zh-Hant", "en"]

# data-i18n="key"
RE_TEXT = re.compile(r'data-i18n="([^"]+)"')
# data-i18n-attr="placeholder:key,aria-label:key2"
RE_ATTR = re.compile(r'data-i18n-attr="([^"]+)"')
# <html data-meta-title="pricing.metaTitle" data-meta-desc="...">
RE_META = re.compile(r'data-meta-(?:title|desc)="([^"]+)"')
# TradingView widget 設定裡的 {"$t": "market.metals"} —— 這些 key 由 widgets.js
# 在掛載當下解析，不會出現在 data-i18n 上，但確實有在用
RE_DOLLAR_T = re.compile(r'&quot;\$t&quot;:\s*&quot;([^&]+)&quot;')
# JS 裡的 I18N.t(...) —— 這些 key 不會出現在 HTML 上，但確實有在用。
# 引數可能是三元運算子（t(open ? 'a' : 'b')），所以先抓整個呼叫再取裡面的字串。
RE_JS_CALL = re.compile(r"I18N\.t\(([^)]*)\)")
RE_JS_STR = re.compile(r"['\"]([A-Za-z][\w.]*\.[\w.]+)['\"]")


def keys_in_html() -> dict[str, set[str]]:
    """回傳 {檔名: {key,...}}，保留來源檔名才好指出是哪一頁漏了。"""
    found: dict[str, set[str]] = {}
    for html in sorted(ROOT.rglob("*.html")):
        text = html.read_text(encoding="utf-8")
        keys = set(RE_TEXT.findall(text))
        for group in RE_ATTR.findall(text):
            for pair in group.split(","):
                bits = pair.split(":")
                if len(bits) == 2 and bits[1].strip():
                    keys.add(bits[1].strip())
        keys.update(RE_META.findall(text))
        keys.update(RE_DOLLAR_T.findall(text))
        if keys:
            found[str(html.relative_to(ROOT))] = keys
    return found


def keys_in_js() -> set[str]:
    """JS 動態填的字串（例如按鈕展開/收起的兩種標籤）。"""
    found: set[str] = set()
    for js in sorted((ROOT / "assets" / "js").glob("*.js")):
        text = js.read_text(encoding="utf-8")
        for call in RE_JS_CALL.findall(text):
            found.update(RE_JS_STR.findall(call))
    return found


def main() -> int:
    dicts = {}
    for lang in LANGS:
        path = ROOT / "i18n" / f"{lang}.json"
        if not path.exists():
            print(f"✗ 找不到語言檔 {path}")
            return 1
        dicts[lang] = json.loads(path.read_text(encoding="utf-8"))

    problems = 0

    # 1. 語言檔之間互相對照
    base, *rest = LANGS
    for lang in rest:
        missing = sorted(set(dicts[base]) - set(dicts[lang]))
        extra = sorted(set(dicts[lang]) - set(dicts[base]))
        if missing:
            print(f"✗ {lang}.json 缺 {len(missing)} 個 key：{', '.join(missing[:8])}"
                  + (" …" if len(missing) > 8 else ""))
            problems += 1
        if extra:
            print(f"✗ {lang}.json 多出 {len(extra)} 個 {base}.json 沒有的 key："
                  f"{', '.join(extra[:8])}" + (" …" if len(extra) > 8 else ""))
            problems += 1

    # 2. HTML 用到但語言檔沒有
    html_keys = keys_in_html()
    all_used: set[str] = set()
    for page, keys in html_keys.items():
        all_used |= keys
        for lang in LANGS:
            missing = sorted(keys - set(dicts[lang]))
            if missing:
                print(f"✗ {page} 用到 {lang}.json 沒有的 key："
                      f"{', '.join(missing[:8])}" + (" …" if len(missing) > 8 else ""))
                problems += 1

    # 3. 孤兒 key。meta.* 由 JS 直接讀，不會出現在 data-i18n 裡。
    exempt = {k for k in dicts[base] if k.startswith("meta.")}
    js_keys = keys_in_js()
    all_used |= js_keys
    if js_keys:
        print(f"（JS 裡另外用到 {len(js_keys)} 個 key：{', '.join(sorted(js_keys))}）")
    orphans = sorted(set(dicts[base]) - all_used - exempt)
    if orphans:
        print(f"⚠ {len(orphans)} 個 key 沒有任何 HTML 用到（可能是改版後的殘留）："
              f"{', '.join(orphans[:12])}" + (" …" if len(orphans) > 12 else ""))
        # 孤兒只警告不擋 —— 之後的頁面可能會用到

    pages = len(html_keys)
    print(f"\n掃了 {pages} 個頁面、{len(all_used)} 個使用中的 key、"
          f"{len(dicts[base])} 個已定義的 key")

    if problems:
        print(f"✗ {problems} 項不通過")
        return 1
    print("✓ i18n 通過")
    return 0


if __name__ == "__main__":
    sys.exit(main())
