"""策略選股儀表板(免費資料版):跑回測、取最新選股,輸出 data.json / 策略選股.html。

不需要 FinLab 或任何帳號:
- 股價:Yahoo Finance(還原權值)
- 月營收、財報(ROE、營業利益成長率、EPS):公開資訊觀測站
- 回測:backtest.py 自建引擎

用法:
    pip install -r requirements.txt
    python fetch_strategies.py    # 第一次約 20~30 分鐘(抓觀測站歷史),之後有快取會快很多
"""
import json
import math
import traceback
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import backtest as bt
import free_data as fd

HERE = Path(__file__).resolve().parent
FEE, TAX = 0.001425, 0.003
START = "2019-01-01"          # 回測起點(之前的資料當指標暖身)


# ---------------------------------------------------------------- data
class Data:
    def __init__(self):
        uni = fd.get_universe()
        self.names = {u["code"]: u["name"] for u in uni}
        px = fd.prices({u["code"]: u["mkt"] for u in uni}, start="2018-01-01")
        self.bench = px["adj_close"].get("0050")
        cols = [c for c in px["adj_close"].columns if c in self.names]
        self.close = px["close"][cols]
        self.adj = px["adj_close"][cols]
        self.adj_open = px["adj_open"][cols]
        self.volume = px["volume"][cols]
        idx = self.adj.index

        self.rev, self.yoy_m, self.industry = fd.monthly_revenue(2018)
        self.rev = self.rev.reindex(columns=cols)
        self.yoy = fd.daily(self.yoy_m.reindex(columns=cols), idx)
        fin = fd.financials(2018)
        self.roe = fd.daily(fin["roe"].reindex(columns=cols), idx)
        self.op_growth = fd.daily(fin["op_growth"].reindex(columns=cols), idx)
        self.eps_ttm = fd.daily(fin["eps_ttm"].reindex(columns=cols), idx)
        print(f"[data] {len(cols)} 檔,{idx.min():%Y-%m-%d} ~ {idx.max():%Y-%m-%d}")


def rsi(close, n):
    """Wilder RSI(與 TA-Lib 相同的平滑方式)。"""
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn)


def largest(df, n):
    return df.rank(axis=1, ascending=False, method="first") <= n


def smallest(df, n):
    return df.rank(axis=1, ascending=True, method="first") <= n


def universe(D):
    return (D.volume.rolling(20).mean() > 100 * 1000) & D.adj.notna()     # 平均日成交 > 100 張


# ---------------------------------------------------------------- strategies
# 回傳 (position, 換股規則, 回測參數, 選股表指標 (名稱, DataFrame, 格式, 是否由小到大排))
def rsi3(D):
    rsi20, rsi60, rsi120 = rsi(D.adj, 20), rsi(D.adj, 60), rsi(D.adj, 120)
    buy = ((rsi120 > 55)                                   # 長週期上漲
           & (rsi60 < 75)                                  # 中週期別過熱
           & (rsi20.pct_change(3) > 0.02)                  # 短週期 RSI 上漲
           & ((rsi20 > 75).rolling(3).sum() == 3)          # 短週期 RSI 高檔鈍化
           & (D.roe > 0))                                  # ROE 為正
    sell = buy.shift(60).fillna(False).astype(bool) | (D.close < D.close.rolling(60).mean())
    return bt.hold_until(buy, sell), "W", {}, ("RSI20", rsi20, "{:.1f}", False)


def _vol60(D):
    ret = D.adj.pct_change()
    return ret.rolling(60).std().where(universe(D))


def low_vol(D):
    v = _vol60(D)
    return v.rank(axis=1, ascending=True) <= 30, "M", {}, ("年化波動率", v * math.sqrt(252) * 100, "{:.1f}%", True)


def high_vol(D):
    v = _vol60(D)
    return v.rank(axis=1, ascending=False) <= 30, "M", {}, ("年化波動率", v * math.sqrt(252) * 100, "{:.1f}%", False)


def risk_adjusted(D):
    ret = D.adj.pct_change()
    ret6m, mom_3m = D.adj.pct_change(120), D.adj.pct_change(60)
    risk_adj = ret6m / ret.rolling(120).std()
    pool = universe(D) & (D.roe > 5) & (D.yoy > 0) & (mom_3m > 0) & (ret6m > 0)
    return largest(risk_adj.where(pool), 15), "Q", {}, ("報酬/波動", risk_adj, "{:.2f}", False)


def peg(D):
    pe = D.close / D.eps_ttm.where(D.eps_ttm > 0)
    peg_ = pe / D.op_growth
    cond_m = (D.rev.rolling(3).mean() / D.rev.rolling(12).mean() > 1.1) & (D.rev / D.rev.shift() > 0.9)
    cond = fd.daily(cond_m.astype(float), D.adj.index) > 0
    result = peg_.where(cond & (peg_ > 0))
    return smallest(result, 10), D.rev.index, dict(fee=1.425 / 1000 * 0.3, stop_loss=0.1), \
        ("PEG", peg_, "{:.2f}", True)


STRATEGIES = [
    dict(id="rsi3", fn=rsi3, name="三頻率 RSI", rebalance="每週檢查,持有 60 天或跌破季線出場",
         rules=["RSI120 > 55(長週期上漲)", "RSI60 < 75(中週期別過熱)", "RSI20 三日漲幅 > 2%",
                "RSI20 > 75 連續 3 天(高檔鈍化)", "ROE(近四季)> 0"]),
    dict(id="lowvol", fn=low_vol, name="低波動 30", rebalance="每月換股",
         rules=["平均日成交 > 100 張", "60 日報酬波動率最低的 30 檔"]),
    dict(id="riskadj", fn=risk_adjusted, name="風險調整報酬 前 15", rebalance="每季換股",
         rules=["平均日成交 > 100 張", "ROE(近四季)> 5%、營收年增 > 0", "3 個月與 6 個月報酬皆為正",
                "6 個月報酬 ÷ 120 日波動率 最高的 15 檔"]),
    dict(id="peg", fn=peg, name="本益成長比 PEG", rebalance="每月營收公告(10 日)後換股,停損 10%",
         rules=["近 3 月營收均值 ÷ 近 12 月均值 > 1.1", "當月營收 ÷ 上月 > 0.9",
                "PEG = 本益比(收盤 ÷ 近四季 EPS)÷ 單季營業利益年增率,取最低 10 檔"]),
    dict(id="highvol", fn=high_vol, name="高波動 30(對照組)", rebalance="每月換股",
         rules=["平均日成交 > 100 張", "60 日報酬波動率最高的 30 檔", "用來對照低波動異象,非建議策略"]),
]


# ---------------------------------------------------------------- helpers
def num(v, nd=4):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else round(v, nd)


def series_points(s, every="W"):
    s = s.dropna()
    if s.empty:
        return []
    s = s.resample(every).last().dropna()
    return [[d.strftime("%Y-%m-%d"), num(v)] for d, v in s.items()]


def latest_picks(D, position, metric):
    when = position.index[-1]
    row = position.loc[when].fillna(False).astype(bool)
    codes = [str(c) for c in row[row].index]
    label, mframe, fmt, ascending = metric
    mrow = mframe.loc[when]
    chg = D.close.pct_change().iloc[-1] * 100
    picks = []
    for c in codes:
        m = num(mrow.get(c))
        picks.append({"code": c, "name": D.names.get(c, ""), "industry": D.industry.get(c, ""),
                      "close": num(D.close[c].iloc[-1], 2), "chg": num(chg.get(c), 2),
                      "metric": None if m is None else fmt.format(m), "metricRaw": m})
    sign = 1 if ascending else -1
    picks.sort(key=lambda p: (p["metricRaw"] is None, sign * (p["metricRaw"] or 0)))
    return when, label, picks


def run_one(D, spec):
    position, rule, kw, metric = spec["fn"](D)
    position = position.reindex(index=D.adj.index, columns=D.adj.columns).fillna(False).astype(bool)
    eq, stats = bt.run(position, D.adj_open, D.adj, rule, start=START,
                       fee=kw.get("fee", FEE), tax=kw.get("tax", TAX), stop_loss=kw.get("stop_loss"))
    bench = D.bench.reindex(eq.index).ffill().dropna() if D.bench is not None else pd.Series(dtype=float)
    when, label, picks = latest_picks(D, position, metric)
    return {
        "id": spec["id"], "name": spec["name"], "rebalance": spec["rebalance"], "rules": spec["rules"],
        "stats": {k: num(stats.get(k)) for k in ("cagr", "max_drawdown", "daily_sharpe", "win_ratio", "avg_n_stock")},
        "start": eq.index[0].strftime("%Y-%m-%d"), "end": eq.index[-1].strftime("%Y-%m-%d"),
        "equity": series_points(eq), "bench": series_points(bench / bench.iloc[0]) if len(bench) else [],
        "signalDate": pd.Timestamp(when).strftime("%Y-%m-%d"), "metricLabel": label, "picks": picks,
    }


def main():
    D = Data()
    out = []
    for spec in STRATEGIES:
        print(f"[run] {spec['name']}", flush=True)
        try:
            out.append(run_one(D, spec))
            s = out[-1]
            print(f"[ok]  {spec['name']}: 選 {len(s['picks'])} 檔,CAGR {s['stats']['cagr']},MDD {s['stats']['max_drawdown']}")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            out.append({"id": spec["id"], "name": spec["name"], "rebalance": spec["rebalance"],
                        "rules": spec["rules"], "error": f"{type(e).__name__}: {e}"[:300]})
    if all("error" in s for s in out):
        raise SystemExit("所有策略都失敗")
    payload = {
        "generatedAt": int(datetime.now(timezone.utc).timestamp() * 1000),
        "dataDate": D.adj.index.max().strftime("%Y-%m-%d"),
        "strategies": out,
    }
    js = json.dumps(payload, ensure_ascii=False, default=str)
    (HERE / "data.json").write_text(js, encoding="utf-8")
    tpl = (HERE / "index.html").read_text(encoding="utf-8")
    (HERE / "策略選股.html").write_text(
        tpl.replace("<!--DATA-->", f"<script>window.STRAT_DATA = {js};</script>"), encoding="utf-8")
    print(f"[done] 資料日期 {payload['dataDate']} → data.json / 策略選股.html")


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()
