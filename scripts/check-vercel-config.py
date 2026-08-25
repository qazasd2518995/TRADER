#!/usr/bin/env python3
"""用 Vercel 官方 schema 驗證 vercel.json。

為什麼需要這支：Vercel 的 schema 是 additionalProperties: false —— 任何多餘
的鍵都會被拒。我曾經在裡面塞 "//" 當註解（JSON 沒有註解語法的常見土法），
結果部署直接被擋：

    The vercel.json schema validation failed with the following message:
    should NOT have additional property `//`

肉眼看不出這種錯，要嘛推上去才知道，要嘛先在本機驗。

    python3 scripts/check-vercel-config.py

離開碼 0 = 通過，1 = 有錯。沒有網路或缺 jsonschema 時會退回基本檢查。
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "vercel.json"
SCHEMA_URL = "https://openapi.vercel.sh/vercel.json"


def find_comment_keys(node, path="$"):
    """揪出 // 這類假註解鍵。JSON 沒有註解，這種寫法在嚴格 schema 下會被拒。"""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key.strip().startswith("//") or key.strip() == "#":
                found.append(f"{path}.{key}")
            found += find_comment_keys(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found += find_comment_keys(value, f"{path}[{i}]")
    return found


def main() -> int:
    if not CONFIG.exists():
        print(f"✗ 找不到 {CONFIG}")
        return 1

    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"✗ vercel.json 不是合法 JSON：{exc}")
        return 1

    # 這一項不需要網路也不需要套件，永遠會跑
    comments = find_comment_keys(cfg)
    if comments:
        print("✗ 有假註解鍵，Vercel 會拒絕（schema 是 additionalProperties: false）：")
        for c in comments:
            print(f"    {c}")
        print("  說明寫到 website/README.md，不要放在 vercel.json 裡。")
        return 1

    try:
        with urllib.request.urlopen(SCHEMA_URL, timeout=15) as resp:
            schema = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"⚠ 取不到官方 schema（{exc}），只做了基本檢查")
        print("✓ 沒有假註解鍵、JSON 合法")
        return 0

    try:
        from jsonschema import Draft7Validator
    except ImportError:
        allowed = set(schema.get("properties", {})) | {"$schema"}
        extra = [k for k in cfg if k not in allowed]
        if extra:
            print(f"✗ 不被允許的頂層鍵：{', '.join(extra)}")
            return 1
        print("⚠ 沒有 jsonschema 套件，只比對了頂層鍵")
        print("  完整驗證：pip install jsonschema")
        print("✓ 頂層鍵皆合法")
        return 0

    errors = sorted(Draft7Validator(schema).iter_errors(cfg),
                    key=lambda e: list(e.path))
    if errors:
        print(f"✗ {len(errors)} 個 schema 錯誤：")
        for err in errors[:10]:
            where = "/".join(map(str, err.path)) or "(根)"
            print(f"    {where}: {err.message[:140]}")
        return 1

    print("✓ vercel.json 符合 Vercel 官方 schema")
    for key in ("framework", "installCommand", "buildCommand", "outputDirectory"):
        if key in cfg:
            print(f"    {key:18s} {cfg[key]!r}")
    print(f"    headers            {len(cfg.get('headers', []))} 組規則")
    return 0


if __name__ == "__main__":
    sys.exit(main())
