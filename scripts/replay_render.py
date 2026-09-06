#!/usr/bin/env python3
"""把一筆真實報單還原成縮時動畫 —— 每根 K 棒依序出現、掛單、進場、出場。

    python scripts/replay_render.py --list          # 看有哪些可以做
    python scripts/replay_render.py --seq 285       # 做指定那筆

為什麼不錄 MT5 的畫面
  1. MT5 視窗上有帳號、餘額、券商名稱。那是要拿去對外推廣的素材，
     等於把自己的帳戶資訊公開貼出去。
  2. 錄影會被任何一個彈窗、更新、斷線毀掉，而且毀了就是毀了，不能重來。
  3. 我們手上本來就有全部資料：K 線在行情倉庫、掛單／停損／止盈在 Hub 的
     訊號紀錄裡。用資料重畫，想重做幾次都行，畫面也完全可控。

輸出
  動畫 GIF（PIL 直接寫，不需要外部程式）。要 MP4 的話裝了 ffmpeg 再轉。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont          # noqa: E402

HISTORY = ROOT / "data" / "market_history"
OUT_DIR = ROOT / "data" / "replays"

W, H = 1080, 1080                # 正方形，社群平台通用
PAD_L, PAD_R, PAD_T, PAD_B = 24, 150, 132, 92
VISIBLE = 90                     # 畫面上同時看得到幾根 K 棒
LEAD_BARS = 40                   # 發單前先鋪多少根當背景
TAIL_FRAMES = 14                 # 結尾定格幾張
# 影格上限。一筆單可能走好幾小時＝好幾百根 K 棒，全部畫出來既吃記憶體
# （1080x1080 全彩每張 3.5MB）又難看：25 秒的片子有 20 秒在發呆。
# 超過就抽格，但掛單／成交／出場那幾根一定保留。
MAX_FRAMES = 170

BG = (14, 16, 22)
GRID = (30, 34, 44)
TEXT = (232, 236, 244)
DIM = (128, 138, 156)
UP = (28, 190, 140)
DOWN = (238, 84, 96)
GOLD = (226, 178, 68)
TP_C = (44, 200, 150)
SL_C = (236, 78, 92)


def _font(size: int, bold: bool = False):
    """找一個裝得下中文的字型。找不到就退回 PIL 內建（會變豆腐字，但不會崩）。"""
    names = (["msjhbd.ttc", "msjh.ttc"] if bold else ["msjh.ttc", "msjhl.ttc"]) + \
            ["seguisb.ttf", "segoeui.ttf", "arial.ttf"]
    for name in names:
        for base in (Path("C:/Windows/Fonts"), Path("/System/Library/Fonts"),
                     Path("/usr/share/fonts/truetype")):
            p = base / name
            if p.is_file():
                try:
                    return ImageFont.truetype(str(p), size)
                except OSError:
                    pass
    return ImageFont.load_default()


@dataclass
class Order:
    when: float
    direction: str
    entry: float
    stop: float
    targets: List[float]
    source: str
    seq: int = 0

    @property
    def is_buy(self) -> bool:
        return self.direction == "buy"


def load_bars() -> List[dict]:
    path = HISTORY / "XAUUSD247m_M1.json"
    if not path.is_file():
        raise SystemExit(f"找不到行情資料：{path}")
    bars = json.loads(path.read_text(encoding="utf-8"))["bars"]
    bars.sort(key=lambda b: b["t"])
    return bars


def load_orders() -> List[Order]:
    """從 LINE 資料庫重建報單。跟訊號中心同一套解析器，不另外寫一份。"""
    from copy_trader.line_db.keys import default_key_provider
    from copy_trader.line_db.sqlite_provider import SQLiteLineDatabaseProvider
    from copy_trader.signal_parser.strict_parser import parse_strict_signal
    from scripts.backtest_signals import LINE_DB, SOURCES

    out: List[Order] = []
    provider = SQLiteLineDatabaseProvider(LINE_DB, default_key_provider("line-db-research"))
    conn = provider.connect()
    for key, cfg in SOURCES.items():
        for chat in cfg["chats"]:
            rows = conn.execute(
                "SELECT _createdTime,_text FROM _message WHERE _chatId=? "
                "AND COALESCE(_text,'')<>''", (chat,)).fetchall()
            for ts, text in rows:
                res = parse_strict_signal(text or "", cfg["profile"])
                sig = res.signal
                if res.status == "accepted" and sig and sig.entry_price and sig.stop_loss:
                    out.append(Order(when=ts / 1000, direction=sig.direction,
                                     entry=float(sig.entry_price),
                                     stop=float(sig.stop_loss),
                                     targets=[float(x) for x in (sig.take_profit or [])],
                                     source=cfg["label"]))
    provider.close()
    out.sort(key=lambda o: o.when)
    for i, o in enumerate(out):
        o.seq = i
    return out


# ── 一筆單的完整經過 ─────────────────────────────────────────────────
@dataclass
class Playback:
    """把「這筆單後來怎麼了」先算好，畫的時候只負責照著演。"""

    fill_index: Optional[int] = None      # 第幾根成交
    exit_index: Optional[int] = None      # 第幾根出場
    outcome: str = "未成交"
    hit: int = 0                          # 觸及到第幾檔止盈
    profit: float = 0.0                   # 每 1 手美元


def trace(order: Order, bars: Sequence[dict], start: int,
          pending_hours: float = 4.0, max_hold_hours: float = 24.0) -> Playback:
    """逐根推進，跟回測引擎同一套規則（含成交當根不判止盈）。"""
    pb = Playback()
    sign = 1 if order.is_buy else -1
    stop = order.stop
    for i in range(start, len(bars)):
        bar = bars[i]
        if pb.fill_index is None:
            if bar["t"] - order.when > pending_hours * 3600:
                pb.outcome = "逾時撤單"
                pb.exit_index = i
                return pb
            if bar["l"] <= order.entry <= bar["h"]:
                pb.fill_index = i
                continue                      # 成交當根不判出場，順行極值多半在成交前
            continue

        hit_stop = (bar["l"] <= stop) if order.is_buy else (bar["h"] >= stop)
        nxt = order.targets[pb.hit] if pb.hit < len(order.targets) else None
        hit_tp = nxt is not None and ((bar["h"] >= nxt) if order.is_buy else (bar["l"] <= nxt))

        if hit_stop:
            pb.exit_index = i
            pb.outcome = "保本出場" if pb.hit else "止損"
            pb.profit = (stop - order.entry) * sign * 100
            return pb
        if hit_tp:
            pb.hit += 1
            if pb.hit == 1:
                stop = order.entry            # 保本移損
            if pb.hit >= len(order.targets):
                pb.exit_index = i
                pb.outcome = "全部止盈"
                pb.profit = (order.targets[-1] - order.entry) * sign * 100
                return pb
        if bar["t"] - bars[pb.fill_index]["t"] > max_hold_hours * 3600:
            pb.exit_index = i
            pb.outcome = "逾時平倉"
            pb.profit = (bar["c"] - order.entry) * sign * 100
            return pb
    pb.outcome = "資料結束"
    return pb


# ── 畫面 ────────────────────────────────────────────────────────────
F_TITLE = _font(38, True)
F_BIG = _font(30, True)
F_MID = _font(23)
F_SMALL = _font(19)
F_TINY = _font(16)


def _levels(order: Order) -> List[tuple]:
    """要畫在圖上的價位線：(價格, 顏色, 標籤)。"""
    rows = [(order.entry, GOLD, "進場"), (order.stop, SL_C, "止損")]
    for i, tp in enumerate(order.targets, 1):
        rows.append((tp, TP_C, f"止盈{i}"))
    return rows


def render(order: Order, bars: Sequence[dict], upto: int, start: int,
           pb: Playback, frame_i: int) -> Image.Image:
    """畫出「到第 upto 根為止」的畫面。upto 之後的一律不畫 —— 縮時的重點就是
    每一格都只看得到當下已經發生的事。"""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    lo_i = max(0, upto - VISIBLE + 1)
    window = bars[lo_i:upto + 1]
    if not window:
        return img

    prices = [b["h"] for b in window] + [b["l"] for b in window]
    # 掛單之後才把價位線納入縮放範圍，否則一開始就會被拉扁
    if upto >= start:
        prices += [p for p, _, _ in _levels(order)]
    hi, lo = max(prices), min(prices)
    span = max(hi - lo, 0.5)
    hi, lo = hi + span * 0.10, lo - span * 0.10
    span = hi - lo

    cw = (W - PAD_L - PAD_R) / VISIBLE
    def x_of(i): return PAD_L + (i - lo_i) * cw + cw / 2
    def y_of(p): return PAD_T + (hi - p) / span * (H - PAD_T - PAD_B)

    # 價位線：掛單那一格之後才出現。先畫它們，順便記下佔掉哪幾列，
    # 價格刻度就避開那些列 —— 否則標籤和數字會疊在一起看不清楚。
    taken: List[float] = []
    if upto >= start:
        filled = pb.fill_index is not None and upto >= pb.fill_index
        for price, colour, label in _levels(order):
            y = y_of(price)
            if not (PAD_T - 30 < y < H - PAD_B + 30):
                continue
            taken.append(y)
            if label == "進場" and filled:
                d.line([(PAD_L, y), (W - PAD_R, y)], fill=colour, width=3)
            else:
                for seg in range(PAD_L, W - PAD_R, 28):
                    d.line([(seg, y), (min(seg + 14, W - PAD_R), y)], fill=colour, width=2)
            d.rectangle([W - PAD_R + 4, y - 13, W - 6, y + 13], fill=colour)
            d.text((W - PAD_R + 10, y - 11), label, font=F_TINY, fill=(12, 14, 18))

    # 格線與右側價格刻度
    for k in range(6):
        y = PAD_T + k * (H - PAD_T - PAD_B) / 5
        d.line([(PAD_L, y), (W - PAD_R, y)], fill=GRID)
        if all(abs(y - t) > 26 for t in taken):
            d.text((W - PAD_R + 10, y - 11), f"{hi - k * span / 5:,.1f}",
                   font=F_TINY, fill=DIM)

    # K 棒
    for i, bar in enumerate(window, lo_i):
        x = x_of(i)
        colour = UP if bar["c"] >= bar["o"] else DOWN
        d.line([(x, y_of(bar["h"])), (x, y_of(bar["l"]))], fill=colour, width=2)
        top, bot = y_of(max(bar["o"], bar["c"])), y_of(min(bar["o"], bar["c"]))
        d.rectangle([x - cw * 0.34, top, x + cw * 0.34, max(bot, top + 1)], fill=colour)

    # 事件標記
    if pb.fill_index is not None and lo_i <= pb.fill_index <= upto:
        _marker(d, x_of(pb.fill_index), y_of(order.entry), GOLD, "成交")
    if pb.exit_index is not None and lo_i <= pb.exit_index <= upto:
        colour = TP_C if pb.profit > 0 else (SL_C if pb.profit < 0 else DIM)
        _marker(d, x_of(pb.exit_index), y_of(_exit_price(order, bars, pb)),
                colour, pb.outcome)

    _chrome(d, order, bars, upto, start, pb)
    return img


def _exit_price(order: Order, bars: Sequence[dict], pb: Playback) -> float:
    """實際的出場價。畫在那根 K 棒的收盤會誤導 —— 觸及止盈那根往往還會
    再往上衝一段，標記就會飄在止盈線上方，看起來像是賺得比實際多。"""
    if pb.outcome == "全部止盈" and order.targets:
        return order.targets[-1]
    if pb.outcome == "止損":
        return order.stop
    if pb.outcome == "保本出場":
        return order.entry
    return bars[pb.exit_index]["c"] if pb.exit_index is not None else order.entry


def _marker(d: ImageDraw.ImageDraw, x: float, y: float, colour, label: str) -> None:
    d.ellipse([x - 8, y - 8, x + 8, y + 8], outline=colour, width=3)
    w = d.textlength(label, font=F_SMALL)
    bx = min(max(x - w / 2 - 8, PAD_L), W - PAD_R - w - 16)
    d.rectangle([bx, y - 44, bx + w + 16, y - 16], fill=colour)
    d.text((bx + 8, y - 41), label, font=F_SMALL, fill=(12, 14, 18))


def _chrome(d: ImageDraw.ImageDraw, order: Order, bars: Sequence[dict],
            upto: int, start: int, pb: Playback) -> None:
    """標題列與底部狀態列。"""
    d.rectangle([0, 0, W, PAD_T - 14], fill=(19, 22, 30))
    d.text((24, 20), "XAUUSD · 黃金訊號跟單", font=F_TITLE, fill=TEXT)
    side = "買進" if order.is_buy else "賣出"
    side_c = UP if order.is_buy else DOWN
    d.rectangle([24, 74, 24 + 78, 74 + 34], fill=side_c)
    d.text((40, 78), side, font=F_BIG, fill=(12, 14, 18))
    d.text((116, 80), order.source, font=F_MID, fill=DIM)
    stamp = datetime.fromtimestamp(bars[upto]["t"]).strftime("%Y-%m-%d  %H:%M")
    d.text((W - 24 - d.textlength(stamp, font=F_MID), 80), stamp, font=F_MID, fill=DIM)

    d.rectangle([0, H - PAD_B + 8, W, H], fill=(19, 22, 30))
    if upto < start:
        state, colour = "等待訊號", DIM
    elif pb.fill_index is None or upto < pb.fill_index:
        state, colour = "已掛單 · 等待進場", GOLD
    elif pb.exit_index is None or upto < pb.exit_index:
        state = f"持倉中 · 已觸及 {pb.hit} 檔止盈" if pb.hit else "持倉中"
        colour = TP_C if pb.hit else TEXT
    else:
        state = pb.outcome
        colour = TP_C if pb.profit > 0 else (SL_C if pb.profit < 0 else DIM)
    d.text((24, H - PAD_B + 22), state, font=F_BIG, fill=colour)

    # 損益只在成交之後才顯示，而且用當下這根的收盤算 —— 不是最終結果
    if pb.fill_index is not None and upto >= pb.fill_index:
        sign = 1 if order.is_buy else -1
        exited = pb.exit_index is not None and upto >= pb.exit_index
        ref = _exit_price(order, bars, pb) if exited else bars[upto]["c"]
        pnl = (ref - order.entry) * sign * 100
        txt = f"{pnl:+,.0f} USD / 手"
        d.text((W - 24 - d.textlength(txt, font=F_BIG), H - PAD_B + 22), txt,
               font=F_BIG, fill=TP_C if pnl >= 0 else SL_C)


def build(order: Order, bars: Sequence[dict], out: Path, ms: int = 90,
          keep_frames: bool = False) -> Path:
    """產生動畫。回傳輸出路徑。

    影格是「逐張畫、逐張寫檔」而不是先全部堆在記憶體裡 —— 一筆單可能上百張
    1080x1080，全留著就是好幾百 MB，記憶體一緊就整批白做。寫成 PNG 之後
    再組裝，中途失敗也還留著素材可以重來。
    """
    start = next((i for i, b in enumerate(bars) if b["t"] >= order.when), None)
    if start is None:
        raise SystemExit("行情資料沒有涵蓋這筆單的時間")
    pb = trace(order, bars, start)
    last = pb.exit_index if pb.exit_index is not None else min(start + 240, len(bars) - 1)
    first = max(0, start - LEAD_BARS)

    frame_dir = out.with_suffix("") / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    for old in frame_dir.glob("*.png"):
        old.unlink()

    indices = _pick_frames(first, last, start, pb)
    paths: List[Path] = []
    for n, i in enumerate(indices):
        frame = render(order, bars, i, start, pb, n)
        path = frame_dir / f"{n:04d}.png"
        frame.save(path)
        frame.close()
        paths.append(path)
    # 結尾定格，讓人看清楚結果
    paths += [paths[-1]] * TAIL_FRAMES

    made = assemble_gif(paths, out, ms)
    if not keep_frames and made:
        for path in set(paths):
            path.unlink(missing_ok=True)
        frame_dir.rmdir()
    return out if made else frame_dir


def assemble_gif(paths: Sequence[Path], out: Path, ms: int) -> bool:
    """把 PNG 組成 GIF。組不起來就回 False，PNG 仍在，可以改用 ffmpeg 轉 MP4。

    GIF 只是「不用裝東西就能看」的方案。真要拿去投放，MP4 檔案更小、畫質
    更好、各平台都能內嵌播放：
        ffmpeg -framerate 11 -i frames/%04d.png -c:v libx264 -pix_fmt yuv420p out.mp4
    """
    try:
        frames = [Image.open(p).convert("RGB") for p in paths]
        frames[0].save(out, save_all=True, append_images=frames[1:],
                       duration=ms, loop=0)
        for f in frames:
            f.close()
        return True
    except (MemoryError, ValueError, OSError) as exc:
        print(f"  GIF 組裝失敗（{type(exc).__name__}: {exc}）—— PNG 影格已保留，"
              f"可用 ffmpeg 轉 MP4")
        return False


def _pick_frames(first: int, last: int, start: int, pb: Playback) -> List[int]:
    """挑要畫哪幾根。太長就抽格，但關鍵時刻不能被抽掉 —— 觀眾要看的就是
    掛單、成交、出場這三個瞬間。"""
    total = last - first + 1
    if total <= MAX_FRAMES:
        return list(range(first, last + 1))
    stride = total / MAX_FRAMES
    picked = {first + int(k * stride) for k in range(MAX_FRAMES)}
    picked.update(i for i in (start, pb.fill_index, pb.exit_index, last)
                  if i is not None and first <= i <= last)
    # 關鍵那幾根前後各補一格，不然會有「上一格還沒掛單、下一格已經出場」
    for key in (start, pb.fill_index, pb.exit_index):
        if key is not None:
            picked.update(i for i in (key - 1, key + 1) if first <= i <= last)
    return sorted(picked)


# ── 每日批次 ─────────────────────────────────────────────────────────
def render_day(day: str, bars: Sequence[dict], orders: Sequence[Order],
               ms: int, keep: bool) -> int:
    """把某一天的每一筆單各做成一支短片。

    刻意「當天有幾筆就做幾筆」，不挑賺錢的做。挑過的素材拿去推廣就是在
    誤導 —— 而且只要有人回頭對帳就會穿幫，那比沒有素材傷得更重。
    """
    todays = [o for o in orders
              if datetime.fromtimestamp(o.when).strftime("%Y-%m-%d") == day]
    if not todays:
        print(f"{day}：沒有報單")
        return 0

    out_dir = OUT_DIR / day
    made, wins, losses, total = 0, 0, 0, 0.0
    print(f"{day}：{len(todays)} 筆")
    for order in todays:
        start = next((i for i, b in enumerate(bars) if b["t"] >= order.when), None)
        if start is None:
            print(f"  {datetime.fromtimestamp(order.when):%H:%M} 行情沒涵蓋，略過")
            continue
        pb = trace(order, bars, start)
        if pb.outcome == "資料結束":
            print(f"  {datetime.fromtimestamp(order.when):%H:%M} 還沒走完，略過")
            continue
        name = (f"{datetime.fromtimestamp(order.when):%H%M}_"
                f"{order.direction}_{order.seq}.gif")
        try:
            build(order, bars, out_dir / name, ms, keep)
        except SystemExit as exc:
            print(f"  {datetime.fromtimestamp(order.when):%H:%M} 失敗：{exc}")
            continue
        made += 1
        total += pb.profit
        wins += pb.profit > 0
        losses += pb.profit < 0
        print(f"  {datetime.fromtimestamp(order.when):%H:%M} "
              f"{'買' if order.is_buy else '賣'} {order.entry:,.1f} → "
              f"{pb.outcome} {pb.profit:+,.0f}   {name}")

    if made:
        print(f"  ── 當日 {made} 支：{wins} 賺 / {losses} 賠 / "
              f"{made - wins - losses} 平，合計 {total:+,.0f} USD/手")
        print(f"  輸出：{out_dir}")
    return made


# ── 給 MT5 策略測試器用的報單清單 ────────────────────────────────────
def write_mt5_job(terminal: Path, orders: Sequence[Order], day: str = "") -> Path:
    """把報單寫成 ReplayTester 讀得懂的 CSV。

    時間一定要換算成該終端的伺服器時間 —— 測試器的 StringToTime 是用
    伺服器時區解讀的，而訊號時間是真實 epoch。這台機器上 Exness 三台快
    3 小時、另一家是 0，不換算整批就會錯開好幾小時（見 bar_store 的
    detect_server_offset）。
    """
    from copy_trader.central.bar_store import detect_server_offset

    files = Path(terminal) / "MQL5" / "Files"
    if not files.is_dir():
        raise SystemExit(f"找不到 {files}（終端路徑對嗎？）")
    offset = detect_server_offset(files)
    if offset is None:
        raise SystemExit(f"量不出 {terminal} 的伺服器時間偏移 —— "
                         f"那台要開著、而且 EA 有在寫 rates_M1.json")

    rows = [o for o in orders
            if not day or datetime.fromtimestamp(o.when).strftime("%Y-%m-%d") == day]
    if not rows:
        raise SystemExit(f"{day or '全部'} 沒有可用的報單")

    # 一定要寫進共用資料夾：測試器的 agent 有自己的沙箱，讀不到終端的
    # MQL5/Files（實際踩過 —— EA 在 OnInit 就因為找不到清單而中止）。
    out = _common_files() / "replay_job.csv"
    # 這個檔是給 EA 讀的（FILE_ANSI），表頭一律 ASCII —— 中文進去會亂碼
    lines = [f"# server time, offset {offset / 3600:+.0f}h | when,direction,"
             f"entry,stop,target,fill,exit,exit_price,outcome"]
    # 順便把「後來怎麼了」一起給 EA。成交點與出場點是我們用回測引擎算的，
    # EA 在真實圖表上沒有訂單，自己算不出來。
    bars = load_bars()
    for o in rows:
        # 只取最後一檔止盈：MT5 一張單只有一個 TP，分批平倉要另外做，
        # 而畫面上要的就是「這單的目標在哪」。
        target = o.targets[-1] if o.targets else o.entry
        start = next((i for i, b in enumerate(bars) if b["t"] >= o.when), None)
        pb = trace(o, bars, start) if start is not None else Playback()
        fill = (_server_stamp(bars[pb.fill_index]["t"], offset)
                if pb.fill_index is not None else "-")
        exit_at = (_server_stamp(bars[pb.exit_index]["t"], offset)
                   if pb.exit_index is not None else "-")
        # 沒成交過就沒有出場價 —— 逾時撤單那幾筆若填當下市價，畫面上會多
        # 一條跟這張單無關的線，看的人會以為那是成交價。
        exit_px = (_exit_price(o, bars, pb)
                   if (pb.fill_index is not None and pb.exit_index is not None)
                   else 0.0)
        lines.append(f"{_server_stamp(o.when, offset)},{o.direction},"
                     f"{o.entry:.2f},{o.stop:.2f},{target:.2f},"
                     f"{fill},{exit_at},{exit_px:.2f},{_ascii_outcome(pb.outcome)}")
    out.write_text(chr(10).join(lines) + chr(10), encoding="ascii")

    first, last = rows[0].when, rows[-1].when
    print(f"寫出 {out}")
    print(f"  {len(rows)} 筆 · 伺服器時間偏移 {offset / 3600:+.0f} 小時")
    print(f"  測試器日期範圍請設："
          f"{_server_stamp(first, offset)[:10]} ~ "
          f"{_server_stamp(last + 30 * 3600, offset)[:10]}")

    # 自我驗證：拿第一筆去對真實 K 線。時間換算錯了整批就會錯開好幾小時，
    # 而且錯了不會有任何徵兆 —— 測試器照樣跑完，只是掛在錯的位置。
    probe = _verify_against_bars(rows[0], offset)
    if probe:
        print(f"  對照 K 線：{probe}")
    return out


# EA 用 FILE_ANSI 讀檔，中文會亂碼，所以結果代碼走 ASCII，由 EA 那邊翻譯。
_OUTCOME_CODE = {"全部止盈": "TP", "止損": "SL", "保本出場": "BE",
                 "逾時撤單": "EXPIRED", "逾時平倉": "TIMEOUT",
                 "未成交": "NOFILL", "資料結束": "OPEN"}


def _ascii_outcome(outcome: str) -> str:
    return _OUTCOME_CODE.get(outcome, "OPEN")


def _common_files() -> Path:
    """MT5 的共用資料夾。終端與測試器 agent 都看得到這裡。"""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise SystemExit("讀不到 APPDATA，找不到 MT5 共用資料夾")
    path = Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _server_stamp(epoch: float, offset: float) -> str:
    """真實 epoch -> 伺服器時間字串。

    MQL5 的 datetime 是「秒數，但用 UTC 的方式解讀」。所以要先把真實 epoch
    加上伺服器偏移，再以 UTC 格式化 —— 用本機時區格式化會再多套一次時差。
    """
    return datetime.fromtimestamp(epoch + offset, timezone.utc).strftime(
        "%Y.%m.%d %H:%M:%S")


def _verify_against_bars(order: Order, offset: float) -> str:
    """把換算後的時間拿去對 M1 歷史，看掛單價是否落在那根附近。"""
    try:
        bars = load_bars()
    except SystemExit:
        return ""
    hit = [b for b in bars if b["t"] <= order.when]
    if not hit:
        return ""
    bar = hit[-1]
    gap = abs(order.entry - bar["c"])
    verdict = "合理" if gap < 30 else "⚠ 差太多，時間可能對不上"
    return (f"{_server_stamp(order.when, offset)} 那根收盤 {bar['c']:,.1f}，"
            f"掛單 {order.entry:,.1f}，差 {gap:.1f} 美元 —— {verdict}")


# ── CLI ─────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="列出行情涵蓋得到的報單")
    ap.add_argument("--seq", type=int, help="要輸出哪一筆")
    ap.add_argument("--ms", type=int, default=90, help="每格毫秒（越小越快）")
    ap.add_argument("--keep-frames", action="store_true",
                    dest="keep_frames", help="保留 PNG 影格（要轉 MP4 時用）")
    ap.add_argument("--date", help="做這一天的全部報單，格式 YYYY-MM-DD")
    ap.add_argument("--today", action="store_true", help="做今天的（給排程用）")
    ap.add_argument("--mt5-job", dest="mt5_job", metavar="終端路徑",
                    help=r"改成產生 MT5 策略測試器用的報單清單，例如 D:\MT5-5")
    args = ap.parse_args()

    bars = load_bars()
    lo, hi = bars[0]["t"], bars[-1]["t"]
    orders = [o for o in load_orders() if lo <= o.when <= hi]

    if args.mt5_job:
        write_mt5_job(Path(args.mt5_job), orders,
                      args.date or ("" if not args.today
                                    else datetime.now().strftime("%Y-%m-%d")))
        return

    if args.today or args.date:
        day = args.date or datetime.now().strftime("%Y-%m-%d")
        render_day(day, bars, orders, args.ms, args.keep_frames)
        return

    if args.list or args.seq is None:
        print(f"行情涵蓋 {datetime.fromtimestamp(lo):%Y-%m-%d} ~ "
              f"{datetime.fromtimestamp(hi):%Y-%m-%d}，可還原 {len(orders)} 筆")
        print(f"{'seq':>4}  {'時間':<17}{'來源':<16}{'方向':<5}{'進場':>9}  結果")
        for o in orders:
            start = next(i for i, b in enumerate(bars) if b["t"] >= o.when)
            pb = trace(o, bars, start)
            mark = "＋" if pb.profit > 0 else ("－" if pb.profit < 0 else "・")
            print(f"{o.seq:>4}  {datetime.fromtimestamp(o.when):%m-%d %H:%M:%S}   "
                  f"{o.source:<16}{'買' if o.is_buy else '賣':<5}{o.entry:>9,.1f}  "
                  f"{mark} {pb.outcome} {pb.profit:+,.0f}")
        return

    order = next((o for o in orders if o.seq == args.seq), None)
    if order is None:
        raise SystemExit(f"找不到 seq={args.seq}（用 --list 看有哪些）")
    out = OUT_DIR / (f"{datetime.fromtimestamp(order.when):%Y%m%d_%H%M}_"
                     f"{order.direction}_{args.seq}.gif")
    path = build(order, bars, out, args.ms, args.keep_frames)
    if path.is_dir():
        print(f"完成：{path}（{len(list(path.glob('*.png')))} 張 PNG 影格）")
    else:
        print(f"完成：{path}  ({path.stat().st_size / 1024:,.0f} KB)")


if __name__ == "__main__":
    main()
