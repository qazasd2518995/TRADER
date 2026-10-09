"""K 線與指標，全部「時間點正確」（point-in-time）。

規則只有一條，但每個指標都要遵守：**在時刻 T 能用的，只有開盤時間 + 週期 <= T
的 K 線。** 形成中的那根含有 T 之後的價格。tests/test_quant.py 會把未來的 K 線
接上去，確認過去的指標值一個都沒變 —— 這是抓前視偏誤最直接的方法。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "market_history"


@dataclass
class Bars:
    t: np.ndarray       # 開盤時間（UTC epoch 秒；Exness 伺服器是 GMT+0）
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    sec: int            # 週期秒數
    sp: np.ndarray = None   # 點差（點；XAUUSDm 一點 = 0.001 美元）。沒有就是 None

    def __len__(self) -> int:
        return len(self.t)

    def closed_index(self, T: float) -> int:
        """時刻 T 當下最後一根「已收完」的 K 線索引；沒有就回 -1。"""
        return int(np.searchsorted(self.t, T - self.sec, side="right")) - 1

    def slice_time(self, start: float, end: float) -> "Bars":
        a = int(np.searchsorted(self.t, start, side="left"))
        b = int(np.searchsorted(self.t, end, side="left"))
        return Bars(self.t[a:b], self.o[a:b], self.h[a:b], self.l[a:b], self.c[a:b], self.sec,
                    None if self.sp is None else self.sp[a:b])


def from_rows(rows: Sequence[dict], sec: int) -> Bars:
    rows = sorted(rows, key=lambda b: b["t"])
    arr = lambda k: np.asarray([float(b[k]) for b in rows], dtype=float)
    sp = arr("sp") if rows and "sp" in rows[0] else None
    return Bars(np.asarray([int(b["t"]) for b in rows], dtype=np.int64),
                arr("o"), arr("h"), arr("l"), arr("c"), sec, sp)


def load(name: str, sec: int) -> Bars:
    return from_rows(json.loads((DATA_DIR / name).read_text(encoding="utf-8")), sec)


def resample(src: Bars, sec: int) -> Bars:
    """把細週期合成粗週期。只收「完整」的組合 —— 缺根的那組（週末、斷線）丟掉，
    否則一根只有一半資料的 K 線會被當成完整的區間。"""
    key = src.t - src.t % sec
    out_t, o, h, l, c = [], [], [], [], []
    need = sec // src.sec
    i, n = 0, len(src)
    while i < n:
        j = i
        while j < n and key[j] == key[i]:
            j += 1
        if j - i == need:
            out_t.append(key[i]); o.append(src.o[i]); h.append(src.h[i:j].max())
            l.append(src.l[i:j].min()); c.append(src.c[j - 1])
        i = j
    return Bars(np.asarray(out_t, dtype=np.int64), np.asarray(o), np.asarray(h),
                np.asarray(l), np.asarray(c), sec)


def atr(b: Bars, n: int = 14) -> np.ndarray:
    """Wilder ATR。第 i 個值只用到第 i 根以前（含）。"""
    tr = np.empty(len(b))
    tr[0] = b.h[0] - b.l[0]
    prev = b.c[:-1]
    tr[1:] = np.maximum(b.h[1:] - b.l[1:], np.maximum(abs(b.h[1:] - prev), abs(b.l[1:] - prev)))
    out = np.full(len(b), np.nan)
    if len(b) <= n:
        return out
    out[n - 1] = tr[:n].mean()
    for i in range(n, len(b)):
        out[i] = out[i - 1] + (tr[i] - out[i - 1]) / n
    return out


def adx(b: Bars, n: int = 14) -> np.ndarray:
    """Wilder ADX —— 趨勢「強度」，不分方向。>25 慣例上視為趨勢盤。"""
    up = np.diff(b.h, prepend=b.h[0])
    dn = -np.diff(b.l, prepend=b.l[0])
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = np.empty(len(b))
    tr[0] = b.h[0] - b.l[0]
    prev = b.c[:-1]
    tr[1:] = np.maximum(b.h[1:] - b.l[1:], np.maximum(abs(b.h[1:] - prev), abs(b.l[1:] - prev)))
    out = np.full(len(b), np.nan)
    if len(b) <= 2 * n:
        return out
    s_tr, s_p, s_n = tr[1:n + 1].sum(), pdm[1:n + 1].sum(), ndm[1:n + 1].sum()
    dx = []
    for i in range(n + 1, len(b)):
        s_tr = s_tr - s_tr / n + tr[i]
        s_p = s_p - s_p / n + pdm[i]
        s_n = s_n - s_n / n + ndm[i]
        pdi = 100 * s_p / s_tr if s_tr else 0.0
        ndi = 100 * s_n / s_tr if s_tr else 0.0
        d = 100 * abs(pdi - ndi) / (pdi + ndi) if (pdi + ndi) else 0.0
        dx.append(d)
        if len(dx) == n:
            out[i] = sum(dx) / n
        elif len(dx) > n:
            out[i] = (out[i - 1] * (n - 1) + d) / n
    return out
