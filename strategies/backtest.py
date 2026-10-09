"""精簡回測引擎:等權重、定期換股、隔日開盤成交、可選停損。

規則:
- 在換股日收盤後算訊號,下一個交易日以「還原開盤價」買進 / 賣出
- 換股日之間買進持有(不再平衡權重);沒選到股票就空手
- 成本:買進收手續費,賣出收手續費 + 交易稅
- 停損:持股收盤價跌破進場價 ×(1 − 停損%)當天收盤出場,換到下一個換股日才再進場
"""
import math

import numpy as np
import pandas as pd


def hold_until(entry, exit_):
    """entry 為 True 時進場,之後 exit 為 True 時出場(與 FinLab hold_until 相同語意)。"""
    e = entry.fillna(False).to_numpy(bool)
    x = exit_.reindex(index=entry.index, columns=entry.columns).fillna(False).to_numpy(bool)
    out = np.zeros_like(e)
    state = np.zeros(e.shape[1], bool)
    for t in range(len(e)):
        state = np.where(state, ~x[t], e[t] & ~x[t])
        out[t] = state
    return pd.DataFrame(out, entry.index, entry.columns)


def hold_until_stop(entry, exit_, price, stop):
    """同 hold_until,另外收盤跌破進場價 ×(1 − stop)也出場(進場價 = 進場當天收盤)。"""
    e = entry.fillna(False).to_numpy(bool)
    x = exit_.reindex(index=entry.index, columns=entry.columns).fillna(False).to_numpy(bool)
    p = price.reindex(index=entry.index, columns=entry.columns).to_numpy(float)
    out = np.zeros_like(e)
    state = np.zeros(e.shape[1], bool)
    cost = np.full(e.shape[1], np.nan)
    for t in range(len(e)):
        stopped = state & (p[t] <= cost * (1 - stop))
        leave = state & (x[t] | stopped)
        enter = ~state & e[t] & ~x[t]
        state = (state & ~leave) | enter
        cost = np.where(enter, p[t], cost)
        out[t] = state
    return pd.DataFrame(out, entry.index, entry.columns)


def rebalance_dates(index, rule):
    """'D' → 每個交易日;'W' / 'M' / 'Q' → 該週 / 月 / 季最後一個交易日;日期序列 → 當天或之後第一個交易日。"""
    s = index.to_series()
    if isinstance(rule, str) and rule == "D":
        return pd.DatetimeIndex(index)
    if isinstance(rule, str):
        freq = {"W": "W", "M": "M", "Q": "Q"}[rule]
        return pd.DatetimeIndex(s.groupby(index.to_period(freq)).last().values)
    pos = index.searchsorted(pd.DatetimeIndex(rule))
    return pd.DatetimeIndex(sorted({index[p] for p in pos if p < len(index)}))


def run(signal, adj_open, adj_close, rebalance, start="2019-01-01",
        fee=0.001425, tax=0.003, stop_loss=None, slots=None):
    """slots:固定格數(每檔最多 1/slots 資金,其餘放現金);None = 資金平均分給所有選到的股票。"""
    idx = adj_close.index[adj_close.index >= pd.Timestamp(start)]
    cols = signal.columns.intersection(adj_close.columns)
    sig = signal.reindex(index=idx, columns=cols).fillna(False).to_numpy(bool)
    O = adj_open.reindex(index=idx, columns=cols).to_numpy(float)
    C = adj_close.reindex(index=idx, columns=cols).ffill().to_numpy(float)

    rpos = idx.get_indexer(rebalance_dates(idx, rebalance))
    entries = sorted({p + 1 for p in rpos if 0 <= p and p + 1 < len(idx)})
    sig_at = {p + 1: p for p in rpos}

    equity = np.ones(len(idx))
    held = np.zeros(len(idx))
    V = 1.0
    prev = {}                 # 上一期出場時各股的市值(貨幣)
    open_trade = {}           # 股票 → 累積報酬倍數(連續持有視為同一筆交易)
    trades = []

    for k, e in enumerate(entries):
        nxt = entries[k + 1] if k + 1 < len(entries) else len(idx)
        if k == 0:
            equity[:e] = 1.0
        p = sig_at[e]
        cand = np.where(sig[p])[0]
        sel = cand[np.isfinite(O[e, cand]) & (O[e, cand] > 0) & np.isfinite(C[e, cand])]

        # 換股成本:賣掉不再持有的、把留下的與新買的調成等權重
        new = set(sel.tolist())
        target = V / max(len(sel), slots or 0) if len(sel) else 0.0
        sells = sum(v for c, v in prev.items() if c not in new)
        buys = 0.0
        for c in new:
            d = target - prev.get(c, 0.0)
            if d > 0:
                buys += d
            else:
                sells -= d
        V -= buys * fee + sells * (fee + tax)
        for c in list(open_trade):
            if c not in new:
                trades.append(open_trade.pop(c) - 1)

        if not len(sel):
            equity[e:nxt] = V
            prev = {}
            continue

        a = V / max(len(sel), slots or 0)                  # 每檔投入金額
        cash = V - a * len(sel)                            # 固定格數時沒用到的資金
        entry = O[e, sel]
        rel = C[e:nxt, sel] / entry                        # 期間每日收盤 ÷ 進場價
        stopped = np.zeros(len(sel), bool)
        if stop_loss:
            hit = np.maximum.accumulate(rel <= 1 - stop_loss, axis=0)
            if hit.any():
                frozen = rel[np.argmax(hit, axis=0), np.arange(len(sel))]
                rel = np.where(hit, frozen * (1 - fee - tax), rel)   # 停損當天收盤賣出(扣稅費)後變現金
                stopped = hit[-1]
        equity[e:nxt] = cash + a * np.nansum(np.where(np.isfinite(rel), rel, 1.0), axis=1)
        held[e:nxt] = len(sel)

        if nxt < len(idx):                                 # 下一期第一天開盤出場
            ex = np.where(stopped, rel[-1], O[nxt, sel] / entry)
            ex = np.where(np.isfinite(ex), ex, rel[-1])
        else:
            ex = rel[-1]
        ex = np.where(np.isfinite(ex), ex, 1.0)
        V = cash + a * float(ex.sum())
        prev = {}
        for c, m, s in zip(sel, ex, stopped):
            open_trade[c] = open_trade.get(c, 1.0) * m
            if s:                                          # 停損那天已賣出:結算交易
                trades.append(open_trade.pop(c) - 1)
            else:
                prev[c] = a * m
    trades += [m - 1 for m in open_trade.values()]

    eq = pd.Series(equity, idx)
    return eq, _stats(eq, trades, held)


def _stats(eq, trades, held):
    r = eq.pct_change().dropna()
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    active = held[held > 0]
    return {
        "cagr": eq.iloc[-1] ** (1 / years) - 1,
        "max_drawdown": float((eq / eq.cummax() - 1).min()),
        "daily_sharpe": float(r.mean() / r.std() * math.sqrt(252)) if r.std() > 0 else None,
        "win_ratio": float(np.mean(np.array(trades) > 0)) if trades else None,
        "avg_n_stock": float(active.mean()) if len(active) else 0.0,
        "trades": len(trades),
    }
