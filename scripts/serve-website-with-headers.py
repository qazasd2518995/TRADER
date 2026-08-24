#!/usr/bin/env python3
"""用 vercel.json 裡的標頭在本機提供官網，驗證 CSP 不會把東西擋掉。

Vercel 上線後才發現 CSP 寫錯、widget 全白，是很難查的那種問題 ——
在本機用同一組標頭跑一次就能提前抓到。

    python3 scripts/serve-website-with-headers.py --port 8282
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "website"
CONFIG = ROOT / "vercel.json"


def load_rules() -> list[tuple[re.Pattern, dict[str, str]]]:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    rules = []
    for entry in cfg.get("headers", []):
        # vercel 的 source 語法是 path-to-regexp，這裡只需要支援我們用到的 (.*)
        pattern = re.compile("^" + entry["source"].replace("(.*)", ".*") + "$")
        headers = {h["key"]: h["value"] for h in entry["headers"] if "key" in h}
        rules.append((pattern, headers))
    return rules


class Handler(SimpleHTTPRequestHandler):
    rules: list = []

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB), **kw)

    def end_headers(self):
        path = self.path.split("?")[0]
        for pattern, headers in self.rules:
            if pattern.match(path):
                for k, v in headers.items():
                    self.send_header(k, v)
        super().end_headers()

    def log_message(self, *a):
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8282)
    args = ap.parse_args()

    if not CONFIG.exists():
        print(f"✗ 找不到 {CONFIG}")
        return 1

    Handler.rules = load_rules()
    print(f"套用 {len(Handler.rules)} 組標頭規則（來自 vercel.json）")
    for pattern, headers in Handler.rules:
        print(f"  {pattern.pattern:16s} → {', '.join(headers)}")
    print(f"\n官網：http://127.0.0.1:{args.port}/")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
