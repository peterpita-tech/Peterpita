"""一次性測試:GitHub runner 能否取得觀測站月營收 / 財報彙總表,以及欄位格式。"""
import io
import time

import pandas as pd
import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


def tables(html):
    try:
        return pd.read_html(io.StringIO(html))
    except Exception as e:  # noqa: BLE001
        print("  read_html error:", e)
        return []


def show(label, r):
    r.encoding = r.apparent_encoding if "big5" in (r.apparent_encoding or "").lower() else (r.encoding or "utf-8")
    t = r.text
    print(f"\n=== {label}\nstatus={r.status_code} bytes={len(r.content)} enc={r.encoding} url={r.url}")
    print("head:", t[:200].replace("\n", " "))
    ts = tables(t)
    print("tables:", len(ts))
    for i, df in enumerate(ts[:6]):
        cols = [" ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in df.columns]
        print(f"  [{i}] shape={df.shape} cols={cols[:22]}")
        hit = df[df.astype(str).apply(lambda s: s.str.contains("2330|5452|台積電", na=False)).any(axis=1)]
        if len(hit):
            print("     sample:", hit.head(1).to_dict("records"))


for mkt in ("sii", "otc"):
    for ym in ((113, 9), (108, 1)):
        url = f"https://mopsov.twse.com.tw/nas/t21/{mkt}/t21sc03_{ym[0]}_{ym[1]}_0.html"
        try:
            show(f"月營收 {mkt} {ym}", requests.get(url, headers=UA, timeout=60))
        except Exception as e:  # noqa: BLE001
            print(f"\n=== 月營收 {mkt} {ym}\nERROR {e}")
        time.sleep(3)

for ep, name in (("ajax_t163sb04", "綜合損益彙總"), ("ajax_t163sb05", "資產負債彙總")):
    for mkt in ("sii", "otc"):
        form = dict(encodeURIComponent=1, step=1, firstin=1, off=1, isQuery="Y",
                    TYPEK=mkt, year="113", season="02")
        try:
            show(f"{name} {mkt} 113Q2", requests.post(f"https://mopsov.twse.com.tw/mops/web/{ep}",
                                                      data=form, headers=UA, timeout=60))
        except Exception as e:  # noqa: BLE001
            print(f"\n=== {name} {mkt}\nERROR {e}")
        time.sleep(4)

import yfinance as yf  # noqa: E402
df = yf.download(["0050.TW", "2330.TW", "5452.TWO"], start="2018-06-01", auto_adjust=False,
                 group_by="ticker", progress=False)
for t in ("0050.TW", "2330.TW", "5452.TWO"):
    sub = df[t].dropna(how="all")
    print(f"\n=== yfinance {t}: rows={len(sub)} {sub.index.min()} ~ {sub.index.max()} cols={list(sub.columns)}")
