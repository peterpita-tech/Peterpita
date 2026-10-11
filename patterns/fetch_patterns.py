"""台股 K 線型態篩選:掃描全部上市櫃普通股最近 5 個交易日出現的陰陽線型態。

型態定義見 candles.py(依 Steve Nison《陰線陽線》)。
資料:TWSE / TPEx 代號表 + Yahoo Finance 日線;型態用還原權值價格判斷(除權息日不會被誤判成缺口),
畫面顯示原始收盤價。

用法:python fetch_patterns.py   # → data.json、K線型態_台股.html
"""
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "zsigma-tw"))
sys.path.insert(0, str(HERE.parent / "zsigma-us"))
from fetch_zsigma_tw import get_universe  # noqa: E402

import candles as cd  # noqa: E402

DAYS = 5                     # 列出最近幾個交易日的型態
MIN_TURNOVER = 1e7           # 「成交熱絡」:20 日均成交金額 ≥ 1,000 萬元


def prices(uni, chunk=200):
    sym = {u["code"]: f"{u['code']}.{'TW' if u['mkt'] == '上市' else 'TWO'}" for u in uni}
    rev = {v: k for k, v in sym.items()}
    frames = {}

    def fetch(tickers, size):
        for i in range(0, len(tickers), size):
            part = tickers[i:i + size]
            print(f"[price] {i + len(part)}/{len(tickers)}", flush=True)
            try:
                df = yf.download(part, period="6mo", auto_adjust=False, group_by="ticker",
                                 threads=True, progress=False)
            except Exception as e:  # noqa: BLE001
                print("  download error:", e, flush=True)
                continue
            for t in part:
                try:
                    sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
                except KeyError:
                    continue
                sub = sub.dropna(subset=["Open", "High", "Low", "Close"])
                if len(sub) >= 40:
                    frames[rev[t]] = sub

    tickers = list(sym.values())
    fetch(tickers, chunk)
    missing = [t for t in tickers if rev[t] not in frames]
    if missing:
        print(f"[price] {len(missing)} 檔補抓", flush=True)
        time.sleep(30)
        fetch(missing, 50)

    def wide(col):
        w = pd.DataFrame({c: f[col] for c, f in frames.items()}).sort_index()
        w.index = pd.DatetimeIndex(w.index).tz_localize(None).normalize()
        return w[~w.index.duplicated(keep="last")]

    close = wide("Close")
    factor = (wide("Adj Close") / close).where(close > 0)
    return {"o": wide("Open") * factor, "h": wide("High") * factor, "l": wide("Low") * factor,
            "c": wide("Adj Close"), "v": wide("Volume"), "raw": close}


def num(v, nd=2):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else round(v, nd)


def main():
    uni = get_universe()
    info = {u["code"]: u for u in uni}
    px = prices(uni)
    k, hits = cd.detect(px["o"], px["h"], px["l"], px["c"], px["v"])
    raw = px["raw"]
    chg = raw.pct_change(fill_method=None) * 100
    turnover = (raw * px["v"]).rolling(20).mean()
    dates = k.c.index[-DAYS:]
    last = k.c.index[-1]
    meta = {p[0]: p for p in cd.P}

    rows = []
    for pid, mask in hits.items():
        _, name, _, bars, side, kind, page, confirm, stop, _ = meta[pid]
        sub = mask.loc[dates]
        for d, row in sub.iterrows():
            for code in row[row].index:
                i = k.c.index.get_loc(d)
                lvl = cd.level(k, stop, bars, d, code)
                factor = raw[code].iloc[i] / k.c[code].iloc[i] if k.c[code].iloc[i] else 1.0
                lvl_raw = lvl * factor if lvl is not None else None              # 換回原始股價
                confirmed = None
                if confirm and d != last:                                        # 吊人 / 倒狀鎚子:看隔天
                    nc = k.c[code].iloc[i + 1]
                    confirmed = bool(nc < k.bot[code].iloc[i]) if pid == "hanging_man" else bool(nc > k.top[code].iloc[i])
                c_now = raw[code].iloc[-1]
                rows.append({
                    "date": d.strftime("%Y-%m-%d"), "code": code, "name": info[code]["name"], "mkt": info[code]["mkt"],
                    "p": pid, "close": num(raw[code].iloc[i]), "chg": num(chg[code].iloc[i]),
                    "volR": num(k.vol_ratio[code].iloc[i]), "level": num(lvl_raw),
                    "dist": num((c_now / lvl_raw - 1) * 100) if lvl_raw else None,          # 目前股價距支撐/壓力
                    "now": num(c_now), "liquid": bool(turnover[code].iloc[i] >= MIN_TURNOVER),
                    "confirmed": confirmed,
                })
    patterns = [{"id": p[0], "name": p[1], "bars": p[3], "side": p[4], "kind": p[5], "page": p[6],
                 "confirm": p[7], "stop": p[8], "desc": p[9]} for p in cd.P]
    payload = {"generatedAt": int(datetime.now(timezone.utc).timestamp() * 1000),
               "dataDate": last.strftime("%Y-%m-%d"), "dates": [d.strftime("%Y-%m-%d") for d in dates],
               "total": int(k.c.shape[1]), "patterns": patterns, "hits": rows}
    js = json.dumps(payload, ensure_ascii=False)
    (HERE / "data.json").write_text(js, encoding="utf-8")
    tpl = (HERE / "index.html").read_text(encoding="utf-8")
    (HERE / "K線型態_台股.html").write_text(tpl.replace("<!--DATA-->", f"<script>window.PAT_DATA = {js};</script>"),
                                        encoding="utf-8")
    today = [r for r in rows if r["date"] == payload["dataDate"]]
    print(f"[done] {payload['dataDate']} · 掃描 {payload['total']} 檔 · 近 {DAYS} 日型態 {len(rows)} 筆(最新一日 {len(today)} 筆)")


if __name__ == "__main__":
    main()
