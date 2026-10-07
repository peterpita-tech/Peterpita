"""Zsigma 籌碼微結構掃描 · 美股版 資料抓取 / 指標計算

用法:
    pip install -r requirements.txt
    python fetch_zsigma_us.py                 # 全美股(NASDAQ / NYSE / AMEX)
    python fetch_zsigma_us.py --tickers AAPL,NVDA,TSLA
    python fetch_zsigma_us.py --min-dollar-vol 5  # 20日均成交額 ≥ 500 萬美元

輸出(與本檔同資料夾):
    data.js                       ← index.html 會讀這個
    Zsigma籌碼掃描_美股.html        ← 資料內嵌的單檔版,可直接雙擊開啟
"""
import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

HERE = Path(__file__).resolve().parent
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept": "application/json, text/plain, */*"}
EXCHANGES = {"nasdaq": "NASDAQ", "nyse": "NYSE", "amex": "AMEX"}
# 排除權證、單位、優先股、權利等非普通股
EXCLUDE_WORDS = ("warrant", "unit", " right", "preferred", "depositary shares representing",
                 "notes due", "debenture", "% series", "subordinated")

WIN = 20          # 月線 / Z-Score / 集中度 / 波動率視窗
CHIP_WIN = 60     # 籌碼分布回溯天數
HALF_LIFE = 20    # 籌碼權重半衰期(天):越舊的成交量權重越低,模擬籌碼換手
CHIP_PTS = 12     # 每天把成交量平均攤在 低~高 之間的價位數


# ---------------------------------------------------------------- universe
def nasdaq_screener():
    """NASDAQ 官方 screener:三大交易所全部上市股票(含產業、市值)。"""
    out = []
    for ex, label in EXCHANGES.items():
        url = ("https://api.nasdaq.com/api/screener/stocks"
               f"?tableonly=true&limit=10000&offset=0&exchange={ex}&download=true")
        r = requests.get(url, headers=UA, timeout=30)
        r.raise_for_status()
        for row in r.json()["data"]["rows"]:
            sym = (row.get("symbol") or "").strip()
            name = (row.get("name") or "").strip()
            if not sym or "^" in sym or any(w in name.lower() for w in EXCLUDE_WORDS):
                continue
            try:
                mcap = float(row.get("marketCap") or 0) or None
            except ValueError:
                mcap = None
            out.append({"code": sym.replace("/", "-"), "name": name, "mkt": label,
                        "sector": (row.get("sector") or "").strip() or "—",
                        "mcap": mcap})
    return out


def nasdaqtrader_list():
    """備援:nasdaqtrader 代號表(無產業/市值)。"""
    url = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    exmap = {"Q": "NASDAQ", "N": "NYSE", "A": "AMEX"}
    out = []
    lines = r.text.strip().splitlines()
    hdr = lines[0].split("|")
    for ln in lines[1:]:
        p = dict(zip(hdr, ln.split("|")))
        if p.get("ETF") != "N" or p.get("Test Issue") != "N":
            continue
        ex = exmap.get(p.get("Listing Exchange"))
        sym, name = p.get("Symbol", ""), p.get("Security Name", "")
        if not ex or not sym or "$" in sym or any(w in name.lower() for w in EXCLUDE_WORDS):
            continue
        out.append({"code": sym.replace(".", "-"), "name": name, "mkt": ex,
                    "sector": "—", "mcap": None})
    return out


def get_universe(tickers=None):
    if tickers:
        return [{"code": t.strip().upper(), "name": t.strip().upper(), "mkt": "—",
                 "sector": "—", "mcap": None} for t in tickers if t.strip()]
    for fn in (nasdaq_screener, nasdaqtrader_list):
        try:
            u = fn()
            if u:
                print(f"[universe] {fn.__name__}: {len(u)} 檔")
                return u
        except Exception as e:  # noqa: BLE001
            print(f"[universe] {fn.__name__} 失敗: {e}", file=sys.stderr)
    sys.exit("無法取得美股代號清單")


# ---------------------------------------------------------------- prices
def download(codes, period, chunk=200, retry=True):
    frames = {}
    for i in range(0, len(codes), chunk):
        part = codes[i:i + chunk]
        print(f"[price] {i + len(part)}/{len(codes)}", end="\r", flush=True)
        for attempt in range(3):
            try:
                df = yf.download(part, period=period, interval="1d", group_by="ticker",
                                 auto_adjust=True, threads=True, progress=False)
                break
            except Exception as e:  # noqa: BLE001
                print(f"\n[price] 重試 {attempt + 1}: {e}", file=sys.stderr)
                time.sleep(3 * (attempt + 1))
        else:
            continue
        for t in part:
            try:
                sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
            except KeyError:
                continue
            sub = sub.dropna(subset=["Open", "High", "Low", "Close"])
            if len(sub):
                frames[t] = sub
    print()
    missing = [t for t in codes if t not in frames]
    if retry and missing:
        print(f"[price] {len(missing)} 檔沒抓到(可能被限流),30 秒後補抓")
        time.sleep(30)
        frames.update(download(missing, period, chunk=50, retry=False))
    return frames


# ---------------------------------------------------------------- metrics
def gini(x):
    x = np.sort(np.asarray(x, dtype=float))
    n, s = len(x), x.sum()
    if n == 0 or s <= 0:
        return None
    return float((2 * np.arange(1, n + 1) - n - 1).dot(x) / (n * s))


def chip_costs(df, close):
    """籌碼分布:近 CHIP_WIN 日每天的成交量平均攤在當日 低~高 價位上,並依天數衰減。
    以現價切開 → 贏家成本 = 現價以下(獲利)籌碼的平均成本,
                 受困成本 = 現價以上(套牢)籌碼的平均成本,獲利比例 = 獲利籌碼占比。"""
    t = df.iloc[-CHIP_WIN:]
    lo, hi = t["Low"].to_numpy(float), t["High"].to_numpy(float)
    vol = t["Volume"].fillna(0).to_numpy(float)
    age = np.arange(len(t))[::-1]
    day_w = vol * 0.5 ** (age / HALF_LIFE) / CHIP_PTS
    frac = (np.arange(CHIP_PTS) + 0.5) / CHIP_PTS
    px = (lo[:, None] + (hi - lo)[:, None] * frac[None, :]).ravel()
    w = np.repeat(day_w, CHIP_PTS)
    tot = w.sum()
    if not tot > 0:
        return None, None, None
    win = px <= close
    ww, wt = w[win].sum(), w[~win].sum()
    cost_w = float((px[win] * w[win]).sum() / ww) if ww > 0 else None
    cost_tr = float((px[~win] * w[~win]).sum() / wt) if wt > 0 else None
    return cost_w, cost_tr, float(ww / tot * 100)


def metrics(df):
    if len(df) < WIN + 5:
        return None
    c = df["Close"]
    close, prev = float(c.iloc[-1]), float(c.iloc[-2])
    ma = c.rolling(WIN).mean().iloc[-1]
    sd = c.rolling(WIN).std().iloc[-1]
    cost_w, cost_tr, win_pct = chip_costs(df, close)

    tail = df.iloc[-WIN:]
    th, tl, tc, tv = tail["High"], tail["Low"], tail["Close"], tail["Volume"].fillna(0)
    hl = np.log(th / tl).replace([np.inf, -np.inf], np.nan).dropna()
    park = math.sqrt((hl ** 2).mean() / (4 * math.log(2))) * math.sqrt(252) * 100 if len(hl) else None
    rng = (th - tl).replace(0, np.nan)
    clv = ((tc - tl) / rng).fillna(0.5).clip(0, 1)
    dollar = tc * tv
    conc = gini((dollar * clv).values)
    dv20 = float(dollar.mean())
    vol_avg = float(tv.iloc[:-1].mean()) if len(tv) > 1 else 0

    return {
        "close": round(close, 2),
        "chg": round((close / prev - 1) * 100, 2) if prev else 0.0,
        "ma20": round(float(ma), 2),
        "costW": round(cost_w, 2) if cost_w else None,
        "costTR": round(cost_tr, 2) if cost_tr else None,
        "biasMA": round((close / ma - 1) * 100, 2),
        "biasW": round((close / cost_w - 1) * 100, 2) if cost_w else None,     # 高於贏家成本 %
        "biasTR": round((cost_tr / close - 1) * 100, 2) if cost_tr else None,  # 距受困成本 %(上檔壓力)
        "winPct": round(win_pct, 1) if win_pct is not None else None,        # 獲利比例 %
        "z": round(float((close - ma) / sd), 4) if sd and sd > 0 else 0.0,
        "park": round(park, 2) if park is not None else None,
        "conc": round(conc, 4) if conc is not None else None,
        "dv": round(dv20 / 1e6, 2),                             # 20日均成交額 (百萬美元)
        "volR": round(float(tv.iloc[-1]) / vol_avg, 2) if vol_avg else None,  # 量比
        "aboveMA": bool(close > ma),
        "aboveCost": bool(win_pct is not None and win_pct >= 50),   # 多數籌碼獲利
        "_last": df.index[-1],
    }


def add_scores(rows):
    """強勢分數 = Z(30%)、月線乖離(20%)、贏家成本乖離(20%)、集中度(20%) 的全市場百分位
    + 獲利比例(10%)。"""
    d = pd.DataFrame(rows)
    pr = lambda k: d[k].rank(pct=True).fillna(0)  # noqa: E731
    struct = d["winPct"].fillna(0) / 100
    s = 100 * (0.30 * pr("z") + 0.20 * pr("biasMA") + 0.20 * pr("biasW")
               + 0.20 * pr("conc") + 0.10 * struct)
    for r, v in zip(rows, s):
        r["score"] = round(float(v), 1)


def is_open(tz, start, end):
    """執行當下交易所是否在盤中(不考慮假日)。"""
    now = pd.Timestamp.now(tz=tz)
    return bool(now.weekday() < 5 and start <= (now.hour, now.minute) < end)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Zsigma 美股版資料抓取")
    ap.add_argument("--tickers", help="只掃指定代號,逗號分隔")
    ap.add_argument("--period", default="3mo", help="回溯期間 (yfinance period,預設 3mo)")
    ap.add_argument("--min-price", type=float, default=1.0, help="最低股價 (美元,預設 1)")
    ap.add_argument("--min-dollar-vol", type=float, default=1.0,
                    help="20日均成交額下限 (百萬美元,預設 1)")
    ap.add_argument("--limit", type=int, help="只取前 N 檔 (測試用)")
    a = ap.parse_args()

    uni = get_universe(a.tickers.split(",") if a.tickers else None)
    if a.limit:
        uni = uni[:a.limit]
    meta = {u["code"]: u for u in uni}
    frames = download(list(meta), a.period)

    rows, skipped = [], 0
    for code, df in frames.items():
        m = metrics(df)
        if not m or m["close"] < a.min_price or m["dv"] < a.min_dollar_vol:
            skipped += 1
            continue
        rows.append({**meta[code], **m})
    if not rows:
        sys.exit("沒有符合條件的股票")

    # 只保留最新交易日的資料(排除停牌 / 下市)
    last = max(r["_last"] for r in rows)
    rows = [r for r in rows if r["_last"] >= last - pd.offsets.BDay(3)]
    add_scores(rows)
    for r in rows:
        r.pop("_last")
    rows.sort(key=lambda r: -r["score"])

    days = max(len(df) for df in frames.values())
    cnt = lambda k: sum(1 for r in rows if r["mkt"] == k)  # noqa: E731
    parks = [r["park"] for r in rows if r["park"] is not None]
    data = {
        "generatedAt": int(datetime.now(timezone.utc).timestamp() * 1000),
        "intraday": is_open("America/New_York", (9, 30), (16, 0)),
        "date": pd.Timestamp(last).strftime("%Y-%m-%d"),
        "tradingDays": days,
        "total": len(rows),
        "nasdaq": cnt("NASDAQ"), "nyse": cnt("NYSE"), "amex": cnt("AMEX"),
        "aboveMA": sum(r["aboveMA"] for r in rows),
        "aboveCost": sum(r["aboveCost"] for r in rows),
        "hiZ": sum(1 for r in rows if r["z"] >= 2),
        "avgPark": round(sum(parks) / len(parks), 2) if parks else None,
        "rows": rows,
    }
    js = "window.ZS_DATA = " + json.dumps(data, ensure_ascii=False) + ";"
    (HERE / "data.js").write_text(js, encoding="utf-8")

    tpl = (HERE / "index.html").read_text(encoding="utf-8")
    embed = tpl.replace("<!--ZS_DATA-->", f"<script>{js}</script>")
    (HERE / "Zsigma籌碼掃描_美股.html").write_text(embed, encoding="utf-8")
    print(f"[done] {data['date']} · {len(rows)} 檔 (略過 {skipped}) → data.js / Zsigma籌碼掃描_美股.html")


if __name__ == "__main__":
    main()
