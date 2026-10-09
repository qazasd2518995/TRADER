"""驗證工具 —— 業界用來分辨「真的有效」與「回測好看」的那一套。

* walk_forward    Pardo 的前進最佳化：用過去選參數、套到下一段沒看過的資料，
                  把每段樣本外接起來才是績效估計。選參數用「鄰居中位數」挑平原，
                  不挑尖峰（尖峰是雜訊最喜歡長出來的地方）。
* cscv_pbo        Bailey, Borwein, López de Prado & Zhu 的 PBO：把時間切成 S 塊，
                  每種「一半當樣本內、一半當樣本外」的組合都問一次：樣本內最好的
                  那組，到了樣本外排在中位數以下的機率是多少。
* deflated_sharpe Bailey & López de Prado：試了 N 種組合才挑出最好的那個，Sharpe
                  要扣掉「純運氣也能挑到這麼高」的部分，並考慮偏態與厚尾。
* monte_carlo     重抽交易 5,000 次。規劃部位大小要看回撤的第 95 百分位，不是
                  歷史上剛好發生的那一條路徑。

所有函式都吃已經算好的交易清單（model.run 的輸出），不會自己跑模型 ——
同一份交易清單才能被不同檢驗共用，結論也才對得起來。
"""
from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import NormalDist
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from copy_trader.quant.model import Trade

EULER = 0.5772156649
_N = NormalDist()


# ── 基本績效 ────────────────────────────────────────────────────────────

def summarize(trades: Sequence[Trade], *, days: Optional[float] = None) -> Dict[str, float]:
    if not trades:
        return {"n": 0}
    R = np.asarray([t.R for t in trades])
    usd = np.asarray([t.usd for t in trades])
    eq = np.cumsum(usd)
    dd = float((np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:] - eq).max())
    span = days or max(1.0, (trades[-1].decided - trades[0].decided) / 86400)
    wins, losses = usd[usd > 0].sum(), -usd[usd < 0].sum()
    lo, hi = bootstrap_ci(R)
    return {
        "n": len(trades), "mean_R": float(R.mean()), "median_R": float(np.median(R)),
        "ci_lo": lo, "ci_hi": hi, "win": float((usd > 0).mean()),
        "pf": float(wins / losses) if losses else float("inf"),
        "usd_per_trade": float(usd.mean()), "usd_total": float(usd.sum()), "max_dd_usd": dd,
        "trades_per_month": len(trades) / span * 30.4,
        "sharpe_trade": float(R.mean() / R.std(ddof=1)) if len(R) > 1 else 0.0,
    }


def bootstrap_ci(x: Sequence[float], n: int = 4000, seed: int = 7) -> Tuple[float, float]:
    x = np.asarray(x, dtype=float)
    if len(x) < 3:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    m = rng.choice(x, size=(n, len(x)), replace=True).mean(axis=1)
    return float(np.quantile(m, .025)), float(np.quantile(m, .975))


def daily_R(trades: Sequence[Trade], start: float, end: float) -> np.ndarray:
    """每個交易日（週一到週五）的 R 加總；沒交易的日子是 0。年化 Sharpe 要把那些 0 也算進去。"""
    d0, d1 = int(start // 86400), int(end // 86400)
    days = [d for d in range(d0, d1 + 1) if (d + 3) % 7 < 5]       # 1970-01-01 是星期四
    pos = {d: k for k, d in enumerate(days)}
    out = np.zeros(len(days))
    for t in trades:
        k = pos.get(int(t.exit_t // 86400))
        if k is not None:
            out[k] += t.R
    return out


# ── 部位大小與權益曲線 ─────────────────────────────────────────────────

def equity_curve(trades: Sequence[Trade], risk: float) -> np.ndarray:
    """固定風險比例：每筆拿當下權益的 risk 去扛停損。回傳每筆之後的權益倍數。"""
    eq, out = 1.0, []
    for t in trades:
        eq *= max(1e-9, 1 + risk * t.R)
        out.append(eq)
    return np.asarray(out)


def equity_stats(trades: Sequence[Trade], risk: float, start: float, end: float) -> Dict[str, float]:
    eq = equity_curve(trades, risk)
    if not len(eq):
        return {}
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    years = max((end - start) / (365.25 * 86400), 1e-9)
    d = daily_R(trades, start, end) * risk
    return {"risk": risk, "final": float(eq[-1]), "cagr": float(eq[-1] ** (1 / years) - 1),
            "max_dd": float((1 - eq / peak).max()),
            "sharpe_ann": float(d.mean() / d.std(ddof=1) * math.sqrt(252)) if d.std() > 0 else 0.0}


def kelly_fraction(trades: Sequence[Trade]) -> float:
    """以 R 為單位的成長最適比例（連續近似 μ/σ²）。實務只用 1/4 ~ 1/2。"""
    R = np.asarray([t.R for t in trades])
    return float(R.mean() / R.var(ddof=1)) if len(R) > 2 and R.var() > 0 else 0.0


def monte_carlo(trades: Sequence[Trade], risk: float, n: int = 5000, seed: int = 11) -> Dict[str, float]:
    """有放回重抽同樣筆數的交易。假設交易彼此獨立 —— 實際上會隨市況成群，
    所以這是下限，不是上限；要搭配前進檢驗一起看。"""
    R = np.asarray([t.R for t in trades])
    rng = np.random.default_rng(seed)
    paths = rng.choice(R, size=(n, len(R)), replace=True)
    eq = np.cumprod(np.maximum(1e-9, 1 + risk * paths), axis=1)
    peak = np.maximum.accumulate(np.concatenate([np.ones((n, 1)), eq], axis=1), axis=1)[:, 1:]
    mdd = (1 - eq / peak).max(axis=1)
    fin = eq[:, -1]
    return {"dd_p50": float(np.quantile(mdd, .5)), "dd_p90": float(np.quantile(mdd, .9)),
            "dd_p95": float(np.quantile(mdd, .95)), "dd_p99": float(np.quantile(mdd, .99)),
            "final_p05": float(np.quantile(fin, .05)), "final_p50": float(np.quantile(fin, .5)),
            "p_loss": float((fin < 1).mean())}


# ── 前進最佳化 ──────────────────────────────────────────────────────────

@dataclass
class WFWindow:
    train: Tuple[float, float]
    test: Tuple[float, float]
    chosen: str
    is_mean_R: float
    oos: List[Trade]


def month_starts(start: float, end: float) -> List[float]:
    d = datetime.fromtimestamp(start, timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    out = []
    while d.timestamp() < end:
        out.append(d.timestamp())
        d = d.replace(year=d.year + (d.month // 12), month=d.month % 12 + 1)
    return out


def walk_forward(table: Dict[str, List[Trade]], neighbours: Dict[str, List[str]],
                 start: float, end: float, *, train_months: int = 12, anchored: bool = False,
                 min_trades: int = 30) -> List[WFWindow]:
    """table: 設定鍵 -> 全期交易。每個測試月只用它之前的資料選參數。"""
    ms = month_starts(start, end) + [end]
    first = train_months
    out: List[WFWindow] = []

    def in_(ts, a, b):
        return [t for t in ts if a <= t.decided < b and t.exit_t <= b]   # 訓練窗內「已結算」的

    for k in range(first, len(ms) - 1):
        tr0 = ms[0] if anchored else ms[k - train_months]
        tr1, te0, te1 = ms[k], ms[k], ms[k + 1]
        score: Dict[str, float] = {}
        raw: Dict[str, float] = {}
        for key, ts in table.items():
            sub = in_(ts, tr0, tr1)
            raw[key] = float(np.mean([t.R for t in sub])) if len(sub) >= min_trades else -math.inf
        for key in table:
            vals = [raw[key]] + [raw[n] for n in neighbours.get(key, [])]
            vals = [v for v in vals if v != -math.inf]
            score[key] = float(np.median(vals)) if len(vals) >= 2 and raw[key] != -math.inf else -math.inf
        best = max(score, key=score.get)
        if score[best] == -math.inf:
            continue
        oos = [t for t in table[best] if te0 <= t.decided < te1]
        out.append(WFWindow((tr0, tr1), (te0, te1), best, raw[best], oos))
    return out


# ── PBO（CSCV）──────────────────────────────────────────────────────────

def cscv_pbo(matrix: np.ndarray, S: int = 16) -> Dict[str, float]:
    """matrix: 天 × 設定 的每日 R。回傳 PBO 與 logit 分佈摘要。"""
    T, N = matrix.shape
    blocks = np.array_split(np.arange(T), S)
    bs = np.stack([matrix[b].sum(0) for b in blocks])            # S × N
    bq = np.stack([(matrix[b] ** 2).sum(0) for b in blocks])
    bn = np.asarray([len(b) for b in blocks], dtype=float)[:, None]
    logits = []
    for is_ids in itertools.combinations(range(S), S // 2):
        m = np.zeros(S, dtype=bool); m[list(is_ids)] = True
        def sharpe(mask):
            n = bn[mask].sum(); s = bs[mask].sum(0); q = bq[mask].sum(0)
            mu = s / n; var = np.maximum(q / n - mu ** 2, 1e-18)
            return mu / np.sqrt(var)
        is_sr, oos_sr = sharpe(m), sharpe(~m)
        best = int(np.argmax(is_sr))
        rank = (oos_sr <= oos_sr[best]).sum()                        # 1..N
        r = rank / (N + 1)
        logits.append(math.log(r / (1 - r)))
    lg = np.asarray(logits)
    return {"pbo": float((lg <= 0).mean()), "logit_median": float(np.median(lg)), "splits": len(lg)}


# ── Deflated Sharpe ─────────────────────────────────────────────────────

def deflated_sharpe(returns: np.ndarray, trial_sharpes: Sequence[float]) -> Dict[str, float]:
    """returns：被選中策略的每期報酬（這裡用每日 R）。trial_sharpes：所有試過的組合
    在同樣週期上的 Sharpe（非年化）。回傳 PSR(0) 與 DSR —— 真實 Sharpe > 0 的機率。"""
    r = np.asarray(returns, dtype=float)
    T = len(r)
    sr = r.mean() / r.std(ddof=1)
    z = (r - r.mean()) / r.std(ddof=0)
    skew, kurt = float((z ** 3).mean()), float((z ** 4).mean())
    denom = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4 * sr ** 2))
    N = max(2, len(trial_sharpes))
    v = float(np.var(trial_sharpes, ddof=1))
    sr0 = math.sqrt(v) * ((1 - EULER) * _N.inv_cdf(1 - 1 / N) + EULER * _N.inv_cdf(1 - 1 / (N * math.e)))
    psr = _N.cdf(sr * math.sqrt(T - 1) / denom)
    dsr = _N.cdf((sr - sr0) * math.sqrt(T - 1) / denom)
    return {"sr": float(sr), "sr_ann": float(sr * math.sqrt(252)), "sr0": float(sr0),
            "psr": float(psr), "dsr": float(dsr), "trials": N, "T": T, "skew": skew, "kurt": kurt}
