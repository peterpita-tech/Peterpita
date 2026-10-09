"""美股策略選股儀表板(免費資料):VCP、趨勢模板,以及比照台股的價格類策略。

資料:NASDAQ 代號表(含市值、產業)+ Yahoo Finance 日線(還原權值),不需要任何帳號。
回測:../strategies/backtest.py(與台股共用的引擎)。

用法:
    pip install -r requirements.txt
    python fetch_us.py         # → data.json、策略選股_美股.html
"""
import json
import math
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "strategies"))
sys.path.insert(0, str(HERE.parent / "zsigma-us"))
import backtest as bt  # noqa: E402
from fetch_zsigma_us import get_universe  # noqa: E402

START = "2019-01-01"            # 回測起點
MIN_MCAP = 3e8                  # 只看目前市值 ≥ 3 億美元的公司(控制資料量)
FEE, TAX = 0.0005, 0.0          # 美股:無交易稅;手續費 + 滑價估 0.05%
BENCH = "SPY"


# ---------------------------------------------------------------- data
def prices(codes, start="2018-01-01", chunk=150):
    frames = {}

    def fetch(tickers, size):
        for i in range(0, len(tickers), size):
            part = tickers[i:i + size]
            print(f"[price] {i + len(part)}/{len(tickers)}", flush=True)
            try:
                df = yf.download(part, start=start, auto_adjust=False, group_by="ticker",
                                 threads=True, progress=False)
            except Exception as e:  # noqa: BLE001
                print("  download error:", e, flush=True)
                continue
            for t in part:
                try:
                    sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
                except KeyError:
                    continue
                sub = sub.dropna(subset=["Close"])
                if len(sub):
                    frames[t] = sub

    tickers = list(codes) + [BENCH]
    fetch(tickers, chunk)
    missing = [t for t in tickers if t not in frames]
    if missing:
        print(f"[price] {len(missing)} 檔補抓", flush=True)
        time.sleep(30)
        fetch(missing, 50)

    def wide(col):
        w = pd.DataFrame({c: f[col] for c, f in frames.items()}).sort_index()
        w.index = pd.DatetimeIndex(w.index).tz_localize(None).normalize()
        return w[~w.index.duplicated(keep="last")]

    close, adj = wide("Close"), wide("Adj Close")
    factor = (adj / close).where(close > 0)
    return {"close": close, "adj": adj, "open": wide("Open") * factor, "high": wide("High") * factor,
            "low": wide("Low") * factor, "volume": wide("Volume")}


class Data:
    def __init__(self):
        uni = [u for u in get_universe() if (u.get("mcap") or 0) >= MIN_MCAP]
        print(f"[universe] 市值 ≥ ${MIN_MCAP / 1e9:.1f}B:{len(uni)} 檔", flush=True)
        self.info = {u["code"]: u for u in uni}
        px = prices([u["code"] for u in uni])
        self.bench = px["adj"].get(BENCH)
        cols = [c for c in px["adj"].columns if c in self.info]
        for k, v in px.items():
            setattr(self, k, v[cols])
        idx = self.adj.index
        print(f"[data] {len(cols)} 檔,{idx.min():%Y-%m-%d} ~ {idx.max():%Y-%m-%d}", flush=True)
        # 共用指標
        self.dollar_vol = (self.close * self.volume).rolling(20).mean()
        self.liquid = (self.dollar_vol > 5e6) & (self.close > 5)            # 日均成交額 > 500 萬美元、股價 > 5
        a = self.adj
        self.ma50, self.ma150, self.ma200 = a.rolling(50).mean(), a.rolling(150).mean(), a.rolling(200).mean()
        # IBD 式相對強度:近 3/6/9/12 月報酬加權,換算成全市場百分位(0~99)
        raw = 0.4 * a.pct_change(63) + 0.2 * a.pct_change(126) + 0.2 * a.pct_change(189) + 0.2 * a.pct_change(252)
        self.rs = (raw.where(self.liquid).rank(axis=1, pct=True) * 99).round()


def rsi(close, n):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn)


def largest(df, n):
    return df.rank(axis=1, ascending=False, method="first") <= n


# ---------------------------------------------------------------- Minervini
def trend_template(D):
    """Minervini 趨勢模板(第二階段上升趨勢)8 條件。"""
    a = D.adj
    hi52, lo52 = D.high.rolling(252).max(), D.low.rolling(252).min()
    return ((a > D.ma150) & (a > D.ma200)                  # 1. 股價在 150、200 日均線之上
            & (D.ma150 > D.ma200)                          # 2. 150 日均線 > 200 日均線
            & (D.ma200 > D.ma200.shift(21))                # 3. 200 日均線至少上升 1 個月
            & (D.ma50 > D.ma150) & (D.ma50 > D.ma200)      # 4. 50 日均線在 150、200 之上
            & (a > D.ma50)                                 # 5. 股價在 50 日均線之上
            & (a >= lo52 * 1.30)                           # 6. 比 52 週低點高 30% 以上
            & (a >= hi52 * 0.75)                           # 7. 距 52 週高點 25% 以內
            & (D.rs >= 70))                                # 8. 相對強度 ≥ 70


def vcp(D):
    """波動收縮:近 60 日切成 3 段(各 20 日),每段高低幅度依序縮小,最後一段 ≤ 10%,且量縮。"""
    hi, lo = D.high.rolling(20).max(), D.low.rolling(20).min()
    depth = (hi - lo) / hi
    d1, d2, d3 = depth.shift(40), depth.shift(20), depth
    contract = (d1 > d2) & (d2 > d3) & (d3 <= 0.10) & (d1 <= 0.35)
    vol50 = D.volume.rolling(50).mean()
    dry = D.volume.rolling(10).mean() < 0.75 * vol50          # 最後收縮段量縮
    pivot = hi                                                # 樞紐點 = 最後收縮段的高點
    return contract & dry, pivot, vol50, d1, d3


def vcp_breakout(D):
    tt = trend_template(D)
    setup, pivot, vol50, d1, d3 = vcp(D)
    breakout = (tt & D.liquid & setup.shift(1).fillna(False).astype(bool)
                & (D.adj > pivot.shift(1)) & (D.volume > 1.5 * vol50.shift(1)))   # 放量突破樞紐點
    exit_ = D.adj < D.ma50                                                         # 跌破 50 日均線出場
    held = bt.hold_until_stop(breakout, exit_, D.adj, stop=0.08)                   # 停損 8%
    held = held & largest(D.rs.where(held), 20)                                    # 最多 20 檔,取 RS 最強
    # 觀察名單:已有 VCP、距樞紐點 5% 內、還沒突破
    watch = tt & D.liquid & setup & (D.adj >= pivot * 0.95) & (D.adj <= pivot)
    return held, "D", {}, ("RS", D.rs, "{:.0f}", False), {
        "frame": watch, "metric": ("距樞紐點", (D.adj / pivot - 1) * 100, "{:+.1f}%", False),
        "extra": ("最後收縮幅度", d3 * 100, "{:.1f}%")}


def trend_rs(D):
    tt = trend_template(D) & D.liquid
    return largest(D.rs.where(tt), 20), "M", {}, ("RS", D.rs, "{:.0f}", False), None


# ---------------------------------------------------------------- 比照台股(價格類)
def rsi3(D):
    rsi20, rsi60, rsi120 = rsi(D.adj, 20), rsi(D.adj, 60), rsi(D.adj, 120)
    buy = ((rsi120 > 55) & (rsi60 < 75) & (rsi20.pct_change(3) > 0.02)
           & ((rsi20 > 75).rolling(3).sum() == 3) & D.liquid)
    sell = buy.shift(60).fillna(False).astype(bool) | (D.close < D.close.rolling(60).mean())
    return bt.hold_until(buy, sell), "W", {}, ("RSI20", rsi20, "{:.1f}", False), None


def _vol60(D):
    return D.adj.pct_change().rolling(60).std().where(D.liquid)


def low_vol(D):
    v = _vol60(D)
    return v.rank(axis=1) <= 30, "M", {}, ("年化波動率", v * math.sqrt(252) * 100, "{:.1f}%", True), None


def high_vol(D):
    v = _vol60(D)
    return v.rank(axis=1, ascending=False) <= 30, "M", {}, \
        ("年化波動率", v * math.sqrt(252) * 100, "{:.1f}%", False), None


def risk_adjusted(D):
    ret = D.adj.pct_change()
    ret6m, mom_3m = D.adj.pct_change(120), D.adj.pct_change(60)
    risk_adj = ret6m / ret.rolling(120).std()
    pool = D.liquid & (mom_3m > 0) & (ret6m > 0)
    return largest(risk_adj.where(pool), 15), "Q", {}, ("報酬/波動", risk_adj, "{:.2f}", False), None


STRATEGIES = [
    dict(id="vcp", fn=vcp_breakout, name="VCP 突破", rebalance="每日檢查;跌破 50 日均線或虧損 8% 出場,最多 20 檔",
         rules=["符合 Minervini 趨勢模板 8 條件(含 RS ≥ 70)",
                "近 60 日分 3 段,高低幅度依序縮小,最後一段 ≤ 10%(第一段 ≤ 35%)",
                "最後 10 日均量 < 50 日均量 × 0.75(量縮)",
                "收盤突破樞紐點(最後一段高點),且成交量 > 50 日均量 × 1.5",
                "日均成交額 > 500 萬美元、股價 > $5"]),
    dict(id="trend", fn=trend_rs, name="趨勢模板 RS 前 20", rebalance="每月換股",
         rules=["股價 > 150 日、200 日均線,150 日 > 200 日", "200 日均線至少上升 1 個月",
                "50 日均線 > 150、200 日均線,股價 > 50 日均線", "比 52 週低點高 30%、距 52 週高點 25% 以內",
                "相對強度(RS)≥ 70,取 RS 最高 20 檔"]),
    dict(id="rsi3", fn=rsi3, name="三頻率 RSI", rebalance="每週檢查,持有 60 天或跌破季線出場",
         rules=["RSI120 > 55、RSI60 < 75", "RSI20 三日漲幅 > 2%、RSI20 > 75 連續 3 天",
                "台股版的 ROE > 0 條件:美股暫無免費財報,先拿掉"]),
    dict(id="lowvol", fn=low_vol, name="低波動 30", rebalance="每月換股",
         rules=["日均成交額 > 500 萬美元", "60 日報酬波動率最低的 30 檔"]),
    dict(id="riskadj", fn=risk_adjusted, name="風險調整報酬 前 15", rebalance="每季換股",
         rules=["日均成交額 > 500 萬美元", "3 個月與 6 個月報酬皆為正", "6 個月報酬 ÷ 120 日波動率 最高的 15 檔",
                "台股版的 ROE、營收條件:美股暫無免費財報,先拿掉"]),
    dict(id="highvol", fn=high_vol, name="高波動 30(對照組)", rebalance="每月換股",
         rules=["日均成交額 > 500 萬美元", "60 日報酬波動率最高的 30 檔", "用來對照低波動異象,非建議策略"]),
]


# ---------------------------------------------------------------- output
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


def picks_of(D, frame, metric, extra=None):
    when = frame.index[-1]
    row = frame.loc[when].fillna(False).astype(bool)
    label, mframe, fmt, ascending = metric
    mrow = mframe.loc[when]
    chg = D.close.pct_change().iloc[-1] * 100
    out = []
    for c in [str(c) for c in row[row].index]:
        m = num(mrow.get(c))
        info = D.info.get(c, {})
        p = {"code": c, "name": info.get("name", ""), "industry": info.get("sector", ""),
             "close": num(D.close[c].iloc[-1], 2), "chg": num(chg.get(c), 2),
             "metric": None if m is None else fmt.format(m), "metricRaw": m}
        if extra:
            e = num(extra[1].loc[when].get(c))
            p["extra"] = None if e is None else extra[2].format(e)
        out.append(p)
    sign = 1 if ascending else -1
    out.sort(key=lambda p: (p["metricRaw"] is None, sign * (p["metricRaw"] or 0)))
    return when, label, out


def run_one(D, spec):
    position, rule, kw, metric, watch = spec["fn"](D)
    position = position.reindex(index=D.adj.index, columns=D.adj.columns).fillna(False).astype(bool)
    eq, stats = bt.run(position, D.open, D.adj, rule, start=START,
                       fee=kw.get("fee", FEE), tax=kw.get("tax", TAX), stop_loss=kw.get("stop_loss"))
    bench = D.bench.reindex(eq.index).ffill().dropna() if D.bench is not None else pd.Series(dtype=float)
    when, label, picks = picks_of(D, position, metric)
    out = {
        "id": spec["id"], "name": spec["name"], "rebalance": spec["rebalance"], "rules": spec["rules"],
        "stats": {k: num(stats.get(k)) for k in ("cagr", "max_drawdown", "daily_sharpe", "win_ratio", "avg_n_stock")},
        "start": eq.index[0].strftime("%Y-%m-%d"), "end": eq.index[-1].strftime("%Y-%m-%d"),
        "equity": series_points(eq), "bench": series_points(bench / bench.iloc[0]) if len(bench) else [],
        "signalDate": pd.Timestamp(when).strftime("%Y-%m-%d"), "metricLabel": label, "picks": picks,
    }
    if watch:
        _, wlabel, wpicks = picks_of(D, watch["frame"], watch["metric"], watch["extra"])
        out["watch"] = {"title": "觀察名單:已形成 VCP、距樞紐點 5% 內、尚未突破", "metricLabel": wlabel,
                        "extraLabel": watch["extra"][0], "picks": wpicks}
    return out


def main():
    D = Data()
    out = []
    for spec in STRATEGIES:
        print(f"[run] {spec['name']}", flush=True)
        try:
            out.append(run_one(D, spec))
            s = out[-1]
            print(f"[ok]  {spec['name']}: 選 {len(s['picks'])} 檔,CAGR {s['stats']['cagr']},MDD {s['stats']['max_drawdown']}"
                  + (f",觀察 {len(s['watch']['picks'])} 檔" if "watch" in s else ""), flush=True)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            out.append({"id": spec["id"], "name": spec["name"], "rebalance": spec["rebalance"],
                        "rules": spec["rules"], "error": f"{type(e).__name__}: {e}"[:300]})
    if all("error" in s for s in out):
        raise SystemExit("所有策略都失敗")
    payload = {"generatedAt": int(datetime.now(timezone.utc).timestamp() * 1000),
               "dataDate": D.adj.index.max().strftime("%Y-%m-%d"), "strategies": out}
    js = json.dumps(payload, ensure_ascii=False, default=str)
    (HERE / "data.json").write_text(js, encoding="utf-8")
    tpl = (HERE / "index.html").read_text(encoding="utf-8")
    (HERE / "策略選股_美股.html").write_text(
        tpl.replace("<!--DATA-->", f"<script>window.STRAT_DATA = {js};</script>"), encoding="utf-8")
    print(f"[done] 資料日期 {payload['dataDate']} → data.json / 策略選股_美股.html")


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()
