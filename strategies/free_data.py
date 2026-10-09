"""免費公開資料:Yahoo Finance 股價、公開資訊觀測站月營收與財報彙總表。

不需要任何帳號或憑證。觀測站的歷史資料不會變動,抓過就存在 .cache/,之後只補新的月份 / 季度。
所有基本面資料都以「公告期限」當作可用日期,避免回測偷看未來。
"""
import io
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache"
sys.path.insert(0, str(HERE.parent / "zsigma-tw"))
sys.path.insert(0, str(HERE.parent / "zsigma-us"))
from fetch_zsigma_tw import get_universe  # noqa: E402,F401  上市 + 上櫃 普通股清單

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
MOPS = "https://mopsov.twse.com.tw"
MKTS = {"上市": "sii", "上櫃": "otc"}
CODE = re.compile(r"^\d{4}$")


def _num(s):
    return pd.to_numeric(pd.Series(s).astype(str).str.replace(",", "").str.strip(), errors="coerce")


def _flat(cols):
    return [" ".join(str(x) for x in c if not str(x).startswith("Unnamed")) if isinstance(c, tuple) else str(c)
            for c in cols]


def _get(url, method="get", data=None, tries=4):
    """觀測站偶爾回「查詢過於頻繁」,遇到就等久一點重試。"""
    for i in range(tries):
        try:
            r = requests.request(method, url, data=data, headers=UA, timeout=60)
            if r.status_code == 200 and "頻繁" not in r.text[:3000]:
                return r
            if r.status_code == 404:                       # 頁面不存在(還沒公布):不用重試
                return None
            print(f"  [retry {i + 1}] HTTP {r.status_code}{' 查詢過於頻繁' if '頻繁' in r.text[:3000] else ''}", flush=True)
        except requests.RequestException as e:
            print(f"  [retry {i + 1}] {e}", flush=True)
        time.sleep(15 * (i + 1))
    return None


# ---------------------------------------------------------------- prices
def prices(codes_mkt, start="2018-06-01", chunk=150):
    """回傳 dict:close(原始收盤)、adj_close、adj_open(還原開盤)、volume(股),欄 = 股票代號。"""
    sym = {c: f"{c}.{'TW' if m == '上市' else 'TWO'}" for c, m in codes_mkt.items()}
    sym["0050"] = "0050.TW"                                   # 大盤基準
    rev = {v: k for k, v in sym.items()}
    frames = {}

    def fetch(tickers, size):
        for i in range(0, len(tickers), size):
            part = tickers[i:i + size]
            print(f"[price] {i + len(part)}/{len(tickers)}", flush=True)
            try:
                df = yf.download(part, start=start, auto_adjust=False, group_by="ticker",
                                 threads=True, progress=False)
            except Exception as e:  # noqa: BLE001
                print("  download error:", e)
                continue
            for t in part:
                try:
                    sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
                except KeyError:
                    continue
                sub = sub.dropna(subset=["Close"])
                if len(sub):
                    frames[rev[t]] = sub

    tickers = list(sym.values())
    fetch(tickers, chunk)
    missing = [t for t in tickers if rev[t] not in frames]
    if missing:
        print(f"[price] {len(missing)} 檔補抓")
        time.sleep(30)
        fetch(missing, 50)

    def wide(col):
        return pd.DataFrame({c: f[col] for c, f in frames.items()}).sort_index()

    close, adj, opn, vol = wide("Close"), wide("Adj Close"), wide("Open"), wide("Volume")
    factor = (adj / close).where(close > 0)
    out = {"close": close, "adj_close": adj, "adj_open": opn * factor, "volume": vol}
    for k, v in out.items():
        v.index = pd.DatetimeIndex(v.index).tz_localize(None).normalize()
        out[k] = v[~v.index.duplicated(keep="last")]
    return out


# ---------------------------------------------------------------- monthly revenue
def _revenue_page(mkt, roc_y, m):
    path = CACHE / "rev" / f"{mkt}_{roc_y}_{m:02d}.pkl"
    if path.exists():
        return pd.read_pickle(path)
    r = _get(f"{MOPS}/nas/t21/{mkt}/t21sc03_{roc_y}_{m}_0.html")
    if r is None:
        return None
    r.encoding = "big5hkscs"
    try:
        tables = pd.read_html(io.StringIO(r.text))
    except ValueError:
        return None
    rows, industry = [], ""
    for t in tables:
        cols = _flat(t.columns)
        if t.shape[1] == 2 and cols[0].startswith("產業別"):
            industry = cols[0].split("：", 1)[-1]
            continue
        if t.shape[1] < 7 or not any("代號" in c for c in cols):
            continue
        t.columns = cols
        code_c = next(c for c in cols if "代號" in c)
        rev_c = next(c for c in cols if "當月營收" in c and "累計" not in c)
        yoy_c = next(c for c in cols if "去年同月" in c)
        t = t[t[code_c].astype(str).str.match(r"^\d{4}$")]
        rows.append(pd.DataFrame({"code": t[code_c].astype(str), "rev": _num(t[rev_c]).values,
                                  "yoy": _num(t[yoy_c]).values, "industry": industry}))
    if not rows:
        return None
    df = pd.concat(rows, ignore_index=True).drop_duplicates("code")
    path.parent.mkdir(parents=True, exist_ok=True)
    today = pd.Timestamp.today()
    # 兩個月前以前的資料不會再變,才寫進快取
    if (today.year - (roc_y + 1911)) * 12 + today.month - m >= 2:
        df.to_pickle(path)
    return df


def monthly_revenue(start_year=2018):
    """回傳 (當月營收, 去年同月增減%, 產業別);索引 = 次月 10 日(公告期限)。"""
    today = pd.Timestamp.today()
    months = []
    y, m = start_year, 1
    while (y, m) < (today.year, today.month):
        months.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    jobs = [(mkt, y, m) for y, m in months for mkt in MKTS.values()]
    todo = [j for j in jobs if not (CACHE / "rev" / f"{j[0]}_{j[1] - 1911}_{j[2]:02d}.pkl").exists()]
    print(f"[revenue] {len(months)} 個月,快取已有 {len(jobs) - len(todo)} 頁,要抓 {len(todo)} 頁", flush=True)
    done = 0

    def one(job):
        nonlocal done
        mkt, y, m = job
        df = _revenue_page(mkt, y - 1911, m)
        done += 1
        if done % 20 == 0:
            print(f"[revenue] {done}/{len(jobs)}", flush=True)
        return job, df

    with ThreadPoolExecutor(4) as ex:                      # 靜態頁面,4 條同時抓
        pages = dict(ex.map(one, jobs))

    revs, yoys, industry = {}, {}, {}
    for y, m in months:
        parts = [pages[(mkt, y, m)] for mkt in MKTS.values() if pages.get((mkt, y, m)) is not None]
        if not parts:
            continue
        avail = (pd.Timestamp(y, m, 1) + pd.offsets.MonthBegin(1)) + pd.Timedelta(days=9)
        df = pd.concat(parts).drop_duplicates("code").set_index("code")
        revs[avail], yoys[avail] = df["rev"], df["yoy"]
        industry.update(df["industry"].to_dict())
    print(f"[revenue] 完成 {len(revs)} 個月", flush=True)
    return pd.DataFrame(revs).T.sort_index(), pd.DataFrame(yoys).T.sort_index(), industry


# ---------------------------------------------------------------- financial statements
def _pick(cols, *patterns):
    for p in patterns:
        for c in cols:
            if re.search(p, c):
                return c
    return None


def _statement(mkt, roc_y, season, kind):
    """kind = sb04(綜合損益,累計值)/ sb05(資產負債)。"""
    path = CACHE / "fin" / f"{kind}_{mkt}_{roc_y}_{season}.pkl"
    if path.exists():
        return pd.read_pickle(path)
    form = dict(encodeURIComponent=1, step=1, firstin=1, off=1, isQuery="Y",
                TYPEK=mkt, year=str(roc_y), season=f"{season:02d}")
    r = _get(f"{MOPS}/mops/web/ajax_t163{kind}", "post", form)
    time.sleep(3)
    if r is None:
        return None
    try:
        tables = pd.read_html(io.StringIO(r.text))
    except ValueError:
        tables = []
    rows = []
    for t in tables:
        cols = _flat(t.columns)
        code_c = _pick(cols, r"代號")
        if code_c is None or t.shape[1] < 10:
            continue
        t.columns = cols
        t = t[t[code_c].astype(str).str.match(r"^\d{4}$")]
        if kind == "sb04":
            ni = _pick(cols, r"^淨利.*歸屬於母公司業主$")
            op = _pick(cols, r"^營業利益")
            eps = _pick(cols, r"基本每股盈餘")
            rows.append(pd.DataFrame({
                "code": t[code_c].astype(str).values,
                "ni": _num(t[ni]).values if ni else np.nan,
                "op": _num(t[op]).values if op else np.nan,
                "eps": _num(t[eps]).values if eps else np.nan}))
        else:
            eq = _pick(cols, r"歸屬於母公司業主.*權益", r"^權益總計$")
            rows.append(pd.DataFrame({"code": t[code_c].astype(str).values,
                                      "equity": _num(t[eq]).values if eq else np.nan}))
    if not rows:
        return None                                     # 還沒公告:不寫快取,下次再抓
    df = pd.concat(rows, ignore_index=True).drop_duplicates("code")
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_pickle(path)
    return df


DEADLINE = {1: (5, 15), 2: (8, 14), 3: (11, 14), 4: (3, 31)}   # 季報公告期限(Q4 = 隔年)


def _avail(year, season):
    mo, d = DEADLINE[season]
    return pd.Timestamp(year + (season == 4), mo, d)


def financials(start_year=2018):
    """回傳 dict 寬表(索引 = 公告期限):
    roe(近四季稅後淨利 ÷ 母公司權益,%)、op_growth(單季營業利益年增率,%)、eps_ttm(近四季 EPS)。"""
    today = pd.Timestamp.today()
    recs = []
    quarters = [(y, s) for y in range(start_year, today.year + 1) for s in (1, 2, 3, 4) if _avail(y, s) <= today]
    for n, (y, s) in enumerate(quarters, 1):
        print(f"[fin] {y}Q{s} ({n}/{len(quarters)})", flush=True)
        for mkt in MKTS.values():
            inc, bal = _statement(mkt, y - 1911, s, "sb04"), _statement(mkt, y - 1911, s, "sb05")
            if inc is None:
                print(f"  {mkt} 損益表沒抓到", flush=True)
                continue
            df = inc.merge(bal, on="code", how="left") if bal is not None else inc.assign(equity=np.nan)
            recs.append(df.assign(year=y, season=s))
    if not recs:
        raise RuntimeError("觀測站財報一筆都沒抓到")
    f = pd.concat(recs, ignore_index=True).drop_duplicates(["code", "year", "season"])
    f = f.sort_values(["code", "year", "season"])

    # 損益表是年初累計值 → 換算成單季
    for col in ("ni", "op", "eps"):
        prev = f.groupby(["code", "year"])[col].shift()
        f[col + "_q"] = np.where(f["season"] == 1, f[col], f[col] - prev)
    f["avail"] = [_avail(y, s) for y, s in zip(f["year"], f["season"])]
    f["qidx"] = f["year"] * 4 + f["season"]

    out = {}
    g = f.groupby("code")
    # 近四季合計:四季必須連續
    for col, name in (("ni_q", "ni_ttm"), ("eps_q", "eps_ttm")):
        roll = g[col].rolling(4).sum().reset_index(level=0, drop=True)
        f[name] = roll.where(f["qidx"] - g["qidx"].shift(3) == 3)
    f["roe"] = f["ni_ttm"] / f["equity"].where(f["equity"] > 0) * 100
    last_year = g["op_q"].shift(4)
    gap = f["qidx"] - g["qidx"].shift(4)
    f["op_growth"] = ((f["op_q"] - last_year) / last_year.abs() * 100).where(gap == 4)
    for col in ("roe", "op_growth", "eps_ttm"):
        w = f.pivot_table(index="avail", columns="code", values=col, aggfunc="last")
        out[col] = w.sort_index()
    print(f"[fin] {f['year'].min()}Q{f['season'].min()} ~ {f['year'].max()} 季,{f['code'].nunique()} 家")
    return out


def daily(frame, index):
    """把公告日索引的資料對齊到交易日(之後沿用到下一次公告)。"""
    full = frame.reindex(frame.index.union(index)).sort_index().ffill()
    return full.reindex(index)
