"""會員管理 CLI — 透過 Hub 的 /admin API 開通、續期、停權、查詢。

訊號中心的圖形後台還沒做，這支先讓整套會員制可以實際運作。

    set COPY_TRADER_HUB_URL=https://gold-signal-hub-tw.fly.dev
    set COPY_TRADER_HUB_TOKEN=<管理 token>

    python -m copy_trader.central.member_admin list
    python -m copy_trader.central.member_admin add wang --tier basic --days 30 --note "LINE:wang"
    python -m copy_trader.central.member_admin extend wang --days 30
    python -m copy_trader.central.member_admin tier wang --tier flagship
    python -m copy_trader.central.member_admin suspend wang
    python -m copy_trader.central.member_admin resume wang
    python -m copy_trader.central.member_admin passwd wang
    python -m copy_trader.central.member_admin kick wang
    python -m copy_trader.central.member_admin remove wang
    python -m copy_trader.central.member_admin logins --limit 30
    python -m copy_trader.central.member_admin tiers
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

DEFAULT_HUB = os.environ.get("COPY_TRADER_HUB_URL", "https://gold-signal-hub-tw.fly.dev")


def _call(hub: str, token: str, path: str,
          payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        f"{hub.rstrip('/')}{path}", data=data,
        method="POST" if data is not None else "GET",
        headers={"Content-Type": "application/json; charset=utf-8",
                 "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:                            # noqa: BLE001
            body = {"error": f"http_{e.code}"}
        raise SystemExit(f"✗ 失敗（HTTP {e.code}）：{body.get('error')}")
    except Exception as e:                           # noqa: BLE001
        raise SystemExit(f"✗ 連不上 Hub：{e}")


def _fmt_expiry(m: dict) -> str:
    # 進階版以上是用量制:顯示剩餘「使用額度」(只在開盤+跟單時扣),不是日曆到期日。
    if m.get("time_pause") and m.get("usage_seconds_left") is not None:
        secs = float(m["usage_seconds_left"])
        if secs <= 0:
            return "額度用盡"
        days = secs / 86400
        return f"使用額度 {days:.1f} 天" if days >= 1 else f"使用額度 {secs / 3600:.0f} 小時"
    ts = m.get("expires_at")
    if not ts:
        return "無期限"
    days = (float(ts) - time.time()) / 86400
    stamp = time.strftime("%Y-%m-%d", time.localtime(float(ts)))
    if days < 0:
        return f"{stamp} (已過期 {abs(days):.0f} 天)"
    return f"{stamp} (剩 {days:.0f} 天)"


def _print_members(members) -> None:
    if not members:
        print("（目前沒有任何會員）")
        return
    print(f"{'帳號':<16}{'等級':<10}{'狀態':<8}{'到期':<28}{'線上':<6}{'備註'}")
    print("─" * 96)
    for m in members:
        state = "停權" if m["status"] != "active" else ("過期" if m["expired"] else "正常")
        print(f"{m['username']:<16}{m['tier_label']:<10}{state:<8}"
              f"{_fmt_expiry(m):<28}"
              f"{'●' if m['online'] else '○':<6}{m['note']}")
    print(f"\n共 {len(members)} 位")


def main() -> None:
    p = argparse.ArgumentParser(description="黃金跟單 — 會員管理")
    p.add_argument("--hub", default=DEFAULT_HUB)
    p.add_argument("--token", default=os.environ.get("COPY_TRADER_HUB_TOKEN", ""))
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="列出所有會員")
    sub.add_parser("tiers", help="列出等級與各自的權限")

    a = sub.add_parser("add", help="開通新會員")
    a.add_argument("username")
    a.add_argument("--tier", default="trial")
    a.add_argument("--days", type=int, default=None, help="不給就用該等級的預設天數")
    a.add_argument("--password", default="", help="不給就自動產生")
    a.add_argument("--note", default="")

    e = sub.add_parser("extend", help="續期")
    e.add_argument("username")
    e.add_argument("--days", type=int, default=30)

    t = sub.add_parser("tier", help="改等級")
    t.add_argument("username")
    t.add_argument("--tier", required=True)

    for name, help_text in (("suspend", "停權"), ("resume", "解除停權"),
                            ("kick", "強制登出"), ("remove", "刪除會員"),
                            ("passwd", "重設密碼")):
        s = sub.add_parser(name, help=help_text)
        s.add_argument("username")
        if name == "passwd":
            s.add_argument("--password", default="", help="不給就自動產生")

    lg = sub.add_parser("logins", help="最近登入紀錄")
    lg.add_argument("--limit", type=int, default=50)

    args = p.parse_args()
    if not args.token:
        raise SystemExit("✗ 缺少管理 token：設環境變數 COPY_TRADER_HUB_TOKEN 或用 --token")
    hub, tok = args.hub, args.token

    if args.cmd == "list":
        _print_members(_call(hub, tok, "/admin/members")["members"])

    elif args.cmd == "tiers":
        for t_ in _call(hub, tok, "/admin/tiers")["tiers"]:
            lot = "不限" if t_["max_lot"] is None else f"{t_['max_lot']} 手"
            extras = [x for x, on in (("馬丁", t_["martingale"]),
                                      ("分批平倉", t_["partial_close"])) if on]
            print(f"{t_['key']:<10}{t_['label']:<8}預設 {t_['default_days']:>3} 天  "
                  f"手數上限 {lot:<8}來源 {', '.join(t_['sources'])}"
                  f"{'  + ' + '、'.join(extras) if extras else ''}")

    elif args.cmd == "add":
        body = {"username": args.username, "tier": args.tier,
                "password": args.password, "note": args.note}
        if args.days is not None:
            body["days"] = args.days
        r = _call(hub, tok, "/admin/members", body)["result"]
        print("✓ 已開通\n")
        print(f"  帳號    {r['username']}")
        print(f"  密碼    {r['password']}        ← 只會顯示這一次，請立刻給會員")
        print(f"  等級    {r['tier_label']}")
        print(f"  到期    {_fmt_expiry(r)}")

    elif args.cmd == "extend":
        r = _call(hub, tok, "/admin/members/extend",
                  {"username": args.username, "days": args.days})["result"]
        print(f"✓ {r['username']} 已續期 {args.days} 天 → {_fmt_expiry(r)}")

    elif args.cmd == "tier":
        r = _call(hub, tok, "/admin/members/update",
                  {"username": args.username, "tier": args.tier})["result"]
        print(f"✓ {r['username']} 等級改為 {r['tier_label']}（該會員下一次輪詢就生效）")

    elif args.cmd in ("suspend", "resume"):
        status = "suspended" if args.cmd == "suspend" else "active"
        r = _call(hub, tok, "/admin/members/update",
                  {"username": args.username, "status": status})["result"]
        print(f"✓ {r['username']} 已{'停權' if status == 'suspended' else '恢復'}")

    elif args.cmd == "passwd":
        r = _call(hub, tok, "/admin/members/reset-password",
                  {"username": args.username, "password": args.password})["result"]
        print(f"✓ {r['username']} 新密碼：{r['password']}")
        print("  （舊密碼與所有已登入裝置立即失效）")

    elif args.cmd == "kick":
        r = _call(hub, tok, "/admin/members/kick", {"username": args.username})["result"]
        print(f"✓ 已登出" if r["kicked"] else "（該帳號目前沒有登入中的裝置）")

    elif args.cmd == "remove":
        confirm = input(f"確定刪除 {args.username}？輸入帳號再確認一次：").strip()
        if confirm != args.username:
            raise SystemExit("已取消")
        r = _call(hub, tok, "/admin/members/delete", {"username": args.username})["result"]
        print("✓ 已刪除" if r["deleted"] else "✗ 查無此帳號")

    elif args.cmd == "logins":
        rows = _call(hub, tok, f"/admin/logins?limit={args.limit}")["logins"]
        print(f"{'時間':<20}{'帳號':<16}{'結果':<8}{'裝置':<24}{'IP'}")
        print("─" * 92)
        for r in rows:
            when = time.strftime("%m-%d %H:%M:%S", time.localtime(r["at"]))
            mark = "成功" if r["ok"] else f"失敗({r['detail']})"
            print(f"{when:<20}{r['username']:<16}{mark:<8}{r['device']:<24}{r['ip']}")


if __name__ == "__main__":
    main()
