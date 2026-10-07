"""Zsigma 籌碼微結構掃描 · 全台股 資料抓取

指標計算與美股版共用 (../zsigma-us/fetch_zsigma_us.py)。

用法:
    pip install -r ../zsigma-us/requirements.txt
    python fetch_zsigma_tw.py
    python fetch_zsigma_tw.py --tickers 2330,5452

輸出(與本檔同資料夾):
    data.js                  ← index.html 會讀這個
    Zsigma籌碼掃描_台股.html   ← 資料內嵌的單檔版
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "zsigma-us"))
from fetch_zsigma_us import UA, add_scores, download, is_open, metrics  # noqa: E402

COMMON = re.compile(r"^[1-9]\d{3}$")   # 4 碼普通股(排除 ETF / 權證 / 特別股)

# 原文三檔標的:Zsigma 分點 Buy_Gini 與洗盤機率(來自 vocus.cc 文章,無法由 OHLCV 重算)
ARTICLES = {
    "5452": {"giniReal": 0.704, "giniPrev": 0.7078, "giniDate": "04/28", "wash": 0.6,
             "url": "https://vocus.cc/article/6a23d616fd89780001ebcdff"},
    "8027": {"giniReal": 0.9024, "giniPrev": 0.8168, "giniDate": "06/04", "wash": 0.0,
             "url": "https://vocus.cc/article/6a23d6dbfd89780001ec1e9b"},
    "4109": {"giniReal": 0.8533, "giniPrev": 0.851, "giniDate": "04/28", "wash": 0.0,
             "url": "https://vocus.cc/article/6a23d0b2fd89780001ea21a6"},
}


OPENAPI = {"上市": ("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", "Code", "Name"),
           "上櫃": ("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
                    "SecuritiesCompanyCode", "CompanyName")}
ISIN_MODE = {"上市": 2, "上櫃": 4}
CACHE = HERE / ".universe_cache.json"
CACHE_HOURS = 20


def openapi_market(mkt):
    """TWSE / TPEx OpenAPI 每日收盤行情 → 代號、名稱。"""
    url, kc, kn = OPENAPI[mkt]
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    out = [{"code": x[kc].strip(), "name": x[kn].strip(), "mkt": mkt}
           for x in r.json() if COMMON.match(str(x.get(kc, "")).strip())]
    if not out:
        raise RuntimeError("清單為空")
    return out


def isin_market(mkt):
    """備援:證交所 ISIN 代號表(較慢)。"""
    r = requests.get(f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={ISIN_MODE[mkt]}",
                     headers=UA, timeout=60)
    r.encoding = "cp950"
    out = [{"code": code, "name": name.strip(), "mkt": mkt}
           for code, name in re.findall(r"<td[^>]*>\s*(\d{4})[\s\u3000]+([^<]+?)\s*</td>", r.text)
           if COMMON.match(code)]
    if not out:
        raise RuntimeError("清單為空")
    return out


def get_universe():
    cached = None
    if CACHE.exists():
        try:
            cached = json.loads(CACHE.read_text(encoding="utf-8"))
        except ValueError:
            pass
    if cached and time.time() - CACHE.stat().st_mtime < CACHE_HOURS * 3600:
        print(f"[universe] 使用快取: {len(cached)} 檔")
        return cached

    out = []
    for mkt in ("上市", "上櫃"):
        for fn in (openapi_market, isin_market):
            try:
                u = fn(mkt)
                print(f"[universe] {mkt} {fn.__name__}: {len(u)} 檔")
                out += u
                break
            except Exception as e:  # noqa: BLE001
                print(f"[universe] {mkt} {fn.__name__} 失敗: {e}", file=sys.stderr)
        else:
            old = [u for u in (cached or []) if u["mkt"] == mkt]
            if not old:
                sys.exit(f"無法取得{mkt}代號清單")
            print(f"[universe] {mkt} 沿用舊快取: {len(old)} 檔")
            out += old
    CACHE.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def main():
    ap = argparse.ArgumentParser(description="Zsigma 台股版資料抓取")
    ap.add_argument("--tickers", help="只掃指定代號,逗號分隔(需同時在代號清單中)")
    ap.add_argument("--period", default="3mo")
    a = ap.parse_args()

    uni = get_universe()
    if a.tickers:
        want = {t.strip() for t in a.tickers.split(",")}
        uni = [u for u in uni if u["code"] in want]
    yf_sym = {u["code"]: f"{u['code']}.{'TW' if u['mkt'] == '上市' else 'TWO'}" for u in uni}
    meta = {u["code"]: u for u in uni}
    frames = download(list(yf_sym.values()), a.period)

    rows = []
    for code, sym in yf_sym.items():
        df = frames.get(sym)
        m = metrics(df) if df is not None else None
        if not m:
            continue
        for k in ("dv", "volR"):
            m.pop(k)
        rows.append({**meta[code], **m, **({"article": True, **ARTICLES[code]} if code in ARTICLES else {})})
    if not rows:
        sys.exit("沒有任何股票資料")

    last = max(r["_last"] for r in rows)
    rows = [r for r in rows if r["_last"] >= last - pd.offsets.BDay(3)]   # 排除停牌 / 下市
    add_scores(rows)
    for r in rows:
        r.pop("_last")
    rows.sort(key=lambda r: -r["score"])

    parks = [r["park"] for r in rows if r["park"] is not None]
    data = {
        "generatedAt": int(datetime.now(timezone.utc).timestamp() * 1000),
        "intraday": is_open("Asia/Taipei", (9, 0), (13, 30)),
        "date": pd.Timestamp(last).strftime("%Y-%m-%d"),
        "tradingDays": max(len(df) for df in frames.values()),
        "total": len(rows),
        "listed": sum(1 for r in rows if r["mkt"] == "上市"),
        "otc": sum(1 for r in rows if r["mkt"] == "上櫃"),
        "aboveMA": sum(r["aboveMA"] for r in rows),
        "aboveCost": sum(r["aboveCost"] for r in rows),
        "hiZ": sum(1 for r in rows if r["z"] >= 2),
        "avgPark": round(sum(parks) / len(parks), 2) if parks else None,
        "rows": rows,
    }
    js = "window.ZS_DATA = " + json.dumps(data, ensure_ascii=False) + ";"
    (HERE / "data.js").write_text(js, encoding="utf-8")
    tpl = (HERE / "index.html").read_text(encoding="utf-8")
    (HERE / "Zsigma籌碼掃描_台股.html").write_text(
        tpl.replace("<!--ZS_DATA-->", f"<script>{js}</script>"), encoding="utf-8")
    print(f"[done] {data['date']} · {len(rows)} 檔 → data.js / Zsigma籌碼掃描_台股.html")


if __name__ == "__main__":
    main()
