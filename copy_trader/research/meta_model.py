"""Meta-labeling：在提供者的訊號上面，學「哪些該跟」。

López de Prado（Advances in Financial Machine Learning, 2018, 第 3 章）的兩段式設計：
主模型提出交易（這裡就是提供者的報單：方向、進場、停損、止盈），副模型只回答
「要不要做」。我們改變不了提供者發什麼，但可以決定跟哪幾筆 —— 這正是副模型的
位置。

刻意的選擇（小樣本下每一條都比模型本身重要）：

* **正則化邏輯迴歸，不用樹模型。** 幾百筆樣本、十幾個特徵，梯度提升樹會直接把
  訓練集背起來。線性模型能學的東西少，但學到的比較可能是真的。
* **前進式驗證 + purging。** 預測時間 t 的那筆時，訓練集只放「t 之前已經結算」
  的單。還在持倉的單結果未知，拿來訓練就是偷看未來（文獻稱 leakage）。
* **正則化強度在每個訓練窗內部自己選**（訓練窗最後 25% 當驗證），不是看完測試
  結果再挑。後者是最常見的自欺方式。
* **決策門檻不調**：預測勝率高於訓練集的基準勝率就跟。可調的門檻會變成另一個
  過擬合的旋鈕。
* **未成交也是樣本（R=0）。** 決策發生在掛單當下，那時不知道會不會成交；只拿
  成交的單評估，會高估所有過濾規則的效果。
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

SESSIONS = ("asia", "london", "overlap", "ny")       # "late" 當基準類別
NUMERIC = ("wait_atr", "sl_atr", "rr1", "with_trend", "dist_ema20", "ret_1h",
           "ret_4h", "ret_24h", "range_pos_2h", "range_pos_24h", "prov_last10_R")
LAMBDAS = (0.3, 1.0, 3.0, 10.0, 30.0)


def known_at(r: Dict[str, Any]) -> float:
    """這筆單的結果在什麼時候才確定。訓練集只能放這個時刻之前的。"""
    if r.get("filled") and r.get("min_to_fill") is not None and r.get("min_to_exit") is not None:
        return r["ts"] + (r["min_to_fill"] + r["min_to_exit"]) * 60
    return r["ts"] + 4 * 3600                         # 未成交：掛單到期才知道


def design(rows: Sequence[Dict[str, Any]], sources: Sequence[str]) -> np.ndarray:
    cols = []
    for r in rows:
        v = [float(r.get(k) or 0.0) for k in NUMERIC]
        v += [1.0 if r.get("session") == s else 0.0 for s in SESSIONS]
        v.append(float(r.get("is_buy") or 0))
        v += [1.0 if r.get("source") == s else 0.0 for s in sources[1:]]
        v.append(math.log(max(float(r.get("atr_h1") or 1.0), 1e-6)))
        cols.append(v)
    return np.asarray(cols, dtype=float)


def fit_logistic(X: np.ndarray, y: np.ndarray, lam: float, iters: int = 50) -> np.ndarray:
    """L2 正則化邏輯迴歸，牛頓法（IRLS）。截距不懲罰。"""
    Xb = np.hstack([np.ones((len(X), 1)), X])
    w = np.zeros(Xb.shape[1])
    pen = np.full(Xb.shape[1], lam); pen[0] = 0.0
    for _ in range(iters):
        z = np.clip(Xb @ w, -30, 30)
        p = 1 / (1 + np.exp(-z))
        g = Xb.T @ (p - y) + pen * w
        H = (Xb * (p * (1 - p))[:, None]).T @ Xb + np.diag(pen) + 1e-9 * np.eye(len(w))
        step = np.linalg.solve(H, g)
        w -= step
        if np.max(np.abs(step)) < 1e-8:
            break
    return w


def predict(w: np.ndarray, X: np.ndarray) -> np.ndarray:
    z = np.clip(np.hstack([np.ones((len(X), 1)), X]) @ w, -30, 30)
    return 1 / (1 + np.exp(-z))


def logloss(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def auc(scores: Sequence[float], labels: Sequence[int]) -> float:
    pos = [s for s, l in zip(scores, labels) if l]
    neg = [s for s, l in zip(scores, labels) if not l]
    if not pos or not neg:
        return float("nan")
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


@dataclass
class Prediction:
    row: Dict[str, Any]
    p: float
    base: float
    take: bool


def walk_forward(rows: List[Dict[str, Any]], *, min_train: int = 100, step: int = 20,
                 target=lambda r: 1.0 if r.get("R", 0) > 0 else 0.0) -> List[Prediction]:
    rows = sorted(rows, key=lambda r: r["ts"])
    sources = sorted({r["source"] for r in rows})
    preds: List[Prediction] = []
    i = 0
    # 從第一個「已知結果的單數 >= min_train」的時刻開始往前走
    while i < len(rows):
        t0 = rows[i]["ts"]
        train = [r for r in rows if known_at(r) < t0]           # purging
        if len(train) < min_train:
            i += 1
            continue
        block = rows[i:i + step]

        Xtr = design(train, sources)
        ytr = np.asarray([target(r) for r in train])
        mu, sd = Xtr.mean(0), Xtr.std(0); sd[sd == 0] = 1.0

        # 正則化強度：只在訓練窗內部選（最後 25% 當驗證）
        cut = int(len(train) * 0.75)
        best, best_ll = LAMBDAS[-1], float("inf")
        for lam in LAMBDAS:
            w = fit_logistic((Xtr[:cut] - mu) / sd, ytr[:cut], lam)
            ll = logloss(predict(w, (Xtr[cut:] - mu) / sd), ytr[cut:])
            if ll < best_ll:
                best, best_ll = lam, ll
        w = fit_logistic((Xtr - mu) / sd, ytr, best)

        base = float(ytr.mean())
        p = predict(w, (design(block, sources) - mu) / sd)
        for r, pi in zip(block, p):
            preds.append(Prediction(r, float(pi), base, bool(pi > base)))
        i += step
    return preds


def bootstrap_diff(a: Sequence[float], b: Sequence[float], n: int = 5000,
                   seed: int = 1) -> Tuple[float, float, float]:
    """兩組平均值差的 bootstrap 95% 區間，與「差 <= 0」的單尾機率。"""
    rnd = random.Random(seed)
    diffs = sorted(sum(rnd.choices(a, k=len(a))) / len(a) - sum(rnd.choices(b, k=len(b))) / len(b)
                   for _ in range(n))
    p_le0 = sum(1 for d in diffs if d <= 0) / n
    return diffs[int(.025 * n)], diffs[int(.975 * n)], p_le0
