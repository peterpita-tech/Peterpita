"""FinLab 策略選股儀表板:跑回測、取最新選股,輸出 data.json 給 index.html。

用法:
    pip install -r requirements.txt
    python -m finlab login        # 第一次:瀏覽器登入,之後自動沿用
    python fetch_strategies.py    # → data.json、策略選股.html(資料內嵌,可直接雙擊開)

無瀏覽器環境(GitHub Actions)用 FINLAB_REFRESH_TOKEN / FINLAB_SESSION_ID / FINLAB_API_KEY
(本機執行 `python -m finlab token --env` 取得)。
"""
import json
import math
import traceback
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from finlab import data
from finlab.backtest import sim

HERE = Path(__file__).resolve().parent
FEE, TAX = 0.001425, 0.003


# ---------------------------------------------------------------- strategies
# 每個策略回傳 (position, sim 參數, 選股表指標 (名稱, DataFrame, 格式, 是否由小到大排))
def rsi3():
    """三頻率 RSI。"""
    rsi20 = data.indicator("RSI", timeperiod=20)
    rsi60 = data.indicator("RSI", timeperiod=60)
    rsi120 = data.indicator("RSI", timeperiod=120)
    roe = data.get("fundamental_features:ROE稅後")
    close = data.get("price:收盤價")

    buy = ((rsi120 > 55)                          # 長週期上漲
           & (rsi60 < 75)                         # 中週期別過熱
           & (rsi20.pct_change(3) > 0.02)         # 短週期 RSI 上漲
           & (rsi20 > 75).sustain(3)              # 短週期 RSI 高檔鈍化
           & (roe > 0))                           # ROE 為正
    sell = buy.shift(60) | (close < close.average(60))   # 持有 60 天 或 跌破季線
    position = buy.hold_until(sell)
    return position, dict(resample="W", live_performance_start="2019-01-01"), ("RSI20", rsi20, "{:.1f}", False)


def _vol_universe():
    adj = data.get("etl:adj_close")
    volume = data.get("price:成交股數")
    ret = adj.pct_change()
    universe = (volume.average(20) > 100 * 1000) & adj.notna()   # 平均日成交 > 100 張
    return adj, ret, universe


def low_vol():
    """低波動:60 日波動率最低 30 檔,月換股。"""
    adj, ret, universe = _vol_universe()
    vol_u = ret.rolling(60).std()[universe]
    position = vol_u.rank(axis=1, ascending=True) <= 30
    return position, dict(resample="M", fee_ratio=FEE, tax_ratio=TAX), \
        ("年化波動率", vol_u * math.sqrt(252) * 100, "{:.1f}%", True)


def high_vol():
    """高波動(對照組):60 日波動率最高 30 檔,月換股。"""
    adj, ret, universe = _vol_universe()
    vol_u = ret.rolling(60).std()[universe]
    position = vol_u.rank(axis=1, ascending=False) <= 30
    return position, dict(resample="M", fee_ratio=FEE, tax_ratio=TAX), \
        ("年化波動率", vol_u * math.sqrt(252) * 100, "{:.1f}%", False)


def risk_adjusted():
    """風險調整報酬優化版:6M 報酬/波動最高 + 品質 + 複合動能,前 15,季換股。"""
    adj, ret, universe = _vol_universe()
    roe = data.get("fundamental_features:ROE稅後")
    yoy = data.get("monthly_revenue:去年同月增減(%)")
    ret6m = adj.pct_change(120)
    vol120 = ret.rolling(120).std()
    mom_3m = adj.pct_change(60)
    risk_adj = ret6m / vol120
    pool = universe & (roe > 5) & (yoy > 0) & (mom_3m > 0) & (ret6m > 0)
    position = risk_adj[pool].is_largest(15)
    return position, dict(resample="Q", fee_ratio=FEE, tax_ratio=TAX), ("報酬/波動", risk_adj, "{:.2f}", False)


def peg():
    """本益成長比:營收動能向上中,PEG 最低 10 檔,隨月營收換股。"""
    pe = data.get("price_earning_ratio:本益比")
    rev = data.get("monthly_revenue:當月營收")
    op_growth = data.get("fundamental_features:營業利益成長率")
    peg_ = pe / op_growth
    cond_all = (rev.average(3) / rev.average(12) > 1.1) & (rev / rev.shift() > 0.9)
    result = peg_ * cond_all
    position = result[result > 0].is_smallest(10)
    return position, dict(resample=rev, fee_ratio=1.425 / 1000 * 0.3, stop_loss=0.1,
                          live_performance_start="2021-06-01"), ("PEG", peg_, "{:.2f}", True)


STRATEGIES = [
    dict(id="rsi3", fn=rsi3, name="三頻率 RSI", rebalance="每週檢查,持有 60 天或跌破季線出場",
         rules=["RSI120 > 55(長週期上漲)", "RSI60 < 75(中週期別過熱)", "RSI20 三日漲幅 > 2%",
                "RSI20 > 75 連續 3 天(高檔鈍化)", "ROE 稅後 > 0"]),
    dict(id="lowvol", fn=low_vol, name="低波動 30", rebalance="每月換股",
         rules=["平均日成交 > 100 張", "60 日報酬波動率最低的 30 檔"]),
    dict(id="riskadj", fn=risk_adjusted, name="風險調整報酬 前 15", rebalance="每季換股",
         rules=["平均日成交 > 100 張", "ROE > 5%、營收年增 > 0", "3 個月與 6 個月報酬皆為正",
                "6 個月報酬 ÷ 120 日波動率 最高的 15 檔"]),
    dict(id="peg", fn=peg, name="本益成長比 PEG", rebalance="隨每月營收公布換股,停損 10%",
         rules=["近 3 月營收均值 ÷ 近 12 月均值 > 1.1", "當月營收 ÷ 上月 > 0.9",
                "PEG = 本益比 ÷ 營業利益成長率,取最低 10 檔"]),
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
    """權益曲線降成週資料,縮小檔案。"""
    s = s.dropna()
    if s.empty:
        return []
    s = s.resample(every).last().dropna()
    return [[d.strftime("%Y-%m-%d"), num(v)] for d, v in s.items()]


def company_info():
    try:
        b = data.get("company_basic_info").reset_index()
        key = "symbol" if "symbol" in b.columns else "stock_id"
        b[key] = b[key].astype(str)
        return b.set_index(key)[["公司簡稱", "產業類別"]].to_dict("index")
    except Exception as e:  # noqa: BLE001
        print("[warn] company_basic_info:", e)
        return {}


def latest_picks(position, metric, info, close):
    pos = position.astype(float).fillna(0)
    pos = pos[pos.index <= close.index.max()]
    nz = pos.sum(axis=1)
    when = nz[nz > 0].index[-1] if (nz > 0).any() else pos.index[-1]
    row = pos.loc[when]
    codes = [str(c) for c in row[row > 0].index]
    label, mframe, fmt, ascending = metric
    mrow = mframe.loc[:when].iloc[-1] if len(mframe.loc[:when]) else pd.Series(dtype=float)
    last_close = close.iloc[-1]
    chg = close.pct_change().iloc[-1] * 100
    picks = []
    for c in codes:
        m = num(mrow.get(c))
        picks.append({"code": c, "name": info.get(c, {}).get("公司簡稱", ""),
                      "industry": info.get(c, {}).get("產業類別", ""),
                      "close": num(last_close.get(c), 2), "chg": num(chg.get(c), 2),
                      "metric": None if m is None else fmt.format(m), "metricRaw": m})
    sign = 1 if ascending else -1
    picks.sort(key=lambda p: (p["metricRaw"] is None, sign * (p["metricRaw"] or 0)))
    return when, label, picks


def run_one(spec, info, close):
    position, kw, metric = spec["fn"]()
    report = sim(position, name=spec["name"], upload=False, **kw)
    st = report.get_stats()
    stats = {k: num(st.get(k)) for k in ("cagr", "max_drawdown", "daily_sharpe", "win_ratio", "avg_n_stock")}
    creturn = report.creturn
    bench = getattr(report, "benchmark", None)
    bench_pts = []
    if bench is not None and len(bench):
        b = bench.reindex(creturn.index).ffill().dropna()
        if len(b):
            bench_pts = series_points(b / b.iloc[0])
    when, label, picks = latest_picks(position, metric, info, close)
    return {
        "id": spec["id"], "name": spec["name"], "rebalance": spec["rebalance"], "rules": spec["rules"],
        "stats": stats,
        "start": creturn.index[0].strftime("%Y-%m-%d"), "end": creturn.index[-1].strftime("%Y-%m-%d"),
        "equity": series_points(creturn), "bench": bench_pts,
        "signalDate": pd.Timestamp(when).strftime("%Y-%m-%d"), "metricLabel": label, "picks": picks,
    }


def main():
    close = data.get("price:收盤價")
    info = company_info()
    out = []
    for spec in STRATEGIES:
        print(f"[run] {spec['name']}")
        try:
            out.append(run_one(spec, info, close))
            print(f"[ok]  {spec['name']}: {len(out[-1]['picks'])} 檔,CAGR {out[-1]['stats']['cagr']}")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            out.append({"id": spec["id"], "name": spec["name"], "rebalance": spec["rebalance"],
                        "rules": spec["rules"], "error": f"{type(e).__name__}: {e}"[:300]})
    if all("error" in s for s in out):
        raise SystemExit("所有策略都失敗")
    payload = {
        "generatedAt": int(datetime.now(timezone.utc).timestamp() * 1000),
        "dataDate": close.index.max().strftime("%Y-%m-%d"),
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
